# Jenkins pipeline

`Jenkinsfile` builds, tests, scans and deploys both apps on every run. Each
stage calls a small script in `ci/`, so the logic is plain bash you can read,
run by hand, and explain in class.

```
Prepare ──► Test ──► Build images ──► Security scan ──► Push to ECR ──► Deploy to k3s ──► Smoke test
   │          │            │                │                │                │               │
 image tag  both suites  2 images,     Trivy 0.69.3,     ECR login via     kubectl apply,   /ready, /ops/health,
 <build>-   in containers labels with   no Docker socket  instance role    wait, auto-       right version
 <commit>   + real PG     version+commit fails on CRITICAL no AWS keys      rollback          served?
```

| Stage | Script | What it does | Fails when |
|---|---|---|---|
| Prepare | (Jenkinsfile) | Builds the image tag `<build number>-<commit>`, checks tools | `ECR_REGISTRY` not set |
| Test | `ci/test.sh` | Both pytest suites in `python:3.12-slim` containers; a throwaway PostgreSQL 18 for the Doubt Wall; JUnit results shown in Jenkins | Any test fails, or the test database is unreachable (`REQUIRE_DB=1`) |
| Build images | `ci/build.sh` | `docker build` for both apps with version and commit labels | Build error |
| Security scan | `ci/scan.sh` | Trivy scans each image for known vulnerabilities; report archived | A CRITICAL vulnerability with a fix exists (switch off with the `FAIL_ON_CRITICAL_VULNS` parameter) |
| Push to ECR | `ci/push.sh` | Logs in with the EC2 instance role, pushes, logs out | No permission or repository missing |
| Update GitOps repo (gitops mode) | `ci/gitops-update.sh` | Commits `[skip ci] Deploy <tag>` to the overlay with a token from the `git-push` credential (never in the URL) | Token missing or without write access |
| Deploy to k3s (kubectl mode) | `ci/deploy.sh` | Refreshes the ECR pull secret, applies `k8s/overlays/aws` with this build's images, waits for rollout | A rollout doesn't finish in 180 s; it's then **rolled back automatically** |
| Smoke test | `ci/smoke.sh` | Calls the public URL and checks the new version is the one serving | An endpoint fails or the old version is still serving |

## One-time setup

> **Using the Ansible playbook?** It does all of this for you (plugins,
> `ECR_REGISTRY`, `AWS_REGION`, the `k3s-kubeconfig` credential, the
> `marathon` job, and optionally `git-push`). The manual steps below are for
> understanding, or for a Jenkins you set up yourself.

### 1. On the Jenkins server

Ansible will install these; check by hand if needed:

| Tool | Check |
|---|---|
| Docker, and the `jenkins` user in the `docker` group | `sudo -u jenkins docker ps` |
| AWS CLI v2 | `aws --version` |
| kubectl | `kubectl version --client` |
| git, curl | `git --version`, `curl --version` |

The EC2 instance needs an **IAM instance role** that can push to ECR
(Terraform creates it). The pipeline never uses AWS access keys.

### 2. In Jenkins

| Setting | Where | Value |
|---|---|---|
| Plugins | Manage Jenkins > Plugins | Pipeline, Git, Credentials Binding, JUnit, Timestamper (most come with "suggested plugins") |
| `ECR_REGISTRY` | Manage Jenkins > System > Global properties > Environment variables | `<account-id>.dkr.ecr.<region>.amazonaws.com` |
| `AWS_REGION` (optional) | Same place | Defaults to `ap-south-1` |
| Credential `git-push` (gitops mode only) | Same place | Kind **Username with password**: GitHub user + a fine-grained token with Contents read/write on your fork |
| Credential `k3s-kubeconfig` | Manage Jenkins > Credentials > System > Global > Add | Kind **Secret file**; upload the k3s kubeconfig (`/etc/rancher/k3s/k3s.yaml` from the k3s node, with `server:` changed from `127.0.0.1` to the node's private IP) |

### 3. In AWS

ECR repositories named `doubt-wall` and `aiops-triage` (Terraform creates them).

### 4. In the cluster (once)

```bash
./k8s/create-secrets.sh       # keep the printed ADMIN_TOKEN and WEBHOOK_TOKEN
```

### 5. Create the job

New Item > **Pipeline** > Pipeline script from SCM > Git > your repo URL >
Script Path `Jenkinsfile`. Run it with **Build with Parameters**:

| Parameter | Default | Meaning |
|---|---|---|
| `DEPLOY` | on | Deploy after a successful build |
| `FAIL_ON_CRITICAL_VULNS` | on | Stop on fixable CRITICAL vulnerabilities |
| `DEPLOY_MODE` | `kubectl` | `kubectl`: Jenkins deploys to k3s. `gitops`: Jenkins commits the new tag to `k8s/overlays/gitops` and Argo CD deploys |
| `APP_URL` | empty | e.g. `http://13.234.10.20`; empty skips the smoke test |

## Security choices to point out in class

- **No secrets in the pipeline.** AWS access comes from the instance role; the
  kubeconfig is a Jenkins credential; app secrets live only in the cluster.
- **The ECR token never appears on a command line.** `deploy.sh` writes it to
  a private temp file, so it can't be seen in the server's process list.
  `push.sh` logs out of ECR when it finishes.
- **The scanner is pinned and isolated.** Trivy was hit by a supply-chain
  attack in March 2026: versions 0.69.4–0.69.6, and `latest` for four days,
  stole CI secrets ([advisory GHSA-69fq-xp46-6x23](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)).
  The pipeline pins 0.69.3, the last clean release, and scans an exported image
  file instead of mounting the Docker socket. Check the advisory before changing
  the version.
- **Tests can't pass by accident.** If the test database is missing, the
  Doubt Wall suite errors instead of silently skipping.
- **Tests can't modify the workspace.** Source is mounted read-only into the
  test containers, which run as the Jenkins user, not root.
- **Every image is traceable.** Tag `<build>-<commit>` plus OCI labels; the
  UI footer shows the running version.
- **Failed deploys don't reach learners.** `maxUnavailable: 0` keeps old pods
  serving until new ones are ready; a failed rollout is undone automatically.
- **One deploy at a time.** `disableConcurrentBuilds()`.

## Demo ideas

| Show | How |
|---|---|
| Rolling update | Change `BANNER_TEXT` in `k8s/base/kustomization.yaml`, push, watch learners' banners change with no downtime |
| Failing test stops a release | Break an assertion in a test, push: the pipeline stops at Test and nothing is deployed |
| Bad deploy rolls back | Set the Doubt Wall's readiness probe path to `/nope` in `k8s/base/doubt-wall/deployment.yaml`, push: rollout times out, Jenkins rolls back, learners never notice |
| Security gate | Show the archived `trivy-*.txt` report on the build page |

## Run a stage by hand

The scripts read the same environment variables Jenkins sets:

```bash
export IMAGE_TAG=dev-$(git rev-parse --short HEAD) ECR_REGISTRY=<registry> AWS_REGION=ap-south-1
bash ci/test.sh          # needs only Docker
bash ci/build.sh
bash ci/scan.sh
```

## Limits of what was tested before handover

The scripts were linted with shellcheck, the Jenkinsfile was compiled with
npm-groovy-lint (0 errors), and every script's success and failure paths were
run with stand-in `docker`, `aws`, `kubectl` and `curl` commands. They
haven't run on a real Jenkins server against real AWS yet, so the first real
run is part of the rehearsal plan.
