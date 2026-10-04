# Doubt Wall

The app learners use during the marathon. They open the public address, pick
a name, post doubts and upvote each other's. Every browser sees new doubts
within 3 seconds. Data lives in PostgreSQL, so it survives pod restarts,
deploys and rescheduling.

## Run it

Full step-by-step setup (Docker Compose or a normal local run on Windows,
Linux or macOS) is in the [top-level README](../README.md). Quick version:

```bash
# needs a PostgreSQL somewhere; easiest is the repo-root docker compose
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://doubtwall:<password>@localhost:5432/doubtwall
export ADMIN_TOKEN=$(openssl rand -hex 16)
python wsgi.py                      # http://localhost:8000
./scripts/seed.sh                   # five sample doubts
```

Tests run against a real database (24 tests):

```bash
docker run -d --name pg-test -e POSTGRES_USER=doubtwall -e POSTGRES_PASSWORD=doubtwall \
  -e POSTGRES_DB=doubtwall_test -p 5432:5432 postgres:18-alpine
TEST_DATABASE_URL=postgresql://doubtwall:doubtwall@localhost:5432/doubtwall_test pytest -q
docker rm -f pg-test
```

If PostgreSQL isn't reachable the tests are skipped, not failed, so check the
summary line says `passed`.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | built from the `DB_*` parts | Full connection string, if you prefer one variable |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | `localhost`, `5432`, `doubtwall`, `doubtwall` | From a ConfigMap |
| `DB_PASSWORD` | `doubtwall` | **From a Secret** |
| `ADMIN_TOKEN` | empty | Unlocks moderation and chaos. Empty = both disabled |
| `SESSION_TITLE` | `DevOps Marathon` | Big title on the coloured band |
| `BANNER_TEXT` | welcome line | Change it in a new build to demo a rolling update |
| `BANNER_COLOR` | `#1F4E79` | Band colour. Must be `#RRGGBB`; anything else falls back to the default |
| `APP_VERSION` | `1.0.0` | Set from the Jenkins build number; shown on the page |
| `POD_NAME` | hostname | Downward API; footer shows which pod answered |
| `POSTS_PER_MINUTE` | `5` | Per learner (browser), per pod |
| `VOTES_PER_MINUTE` | `60` | Per learner, per pod |
| `IP_REQUESTS_PER_MINUTE` | `6000` | Per IP, per pod. A backstop against scripts. Each open browser polls about 40 times a minute, so this covers ~150 learners behind one IP. Raise it for a bigger class on one network |
| `CHAOS_ENABLED` | `true` | Allow the chaos admin API |
| `TRUST_PROXY` | `true` | Read the client IP from `X-Forwarded-For` (Traefik/Ingress) |

## What learners can and can't do

- Post (name up to 30 chars, doubt 5–500 chars), upvote once per doubt per browser, switch between Most upvoted and Newest.
- Text is stored as typed and escaped when shown, so `<script>` shows up as text.
- Rate limits return a clear "wait a minute" message instead of an error page.

## Moderation (instructor)

Open `http://<address>/?admin` or click **Moderate** in the footer, paste the
admin token, and each doubt gets **Mark answered** and **Remove** buttons.
The token stays in that browser tab only.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/` | The wall |
| GET | `/health` | Liveness. Never checks the database, so a DB blip doesn't restart app pods |
| GET | `/ready` | Readiness. 503 if PostgreSQL is unreachable or chaos says so |
| GET | `/metrics` | Prometheus |
| GET | `/api/doubts?sort=top\|new` | Up to 200 doubts |
| POST | `/api/doubts` | `{"nickname", "question"}` |
| POST | `/api/doubts/{id}/upvote` | |
| GET | `/api/stats`, `/api/info` | Counts, version, pod name |
| POST | `/api/admin/check` | Header `X-Admin-Token` |
| POST | `/api/admin/doubts/{id}/answered` | `{"answered": true}` |
| DELETE | `/api/admin/doubts/{id}` | |
| GET/POST | `/api/admin/chaos`, `/api/admin/chaos/{errors,latency,cpu,ready,reset}` | Used by AIOps Triage |

## Metrics for Grafana

| Metric | Panel idea |
|---|---|
| `doubtwall_http_requests_total{status}` | Error rate: `sum(rate(doubtwall_http_requests_total{status=~"5.."}[2m])) / sum(rate(doubtwall_http_requests_total[2m]))` |
| `doubtwall_http_request_duration_seconds` | p95: `histogram_quantile(0.95, sum by (le) (rate(doubtwall_http_request_duration_seconds_bucket[5m])))` |
| `doubtwall_db_query_duration_seconds{query}` | p95 per query, to show PostgreSQL is not the bottleneck |
| `doubtwall_db_errors_total` | Spikes when PostgreSQL is down |
| `doubtwall_doubts_created_total`, `doubtwall_upvotes_total` | Class activity: `sum(increase(doubtwall_doubts_created_total[5m]))` |
| `doubtwall_rate_limited_total{kind}` | Who's hammering the wall |
| `doubtwall_chaos_*` | Annotate when chaos was switched on |

Alert rules for Day 2:

```yaml
- alert: HighErrorRate
  expr: sum(rate(doubtwall_http_requests_total{status=~"5.."}[2m])) / sum(rate(doubtwall_http_requests_total[2m])) > 0.05
  for: 1m
  labels: { severity: critical }
  annotations: { summary: "Doubt Wall 5xx error rate above 5%" }
- alert: HighLatency
  expr: histogram_quantile(0.95, sum by (le) (rate(doubtwall_http_request_duration_seconds_bucket[2m]))) > 1
  for: 1m
  labels: { severity: warning }
  annotations: { summary: "Doubt Wall p95 latency above 1 s" }
```

## Layout

```
app/
  __init__.py   app factory, DB error handling
  config.py     env settings
  db.py         every SQL statement (one table: doubts)
  routes.py     page, learner API, admin API, probes
  chaos.py      chaos switches
  ratelimit.py  per-learner and per-IP limits
  metrics.py    Prometheus metrics
  templates/, static/
tests/          24 tests against real PostgreSQL
scripts/        seed.sh, load.sh
```
