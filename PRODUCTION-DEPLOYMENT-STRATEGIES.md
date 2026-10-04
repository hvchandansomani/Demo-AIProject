# How Companies Deploy to Production

A guide for explaining real-world deployment to learners: what happens between
"a developer pushes code" and "customers use it", the main deployment
strategies, how different kinds of companies choose between them, and how our
marathon setup compares. Ends with questions learners usually ask, and answers.

---

## 1. The short answer: is what we built "the right way"?

**The patterns are real; the scale is not.** Everything in the marathon is
something production teams do every day:

| What we did | Is it used in production? |
|---|---|
| Infrastructure as code (Terraform) with remote, locked state | Yes, standard practice |
| Configuration as code (Ansible, Jenkins Configuration as Code) | Yes, though containers and managed services reduce how much server config is needed |
| Immutable, versioned images (`<build>-<commit>`, ECR immutable tags) | Yes, essential |
| CI with tests, a security scan and a gate before deploy | Yes, standard |
| Kubernetes rolling updates with readiness probes and automatic rollback | Yes, the default strategy for most services |
| GitOps with Argo CD (git as source of truth, pull-based) | Yes, very common for Kubernetes teams |
| Least-privilege IAM roles, no access keys, secrets outside git | Yes, required by most security teams |

What we **simplified** for a 2-day class:

