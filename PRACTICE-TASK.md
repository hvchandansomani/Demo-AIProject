# Practice Task: Deploy a Real App with Terraform, Ansible, Jenkins, Kubernetes and Argo CD

You'll take two small apps and a database from your laptop to AWS, the way a
real team would: infrastructure as code, configuration as code, a CI/CD
pipeline with tests and a security scan, Kubernetes, and finally GitOps with
Argo CD.

Every file you need is in this repository, and copies of all of them are at
the end of this guide (Appendix) for reference.

---

## What you'll build

```
                 YOUR LAPTOP (WSL / Linux / macOS)
                 terraform ──► AWS        ansible ──► servers       kubectl / argocd
                                │                        │
        ┌───────────────────────┼────────────────────────┼──────────────────────────┐
        │ AWS (ap-south-1)      ▼                        ▼                          │
        │  ┌──────────────── VPC 10.20.0.0/16, public subnet ───────────────────┐   │
        │  │                                                                    │   │
        │  │  EC2 "jenkins" (t3.medium)          EC2 "k3s" (t3.large)           │   │
        │  │  Jenkins + Docker + kubectl  ─6443─► k3s (Kubernetes)              │   │
        │  │     │ push images                    ├─ Traefik Ingress :80 ◄── learners
        │  │     ▼                                ├─ Doubt Wall (2-5 pods)      │   │
        │  │  ECR (doubt-wall, aiops-triage) ◄─pull┤─ AIOps Triage (1 pod)      │   │
        │  │                                      ├─ PostgreSQL (StatefulSet)   │   │
        │  │                                      └─ Argo CD (Part 6)           │   │
        │  └────────────────────────────────────────────────────────────────────┘   │
        │  S3: Terraform state (versioned, encrypted, locked)   Budget alarm        │
        └───────────────────────────────────────────────────────────────────────────┘
```

| App | What it is |
|---|---|
| **Doubt Wall** (`doubt-wall/`) | Learners post and upvote questions. Flask + PostgreSQL |
| **AIOps Triage** (`aiops-triage/`) | Instructor console: turns alerts into diagnoses (rules or Claude), can break the Doubt Wall on purpose |

## How the task is organised

| Part | You do | Tool | Time |
|---|---|---|---|
| 0 | Set up your machine and fork the repo | Git, WSL | 30 min |
| 1 | Run the apps locally | Docker Compose | 15 min |
| 2 | Create the AWS infrastructure, with remote state and locking | Terraform | 30 min |
| 3 | Configure the servers | Ansible | 20 min |
| 4 | **Deploy to Kubernetes by hand** | Docker, ECR, kubectl, Kustomize | 45 min |
| 5 | Automate it with a pipeline | Jenkins | 30 min |
| 6 | Switch to GitOps | Argo CD | 45 min |
| 7 | Break it and watch it recover (optional) | AIOps Triage, kubectl | 20 min |
| 8 | **Destroy everything** | Terraform | 10 min |

Each part ends with a ✅ **Checkpoint**. Don't move on until it passes.

> **Cost warning.** While the servers exist you pay roughly US$0.15 per hour
> (ap-south-1). Do Part 8 at the end of every session. A budget alarm emails
> you if spending heads past your limit, but it's a safety net, not a brake.

---

## Part 0: Prepare your machine

### 0.1 Tools

Windows users: install **WSL with Ubuntu** (`wsl --install` in an admin
PowerShell, then reboot) and run **every command in this guide inside WSL**.
Ansible doesn't run on Windows, and mixing Windows and WSL paths breaks SSH keys.

Inside WSL / Linux:

```bash
sudo apt update && sudo apt install -y git curl unzip jq python3-pip pipx docker.io
pipx install ansible-core && pipx ensurepath

# Terraform (HashiCorp's official repository)
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y terraform

# AWS CLI v2
curl -sSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscli.zip
unzip -q /tmp/awscli.zip -d /tmp && sudo /tmp/aws/install

# kubectl (same minor version as the cluster)
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.34/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.34/deb/ /" \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt update && sudo apt install -y kubectl
```

Docker Desktop users on Windows can enable "WSL integration" instead of
installing `docker.io` inside WSL.

### 0.2 AWS access

Use an IAM user or IAM Identity Center (SSO), **never the root user**, with
MFA enabled. For this lab, `AdministratorAccess` on a practice account is fine.

```bash
aws configure              # or: aws configure sso
aws sts get-caller-identity   # shows your account ID: you're signed in
```

### 0.3 SSH key and your IP

```bash
ssh-keygen -t ed25519 -f ~/.ssh/marathon -N ""
curl -s https://checkip.amazonaws.com          # note this IP; you'll add /32
```

### 0.4 Fork and clone

1. On GitHub, fork this repository and make sure your fork is **public**
   (Jenkins and Argo CD read it without credentials; there are no secrets in it).
2. Clone **your fork**:

```bash
git clone https://github.com/<you>/Demo-B17.git && cd Demo-B17
```

✅ **Checkpoint 0:** `terraform version` (1.11+), `ansible --version`,
`aws sts get-caller-identity`, `kubectl version --client` and `docker version`
all work.

---

## Part 1: Run it locally

```bash
cp .env.example .env               # change the three change-me values
docker compose up --build
```

Open http://localhost:8000 (Doubt Wall) and http://localhost:8080/ops (console;
sign in with your `ADMIN_TOKEN`). Post a doubt, then `Ctrl+C` and
`docker compose down`.

✅ **Checkpoint 1:** you posted a doubt and saw it on the wall.

📖 Read: `README.md` (Option 1), `docker-compose.yml`, both `Dockerfile`s.

---

## Part 2: Infrastructure with Terraform

### 2.1 Remote state bucket (once per AWS account)

Terraform remembers what it created in a **state file**. On a team, it must
live somewhere shared (S3) and be **locked** while someone is running
Terraform, or two people's changes corrupt it.

```bash
cd terraform/bootstrap
terraform init
terraform apply                       # type "yes"
terraform output -raw backend_hcl > ../infra/backend.hcl
cat ../infra/backend.hcl              # note: use_lockfile = true
```

### 2.2 The infrastructure

```bash
cd ../infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: admin_cidr = "<your IP>/32", budget_email = "<you>"
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan            # read it: ~30 resources to add
terraform apply tfplan
```

When it finishes, note the outputs `jenkins_url`, `app_url` and `ecr_registry`.

### 2.3 Prove the lock works

1. Terminal A: `terraform apply` (no plan file). Leave it waiting at "Enter a value:".
2. Terminal B (same folder): `terraform plan`. It fails with
   **"Error acquiring the state lock"** and shows who holds it.
3. In the S3 console, open your state bucket: there's a `terraform.tfstate.tflock` object.
4. Type `no` in Terminal A. The lock object disappears.

✅ **Checkpoint 2:** `terraform output` shows both IPs;
`ansible/inventory/hosts.ini` exists (Terraform wrote it); you saw the lock error.

📖 Read: `terraform/README.md`, `terraform/infra/security.tf`, `iam.tf`, `ec2.tf`.
**Questions to answer:** Why is `admin_cidr` validated? Why do the servers have
IAM roles instead of access keys? What does `http_tokens = "required"` protect against?

---

## Part 3: Configure the servers with Ansible

```bash
cd ../../ansible
# group_vars/all.yml: set app_repo_url to YOUR fork's URL
export JENKINS_ADMIN_PASSWORD='pick-a-long-password'
ansible all -m ping                   # both answer "pong"
ansible-playbook site.yml             # ~10 minutes
```

The playbook installs k3s on one server and Jenkins on the other, connects
them, and saves two kubeconfig files in `ansible/artifacts/`.

```bash
export KUBECONFIG=$PWD/artifacts/kubeconfig-admin.yaml
kubectl get nodes                     # k3s-1   Ready
```

Open the Jenkins URL, sign in as `admin`. The `marathon` job already exists,
and **Manage Jenkins > Credentials** shows `k3s-kubeconfig`. Nobody clicked
through a setup wizard: that's Configuration as Code.

✅ **Checkpoint 3:** `kubectl get nodes` shows `Ready`; you can sign in to Jenkins.

📖 Read: `ansible/README.md`, `ansible/site.yml`, `roles/k3s/tasks/main.yml`,
`roles/jenkins/templates/casc.yaml.j2`. **Question:** run `ansible-playbook site.yml`
again. Why does almost every task now say `ok` instead of `changed`?

---

## Part 4: Deploy to Kubernetes by hand

Before automating anything, do every step yourself once. Jenkins will later do
exactly these steps (`ci/build.sh`, `ci/push.sh`, `ci/deploy.sh`).

### 4.1 Build and push the images (on the Jenkins server)

```bash
ssh -i ~/.ssh/marathon ubuntu@<jenkins_public_ip>
git clone https://github.com/<you>/Demo-B17.git && cd Demo-B17
export IMAGE_TAG=manual-1 AWS_REGION=ap-south-1 \
       ECR_REGISTRY=<ecr_registry output from Terraform>
sudo -E bash ci/build.sh               # docker build, both apps
sudo -E bash ci/push.sh                # ECR login with the server's IAM role, then push
exit
```

In the AWS console, **ECR > Repositories**: both repositories have a
`manual-1` image, and "Scan on push" found the vulnerabilities (if any).

### 4.2 Create the secrets (from your laptop)

```bash
cd ~/Demo-B17
export KUBECONFIG=$PWD/ansible/artifacts/kubeconfig-admin.yaml
./k8s/create-secrets.sh                # save the ADMIN_TOKEN it prints
kubectl -n marathon get secrets        # marathon-secrets, and ecr-pull (made by the node's timer)
```

### 4.3 Apply the manifests

The AWS overlay has two placeholders. Fill them in on a copy (never commit them):

```bash
export ECR_REGISTRY=<ecr_registry>
cp -r k8s /tmp/k8s-manual
sed -i "s|REPLACE_ECR_REGISTRY|$ECR_REGISTRY|g; s|REPLACE_IMAGE_TAG|manual-1|g" \
  /tmp/k8s-manual/overlays/aws/kustomization.yaml

kubectl kustomize /tmp/k8s-manual/overlays/aws | less     # look before you apply
kubectl apply -k /tmp/k8s-manual/overlays/aws
kubectl -n marathon get pods -w                           # Ctrl+C when all are Running 1/1
```

### 4.4 Look around

```bash
kubectl -n marathon get deploy,sts,svc,ingress,hpa,pvc,networkpolicy
kubectl -n marathon describe pod -l app=doubt-wall | grep -A3 -i "liveness\|readiness"
kubectl -n marathon logs deploy/doubt-wall --tail=20
kubectl -n marathon exec -it postgres-0 -- psql -U postgres -d doubtwall -c '\dt'
```

Open `http://<k3s_public_ip>` (Doubt Wall) and `http://<k3s_public_ip>/ops`.

✅ **Checkpoint 4:** the Doubt Wall works on the public IP; the footer says
version `manual-1`; you posted a doubt and it survived
`kubectl -n marathon delete pod -l app=doubt-wall`.

📖 Read: `k8s/README.md` and the manifests in `k8s/base/`. **Questions:** Why
does `/health` not check the database but `/ready` does? What does
`maxUnavailable: 0` do during an update? Why can the app's database user not
`DROP TABLE`?

---

## Part 5: Automate it with Jenkins

1. Open Jenkins > `marathon` > **Build Now**. The first build only registers
   the parameters; it may deploy with defaults.
2. Then **Build with Parameters**:

| Parameter | Value |
|---|---|
| `DEPLOY` | ✔ |
| `DEPLOY_MODE` | `kubectl` |
| `FAIL_ON_CRITICAL_VULNS` | ✔ |
| `APP_URL` | `http://<k3s_public_ip>` |

Watch the stages: **Test** (unit tests against a real throwaway PostgreSQL),
**Build images**, **Security scan** (Trivy), **Push to ECR**, **Deploy to k3s**,
**Smoke test**.

### 5.1 Ship a visible change (rolling update)

Edit `k8s/base/kustomization.yaml`, change `BANNER_TEXT` and `BANNER_COLOR`
(e.g. `#0B7A3E`), commit, push, and run the job. In a second terminal:

```bash
kubectl -n marathon get pods -w
```

Old pods are replaced one at a time; the page never goes down; the banner changes.

### 5.2 Watch a bad release get rolled back

In `k8s/base/doubt-wall/deployment.yaml`, change the readiness probe path from
`/ready` to `/nope`. Commit, push, run the job. The new pod never becomes
ready, the old pods keep serving, and after 3 minutes Jenkins rolls back.
Revert the change afterwards.

✅ **Checkpoint 5:** a green pipeline; the banner changed with zero downtime;
the broken release was rolled back automatically.

📖 Read: `JENKINS.md`, `Jenkinsfile`, `ci/*.sh`. **Questions:** Why is Trivy
pinned to 0.69.3? Why does the scan export the image to a file instead of
mounting the Docker socket? Where does Jenkins get AWS credentials?

---

## Part 6: GitOps with Argo CD

So far Jenkins **pushes** changes into the cluster. With GitOps, git is the
single source of truth and an agent inside the cluster (Argo CD) **pulls**:
Jenkins only builds images and writes the new tag into git.

```
push model (Part 5):  Jenkins ──kubectl apply──► cluster
pull model (Part 6):  Jenkins ──commit tag──► git ◄──watches── Argo CD ──applies──► cluster
```

### 6.1 Install Argo CD

```bash
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.3/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8443:443
```

Open https://localhost:8443 (accept the self-signed certificate), user `admin`.
`--server-side` is needed because some Argo CD CRDs are too big for a normal apply.

### 6.2 Let Jenkins push to your fork

1. GitHub > Settings > Developer settings > **Fine-grained tokens** > Generate.
   Repository access: **only your fork**. Permissions: **Contents: Read and write**.
   Expiry: 7 days.
2. Jenkins > Manage Jenkins > Credentials > System > Global > **Add Credentials**:
   Kind *Username with password*, username = your GitHub user, password = the
   token, ID = **`git-push`**.

