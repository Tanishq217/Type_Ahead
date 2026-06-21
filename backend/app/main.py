import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db, engine, Base
from .models import SearchQuery, SearchActivity
from .cache import cache_manager
from .batch_writer import batch_writer

# Modern FastAPI lifespan context manager for startup and shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if they do not exist
    Base.metadata.create_all(bind=engine)
    # Start the batch writer background service
    batch_writer.start()
    yield
    # Stop the batch writer to flush remaining queue items
    batch_writer.stop()

app = FastAPI(
    title="Search Typeahead API",
    description="A high-performance prefix search suggestion system with consistent hashing, Redis caching, and batch writing.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="The search query submitted by the user")

class CacheDebugResponse(BaseModel):
    prefix: str
    routed_node: str
    circuit_state: str
    cache_status: str
    cached_suggestions: Optional[List[str]] = None

@app.get("/suggest")
def get_suggestions(
    q: str = Query("", description="The prefix string to search for"),
    db: Session = Depends(get_db)
):
    """
    Fetches autocomplete suggestions matching the given prefix.
    Checks the consistent-hash routed Redis node first, falling back to PostgreSQL on a miss.
    """
    start_time = time.time()
    
    # Normalize input
    prefix = q.strip().lower()
    
    # 1. Handle empty input gracefully
    if not prefix:
        return {
            "suggestions": [],
            "latency_ms": round((time.time() - start_time) * 1000, 2),
            "source": "empty_input",
            "cache_node": "none",
            "circuit_state": "CLOSED"
        }

    # 2. Formulate cache key and resolve route metadata
    cache_key = f"suggest:{prefix}"
    routed_node, circuit_state = cache_manager.get_route_info(cache_key)

    # 3. Retrieve from Cache
    cached_results = cache_manager.get(cache_key)
    if cached_results is not None:
        latency_ms = round((time.time() - start_time) * 1000, 2)
        return {
            "suggestions": cached_results,
            "latency_ms": latency_ms,
            "source": "cache",
            "cache_node": routed_node,
            "circuit_state": circuit_state
        }

    # 4. Cache Miss: Query PostgreSQL
    db_start_time = time.time()
    db_results = (
        db.query(SearchQuery)
        .filter(SearchQuery.query_text.like(f"{prefix}%"))
        .order_by(SearchQuery.total_count.desc())
        .limit(10)
        .all()
    )
    db_latency = time.time() - db_start_time

    suggestions = [item.query_text for item in db_results]

    # 5. Store result back into the routed Cache node
    cache_manager.set(cache_key, suggestions, ttl=300)

    latency_ms = round((time.time() - start_time) * 1000, 2)
    return {
        "suggestions": suggestions,
        "latency_ms": latency_ms,
        "source": "database",
        "cache_node": routed_node,
        "circuit_state": circuit_state,
        "db_latency_ms": round(db_latency * 1000, 2)
    }

@app.post("/search")
def submit_search(payload: SearchRequest):
    """
    Submits a search query.
    Pushes the query into the batch queue to be processed asynchronously.
    """
    query_text = payload.query.strip()
    if not query_text:
        return {"message": "Invalid query"}, 400
        
    batch_writer.add_query(query_text)
    return {"message": "Searched"}

@app.get("/cache/debug", response_model=CacheDebugResponse)
def cache_debug(prefix: str = Query(..., description="The prefix cache key to inspect")):
    """
    Debug endpoint to trace consistent hashing routing and node statuses.
    """
    normalized_prefix = prefix.strip().lower()
    cache_key = f"suggest:{normalized_prefix}"
    routed_node, circuit_state = cache_manager.get_route_info(cache_key)
    
    # Try fetching content
    cached_content = cache_manager.get(cache_key)
    cache_status = "HIT" if cached_content is not None else "MISS"

    return CacheDebugResponse(
        prefix=normalized_prefix,
        routed_node=routed_node,
        circuit_state=circuit_state,
        cache_status=cache_status,
        cached_suggestions=cached_content
    )

@app.get("/trending")
def get_trending_queries(
    db: Session = Depends(get_db)
):
    """
    Computes trending searches combining historical popularity with recent activity.
    Uses a time-decayed activity window from the past 2 hours.
    """
    start_time = time.time()
    two_hours_ago = datetime.utcnow() - timedelta(hours=2)

    # Subquery aggregating search activity over the past 2 hours
    recent_activity_sub = (
        db.query(
            SearchActivity.query_text,
            func.count(SearchActivity.id).label("recent_count")
        )
        .filter(SearchActivity.timestamp >= two_hours_ago)
        .group_by(SearchActivity.query_text)
        .subquery()
    )

    # Join total historical search counts with recent activity counts
    # Trending Score formula: 0.3 * total_count + 0.7 * (recent_count * 10)
    # Allows newly searched queries to trend rapidly while keeping popular queries visible.
    trending_queries = (
        db.query(
            SearchQuery.query_text,
            SearchQuery.total_count,
            func.coalesce(recent_activity_sub.c.recent_count, 0).label("recent_count"),
            (
                0.3 * SearchQuery.total_count + 
                0.7 * func.coalesce(recent_activity_sub.c.recent_count, 0) * 10
            ).label("trending_score")
        )
        .outerjoin(recent_activity_sub, SearchQuery.query_text == recent_activity_sub.c.query_text)
        .order_by(
            (0.3 * SearchQuery.total_count + 0.7 * func.coalesce(recent_activity_sub.c.recent_count, 0) * 10).desc(),
            SearchQuery.total_count.desc()
        )
        .limit(10)
        .all()
    )

    results = [
        {
            "query": item.query_text,
            "total_count": item.total_count,
            "recent_count": item.recent_count,
            "score": round(float(item.trending_score), 2)
        }
        for item in trending_queries
    ]

    return {
        "trending": results,
        "latency_ms": round((time.time() - start_time) * 1000, 2)
    }

@app.get("/metrics")
def get_system_metrics():
    """
    Exposes metrics detailing database write reduction achieved through batch writing.
    """
    return {
        "batch_writer_metrics": batch_writer.metrics,
        "queue_size": batch_writer.queue.qsize()
    }