| Our setup | Typical production |
|---|---|
| One environment | Dev → staging → production (often separate AWS accounts) |
| One k3s node on one EC2 server | Managed Kubernetes (EKS/GKE/AKS), several nodes across 3 availability zones |
| PostgreSQL in a pod | Managed database (RDS/Aurora), multi-AZ, automated backups, point-in-time restore |
| Rolling update to everyone at once | Progressive delivery: canary or blue/green, sometimes region by region |
| HTTP | HTTPS everywhere (certificates from ACM or Let's Encrypt) |
| One shared admin token | Single sign-on (SSO/OIDC), per-person roles, MFA, audit logs |
| Self-hosted Jenkins on one server | Often a managed CI (GitHub Actions, GitLab CI) or Jenkins with agents that scale |
| Secrets created by a script | Secrets manager (AWS Secrets Manager, Vault) synced by External Secrets Operator, or Sealed Secrets |
| Deploy whenever the instructor clicks | Change process: code review, approvals, deploy windows, freeze periods |
| Prometheus and a few alerts (planned) | SLO-based alerting, on-call rota, runbooks, incident process |

So when a learner asks "is this how companies do it?", the honest answer is:
*yes, the building blocks and the order are the same; companies add more
environments, more safety layers around the rollout, and more redundancy.*

---

## 2. The life of a change in a real company

```
 Developer                        CI (minutes)                          CD (minutes to days)
 ─────────                        ────────────                          ────────────────────
 branch ─► pull request ─► review ─► build ─► unit tests ─► lint ─► SAST ─► dependency scan
                           (1-2 people)                                         │
                                         ┌──── image built ONCE, signed, SBOM ◄─┘
                                         ▼
                                   artifact registry (ECR, Artifactory)
                                         │   same image, promoted, never rebuilt
                 ┌───────────────────────┼─────────────────────────┐
                 ▼                       ▼                         ▼
               dev                    staging                  production
         (auto, on merge)     (integration, e2e, load     (approval or automatic gate,
                               tests, security tests)      then progressive rollout:
                                                           canary 1% → 10% → 50% → 100%,
                                                           watching error rate & latency)
                                                                   │
                                                  metrics bad? ──► automatic rollback
                                                  metrics good ──► next stage / done
```

Key ideas to emphasise:

1. **Build once, deploy many.** The exact image (same digest) that passed
   staging is the one that reaches production. Rebuilding for each
   environment risks shipping something untested. Only configuration changes
   between environments.
2. **Shift left.** Tests, linting and security scans run on every pull
   request, where fixing a problem is cheapest.
3. **Deploy is not release.** Code can be in production but switched off
   behind a *feature flag*, then turned on for 1% of users, then everyone.
   That separates "the code is running" from "customers see the feature".
4. **Small, frequent changes.** A deploy containing one change is easy to
   reason about and easy to roll back. Research behind the DORA metrics
   consistently links frequent, small deploys with fewer failures and faster recovery.
5. **Automated verification and rollback.** Large companies don't rely on a
   person watching a dashboard; the pipeline compares metrics and stops or
   reverts automatically.

### The four DORA metrics

The industry-standard way to measure delivery performance:

| Metric | Question | Our marathon |
|---|---|---|
| Deployment frequency | How often do we deploy? | Every pipeline run |
| Lead time for changes | Commit to production: how long? | About 5–10 minutes |
| Change failure rate | What share of deploys cause problems? | Visible from rollbacks |
| Time to restore service | How fast do we recover? | Automatic rollback (≈3 min) or `git revert` |

---

## 3. Deployment strategies in detail

### 3.1 Recreate

```
v1 v1 v1  ──stop all──►  (nothing)  ──start all──►  v2 v2 v2
```

- **How:** stop every old instance, then start the new ones.
- **Pros:** simplest; never two versions running at once (useful when they can't coexist, e.g. an incompatible database change).
- **Cons:** **downtime** for the whole switch.
- **Used for:** internal tools, batch jobs, dev environments, maintenance windows.
- **Kubernetes:** `strategy: type: Recreate`.

### 3.2 Rolling update (what we use)

```
v1 v1 v1 → v2 v1 v1 → v2 v2 v1 → v2 v2 v2
```

- **How:** replace instances a few at a time; each new one must pass its readiness check before the next old one is removed.
- **Pros:** no downtime, no extra infrastructure, built into Kubernetes.
- **Cons:** for a while **both versions serve traffic**, so they must be compatible (APIs, database schema). Every user is eventually exposed, so problems spread to everyone before you notice. Rollback is another rolling update, so it's not instant.
- **Used for:** the default for most stateless services.
- **Kubernetes:** `strategy: RollingUpdate` with `maxSurge` / `maxUnavailable`. Ours uses `maxUnavailable: 0, maxSurge: 1` plus readiness probes, so capacity never drops and a broken version never receives traffic.

### 3.3 Blue/green

```
            ┌─► BLUE  (v1, live)        switch traffic        BLUE  (v1, idle, kept for rollback)
router ─────┤                         ───────────────►  ┌─►
            └   GREEN (v2, testing)                     │   GREEN (v2, live)
```

- **How:** run a full copy of the new version (green) next to the old one (blue), test it privately, then switch all traffic at once (load balancer, DNS or Kubernetes Service selector). Keep blue for a while.
- **Pros:** **instant rollback** (switch back); you can test the exact production environment before users see it.
- **Cons:** **double the infrastructure** during the switch; database changes still need to work for both; switching DNS can be slow because of caching.
- **Used for:** banks, payment systems, monoliths, anything where a fast, clean rollback matters more than cost.
- **Kubernetes:** two Deployments and a Service whose selector you flip, or Argo Rollouts' `blueGreen` strategy. **AWS:** CodeDeploy blue/green for ECS, ALB target-group switching.

### 3.4 Canary (progressive delivery)

```
v2 ─► 1% of traffic ─► wait, compare metrics ─► 10% ─► 50% ─► 100%
                              │ worse than v1?
                              └──────────────► roll back automatically
```

- **How:** send a small share of real traffic to the new version, compare its error rate, latency and business metrics with the old version (the "baseline"), and widen step by step. The name comes from the canaries miners carried to detect gas.
- **Pros:** a bad release hurts only a small share of users, briefly; decisions are based on real production traffic, not just tests.
- **Cons:** needs good metrics and traffic control (ingress controller, service mesh, or weighted load balancer); two versions coexist; small services may not have enough traffic for meaningful statistics.
- **Used for:** most large consumer-facing companies; Google's SRE Workbook has a whole chapter on canarying, and Netflix and Google built Kayenta, an open-source automated canary analysis tool.
- **Kubernetes:** Argo Rollouts or Flagger (with Prometheus analysis), or service mesh traffic splitting (Istio, Linkerd). **AWS:** ALB weighted target groups, Lambda aliases with weights, CodeDeploy canary configs.

### 3.5 Ring / wave deployments

```
internal users ─► 1 region (small) ─► bake ─► a few regions ─► bake ─► all regions
```

- **How:** deploy to progressively larger groups ("rings" or "waves"), waiting a "bake time" between each and watching alarms.
- **Pros:** limits the blast radius at a very large scale; an incident affects one region or cell, not the world.
- **Cons:** a full rollout can take days; needs mature automation.
- **Used for:** cloud providers and very large services. Amazon's Builders' Library describes AWS pipelines that deploy first to a single host ("one-box"), then in waves of increasing size, with bake times and automatic rollback on alarms.

### 3.6 Feature flags (dark launches)

- **How:** new code ships switched off, and a flag service turns it on for chosen users (staff, 1%, one country), separately from deploying.
- **Pros:** instant "rollback" by flipping the flag; A/B tests; trunk-based development with unfinished features merged safely.
- **Cons:** flags pile up as technical debt; every flag doubles a code path to test.
- **Tools:** LaunchDarkly, Unleash, Flagsmith, AWS AppConfig, the OpenFeature standard.

### 3.7 Shadow (traffic mirroring)

- **How:** copy real requests to the new version, but throw away its responses. Users only see the old version.
- **Pros:** tests with real traffic and zero user impact.
- **Cons:** double the load; dangerous for anything with side effects (payments, emails) unless those are stubbed.
- **Used for:** rewrites, migrations, performance testing. Service meshes (Istio) support mirroring.

### 3.8 A/B testing

Not really a deployment strategy but often confused with canary: two variants
run on purpose to measure **user behaviour** (conversion, clicks), not technical
health. Usually done with feature flags.

### Comparison

| Strategy | Downtime | Rollback speed | Extra infra | Complexity | Users exposed to a bad release |
|---|---|---|---|---|---|
| Recreate | Yes | Slow (redeploy) | None | Very low | All |
| Rolling | No | Medium (roll back) | Minimal | Low | All, gradually |
| Blue/green | No | Instant (switch) | 2× during switch | Medium | All, but briefly |
| Canary | No | Fast (stop the canary) | Small | High | Small share |
| Waves/rings | No | Fast per wave | Small | High | One wave |
| Feature flags | No | Instant (flip) | Flag service | Medium, plus code discipline | Chosen group |
| Shadow | No | Not needed | 2× load | High | None |

**Rule of thumb for learners:** start with rolling updates and good health
checks (what we built). Add blue/green when instant rollback matters, canary
when you have enough traffic and good metrics, and feature flags when you want
to release features separately from deploying code.

---

## 4. Push vs pull: Jenkins kubectl vs GitOps

| | Push (our Part 5) | Pull / GitOps (our Part 6) |
|---|---|---|
| Who changes the cluster | The CI server runs `kubectl apply` | An agent inside the cluster (Argo CD, Flux) applies what's in git |
| Credentials | CI needs cluster-admin-like access | Nothing outside the cluster needs cluster credentials |
| Source of truth | Whatever was applied last | Git: the cluster always converges to it |
| Drift (manual changes) | Stays until the next deploy | Detected and reverted (self-heal) |
| Rollback | Re-run an old build or `kubectl rollout undo` | `git revert` |
| Audit | CI logs | Git history (who, what, when, reviewed by whom) |
| Common in | Simple setups, non-Kubernetes targets | Most Kubernetes platforms today |

The common real-world pattern is **both**: CI (Jenkins, GitHub Actions)
builds, tests and pushes images and updates a tag in git; GitOps deploys.
That's exactly the marathon's gitops mode. Many teams also keep manifests in a
separate "config repo" so app code and deployment config have different
permissions and review rules.

---

## 5. The hard part: databases

Code can be rolled back in seconds; data can't. Real teams follow
**expand / contract** (also called "parallel change"):

1. **Expand:** add the new column/table, nullable, without removing anything. Deploy. Old and new code both work.
2. **Migrate:** deploy code that writes to both and backfills old rows.
3. **Switch:** deploy code that reads from the new structure.
4. **Contract:** only after the old version is gone and the rollback window has passed, remove the old column.

Rules that follow from it:

- Never ship a destructive migration (drop/rename) in the same release as the code that needs it.
- Every migration must work with the **previous** version of the app, because during a rolling update or canary both run.
- Run migrations with a dedicated tool (Flyway, Liquibase, Alembic) as a separate step or Kubernetes Job, using a database role with DDL rights. The app's role shouldn't have them. Our app's restricted `doubtwall` role in Kubernetes is that same idea.
- Take a backup or snapshot before risky migrations; test restoring it.

---

## 6. Guardrails around every production deploy

| Guardrail | What it prevents | In our marathon |
|---|---|---|
| Code review on every change | Unreviewed changes | Not enforced (add branch protection) |
| Automated tests in CI | Known bugs reaching users | ✅ 60 tests, real PostgreSQL |
| Security scanning (SAST, dependencies, images) | Known vulnerabilities | ✅ Trivy image scan (pinned), ECR scan on push |
| Signed images, SBOM, provenance (SLSA) | Tampered or unknown artifacts | Not yet |
| Readiness/liveness/startup probes | Traffic to broken pods; stuck pods | ✅ |
| `maxUnavailable: 0`, PodDisruptionBudget | Capacity drops during deploys and maintenance | ✅ |
| Graceful shutdown (`preStop`, termination grace) | Dropped requests during rollouts | ✅ |
| Resource requests/limits, autoscaling | Noisy neighbours, overload | ✅ HPA 2–5 pods |
| Automatic rollback on failed rollout | Broken releases staying live | ✅ rollout timeout → undo; Argo CD retries |
| Metric-based rollback (error rate, latency) | Releases that are "up" but wrong | Not yet (Argo Rollouts + Prometheus) |
| Deploy freezes (holidays, big events), deploy windows | Changes when nobody's there to react | Not needed in class |
| Change approval / separation of duties | Unauthorised changes (regulated industries) | Not needed in class |
| Observability + on-call | Problems nobody notices | Planned (Prometheus, Grafana, CloudWatch, AIOps Triage) |

---

## 7. How different companies choose

These are typical patterns, not rules; every company mixes them.

| Kind of company | Typical setup | Why |
|---|---|---|
| **Startup / small team** | Managed platform (ECS, Cloud Run, App Runner, Vercel) or a small EKS cluster; GitHub Actions; rolling updates; feature flags early | Speed and low operational effort matter most |
| **Mid-size SaaS** | EKS/GKE, Terraform, GitHub Actions or Jenkins for CI, Argo CD for CD, canary with Argo Rollouts, staging environment, SLOs | Many services and teams; need safety without slowing down |
| **Large tech / cloud provider** | Internal deployment systems; one-box → waves → regions with bake times; automated canary analysis; cell-based architecture; deploys many times a day, fully automated | Huge blast radius; humans can't watch everything |
| **Bank / healthcare / government** | Blue/green, scheduled release windows, change advisory board approval, strict separation of duties, full audit trail, sometimes on-premises | Regulation and auditability; predictability over speed |
| **Mobile / desktop apps** | Staged rollouts in app stores (e.g. 1% → 100%), server-side feature flags | You can't roll back an app on someone's phone |

---

## 8. Turning the marathon into a production setup

In roughly the order a team would do it:

1. **Environments:** separate dev, staging and prod (ideally separate AWS accounts); promote the same image between them.
2. **Managed services:** EKS across 3 availability zones; RDS PostgreSQL multi-AZ with automated backups; drop self-managed PostgreSQL.
3. **HTTPS and identity:** AWS Load Balancer Controller + ACM certificates; SSO for Jenkins, Argo CD and the console.
4. **Secrets:** AWS Secrets Manager with External Secrets Operator (or Sealed Secrets), rotation.
5. **Progressive delivery:** replace the Deployment with an Argo Rollouts `Rollout` (canary 10% → 50% → 100%) that queries Prometheus and aborts automatically.
6. **Supply chain:** branch protection and required reviews; sign images (cosign) and verify them at admission (Kyverno or similar); generate SBOMs.
7. **Policy:** admission policies (no `latest` tags, required limits, non-root), network policies for every app.
8. **Observability and on-call:** SLOs, burn-rate alerts, runbooks, an incident process. AIOps Triage becomes one input, not the whole story.
9. **Data:** migrations as a separate pipeline step with expand/contract; tested restores.
10. **Cost and resilience:** autoscaling nodes (Karpenter), budgets per team, regular disaster-recovery drills.

The `#checkov:skip` comments in `terraform/` and the gaps table in
`ARCHITECTURE-AND-SECURITY.md` list the infrastructure-level items.

---

## 9. Questions learners usually ask

**Q1. Is rolling update good enough for production?**
For most stateless services, yes, *if* readiness probes are real and the old
and new versions are compatible. It's the default for good reason. It's not
enough when a bad release must reach as few users as possible (use canary) or
when rollback must be instant (use blue/green).

**Q2. What's the difference between canary and blue/green?**
Blue/green switches *all* traffic at once between two full environments, with
instant rollback. Canary moves traffic *gradually* (1% → 100%) to limit how
many users see a problem. Blue/green costs more infrastructure; canary needs
good metrics and traffic splitting.

**Q3. Why not just test properly in staging and skip all this?**
Staging is never identical to production: different data, traffic, scale and
integrations. Some bugs only appear with real users. Progressive rollouts
catch what staging can't, with a small blast radius.

**Q4. Do companies really deploy many times a day?**
Teams with mature automation do, often dozens or hundreds of times across all
services, because each change is small and the pipeline is trusted. Regulated
companies may deploy weekly or monthly in planned windows. Both are valid; the
DORA research shows frequent small deploys correlate with fewer failures.

**Q5. Who decides when to deploy: a person or the pipeline?**
Increasingly the pipeline. A merge to main triggers everything, and automated
checks decide whether to continue. People approve where regulation requires it
or for high-risk changes. Our marathon has a person press "Build".

**Q6. What happens if the deploy fails at 2 a.m.?**
In good setups: the rollout fails its health checks, the pipeline rolls back
automatically, and an alert pages the on-call engineer only if users are
still affected. Many companies avoid risky deploys outside working hours for
this reason.

**Q7. How do you roll back a database change?**
Usually you don't; you design changes so you never have to. Use
expand/contract: add first, remove much later. If data must be restored, use
backups or point-in-time recovery, which is slow, so it's the last resort.

**Q8. Why use Argo CD if Jenkins can already run `kubectl apply`?**
GitOps gives you a single source of truth (git), automatic drift correction,
rollback by `git revert`, an audit trail through pull requests, and no cluster
credentials outside the cluster. Jenkins still builds and tests. The two work
together.

**Q9. Is Jenkins still used, or is everyone on GitHub Actions?**
Both are widely used. Jenkins is common in large and older organisations and
where builds must run on private infrastructure; GitHub Actions and GitLab CI
are common in newer teams. The concepts (stages, credentials, artifacts,
gates) transfer directly.

**Q10. Why k3s and not EKS?**
k3s starts in about a minute and costs less, which suits a timed class. EKS is
managed Kubernetes for production: AWS runs the control plane across
availability zones. The manifests we wrote work on both; only the
infrastructure code changes.

**Q11. Should the database run in Kubernetes?**
It can (StatefulSets and operators such as CloudNativePG make it workable),
but most teams prefer a managed database (RDS/Aurora) for backups, failover,
patching and point-in-time recovery without running it themselves.

**Q12. Where should secrets live?**
In a secrets manager (AWS Secrets Manager, HashiCorp Vault), synced into
Kubernetes by External Secrets Operator, or encrypted into git with Sealed
Secrets. Never plain in git, images or CI logs. Our `create-secrets.sh` is the
simple classroom version.

**Q13. What's the difference between deploying and releasing?**
Deploying puts new code on servers. Releasing makes a feature available to
users. Feature flags let you deploy on Tuesday and release on Thursday, or
release to 1% first.

**Q14. How do they make sure the image in production is the one that was tested?**
Build once, tag immutably (or reference by digest), promote the same image
through environments, and in stricter setups sign it (cosign) and have the
cluster refuse unsigned images.

**Q15. What's a "blast radius"?**
How much breaks when something goes wrong. Canary, waves, cells, separate
accounts and feature flags all exist to keep it small.

**Q16. How long should a canary run?**
Long enough to see meaningful metrics: minutes for high-traffic services,
hours (or a full daily cycle) when traffic is low or problems are slow to show
up, like memory leaks. Google's SRE Workbook discusses balancing canary size and
duration against risk.

**Q17. What triggers an automatic rollback?**
Failed health checks (Kubernetes rollout doesn't complete), alarms on error
rate or latency, or a canary analysis that finds the new version worse than
the baseline. Ours: rollout timeout → `kubectl rollout undo`.

**Q18. Do they use `latest` tags?**
No. `latest` changes meaning silently, breaks rollbacks and can pull untested
images. Use immutable tags like our `<build>-<commit>` or image digests. The
Trivy incident in March 2026 is a good example: pinned versions protected
teams from the malicious builds; `latest` didn't.

**Q19. How many environments do companies have?**
Commonly three (dev, staging, prod), sometimes more (QA, performance, pre-prod,
a sandbox per developer). More environments cost money and time; teams balance
confidence against speed.

**Q20. What's GitOps "drift" and why does self-heal matter?**
Drift is when the cluster differs from git, usually because someone ran
`kubectl edit` during an incident. Self-heal reverts it automatically, so git
stays the truth. During an emergency teams may pause auto-sync, fix, then
commit the fix to git.

**Q21. Is Terraform state locking really necessary for one person?**
For one person, rarely. For a team or a pipeline, yes: two concurrent applies
can corrupt the state or create duplicates. It costs nothing with S3 lock files,
so it's always on.

**Q22. Why do we need both Ansible and Terraform?**
Terraform creates infrastructure (servers, networks, permissions); Ansible
configures what's inside a server. With containers and managed services, the
Ansible part shrinks a lot, and many teams use pre-built machine images (Packer)
or managed nodes instead.

**Q23. What does a company do when a deploy goes wrong anyway?**
Roll back or forward quickly, communicate (status page), then hold a
blameless postmortem: what happened, why the safety nets didn't catch it, and
what to change. The goal is to fix the system, not blame a person.

**Q24. Will AI replace this work?**
AI already writes much of the YAML and scripts. What stays human is deciding
the strategy, judging risk, designing safe database changes, and responding
when things fail. AIOps Triage is a small example of AI *helping* an
operator, not replacing them.

**Q25. If I could add only one thing to our setup for production, what should it be?**
Separate environments with the same image promoted through them, plus HTTPS
and a managed database. After that, canary with automatic metric checks.

---

## 10. Further reading

- Google, *The Site Reliability Workbook*, chapter "Canarying Releases": https://sre.google/workbook/canarying-releases/
- Google, *Site Reliability Engineering*, chapter "Release Engineering": https://sre.google/sre-book/release-engineering/
- Amazon Builders' Library, "Automating safe, hands-off deployments": https://aws.amazon.com/builders-library/automating-safe-hands-off-deployments/
- Argo Rollouts (canary and blue/green for Kubernetes): https://argo-rollouts.readthedocs.io/
- Argo CD documentation: https://argo-cd.readthedocs.io/
- Kubernetes Deployments, rolling update strategy: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
- DORA research and the four key metrics: https://dora.dev/
- OpenFeature (feature flag standard): https://openfeature.dev/
- Martin Fowler, "BlueGreenDeployment", "CanaryRelease", "ParallelChange": https://martinfowler.com/bliki/
