import pytest
from app.consistent_hash import ConsistentHashRing

def test_consistent_hash_ring_init():
    ring = ConsistentHashRing(nodes=["redis-1", "redis-2", "redis-3"], virtual_nodes_count=200)
    assert len(ring.nodes) == 3
    assert len(ring.ring) == 600  # 3 nodes * 200 virtual nodes

def test_consistent_hash_node_routing():
    ring = ConsistentHashRing(nodes=["redis-1", "redis-2", "redis-3"], virtual_nodes_count=100)
    
    # Check that keys consistently route to the same node
    node_a = ring.get_node("iphone")
    node_b = ring.get_node("iphone")
    assert node_a == node_b
    
    # Test routing to different nodes for different keys
    nodes = {ring.get_node(f"key-{i}") for i in range(100)}
    assert len(nodes) > 1  # Should distribute across multiple nodes

def test_node_addition_minimal_remapping():
    """
    Adding a node should only remap approximately 1/(N+1) of the keys.
    For 3 -> 4 nodes, that's roughly 25% of keys remapped.
    """
    nodes = ["redis-1", "redis-2", "redis-3"]
    ring = ConsistentHashRing(nodes=nodes, virtual_nodes_count=200)
    
    keys = [f"query_key_{i}" for i in range(1000)]
    original_mappings = {key: ring.get_node(key) for key in keys}
    
    # Add new node
    ring.add_node("redis-4")
    
    new_mappings = {key: ring.get_node(key) for key in keys}
    
    # Calculate how many keys remapped
    remapped_count = 0
    for key in keys:
        if original_mappings[key] != new_mappings[key]:
            remapped_count += 1
            # The newly mapped node MUST be the new node (redis-4)
            assert new_mappings[key] == "redis-4"
            
    # Remapped percentage should be roughly 25% (under 35% in actual run with 1000 keys)
    remapped_ratio = remapped_count / len(keys)
    print(f"Remapped ratio on node addition: {remapped_ratio:.2%}")
    assert remapped_ratio < 0.35
    assert remapped_ratio > 0.15

def test_node_removal_minimal_remapping():
    """
    Removing a node should only remap keys that were previously mapped to that node.
    Keys mapped to other nodes should not move.
    """
    nodes = ["redis-1", "redis-2", "redis-3", "redis-4"]
    ring = ConsistentHashRing(nodes=nodes, virtual_nodes_count=200)
    
    keys = [f"query_key_{i}" for i in range(1000)]
    original_mappings = {key: ring.get_node(key) for key in keys}
    
    # Remove node
    ring.remove_node("redis-4")
    
    new_mappings = {key: ring.get_node(key) for key in keys}
    
    remapped_count = 0
    for key in keys:
        if original_mappings[key] != new_mappings[key]:
            remapped_count += 1
            # Only keys previously mapped to redis-4 should be remapped
            assert original_mappings[key] == "redis-4"
            
    # Remapped count must match exactly the number of keys originally mapped to redis-4
    original_redis4_count = sum(1 for node in original_mappings.values() if node == "redis-4")
    assert remapped_count == original_redis4_count
