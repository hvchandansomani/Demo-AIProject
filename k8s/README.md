# Kubernetes manifests

Everything needed to run the Doubt Wall, AIOps Triage and PostgreSQL on
Kubernetes. Written for **k3s** (the marathon cluster) and also works on any
local cluster.

```
k8s/
  create-secrets.sh          run once per cluster: creates marathon-secrets
  base/                      shared manifests (don't apply directly)
    namespace.yaml             "marathon" namespace + Pod Security labels
    kustomization.yaml         resource list + non-secret config (ConfigMaps)
    ingress.yaml               /  -> Doubt Wall,  /ops -> AIOps Triage
    postgres/                  StatefulSet, headless Service, NetworkPolicy,
                               init-app-role.sh (least-privilege DB setup)
    doubt-wall/                Deployment, Services, HPA, PodDisruptionBudget
    aiops-triage/              Deployment, Service, read-only RBAC
  overlays/
    aws/                       images from ECR (Jenkins kubectl mode)
    gitops/                    images from ECR, tags written by Jenkins, deployed by Argo CD
    local/                     images built on your machine
```

## What gets created

| Resource | Name | Why |
|---|---|---|
| Namespace | `marathon` | Keeps everything together; Pod Security "baseline" enforced, "restricted" warned |
| StatefulSet + PVC | `postgres`, `data-postgres-0` (2 Gi) | Database with data on a persistent volume |
| Service (headless) | `postgres` | Stable DNS name, cluster-internal only |
| NetworkPolicy | `postgres-allow-doubt-wall` | Only Doubt Wall pods may connect to PostgreSQL |
| Deployment | `doubt-wall` (2–5 pods) | Learner app |
| Service | `doubt-wall` | Load-balances learner traffic |
| Service (headless) | `doubt-wall-pods` | Lists every pod so chaos switches reach all replicas |
| HPA | `doubt-wall` | 2–5 pods at 70% CPU |
| PodDisruptionBudget | `doubt-wall` | At least one pod stays up during node maintenance |
| Deployment | `aiops-triage` (1 pod) | Instructor console |
| ServiceAccount + Role | `aiops-triage`, `aiops-triage-read` | Read pods, logs, events in `marathon` only |
| Ingress | `marathon` | One public entry point through Traefik |
| ConfigMaps | `*-config-<hash>`, `postgres-init-<hash>` | Settings; a change rolls the pods automatically |
| Secret | `marathon-secrets` | Created by `create-secrets.sh`, never in git |

## Deploy to the marathon cluster (k3s)

Jenkins does this on every build (`ci/deploy.sh`). By hand, from a machine
with `kubectl` pointed at the cluster:

```bash
./k8s/create-secrets.sh                 # once per cluster; prints ADMIN_TOKEN and WEBHOOK_TOKEN
# Then let Jenkins deploy (it fills in the ECR registry and image tag), or see ci/deploy.sh.
```

Check it:

```bash
kubectl -n marathon get pods,svc,ingress,hpa,pvc
curl http://<node-ip>/ready              # Doubt Wall
curl http://<node-ip>/ops/health         # AIOps Triage
```

## Run on a local cluster

1. **Build the images:**
   ```bash
   docker compose build                   # produces doubt-wall:local and aiops-triage:local
   ```
