# 🚀 Distributed Search Autocomplete & TypeAhead System

Have you ever wondered how Google suggests terms like **"what is python"** or **"how to learn coding"** before you even finish typing? 

This project is a **High-Performance Distributed Autocomplete System** built to handle millions of queries with sub-3ms response times. It demonstrates how modern scale architectures route cache keys across multiple servers, buffer database writes to prevent crashes, and calculate viral search trends.

---

## ⚡ Quick Start: Spin Up Everything in 1 Second

You can build the system, start the database and caching servers, populate the dataset, and launch the web interface in your browser with **one single command**:

```bash
docker-compose up -d --build && open http://localhost:8000
```
*(If you are on Docker Compose V2, you can also use `docker compose` instead of `docker-compose`).*

### What this single command does automatically:
1.  **Starts 5 Isolated Servers**: Boots the FastAPI web server (`typeahead-web`), the PostgreSQL database (`typeahead-db`), and 3 independent Redis cache nodes (`redis-1`, `redis-2`, `redis-3`).
2.  **Generates & Seeds 105,000+ Queries**: The FastAPI server detects that the database is fresh, generates a Zipfian (power-law) distributed CSV dataset, and seeds the PostgreSQL database with all 105,000 terms in **under 2 seconds**.
3.  **Opens the Visual Interface**: Automatically opens your default web browser to `http://localhost:8000` to access the premium search dashboard!

---

## 🔍 Verification & Testing Guide

To see the system working, navigate to the web UI at `http://localhost:8000` and try these test cases:

### 1. Autocomplete Search Examples (Head Queries)
The database is pre-seeded with highly realistic question phrases. You can type these prefixes in the search input box to test autocomplete suggestions:
*   Type **`wh`** ➡️ Suggestions: *"what is python"*, *"what is consistent hashing"*, *"what is docker"*...
*   Type **`ho`** ➡️ Suggestions: *"how to learn coding"*, *"how to build typeahead"*, *"how to use docker compose"*...
*   Type **`wi`** ➡️ Suggestions: *"will there be database fallbacks"*, *"will there be consistent routing"*...

### 2. Search Submissions (`POST /search` & WAL buffering)
1.  Type a new query like **`what is a database index`** and press **Enter** (or click the search button).
2.  A success banner will read **`Searched`** (this is the API response).
3.  Observe the **WAL Queue Size** increment under the *Batch Ingestion* panel. Every 3 seconds, a background daemon flushes this queue, updates PostgreSQL, and clears the cache for that prefix.

### 3. Basic vs. Trending Sorting (Demonstrating Recency Weighting)
1.  Toggle **Recency-Aware Trending Mode** on the UI.
2.  Type a phrase like **`what is`** to reveal the **Real-time Ranking Comparison Dashboard**.
3.  *Basic Popularity* lists results strictly by all-time search counts.
4.  *Trending* elevates terms that have been searched multiple times in the last few minutes, showing how the scoring formula weights recency.

---

## 🛠️ The 4 Core Engineering Concepts Explained

Here is a technical overview of the core architectural patterns implemented in this system:

### 1. Consistent Hashing Caching Ring (`consistent_hash.py`)
*   **The Problem**: If we have 3 Redis cache servers and assign keys using a simple modulo hash (`hash(key) % 3`), adding or removing a Redis node changes the denominator. This immediately invalidates **100% of our cache keys**, causing a database overload.
*   **The Solution**: We map both cache keys and Redis servers onto a circular mathematical ring ($2^{128} - 1$ size). A key is routed to the first Redis server it encounters clockwise on the ring.
*   **Virtual Nodes**: To prevent one Redis node from receiving more than its share of traffic (hotspots), we map **200 virtual node hashes** per physical server, ensuring a standard deviation of **<0.5% key distribution** (highly uniform).

### 2. Redis-Backed Write-Ahead Log (WAL) Batch Ingestor (`batch_writer.py`)
*   **The Problem**: Writing search updates directly to the database disk on every keystroke/search kills I/O throughput.
*   **The Solution**: When you search, the API pushes the query text to a Redis List queue (Write-Ahead Log) and returns `200 OK` instantly. A background worker drains the queue every 3 seconds, aggregates duplicates (e.g. 50 counts of "python" become 1 SQL update), and does a bulk `UPSERT` to PostgreSQL.
*   **Failure Recovery**: If PostgreSQL goes down, transactions roll back, and queries are re-queued back into Redis. If the web server crashes, buffered writes are safely persisted in Redis.

### 3. Recency-Aware Trending Engine (`trending_scheduler.py`)
*   **The Problem**: If a search query is historically popular (e.g. "iphone"), it will rank top forever. We need viral queries (e.g. "earthquake") to trend immediately.
*   **The Scoring Formula**: 
    $$\text{Score} = 0.2 \times \text{Historical\_Count} + 0.8 \times (\text{Recency\_Count} \times 10)$$
*   We log every search timestamp in `SearchActivity`. A background scheduler recalculates scores in a sliding window of the last 2 hours and updates the `QueryTrending` table, preventing stale items from over-ranking permanently.

### 4. What is a "Circuit Breaker"? (`cache.py`)
*   **The Problem**: If one of our Redis nodes crashes, any request routed to it will hang, waiting for a connection timeout, causing a slow, laggy UI experience.
*   **The Solution**: Just like an electrical fuse box in your home, we wrap every Redis node in a **Circuit Breaker** state machine:
    *   **`CLOSED`**: Caches are online. Requests route to Redis.
    *   **`OPEN`**: If Redis fails 3 times, the breaker "blows." Requests bypass Redis and go straight to PostgreSQL fallback immediately, keeping page response time under 10ms.
    *   **`HALF-OPEN`**: After 10 seconds, one test query is sent to Redis. If it succeeds, the breaker closes (caching resumes). If it fails, the breaker stays open.

---

## 📈 System Metrics & telemetries

We have exposed real-time diagnostic indicators directly in the UI footer and CLI scripts:
*   **Cache Routing Diagnostics**: Displays response latency in milliseconds, cache HIT/MISS status, routed cache server (`redis-1`, `redis-2`, `redis-3`), and Circuit Breaker states (`CLOSED`, `OPEN`, `HALF-OPEN`).
*   **Distributed Cache Statistics (`GET /cache/stats`)**: Reports hit rate and count logs.
*   **Batch Ingestion Statistics (`GET /batch/stats`)**: Reports total database writes saved and WAL queue length.

To run tests:
```bash
make test
```
To run the keys hashing distribution check:
```bash
docker-compose exec web python scripts/cache_distribution_check.py
```
To run the concurrency latency stress test:
```bash
docker-compose exec web python scripts/load_test.py
```
