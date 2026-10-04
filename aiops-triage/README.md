# AIOps Triage

A small Flask app for the DevOps marathon. It takes alerts from Prometheus
Alertmanager, CloudWatch (via SNS) or a person pasting logs, and returns a
diagnosis: likely root cause, impact, ordered fix steps and commands to run.

Diagnoses come from one of two engines with the same output shape:

| Mode | What answers | Needs |
|---|---|---|
| `mock` (default) | Built-in rules for common Kubernetes/AWS failures | Nothing. Free and deterministic, ideal for CI and learners without a key |
| `claude` | Claude Messages API, asked for strict JSON | `ANTHROPIC_API_KEY` |

If `claude` is requested without a key, or a Claude call fails, the app falls
back to the rules and says so in the UI.

## Run it

Full step-by-step setup (Docker Compose or a normal local run on Windows,
Linux or macOS) is in the [top-level README](../README.md). Quick version:

**Python (Day 1, first 5 minutes)**

```bash
pip install -r requirements-dev.txt
pytest -q                      # 30 tests
python wsgi.py                 # http://localhost:8000
```

**Docker (whole stack)**: see the top-level README. `docker compose up --build`
from the repo root starts PostgreSQL, the Doubt Wall and this console at
`http://localhost:8080/ops`.

```bash
WEBHOOK_TOKEN=<your token> ./scripts/send-sample-alerts.sh   # Alertmanager + CloudWatch samples
```

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `LLM_MODE` | `mock` | `mock` or `claude` |
| `ANTHROPIC_API_KEY` | empty | Put in a Kubernetes Secret / Jenkins credential. Never in the image |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | Small, fast, cheap model is plenty for triage |
| `MAX_TOKENS` | `700` | Caps output cost per diagnosis |
| `PRICE_INPUT_PER_MTOK` / `PRICE_OUTPUT_PER_MTOK` | `1.0` / `5.0` | USD, only for the cost estimate. Check current pricing |
| `ADMIN_TOKEN` | empty | **Set it before sharing the address.** The console asks for it once and keeps a signed 12-hour cookie. Empty = open to anyone |
| `SECRET_KEY` | derived from `ADMIN_TOKEN` | Cookie signing key. Same value on every replica |
| `WEBHOOK_TOKEN` | empty | When set, webhook URLs need `?token=<value>` |
| `URL_PREFIX` | empty | Serve under a path, e.g. `/ops` behind the Ingress. `/health` and `/metrics` also answer without it |
| `CHAOS_TARGET_URL` | empty | App the chaos switches act on, e.g. `http://doubt-wall.marathon.svc.cluster.local`. Empty = this app |
| `CHAOS_TARGET_NAME` | `Doubt Wall` | Shown in the UI |
| `CHAOS_TARGET_TOKEN` | `ADMIN_TOKEN` | The target app's admin token |
| `CHAOS_ENABLED` | `true` | Turns the "Break things on purpose" panel on or off |
| `K8S_CONTEXT_ENABLED` | `false` | Pull pod logs and events into the prompt (needs RBAC, below) |
| `APP_VERSION` | `1.0.0` | Set from the Jenkins build number |
| `POD_NAME` | hostname | Set via the Downward API so the UI shows which pod answered |

## Endpoints

| Method | Path | Used by |
|---|---|---|
| GET | `/` | The UI (sign-in first when `ADMIN_TOKEN` is set) |
| GET/POST | `/login`, POST `/logout` | Console sign-in |
| GET | `/health` | Liveness probe (never affected by chaos) |
| GET | `/ready` | Readiness probe (returns 503 when the chaos switch is on) |
| GET | `/metrics` | Prometheus scrape |
| POST | `/api/analyze` | `{"text": "..."}` from the UI or curl |
| GET | `/api/incidents`, `/api/stats`, `/api/info` | UI polling |
| GET | `/api/work` | Stand-in for real traffic; chaos error rate applies here |
| POST | `/webhook/alertmanager?token=...` | Alertmanager webhook receiver |
| POST | `/webhook/cloudwatch?token=...` | SNS HTTP subscription (auto-confirms, AWS URLs only) |
| POST | `/api/chaos/{errors,latency,cpu,ready,reset,traffic}` | Day 2 finale; forwarded to `CHAOS_TARGET_URL` |

