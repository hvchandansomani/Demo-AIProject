# Architecture, Stack and Security Guide

How the DevOps Marathon project is built, why each piece was chosen, what the
alternatives are, how it is secured (including the database guardrails), and
how to explain the whole thing end to end in class.

> **Status legend.** ✅ built and tested in this repo · ☑️ written and validated, not yet run on the real AWS cluster · 🔜 planned for the
> marathon build-out (Terraform, Ansible, Jenkins, Kubernetes, monitoring files
> are added next) · ⚠️ known gap, with the recommended fix.

---

## 1. The system in one picture

```
                         ┌──────────────── EC2 / k3s cluster (🔜) ───────────────┐
Learners' browsers ──►   │  Ingress (Traefik)                                      │
  http://<IP>/           │     │  /      ──► Doubt Wall pods ──► PostgreSQL 18    │
                         │     │                 │ /metrics       (StatefulSet+PV)│
Instructor browser ──►   │     │  /ops   ──► AIOps Triage pod ──► Claude API      │
  http://<IP>/ops        │     │                 ▲      │ chaos switches         │
                         │     │                 │      └──► Doubt Wall admin API │
                         │  Prometheus ── scrapes /metrics ── Alertmanager ──┘    │
                         │  Grafana (dashboards)                                   │
                         └─────────────────────────────────────────────────────────┘
CloudWatch alarms ──► SNS ──► /ops/webhook/cloudwatch
Git push ──► Jenkins ──► test ──► docker build ──► Trivy ──► ECR ──► kubectl rollout (☑️)
```

Two apps, one database:

| Component | Role | Status |
|---|---|---|
| **Doubt Wall** | Public app learners use: post doubts, upvote, live board | ✅ |
| **AIOps Triage** | Instructor console: receives alerts, diagnoses them (rules or Claude), injects chaos | ✅ |
| **PostgreSQL 18** | Stores doubts (one table) | ✅ |
| Docker / Compose | Package and run locally | ✅ |
| Kubernetes manifests (`k8s/`), Jenkins pipeline (`Jenkinsfile`, `ci/`) | Run and deliver | ☑️ |
| Terraform (`terraform/`), Ansible (`ansible/`), Argo CD GitOps (`argocd/`) | Create, configure and deliver on AWS | ☑️ |
| Prometheus, Grafana, CloudWatch | Monitoring and alerting | 🔜 |

---

## 2. Technology stack: what, why, and alternatives

### 2.1 Application layer

| Layer | Chosen | Why this one | Alternatives | Why not (for this session) |
|---|---|---|---|---|
| Language | **Python 3.12** | Learners already know it from earlier modules; readable in a live session; first-class Claude SDK | Node.js, Go, Java | Node/Go are great but add a new language to a DevOps-focused class; Java is heavier to build and slower to start |
| Web framework | **Flask 3** | Tiny, no magic, every route is visible in one file; easy to explain | FastAPI, Django, Express | FastAPI adds async and type concepts; Django brings an ORM, admin and migrations that distract from the DevOps goal |
| App server | **gunicorn** (1 worker, 8 threads) | Standard production server for Flask; threads fit the I/O-bound workload | uWSGI, Waitress, uvicorn | uWSGI is complex to configure; Waitress is mainly for Windows; uvicorn is for async apps |
| Front end | **Plain HTML, CSS and JavaScript** (no build step) | Nothing to compile, so the Docker image stays simple and the Jenkins pipeline has no Node stage | React, Vue, HTMX | A framework needs a build pipeline and adds a second toolchain to explain |
| Live updates | **Polling every 3–4 s** | Works through every proxy and load balancer; trivial to scale; creates steady traffic for Grafana | WebSockets, Server-Sent Events | Need sticky sessions or a message broker across replicas; harder to debug in class |
| AI engine | **Claude API** (`claude-haiku-4-5` by default) plus a **built-in rule engine** (mock mode) | Haiku-class models are fast and cheap for short triage; mock mode means tests and learners without a key still work | OpenAI, Gemini, local models via Ollama | Any would work behind the same interface; a local model needs a GPU-sized node |
| Metrics library | **prometheus_client** | Native Prometheus format, no agent needed | OpenTelemetry SDK, StatsD | OpenTelemetry is the future standard but adds a collector to deploy; StatsD needs an extra daemon |
| Tests | **pytest** (54 tests) | De-facto Python standard; Jenkins can publish its results | unittest | More boilerplate, less readable |

