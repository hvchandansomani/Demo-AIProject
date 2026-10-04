# DevOps Marathon apps

Two small Flask apps and a PostgreSQL database, built to be deployed during a
two-day DevOps marathon (Docker, Terraform, Ansible, Jenkins, Kubernetes,
Prometheus, Grafana, CloudWatch).

```
Learners (browser) ──► http://<IP>/         ──► Doubt Wall ──► PostgreSQL
                                                    │ /metrics        (one table: doubts)
Instructor         ──► http://<IP>/ops      ──► AIOps Triage ◄── Alertmanager / CloudWatch
                        (admin token)             │
                                                  └── chaos switches ──► Doubt Wall admin API
```

| App | Who uses it | What it does |
| --- | ----------- | ------------ |
| [`doubt-wall/`](doubt-wall/README.md) | Learners | Post doubts, upvote, live updates. Real traffic and real data |
| [`aiops-triage/`](aiops-triage/README.md) | You | Receives alerts, diagnoses them (rules or Claude), and breaks the Doubt Wall on purpose |

## Ways to run it

| Option | Use it when | Needs |
| ------ | ----------- | ----- |
| [1\. Docker Compose](#option-1-docker-compose-easiest) | You want everything running in one command | Docker Desktop |
| [2\. Normal local run \(no Docker\)](#option-2-normal-local-run-no-docker) | You want to see each piece start by hand, or Docker isn't available | Python 3.11+ and PostgreSQL 18 (16 or 17 also work) |
| 3\. AWS: Terraform \+ Ansible \+ Jenkins \+ Kubernetes \+ Argo CD | The marathon itself | Step by step in [`PRACTICE-TASK.md`](PRACTICE-TASK.md); details in [`terraform/`](terraform/README.md), [`ansible/`](ansible/README.md), [`k8s/`](k8s/README.md), [`JENKINS.md`](JENKINS.md) |

Both local options give you the same result:

| Open | You see |
| ---- | ------- |
| http://localhost:8000 | Doubt Wall |
| http://localhost:8000/?admin | Doubt Wall with the moderation prompt |
| http://localhost:8080/ops | AIOps Triage sign-in, then the console |

- - -

## Option 1: Docker Compose (easiest)

**Prerequisites:** Docker Desktop (Windows/macOS) or Docker Engine with the
compose plugin (Linux). Check with `docker compose version`.

**Step 1. Open a terminal in this folder**

``` bash
cd Demo-B17
```

**Step 2. Create your `.env` file**

``` bash
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
```

Open `.env` and replace the three `change-me` values. Any long random text
works; `openssl rand -hex 16` generates one. `.env` is in `.gitignore`, so it
never gets committed.

**Step 3. Build and start**

``` bash
docker compose up --build
```

The first build takes a few minutes. It's ready when the log shows both apps
`started`. Keep this terminal open; logs stream here.

**Step 4. Check it works** (in a second terminal)

``` bash
curl http://localhost:8000/ready          # {"status":"ready"}
curl http://localhost:8080/health         # {"status":"ok"}
```

Then open the three URLs from the table above. Sign in to the console with
your `ADMIN_TOKEN`.

**Step 5. Add sample data** (Git Bash, WSL, macOS or Linux)

``` bash
./doubt-wall/scripts/seed.sh
WEBHOOK_TOKEN=<your webhook token> ./aiops-triage/scripts/send-sample-alerts.sh
```

**Stop, restart, reset**

``` bash
docker compose down          # stop; doubts are kept in the pgdata volume
docker compose up            # start again, same data
docker compose down -v       # stop AND delete the database volume (fresh start)
```

If you ran this project with Docker before the switch to PostgreSQL 18, run
`docker compose down -v` once so PostgreSQL 18 starts on a fresh volume.

- - -

## Option 2: Normal local run (no Docker)

You start PostgreSQL, then each app in its own terminal. Commands are given
for **Windows PowerShell** and **Linux/macOS** where they differ.

### Step 1. Install the prerequisites

| Tool | Windows | Linux (Ubuntu) | macOS |
| ---- | ------- | -------------- | ----- |
| Python 3.11 or newer | [python.org](https://www.python.org/downloads/) installer, tick **Add python.exe to PATH** | `sudo apt install python3 python3-venv` | `brew install python` |
| PostgreSQL 18 (16 or 17 also work) | [postgresql.org](https://www.postgresql.org/download/windows/) installer; remember the `postgres` password you set | `sudo apt install postgresql` | `brew install postgresql@18 && brew services start postgresql@18` |

Check:

``` bash
python --version        # Linux/macOS may need: python3 --version
psql --version          # e.g. psql (PostgreSQL) 18.4; Windows: see the tip below if not found
```

> **Windows tip:** if `psql~` isn't found, use the full path
> `& "C:\Program Files\PostgreSQL\18\bin\psql.exe"` in place of `psql` (change 18 to
> your installed version), or
> open **SQL Shell (psql)** from the Start menu.

### Step 2. Create the database and user

Connect as the `postgres` admin:

``` bash
# Windows PowerShell (asks for the postgres password from the installer)
psql -U postgres -h localhost

# Linux
sudo -u postgres psql

# macOS (Homebrew makes your own user the admin)
psql postgres
```

At the `postgres=#` prompt, run (change `devpass` to your own password):

``` sql
CREATE USER doubtwall WITH PASSWORD 'devpass';
CREATE DATABASE doubtwall OWNER doubtwall;
CREATE DATABASE doubtwall_test OWNER doubtwall;   -- only needed for the tests
\q
```

Check the new login works:

``` bash
psql "postgresql://doubtwall:devpass@localhost:5432/doubtwall" -c "SELECT current_user;"
```

You don't create any tables. The Doubt Wall creates its `doubts` table the
first time it connects.

### Step 3. Start the Doubt Wall (terminal 1)

**Windows PowerShell**

``` powershell
cd Demo-B17\doubt-wall
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DB_HOST="localhost"; $env:DB_PORT="5432"; $env:DB_NAME="doubtwall"
$env:DB_USER="doubtwall"; $env:DB_PASSWORD="devpass"
$env:ADMIN_TOKEN="local-admin-token"; $env:PORT="8000"
python wsgi.py
```

**Linux / macOS**

``` bash
cd doubt-wall
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DB_HOST=localhost DB_PORT=5432 DB_NAME=doubtwall \
       DB_USER=doubtwall DB_PASSWORD=devpass \
       ADMIN_TOKEN=local-admin-token PORT=8000
python wsgi.py
```

It's running when you see `Running on http://127.0.0.1:8000`. The line
"This is a development server" is expected for a local run; Docker and
Kubernetes use gunicorn instead. Leave this terminal open.

### Step 4. Start AIOps Triage (terminal 2)

**Windows PowerShell**

``` powershell
cd Demo-B17\aiops-triage
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:ADMIN_TOKEN="local-admin-token" 
$env:WEBHOOK_TOKEN="local-webhook-token"
$env:URL_PREFIX="/ops"
$env:CHAOS_TARGET_URL="[http://localhost:8000](http://localhost:8000)"
$env:LLM_MODE="mock"
$env:PORT="8080"
python wsgi.py
```

**Linux / macOS**

``` bash
cd aiops-triage
python3 -m venv .venv
source .venv/bin/activate 
pip install -r requirements.txt

export ADMIN_TOKEN=local-admin-token WEBHOOK_TOKEN=local-webhook-token \
       URL_PREFIX=/ops CHAOS_TARGET_URL=[http://localhost:8000](http://localhost:8000) \
       LLM_MODE=mock PORT=8080
python wsgi.py
```

To use real Claude answers, also set `LLM_MODE=claude` and
`ANTHROPIC_API_KEY=<your key>` before `python wsgi.py`.

### Step 5. Check both apps (terminal 3)

| Check | Expected |
| ----- | -------- |
| `curl http://localhost:8000/ready` | `{"status":"ready"}` (proves the database connection) |
| Open http://localhost:8000 | The Doubt Wall with an empty wall |
| Open http://localhost:8080/ops | Sign-in page; token `local-admin-token` opens the console |
| `curl http://localhost:8080/health` | `{"status":"ok"}` |

On Windows, use `curl.exe` so PowerShell doesn't use its own `curl` alias.

### Step 6. Try the whole flow

1. Post a doubt and upvote it at http://localhost:8000.
2. Add sample data:

``` bash
# Git Bash / Linux / macOS
./doubt-wall/scripts/seed.sh
WEBHOOK_TOKEN=local-webhook-token ./aiops-triage/scripts/send-sample-alerts.sh http://localhost:8080/ops
```

``` powershell
# PowerShell alternative: post one doubt
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/doubts `
  -ContentType "application/json" -Body '{"nickname":"Priya","question":"Why does my pod restart?"}'
```

3. In the console, click **Fail 30% of requests**, then **Send 50 test requests**.
About a third fail, and the Doubt Wall tab shows an error banner.
4. Click **Stop all chaos**. The wall recovers within a few seconds.
5. Moderate: open http://localhost:8000/?admin, paste `local-admin-token`,
and mark a doubt answered.

### Step 7. Run the tests (optional)

With the virtual environment of each app active:

``` bash
cd doubt-wall
pip install -r requirements-dev.txt
# Windows: $env:TEST_DATABASE_URL="postgresql://doubtwall:devpass@localhost:5432/doubtwall_test"; pytest -q
TEST_DATABASE_URL=postgresql://doubtwall:devpass@localhost:5432/doubtwall_test pytest -q   # 24 passed

cd ../aiops-triage
pip install -r requirements-dev.txt
pytest -q                                                                                 # 30 passed
```

If the Doubt Wall summary says `skipped` instead of `passed`, pytest couldn't
reach `doubtwall_test`; check the URL and that PostgreSQL is running.

### Stop and start again

* **Stop:** press `Ctrl+C` in terminals 1 and 2. PostgreSQL keeps the doubts.
* **Start again later:** open two terminals, activate each `.venv`, set the
same environment variables and run `python wsgi.py`. Environment variables
are per terminal, so set them every time (or put them in a small script).
* **Empty the wall:** `psql "postgresql://doubtwall:devpass@localhost:5432/doubtwall" -c "TRUNCATE doubts RESTART IDENTITY;"`

- - -

## Troubleshooting local runs

| Symptom | Fix |
| ------- | --- |
| `/ready` says `database unreachable` | PostgreSQL isn't running, or `DB_HOST`/`DB_PASSWORD` differ from Step 2. Test with the `psql "postgresql://..."` command from Step 2 |
| `password authentication failed for user "doubtwall"` | The password in `DB_PASSWORD` doesn't match `CREATE USER`. Reset it: `ALTER USER doubtwall PASSWORD 'devpass';` as `postgres` |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Windows blocks venv scripts by default. Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again |
| `Address already in use` / port 8000 or 8080 busy | Another program uses the port. Change `PORT` (and `CHAOS_TARGET_URL` if you move the Doubt Wall) |
| Console chaos panel says `Doubt Wall unreachable` | The Doubt Wall isn't running, or `CHAOS_TARGET_URL` is wrong |
| Console chaos says `Admin token is missing or wrong` | Both apps must use the same `ADMIN_TOKEN` (or set `CHAOS_TARGET_TOKEN` on the console) |
| `seed.sh: command not found` on Windows | The scripts need Git Bash or WSL. Use the PowerShell example in Step 6 instead |
| Docker: `Set DB_PASSWORD in .env` | You skipped Option 1, Step 2 |

- - -

## How the pieces talk

1. Learners use the Doubt Wall. Each request is counted in `/metrics`.
2. Prometheus scrapes both apps. Alert rules (in `doubt-wall/README.md`) fire on
error rate and latency.
3. Alertmanager posts firing alerts to `/ops/webhook/alertmanager?token=…`.
CloudWatch alarms arrive through SNS at `/ops/webhook/cloudwatch?token=…`.
4. AIOps Triage turns each alert into a diagnosis: root cause, impact, steps,
commands. Mock rules by default; Claude when `LLM_MODE=claude` and a key is set.
5. The console's chaos panel calls the Doubt Wall's admin API to inject
errors, latency, CPU load or a failing readiness probe, which starts the loop again.

## Secrets and tokens

| Value | Used by | Keep it in |
| ----- | ------- | ---------- |
| `DB_PASSWORD` | PostgreSQL, Doubt Wall | Kubernetes Secret |
| `ADMIN_TOKEN` | Doubt Wall moderation + chaos, console sign-in | Kubernetes Secret |
| `WEBHOOK_TOKEN` | Alertmanager and SNS URLs | Kubernetes Secret / Alertmanager config |
| `ANTHROPIC_API_KEY` | AIOps Triage (Claude mode only) | Kubernetes Secret, Jenkins credential |

None of them go into an image, a Jenkinsfile or git. The tokens in Option 2
(`local-admin-token`, `devpass`) are for your laptop only.

## What's public and what isn't

* Public on port 80: the Doubt Wall. Learners can only post, upvote and read.
* Behind the admin token: moderation, every chaos switch, the whole console
(so learners can't spend your Claude credits or break the demo).
* Behind a webhook token: the two alert webhooks.
* Always open: `/health`, `/ready`, `/metrics` on both apps (for probes and
Prometheus). Keep Prometheus and Jenkins ports closed to the internet in the
security group.

## Folder layout

```

  README.md                 this guide
  POSTGRES-CHEATSHEET.md    psql commands used in class
  ARCHITECTURE-AND-SECURITY.md  stack, security, guardrails, how to explain it
  PRODUCTION-READINESS-GUIDE.md checklist before building/deploying, stack selection, reading list
  docker-compose.yml        Option 1
  .env.example              template for Option 1's .env
  PRACTICE-TASK.md          learner guide: AWS deployment end to end, all files in the appendix
  PRODUCTION-DEPLOYMENT-STRATEGIES.md  how companies deploy, strategies, learner Q&A
  terraform/                AWS infrastructure (bootstrap state bucket + infra)
  ansible/                  server configuration (k3s, Jenkins as code, Argo CD)
  argocd/                   Argo CD Application (GitOps)
  tools/                    build_practice_guide.py (refreshes the guide's appendix)
  Jenkinsfile               CI/CD pipeline (stages call ci/*.sh)
  JENKINS.md                pipeline setup and security notes
  ci/                       test, build, scan, push, deploy, smoke scripts
  k8s/                      Kubernetes manifests (base + aws/local/gitops overlays)
  .gitattributes            keeps Linux line endings for scripts on Windows
  doubt-wall/               learner app: code, tests, Dockerfile, scripts
  aiops-triage/             instructor console: code, tests, Dockerfile, samples
```

See [`POSTGRES-CHEATSHEET.md`](POSTGRES-CHEATSHEET.md) for the database commands
used in class, and [`ARCHITECTURE-AND-SECURITY.md`](ARCHITECTURE-AND-SECURITY.md) for
the stack choices, security model, database guardrails and an end-to-end
explanation script. [`PRODUCTION-READINESS-GUIDE.md`](PRODUCTION-READINESS-GUIDE.md)
covers what to check before building or deploying anything for production,
how to choose a tech stack, and a reading list of standards and books.