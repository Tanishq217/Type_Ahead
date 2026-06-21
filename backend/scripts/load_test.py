import time
import random
import threading
import statistics
import http.client
import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

# Configurable parameters for load test
API_HOST = "localhost"
API_PORT = 8000
TOTAL_REQUESTS = 1200
CONCURRENT_WORKERS = 15  # Simulates 15 active concurrent threads/users

# Prefixes to test against, including cached and uncached variations
TEST_PREFIXES = [
    "i", "ip", "iph", "iphone", 
    "l", "la", "lat", "latest",
    "x", "xy", "xyz",
    "p", "py", "pyt", "python",
    "j", "ja", "jav", "java",
    "r", "re", "rea", "react"
]

latencies = []
latencies_lock = threading.Lock()

def send_suggest_request(prefix: str):
    """Sends a single HTTP GET request to /suggest endpoint and records latency."""
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=2.0)
    try:
        # Urlencode query string
        params = urllib.parse.urlencode({"q": prefix})
        url = f"/suggest?{params}"
        
        start_time = time.time()
        conn.request("GET", url)
        response = conn.getresponse()
        response.read() # Read body to complete request
        latency = (time.time() - start_time) * 1000 # ms
        
        if response.status == 200:
            with latencies_lock:
                latencies.append(latency)
        else:
            print(f"Error response: {response.status}")
    except Exception as e:
        print(f"Connection failure during test: {e}")
    finally:
        conn.close()

def run_load_test():
    print(f"Starting load test on http://{API_HOST}:{API_PORT}/suggest...")
    print(f"Simulating {TOTAL_REQUESTS} total requests with {CONCURRENT_WORKERS} concurrent threads...")
    
    start_time = time.time()
    
    # Generate request list
    requests_prefixes = [random.choice(TEST_PREFIXES) for _ in range(TOTAL_REQUESTS)]
    
    # Run requests concurrently using ThreadPool
    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        executor.map(send_suggest_request, requests_prefixes)
        
    duration = time.time() - start_time
    
    # Calculate performance metrics
    with latencies_lock:
        count = len(latencies)
        if count == 0:
            print("No successful requests recorded.")
            return
        
        sorted_latencies = sorted(latencies)
        avg_latency = sum(latencies) / count
        p50 = sorted_latencies[int(count * 0.50)]
        p95 = sorted_latencies[int(count * 0.95)]
        p99 = sorted_latencies[int(count * 0.99)]
        min_lat = sorted_latencies[0]
        max_lat = sorted_latencies[-1]
        throughput = count / duration

    # 3. Retrieve system-wide cache metrics at the end of the test
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=2.0)
    cache_hit_rate = "N/A"
    try:
        conn.request("GET", "/cache/stats")
        res = conn.getresponse()
        if res.status == 200:
            stats = json.loads(res.read().decode())
            cache_hit_rate = f"{stats.get('hit_rate', 0.0) * 100:.2f}%"
    except Exception as e:
        print(f"Failed to query cache metrics: {e}")
    finally:
        conn.close()

    # Output Markdown Performance Report
    print("\n" + "="*50)
    print("           PERFORMANCE TESTING REPORT")
    print("="*50)
    print(f"Total Requests Processed : {count}/{TOTAL_REQUESTS}")
    print(f"Concurrency level        : {CONCURRENT_WORKERS} threads")
    print(f"Total Test Duration      : {duration:.2f} seconds")
    print(f"Request Throughput       : {throughput:.2f} req/sec")
    print(f"System Cache Hit Rate    : {cache_hit_rate}")
    print("\nLatencies Percentiles:")
    print(f"  Min Latency            : {min_lat:.2f} ms")
    print(f"  Average Latency        : {avg_latency:.2f} ms")
    print(f"  p50 (Median)           : {p50:.2f} ms")
    print(f"  p95                    : {p95:.2f} ms")
    print(f"  p99 (Tail Latency)     : {p99:.2f} ms")
    print(f"  Max Latency            : {max_lat:.2f} ms")
    print("="*50)

if __name__ == "__main__":
    run_load_test()
