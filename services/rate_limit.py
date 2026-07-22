"""Simple in-memory sliding-window rate limiter (per key).

Thread-safe, dependency-free. Backs login brute-force protection and /ask
abuse throttling. Not shared across Cloud Run instances — each instance keeps
its own counters, which is acceptable as a first-line, per-instance guard.
"""

import time
from collections import deque
from threading import Lock


def client_ip(request) -> str:
    """Best-effort caller IP for rate-limit keys. Uses the direct peer address;
    behind a proxy (e.g. Cloud Run) this is the proxy IP — an X-Forwarded-For
    aware version is a known future refinement."""
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: float):
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._events: dict[str, deque] = {}
        self._lock = Lock()

    def hit(self, key: str) -> bool:
        """Record an event for ``key``. Return True if it is allowed (within
        the limit), False if the limit is exceeded within the window."""
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            dq = self._events.get(key)
            if dq is None:
                dq = deque()
                self._events[key] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max_events:
                return False
            dq.append(now)
            return True
