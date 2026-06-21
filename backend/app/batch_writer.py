import os
import time
import queue
import threading
import logging
from typing import Dict, List
import redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from .database import SessionLocal
from .models import SearchQuery, SearchActivity
from .cache import cache_manager

logger = logging.getLogger(__name__)

class BatchWriter:
    """
    Asynchronously aggregates and flushes search queries in batches to PostgreSQL.
    Features a Redis-backed Write-Ahead Log (WAL) journal queue for crash resilience
    and at-least-once delivery guarantees.
    """
    def __init__(self):
        # Configuration parameters loaded from environment variables
        self.flush_interval = float(os.getenv("BATCH_FLUSH_INTERVAL", "5.0"))
        self.batch_limit = int(os.getenv("BATCH_SIZE_LIMIT", "100"))
        
        # Centralized queue keys
        self.journal_key = "system:search_write_journal"
        
        # Local memory backup queue in case all Redis instances are unreachable
        self.memory_backup_queue: queue.Queue = queue.Queue()
        
        self.running = False
        self.worker_thread: threading.Thread = None
        
        # Telemetry metrics for performance reports
        self.metrics = {
            "total_raw_writes_saved": 0,
            "total_db_transactions": 0,
            "queries_flushed": 0,
            "redis_wal_pushes": 0,
            "redis_wal_failures": 0,
            "recovered_queries_count": 0
        }

    def _get_redis_client(self) -> redis.Redis:
        """Resolves the routed Redis client for the journal key from the hash ring."""
        try:
            node, _ = cache_manager.get_route_info(self.journal_key)
            client = cache_manager.clients.get(node)
            return client
        except Exception:
            return None

    def add_query(self, query_text: str):
        """
        Adds a search query to the distributed journal queue in Redis (WAL).
        Falls back to local memory queue if Redis is unreachable.
        """
        normalized_query = query_text.strip().lower()
        if not normalized_query:
            return

        client = self._get_redis_client()
        if client:
            try:
                # Store query in Redis journal list
                client.rpush(self.journal_key, normalized_query)
                self.metrics["redis_wal_pushes"] += 1
                return
            except redis.RedisError as e:
                logger.error(f"Failed to push to Redis WAL journal: {e}")
                self.metrics["redis_wal_failures"] += 1
        
        # Local memory fallback
        self.memory_backup_queue.put(normalized_query)

    def start(self):
        """Starts the background thread and triggers crash recovery on startup."""
        self.running = True
        
        # Startup Crash Recovery: Drain any leftovers in Redis from previous crash
        self.recover_from_journal()

        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        logger.info("BatchWriter background thread started with WAL crash recovery.")

    def stop(self):
        """Gracefully stops the background thread and runs a final force flush."""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5.0)
        
        # Final flush on shutdown
        self.flush()
        logger.info("BatchWriter background thread stopped. Final buffer flush executed.")

    def recover_from_journal(self):
        """Checks for and flushes any queries remaining in the Redis journal on startup."""
        client = self._get_redis_client()
        if client:
            try:
                qsize = client.llen(self.journal_key)
                if qsize > 0:
                    logger.info(f"Crash Recovery: Found {qsize} unwritten queries in Redis WAL journal. Flushing...")
                    self.flush()
                    self.metrics["recovered_queries_count"] += qsize
            except redis.RedisError as e:
                logger.error(f"Failed to run journal recovery: {e}")

    def _run_loop(self):
        """Background execution loop monitoring flush triggers."""
        last_flush_time = time.time()
        while self.running:
            try:
                time.sleep(0.5)
                
                # Check current queue size
                redis_qsize = 0
                client = self._get_redis_client()
                if client:
                    try:
                        redis_qsize = client.llen(self.journal_key)
                    except redis.RedisError:
                        pass
                
                total_qsize = redis_qsize + self.memory_backup_queue.qsize()

                # Flush condition triggered by time interval or size limit
                if (time.time() - last_flush_time >= self.flush_interval) or (total_qsize >= self.batch_limit):
                    if total_qsize > 0:
                        self.flush()
                    last_flush_time = time.time()
            except Exception as e:
                logger.error(f"Error in BatchWriter loop: {e}")

    def flush(self):
        """
        Drains both Redis WAL and memory backup queues, aggregates counts,
        updates PostgreSQL via UPSERT, and invalidates prefix caches.
        """
        queries_to_process = []

        # 1. Drain memory backup queue if items exist
        mem_qsize = self.memory_backup_queue.qsize()
        for _ in range(mem_qsize):
            try:
                queries_to_process.append(self.memory_backup_queue.get_nowait())
            except queue.Empty:
                break

        # 2. Drain Redis WAL queue atomically using transaction pipeline
        client = self._get_redis_client()
        redis_queries = []
        if client:
            try:
                pipe = client.pipeline()
                pipe.lrange(self.journal_key, 0, -1)
                pipe.delete(self.journal_key)
                results = pipe.execute()
                if results and results[0]:
                    redis_queries = results[0]
                    queries_to_process.extend(redis_queries)
            except redis.RedisError as e:
                logger.error(f"Failed to retrieve queries from Redis WAL: {e}")

        if not queries_to_process:
            return

        # 3. Aggregate duplicates to minimize DB updates
        aggregated_counts: Dict[str, int] = {}
        for q in queries_to_process:
            aggregated_counts[q] = aggregated_counts.get(q, 0) + 1

        # 4. Write to PostgreSQL using bulk upsert
        session = SessionLocal()
        try:
            # Upsert SearchQuery overall frequencies
            insert_values = [{"query_text": q, "total_count": count} for q, count in aggregated_counts.items()]
            stmt = pg_insert(SearchQuery).values(insert_values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[SearchQuery.query_text],
                set_={"total_count": SearchQuery.total_count + stmt.excluded.total_count}
            )
            session.execute(stmt)

            # Insert activity events for trending sliding-window scoring
            activity_values = [{"query_text": q} for q in queries_to_process]
            session.bulk_insert_mappings(SearchActivity, activity_values)

            session.commit()

            # Update Telemetry Metrics
            self.metrics["total_raw_writes_saved"] += (len(queries_to_process) - len(aggregated_counts))
            self.metrics["total_db_transactions"] += 1
            self.metrics["queries_flushed"] += len(queries_to_process)

        except Exception as e:
            session.rollback()
            logger.error(f"Database write failure in BatchWriter: {e}. Restoring journal log...")
            
            # Crash-resilient rollback: return queries to the queue to prevent data loss
            if redis_queries and client:
                try:
                    # Put them back in Redis journal queue
                    client.lpush(self.journal_key, *redis_queries)
                except redis.RedisError as re:
                    logger.critical(f"Failed to restore queries back to Redis WAL: {re}")
                    # If Redis fails too, dump to memory backup as last resort
                    for q in redis_queries:
                        self.memory_backup_queue.put(q)
            
            # Put memory queries back
            for q in queries_to_process[:mem_qsize]:
                self.memory_backup_queue.put(q)
            return
        finally:
            session.close()

        # 5. Cache Invalidation
        invalidated_prefixes = set()
        for q in aggregated_counts.keys():
            for i in range(1, len(q) + 1):
                invalidated_prefixes.add(q[:i])

        for prefix in invalidated_prefixes:
            cache_manager.delete(f"suggest:{prefix}")
            cache_manager.delete(f"suggest:trending:{prefix}")


# Singleton instance
batch_writer = BatchWriter()
