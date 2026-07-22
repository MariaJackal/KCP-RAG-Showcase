import time
from dataclasses import dataclass
from threading import Lock


@dataclass
class _Entry:
    value: object
    expires_at: float


class TTLCache:
    def __init__(self, ttl_seconds=600, max_entries=256):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store = {}
        self._lock = Lock()

    def _purge_expired(self):
        now = time.time()
        expired_keys = [k for k, v in self._store.items() if v.expires_at <= now]
        for k in expired_keys:
            self._store.pop(k, None)

    def get(self, key):
        with self._lock:
            self._purge_expired()
            entry = self._store.get(key)
            if entry is None:
                return None
            return entry.value

    def set(self, key, value):
        with self._lock:
            self._purge_expired()
            if len(self._store) >= self.max_entries:
                # Remove oldest by expiry to keep implementation simple and bounded.
                oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
                self._store.pop(oldest_key, None)
            self._store[key] = _Entry(value=value, expires_at=time.time() + self.ttl_seconds)

