"""In-memory incident store.

Teaching note: this is per-pod memory. When the HPA scales to 3 replicas,
each pod has its own list — refresh the UI and you'll see different
incidents depending on which pod answered. That's the "why stateless
apps need external storage (Redis/RDS)" discussion for Day 2."""
import itertools
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Incident:
    id: int
    source: str            # manual | alertmanager | cloudwatch
    title: str
    raw: str
    created_at: str = field(default_factory=utcnow)
    severity: str = "info"  # critical | warning | info
    analysis: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)  # mode, latency_ms, tokens, cost, instance

    def to_dict(self) -> dict:
        return asdict(self)


class IncidentStore:
    def __init__(self, max_items: int = 100):
        self._items: deque = deque(maxlen=max_items)
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def add(self, source: str, title: str, raw: str) -> Incident:
        inc = Incident(id=next(self._ids), source=source, title=title, raw=raw)
        with self._lock:
            self._items.appendleft(inc)
        return inc

    def list(self, limit: int = 50) -> list:
        with self._lock:
            return [i.to_dict() for i in list(self._items)[:limit]]

    def get(self, incident_id: int) -> Optional[Incident]:
        with self._lock:
            return next((i for i in self._items if i.id == incident_id), None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict:
        with self._lock:
            items = list(self._items)
        analysed = [i for i in items if i.meta]
        latencies = [i.meta.get("latency_ms", 0) for i in analysed]
        return {
            "total": len(items),
            "critical": sum(1 for i in items if i.severity == "critical"),
            "warning": sum(1 for i in items if i.severity == "warning"),
            "info": sum(1 for i in items if i.severity == "info"),
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
            "tokens": sum(i.meta.get("input_tokens", 0) + i.meta.get("output_tokens", 0) for i in analysed),
            "cost_usd": round(sum(i.meta.get("cost_usd", 0.0) for i in analysed), 5),
        }
