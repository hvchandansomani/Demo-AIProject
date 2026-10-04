"""Chaos switches, driven from the AIOps Triage console on Day 2.

  errors  -> a share of learner API calls return 500  (HighErrorRate alert)
  latency -> every learner API call waits extra ms    (HighLatency alert)
  cpu     -> one core spins for N seconds             (HPA scale-out, CPU alarm)
  ready   -> /ready returns 503                       (pod leaves the Service)
"""
import random
import threading
import time

from . import metrics


class Chaos:
    def __init__(self):
        self._lock = threading.Lock()
        self.error_rate = 0
        self.latency_ms = 0
        self.ready = True
        self.cpu_burn_until = 0.0

    def state(self) -> dict:
        return {
            "error_rate": self.error_rate,
            "latency_ms": self.latency_ms,
            "ready": self.ready,
            "cpu_burn_seconds_left": max(0, round(self.cpu_burn_until - time.time())),
        }

    def set_error_rate(self, pct) -> None:
        self.error_rate = max(0, min(100, int(pct)))
        metrics.CHAOS_ERROR_RATE.set(self.error_rate)

    def set_latency(self, ms) -> None:
        self.latency_ms = max(0, min(10_000, int(ms)))
        metrics.CHAOS_LATENCY_MS.set(self.latency_ms)

    def set_ready(self, ready) -> None:
        self.ready = bool(ready)

    def apply(self) -> bool:
        """Called before each learner API request. Returns True to fail it."""
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        return self.error_rate > 0 and random.randint(1, 100) <= self.error_rate

    def burn_cpu(self, seconds) -> int:
        seconds = max(1, min(300, int(seconds)))
        with self._lock:
            if time.time() < self.cpu_burn_until:
                return round(self.cpu_burn_until - time.time())
            self.cpu_burn_until = time.time() + seconds

        def _spin():
            metrics.CHAOS_CPU_BURN.set(1)
            x = 0
            while time.time() < self.cpu_burn_until:
                for _ in range(10_000):
                    x = (x * 31 + 7) % 1_000_003
            metrics.CHAOS_CPU_BURN.set(0)

        threading.Thread(target=_spin, daemon=True).start()
        return seconds

    def reset(self) -> None:
        self.set_error_rate(0)
        self.set_latency(0)
        self.set_ready(True)
        self.cpu_burn_until = 0.0