2. **Make the images visible to the cluster:**

   | Cluster | Extra step |
   |---|---|
   | Docker Desktop Kubernetes | none (it shares Docker's images) |
   | k3d | `k3d image import doubt-wall:local aiops-triage:local` |
   | kind | `kind load docker-image doubt-wall:local aiops-triage:local` |
   | minikube | `minikube image load doubt-wall:local` and the same for `aiops-triage:local` |

3. **Create secrets and deploy:**
   ```bash
   ./k8s/create-secrets.sh
   kubectl apply -k k8s/overlays/local
   kubectl -n marathon get pods -w        # wait until everything is Running and READY
   ```
4. **Open it:** k3d and k3s include Traefik, so use the cluster's address. On
   Docker Desktop or kind (no ingress controller by default):
   ```bash
   kubectl -n marathon port-forward svc/doubt-wall 8000:80     # http://localhost:8000
   kubectl -n marathon port-forward svc/aiops-triage 8080:80   # http://localhost:8080/ops
   ```

`create-secrets.sh` uses bash and openssl: use Git Bash or WSL on Windows.

## Design decisions worth explaining

**Least-privilege database.** PostgreSQL's first start runs
`postgres/init-app-role.sh`. It creates the `doubts` table owned by the
`postgres` superuser, plus a `doubtwall` role that can only `SELECT`, `INSERT`,
`UPDATE` and `DELETE` rows. The app logs in as `doubtwall`, so even a
compromised app can't drop or alter the table. Tested: `DROP`, `ALTER`,
`CREATE` and `TRUNCATE` are refused. The role also gets
`statement_timeout = 5s` and a connection limit of 60.

**Liveness vs readiness.** `/health` (liveness) never checks the database;
`/ready` (readiness) does. A database blip takes pods out of the Service
instead of restarting them all.

**Zero-downtime rollouts.** `maxUnavailable: 0` plus readiness probes: a new
version receives traffic only once it's ready. If it never becomes ready, the
old pods keep serving and Jenkins rolls back.

**Hardened app pods.** Both apps meet the "restricted" Pod Security Standard:
non-root (UID 10001), no privilege escalation, all Linux capabilities dropped,
read-only root filesystem (`/tmp` is an emptyDir), default seccomp profile,
and no Kubernetes API token for the Doubt Wall.

**Config vs secrets.** Non-secret settings live in ConfigMaps generated by
Kustomize. Their names carry a hash of the content, so editing a value in
`base/kustomization.yaml` and re-applying rolls the pods automatically.
Secrets live only in `marathon-secrets`.

**Chaos reaches every replica.** AIOps Triage sends each switch to every pod
through the headless `doubt-wall-pods` Service, which also lists not-ready
pods, so "Stop all chaos" can still reach a pod whose readiness was switched
off. Pods created later by the HPA start without chaos.

## Common changes

| To | Do |
|---|---|
| Use Claude instead of the rules | Run `create-secrets.sh` with `ANTHROPIC_API_KEY` (or patch the secret), set `LLM_MODE=claude` in `base/kustomization.yaml`, re-deploy |
| Demo a rolling update | Change `BANNER_TEXT` or `BANNER_COLOR` in `base/kustomization.yaml`, commit, let Jenkins deploy |
| Scale by hand | `kubectl -n marathon scale deploy/doubt-wall --replicas=3` (the HPA keeps it within 2–5) |
| Open psql | `kubectl -n marathon exec -it postgres-0 -- psql -U postgres -d doubtwall` |
| Reset the database | `kubectl -n marathon delete statefulset postgres && kubectl -n marathon delete pvc data-postgres-0`, then re-deploy (all doubts are lost) |

## Troubleshooting

| Symptom | Check |
|---|---|
| Pods `CreateContainerConfigError` | `marathon-secrets` missing: run `create-secrets.sh` |
| Pods `ImagePullBackOff` (AWS) | The image tag isn't in ECR, or the `ecr-pull` secret expired (it lasts 12 h; any Jenkins deploy refreshes it) |
| Pods `ErrImagePull` / `ImagePullBackOff` (local) | Images not loaded into the cluster (step 2 above) |
| Doubt Wall `0/1 READY`, logs say database unreachable | `kubectl -n marathon get pods -l app=postgres`; check `kubectl -n marathon logs postgres-0` |
| `password authentication failed` after re-running secrets | PostgreSQL keeps the passwords from its first start. Reset the database (table above) |
| PVC `Pending` | No default StorageClass. k3s has `local-path`; on other clusters check `kubectl get storageclass` |
| HPA shows `<unknown>` CPU | metrics-server isn't installed (k3s includes it) |
