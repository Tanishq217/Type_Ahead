import hashlib
import bisect
from typing import Dict, List

class ConsistentHashRing:
    """
    Consistent Hash Ring for distributing cache keys across multiple Redis nodes.
    Supports virtual nodes to ensure even distribution and minimize key movement
    when nodes are added or removed.
    """
    def __init__(self, nodes: List[str] = None, virtual_nodes_count: int = 200):
        self.virtual_nodes_count = virtual_nodes_count
        self.ring: List[int] = []  # Sorted list of virtual node hashes
        self.hash_to_node: Dict[int, str] = {}  # Mapping from virtual node hash to physical node
        self.nodes = set()

        if nodes:
            for node in nodes:
                self.add_node(node)

    def _hash(self, key: str) -> int:
        """MD5 hash helper converting string keys to integer representation."""
        return int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16)

    def add_node(self, node: str) -> None:
        """Adds a physical node and its virtual nodes to the hash ring."""
        if node in self.nodes:
            return
        self.nodes.add(node)
        for i in range(self.virtual_nodes_count):
            # Virtual node tag to differentiate virtual instances of the same node
            vnode_name = f"{node}-v-{i}"
            vnode_hash = self._hash(vnode_name)
            bisect.insort(self.ring, vnode_hash)
            self.hash_to_node[vnode_hash] = node

    def remove_node(self, node: str) -> None:
        """Removes a physical node and its virtual nodes from the hash ring."""
        if node not in self.nodes:
            return
        self.nodes.remove(node)
        for i in range(self.virtual_nodes_count):
            vnode_name = f"{node}-v-{i}"
            vnode_hash = self._hash(vnode_name)
            idx = bisect.bisect_left(self.ring, vnode_hash)
            if idx < len(self.ring) and self.ring[idx] == vnode_hash:
                del self.ring[idx]
            self.hash_to_node.pop(vnode_hash, None)

    def get_node(self, key: str) -> str:
        """Determines the physical node responsible for a given key."""
        if not self.ring:
            raise ValueError("Consistent Hash Ring is empty")

        key_hash = self._hash(key)
        # Find position to insert key_hash to maintain sorted order
        idx = bisect.bisect_right(self.ring, key_hash)

        # Wrap around to the beginning if index exceeds ring length
        if idx == len(self.ring):
            idx = 0

        return self.hash_to_node[self.ring[idx]]