### 2.2 Data layer

| Chosen | Why | Alternatives | Why not (for this session) |
|---|---|---|---|
| **PostgreSQL 18** | Data is small and structured (one table); SQL is familiar to most learners; light on memory on a shared node; maps directly to AWS RDS for the take-home | MongoDB, MySQL/MariaDB, Redis, DynamoDB | MongoDB's default cache takes about half the node's RAM, risky next to Prometheus; MySQL would work equally well; Redis alone doesn't give durable, queryable data; DynamoDB locks the design to AWS and hides the database from the Kubernetes lesson |
| **psycopg 3 + psycopg_pool** | Official modern PostgreSQL driver; built-in connection pool; parameterised queries by default | SQLAlchemy ORM, psycopg2 | An ORM hides the SQL learners should see; psycopg2 is the older generation |
| **No ORM, no migration tool** | One table, created with `CREATE TABLE IF NOT EXISTS` on first start | Alembic, Flyway, Liquibase | Worth it with several tables or schema changes; overkill for one table in a 4-hour event |

### 2.3 Packaging and delivery

| Chosen | Why | Alternatives |
|---|---|---|
| **Docker**, multi-stage `python:3.12-slim` | Small image, compilers stay in the build stage, runs as a non-root user | Alpine base (smaller but musl breaks some Python wheels), distroless (smaller attack surface, harder to debug live), Buildpacks |
| **Docker Compose** | One command to run everything on a laptop | Podman Compose, Tilt, Skaffold |
| 🔜 **Amazon ECR** | Private registry in the same AWS account; IAM-based access | Docker Hub (public pull limits), GitHub Container Registry |
| ☑️ **Jenkins** | Industry-common, taught in the programme, full control on your own EC2 | GitHub Actions (simpler, SaaS), GitLab CI, AWS CodePipeline |

### 2.4 Infrastructure and operations (🔜 built during the marathon)

| Chosen | Why | Alternatives |
|---|---|---|
| **Terraform** | Declarative, cloud-agnostic, the standard IaC tool | CloudFormation (AWS-only), Pulumi (code instead of HCL), AWS CDK |
| **Ansible** | Agentless, SSH-based, perfect for configuring the Jenkins and k3s servers | cloud-init / user_data (no idempotent re-runs), Chef, Puppet (agents), Salt |
| **k3s on EC2** | Starts in about a minute, includes Traefik Ingress and a storage class; cheap | Amazon EKS (15–20 min to create, control-plane cost; used as the take-home), kubeadm (more setup), minikube (local only) |
| **Traefik Ingress** (bundled with k3s) | Zero extra install; routes `/` and `/ops` | NGINX Ingress, AWS Load Balancer Controller |
| **Prometheus + Alertmanager + Grafana** (`kube-prometheus-stack` Helm chart) | Kubernetes-native metrics and alerting; one chart installs all three | Datadog, New Relic (SaaS, paid), Amazon Managed Prometheus/Grafana |
| **CloudWatch + SNS** | Watches the AWS layer (EC2 CPU, disk, billing) that Prometheus doesn't see; SNS delivers alarms to the app | Only Prometheus node-exporter (misses billing and AWS status checks) |

**Why both Prometheus and CloudWatch?** Prometheus sees *inside* the cluster
(requests, errors, latency, pods). CloudWatch sees *the AWS resources* (the
EC2 instance, disk, the bill). Each alerts on what the other can't observe.

---

## 3. Security model

Security is built in layers, so one mistake doesn't expose everything.

### 3.1 Who can reach what

