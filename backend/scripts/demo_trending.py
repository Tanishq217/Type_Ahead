import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import SearchQuery, SearchActivity
from app.trending_scheduler import trending_scheduler

def setup_demo():
    print("Setting up trending demonstration data...")
    session = SessionLocal()
    try:
        # Delete any existing queries starting with xyz
        session.query(SearchQuery).filter(SearchQuery.query_text.like("xyz%")).delete()
        session.query(SearchActivity).filter(SearchActivity.query_text.like("xyz%")).delete()
        
        # 1. xyz normal: popular historically, but no recent activity
        q_normal = SearchQuery(query_text="xyz normal", total_count=100)
        session.add(q_normal)
        
        # 2. xyz active: low historical popularity, but high recent traffic
        q_active = SearchQuery(query_text="xyz active", total_count=10)
        session.add(q_active)
        
        # Add 5 recent activity records for xyz active
        for _ in range(5):
            session.add(SearchActivity(query_text="xyz active", timestamp=datetime.utcnow()))
            
        session.commit()
        print("Demo queries committed. Computing trending scores...")
        
        # Run trending calculation manually
        trending_scheduler.compute_trending()
        
        print("Trending scores computed successfully!")
    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    setup_demo()
