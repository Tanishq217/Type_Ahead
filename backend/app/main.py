import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, Query, HTTPException
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
        # Backward-compatible parsing
        if cached_results and isinstance(cached_results[0], dict):
            suggestions = [item["query"] for item in cached_results]
            details = cached_results
        else:
            suggestions = cached_results
            details = [{"query": q, "count": 0} for q in suggestions]
            
        return {
            "suggestions": suggestions,
            "details": details,
            "latency_ms": round((time.time() - start_time) * 1000, 2),
            "source": "cache",
            "cache_node": routed_node,
            "circuit_state": circuit_state
        }

    # Cache Miss: Query Database
    db_start_time = time.time()
    
    if trending:
        # Trending Mode: Join SearchQuery with precalculated QueryTrending
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
    details = [
        {
            "query": item.query_text,
            "count": item.total_count,
            "trending_score": float(item.trending_score) if hasattr(item, "trending_score") and item.trending_score else None
        }
        for item in db_results
    ]

    # Store structured details back into the routed Redis cache node
    cache_manager.set(cache_key, details, ttl=300)

    return {
        "suggestions": suggestions,
        "details": details,
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
        raise HTTPException(status_code=400, detail="Invalid query")
        
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
    
    # Extract query strings if cached items are in structured format
    suggestions_strings = None
    if cached_content is not None:
        if cached_content and isinstance(cached_content[0], dict):
            suggestions_strings = [item["query"] for item in cached_content]
        else:
            suggestions_strings = cached_content
            
    cache_status = "HIT" if cached_content is not None else "MISS"

    return CacheDebugResponse(
        prefix=normalized_prefix,
        routed_node=routed_node,
        circuit_state=circuit_state,
        cache_status=cache_status,
        cached_suggestions=suggestions_strings
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

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    System health check. Pings PostgreSQL database and all configured Redis nodes.
    Returns 503 if any service is down.
    """
    from sqlalchemy import text
    from fastapi import Response, status
    import json


    db_status = "healthy"
    redis_status = {}
    is_healthy = True

    # 1. Check PostgreSQL
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {e}"
        is_healthy = False

    # 2. Check Redis nodes
    for node in cache_manager.node_names:
        client = cache_manager.clients.get(node)
        if client:
            try:
                client.ping()
                redis_status[node] = "healthy"
            except Exception as e:
                redis_status[node] = f"unhealthy: {e}"
                is_healthy = False
        else:
            redis_status[node] = "unhealthy: client not initialized"
            is_healthy = False

    payload = {
        "status": "healthy" if is_healthy else "unhealthy",
        "database": db_status,
        "redis_nodes": redis_status,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Return 503 Service Unavailable if unhealthy
    if not is_healthy:
        return Response(content=json.dumps(payload), status_code=status.HTTP_503_SERVICE_UNAVAILABLE, media_type="application/json")
        
    return payload

@app.get("/cache/stats")
def get_cache_stats():
    """
    Returns detailed distributed cache hit, miss, and routing telemetry.
    """
    return cache_manager.stats.get_stats()

@app.get("/batch/stats")
def get_batch_stats():
    """
    Returns BatchWriter buffering, WAL journal queues, and write reduction performance statistics.
    """
    redis_qsize = 0
    client = batch_writer._get_redis_client()
    if client:
        try:
            redis_qsize = client.llen(batch_writer.journal_key)
        except Exception:
            pass

    return {
        "metrics": batch_writer.metrics,
        "flush_interval_seconds": batch_writer.flush_interval,
        "batch_size_limit": batch_writer.batch_limit,
        "memory_queue_size": batch_writer.memory_backup_queue.qsize(),
        "redis_wal_queue_size": redis_qsize,
        "write_reduction_percentage": round(
            (batch_writer.metrics["total_raw_writes_saved"] / batch_writer.metrics["queries_flushed"] * 100)
            if batch_writer.metrics["queries_flushed"] > 0 else 0.0,
            2
        )
    }

