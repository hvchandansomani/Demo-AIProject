"""Small in-memory sliding-window rate limiter (per pod).

Good enough for a classroom: it stops one learner (or a script) from
flooding the wall. Real systems would do this at the Ingress or in Redis."""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_sweep = time.time()

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if now - self._last_sweep > 300:  # drop idle keys so memory stays flat
                for k in [k for k, v in self._hits.items() if not v or v[-1] < now - window]:
                    del self._hits[k]
                self._last_sweep = now
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()
