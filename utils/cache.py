import time
from typing import Any, Optional


class TTLCache:
    """In-memory cache with per-key TTL expiration."""

    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, tuple[Any, float]] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
        value, expires_at = self._store[key]
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        self._store[key] = (value, time.time() + ttl)

    def clear(self) -> None:
        self._store.clear()


# Global cache instance (TTL: 5 minutes)
cache = TTLCache(default_ttl=300)
