import os
import time
import json
import logging
from typing import Dict, List, Optional, Tuple
import redis
from .consistent_hash import ConsistentHashRing

logger = logging.getLogger(__name__)

class CircuitBreaker:
    """
    Circuit Breaker pattern implementation for individual Redis cache nodes.
    States:
    - CLOSED: Normal operation, requests are allowed to proceed.
    - OPEN: Requests bypass the cache directly to the DB due to recent failures.
    - HALF-OPEN: Probe request allowed to verify if the node has recovered.
    """
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 10.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_state_change = 0.0

    def record_success(self):
        """Resets the failure counter and closes the circuit."""
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self):
        """Increments failure count and trips the circuit to OPEN if threshold reached."""
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            self.last_state_change = time.time()
            logger.error(f"Circuit breaker tripped to OPEN. Failure threshold of {self.failure_threshold} reached.")

    def allow_request(self) -> bool:
        """Determines if requests should be sent to the cache node."""
        if self.state == "CLOSED":
            return True
        
        if self.state == "OPEN":
            # Check if the recovery timeout has elapsed
            if time.time() - self.last_state_change > self.recovery_timeout:
                self.state = "HALF-OPEN"
                self.last_state_change = time.time()
                logger.info("Circuit breaker entering HALF-OPEN. Attempting cache probe request.")
                return True
            return False
            
        # In HALF-OPEN state, allow a probe request
        return True


class CacheManager:
    """
    Manages connections to multiple Redis nodes, handles key routing using consistent hashing,
    and applies circuit breakers to prevent cascading database latency during cache failures.
    """
    def __init__(self):
        # Expecting env format: "redis-1:6379,redis-2:6379,redis-3:6379"
        redis_endpoints = os.getenv("REDIS_NODES", "redis-1:6379,redis-2:6379,redis-3:6379")
        self.clients: Dict[str, redis.Redis] = {}
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.node_names: List[str] = []

        for endpoint in redis_endpoints.split(","):
            if not endpoint:
                continue
            try:
                host, port = endpoint.split(":")
                # Node name is mapped to its address/container hostname
                node_name = host
                self.node_names.append(node_name)
                
                # Setup connection pool
                pool = redis.ConnectionPool(
                    host=host,
                    port=int(port),
                    socket_timeout=1.0,
                    socket_connect_timeout=1.0,
                    decode_responses=True
                )
                self.clients[node_name] = redis.Redis(connection_pool=pool)
                self.circuit_breakers[node_name] = CircuitBreaker()
            except Exception as e:
                logger.error(f"Failed to initialize Redis client for {endpoint}: {e}")

        # Initialize the consistent hash ring
        self.ring = ConsistentHashRing(nodes=self.node_names, virtual_nodes_count=200)

    def get_route_info(self, key: str) -> Tuple[str, str]:
        """Returns the routed node name and the circuit status for a key."""
        if not self.node_names:
            return "none", "DISABLED"
        try:
            node = self.ring.get_node(key)
            cb = self.circuit_breakers[node]
            return node, cb.state
        except Exception:
            return "none", "ERROR"

    def get(self, key: str) -> Optional[List[str]]:
        """Gets a list of suggestions from the cache using consistent hash routing."""
        if not self.node_names:
            return None

        try:
            node = self.ring.get_node(key)
        except Exception as e:
            logger.error(f"Consistent hash routing failed: {e}")
            return None

        client = self.clients.get(node)
        cb = self.circuit_breakers.get(node)

        if not client or not cb or not cb.allow_request():
            # Bypass cache (either node not found or circuit is open)
            return None

        try:
            val = client.get(key)
            # Request succeeded, notify circuit breaker
            cb.record_success()
            if val:
                return json.loads(val)
        except redis.RedisError as e:
            logger.warning(f"Cache node {node} error during GET: {e}")
            cb.record_failure()
        
        return None

    def set(self, key: str, value: List[str], ttl: int = 300) -> bool:
        """Sets a list of suggestions in the cache using consistent hash routing."""
        if not self.node_names:
            return False

        try:
            node = self.ring.get_node(key)
        except Exception as e:
            logger.error(f"Consistent hash routing failed: {e}")
            return False

        client = self.clients.get(node)
        cb = self.circuit_breakers.get(node)

        if not client or not cb or not cb.allow_request():
            return False

        try:
            client.set(key, json.dumps(value), ex=ttl)
            cb.record_success()
            return True
        except redis.RedisError as e:
            logger.warning(f"Cache node {node} error during SET: {e}")
            cb.record_failure()
            
        return False

    def delete(self, key: str) -> bool:
        """Invalidates a cache key on the routed node."""
        if not self.node_names:
            return False

        try:
            node = self.ring.get_node(key)
        except Exception as e:
            logger.error(f"Consistent hash routing failed: {e}")
            return False

        client = self.clients.get(node)
        cb = self.circuit_breakers.get(node)

        if not client or not cb or not cb.allow_request():
            return False

        try:
            client.delete(key)
            cb.record_success()
            return True
        except redis.RedisError as e:
            logger.warning(f"Cache node {node} error during DELETE: {e}")
            cb.record_failure()
            
        return False


# Singleton instance to be imported across backend components
cache_manager = CacheManager()
