# Search Typeahead System

A highly scalable, distributed Search Typeahead System built with FastAPI, PostgreSQL, and a cached routing layer using Consistent Hashing across 3 Redis instances. The write path features a Batch Writer queue to throttle database operations.

## System Architecture

```mermaid
graph TD
    UI[Frontend: HTML/JS/CSS Web App] -->|1. GET /suggest?q=prefix| API[FastAPI Server]
    UI -->|2. POST /search {query}| API
    API -->|3. Consistent Hash Ring| CH[consistent_hash.py]
    CH -->|4. Route cache query| RC[Redis Cache Nodes: 3 instances]
    API -->|5. Fallback on cache miss| DB[(PostgreSQL Database)]
    API -->|6. Push to Queue| Q[In-Memory/Redis Queue]
    Q -->|7. Consume & Batch| BW[Batch Writer Background Service]
    BW -->|8. Upsert Bulk Counts| DB
    BW -->|9. Invalidate cached prefixes| RC
```

## Directory Structure

```text
TypeAhead/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── batch_writer.py      # Aggregates and flushes query counts
│   │   ├── cache.py             # Redis connection manager & circuit breaker
│   │   ├── consistent_hash.py   # Hashing ring with virtual nodes
│   │   ├── database.py          # SQLAlchemy PostgreSQL connection
│   │   ├── main.py              # FastAPI app endpoints
│   │   ├── models.py            # SQLAlchemy database tables
│   │   └── schemas.py
│   ├── scripts/
│   │   ├── generate_dataset.py  # Generates Zipfian 100k query dataset
│   │   └── ingest_data.py       # Ingests dataset using SQLAlchemy copy/bulk
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_api.py          # Unit tests for endpoints and API logic
│   │   └── test_consistent_hash.py # Tests for consistent hash ring mapping
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── index.html               # Glassmorphic search frontend
│   ├── style.css                # Custom UI styling (dark theme)
│   └── app.js                   # UI logic, debouncing, & diagnostics panel
├── docker-compose.yml
├── Makefile                     # Build & orchestration automations
├── .env.example                 # Environment variable templates
└── README.md
```

## Key Components

### 1. Consistent Hashing (`consistent_hash.py`)
- Maps search prefixes to one of the 3 active Redis nodes.
- Uses **MD5 hashing** to place nodes and keys on a circular ring (`0` to `2^128 - 1`).
- Implements **200 virtual nodes** per physical server to maintain uniform key distribution.
- Resilient to node addition/removal, causing only `K/N` keys to relocate (where `K` is keys and `N` is servers).

### 2. Cache Layer with Circuit Breakers (`cache.py`)
- Standard cache key naming: `suggest:{prefix}` storing up to 10 sorted suggestions.
- **Circuit Breaker Pattern**: If a specific Redis node fails (e.g. timeout or connection error) more than 3 times consecutively, its circuit breaker trips to `OPEN`.
- While `OPEN`, requests for keys hashing to that node bypass the cache to query PostgreSQL directly, preventing frontend latency spikes.
- Automatically transitions to `HALF-OPEN` after 10 seconds to attempt a probe recovery request.

### 3. Asynchronous Batch Writes (`batch_writer.py`)
- Throttles PostgreSQL write volume. `POST /search` calls append the query to a thread-safe memory queue and immediately return `{"message": "Searched"}`.
- A background worker aggregates duplicates in the queue and flushes them to the DB using a bulk SQL `UPSERT` transaction:
  ```sql
  INSERT INTO search_queries (query_text, total_count)
  VALUES (:query, :count)
  ON CONFLICT (query_text) DO UPDATE 
  SET total_count = search_queries.total_count + EXCLUDED.total_count;
  ```
- Executes cache invalidation for all affected query prefixes upon flush.

### 4. Recency-Aware Trending Searches
- Combines historical search frequency with a sliding window of recent activity.
- The `SearchActivity` table logs individual queries with timestamps.
- Trending query scores are calculated as:
  $$\text{Score} = 0.3 \times \text{total\_count} + 0.7 \times (\text{recent\_count\_last\_2\_hours} \times 10)$$
- Promotes newly viral searches quickly while retaining high-volume historical queries.

---

## Getting Started

### Prerequisites
- Docker & Docker Compose installed.

### Setup Instructions

1. **Spin Up Containers**:
   ```bash
   make up
   ```
   This builds the FastAPI service and launches PostgreSQL along with 3 Redis nodes.

2. **Generate the Dataset (100k+ Queries)**:
   Ensure you have python3 installed locally:
   ```bash
   make generate-data
   ```
   This generates a power-law distributed CSV dataset at `backend/scripts/queries.csv`.

3. **Ingest the Dataset**:
   Seed the PostgreSQL database with the generated queries:
   ```bash
   make seed-data
   ```

4. **Launch the Frontend**:
   Open `frontend/index.html` in any web browser.

---

## API Endpoints

- **GET `/suggest?q=<prefix>`**: Fetches autocomplete suggestions.
- **POST `/search`**: Submits a query. Payload: `{"query": "iphone"}`.
- **GET `/trending`**: Returns the top 10 trending queries.
- **GET `/cache/debug?prefix=<prefix>`**: Inspects cache routing (Redis node target, hit/miss status, cached items).
- **GET `/metrics`**: Returns write-reduction statistics from the batch writer.
