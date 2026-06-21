import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db, engine, Base
from .models import SearchQuery, SearchActivity, QueryTrending
from .cache import cache_manager
from .batch_writer import batch_writer
from .trending_scheduler import trending_scheduler

# Modern FastAPI lifespan context manager for startup and shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if they do not exist
    Base.metadata.create_all(bind=engine)
    
    # Start the batch writer background service
    batch_writer.start()
    
    # Start the trending scoring scheduler background service
    trending_scheduler.start()
    
    yield
    
    # Stop all background services on shutdown
    trending_scheduler.stop()
    batch_writer.stop()

app = FastAPI(
    title="Search Typeahead API",
    description="A high-performance prefix search suggestion system with consistent hashing, Redis caching, batch writing, and trending score calculations.",
    version="1.1.0",
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
    trending: bool = Query(False, description="Enable recency-aware trending suggestion mode"),
    db: Session = Depends(get_db)
):
    """
    Fetches autocomplete suggestions matching the given prefix.
    Supports basic mode (overall count sorting) and trending mode (recency-aware sorting).
    """
    start_time = time.time()
    prefix = q.strip().lower()
    
    if not prefix:
        return {
            "suggestions": [],
            "latency_ms": round((time.time() - start_time) * 1000, 2),
            "source": "empty_input",
            "cache_node": "none",
            "circuit_state": "CLOSED"
        }

    # Formulate cache key based on mode
    cache_key = f"suggest:trending:{prefix}" if trending else f"suggest:{prefix}"
    routed_node, circuit_state = cache_manager.get_route_info(cache_key)

    # Retrieve from Cache
    cached_results = cache_manager.get(cache_key)
    if cached_results is not None:
        return {
            "suggestions": cached_results,
            "latency_ms": round((time.time() - start_time) * 1000, 2),
            "source": "cache",
            "cache_node": routed_node,
            "circuit_state": circuit_state
        }

    # Cache Miss: Query Database
    db_start_time = time.time()
    
    if trending:
        # Trending Mode: Join SearchQuery with precalculated QueryTrending
        # Fallback to total_count * alpha (0.2) if query is not in trending table
        alpha = float(os.getenv("TRENDING_ALPHA", "0.2"))
        db_results = (
            db.query(SearchQuery)
            .outerjoin(QueryTrending, SearchQuery.query_text == QueryTrending.query_text)
            .filter(SearchQuery.query_text.like(f"{prefix}%"))
            .order_by(
                func.coalesce(QueryTrending.trending_score, SearchQuery.total_count * alpha).desc(),
                SearchQuery.total_count.desc()
            )
            .limit(10)
            .all()
        )
    else:
        # Basic Mode: Sort by total historical counts
        db_results = (
            db.query(SearchQuery)
            .filter(SearchQuery.query_text.like(f"{prefix}%"))
            .order_by(SearchQuery.total_count.desc())
            .limit(10)
            .all()
        )
        
    db_latency = time.time() - db_start_time
    suggestions = [item.query_text for item in db_results]

    # Store back to the routed Redis cache node
    cache_manager.set(cache_key, suggestions, ttl=300)

    return {
        "suggestions": suggestions,
        "latency_ms": round((time.time() - start_time) * 1000, 2),
        "source": "database",
        "cache_node": routed_node,
        "circuit_state": circuit_state,
        "db_latency_ms": round(db_latency * 1000, 2)
    }

@app.get("/suggest/compare")
def compare_suggestions(
    q: str = Query(..., description="The prefix string to search for"),
    db: Session = Depends(get_db)
):
    """
    Comparison endpoint returning suggestions in both Basic and Trending sorting modes
    side-by-side for comparison.
    """
    prefix = q.strip().lower()
    
    # 1. Fetch Basic suggestions
    basic_results = (
        db.query(SearchQuery)
        .filter(SearchQuery.query_text.like(f"{prefix}%"))
        .order_by(SearchQuery.total_count.desc())
        .limit(10)
        .all()
    )
    basic_suggestions = [item.query_text for item in basic_results]
    
    # 2. Fetch Trending suggestions
    alpha = float(os.getenv("TRENDING_ALPHA", "0.2"))
    trending_results = (
        db.query(SearchQuery)
        .outerjoin(QueryTrending, SearchQuery.query_text == QueryTrending.query_text)
        .filter(SearchQuery.query_text.like(f"{prefix}%"))
        .order_by(
            func.coalesce(QueryTrending.trending_score, SearchQuery.total_count * alpha).desc(),
            SearchQuery.total_count.desc()
        )
        .limit(10)
        .all()
    )
    trending_suggestions = [item.query_text for item in trending_results]

    return {
        "prefix": prefix,
        "basic_suggestions": basic_suggestions,
        "trending_suggestions": trending_suggestions
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
def cache_debug(
    prefix: str = Query(..., description="The prefix cache key to inspect"),
    trending: bool = Query(False, description="Inspect the trending prefix cache")
):
    """
    Debug endpoint to trace consistent hashing routing and node statuses.
    """
    normalized_prefix = prefix.strip().lower()
    cache_key = f"suggest:trending:{normalized_prefix}" if trending else f"suggest:{normalized_prefix}"
    routed_node, circuit_state = cache_manager.get_route_info(cache_key)
    
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
    Retrieves the top 10 trending searches computed by the background trending scheduler.
    Extremely fast read operation since results are pre-calculated in the QueryTrending table.
    """
    start_time = time.time()
    
    # Retrieve top 10 calculated trends
    trending_queries = (
        db.query(QueryTrending)
        .order_by(QueryTrending.trending_score.desc())
        .limit(10)
        .all()
    )

    # Fetch total historical counts to show relative data
    query_texts = [item.query_text for item in trending_queries]
    historical = db.query(SearchQuery).filter(SearchQuery.query_text.in_(query_texts)).all() if query_texts else []
    hist_map = {q.query_text: q.total_count for q in historical}

    results = [
        {
            "query": item.query_text,
            "total_count": hist_map.get(item.query_text, 0),
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
    # Fetch queue sizes
    redis_qsize = 0
    client = batch_writer._get_redis_client()
    if client:
        try:
            redis_qsize = client.llen(batch_writer.journal_key)
        except Exception:
            pass
            
    total_queue_size = redis_qsize + batch_writer.memory_backup_queue.qsize()
    
    return {
        "batch_writer_metrics": batch_writer.metrics,
        "queue_size": total_queue_size,
        "memory_queue_size": batch_writer.memory_backup_queue.qsize(),
        "redis_queue_size": redis_qsize
    }