| Surface | Who | Protection |
|---|---|---|
| Doubt Wall `/` and learner API | Anyone with the IP | Read, post, upvote only. Validation and rate limits (3.4, 3.5) |
| Doubt Wall moderation and chaos (`/api/admin/*`) | Instructor | `X-Admin-Token` header, compared in constant time (`hmac.compare_digest`) ✅ |
| AIOps Triage console `/ops` | Instructor | Sign-in with `ADMIN_TOKEN`, signed session cookie (HttpOnly, SameSite=Lax, 12 h) ✅ |
| Console sign-in form | Instructor | Max 20 attempts per IP per minute, then locked for a minute ✅ |
| Alert webhooks | Alertmanager, AWS SNS | `?token=<WEBHOOK_TOKEN>` in the URL ✅; SNS subscription links accepted only from `https://sns.<region>.amazonaws.com` ✅ |
| `/health`, `/ready`, `/metrics` | Kubernetes probes, Prometheus | Open by design; contain no personal data. Keep ports 9090 (Prometheus) and 8080 (Jenkins) closed to the internet in the security group 🔜 |
| PostgreSQL | Doubt Wall only | Not published to the host in Compose ✅; in Kubernetes a headless Service plus a NetworkPolicy allowing only Doubt Wall pods ☑️ |

### 3.2 Secrets management

| Rule | How |
|---|---|
| No secret in code, image, Jenkinsfile or git | All secrets come from environment variables ✅; `.env` is in `.gitignore` ✅ |
| Local | `.env` file (from `.env.example`) ✅ |
| Kubernetes | One Secret, `marathon-secrets`, created by `k8s/create-secrets.sh` and mounted as env vars ☑️ |
| Jenkins | Only the kubeconfig, as a Secret-file credential; AWS access through the EC2 instance role (no keys) ☑️ |
| Strong values | Generate with `openssl rand -hex 16` ✅ (documented) |
| Same session key on every replica | Derived from `ADMIN_TOKEN` or set via `SECRET_KEY`, so sign-in works whichever pod answers ✅ |

### 3.3 Database guardrails

**Built in and tested ✅**

