# API Specification & Endpoints Documentation

The Search Typeahead System exposes REST APIs for autocomplete suggestions, searches, diagnostics, and system monitoring.

---

## 1. Autocomplete Suggestions API

### `GET /suggest`
Fetches suggestion results matching a given prefix.

#### Request Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `q` | `string` | Yes | The prefix search string to match suggestions. |
| `trending` | `boolean` | No | Toggle recency-aware trending suggestions mode. Default: `false`. |

#### Response Example (Cache Hit)
```json
{
   "suggestions" : [
      "iphone",
      "iphone 15",
      "iphone charger"
   ],
   "details" : [
      { "query": "iphone", "count": 1000, "trending_score": null },
      { "query": "iphone 15", "count": 500, "trending_score": null },
      { "query": "iphone charger", "count": 200, "trending_score": null }
   ],
   "latency_ms" : 2.72,
   "source" : "cache",
   "cache_node" : "redis-2",
   "circuit_state" : "CLOSED"
}
```

#### Response Example (Database Fallback / Cache Miss)
```json
{
   "suggestions" : [
      "latest fastapi show",
      "latest linux software"
   ],
   "details" : [
      { "query": "latest fastapi show", "count": 800000, "trending_score": 240021.9 },
      { "query": "latest linux software", "count": 10500, "trending_score": 2100.0 }
   ],
   "latency_ms" : 113.02,
   "source" : "database",
   "cache_node" : "redis-2",
   "circuit_state" : "CLOSED",
   "db_latency_ms" : 110.43
}
```

---

## 2. Comparative Rankings API

### `GET /suggest/compare`
Exposes autocomplete lists in both Basic (historical popularity) and Trending (recency-aware) sorting side-by-side for comparison.

#### Request Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `q` | `string` | Yes | The prefix search string. |

#### Response Example
```json
{
   "prefix" : "xyz",
   "basic_suggestions" : [
      "xyz normal",
      "xyz active"
   ],
   "trending_suggestions" : [
      "xyz active",
      "xyz normal"
   ]
}
```

---

## 3. Search Submission API

### `POST /search`
Submits a query to the WAL write journal queue.

#### Request Body
```json
{
  "query": "rustlang tutorial"
}
```

#### Response Example
```json
{
  "message": "Searched"
}
```

---

## 4. Cache Debug API

### `GET /cache/debug`
Inspects consistent hash routing mapping for a key prefix.

#### Request Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `prefix` | `string` | Yes | The query prefix key to inspect. |
| `trending` | `boolean` | No | Check the trending cache key path. Default: `false`. |

#### Response Example
```json
{
   "prefix" : "l",
   "routed_node" : "redis-2",
   "circuit_state" : "CLOSED",
   "cache_status" : "HIT",
   "cached_suggestions" : [
      "latest fastapi show",
      "latest linux software"
   ]
}
```

---

## 5. Trending Searches API

### `GET /trending`
Retrieves precalculated top 10 trending searches.

#### Response Example
```json
{
   "latency_ms" : 11.62,
   "trending" : [
      {
         "query" : "iphone 15",
         "recent_count" : 8,
         "score" : 65.6,
         "total_count" : 8
      },
      {
         "query" : "react native",
         "recent_count" : 4,
         "score" : 32.8,
         "total_count" : 4
      }
   ]
}
```

---

## 6. System Health check API

### `GET /health`
Pings database and Redis containers. Returns `200 OK` or `503 Service Unavailable`.

#### Response Example (Healthy)
```json
{
   "status" : "healthy",
   "database" : "healthy",
   "redis_nodes" : {
      "redis-1" : "healthy",
      "redis-2" : "healthy",
      "redis-3" : "healthy"
   },
   "timestamp" : "2026-06-22T00:01:14.283120"
}
```

---

## 7. Distributed Cache Statistics API

### `GET /cache/stats`
Exposes hit rate telemetry tracked by the stats manager.

#### Response Example
```json
{
   "total_requests" : 1205,
   "hits" : 1175,
   "misses" : 30,
   "hit_rate" : 0.9751,
   "hits_by_prefix" : {
      "ip" : 500,
      "lat" : 200
   },
   "misses_by_prefix" : {
      "xyz" : 10,
      "rustl" : 2
   }
}
```

---

## 8. Batch Writer Statistics API

### `GET /batch/stats`
Exposes the performance metrics of the WAL batch buffer.

#### Response Example
```json
{
   "metrics" : {
      "total_raw_writes_saved" : 4,
      "total_db_transactions" : 1,
      "queries_flushed" : 5,
      "redis_wal_pushes" : 5,
      "redis_wal_failures" : 0,
      "recovered_queries_count" : 0
   },
   "flush_interval_seconds" : 3.0,
   "batch_size_limit" : 100,
   "memory_queue_size" : 0,
   "redis_wal_queue_size" : 0,
   "write_reduction_percentage" : 80.0
}
```