### 6.3 Record the first version in git

Run the job with `DEPLOY_MODE = gitops`. The **Update GitOps repo** stage
commits `[skip ci] Deploy <tag>` to your fork, changing only
`k8s/overlays/gitops/kustomization.yaml`. Look at the commit on GitHub.

### 6.4 Create the Argo CD Application

Edit `argocd/application.yaml`: set `repoURL` to your fork. Then:

```bash
kubectl apply -f argocd/application.yaml
```

In the Argo CD UI, the `marathon` app appears, syncs, and turns
**Healthy / Synced**. It takes over the resources you created in Part 4.
From now on, use only `DEPLOY_MODE = gitops`: if Jenkins also ran
`kubectl apply`, Argo CD would undo it.

### 6.5 See GitOps behaviour

| Try | What happens |
|---|---|
| `kubectl -n marathon scale deploy/aiops-triage --replicas=3` | Argo CD's **self-heal** puts it back to 1 within seconds: git wins |
| Change `BANNER_TEXT`, push, run the job in gitops mode | Jenkins commits the new tag, Argo CD deploys it (up to ~3 minutes, or click **Refresh**) |
| `git revert` the last `[skip ci] Deploy` commit and push | Argo CD rolls the cluster back to the previous version. **Rollback = git revert** |
| Delete a Service in the UI | Argo CD recreates it |

✅ **Checkpoint 6:** the app is Synced/Healthy in Argo CD; a change reached the
cluster without Jenkins touching it; self-heal reverted your manual scale;
`git revert` rolled back.

📖 Read: `argocd/application.yaml`, `k8s/overlays/gitops/kustomization.yaml`,
`ci/gitops-update.sh`. **Questions:** Why does the commit message contain
`[skip ci]`? Why are Secrets not in git, and what tools solve that in real
projects? What does `prune: true` do?

---

## Part 7 (optional): Break it and watch it recover

Open `http://<k3s_public_ip>/ops`, sign in with `ADMIN_TOKEN`, and use
**Break things on purpose**. Each switch hits every Doubt Wall pod:

| Switch | Watch with |
|---|---|
| Fail 30% of requests | Learners see errors; `Send 50 test requests` shows ~15 failures |
| Slow every request by 1.5 s | `time curl http://<k3s_public_ip>/api/doubts` |
| Burn CPU for 2 min | `kubectl -n marathon get hpa -w`: the HPA adds pods |
| Fail readiness probe | `kubectl -n marathon get endpoints doubt-wall -w`: pods leave the Service |

Then **Stop all chaos**. Also try: `kubectl -n marathon delete pod postgres-0`.
The StatefulSet recreates it and the doubts are still there.

---

## Part 8: Destroy everything

```bash
# If you did Part 6, delete the Argo CD app first (it also deletes the app's resources)
kubectl delete -f argocd/application.yaml --ignore-not-found

cd terraform/infra
terraform destroy                     # type "yes"; ~3 minutes
```

Check in the AWS console (your region): **EC2 instances** terminated, **Elastic
IPs** released, **ECR** repositories gone. The state bucket from `bootstrap/`
stays; it costs a few cents a month (see `terraform/README.md` to remove it).
Delete your GitHub token too.

✅ **Checkpoint 8:** `terraform state list` in `terraform/infra` prints nothing.

---

## What to submit

1. Screenshot: `terraform apply` outputs, and the "Error acquiring the state lock" message.
2. Screenshot: `kubectl -n marathon get pods,svc,ingress,hpa` after Part 4.
3. Screenshot: a green Jenkins pipeline with all stages.
4. Screenshot: the Argo CD app tree, Synced and Healthy.
5. Short answers (2–3 lines each) to the **Questions** in Parts 2–6.
6. One paragraph: what would you change before running this in production?
   (Hint: `PRODUCTION-DEPLOYMENT-STRATEGIES.md` and the `#checkov:skip` comments.)

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `terraform init`: `NoSuchBucket` | `backend.hcl` bucket name wrong; re-run `terraform output -raw backend_hcl` in `bootstrap/` |
| `terraform apply`: `admin_cidr must be a valid CIDR` | Use `x.x.x.x/32` |
| Plan/apply: "Error acquiring the state lock" (when nobody is running) | A crashed run left the lock: `terraform force-unlock <ID>` |
| Ansible `UNREACHABLE` | Your IP changed: update `admin_cidr`, `terraform apply` |
| Ansible: "WARNING: unprotected private key file" | Key under `/mnt/c`; keep it in WSL `~/.ssh` with `chmod 600` |
| `kubectl`: connection refused / timeout | `KUBECONFIG` not set, or your IP changed (port 6443 is limited to `admin_cidr`) |
| Pods `ImagePullBackOff` | Tag not in ECR, or the pull secret missing: `sudo systemctl start ecr-pull-secret-refresh` on the k3s server |
| Pods `CreateContainerConfigError` | `marathon-secrets` missing: run `./k8s/create-secrets.sh` |
| Jenkins Test stage: `REQUIRE_DB is set but PostgreSQL is not reachable` | Docker on Jenkins can't start the test database; check `docker ps -a` on the server |
| Jenkins Security scan fails | A fixable CRITICAL vulnerability: read `reports/trivy-*.txt`, update the base image, or untick `FAIL_ON_CRITICAL_VULNS` while learning |
| Jenkins GitOps stage: `Authentication failed` | Token expired, wrong scope, or credential ID isn't `git-push` |
| Argo CD app `OutOfSync` / `Degraded` | Click the resource for the exact error; often `repoURL` points at the original repo instead of your fork |
| Smoke test times out in gitops mode | Argo CD hasn't synced yet: click **Refresh** in Argo CD, or re-run |

---

## Appendix: every file, for reference

Copied automatically from the repository by `tools/build_practice_guide.py`,
so they always match the real files. Click a file name to expand it.

<!-- APPENDIX:START (everything below is generated; edit the real files, then re-run the script) -->