| # | Guardrail | What it prevents | Where |
|---|---|---|---|
| 1 | **Parameterised queries only** (`%s` placeholders); the one dynamic `ORDER BY` is picked from two fixed strings, never from user input | SQL injection. Tested with `x'); DROP TABLE doubts;--` as input | `doubt-wall/app/db.py` |
| 2 | **All SQL in one file** | Hidden queries scattered across the code; easy to review | `db.py` |
| 3 | **Dedicated app user, not the superuser.** In Kubernetes the app role can only read and write rows (no `CREATE`, `ALTER`, `DROP`, `TRUNCATE`); tested on real PostgreSQL | App compromise dropping the table or reaching server settings | `k8s/base/postgres/init-app-role.sh`; README Option 2, Step 2. ⚠️ Not yet true in Docker Compose (see gaps) |
| 4 | **Database not exposed**: no port published in Compose; cluster-internal Service and NetworkPolicy in Kubernetes | Direct internet access to PostgreSQL | `docker-compose.yml` |
| 5 | **Password from a secret**, never in code | Credential leaks via git or images | `config.py` |
| 6 | **Connection pool, max 10 connections, 5 s wait timeout, 5 s connect timeout** | App exhausting PostgreSQL connections; requests hanging forever when the database is down | `db.py` |
| 7 | **Input validation before the database**: name 1–30 chars, doubt 5–500 chars, control characters stripped, whitespace collapsed | Oversized or junk rows | `routes.py` |
| 8 | **`NOT NULL` columns and defaults** | Half-written rows | table schema |
| 9 | **Atomic counter** `UPDATE … SET upvotes = upvotes + 1` | Lost votes under concurrency. Tested: 40 simultaneous votes → exactly 40 | `db.py` |
| 10 | **Read cap** `LIMIT 200` on list queries | One request pulling the whole table | `db.py` |
| 11 | **Write rate limits**: 5 posts and 60 votes per minute per learner, 6000 requests per minute per IP | A script flooding the table | `routes.py` |
| 12 | **Safe error messages**: database failures return a generic 503 ("The database is not reachable right now"), never the SQL error | Leaking table names, hosts or versions | `app/__init__.py` |
| 13 | **Liveness does not check the database; readiness does** | A database blip restarting every app pod (a cascading outage) | `/health` vs `/ready` |
| 14 | **Delete and "mark answered" need the admin token** | Learners removing others' doubts | `/api/admin/*` |
| 15 | **Query latency and error metrics** (`doubtwall_db_query_duration_seconds`, `doubtwall_db_errors_total`) | Silent database slowness | `metrics.py` |
| 16 | **Backup and restore procedure** (`pg_dump` / `psql`) | Data loss with no way back. Tested: dump restored with all rows | `POSTGRES-CHEATSHEET.md` |
| 17 | **Persistent volume** for the data | Doubts lost when the container or pod restarts. Tested: restart keeps data | Compose volume; StatefulSet PVC `data-postgres-0` |
| 18 | **`CHECK` constraints in the table** (name ≤ 100, doubt ≤ 2000 characters) | Huge rows written by anything that bypasses the app. Tested | `doubt-wall/app/db.py`, `init-app-role.sh` |
| 19 | **`statement_timeout = 5s` and `idle_in_transaction_session_timeout = 30s`** on the app role (Kubernetes) | One slow or stuck query holding a connection. Tested: a 7 s query is cancelled at 5 s | `init-app-role.sh` |
| 20 | **Connection limit 60** on the app role; 5 pods × pool 10 = 50 max | The app exhausting PostgreSQL's 100 connections | `init-app-role.sh`, `hpa.yaml` |
| 21 | **Credentials percent-encoded** in the connection URL | Passwords containing `@ : / ?` breaking the connection | `doubt-wall/app/config.py` |

**Recommended next guardrails ⚠️** (not yet in the code; good "production vs demo" discussion)

| Gap | Risk | Fix |
|---|---|---|
| In Docker Compose, the app connects as `POSTGRES_USER`, which the official image creates as a **superuser** | A compromised app could reach every database and server setting | Mount `k8s/base/postgres/init-app-role.sh` into the Compose PostgreSQL too (Kubernetes already does this) |
| No TLS between app and database | Traffic readable on the network | Fine inside one node; on RDS or across hosts use `sslmode=require` |
| Backups are manual | Forgetting to back up | A Kubernetes CronJob running `pg_dump` to S3, or RDS automated backups |
| Hard delete | A removed doubt can't be recovered | Soft delete (`hidden = true`) plus an audit column |
| Upvote de-duplication is per browser | Clearing storage allows voting again | A `votes(doubt_id, client_id)` table with a unique constraint |

### 3.4 Input and output handling ✅

- **Stored text is escaped when shown.** Every user string is HTML-escaped in
  the browser before rendering. Tested: `<script>` posts show as text and never run.
- **Banner colour is validated** as `#RRGGBB`, because it's written into CSS.
- **JSON responses have stable keys** and never include stack traces.
- **Alerts sent to Claude are capped** at 6000 characters.

### 3.5 Abuse and availability controls ✅

| Control | Value |
|---|---|
| Posts per learner | 5 per minute |
| Upvotes per learner | 60 per minute |
| Requests per IP (backstop) | 6000 per minute (covers ~150 learners behind one Wi-Fi) |
| Polling pauses in background tabs | Cuts idle load |
| Alerts analysed per webhook call | Max 5 |
| Chaos switches | Admin-only; can be turned off entirely with `CHAOS_ENABLED=false` |

Limits are in memory per pod: good for a classroom; production would enforce
them at the Ingress or in Redis. ⚠️

### 3.6 AI safety and cost controls ✅

