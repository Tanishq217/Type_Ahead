import os
import csv
import time
import sys

# Add backend directory to path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine, Base
from app.models import SearchQuery

def ingest_data(csv_path: str):
    """
    Ingests the generated CSV dataset of 100,000+ queries into PostgreSQL.
    Uses bulk inserts in chunks of 10,000 to complete ingestion within seconds.
    """
    if not os.path.exists(csv_path):
        print(f"Error: Dataset CSV file not found at {csv_path}. Please run generate_dataset.py first.")
        return

    print("Re-creating database tables...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    print(f"Reading dataset from {csv_path}...")
    
    start_time = time.time()
    session = SessionLocal()
    
    try:
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            
            chunk = []
            chunk_size = 10000
            total_inserted = 0
            
            for row in reader:
                chunk.append({
                    "query_text": row["query"].strip().lower(),
                    "total_count": int(row["count"])
                })
                
                if len(chunk) >= chunk_size:
                    session.bulk_insert_mappings(SearchQuery, chunk)
                    session.commit()
                    total_inserted += len(chunk)
                    print(f"Inserted {total_inserted} records...")
                    chunk = []
            
            # Insert remaining records
            if chunk:
                session.bulk_insert_mappings(SearchQuery, chunk)
                session.commit()
                total_inserted += len(chunk)
                
        end_time = time.time()
        elapsed = end_time - start_time
        print(f"Successfully ingested {total_inserted} queries in {elapsed:.2f} seconds!")
        
    except Exception as e:
        session.rollback()
        print(f"Ingestion failed due to error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(script_dir, "queries.csv")
    ingest_data(csv_file)