Open without sign-in: `/health`, `/ready`, `/metrics`, `/static`, `/login`,
`/webhook/*` (token-checked) and `/api/work`. Everything else needs the admin session.

## Metrics for Grafana

| Metric | Panel idea |
|---|---|
| `aiops_http_requests_total{status}` | Error rate: `sum(rate(aiops_http_requests_total{status=~"5.."}[2m])) / sum(rate(aiops_http_requests_total[2m]))` |
| `aiops_http_request_duration_seconds` | p95 latency: `histogram_quantile(0.95, sum by (le) (rate(aiops_http_request_duration_seconds_bucket[5m])))` |
| `aiops_llm_latency_seconds` | AI diagnosis time |
| `aiops_llm_tokens_total{direction}` | Tokens per hour: `sum by (direction) (increase(aiops_llm_tokens_total[1h]))` |
| `aiops_llm_cost_usd_total` | AI spend per hour: `increase(aiops_llm_cost_usd_total[1h])` |
| `aiops_incidents_total{severity,source}` | Incidents by source |
| `aiops_chaos_*` | Annotate when chaos was switched on |

Sample alert rules for Day 2:

```yaml
- alert: HighErrorRate
  expr: sum(rate(aiops_http_requests_total{status=~"5.."}[2m])) / sum(rate(aiops_http_requests_total[2m])) > 0.05
  for: 1m
  labels: { severity: critical }
  annotations: { summary: "5xx error rate above 5%" }
```

Alertmanager receiver pointing back at the app:

```yaml
receivers:
  - name: aiops-triage
    webhook_configs:
      - url: http://aiops-triage.marathon.svc.cluster.local/webhook/alertmanager?token=<WEBHOOK_TOKEN>
        send_resolved: false
```

## Chaos switches (Day 2)

With `CHAOS_TARGET_URL` set, every switch acts on the Doubt Wall that learners
are using, through its admin API. The console itself keeps running normally.

| Button | What happens on the Doubt Wall | Where you see it |
|---|---|---|
| Fail 30% of requests | Learner API calls return 500 at that rate | Learners see errors; error-rate panel; `HighErrorRate` alert |
| Slow every request by 1.5 s | Each learner API call waits 1.5 s | p95 latency panel; `HighLatency` alert |
| Burn CPU for 2 min | One core spins | HPA scales out; CPU alert; CloudWatch EC2 alarm |
| Fail readiness probe | `/ready` returns 503 | Pod drops out of the Service endpoints |
| Send 50 test requests | The console calls the Doubt Wall 50 times | Success/failure count and average time, right away |

## Pod context (optional)

With `K8S_CONTEXT_ENABLED=true`, alerts that carry `namespace` and `pod` labels
get the pod's last 40 log lines and recent namespace events added to the
prompt. The pod's ServiceAccount needs `get`/`list` on `pods`, `pods/log` and
`events` in that namespace.

## Known limitation (a teaching point)

Incidents are kept in the pod's memory. Scale to 3 replicas and each pod has
its own list; the header shows which pod served you. That's the natural lead-in
to "stateless apps keep state in Redis or RDS".

Gunicorn runs one worker with 8 threads so metrics and incidents stay in one
process. Scale with Kubernetes replicas, not gunicorn workers.

## Layout

```
app/
  __init__.py      app factory
  config.py        env-driven settings
  analyzer.py      mock rules + Claude engine
  routes.py        pages, API, webhooks, chaos
  metrics.py       Prometheus metrics
  chaos.py         chaos switches
  k8s_context.py   optional pod logs/events
  store.py         in-memory incident store
  templates/, static/   UI
tests/             pytest suite (Jenkins test stage)
samples/           Alertmanager and SNS payloads
scripts/           send-sample-alerts.sh
Dockerfile         multi-stage, non-root, healthcheck
```
