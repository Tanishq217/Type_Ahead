import os
import time
import threading
import logging
from datetime import datetime, timedelta
from sqlalchemy import func
from .database import SessionLocal
from .models import SearchQuery, SearchActivity, QueryTrending

logger = logging.getLogger(__name__)

class TrendingScheduler:
    """
    Background worker that periodically computes trending search scores.
    Formula: Score = alpha * Historical_Count + beta * (Recent_Count * 10)
    Allows trending queries to decay gracefully over time as recent activity slows down.
    """
    def __init__(self):
        self.interval = float(os.getenv("TRENDING_INTERVAL_SECONDS", "30.0"))
        self.alpha = float(os.getenv("TRENDING_ALPHA", "0.2"))
        self.beta = float(os.getenv("TRENDING_BETA", "0.8"))
        self.recency_window_hours = float(os.getenv("RECENCY_WINDOW_HOURS", "2.0"))
        
        self.running = False
        self.thread: threading.Thread = None

    def start(self):
        """Starts the background calculation thread."""
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info(f"TrendingScheduler thread started. Run interval: {self.interval}s.")

    def stop(self):
        """Gracefully stops the background calculation thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5.0)
        logger.info("TrendingScheduler thread stopped.")

    def _run_loop(self):
        """Worker loop executing at configured intervals."""
        while self.running:
            try:
                self.compute_trending()
                # Sleep in short increments to allow rapid shutdown response
                for _ in range(int(self.interval * 2)):
                    if not self.running:
                        break
                    time.sleep(0.5)
            except Exception as e:
                logger.error(f"Error in TrendingScheduler loop: {e}")

    def compute_trending(self):
        """
        Calculates trending scores for all search queries based on their recent activity window
        relative to overall historical search count. Performs a batch atomic update in the DB.
        """
        session = SessionLocal()
        try:
            # 1. Fetch recent search activity counts within the sliding time window
            cutoff = datetime.utcnow() - timedelta(hours=self.recency_window_hours)
            recent_activities = (
                session.query(
                    SearchActivity.query_text,
                    func.count(SearchActivity.id).label("recent_count")
                )
                .filter(SearchActivity.timestamp >= cutoff)
                .group_by(SearchActivity.query_text)
                .all()
            )
            recent_counts = {row.query_text: row.recent_count for row in recent_activities}

            # 2. Get the list of all search terms that are either active now OR exist in the trending table
            existing_trending = session.query(QueryTrending).all()
            all_queries_to_score = set(recent_counts.keys()) | {q.query_text for q in existing_trending}

            if not all_queries_to_score:
                return

            # 3. Retrieve overall historical totals for these queries
            historical_queries = (
                session.query(SearchQuery)
                .filter(SearchQuery.query_text.in_(all_queries_to_score))
                .all()
            )
            historical_counts = {q.query_text: q.total_count for q in historical_queries}

            # 4. Compute trending scores
            trending_data = []
            for query in all_queries_to_score:
                hist_count = historical_counts.get(query, 1)
                rec_count = recent_counts.get(query, 0)
                
                # Combine score using weighted components
                score = (self.alpha * hist_count) + (self.beta * rec_count * 10)
                
                trending_data.append({
                    "query_text": query,
                    "trending_score": float(score),
                    "recent_count": rec_count
                })

            # 5. Atomic refresh: truncate and bulk insert the recalculated scores
            session.query(QueryTrending).delete()
            session.bulk_insert_mappings(QueryTrending, trending_data)
            session.commit()
            
            logger.info(f"Recalculated trending scores for {len(trending_data)} search queries.")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to calculate trending search scores: {e}")
        finally:
            session.close()


# Singleton scheduler instance
trending_scheduler = TrendingScheduler()