| Control | Why |
|---|---|
| Console (and therefore Claude) is admin-only; webhooks need a token | Learners can't spend your API credits |
| `max_tokens = 700`, input capped at 6000 chars | Bounded cost per diagnosis |
| 30 s timeout, 1 retry | A slow API never hangs the console |
| Strict JSON output, validated and normalised | A malformed reply can't break the page |
| Automatic fallback to rules on any Claude error, shown in the UI | The demo keeps working without the API |
| Token and cost metrics (`aiops_llm_tokens_total`, `aiops_llm_cost_usd_total`) | Spend is visible in Grafana |
| Mock mode by default | Tests and CI never call the paid API |

### 3.7 Container security ✅

- Runs as a **non-root user** (UID 10001).
- **Multi-stage build**: compilers and build tools never reach the runtime image.
- **Slim base image** and `.dockerignore` (no tests, `.env` or `.git` in the image).
- **Pinned dependency versions** in `requirements.txt`.
- **`HEALTHCHECK`** built into the image.

☑️ In Kubernetes: `runAsNonRoot`, `readOnlyRootFilesystem`, all capabilities
dropped, default seccomp profile (the "restricted" Pod Security Standard).

☑️ In the pipeline: Trivy scans every image and fails on fixable CRITICAL
vulnerabilities. Trivy is pinned to 0.69.3 because 0.69.4–0.69.6 were
malicious builds (March 2026 supply-chain attack), and it scans an exported
image file instead of getting the Docker socket.

### 3.8 Known gaps to be upfront about ⚠️

| Gap | Why acceptable for the session | Production fix |
|---|---|---|
| Plain HTTP, no TLS | Short-lived classroom demo with no sensitive data | cert-manager + Let's Encrypt on the Ingress, or an ALB with ACM |
| One shared admin token | One instructor | SSO / OIDC (e.g. Cognito, Google) with per-user roles |
| Rate limits per pod, in memory | Few replicas, short event | Ingress rate limiting or Redis |
| Nicknames are self-declared | Anonymous classroom board | Real sign-in if identity matters |

---

## 4. How to explain it end to end (instructor script)

### 4.1 The 60-second version

> "Learners use a simple Doubt Wall. Every click is a request that Flask
> handles and PostgreSQL stores. Each request is also counted, and Prometheus
> collects those counts. When errors or latency cross a threshold, an alert
> fires and is sent to our second app, AIOps Triage, which asks Claude what
> went wrong and how to fix it. To prove the loop works, we break the Doubt
> Wall on purpose and watch it detect, diagnose and recover. Everything is
> built by Jenkins, packaged by Docker, created by Terraform, configured by
> Ansible, and run by Kubernetes."

### 4.2 The detailed walk-through (seven flows)

Explain one flow at a time, in this order. Each builds on the previous one.

**Flow 1: A learner posts a doubt (the request path)**

```
Browser ──POST /api/doubts──► Ingress ──► Doubt Wall pod (gunicorn → Flask)
   ▲                                          │ 1. rate-limit check
   │                                          │ 2. validate and clean input
   │                                          │ 3. INSERT … VALUES (%s, %s)
   │                                          ▼
   └────────── 201 + JSON ◄──────────── PostgreSQL (row saved on the PV)
```
*Say:* "The browser never talks to the database. Flask checks, cleans and
parameterises everything first."

**Flow 2: Everyone sees it (the read path)**

```
Every browser, every 3 s ──GET /api/doubts──► any Doubt Wall pod ──SELECT … LIMIT 200──► PostgreSQL
```
*Say:* "Because data is in PostgreSQL, not in the pod, every replica shows the
same wall. The footer shows which pod answered: refresh and watch it change."

**Flow 3: Measuring it (the metrics path)**

```
Doubt Wall /metrics ◄──scrape every 15 s── Prometheus ──► Grafana dashboards
  (requests, errors, latency, DB query time)
```
*Say:* "The app counts; Prometheus pulls. Grafana only draws what Prometheus stored."

**Flow 4: Something goes wrong (the alert path)**

