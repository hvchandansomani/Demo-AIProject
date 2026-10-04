"""Prometheus metrics for the Doubt Wall. These drive the Day 2 Grafana
dashboard and the HighErrorRate / HighLatency alerts."""
import time

from flask import g, request
from prometheus_client import Counter, Gauge, Histogram, Info

HTTP_REQUESTS = Counter(
    "doubtwall_http_requests_total", "HTTP requests", ["method", "endpoint", "status"]
)
HTTP_LATENCY = Histogram(
    "doubtwall_http_request_duration_seconds", "HTTP request latency", ["endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
DB_LATENCY = Histogram(
    "doubtwall_db_query_duration_seconds", "PostgreSQL query latency", ["query"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5),
)
DB_ERRORS = Counter(
    "doubtwall_db_errors_total", "Failed PostgreSQL queries", ["query"]
)
DOUBTS_CREATED = Counter("doubtwall_doubts_created_total", "Doubts posted")
UPVOTES = Counter("doubtwall_upvotes_total", "Upvotes given")
RATE_LIMITED = Counter("doubtwall_rate_limited_total", "Requests refused by rate limit", ["kind"])
DOUBTS_IN_DB = Gauge("doubtwall_doubts", "Doubts currently stored")
CHAOS_ERROR_RATE = Gauge("doubtwall_chaos_error_rate_percent", "Injected error rate")
CHAOS_CPU_BURN = Gauge("doubtwall_chaos_cpu_burn_active", "1 while a CPU burn runs")
CHAOS_LATENCY_MS = Gauge("doubtwall_chaos_latency_ms", "Injected extra latency")
APP_INFO = Info("doubtwall_app", "Build and runtime info")

_SKIP = ("/metrics", "/health", "/ready", "/static")


def init_metrics(app):
    cfg = app.config["CFG"]
    APP_INFO.info({"version": cfg.APP_VERSION, "instance": cfg.INSTANCE})

    @app.before_request
    def _start():
        g._t0 = time.perf_counter()

    @app.after_request
    def _record(resp):
        if request.path.startswith(_SKIP):
            return resp
        endpoint = request.url_rule.rule if request.url_rule else "unmatched"
        HTTP_REQUESTS.labels(request.method, endpoint, str(resp.status_code)).inc()
        HTTP_LATENCY.labels(endpoint).observe(time.perf_counter() - g.get("_t0", time.perf_counter()))
        return resp
