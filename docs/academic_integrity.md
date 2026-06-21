# Academic Integrity & Viva (Mock Interview) Guide

This document explains the core algorithms, time/space complexities, and implementation details of the Search Typeahead System, designed to prepare you for mock interviews and vivas.

---

## 1. Consistent Hashing Ring (`backend/app/consistent_hash.py`)

### What is Consistent Hashing?
Consistent hashing is a distributed hashing scheme that allows hash tables to be scaled up or down without complete key re-mapping. In a traditional hashing scheme (e.g. `hash(key) % N` where $N$ is the number of servers), adding or removing a server changes $N$, causing almost **100% of the keys** to map to different servers.
In consistent hashing, both keys and servers are mapped to the same circular hash space (a ring of size $2^{128} - 1$ when using MD5). A key routes to the first server it encounters on the ring in a clockwise direction.

### The Role of Virtual Nodes
If we place only physical servers on the ring, they might be distributed unevenly, causing one server to handle a disproportionate amount of keys (hotspots/data skew).
To solve this, we implement **200 virtual nodes** per physical server:
- Instead of adding just `redis-1` to the ring, we add `redis-1-v-0`, `redis-1-v-1`, ..., `redis-1-v-199` to the ring.
- This distributes the server's presence evenly across the circle, ensuring a uniform distribution of cache keys.

### Core Functions Explained
1. **`_hash(key)`**: Computes the MD5 hash of the string key and converts it to a 128-bit integer.
2. **`add_node(node)`**: Loops 200 times to create virtual node names, hashes them, inserts them in sorted order into `self.ring` (using binary insertion `bisect.insort`), and maps each hash to the physical node name in `self.hash_to_node` dictionary.
3. **`remove_node(node)`**: Finds and deletes the node's 200 virtual hashes from the ring list and the mapping dictionary.
4. **`get_node(key)`**: 
   - Hashes the key.
   - Uses binary search (`bisect.bisect_right`) to find the first virtual node hash on the ring that is greater than or equal to the key's hash.
   - If the index is equal to the ring size, it wraps around to index `0` (the circular ring property).
   - Returns the physical server name mapped to that hash.

### Time & Space Complexities
- **Key Routing Lookup (`get_node`)**: $O(\log(M \times V))$ where $M$ is the number of servers and $V$ is the count of virtual nodes. (Binary search over the sorted ring).
- **Node Addition/Removal**: $O(V \log(M \times V))$ to insert/delete virtual nodes into a sorted list.
- **Space Complexity**: $O(M \times V)$ to store the ring hashes and dictionaries.

---

## 2. Asynchronous Batch Writer & Redis WAL (`backend/app/batch_writer.py`)

### Rationale
Writing directly to the database on every search request blocks thread execution and exhausts database connection pools. 
The system routes incoming searches to a Redis list queue (`system:search_write_journal`) located on a central coordinator node. This serves as our **Write-Ahead Log (WAL)**.
If the FastAPI web server crashes, the queries are preserved in the Redis container. On restart, the `BatchWriter` drains the queue and writes them to PostgreSQL.

### Core Functions Explained
1. **`add_query(query_text)`**: Normalizes search queries to lowercase, and pushes them to Redis list (`rpush`). If Redis is down, it falls back to a thread-safe local `memory_backup_queue`.
2. **`recover_from_journal()`**: Scans the Redis journal list during system startup. If queries are found, it triggers `flush()` to write them to PostgreSQL immediately.
3. **`flush()`**:
   - Atomically retrieves all elements from Redis journal using a pipeline `lrange` and `delete` block.
   - Aggregates queries in memory (e.g. `{"iphone": 5, "java": 2}`) to compress DB writes.
   - Executes a single bulk SQL upsert transaction using `ON CONFLICT DO UPDATE` in SQLAlchemy.
   - If database transaction fails, the queries are rolled back (pushed back to the head of the Redis list via `lpush`) to prevent data loss.
   - Executes cache invalidation for all prefix variations of the updated terms.

---

## 3. Recency-Aware Trending Scheduler (`backend/app/trending_scheduler.py`)

### Rationale
Trending searches cannot rely purely on total counts (otherwise historically popular items trend forever). We combine historical popularity with a sliding window of recent activity. 
To avoid executing complex grouping SQL queries on the lookup path, a background worker precomputes these values.

### The Scoring Formula
$$\text{Score} = \alpha \times \text{Historical\_Count} + \beta \times (\text{Recency\_Count} \times 10)$$

- **Historical Component**: Total counts stored in `SearchQuery` table, weighted by $\alpha = 0.2$. Keeps popular searches visible.
- **Recency Component**: Counts in the last 2 hours from `SearchActivity` table, weighted by $\beta = 0.8$. Boosts trending terms.

### Core Functions Explained
1. **`compute_trending()`**:
   - Queries `SearchActivity` to count searches grouped by query string over the past 2 hours.
   - Resolves all queries that have recent activity or are currently trending in the DB.
   - Retrieves historical counts from `SearchQuery`.
   - Computes trending scores.
   - Performs an atomic database update (deletes old trending entries and inserts newly calculated scores).

---

## 4. Cache Managers & Circuit Breakers (`backend/app/cache.py`)

### What is a Circuit Breaker?
A circuit breaker protects database systems during cache outages. If a Redis node fails, requests would normally hang or fail, causing a cascading outage. A circuit breaker detects failures and routes requests directly to the DB without querying the failing Redis node.

### State Transitions
- **`CLOSED`**: Normal operation. Requests route to Redis.
- **`OPEN`**: Node has failed more than 3 times. Requests bypass the cache directly to the DB.
- **`HALF-OPEN`**: After 10 seconds, one probe request is sent to Redis. If it succeeds, the circuit closes. If it fails, the circuit re-opens.