```
Prometheus rule: 5xx rate > 5% for 1 min ──► Alertmanager ──POST /ops/webhook/alertmanager?token=…──► AIOps Triage
CloudWatch alarm: EC2 CPU > 80%          ──► SNS          ──POST /ops/webhook/cloudwatch?token=…───► AIOps Triage
```
*Say:* "Two watchers: Prometheus for the app, CloudWatch for AWS. Both deliver
to one place."

**Flow 5: Diagnosing it (the AIOps path)**

```
Alert text ──► (optional) recent pod logs + events ──► Claude (or rules) ──► JSON:
  severity · root cause · impact · steps · commands ──► shown in the console
```
*Say:* "The AI doesn't fix anything by itself. It shortens the time from alert
to understanding, and a human decides."

**Flow 6: Proving it (the chaos loop)**

```
Instructor clicks "Fail 30%" ──► Triage ──X-Admin-Token──► Doubt Wall /api/admin/chaos/errors
      ▲                                                        │
      │                                     learners start seeing errors
      │                                                        ▼
Diagnosis appears ◄── Triage ◄── Alertmanager ◄── Prometheus sees 5xx spike
```
*Say:* "This is the whole course in one loop: deploy, observe, alert,
diagnose, recover."

**Flow 7: Shipping a change (the delivery path) ☑️**

```
git push ──► Jenkins: pytest ──► docker build ──► Trivy scan ──► push to ECR
         ──► kubectl set image ──► rolling update (readiness gates each new pod)
```
*Say:* "Change `BANNER_TEXT`, push, and every learner sees the new banner with
zero downtime. If the new pods fail readiness, the rollout stops and the old
version keeps serving."

### 4.3 Where each tool appears in the story

| Tool | Appears in flow | One-line role |
|---|---|---|
| Terraform | Setup | Creates the EC2 instances, security groups, ECR, IAM |
| Ansible | Setup | Installs and configures Jenkins, Docker and k3s on those instances |
| Docker | 7 | Packages each app into an image |
| Jenkins | 7 | Tests, builds, scans, pushes and deploys |
| Kubernetes (k3s) | 1, 2, 7 | Runs pods, restarts them, scales them, rolls out new versions |
| PostgreSQL | 1, 2 | Keeps the data outside the pods |
| Prometheus | 3, 4 | Collects metrics and evaluates alert rules |
| Grafana | 3 | Shows the metrics |
| CloudWatch + SNS | 4 | Watches AWS resources and delivers alarms |
| Claude API | 5 | Turns an alert into a readable diagnosis |

### 4.4 Questions learners usually ask

| Question | Short answer |
|---|---|
| Why not store doubts in the pod? | Pods are replaceable; a restart or a second replica would lose or split the data |
| Why do `/health` and `/ready` differ? | Liveness asks "is the process alive?" (restart if not). Readiness asks "can it serve now?" (stop sending traffic if not). Mixing them turns a database blip into a restart storm |
| Why is `/metrics` public? | Prometheus needs it and it holds only counts; in production, restrict it with a NetworkPolicy |
| What if Claude is down? | The console falls back to rule-based diagnosis and says so |
| Is the AI making changes to the cluster? | No. It only reads alert text (and optionally logs) and suggests; humans act |
| Why PostgreSQL instead of MongoDB? | Structured data, lower memory on a shared node, SQL familiarity, direct path to RDS |

---

## 5. Files to look at while explaining

| Topic | File |
|---|---|
| Every SQL statement | `doubt-wall/app/db.py` |
| Learner and admin API, validation, rate limits | `doubt-wall/app/routes.py` |
| Chaos switches | `doubt-wall/app/chaos.py` |
| Claude call, prompt, fallback | `aiops-triage/app/analyzer.py` |
| Console sign-in, webhooks, chaos forwarding | `aiops-triage/app/routes.py` |
| Metrics definitions | `*/app/metrics.py` |
| Container build | `*/Dockerfile` |
| Local stack | `docker-compose.yml` |
| Database commands for class | `POSTGRES-CHEATSHEET.md` |