- [Docker and local run](#docker-and-local-run) (6 files)
- [Terraform: state bucket (bootstrap)](#terraform-state-bucket-bootstrap) (4 files)
- [Terraform: infrastructure](#terraform-infrastructure) (14 files)
- [Ansible](#ansible) (24 files)
- [Kubernetes manifests](#kubernetes-manifests) (18 files)
- [Jenkins pipeline](#jenkins-pipeline) (8 files)
- [Argo CD](#argo-cd) (1 files)

### Docker and local run

<details>
<summary><code>docker-compose.yml</code></summary>

```yaml
# Runs the whole marathon stack on one machine:
#   Doubt Wall    http://localhost:8000      (what learners use)
#   AIOps Triage  http://localhost:8080/ops  (instructor console)
#   PostgreSQL    internal only
#
#   cp .env.example .env    # then edit the tokens
#   docker compose up --build
services:
  postgres:
    image: postgres:18-alpine
    environment:
      POSTGRES_USER: doubtwall
      POSTGRES_PASSWORD: ${DB_PASSWORD:?Set DB_PASSWORD in .env}
      POSTGRES_DB: doubtwall
    volumes:
      # PostgreSQL 18 images keep data under /var/lib/postgresql/18/docker,
      # so the volume is mounted one level up (not .../data as in 17 and older).
      - pgdata:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U doubtwall -d doubtwall"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  doubt-wall:
    build:
      context: ./doubt-wall
      args: { APP_VERSION: "${APP_VERSION:-1.0.0}" }
    image: doubt-wall:local
    ports: ["8000:8000"]
    environment:
      DB_HOST: postgres
      DB_USER: doubtwall
      DB_NAME: doubtwall
      DB_PASSWORD: ${DB_PASSWORD}
      ADMIN_TOKEN: ${ADMIN_TOKEN:?Set ADMIN_TOKEN in .env}
      SESSION_TITLE: ${SESSION_TITLE:-DevOps Marathon}
      BANNER_TEXT: ${BANNER_TEXT:-Welcome to the DevOps marathon. Ask anything.}
      BANNER_COLOR: "${BANNER_COLOR:-#1F4E79}"
    depends_on:
      postgres: { condition: service_healthy }
    restart: unless-stopped

  aiops-triage:
    build:
      context: ./aiops-triage
      args: { APP_VERSION: "${APP_VERSION:-1.0.0}" }
    image: aiops-triage:local
    ports: ["8080:8000"]
    environment:
      URL_PREFIX: /ops
      ADMIN_TOKEN: ${ADMIN_TOKEN}
      WEBHOOK_TOKEN: ${WEBHOOK_TOKEN:-}
      LLM_MODE: ${LLM_MODE:-mock}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      CLAUDE_MODEL: ${CLAUDE_MODEL:-claude-haiku-4-5}
      CHAOS_TARGET_URL: http://doubt-wall:8000
      CHAOS_TARGET_NAME: Doubt Wall
    depends_on: [doubt-wall]
    restart: unless-stopped

volumes:
  pgdata:
```

</details>

<details>
<summary><code>.env.example</code></summary>

```bash
# Copy to .env and change every value. Never commit .env.
# Generate tokens with:  openssl rand -hex 16

DB_PASSWORD=change-me-db-password
ADMIN_TOKEN=change-me-admin-token
WEBHOOK_TOKEN=change-me-webhook-token

# mock = built-in rules (free). claude = real Claude API (needs the key).
LLM_MODE=mock
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-haiku-4-5

APP_VERSION=1.0.0
SESSION_TITLE=DevOps Marathon
BANNER_TEXT=Welcome to the DevOps marathon. Ask anything.
BANNER_COLOR="#1F4E79"
```

</details>

<details>
<summary><code>doubt-wall/Dockerfile</code></summary>

```dockerfile
# ---------- stage 1: build wheels (compilers stay out of the final image)
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- stage 2: runtime
FROM python:3.12-slim
ARG APP_VERSION=1.0.0
ARG GIT_COMMIT=unknown
# Standard image labels: `docker inspect` (or the ECR console) shows which
# build and commit produced this image.
LABEL org.opencontainers.image.title="Doubt Wall" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION} \
    TRUST_PROXY=true

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser wsgi.py .

# Kubernetes runs this with runAsNonRoot, runAsUser 10001 and a read-only root
# filesystem (/tmp is an emptyDir), so keep the UID and avoid writing elsewhere.
USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"

# One worker, many threads: Prometheus metrics, rate limits and chaos state live
# in process memory, so one process keeps them consistent. Scale with replicas.
# (Doubts themselves are in PostgreSQL, so every replica shows the same wall.)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
```

</details>

<details>
<summary><code>aiops-triage/Dockerfile</code></summary>

```dockerfile
# ---------- stage 1: build wheels (compilers stay out of the final image)
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- stage 2: runtime
FROM python:3.12-slim
ARG APP_VERSION=1.0.0
ARG GIT_COMMIT=unknown
# Standard image labels: `docker inspect` (or the ECR console) shows which
# build and commit produced this image.
LABEL org.opencontainers.image.title="AIOps Triage" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION} \
    LLM_MODE=mock

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser wsgi.py .

# Kubernetes runs this with runAsNonRoot, runAsUser 10001 and a read-only root
# filesystem (/tmp is an emptyDir), so keep the UID and avoid writing elsewhere.
USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"

# One worker, many threads: incidents and Prometheus metrics live in process
# memory, so a single process keeps them consistent. Scale with replicas instead.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
```

</details>

<details>
<summary><code>doubt-wall/.dockerignore</code></summary>

```
.git
.env
__pycache__
*.pyc
.pytest_cache
tests
README.md
```

</details>

<details>
<summary><code>.gitattributes</code></summary>

```
# Keep Linux line endings for files that run on the Linux servers, even when
# the repo is checked out on Windows. CRLF line endings break bash scripts
# ("$'\r': command not found") and can confuse YAML and the Jenkinsfile.
*.sh        text eol=lf
Jenkinsfile text eol=lf
*.yaml      text eol=lf
*.yml       text eol=lf
Dockerfile  text eol=lf
*.py        text eol=lf
```

</details>


### Terraform: state bucket (bootstrap)

<details>
<summary><code>terraform/bootstrap/main.tf</code></summary>

```hcl
# One-time setup: the S3 bucket that stores Terraform state for ../infra.
#
# Chicken-and-egg: the bucket can't store its own state before it exists,
# so this small stack keeps a LOCAL state file (terraform.tfstate in this
# folder). Keep that file safe, or just re-import the bucket if it's lost.

data "aws_caller_identity" "current" {}

locals {
  # Bucket names are global across all AWS accounts, so the account ID and
  # region are added to make the name unique.
  bucket_name = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"
}

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_18:Access logs need a second bucket; production should add them, a class doesn't need them.
  #checkov:skip=CKV2_AWS_62:Event notifications aren't needed for a state bucket.
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles cost; versioning already protects the state history.
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256) encryption is enabled; a KMS key adds cost and key management.
  bucket = local.bucket_name

  # Protects the state history from an accidental `terraform destroy`.
  # To really delete it: set to false, apply, then empty and destroy.
  lifecycle {
    prevent_destroy = true
  }
}

# Every state change keeps the previous version, so a bad apply or a
# corrupted state can be rolled back.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# State files contain secrets (IPs, ARNs, sometimes passwords): encrypt them.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Refuse any request that isn't over HTTPS.
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# Keep old state versions for 90 days, then let S3 delete them.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.state]
}

# Optional, legacy: DynamoDB lock table (see variable description).
resource "aws_dynamodb_table" "lock" {
  count        = var.create_dynamodb_lock_table ? 1 : 0
  name         = "${var.project}-tf-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
```

</details>

<details>
<summary><code>terraform/bootstrap/outputs.tf</code></summary>

```hcl
output "state_bucket" {
  description = "Put this in ../infra/backend.hcl as `bucket`."
  value       = aws_s3_bucket.state.bucket
}

output "dynamodb_lock_table" {
  description = "Only set when create_dynamodb_lock_table = true."
  value       = var.create_dynamodb_lock_table ? aws_dynamodb_table.lock[0].name : null
}

output "backend_hcl" {
  description = "Ready-to-paste contents for ../infra/backend.hcl."
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    key          = "${var.project}/infra/terraform.tfstate"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}
```

</details>

<details>
<summary><code>terraform/bootstrap/variables.tf</code></summary>

```hcl
variable "region" {
  description = "AWS region for the state bucket (use the same region as ../infra)."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Short name used in resource names and tags."
  type        = string
  default     = "marathon"
}

variable "create_dynamodb_lock_table" {
  description = <<-EOT
    Also create a DynamoDB table for state locking. Not needed with Terraform
    1.11+, which locks with a lock file in S3 (use_lockfile = true). DynamoDB
    locking is deprecated; enable this only to demonstrate the older method or
    for teams still on Terraform < 1.11.
  EOT
  type        = bool
  default     = false
}
```

</details>

<details>
<summary><code>terraform/bootstrap/versions.tf</code></summary>

```hcl
terraform {
  # 1.11+ is needed for S3-native state locking (use_lockfile) in ../infra.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Stack     = "bootstrap"
    }
  }
}
```

</details>


### Terraform: infrastructure

<details>
<summary><code>terraform/infra/versions.tf</code></summary>

```hcl
terraform {
  # 1.11+: S3-native state locking (use_lockfile) is generally available.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource gets these tags: easy to find, and easy to see the cost of.
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Stack     = "infra"
    }
  }
}
```

</details>

<details>
<summary><code>terraform/infra/backend.tf</code></summary>

```hcl
# Remote state in S3 with locking.
#
# The settings live in backend.hcl (not in git, because the bucket name
# contains your AWS account ID). Create it from backend.hcl.example or from
# the `backend_hcl` output of ../bootstrap, then run:
#
#   terraform init -backend-config=backend.hcl
#
# Locking: with `use_lockfile = true`, Terraform writes a small
# terraform.tfstate.tflock object next to the state while it runs. A second
# `terraform apply` started at the same time sees the lock and waits/fails
# instead of corrupting the state.
terraform {
  backend "s3" {}
}
```

</details>

<details>
<summary><code>terraform/infra/backend.hcl.example</code></summary>

```hcl
# Copy to backend.hcl and fill in the bucket name printed by ../bootstrap
# (terraform -chdir=../bootstrap output -raw backend_hcl > backend.hcl).
bucket       = "marathon-tfstate-123456789012-ap-south-1"
key          = "marathon/infra/terraform.tfstate"
region       = "ap-south-1"
encrypt      = true

# Terraform 1.11+: lock with a lock file stored in S3 (recommended).
use_lockfile = true

# Older method, deprecated since Terraform 1.11: lock with a DynamoDB table.
# Needs create_dynamodb_lock_table = true in ../bootstrap. You can set both
# while migrating; Terraform then takes both locks.
# dynamodb_table = "marathon-tf-lock"
```

</details>

<details>
<summary><code>terraform/infra/variables.tf</code></summary>

```hcl
variable "region" {
  description = "AWS region for everything."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Short name used in resource names and tags."
  type        = string
  default     = "marathon"
}

variable "admin_cidr" {
  description = <<-EOT
    Your public IP as a /32 (e.g. 203.0.113.10/32). Only this address can
    reach SSH, Jenkins (8080), the Kubernetes API (6443) and NodePorts.
    Find it with: curl -s https://checkip.amazonaws.com
  EOT
  type        = string

  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid CIDR such as 203.0.113.10/32, and not 0.0.0.0/0 (that would open SSH and Jenkins to the whole internet)."
  }
}

variable "ssh_public_key_path" {
  description = "Public key to install on both servers (create one with: ssh-keygen -t ed25519 -f ~/.ssh/marathon)."
  type        = string
  default     = "~/.ssh/marathon.pub"
}

variable "ssh_private_key_path" {
  description = "Matching private key. Only written into the Ansible inventory; never read by Terraform."
  type        = string
  default     = "~/.ssh/marathon"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.20.1.0/24"
}

variable "jenkins_instance_type" {
  description = "Jenkins builds Docker images: 2 vCPU / 4 GiB is the comfortable minimum."
  type        = string
  default     = "t3.medium"
}

variable "k3s_instance_type" {
  description = "Runs both apps, PostgreSQL, and later Prometheus + Grafana: 2 vCPU / 8 GiB."
  type        = string
  default     = "t3.large"
}

variable "jenkins_volume_gb" {
  type    = number
  default = 30
}

variable "k3s_volume_gb" {
  type    = number
  default = 30
}

variable "ecr_repositories" {
  description = "One private image repository per app."
  type        = list(string)
  default     = ["doubt-wall", "aiops-triage"]
}

variable "budget_limit_usd" {
  description = "Monthly cost alarm threshold for the whole AWS account."
  type        = number
  default     = 25
}

variable "budget_email" {
  description = "Email for budget alerts (80% spent, 100% forecast). Empty = no budget alarm."
  type        = string
  default     = ""
}
```

</details>

<details>
<summary><code>terraform/infra/terraform.tfvars.example</code></summary>

```hcl
# Copy to terraform.tfvars (not committed) and fill in.
region       = "ap-south-1"
project      = "marathon"
admin_cidr   = "203.0.113.10/32"       # curl -s https://checkip.amazonaws.com, then add /32
budget_email = "you@example.com"

# Defaults, change only if needed:
# ssh_public_key_path   = "~/.ssh/marathon.pub"
# ssh_private_key_path  = "~/.ssh/marathon"
# jenkins_instance_type = "t3.medium"
# k3s_instance_type     = "t3.large"
# budget_limit_usd      = 25
```

</details>

<details>
<summary><code>terraform/infra/network.tf</code></summary>

```hcl
# A small VPC with one public subnet. Both servers get public IPs so the
# class can reach them directly; security groups decide what's open.
# (Production would put servers in private subnets behind a load balancer.)

data "aws_availability_zones" "available" {
  #checkov:skip=CKV_AWS_394:Only the first zone is used, once, when the subnet is created; later zone additions don't change it.
  state = "available"
}

resource "aws_vpc" "main" {
  #checkov:skip=CKV2_AWS_11:VPC flow logs cost money in CloudWatch Logs; worth enabling in production, skipped for a 2-day class.
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false # public IPs come from the Elastic IPs in ec2.tf
  tags                    = { Name = "${var.project}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.project}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The VPC's default security group allows all traffic between its members.
# Take control of it and leave it empty, so nothing can use it by accident.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-default-unused" }
}
```

</details>

<details>
<summary><code>terraform/infra/security.tf</code></summary>

```hcl
# Firewall rules. Default is "deny"; each rule below opens one thing.
#
#   Port         Jenkins          k3s
#   22  SSH      admin_cidr       admin_cidr
#   8080 Jenkins admin_cidr       -
#   80  HTTP     -                everyone (learners use the Doubt Wall)
#   6443 K8s API -                admin_cidr + Jenkins server
#   30000-32767  -                admin_cidr (NodePorts: Argo CD, Grafana)

resource "aws_security_group" "jenkins" {
  name        = "${var.project}-jenkins"
  description = "Jenkins server"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${var.project}-jenkins" }
}

resource "aws_security_group" "k3s" {
  name        = "${var.project}-k3s"
  description = "k3s node"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${var.project}-k3s" }
}

# ---------------------------------------------------------------- Jenkins
resource "aws_vpc_security_group_ingress_rule" "jenkins_ssh" {
  security_group_id = aws_security_group.jenkins.id
  description       = "SSH from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "jenkins_ui" {
  security_group_id = aws_security_group.jenkins.id
  description       = "Jenkins UI from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 8080
  to_port           = 8080
}

resource "aws_vpc_security_group_egress_rule" "jenkins_all" {
  security_group_id = aws_security_group.jenkins.id
  description       = "Outbound: packages, Docker Hub, ECR, GitHub, k3s API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------- k3s
resource "aws_vpc_security_group_ingress_rule" "k3s_ssh" {
  security_group_id = aws_security_group.k3s.id
  description       = "SSH from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "k3s_http" {
  #checkov:skip=CKV_AWS_260:Intentional. Learners open the Doubt Wall from anywhere; admin paths need a token.
  security_group_id = aws_security_group.k3s.id
  description       = "Doubt Wall and AIOps Triage through Traefik (public on purpose)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "k3s_api_admin" {
  security_group_id = aws_security_group.k3s.id
  description       = "Kubernetes API from the admin IP (kubectl from your laptop)"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 6443
  to_port           = 6443
}

resource "aws_vpc_security_group_ingress_rule" "k3s_api_jenkins" {
  security_group_id            = aws_security_group.k3s.id
  description                  = "Kubernetes API from the Jenkins server (deploy stage)"
  referenced_security_group_id = aws_security_group.jenkins.id
  ip_protocol                  = "tcp"
  from_port                    = 6443
  to_port                      = 6443
}

resource "aws_vpc_security_group_ingress_rule" "k3s_nodeports" {
  security_group_id = aws_security_group.k3s.id
  description       = "NodePorts (Argo CD, Grafana) from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 30000
  to_port           = 32767
}

resource "aws_vpc_security_group_egress_rule" "k3s_all" {
  security_group_id = aws_security_group.k3s.id
  description       = "Outbound: images, packages, Claude API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
```

</details>

<details>
<summary><code>terraform/infra/iam.tf</code></summary>

```hcl
# Each server gets an IAM role instead of AWS access keys. The AWS CLI on the
# server picks up short-lived credentials automatically, so there are no
# long-lived keys to leak, rotate or copy into Jenkins.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

locals {
  ecr_repo_arns = [for r in aws_ecr_repository.app : r.arn]
}

# ---------------------------------------------------------------- Jenkins: push images
resource "aws_iam_role" "jenkins" {
  name               = "${var.project}-jenkins"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "jenkins_ecr" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action doesn't support resource-level permissions
  }
  statement {
    sid = "PushAndPullOnlyOurRepos"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = local.ecr_repo_arns
  }
}

resource "aws_iam_role_policy" "jenkins_ecr" {
  name   = "ecr-push"
  role   = aws_iam_role.jenkins.id
  policy = data.aws_iam_policy_document.jenkins_ecr.json
}

resource "aws_iam_instance_profile" "jenkins" {
  name = "${var.project}-jenkins"
  role = aws_iam_role.jenkins.name
}

# ---------------------------------------------------------------- k3s: pull images
resource "aws_iam_role" "k3s" {
  name               = "${var.project}-k3s"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "k3s_ecr" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PullOnlyOurRepos"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = local.ecr_repo_arns
  }
}

resource "aws_iam_role_policy" "k3s_ecr" {
  name   = "ecr-pull"
  role   = aws_iam_role.k3s.id
  policy = data.aws_iam_policy_document.k3s_ecr.json
}

resource "aws_iam_instance_profile" "k3s" {
  name = "${var.project}-k3s"
  role = aws_iam_role.k3s.name
}

# ---------------------------------------------------------------- both: Session Manager
# Lets you open a shell from the AWS console (Systems Manager > Session
# Manager) even if SSH breaks or your IP changes. No port needs to be open.
resource "aws_iam_role_policy_attachment" "jenkins_ssm" {
  role       = aws_iam_role.jenkins.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "k3s_ssm" {
  role       = aws_iam_role.k3s.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
```

</details>

<details>
<summary><code>terraform/infra/ecr.tf</code></summary>

```hcl
# Private image registry: one repository per app.

resource "aws_ecr_repository" "app" {
  #checkov:skip=CKV_AWS_136:AES256 (AWS-managed) encryption at rest is enough for class images; a customer-managed KMS key adds cost and key management.
  for_each = toset(var.ecr_repositories)
  name     = each.value

  # A tag like "42-a1b2c3d" always means the same image: it can't be
  # overwritten, so what was tested is exactly what gets deployed.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true # AWS's own vulnerability scan, on top of Trivy in Jenkins
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  # Lets `terraform destroy` remove repositories that still contain images
  # (the class tears everything down after each day).
  force_delete = true
}

resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Delete untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the 15 newest images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 15
        }
        action = { type = "expire" }
      },
    ]
  })
}
```

</details>

<details>
<summary><code>terraform/infra/ec2.tf</code></summary>

```hcl
# Two Ubuntu 24.04 servers: Jenkins (CI/CD) and k3s (single-node Kubernetes).
# Terraform only creates them; Ansible installs and configures the software.

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720108477"] # Canonical, the publisher of official Ubuntu images

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_key_pair" "admin" {
  key_name   = "${var.project}-admin"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

locals {
  # Shared hardening for both servers.
  metadata_options = {
    http_endpoint = "enabled"
    # IMDSv2 only: blocks the classic SSRF trick of reading the instance's
    # IAM credentials through a plain GET to 169.254.169.254.
    http_tokens = "required"
    # 1 hop: processes on the host can reach the metadata service, containers
    # and pods (one extra network hop) cannot, so they can't borrow the role.
    http_put_response_hop_limit = 1
  }
}

resource "aws_instance" "jenkins" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.jenkins_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.jenkins.id]
  iam_instance_profile   = aws_iam_instance_profile.jenkins.name
  key_name               = aws_key_pair.admin.key_name
  monitoring             = true # 1-minute CloudWatch metrics (used in the CloudWatch phase)
  ebs_optimized          = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.jenkins_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = local.metadata_options.http_endpoint
    http_tokens                 = local.metadata_options.http_tokens
    http_put_response_hop_limit = local.metadata_options.http_put_response_hop_limit
  }

  # Don't replace the server just because Canonical published a newer image.
  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = "${var.project}-jenkins", Role = "jenkins" }
}

resource "aws_instance" "k3s" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.k3s_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.k3s.id]
  iam_instance_profile   = aws_iam_instance_profile.k3s.name
  key_name               = aws_key_pair.admin.key_name
  monitoring             = true
  ebs_optimized          = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.k3s_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = local.metadata_options.http_endpoint
    http_tokens                 = local.metadata_options.http_tokens
    http_put_response_hop_limit = local.metadata_options.http_put_response_hop_limit
  }

  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = "${var.project}-k3s", Role = "k3s" }
}

# Fixed public IPs: they survive stop/start, so URLs, the Ansible inventory and
# the k3s certificate (which lists the public IP) stay valid overnight.
resource "aws_eip" "jenkins" {
  domain   = "vpc"
  instance = aws_instance.jenkins.id
  tags     = { Name = "${var.project}-jenkins" }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_eip" "k3s" {
  domain   = "vpc"
  instance = aws_instance.k3s.id
  tags     = { Name = "${var.project}-k3s" }

  depends_on = [aws_internet_gateway.main]
}
```

</details>

<details>
<summary><code>terraform/infra/budget.tf</code></summary>

```hcl
# Email alarm when spending heads past the monthly limit: the most common
# surprise in cloud training is an instance left running for a month.
# Note: budget data refreshes a few times a day, so it's a safety net, not a
# real-time meter. `terraform destroy` after each session is the real fix.
resource "aws_budgets_budget" "monthly" {
  count = var.budget_email == "" ? 0 : 1

  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
```

</details>

<details>
<summary><code>terraform/infra/inventory.tf</code></summary>

```hcl
# Writes the Ansible inventory, so Ansible always targets the servers that
# Terraform just created. Terraform hands over to Ansible through this file.
locals {
  ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

data "aws_caller_identity" "current" {}

resource "local_file" "ansible_inventory" {
  filename        = "${path.module}/../../ansible/inventory/hosts.ini"
  file_permission = "0644"
  content = templatefile("${path.module}/templates/hosts.ini.tftpl", {
    jenkins_public_ip    = aws_eip.jenkins.public_ip
    jenkins_private_ip   = aws_instance.jenkins.private_ip
    k3s_public_ip        = aws_eip.k3s.public_ip
    k3s_private_ip       = aws_instance.k3s.private_ip
    ssh_private_key_path = var.ssh_private_key_path
    region               = var.region
    ecr_registry         = local.ecr_registry
  })
}
```

</details>

<details>
<summary><code>terraform/infra/templates/hosts.ini.tftpl</code></summary>

```ini
# Generated by Terraform (terraform/infra/inventory.tf). Don't edit by hand:
# run `terraform apply` again to regenerate it.

[jenkins]
jenkins-1 ansible_host=${jenkins_public_ip} private_ip=${jenkins_private_ip}

[k3s]
k3s-1 ansible_host=${k3s_public_ip} private_ip=${k3s_private_ip}

[all:vars]
ansible_user=ubuntu
ansible_ssh_private_key_file=${ssh_private_key_path}
aws_region=${region}
ecr_registry=${ecr_registry}
jenkins_public_ip=${jenkins_public_ip}
k3s_public_ip=${k3s_public_ip}
k3s_private_ip=${k3s_private_ip}
```

</details>

<details>
<summary><code>terraform/infra/outputs.tf</code></summary>

```hcl
output "jenkins_url" {
  value = "http://${aws_eip.jenkins.public_ip}:8080"
}

output "app_url" {
  description = "Doubt Wall for learners; the console is at /ops."
  value       = "http://${aws_eip.k3s.public_ip}"
}

output "jenkins_public_ip" {
  value = aws_eip.jenkins.public_ip
}

output "k3s_public_ip" {
  value = aws_eip.k3s.public_ip
}

output "k3s_private_ip" {
  value = aws_instance.k3s.private_ip
}

output "ecr_registry" {
  description = "Set as ECR_REGISTRY in Jenkins (Ansible does this for you)."
  value       = local.ecr_registry
}

output "ssh_commands" {
  value = {
    jenkins = "ssh -i ${var.ssh_private_key_path} ubuntu@${aws_eip.jenkins.public_ip}"
    k3s     = "ssh -i ${var.ssh_private_key_path} ubuntu@${aws_eip.k3s.public_ip}"
  }
}

output "next_step" {
  value = "Inventory written to ansible/inventory/hosts.ini. Next: cd ../../ansible && ansible-playbook site.yml"
}
```

</details>


### Ansible

<details>
<summary><code>ansible/ansible.cfg</code></summary>

```ini
[defaults]
inventory = inventory/hosts.ini
roles_path = roles
interpreter_python = auto_silent
forks = 5
retry_files_enabled = False
callback_result_format = yaml

[ssh_connection]
# accept-new: trust a server's key the first time, refuse if it later changes.
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=accept-new
pipelining = True
```

</details>

<details>
<summary><code>ansible/site.yml</code></summary>

```yaml
---
# Configures the servers Terraform created.
#   ansible-playbook site.yml
# Safe to run again: every task checks before it changes anything.

- name: Wait for both servers to finish booting
  hosts: all
  gather_facts: false
  tasks:
    - name: Wait until SSH answers
      ansible.builtin.wait_for_connection:
        timeout: 300

    - name: Wait for cloud-init (first-boot setup) to finish, so apt isn't locked
      ansible.builtin.command: cloud-init status --wait
      changed_when: false

    - name: Gather facts
      ansible.builtin.setup:

- name: Base setup for every server
  hosts: all
  become: true
  roles:
    - common
    - awscli

# k3s goes first: Jenkins needs its kubeconfig.
- name: Kubernetes (k3s) node
  hosts: k3s
  become: true
  roles:
    - k3s

- name: Jenkins server
  hosts: jenkins
  become: true
  roles:
    - docker
    - kubectl
    - jenkins

- name: Argo CD (optional, see install_argocd)
  hosts: k3s
  become: true
  roles:
    - role: argocd
      when: install_argocd | bool
```

</details>

<details>
<summary><code>ansible/group_vars/all.yml</code></summary>

```yaml
---
# Settings for every server. Server IPs, the region and the ECR registry come
# from inventory/hosts.ini, which Terraform writes.

project: marathon
app_namespace: marathon

# ------------------------------------------------------------------ versions
# Pinned on purpose, so every run installs exactly the same thing.
# Change them deliberately, then re-run and re-test.
k3s_version: v1.34.11+k3s1
kubectl_minor: "1.34"                    # kubectl on Jenkins, same minor as k3s
argocd_version: v3.5.3
jenkins_plugin_manager_version: "2.15.0"

# ------------------------------------------------------------------ Jenkins
# true: Jenkins starts fully configured (admin user, credentials, settings and
# the pipeline job) with no setup wizard. false: plain install with the wizard.
jenkins_configure_as_code: true

jenkins_admin_user: admin
# Never commit the password. Export it before running the playbook:
#   export JENKINS_ADMIN_PASSWORD='a-long-random-password'
jenkins_admin_password: "{{ lookup('ansible.builtin.env', 'JENKINS_ADMIN_PASSWORD') }}"

# The Git repository Jenkins builds (your fork of this project).
app_repo_url: "https://github.com/YOUR-GITHUB-USER/Demo-B17.git"
app_repo_branch: main

# Optional, for the GitOps (Argo CD) path: lets Jenkins push image-tag updates
# back to the repo. Leave empty to add the credential by hand later.
#   export GIT_PUSH_USER=your-github-user GIT_PUSH_TOKEN=github_pat_...
git_push_user: "{{ lookup('ansible.builtin.env', 'GIT_PUSH_USER') }}"
git_push_token: "{{ lookup('ansible.builtin.env', 'GIT_PUSH_TOKEN') }}"

# ------------------------------------------------------------------ Argo CD
# false: the learners install Argo CD themselves (PRACTICE-TASK.md, Part 6).
install_argocd: false
```

</details>

<details>
<summary><code>ansible/inventory/hosts.ini.example</code></summary>

```ini
# Example only. Terraform writes the real inventory/hosts.ini for you
# (terraform/infra/inventory.tf), with your servers' IPs filled in.

[jenkins]
jenkins-1 ansible_host=203.0.113.20 private_ip=10.20.1.10

[k3s]
k3s-1 ansible_host=203.0.113.30 private_ip=10.20.1.20

[all:vars]
ansible_user=ubuntu
ansible_ssh_private_key_file=~/.ssh/marathon
aws_region=ap-south-1
ecr_registry=123456789012.dkr.ecr.ap-south-1.amazonaws.com
jenkins_public_ip=203.0.113.20
k3s_public_ip=203.0.113.30
k3s_private_ip=10.20.1.20
```

</details>

<details>
<summary><code>ansible/roles/argocd/defaults/main.yml</code></summary>

```yaml
---
argocd_version: v3.5.3
```

</details>

<details>
<summary><code>ansible/roles/awscli/defaults/main.yml</code></summary>

```yaml
---
awscli_zip_url: https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip
```

</details>

<details>
<summary><code>ansible/roles/docker/defaults/main.yml</code></summary>

```yaml
---
# Images older than this are removed every night, so build images can't
# slowly fill the Jenkins disk.
docker_prune_older_than: 72h
```

</details>

<details>
<summary><code>ansible/roles/jenkins/defaults/main.yml</code></summary>

```yaml
---
jenkins_configure_as_code: true
jenkins_plugin_manager_version: "2.15.0"
jenkins_plugin_manager_url: >-
  {{ jenkins_plugin_manager_base }}/{{ jenkins_plugin_manager_version }}/jenkins-plugin-manager-{{ jenkins_plugin_manager_version }}.jar
jenkins_plugin_manager_base: https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download
jenkins_home: /var/lib/jenkins
jenkins_casc_secrets_dir: "{{ jenkins_home }}/casc-secrets"
# Plugins the Jenkinsfile needs, plus Configuration as Code and Job DSL.
# The plugin manager pulls in each plugin's dependencies automatically.
jenkins_plugins:
  - configuration-as-code
  - job-dsl
  - workflow-aggregator
  - git
  - credentials-binding
  - plain-credentials
  - junit
  - timestamper
  - pipeline-stage-view
```

</details>

<details>
<summary><code>ansible/roles/k3s/defaults/main.yml</code></summary>

```yaml
---
k3s_version: v1.34.11+k3s1
# Where the kubeconfigs are saved on YOUR machine (the Ansible controller).
k3s_artifacts_dir: "{{ playbook_dir }}/artifacts"
# The ECR pull secret is refreshed this often (ECR tokens last 12 hours).
k3s_ecr_refresh_every: 6h
```

</details>

<details>
<summary><code>ansible/roles/kubectl/defaults/main.yml</code></summary>

```yaml
---
kubectl_minor: "1.34"
```

</details>

<details>
<summary><code>ansible/roles/argocd/tasks/main.yml</code></summary>

```yaml
---
# Optional: installs Argo CD for the instructor. Learners do these steps by
# hand in PRACTICE-TASK.md, Part 6.
- name: Create the argocd namespace
  ansible.builtin.shell: |
    set -o pipefail
    k3s kubectl create namespace argocd --dry-run=client -o yaml | k3s kubectl apply -f -
  args:
    executable: /bin/bash
  register: argocd_ns
  changed_when: "'created' in argocd_ns.stdout"

- name: Install Argo CD {{ argocd_version }}
  # --server-side: some Argo CD CRDs are too large for client-side apply.
  ansible.builtin.command: >-
    k3s kubectl apply -n argocd --server-side --force-conflicts
    -f https://raw.githubusercontent.com/argoproj/argo-cd/{{ argocd_version }}/manifests/install.yaml
  register: argocd_install
  changed_when: "'serverside-applied' in argocd_install.stdout and 'unchanged' not in argocd_install.stdout"

- name: Wait for the Argo CD server
  ansible.builtin.command: k3s kubectl -n argocd rollout status deployment/argocd-server --timeout=300s
  changed_when: false

- name: Show how to open Argo CD
  ansible.builtin.debug:
    msg:
      - "Argo CD {{ argocd_version }} is installed."
      - "Password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
      - "UI: kubectl -n argocd port-forward svc/argocd-server 8443:443, then https://localhost:8443 (user: admin)"
```

</details>

<details>
<summary><code>ansible/roles/awscli/tasks/main.yml</code></summary>

```yaml
---
# AWS CLI v2 (the Ubuntu package is the old v1). Used for ECR logins; it
# authenticates with the server's IAM role, so no keys are configured.
- name: Check whether the AWS CLI is installed
  ansible.builtin.stat:
    path: /usr/local/bin/aws
  register: awscli_binary

- name: Install AWS CLI v2
  when: not awscli_binary.stat.exists
  block:
    - name: Download and unpack the installer
      ansible.builtin.unarchive:
        src: "{{ awscli_zip_url }}"
        dest: /tmp
        remote_src: true
        creates: /tmp/aws/install

    - name: Run the installer
      ansible.builtin.command: /tmp/aws/install
      args:
        creates: /usr/local/bin/aws

    - name: Remove the installer files
      ansible.builtin.file:
        path: /tmp/aws
        state: absent
```

</details>

<details>
<summary><code>ansible/roles/common/tasks/main.yml</code></summary>

```yaml
---
- name: Set a readable hostname
  ansible.builtin.hostname:
    name: "{{ project }}-{{ inventory_hostname }}"

- name: Install base packages
  ansible.builtin.apt:
    name:
      - ca-certificates
      - curl
      - git
      - gnupg
      - jq
      - unzip
      - unattended-upgrades   # security patches install themselves
    state: present
    update_cache: true
    cache_valid_time: 3600
```

</details>

<details>
<summary><code>ansible/roles/docker/tasks/main.yml</code></summary>

```yaml
---
- name: Install Docker (Ubuntu packages)
  ansible.builtin.apt:
    name:
      - docker.io
      - docker-buildx
    state: present

- name: Limit container log size (a chatty container can't fill the disk)
  ansible.builtin.copy:
    dest: /etc/docker/daemon.json
    mode: "0644"
    content: |
      {
        "log-driver": "json-file",
        "log-opts": { "max-size": "10m", "max-file": "3" }
      }
  notify: Restart docker

- name: Start Docker now and at boot
  ansible.builtin.systemd_service:
    name: docker
    state: started
    enabled: true

- name: Install the nightly image clean-up service
  ansible.builtin.copy:
    dest: /etc/systemd/system/docker-prune.service
    mode: "0644"
    content: |
      [Unit]
      Description=Remove unused Docker images older than {{ docker_prune_older_than }}
      [Service]
      Type=oneshot
      ExecStart=/usr/bin/docker system prune --all --force --filter until={{ docker_prune_older_than }}

- name: Install the nightly image clean-up timer
  ansible.builtin.copy:
    dest: /etc/systemd/system/docker-prune.timer
    mode: "0644"
    content: |
      [Unit]
      Description=Nightly Docker clean-up
      [Timer]
      OnCalendar=*-*-* 03:00:00
      Persistent=true
      [Install]
      WantedBy=timers.target

- name: Enable the clean-up timer
  ansible.builtin.systemd_service:
    name: docker-prune.timer
    state: started
    enabled: true
    daemon_reload: true
```

</details>

<details>
<summary><code>ansible/roles/jenkins/tasks/main.yml</code></summary>

```yaml
---
- name: Check the admin password was provided
  ansible.builtin.assert:
    that:
      - jenkins_admin_password | length >= 12
    fail_msg: >-
      Set a Jenkins admin password of at least 12 characters before running:
      export JENKINS_ADMIN_PASSWORD='...'
    quiet: true
  when: jenkins_configure_as_code | bool
  no_log: true

- name: Warn if the repository URL is still the placeholder
  ansible.builtin.debug:
    msg: "app_repo_url is still the example value. Set it to your fork in group_vars/all.yml, or the pipeline job won't find the code."
  when: "'YOUR-GITHUB-USER' in app_repo_url"

# ------------------------------------------------------------ install
- name: Install Java 21 (Jenkins requires Java 21+)
  ansible.builtin.apt:
    name:
      - fontconfig
      - openjdk-21-jre-headless
    state: present

- name: Add the Jenkins package signing key (the 2026 key; older tutorials use a retired one)
  ansible.builtin.get_url:
    url: https://pkg.jenkins.io/debian-stable/jenkins.io-2026.key
    dest: /etc/apt/keyrings/jenkins-keyring.asc
    mode: "0644"

- name: Add the Jenkins LTS repository
  ansible.builtin.apt_repository:
    repo: "deb [signed-by=/etc/apt/keyrings/jenkins-keyring.asc] https://pkg.jenkins.io/debian-stable binary/"
    filename: jenkins
    state: present

- name: Install Jenkins LTS
  ansible.builtin.apt:
    name: jenkins
    state: present
    update_cache: true

- name: Let the jenkins user run Docker (build, scan and test stages)
  ansible.builtin.user:
    name: jenkins
    groups: docker
    append: true
  notify: Restart jenkins

- name: Create the systemd override directory
  ansible.builtin.file:
    path: /etc/systemd/system/jenkins.service.d
    state: directory
    mode: "0755"

- name: Write the Jenkins service override
  ansible.builtin.template:
    src: override.conf.j2
    dest: /etc/systemd/system/jenkins.service.d/override.conf
    mode: "0644"
  notify: Restart jenkins

# ------------------------------------------------------------ configuration as code
- name: Configuration as Code
  when: jenkins_configure_as_code | bool
  block:
    - name: Download the Jenkins plugin manager {{ jenkins_plugin_manager_version }}
      ansible.builtin.get_url:
        url: "{{ jenkins_plugin_manager_url }}"
        dest: /opt/jenkins-plugin-manager.jar
        mode: "0644"

    - name: Install plugins and their dependencies
      # Each plugin must be its own argument after --plugins.
      ansible.builtin.command:
        argv: "{{ ['java', '-jar', '/opt/jenkins-plugin-manager.jar',
                   '--war', '/usr/share/java/jenkins.war',
                   '--plugin-download-directory', jenkins_home ~ '/plugins',
                   '--plugins'] + jenkins_plugins }}"
        creates: "{{ jenkins_home }}/plugins/configuration-as-code.jpi"
      become: true
      become_user: jenkins
      notify: Restart jenkins

    - name: Create the secrets folder (readable by jenkins only)
      ansible.builtin.file:
        path: "{{ jenkins_casc_secrets_dir }}"
        state: directory
        owner: jenkins
        group: jenkins
        mode: "0700"

    - name: Store the admin password
      ansible.builtin.copy:
        dest: "{{ jenkins_casc_secrets_dir }}/admin-password"
        content: "{{ jenkins_admin_password }}"
        owner: jenkins
        group: jenkins
        mode: "0400"
      no_log: true
      notify: Restart jenkins

    - name: Store the k3s kubeconfig (fetched by the k3s role)
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/artifacts/kubeconfig-jenkins.yaml"
        dest: "{{ jenkins_casc_secrets_dir }}/k3s-kubeconfig"
        owner: jenkins
        group: jenkins
        mode: "0400"
      no_log: true
      notify: Restart jenkins

    - name: Store the Git push token (GitOps path)
      ansible.builtin.copy:
        dest: "{{ jenkins_casc_secrets_dir }}/git-push-token"
        content: "{{ git_push_token }}"
        owner: jenkins
        group: jenkins
        mode: "0400"
      when: git_push_user | length > 0 and git_push_token | length > 0
      no_log: true
      notify: Restart jenkins

    - name: Write the Configuration as Code file
      ansible.builtin.template:
        src: casc.yaml.j2
        dest: "{{ jenkins_home }}/casc.yaml"
        owner: jenkins
        group: jenkins
        mode: "0600"
      notify: Restart jenkins

# ------------------------------------------------------------ start and check
- name: Apply pending restarts now
  ansible.builtin.meta: flush_handlers

- name: Start Jenkins now and at boot
  ansible.builtin.systemd_service:
    name: jenkins
    state: started
    enabled: true

- name: Wait for Jenkins to answer
  ansible.builtin.uri:
    url: http://localhost:8080/login
    status_code: 200
  register: jenkins_up
  until: jenkins_up.status == 200
  retries: 40
  delay: 5

- name: Read the setup-wizard password (only without Configuration as Code)
  ansible.builtin.slurp:
    src: "{{ jenkins_home }}/secrets/initialAdminPassword"
  register: jenkins_initial_password
  when: not (jenkins_configure_as_code | bool)

- name: Show how to sign in
  ansible.builtin.debug:
    msg: >-
      {{ 'Jenkins is ready at http://' ~ jenkins_public_ip ~ ':8080 . Sign in as '
         ~ jenkins_admin_user ~ ' with JENKINS_ADMIN_PASSWORD. The pipeline job "marathon" is already created.'
         if (jenkins_configure_as_code | bool) else
         'Jenkins is at http://' ~ jenkins_public_ip ~ ':8080 . Setup-wizard password: '
         ~ (jenkins_initial_password.content | b64decode | trim) }}
```

</details>

<details>
<summary><code>ansible/roles/k3s/tasks/main.yml</code></summary>

```yaml
---
- name: Create the k3s config directory
  ansible.builtin.file:
    path: /etc/rancher/k3s
    state: directory
    mode: "0755"

- name: Write the k3s server config
  ansible.builtin.template:
    src: config.yaml.j2
    dest: /etc/rancher/k3s/config.yaml
    mode: "0600"
  notify: Restart k3s

- name: Download the k3s install script
  ansible.builtin.get_url:
    url: https://get.k3s.io
    dest: /usr/local/bin/k3s-install.sh
    mode: "0700"

- name: Install k3s {{ k3s_version }}
  ansible.builtin.command: /usr/local/bin/k3s-install.sh
  args:
    creates: /usr/local/bin/k3s
  environment:
    INSTALL_K3S_VERSION: "{{ k3s_version }}"

# A config change on an existing install needs a restart before the checks below.
- name: Apply a pending k3s restart now
  ansible.builtin.meta: flush_handlers

- name: Wait for the node to be Ready
  ansible.builtin.command: >-
    k3s kubectl get node {{ inventory_hostname }}
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
  register: k3s_node_ready
  until: k3s_node_ready.stdout == "True"
  retries: 30
  delay: 5
  changed_when: false

- name: Wait for Traefik (the Ingress controller) to be running
  ansible.builtin.command: k3s kubectl -n kube-system rollout status deployment/traefik --timeout=30s
  register: k3s_traefik
  until: k3s_traefik.rc == 0
  retries: 20
  delay: 10
  changed_when: false

# ------------------------------------------------------------ ECR pull secret
- name: Install the ECR pull-secret refresh script
  ansible.builtin.template:
    src: ecr-pull-secret-refresh.sh.j2
    dest: /usr/local/bin/ecr-pull-secret-refresh.sh
    mode: "0700"

- name: Install the refresh service
  ansible.builtin.copy:
    dest: /etc/systemd/system/ecr-pull-secret-refresh.service
    mode: "0644"
    content: |
      [Unit]
      Description=Refresh the ECR image pull secret in Kubernetes
      After=k3s.service network-online.target
      Wants=network-online.target
      [Service]
      Type=oneshot
      ExecStart=/usr/local/bin/ecr-pull-secret-refresh.sh

- name: Install the refresh timer
  ansible.builtin.copy:
    dest: /etc/systemd/system/ecr-pull-secret-refresh.timer
    mode: "0644"
    content: |
      [Unit]
      Description=Refresh the ECR image pull secret every {{ k3s_ecr_refresh_every }}
      [Timer]
      OnBootSec=2min
      OnUnitActiveSec={{ k3s_ecr_refresh_every }}
      Persistent=true
      [Install]
      WantedBy=timers.target

- name: Enable the refresh timer
  ansible.builtin.systemd_service:
    name: ecr-pull-secret-refresh.timer
    state: started
    enabled: true
    daemon_reload: true

- name: Create the pull secret now
  ansible.builtin.command: /usr/local/bin/ecr-pull-secret-refresh.sh
  changed_when: false

# ------------------------------------------------------------ kubeconfigs
- name: Read the admin kubeconfig
  ansible.builtin.slurp:
    src: /etc/rancher/k3s/k3s.yaml
  register: k3s_kubeconfig
  no_log: true

- name: Create the local artifacts folder
  ansible.builtin.file:
    path: "{{ k3s_artifacts_dir }}"
    state: directory
    mode: "0700"
  delegate_to: localhost
  become: false

# Two copies: the Jenkins server reaches k3s over the private network, your
# laptop over the public IP (port 6443 is open only to admin_cidr).
- name: Save the kubeconfig for Jenkins (private IP)
  ansible.builtin.copy:
    dest: "{{ k3s_artifacts_dir }}/kubeconfig-jenkins.yaml"
    content: "{{ k3s_kubeconfig.content | b64decode | replace('https://127.0.0.1:6443', 'https://' ~ private_ip ~ ':6443') }}"
    mode: "0600"
  delegate_to: localhost
  become: false
  no_log: true

- name: Save the kubeconfig for your laptop (public IP)
  ansible.builtin.copy:
    dest: "{{ k3s_artifacts_dir }}/kubeconfig-admin.yaml"
    content: "{{ k3s_kubeconfig.content | b64decode | replace('https://127.0.0.1:6443', 'https://' ~ k3s_public_ip ~ ':6443') }}"
    mode: "0600"
  delegate_to: localhost
  become: false
  no_log: true

- name: Show how to use kubectl from your laptop
  ansible.builtin.debug:
    msg:
      - "k3s {{ k3s_version }} is ready."
      - "From your laptop: export KUBECONFIG={{ k3s_artifacts_dir }}/kubeconfig-admin.yaml && kubectl get nodes"
```

</details>

<details>
<summary><code>ansible/roles/kubectl/tasks/main.yml</code></summary>

```yaml
---
# kubectl from the official Kubernetes package repository (signed packages).
- name: Add the Kubernetes package signing key
  ansible.builtin.get_url:
    url: "https://pkgs.k8s.io/core:/stable:/v{{ kubectl_minor }}/deb/Release.key"
    dest: /etc/apt/keyrings/kubernetes-apt-keyring.asc
    mode: "0644"

- name: Add the Kubernetes package repository
  ansible.builtin.apt_repository:
    repo: "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.asc] https://pkgs.k8s.io/core:/stable:/v{{ kubectl_minor }}/deb/ /"
    filename: kubernetes
    state: present

- name: Install kubectl
  ansible.builtin.apt:
    name: kubectl
    state: present
    update_cache: true
```

</details>

<details>
<summary><code>ansible/roles/docker/handlers/main.yml</code></summary>

```yaml
---
- name: Restart docker
  ansible.builtin.systemd_service:
    name: docker
    state: restarted
```

</details>

<details>
<summary><code>ansible/roles/jenkins/handlers/main.yml</code></summary>

```yaml
---
- name: Restart jenkins
  ansible.builtin.systemd_service:
    name: jenkins
    state: restarted
    daemon_reload: true
```

</details>

<details>
<summary><code>ansible/roles/k3s/handlers/main.yml</code></summary>

```yaml
---
- name: Restart k3s
  ansible.builtin.systemd_service:
    name: k3s
    state: restarted
```

</details>

<details>
<summary><code>ansible/roles/jenkins/templates/casc.yaml.j2</code></summary>

```jinja
# Jenkins Configuration as Code, written by Ansible (roles/jenkins).
# Jenkins reads this at every start. Edit the template and re-run Ansible
# instead of clicking in the UI, so the setup is repeatable.
#
# Secrets are NOT in this file: ${readFile:...} and ${readFileBase64:...}
# read them at start-up from {{ jenkins_casc_secrets_dir }} (readable by jenkins only).

jenkins:
  systemMessage: "DevOps Marathon CI/CD. Configured by Ansible and Configuration as Code: change the code, not the UI."
  numExecutors: 2
  mode: NORMAL
  securityRealm:
    local:
      allowsSignup: false
      users:
        - id: "{{ jenkins_admin_user }}"
          password: "${readFile:{{ jenkins_casc_secrets_dir }}/admin-password}"
  authorizationStrategy:
    loggedInUsersCanDoAnything:
      allowAnonymousRead: false
  globalNodeProperties:
    - envVars:
        env:
          - key: ECR_REGISTRY
            value: "{{ ecr_registry }}"
          - key: AWS_REGION
            value: "{{ aws_region }}"

unclassified:
  location:
    url: "http://{{ jenkins_public_ip }}:8080/"

credentials:
  system:
    domainCredentials:
      - credentials:
          - file:
              scope: GLOBAL
              id: k3s-kubeconfig
              description: "k3s kubeconfig (private IP), written by Ansible"
              fileName: kubeconfig
              secretBytes: "${readFileBase64:{{ jenkins_casc_secrets_dir }}/k3s-kubeconfig}"
{% if git_push_user and git_push_token %}
          - usernamePassword:
              scope: GLOBAL
              id: git-push
              description: "GitHub token used by the GitOps stage to push image tags"
              username: "{{ git_push_user }}"
              password: "${readFile:{{ jenkins_casc_secrets_dir }}/git-push-token}"
{% endif %}

jobs:
  - script: |
      pipelineJob('marathon') {
        description('Tests, builds, scans and deploys the Doubt Wall and AIOps Triage. See JENKINS.md.')
        definition {
          cpsScm {
            scm {
              git {
                remote { url('{{ app_repo_url }}') }
                branches('*/{{ app_repo_branch }}')
              }
            }
            scriptPath('Jenkinsfile')
            lightweight(true)
          }
        }
      }
```

</details>

<details>
<summary><code>ansible/roles/jenkins/templates/override.conf.j2</code></summary>

```jinja
# Written by Ansible (roles/jenkins).
[Service]
{% if jenkins_configure_as_code %}
# No setup wizard: everything comes from Configuration as Code (casc.yaml).
Environment="JAVA_OPTS=-Djava.awt.headless=true -Djenkins.install.runSetupWizard=false"
Environment="CASC_JENKINS_CONFIG={{ jenkins_home }}/casc.yaml"
{% else %}
Environment="JAVA_OPTS=-Djava.awt.headless=true"
{% endif %}
```

</details>

<details>
<summary><code>ansible/roles/k3s/templates/config.yaml.j2</code></summary>

```jinja
# k3s server settings, written by Ansible (roles/k3s).
node-name: {{ inventory_hostname }}

# Only root can read the admin kubeconfig on the server.
write-kubeconfig-mode: "0600"

# Extra names in the API server's TLS certificate, so kubectl works through
# the public IP (your laptop) and the private IP (Jenkins).
tls-san:
  - {{ k3s_public_ip }}
  - {{ private_ip }}

# Encrypt Kubernetes Secrets at rest in the k3s datastore.
secrets-encryption: true
```

</details>

<details>
<summary><code>ansible/roles/k3s/templates/ecr-pull-secret-refresh.sh.j2</code></summary>

```jinja
#!/bin/bash
# Keeps the "ecr-pull" image pull secret fresh in the {{ app_namespace }} namespace.
# ECR login tokens expire after 12 hours; a systemd timer runs this every
# {{ k3s_ecr_refresh_every }}. It uses this server's IAM role (no stored keys), so the
# cluster can pull images even when nobody has run Jenkins for a while
# (needed for Argo CD / GitOps, where Jenkins never talks to the cluster).
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
REGION="{{ aws_region }}"
REGISTRY="{{ ecr_registry }}"
NS="{{ app_namespace }}"

work="$(mktemp -d)"
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT

token="$(/usr/local/bin/aws ecr get-login-password --region "$REGION")"
auth="$(printf 'AWS:%s' "$token" | base64 -w0)"
printf '{"auths":{"%s":{"auth":"%s"}}}' "$REGISTRY" "$auth" > "$work/config.json"

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NS" create secret generic ecr-pull \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson="$work/config.json" \
  --dry-run=client -o yaml | kubectl apply -f -
```

</details>


### Kubernetes manifests

<details>
<summary><code>k8s/create-secrets.sh</code></summary>

```bash
#!/usr/bin/env bash
# Creates the "marathon-secrets" Secret that both apps and PostgreSQL read.
# Run ONCE per cluster, before the first deploy:
#
#   ./k8s/create-secrets.sh
#   ANTHROPIC_API_KEY=sk-ant-... ./k8s/create-secrets.sh     # to enable Claude mode
#
# Any value you don't pass is generated randomly. Secrets never go into git,
# the images or the Jenkinsfile.
set -euo pipefail

NS="${NS:-marathon}"
NAME="marathon-secrets"

command -v kubectl >/dev/null || { echo "kubectl is not installed or not on PATH." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is needed to generate passwords." >&2; exit 1; }

if kubectl -n "$NS" get secret "$NAME" >/dev/null 2>&1 && [ "${FORCE:-}" != "1" ]; then
  cat >&2 <<MSG
Secret $NS/$NAME already exists, so nothing was changed.

Replacing it would give the apps new database passwords that PostgreSQL
doesn't know (PostgreSQL reads them only when its volume is first created).
To replace it anyway, run with FORCE=1 and also reset the database:
  kubectl -n $NS delete statefulset postgres && kubectl -n $NS delete pvc data-postgres-0
MSG
  exit 1
fi

gen() { openssl rand -hex 24; }
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(gen)}"
DB_PASSWORD="${DB_PASSWORD:-$(gen)}"
ADMIN_TOKEN="${ADMIN_TOKEN:-$(gen)}"
WEBHOOK_TOKEN="${WEBHOOK_TOKEN:-$(gen)}"
SECRET_KEY="${SECRET_KEY:-$(gen)}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

kubectl -n "$NS" create secret generic "$NAME" \
  --from-literal=postgres-password="$POSTGRES_PASSWORD" \
  --from-literal=db-password="$DB_PASSWORD" \
  --from-literal=admin-token="$ADMIN_TOKEN" \
  --from-literal=webhook-token="$WEBHOOK_TOKEN" \
  --from-literal=secret-key="$SECRET_KEY" \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

cat <<MSG

Secret $NS/$NAME is ready.

Save these two in a password manager. You need them during the session:
  ADMIN_TOKEN   = $ADMIN_TOKEN     (console sign-in at /ops, Doubt Wall moderation)
  WEBHOOK_TOKEN = $WEBHOOK_TOKEN   (Alertmanager / SNS webhook URLs)

Read any value again later with:
  kubectl -n $NS get secret $NAME -o jsonpath='{.data.admin-token}' | base64 -d; echo
MSG
```

</details>

<details>
<summary><code>k8s/base/kustomization.yaml</code></summary>

```yaml
# Shared manifests for both apps and PostgreSQL. Don't apply this directly:
# use an overlay, which sets the image names and tags.
#   kubectl apply -k k8s/overlays/aws     (images from ECR, used by Jenkins)
#   kubectl apply -k k8s/overlays/local   (images built locally)
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: marathon

labels:
  - pairs:
      app.kubernetes.io/part-of: devops-marathon
    includeSelectors: false

resources:
  - namespace.yaml
  - postgres/statefulset.yaml
  - postgres/service.yaml
  - postgres/networkpolicy.yaml
  - doubt-wall/deployment.yaml
  - doubt-wall/service.yaml
  - doubt-wall/hpa.yaml
  - doubt-wall/pdb.yaml
  - aiops-triage/rbac.yaml
  - aiops-triage/deployment.yaml
  - aiops-triage/service.yaml
  - ingress.yaml

# Non-secret settings. Kustomize adds a content hash to each ConfigMap name,
# so changing a value here rolls the pods that use it automatically.
# Secrets are NOT here: create them with k8s/create-secrets.sh.
configMapGenerator:
  - name: doubt-wall-config
    literals:
      - DB_HOST=postgres
      - DB_PORT=5432
      - DB_NAME=doubtwall
      - DB_USER=doubtwall
      - DB_POOL_MAX=10
      - SESSION_TITLE=DevOps Marathon
      - BANNER_TEXT=Welcome to the DevOps marathon. Ask anything.
      - BANNER_COLOR=#1F4E79
      - CHAOS_ENABLED=true
      - TRUST_PROXY=true
      - APP_VERSION=dev
  - name: aiops-triage-config
    literals:
      - URL_PREFIX=/ops
      - LLM_MODE=mock
      - CLAUDE_MODEL=claude-haiku-4-5
      - MAX_TOKENS=700
      - CHAOS_ENABLED=true
      - CHAOS_TARGET_URL=http://doubt-wall
      - CHAOS_TARGET_NAME=Doubt Wall
      - CHAOS_TARGET_PODS=doubt-wall-pods:8000
      - K8S_CONTEXT_ENABLED=true
      - APP_VERSION=dev
  - name: postgres-init
    files:
      - postgres/init-app-role.sh
```

</details>

<details>
<summary><code>k8s/base/namespace.yaml</code></summary>

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: marathon
  labels:
    # Pod Security Admission. "baseline" is enforced because the official
    # PostgreSQL image starts as root before dropping to the postgres user.
    # Both apps also meet "restricted"; warnings show if that ever changes.
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/warn: restricted
    pod-security.kubernetes.io/audit: restricted
```

</details>

<details>
<summary><code>k8s/base/ingress.yaml</code></summary>

```yaml
# One public entry point (Traefik, bundled with k3s):
#   http://<node-ip>/      -> Doubt Wall   (learners)
#   http://<node-ip>/ops   -> AIOps Triage (instructor; sign-in required)
# AIOps Triage handles the /ops prefix itself (URL_PREFIX), so no rewrite.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: marathon
spec:
  ingressClassName: traefik
  rules:
    - http:
        paths:
          - path: /ops
            pathType: Prefix
            backend:
              service:
                name: aiops-triage
                port:
                  name: http
          - path: /
            pathType: Prefix
            backend:
              service:
                name: doubt-wall
                port:
                  name: http
```

</details>

<details>
<summary><code>k8s/base/postgres/init-app-role.sh</code></summary>

```bash
#!/bin/sh
# Runs ONCE, the first time PostgreSQL starts on an empty data volume
# (the official image runs every script in /docker-entrypoint-initdb.d).
#
# Least privilege:
#   postgres  - superuser and owner of the database and the doubts table
#   doubtwall - the role the app logs in as: may read and write rows,
#               may NOT create, alter or drop anything
#
# Keep the table definition in step with SCHEMA in doubt-wall/app/db.py.
set -eu
: "${APP_DB_USER:=doubtwall}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" <<'EOSQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' CONNECTION LIMIT 60;

-- Guardrails applied to every session of the app role
ALTER ROLE :"app_user" SET statement_timeout = '5s';
ALTER ROLE :"app_user" SET idle_in_transaction_session_timeout = '30s';

-- Nobody but the owner can create objects in the public schema
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app_user";

CREATE TABLE doubts (
    id         SERIAL PRIMARY KEY,
    nickname   TEXT        NOT NULL CHECK (char_length(nickname) BETWEEN 1 AND 100),
    question   TEXT        NOT NULL CHECK (char_length(question) BETWEEN 1 AND 2000),
    upvotes    INT         NOT NULL DEFAULT 0,
    answered   BOOLEAN     NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX doubts_created_at_idx ON doubts (created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON doubts TO :"app_user";
GRANT USAGE, SELECT ON SEQUENCE doubts_id_seq TO :"app_user";
EOSQL
echo "init-app-role: created role $APP_DB_USER and table doubts"
```

</details>

<details>
<summary><code>k8s/base/postgres/networkpolicy.yaml</code></summary>

```yaml
# Only Doubt Wall pods may open a connection to PostgreSQL.
# (kubectl exec for psql demos is not network traffic, so it still works.)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: postgres-allow-doubt-wall
spec:
  podSelector:
    matchLabels:
      app: postgres
  policyTypes: ["Ingress"]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: doubt-wall
      ports:
        - protocol: TCP
          port: 5432
```

</details>

<details>
<summary><code>k8s/base/postgres/service.yaml</code></summary>

```yaml
# Headless Service: gives the StatefulSet pod a stable DNS name ("postgres").
# ClusterIP: None also means it's reachable only inside the cluster.
apiVersion: v1
kind: Service
metadata:
  name: postgres
  labels:
    app: postgres
spec:
  clusterIP: None
  selector:
    app: postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: postgres
```

</details>

<details>
<summary><code>k8s/base/postgres/statefulset.yaml</code></summary>

```yaml
# One PostgreSQL pod with its data on a PersistentVolume (k3s: local-path).
# Deleting the pod keeps the data; deleting the PVC deletes it.
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  labels:
    app: postgres
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 30
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: postgres
          image: postgres:18-alpine
          ports:
            - name: postgres
              containerPort: 5432
          env:
            - name: POSTGRES_USER
              value: postgres
            - name: POSTGRES_DB
              value: doubtwall
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: postgres-password
            - name: APP_DB_USER
              value: doubtwall
            - name: APP_DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: db-password
          volumeMounts:
            # PostgreSQL 18 images keep data in /var/lib/postgresql/18/docker,
            # so the volume is mounted at the parent directory.
            - name: data
              mountPath: /var/lib/postgresql
            - name: init
              mountPath: /docker-entrypoint-initdb.d
              readOnly: true
          readinessProbe:
            exec:
              command: ["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "doubtwall"]
            periodSeconds: 5
            timeoutSeconds: 3
          livenessProbe:
            exec:
              command: ["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "doubtwall"]
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 6
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            # The image starts as root only to fix file ownership, then drops
            # to the postgres user; it never needs to gain privileges.
            allowPrivilegeEscalation: false
      volumes:
        - name: init
          configMap:
            name: postgres-init
            defaultMode: 0555
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 2Gi
```

</details>

<details>
<summary><code>k8s/base/doubt-wall/deployment.yaml</code></summary>

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: doubt-wall
  labels:
    app: doubt-wall
spec:
  replicas: 2
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0   # never drop below the current number of ready pods
      maxSurge: 1
  selector:
    matchLabels:
      app: doubt-wall
  template:
    metadata:
      labels:
        app: doubt-wall
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: /metrics
    spec:
      automountServiceAccountToken: false   # this app never calls the Kubernetes API
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: doubt-wall
          image: doubt-wall          # set by the overlay (kustomize images)
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8000
          envFrom:
            - configMapRef:
                name: doubt-wall-config
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: db-password
            - name: ADMIN_TOKEN
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: admin-token
          # Liveness = "is the process alive?" (never checks the database).
          # Readiness = "can it serve now?" (checks the database and chaos).
          startupProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 2
            failureThreshold: 30
          livenessProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            periodSeconds: 5
            timeoutSeconds: 3
            failureThreshold: 2
          lifecycle:
            preStop:
              # Give the Service time to stop sending traffic before shutdown.
              exec:
                command: ["sleep", "5"]
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 64Mi
```

</details>

<details>
<summary><code>k8s/base/doubt-wall/hpa.yaml</code></summary>

```yaml
# Scale 2-5 pods on CPU. k3s ships metrics-server, which the HPA needs.
# 5 pods x DB_POOL_MAX 10 = 50 connections, under PostgreSQL's default 100.
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: doubt-wall
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: doubt-wall
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 120   # scale down after 2 calm minutes (demo-friendly)
```

</details>

<details>
<summary><code>k8s/base/doubt-wall/pdb.yaml</code></summary>

```yaml
# Keep at least one Doubt Wall pod during voluntary disruptions (node drain).
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: doubt-wall
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: doubt-wall
```

</details>

<details>
<summary><code>k8s/base/doubt-wall/service.yaml</code></summary>

```yaml
# Normal Service: load-balances learner traffic across ready pods.
apiVersion: v1
kind: Service
metadata:
  name: doubt-wall
  labels:
    app: doubt-wall
spec:
  selector:
    app: doubt-wall
  ports:
    - name: http
      port: 80
      targetPort: http
---
# Headless Service listing EVERY pod, including not-ready ones. AIOps Triage
# uses it to send each chaos switch to all replicas, and to reach pods whose
# readiness it switched off (otherwise "Stop all chaos" couldn't reach them).
apiVersion: v1
kind: Service
metadata:
  name: doubt-wall-pods
  labels:
    app: doubt-wall
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  selector:
    app: doubt-wall
  ports:
    - name: http
      port: 8000
      targetPort: http
```

</details>

<details>
<summary><code>k8s/base/aiops-triage/deployment.yaml</code></summary>

```yaml
# One replica on purpose: incidents are kept in memory (see the README's
# "teaching point"). A second replica would show a different incident list.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aiops-triage
  labels:
    app: aiops-triage
spec:
  replicas: 1
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      app: aiops-triage
  template:
    metadata:
      labels:
        app: aiops-triage
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: /metrics
    spec:
      serviceAccountName: aiops-triage
      automountServiceAccountToken: true   # needed to read pod logs (see rbac.yaml)
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: aiops-triage
          image: aiops-triage        # set by the overlay (kustomize images)
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8000
          envFrom:
            - configMapRef:
                name: aiops-triage-config
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: ADMIN_TOKEN
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: admin-token
            - name: WEBHOOK_TOKEN
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: webhook-token
            - name: SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: secret-key
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: marathon-secrets
                  key: anthropic-api-key
                  optional: true
          startupProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 2
            failureThreshold: 30
          livenessProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            periodSeconds: 5
            timeoutSeconds: 3
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 64Mi
```

</details>

<details>
<summary><code>k8s/base/aiops-triage/rbac.yaml</code></summary>

```yaml
# AIOps Triage can READ pods, pod logs and events in this namespace only,
# so it can add recent logs to an alert before diagnosing it.
# It cannot change, delete or exec into anything.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: aiops-triage
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: aiops-triage-read
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "events"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: aiops-triage-read
subjects:
  - kind: ServiceAccount
    name: aiops-triage
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: aiops-triage-read
```

</details>

<details>
<summary><code>k8s/base/aiops-triage/service.yaml</code></summary>

```yaml
apiVersion: v1
kind: Service
metadata:
  name: aiops-triage
  labels:
    app: aiops-triage
spec:
  selector:
    app: aiops-triage
  ports:
    - name: http
      port: 80
      targetPort: http
```

</details>

<details>
<summary><code>k8s/overlays/aws/kustomization.yaml</code></summary>

```yaml
# AWS overlay, used by Jenkins. Images come from Amazon ECR.
#
# Jenkins replaces the two placeholders in its own workspace copy before
# running `kubectl apply -k k8s/overlays/aws` (this file in git stays as is):
#   REPLACE_ECR_REGISTRY -> e.g. 123456789012.dkr.ecr.ap-south-1.amazonaws.com
#   REPLACE_IMAGE_TAG    -> e.g. 42-a1b2c3d (build number + git commit)
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base

images:
  - name: doubt-wall
    newName: REPLACE_ECR_REGISTRY/doubt-wall
    newTag: REPLACE_IMAGE_TAG
  - name: aiops-triage
    newName: REPLACE_ECR_REGISTRY/aiops-triage
    newTag: REPLACE_IMAGE_TAG

# Show the build tag in both apps' UI ("v42-a1b2c3d").
configMapGenerator:
  - name: doubt-wall-config
    behavior: merge
    literals:
      - APP_VERSION=REPLACE_IMAGE_TAG
  - name: aiops-triage-config
    behavior: merge
    literals:
      - APP_VERSION=REPLACE_IMAGE_TAG

# k3s nodes pull from the private ECR registry with this secret. Jenkins
# refreshes it on every deploy, because ECR login tokens expire after 12 hours.
patches:
  - target:
      kind: Deployment
    patch: |-
      - op: add
        path: /spec/template/spec/imagePullSecrets
        value:
          - name: ecr-pull
```

</details>

<details>
<summary><code>k8s/overlays/gitops/kustomization.yaml</code></summary>

```yaml
# GitOps overlay: Argo CD watches THIS file in git and deploys whatever it says.
#
# Nobody runs kubectl apply. To release a new version, the Jenkins GitOps stage
# (ci/gitops-update.sh) changes the image registry and tags below, commits and
# pushes; Argo CD sees the commit and rolls the cluster forward.
# The placeholders are filled in by the first GitOps pipeline run.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base

images:
  - name: doubt-wall
    newName: REPLACE_ECR_REGISTRY/doubt-wall
    newTag: REPLACE_IMAGE_TAG
  - name: aiops-triage
    newName: REPLACE_ECR_REGISTRY/aiops-triage
    newTag: REPLACE_IMAGE_TAG

configMapGenerator:
  - name: doubt-wall-config
    behavior: merge
    literals:
      - APP_VERSION=REPLACE_IMAGE_TAG
  - name: aiops-triage-config
    behavior: merge
    literals:
      - APP_VERSION=REPLACE_IMAGE_TAG

# The pull secret is kept fresh on the k3s node by a systemd timer
# (ansible/roles/k3s), because in GitOps Jenkins never talks to the cluster.
patches:
  - target:
      kind: Deployment
    patch: |-
      - op: add
        path: /spec/template/spec/imagePullSecrets
        value:
          - name: ecr-pull
```

</details>

<details>
<summary><code>k8s/overlays/local/kustomization.yaml</code></summary>

```yaml
# Local overlay: a cluster on your own machine (Docker Desktop Kubernetes,
# k3d, kind or minikube) using images you built with docker compose.
#
#   docker compose build                          # builds doubt-wall:local, aiops-triage:local
#   ./k8s/create-secrets.sh
#   kubectl apply -k k8s/overlays/local
#
# kind/minikube don't share Docker's images; load them first
# (kind load docker-image ..., minikube image load ...). See k8s/README.md.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base

images:
  - name: doubt-wall
    newTag: local
  - name: aiops-triage
    newTag: local

configMapGenerator:
  - name: doubt-wall-config
    behavior: merge
    literals:
      - APP_VERSION=local
  - name: aiops-triage-config
    behavior: merge
    literals:
      - APP_VERSION=local
```

</details>


### Jenkins pipeline

<details>
<summary><code>Jenkinsfile</code></summary>

```groovy
// CI/CD for the DevOps Marathon apps (Doubt Wall + AIOps Triage).
//
//   Prepare -> Test -> Build images -> Security scan -> Push to ECR
//           -> Deploy:  DEPLOY_MODE=kubectl  Jenkins applies to k3s (auto-rollback)
//                       DEPLOY_MODE=gitops   Jenkins commits the new tag to git,
//                                            Argo CD deploys it
//           -> Smoke test
//
// Each stage's logic lives in ci/*.sh, so you can read it, run it by hand,
// and explain it without reading Groovy. Setup steps are in JENKINS.md.

pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()          // two deploys at once would fight over the cluster
    timeout(time: 30, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  parameters {
    booleanParam(name: 'DEPLOY', defaultValue: true,
                 description: 'Deploy after a successful build')
    choice(name: 'DEPLOY_MODE', choices: ['kubectl', 'gitops'],
           description: 'kubectl: Jenkins deploys to k3s. gitops: Jenkins commits the new tag, Argo CD deploys.')
    booleanParam(name: 'FAIL_ON_CRITICAL_VULNS', defaultValue: true,
                 description: 'Stop if Trivy finds a CRITICAL vulnerability that has a fix')
    string(name: 'APP_URL', defaultValue: '',
           description: 'k3s node address for the smoke test, e.g. http://13.234.10.20 (empty = skip)')
  }

  environment {
    // ECR_REGISTRY is set once under Manage Jenkins > System > Global properties,
    // e.g. 123456789012.dkr.ecr.ap-south-1.amazonaws.com
    AWS_REGION = "${env.AWS_REGION ?: 'ap-south-1'}"
    // SKIP_PIPELINE is deliberately NOT declared here: values from this block
    // can't be changed later with env.X = ... in a script, and Prepare sets it.
  }

  stages {
    stage('Prepare') {
      steps {
        script {
          // The GitOps stage pushes a "[skip ci]" commit. If that commit
          // triggers this job (webhook or polling), stop here instead of looping.
          def lastMessage = sh(script: 'git log -1 --pretty=%B', returnStdout: true).trim()
          if (lastMessage.contains('[skip ci]')) {
            env.SKIP_PIPELINE = 'true'
            currentBuild.result = 'NOT_BUILT'
            currentBuild.description = 'GitOps commit from Jenkins: nothing to build'
            echo 'Latest commit is a GitOps tag update ([skip ci]). Skipping all stages.'
            return
          }
          if (!env.ECR_REGISTRY?.trim()) {
            error('ECR_REGISTRY is not set. Add it under Manage Jenkins > System > Global properties.')
          }
          env.GIT_SHORT = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
          env.IMAGE_TAG = "${env.BUILD_NUMBER}-${env.GIT_SHORT}"
          currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.IMAGE_TAG}"
        }
        script {
          if (env.SKIP_PIPELINE != 'true') {
            sh 'docker version --format "Docker {{.Server.Version}}" && aws --version'
          }
        }
      }
    }

    stage('Test') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      steps {
        sh 'bash ci/test.sh'
      }
      post {
        always {
          junit allowEmptyResults: true, testResults: 'reports/*.xml'
        }
      }
    }

    stage('Build images') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      steps {
        sh 'bash ci/build.sh'
      }
    }

    stage('Security scan') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      environment {
        TRIVY_FAIL = "${params.FAIL_ON_CRITICAL_VULNS}"
      }
      steps {
        sh 'bash ci/scan.sh'
      }
      post {
        always {
          archiveArtifacts artifacts: 'reports/trivy-*.txt', allowEmptyArchive: true
        }
      }
    }

    stage('Push to ECR') {
      when { not { environment name: 'SKIP_PIPELINE', value: 'true' } }
      steps {
        sh 'bash ci/push.sh'
      }
    }

    stage('Deploy to k3s') {
      when {
        allOf {
          not { environment name: 'SKIP_PIPELINE', value: 'true' }
          expression { params.DEPLOY && params.DEPLOY_MODE == 'kubectl' }
        }
      }
      steps {
        // A "Secret file" credential holding the k3s kubeconfig (see JENKINS.md).
        withCredentials([file(credentialsId: 'k3s-kubeconfig', variable: 'KUBECONFIG')]) {
          sh 'bash ci/deploy.sh'
        }
      }
    }

    stage('Update GitOps repo') {
      when {
        allOf {
          not { environment name: 'SKIP_PIPELINE', value: 'true' }
          expression { params.DEPLOY && params.DEPLOY_MODE == 'gitops' }
        }
      }
      steps {
        // A "Username with password" credential: GitHub user + a token that
        // can push to this repository (see PRACTICE-TASK.md, Part 6).
        withCredentials([usernamePassword(credentialsId: 'git-push',
                                          usernameVariable: 'GIT_USER',
                                          passwordVariable: 'GIT_TOKEN')]) {
          sh 'bash ci/gitops-update.sh'
        }
      }
    }

    stage('Smoke test') {
      when {
        allOf {
          not { environment name: 'SKIP_PIPELINE', value: 'true' }
          expression { params.DEPLOY }
          expression { params.APP_URL?.trim() }
        }
      }
      environment {
        // Argo CD needs time to notice the commit (it checks git every ~3 min).
        SMOKE_TIMEOUT = "${params.DEPLOY_MODE == 'gitops' ? '420' : '90'}"
      }
      steps {
        sh 'bash ci/smoke.sh'
      }
    }
  }

  post {
    always {
      sh 'docker image prune -f >/dev/null 2>&1 || true'
    }
    success {
      echo "Done: ${env.IMAGE_TAG}"
    }
    failure {
      echo 'Failed. Open the red stage above.'
      echo 'A failed rollout is rolled back automatically, so the previous version is still serving.'
    }
  }
}
```

</details>

<details>
<summary><code>ci/build.sh</code></summary>

```bash
#!/usr/bin/env bash
# Stage "Build images": one image per app, tagged <build>-<commit>.
set -euo pipefail
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
REGISTRY="${ECR_REGISTRY:-local}"

for app in doubt-wall aiops-triage; do
  echo "=== Building $REGISTRY/$app:$IMAGE_TAG ==="
  docker build --pull \
    --build-arg APP_VERSION="$IMAGE_TAG" \
    --build-arg GIT_COMMIT="${GIT_COMMIT:-unknown}" \
    -t "$REGISTRY/$app:$IMAGE_TAG" "$app"
done
```

</details>

<details>
<summary><code>ci/deploy.sh</code></summary>

```bash
#!/usr/bin/env bash
# Stage "Deploy to k3s": applies k8s/overlays/aws with this build's images,
# waits for the rollout, and rolls back automatically if it fails.
set -euo pipefail
: "${ECR_REGISTRY:?ECR_REGISTRY is not set}"
: "${AWS_REGION:?AWS_REGION is not set}"
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
NS="${NS:-marathon}"
TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"

kubectl -n "$NS" get secret marathon-secrets >/dev/null 2>&1 || {
  echo "Secret $NS/marathon-secrets not found. Run ./k8s/create-secrets.sh once against this cluster first." >&2
  exit 1
}

work="$(mktemp -d)"
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT

# 1. Refresh the image pull secret (ECR tokens expire after 12 hours).
#    The token goes through a private temp file, never on a command line,
#    so it can't be seen in the server's process list.
echo "Refreshing the ECR pull secret"
token="$(aws ecr get-login-password --region "$AWS_REGION")"
auth="$(printf 'AWS:%s' "$token" | base64 | tr -d '\n')"
printf '{"auths":{"%s":{"auth":"%s"}}}' "$ECR_REGISTRY" "$auth" > "$work/dockerconfig.json"
kubectl -n "$NS" create secret generic ecr-pull \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson="$work/dockerconfig.json" \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. Fill in the overlay's placeholders in a temporary copy (git stays clean).
cp -r k8s "$work/k8s"
overlay="$work/k8s/overlays/aws/kustomization.yaml"
sed -i "s|REPLACE_ECR_REGISTRY|$ECR_REGISTRY|g; s|REPLACE_IMAGE_TAG|$IMAGE_TAG|g" "$overlay"
if grep -q REPLACE_ "$overlay"; then
  echo "Placeholders left in the overlay; refusing to deploy." >&2
  exit 1
fi

# 3. Apply and wait.
echo "Deploying $IMAGE_TAG"
kubectl apply -k "$work/k8s/overlays/aws"
kubectl -n "$NS" rollout status statefulset/postgres --timeout="$TIMEOUT"

failed=""
for d in doubt-wall aiops-triage; do
  kubectl -n "$NS" rollout status "deployment/$d" --timeout="$TIMEOUT" || failed="$failed $d"
done

# 4. Roll back what didn't come up. With maxUnavailable: 0 the old pods kept
#    serving the whole time, so learners never saw the broken version.
if [ -n "$failed" ]; then
  echo "Rollout failed for:$failed. Rolling back." >&2
  kubectl -n "$NS" get pods -o wide >&2 || true
  kubectl -n "$NS" get events --sort-by=.lastTimestamp 2>/dev/null | tail -20 >&2 || true
  for d in $failed; do
    kubectl -n "$NS" rollout undo "deployment/$d" || echo "No previous revision of $d to roll back to." >&2
  done
  exit 1
fi

kubectl -n "$NS" get pods -o wide
echo "Deployed $IMAGE_TAG"
```

</details>

<details>
<summary><code>ci/gitops-update.sh</code></summary>

```bash
#!/usr/bin/env bash
# Stage "Update GitOps repo" (DEPLOY_MODE=gitops): instead of deploying,
# record the new image tag in git. Argo CD notices the commit and deploys it.
#
#   1. Start from the latest commit on the GitOps branch (not Jenkins' copy,
#      which may be behind if someone pushed meanwhile).
#   2. Change only k8s/overlays/gitops/kustomization.yaml.
#   3. Commit with "[skip ci]" so this commit doesn't trigger another build.
#   4. Push using a token from Jenkins credentials. The token is handed to git
#      through GIT_ASKPASS, never put in the URL, so it can't leak into logs
#      or .git/config.
set -euo pipefail
: "${ECR_REGISTRY:?ECR_REGISTRY is not set}"
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
: "${GIT_USER:?GIT_USER is not set (Jenkins credential git-push)}"
: "${GIT_TOKEN:?GIT_TOKEN is not set (Jenkins credential git-push)}"
BRANCH="${GITOPS_BRANCH:-main}"
FILE="k8s/overlays/gitops/kustomization.yaml"

work="$(mktemp -d)"
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT
cat > "$work/askpass.sh" <<'ASKPASS'
#!/bin/sh
case "$1" in
  Username*) printf '%s\n' "$GIT_USER" ;;
  *)         printf '%s\n' "$GIT_TOKEN" ;;
esac
ASKPASS
chmod 700 "$work/askpass.sh"
export GIT_ASKPASS="$work/askpass.sh" GIT_TERMINAL_PROMPT=0

git fetch --quiet origin "$BRANCH"
git checkout --quiet -B gitops-update "origin/$BRANCH"

# Update the registry, both image tags and the version shown in the UI.
# ("#" separates the sed parts, because "|" is used inside the pattern.)
sed -i -E \
  -e "s#(newName: )[^/]+/(doubt-wall|aiops-triage)\$#\1${ECR_REGISTRY}/\2#" \
  -e "s#(newTag: ).*#\1${IMAGE_TAG}#" \
  -e "s#(APP_VERSION=).*#\1${IMAGE_TAG}#" \
  "$FILE"

if grep -q REPLACE_ "$FILE"; then
  echo "Placeholders are still in $FILE; refusing to commit." >&2
  exit 1
fi

if git diff --quiet -- "$FILE"; then
  echo "$FILE already points at $IMAGE_TAG; nothing to commit."
  exit 0
fi

git -c user.name="Jenkins" -c user.email="jenkins@marathon.local" \
  commit --quiet -m "[skip ci] Deploy ${IMAGE_TAG}" -- "$FILE"
git push --quiet origin "HEAD:${BRANCH}"
echo "Pushed ${IMAGE_TAG} to ${BRANCH}. Argo CD will deploy it (within ~3 minutes, or press Refresh in the Argo CD UI)."
git --no-pager show --stat --oneline HEAD
```

</details>

<details>
<summary><code>ci/push.sh</code></summary>

```bash
#!/usr/bin/env bash
# Stage "Push to ECR". The Jenkins server authenticates with its EC2 instance
# role (created by Terraform), so there are no AWS keys in Jenkins.
# The ECR repositories "doubt-wall" and "aiops-triage" must already exist.
set -euo pipefail
: "${ECR_REGISTRY:?ECR_REGISTRY is not set}"
: "${AWS_REGION:?AWS_REGION is not set}"
: "${IMAGE_TAG:?IMAGE_TAG is not set}"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"
trap 'docker logout "$ECR_REGISTRY" >/dev/null 2>&1 || true' EXIT   # don't leave the token on disk

for app in doubt-wall aiops-triage; do
  echo "=== Pushing $ECR_REGISTRY/$app:$IMAGE_TAG ==="
  docker push "$ECR_REGISTRY/$app:$IMAGE_TAG"
done
```

</details>

<details>
<summary><code>ci/scan.sh</code></summary>

```bash
#!/usr/bin/env bash
# Stage "Security scan": checks each image for known vulnerabilities (Trivy).
#
# Two security choices worth explaining in class:
#  1. Trivy is PINNED. Versions 0.69.4-0.69.6, and the "latest" tag during
#     19-23 March 2026, were malicious builds that stole CI secrets
#     (advisory GHSA-69fq-xp46-6x23). 0.69.3 is the last known clean release.
#     Read the advisory before changing this version. Never use "latest".
#  2. Trivy never gets the Docker socket. The image is exported to a file and
#     scanned from there. Mounting /var/run/docker.sock would give the scanner
#     full control of the build server.
set -euo pipefail
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
REGISTRY="${ECR_REGISTRY:-local}"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:0.69.3}"
SEVERITY="${TRIVY_SEVERITY:-CRITICAL}"
EXIT_CODE=1
[ "${TRIVY_FAIL:-true}" = "true" ] || EXIT_CODE=0

mkdir -p reports .trivy-cache
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

status=0
for app in doubt-wall aiops-triage; do
  ref="$REGISTRY/$app:$IMAGE_TAG"
  echo "=== Scanning $ref (severity: $SEVERITY, unfixed ignored) ==="
  docker save "$ref" -o "$tmp/$app.tar"
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$tmp:/scan:ro" -v "$PWD/.trivy-cache:/cache" -v "$PWD/reports:/reports" \
    "$TRIVY_IMAGE" image \
      --cache-dir /cache --input "/scan/$app.tar" \
      --severity "$SEVERITY" --ignore-unfixed --no-progress \
      --exit-code "$EXIT_CODE" --format table --output "/reports/trivy-$app.txt" \
    || status=1
  cat "reports/trivy-$app.txt" 2>/dev/null || true
done

if [ "$status" -ne 0 ]; then
  echo "Trivy found fixable $SEVERITY vulnerabilities (or failed to run). See reports/trivy-*.txt." >&2
fi
exit "$status"
```

</details>

<details>
<summary><code>ci/smoke.sh</code></summary>

```bash
#!/usr/bin/env bash
# Stage "Smoke test": checks the deployed apps through the real Ingress.
set -euo pipefail
: "${APP_URL:?Set APP_URL to the k3s node address, e.g. http://13.234.10.20}"
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
url="${APP_URL%/}"

get() {
  curl -fsS --max-time 5 --retry 12 --retry-delay 5 --retry-all-errors "$1"
}

echo "Doubt Wall ready:    $(get "$url/ready")"
echo "AIOps Triage health: $(get "$url/ops/health")"

# Wait until the NEW version is the one answering. With kubectl this is
# immediate; with Argo CD it can take a few minutes after the commit.
deadline=$(( $(date +%s) + ${SMOKE_TIMEOUT:-90} ))
while :; do
  info="$(curl -fsS --max-time 5 "$url/api/info" 2>/dev/null || true)"
  if printf '%s' "$info" | grep -q "\"version\":\"$IMAGE_TAG\""; then
    echo "Doubt Wall is serving version $IMAGE_TAG."
    exit 0
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "The Doubt Wall is not serving version $IMAGE_TAG after ${SMOKE_TIMEOUT:-90} s. /api/info said: ${info:-no answer}" >&2
    exit 1
  fi
  echo "Waiting for version $IMAGE_TAG (currently: ${info:-no answer})"
  sleep 10
done
```

</details>

<details>
<summary><code>ci/test.sh</code></summary>

```bash
#!/usr/bin/env bash
# Stage "Test": runs both test suites inside throwaway containers, so the
# Jenkins server needs only Docker (no Python installed on it).
# The Doubt Wall suite gets a real, temporary PostgreSQL; REQUIRE_DB=1 makes
# it fail (instead of silently skipping) if that database isn't reachable.
set -euo pipefail

PY_IMAGE="${PY_IMAGE:-python:3.12-slim}"
PG_IMAGE="${PG_IMAGE:-postgres:18-alpine}"
ID="$(printf '%s' "${BUILD_TAG:-local}-$$" | tr -c 'a-zA-Z0-9_.-' '-')"
NET="ci-net-$ID"
PG="ci-pg-$ID"

cleanup() {
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p reports
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER=doubtwall -e POSTGRES_PASSWORD=doubtwall -e POSTGRES_DB=doubtwall_test \
  "$PG_IMAGE" >/dev/null

echo "Waiting for PostgreSQL..."
for i in $(seq 1 60); do
  # -h 127.0.0.1: the image's temporary setup server listens only on a unix
  # socket, so a TCP check passes only once the real server is up.
  if docker exec "$PG" pg_isready -h 127.0.0.1 -U doubtwall -d doubtwall_test >/dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "PostgreSQL did not become ready in 60 s." >&2
    docker logs "$PG" >&2
    exit 1
  fi
  sleep 1
done

# Source is mounted read-only; results go to ./reports for Jenkins (JUnit).
# Running as the Jenkins user's UID keeps report files deletable by Jenkins.
run_suite() {
  local app="$1"; shift
  echo "=== Testing $app ==="
  docker run --rm --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -v "$PWD/$app:/src:ro" -v "$PWD/reports:/reports" -w /src \
    "$@" "$PY_IMAGE" \
    sh -c "pip install --user --quiet -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider --junitxml=/reports/$app.xml"
}

run_suite aiops-triage
run_suite doubt-wall --network "$NET" \
  -e "TEST_DATABASE_URL=postgresql://doubtwall:doubtwall@$PG:5432/doubtwall_test" \
  -e REQUIRE_DB=1
echo "All tests passed."
```

</details>


### Argo CD

<details>
<summary><code>argocd/application.yaml</code></summary>

```yaml
# Tells Argo CD: "keep the marathon namespace in sync with k8s/overlays/gitops
# in this Git repository". Apply once:
#   kubectl apply -f argocd/application.yaml
#
# Before applying, set repoURL to YOUR fork (and targetRevision to your branch).
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: marathon
  namespace: argocd
  # Deleting this Application also deletes the app's Kubernetes resources.
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/YOUR-GITHUB-USER/Demo-B17.git
    targetRevision: main
    path: k8s/overlays/gitops
  destination:
    server: https://kubernetes.default.svc
    namespace: marathon
  syncPolicy:
    automated:
      prune: true      # resources removed from git are removed from the cluster
      selfHeal: true   # manual kubectl changes are reverted to what git says
    syncOptions:
      - CreateNamespace=true
    retry:
      limit: 5
      backoff:
        duration: 10s
        factor: 2
        maxDuration: 3m
# Secrets are not in git: marathon-secrets comes from k8s/create-secrets.sh and
# ecr-pull from the refresh timer on the k3s node. Argo CD only manages what it
# created from git, so it leaves both alone. (Next step in a real project:
# Sealed Secrets or External Secrets Operator, so secrets can live in git safely.)
```

</details>
