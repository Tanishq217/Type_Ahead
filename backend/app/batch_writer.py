import time
import queue
import threading
import logging
from typing import Dict, List
from sqlalchemy.dialects.postgresql import insert as pg_insert
from .database import SessionLocal
from .models import SearchQuery, SearchActivity
from .cache import cache_manager

logger = logging.getLogger(__name__)

class BatchWriter:
    """
    Asynchronously aggregates and flushes search queries in batches to PostgreSQL.
    Reduces database write operations and handles cache invalidation for updated prefixes.
    """
    def __init__(self, flush_interval: float = 5.0, batch_limit: int = 100):
        self.flush_interval = flush_interval
        self.batch_limit = batch_limit
        self.queue: queue.Queue = queue.Queue()
        self.running = False
        self.worker_thread: threading.Thread = None
        
        # Performance tracking metrics for student reports
        self.metrics = {
            "total_raw_writes_saved": 0,
            "total_db_transactions": 0,
            "queries_flushed": 0
        }

    def add_query(self, query_text: str):
        """Adds a query to the batch buffer queue."""
        normalized_query = query_text.strip().lower()
        if normalized_query:
            self.queue.put(normalized_query)

    def start(self):
        """Starts the background batch writer daemon thread."""
        self.running = True
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        logger.info("BatchWriter background thread started.")

    def stop(self):
        """Stops the background thread and flushes remaining queries."""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=10.0)
        self.flush()
        logger.info("BatchWriter background thread stopped.")

    def _run_loop(self):
        """Background thread execution loop checking for flush conditions."""
        last_flush_time = time.time()
        while self.running:
            try:
                time.sleep(0.5)
                # Flush if time interval exceeded or queue size hits limit
                if (time.time() - last_flush_time >= self.flush_interval) or (self.queue.qsize() >= self.batch_limit):
                    self.flush()
                    last_flush_time = time.time()
            except Exception as e:
                logger.error(f"Error in BatchWriter loop: {e}")

    def flush(self):
        """Drains the queue, aggregates duplicates, upserts to DB, and invalidates cache."""
        qsize = self.queue.qsize()
        if qsize == 0:
            return

        queries_to_process = []
        for _ in range(qsize):
            try:
                queries_to_process.append(self.queue.get_nowait())
            except queue.Empty:
                break

        if not queries_to_process:
            return

        # 1. Aggregate counts for identical queries
        aggregated_counts: Dict[str, int] = {}
        for q in queries_to_process:
            aggregated_counts[q] = aggregated_counts.get(q, 0) + 1

        # 2. Write to PostgreSQL using bulk upsert
        session = SessionLocal()
        try:
            # Batch upsert SearchQuery table
            insert_values = [{"query_text": q, "total_count": count} for q, count in aggregated_counts.items()]
            stmt = pg_insert(SearchQuery).values(insert_values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[SearchQuery.query_text],
                set_={"total_count": SearchQuery.total_count + stmt.excluded.total_count}
            )
            session.execute(stmt)

            # Record individual activities for trending recency window calculations
            activity_values = [{"query_text": q} for q in queries_to_process]
            session.bulk_insert_mappings(SearchActivity, activity_values)

            session.commit()

            # Update student metrics
            self.metrics["total_raw_writes_saved"] += (len(queries_to_process) - len(aggregated_counts))
            self.metrics["total_db_transactions"] += 1
            self.metrics["queries_flushed"] += len(queries_to_process)

        except Exception as e:
            session.rollback()
            logger.error(f"Database write failure in BatchWriter flush: {e}")
            # Requeue items if DB write failed
            for q in queries_to_process:
                self.queue.put(q)
            return
        finally:
            session.close()

        # 3. Cache Invalidation
        # Invalidate all prefix cache paths that could contain these updated queries
        invalidated_prefixes = set()
        for q in aggregated_counts.keys():
            # Invalidate all prefixes: e.g. for "iphone" -> "i", "ip", "iph", "ipho", "iphon", "iphone"
            for i in range(1, len(q) + 1):
                invalidated_prefixes.add(q[:i])

        for prefix in invalidated_prefixes:
            cache_manager.delete(f"suggest:{prefix}")


# Singleton instance to be used across endpoints
batch_writer = BatchWriter()
