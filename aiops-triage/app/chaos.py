"""Chaos switches for the Day 2 finale.

  CPU burn   -> HPA scales out, Prometheus CPU alert fires
  Error rate -> 5xx spike on /api/work, error-rate alert fires
  Not ready  -> readiness probe fails, pod leaves the Service

Disable in anything real with CHAOS_ENABLED=false."""
import random
import threading
import time

from . import metrics


class Chaos:
    def __init__(self):
        self._lock = threading.Lock()
        self.error_rate = 0          # percent of /api/work requests that fail
        self.latency_ms = 0          # extra wait on /api/work
        self.ready = True
        self.cpu_burn_until = 0.0

    def state(self) -> dict:
        remaining = max(0, round(self.cpu_burn_until - time.time()))
        return {"error_rate": self.error_rate, "latency_ms": self.latency_ms,
                "ready": self.ready, "cpu_burn_seconds_left": remaining}

    def set_error_rate(self, pct: int) -> None:
        with self._lock:
            self.error_rate = max(0, min(100, int(pct)))
        metrics.CHAOS_ERROR_RATE.set(self.error_rate)

    def set_latency(self, ms: int) -> None:
        with self._lock:
            self.latency_ms = max(0, min(10_000, int(ms)))

    def set_ready(self, ready: bool) -> None:
        with self._lock:
            self.ready = bool(ready)

    def should_fail(self) -> bool:
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        return self.error_rate > 0 and random.randint(1, 100) <= self.error_rate

    def burn_cpu(self, seconds: int) -> int:
        seconds = max(1, min(300, int(seconds)))
        with self._lock:
            if time.time() < self.cpu_burn_until:
                return round(self.cpu_burn_until - time.time())
            self.cpu_burn_until = time.time() + seconds

        def _spin():
            metrics.CHAOS_CPU_BURN.set(1)
            x = 0
            # re-reads cpu_burn_until so "Stop all chaos" ends the burn early
            while time.time() < self.cpu_burn_until:
                for _ in range(10_000):
                    x = (x * 31 + 7) % 1_000_003  # busy work
            metrics.CHAOS_CPU_BURN.set(0)

        threading.Thread(target=_spin, daemon=True).start()
        return seconds

    def reset(self) -> None:
        self.set_error_rate(0)
        self.set_latency(0)
        self.set_ready(True)
        with self._lock:
            self.cpu_burn_until = 0.0
