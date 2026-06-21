import sys
import os

# Add backend directory to sys.path to resolve imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.consistent_hash import ConsistentHashRing

def check_distribution(key_count: int = 10000):
    """
    Simulates hashing and mapping key_count keys across a 3-node Redis cluster.
    Outputs a distribution report to verify ring balance and lack of data skew.
    """
    nodes = ["redis-1", "redis-2", "redis-3"]
    ring = ConsistentHashRing(nodes=nodes, virtual_nodes_count=200)
    
    distribution = {node: 0 for node in nodes}
    
    # Simulate generating unique keys
    for i in range(key_count):
        key = f"suggest:prefix_lookup_{i}"
        routed_node = ring.get_node(key)
        distribution[routed_node] += 1
        
    print("=" * 60)
    print(f"      CONSISTENT HASH RING KEY DISTRIBUTION REPORT ({key_count:,} Keys)")
    print("=" * 60)
    print(f"Total physical nodes: {len(nodes)}")
    print(f"Virtual nodes/server: {ring.virtual_nodes_count}")
    print(f"Total virtual nodes : {len(ring.ring)}")
    print("-" * 60)
    
    for node, count in distribution.items():
        percentage = (count / key_count) * 100
        print(f" Node: {node:<10} | Key Count: {count:<6} | Share: {percentage:.2f}%")
        
    print("=" * 60)
    
    # Assert balanced distribution (standard deviation / variance check)
    # Expected share is ~33.33% per server. Standard deviation should be extremely low (< 5%).
    shares = [count / key_count for count in distribution.values()]
    avg_share = 1 / len(nodes)
    variance = sum((s - avg_share) ** 2 for s in shares) / len(nodes)
    std_dev = variance ** 0.5
    
    print(f"Distribution Standard Deviation: {std_dev:.4%}")
    if std_dev < 0.05:
        print("STATUS: SUCCESS (Key distribution is highly uniform and balanced!)")
    else:
        print("STATUS: WARNING (Key distribution variance is higher than expected!)")
    print("=" * 60)

if __name__ == "__main__":
    check_distribution()
