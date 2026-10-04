"""Prometheus metrics. Scraped at /metrics by Prometheus (Day 2).

The llm_* metrics are the AIOps-specific ones: they let a Grafana panel
show AI latency, token usage and estimated spend per hour."""
import time

from flask import g, request
from prometheus_client import Counter, Gauge, Histogram, Info

HTTP_REQUESTS = Counter(
    "aiops_http_requests_total", "HTTP requests", ["method", "endpoint", "status"]
)
HTTP_LATENCY = Histogram(
    "aiops_http_request_duration_seconds", "HTTP request latency", ["endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
LLM_REQUESTS = Counter(
    "aiops_llm_requests_total", "Analysis calls", ["mode", "status"]
)
LLM_LATENCY = Histogram(
    "aiops_llm_latency_seconds", "Time spent getting an analysis", ["mode"],
    buckets=(0.05, 0.25, 0.5, 1, 2, 4, 8, 15, 30),
)
LLM_TOKENS = Counter(
    "aiops_llm_tokens_total", "Tokens used by Claude", ["direction"]
)
LLM_COST = Counter(
    "aiops_llm_cost_usd_total", "Estimated Claude spend in USD"
)
INCIDENTS = Counter(
    "aiops_incidents_total", "Incidents analysed", ["severity", "source"]
)
CHAOS_ERROR_RATE = Gauge(
    "aiops_chaos_error_rate_percent", "Injected error rate (chaos)"
)
CHAOS_CPU_BURN = Gauge(
    "aiops_chaos_cpu_burn_active", "1 while a CPU burn is running"
)
APP_INFO = Info("aiops_app", "Build and runtime info")

_SKIP = {"/metrics", "/health", "/ready", "/static"}


def init_metrics(app):
    APP_INFO.info({
        "version": app.config["CFG"].APP_VERSION,
        "mode": app.config["CFG"].effective_mode,
        "instance": app.config["CFG"].INSTANCE,
    })

    @app.before_request
    def _start_timer():
        g._t0 = time.perf_counter()

    @app.after_request
    def _record(response):
        path = request.path
        if any(path.startswith(p) for p in _SKIP):
            return response
        endpoint = request.url_rule.rule if request.url_rule else "unmatched"
        HTTP_REQUESTS.labels(request.method, endpoint, str(response.status_code)).inc()
        HTTP_LATENCY.labels(endpoint).observe(time.perf_counter() - g.get("_t0", time.perf_counter()))
        return response
