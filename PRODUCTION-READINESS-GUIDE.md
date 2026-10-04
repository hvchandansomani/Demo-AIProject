# Production Readiness Guide

What to check **before** you build or deploy an application, how to choose a
tech stack and justify it, and where to read more. Written for moving from
"small demo in a safe lab" to "real system in production".

Use it three ways:
1. **Before a project:** walk the checklist in Part A and write down your answers.
2. **When choosing tools:** use the scoring method and decision record in Part B.
3. **For learning:** follow the reading list in Part D, free resources first.

---

## Part A: The pre-build and pre-deploy checklist

Work top to bottom. Each stage feeds the next: you can't choose a database
before you know your data, and you can't set alerts before you know your
reliability target.

### A1. Understand the need (before any technology)

| Question | Why it matters |
|---|---|
| What problem does this solve, and for whom? | Stops you building features nobody needs |
| How many users now, and in one year? Peak vs average? | Drives capacity, cost and architecture |
| What happens if it's down for 5 minutes? For a day? | Sets the reliability target (A3) |
| What data does it hold? Any personal, payment or health data? | Decides security and compliance work (A5) |
| Who will run it at 3 a.m.? | A system nobody can operate isn't production-ready |
| What's the budget per month? | Rules out options early |
| Deadline and team size? | Favours familiar tools over ideal ones |

📖 *Read:* The Twelve-Factor App (principles); AWS Well-Architected, Operational Excellence pillar.

### A2. Non-functional requirements (NFRs)

Write these down as numbers, not adjectives. "Fast" is not a requirement; "p95 under 300 ms" is.

| NFR | Example target |
|---|---|
| Availability | 99.9% per month (about 43 min downtime allowed) |
| Latency | p95 < 300 ms, p99 < 1 s |
| Throughput | 200 requests/s at peak |
| Data durability | No data loss (RPO = 0) or "lose at most 15 min" (RPO = 15 min) |
| Recovery time | Back online within 30 min (RTO = 30 min) |
| Scalability | Handle 10× today's load with config changes only |
| Security | OWASP ASVS Level 1 or 2 |
| Compliance | e.g. GDPR, India's DPDP Act, PCI DSS, HIPAA, if they apply |
| Cost | Under ₹X / $X per month at expected load |

📖 *Read:* Google SRE Book, chapters on SLOs; *Designing Data-Intensive Applications*, chapter 1.

### A3. Reliability targets (SLIs, SLOs, error budgets)

| Step | Example |
|---|---|
| Pick **SLIs** (what you measure) | Share of requests that succeed; share faster than 300 ms |
| Set **SLOs** (the target) | 99.9% succeed over 30 days |
| Derive the **error budget** | 0.1% of requests may fail; if it's spent, freeze risky releases |
| Decide what **pages** a human vs what goes to a ticket | Page on fast budget burn; ticket on slow burn |

📖 *Read:* The Site Reliability Workbook, "Implementing SLOs" and "Alerting on SLOs".

### A4. Architecture and tech stack

Covered in detail in Part B. Minimum outputs:
- A diagram at C4 "Context" and "Container" level.
- One **Architecture Decision Record (ADR)** per major choice: language, database, hosting, CI/CD, observability.
- A list of every external dependency, with what happens when it's down.

📖 *Read:* C4 model; ADR templates; *Fundamentals of Software Architecture*.

### A5. Security (design it in, don't bolt it on)

| Area | Check |
|---|---|
| Threat model | What are you protecting, from whom, and how could they get in? (STRIDE is a simple method) |
| Identity and access | Real user authentication (SSO/OIDC), roles, least privilege for people *and* services |
| Secrets | In a secrets manager (AWS Secrets Manager, Vault, Kubernetes Secrets with encryption at rest). Never in git, images or CI logs. Rotation plan |
| Network | Only the load balancer is public; databases in private subnets; security groups deny by default |
| Encryption | TLS everywhere users connect; encryption at rest for disks, databases, backups |
| Application | OWASP Top 10 covered; input validation; parameterised queries; output escaping; rate limiting |
| Dependencies | Pinned versions, vulnerability scanning (Dependabot, Trivy, Snyk), SBOM |
| Containers | Non-root, minimal base image, read-only filesystem, image scanning, signed images |
| Kubernetes | RBAC, NetworkPolicies, Pod Security Standards ("restricted"), no privileged pods |
| Cloud account | MFA, no root-account usage, CloudTrail on, budget alarms, separate accounts for prod and non-prod |
| Audit | Who did what, when: app audit logs, cloud audit logs |
| AI features | Prompt injection, data leakage, cost limits, human review: OWASP Top 10 for LLM Apps |

📖 *Read:* OWASP Top 10:2025; OWASP ASVS 5.0; OWASP Cheat Sheet Series; CIS Benchmarks; NSA/CISA Kubernetes Hardening Guide; *Building Secure and Reliable Systems*.

### A6. Data

| Check | Detail |
|---|---|
| Data model fits the store | Relational? Documents? Key-value? Time series? (Part B) |
| Managed or self-hosted | Managed (RDS, Cloud SQL) gives backups, patching and failover; self-hosted gives control and more work |
| Backups | Automated, encrypted, stored elsewhere, **restore tested regularly** (an untested backup isn't a backup) |
| High availability | Replica or multi-AZ; what happens on failover |
| Schema changes | Migration tool (Flyway, Alembic, Liquibase); backwards-compatible changes for zero-downtime deploys |
| Least privilege | Separate roles: owner/migrations vs app runtime (read/write only) |
| Guardrails | Connection pool limits, statement timeouts, `CHECK` constraints, row limits on queries |
| Retention and deletion | How long data is kept; how a user's data is deleted on request |
| PII | Know where it is; mask it in logs and non-prod copies |

📖 *Read:* *Designing Data-Intensive Applications* (2nd ed.); *Database Reliability Engineering*; PostgreSQL documentation, "Server Administration".

### A7. Reliability and resilience

| Check | Detail |
|---|---|
| No single points of failure | At least 2 replicas, across availability zones for anything critical |
| Health checks | Separate liveness and readiness; readiness checks real dependencies |
| Timeouts and retries | Every outbound call has a timeout; retries with backoff and jitter; no infinite retries |
| Circuit breakers / fallbacks | Degrade gracefully when a dependency fails (like the rules fallback when Claude is down) |
| Graceful shutdown | Finish in-flight requests on SIGTERM; `preStop` hooks |
| Resource limits | CPU and memory requests/limits; autoscaling rules |
| Load test | Prove the NFRs from A2 before launch |
| Chaos / game days | Break things on purpose in a safe window and practise recovery |
| Disaster recovery | Documented and rehearsed restore of the whole system in another region or account |

📖 *Read:* *Release It!* (stability patterns); Google SRE Book; Mercari production-readiness checklist.

### A8. Observability

| Signal | Minimum |
|---|---|
| Metrics | RED (Rate, Errors, Duration) per endpoint; USE (Utilisation, Saturation, Errors) per resource |
| Logs | Structured (JSON), with request IDs; no secrets or PII; central store with retention |
| Traces | Distributed tracing across services (OpenTelemetry) once you have more than one service |
| Dashboards | One per service, showing its SLOs first |
| Alerts | On SLO burn and user-visible symptoms, not on every CPU blip; every alert has a runbook |
| Business metrics | Sign-ups, orders, doubts posted: whatever proves the product works |

📖 *Read:* *Observability Engineering*; Google SRE Book, "Monitoring Distributed Systems"; OpenTelemetry docs.

### A9. Delivery (CI/CD)

| Check | Detail |
|---|---|
| Everything in git | Code, infrastructure (Terraform), config (Ansible/Helm), dashboards, alert rules |
| Pipeline gates | Tests, lint, security scans, image scan, then deploy |
| Environments | At least dev, staging, prod, built the same way from the same code |
| Deployment strategy | Rolling, blue/green or canary, with automatic rollback on failed health checks |
| Supply chain | Signed artifacts, provenance (SLSA), protected branches, reviewed changes |
| Change tracking | Every production change is traceable to a commit and a person |

📖 *Read:* *Continuous Delivery*; *Accelerate*; DORA research; SLSA; NIST SSDF (SP 800-218).

### A10. Cost

| Check | Detail |
|---|---|
| Estimate before building | AWS Pricing Calculator (or equivalent) at expected and 10× load |
| Budgets and alerts | AWS Budgets / billing alarms from day one |
| Tagging | Every resource tagged with owner, environment and project |
| Right-sizing | Review usage monthly; autoscale down at night for non-prod |
| Hidden costs | Data transfer, NAT gateways, log storage, idle load balancers, AI tokens |

📖 *Read:* AWS Well-Architected, Cost Optimization pillar; FinOps Foundation framework.

### A11. Operations and people

| Check | Detail |
|---|---|
| Ownership | A named team owns the service |
| On-call | Rota, escalation path, paging tool |
| Runbooks | For every alert and common task (restart, restore, rotate secret, scale) |
| Incident process | Severity levels, incident commander role, blameless postmortems |
| Documentation | README, architecture diagram, ADRs, runbooks, all kept current |
| Launch review | A production readiness review (PRR) sign-off before go-live |

📖 *Read:* Google SRE Book, "Managing Incidents" and "Postmortem Culture"; *The DevOps Handbook*.

### A12. AI-specific (for LLM features like AIOps Triage)

| Check | Detail |
|---|---|
| Prompt injection | Treat all user or alert text as untrusted; never let model output run commands unreviewed |
| Data sent to the model | Know what leaves your system; strip secrets and PII |
| Cost controls | Max tokens, input caps, rate limits, per-day spend alerts |
| Reliability | Timeouts, retries, and a non-AI fallback path |
| Output validation | Structured output (JSON), schema checks, safe rendering |
| Evaluation | A test set of real inputs with expected answers, run on every prompt or model change |
| Human in the loop | AI suggests; humans approve actions that change systems |

📖 *Read:* OWASP Top 10 for LLM Applications; *AI Engineering* (Chip Huyen).

---

## Part B: How to choose a tech stack

### B1. Start from constraints, not from tools

Order of questions:
1. **What shape is the data and the workload?** (A1, A2)
2. **What does the team already know well?** Familiar and boring usually beats new and exciting.
3. **Is there a managed service?** Paying a cloud provider to run it is often cheaper than your team's time.
4. **Can we operate it at 3 a.m.?** Monitoring, backups, upgrades, docs, community answers.
5. **What does it cost at 10× scale?**
6. **How hard is it to leave?** (lock-in)

### B2. Score the options

Rate each candidate 1–5 per criterion, multiply by the weight you choose, and add up.

| Criterion | Weight (example) | What to ask |
|---|---|---|
| Fits the problem | 5 | Does it match the data model and access patterns? |
| Team skill | 4 | Can the team build and debug it now? |
| Operability | 4 | Backups, monitoring, upgrades, failure behaviour |
| Maturity and community | 3 | Years in production, release cadence, Stack Overflow answers |
| Security record | 3 | CVE history, patch speed, hardening guides |
| Managed option | 3 | RDS, Cloud SQL, Atlas, etc. |
| Cost (licence + infra + people) | 3 | Total, not just the licence |
| Licence | 2 | Open source licence changes have happened (e.g. Terraform → OpenTofu fork) |
| Lock-in / exit cost | 2 | Standard interfaces (SQL, S3 API, Kubernetes) lower it |
| Hiring | 2 | Can you find people who know it? |

**Worked example: database for the Doubt Wall** (using a subset of the criteria)

| Criterion (weight) | PostgreSQL | MongoDB | DynamoDB |
|---|---|---|---|
| Fits problem (5) | 5 → 25 | 4 → 20 | 3 → 15 |
| Team skill (4) | 4 → 16 | 3 → 12 | 2 → 8 |
| Operability on a small shared node (4) | 5 → 20 | 3 → 12 | 5 → 20 |
| Maturity (3) | 5 → 15 | 4 → 12 | 5 → 15 |
| Managed option (3) | 5 → 15 | 4 → 12 | 5 → 15 |
| Cost (3) | 5 → 15 | 4 → 12 | 4 → 12 |
| Lock-in (2) | 5 → 10 | 4 → 8 | 2 → 4 |
| **Total** | **116** | 88 | 89 |

The numbers matter less than writing the reasons down.

### B3. Record the decision (ADR)

Keep one short file per decision in `docs/adr/`:

```markdown
# ADR 001: Use PostgreSQL for the Doubt Wall

Status: Accepted (2026-09-27)

## Context
One small structured table, shared node with Prometheus, learners know SQL,
take-home moves to AWS RDS.

## Decision
PostgreSQL 18 with psycopg 3 and a connection pool.

## Alternatives considered
MongoDB (memory use on a shared node), DynamoDB (AWS lock-in, hides the DB lesson).

## Consequences
+ Familiar SQL, low memory, direct path to RDS.
- Needs a StatefulSet and backups in Kubernetes; schema changes need care.
```

### B4. Use industry signals, carefully

- **ThoughtWorks Technology Radar**: which tools are "Adopt", "Trial", "Assess" or "Hold", with reasons.
- **CNCF Landscape and project maturity levels** (Sandbox, Incubating, Graduated) for cloud-native tools.
- **DB-Engines ranking** for database popularity trends.
- Vendor benchmarks are marketing; test with your own workload.

---

## Part C: Classroom demo vs production

| Area | Our marathon (safe lab) | Production |
|---|---|---|
| Users | ~60 learners, 2 days | Real customers, 24/7 |
| Access | One shared admin token | SSO, per-person roles, MFA |
| Transport | HTTP | HTTPS everywhere, HSTS |
| Secrets | `.env` / Kubernetes Secrets | Secrets manager with rotation and audit |
| Database | One PostgreSQL pod on a PV | Managed (RDS), multi-AZ, automated tested backups, split roles |
| Cluster | k3s on one EC2 | EKS or similar, multi-AZ node groups, autoscaling |
| Environments | One | Dev, staging, prod (separate accounts) |
| Deploy | Jenkins → rolling update | Canary or blue/green, automatic rollback, change approvals |
| Security scans | Planned | Mandatory pipeline gates, SBOM, signed images |
| Monitoring | Prometheus/Grafana/CloudWatch, a few alerts | SLO-based alerts, runbooks, on-call paging, log retention policy |
| Incidents | Instructor fixes live | Incident process, postmortems |
| Compliance | None | GDPR / DPDP / PCI / HIPAA as applicable, audits |
| Cost control | Tear down after class | Budgets, tagging, monthly reviews |

---

## Part D: Where to read more

### D1. Free official frameworks and checklists (start here)

| Resource | What it gives you | Link |
|---|---|---|
| **AWS Well-Architected Framework** | Six pillars: operational excellence, security, reliability, performance efficiency, cost optimization, sustainability; with review questions | https://aws.amazon.com/architecture/well-architected/ |
| Azure Well-Architected / Google Cloud Architecture Framework | Same idea for other clouds | https://learn.microsoft.com/azure/well-architected/ · https://cloud.google.com/architecture/framework |
| **The Twelve-Factor App** | Twelve principles for cloud-native apps (config in env, stateless processes, logs as streams) | https://12factor.net/ |
| **Google SRE books** (free online) | *Site Reliability Engineering*, *The Site Reliability Workbook*, *Building Secure and Reliable Systems* | https://sre.google/books/ |
| **Mercari production readiness checklist** | A real company's checklist for microservices | https://github.com/mercari/production-readiness-checklist |
| Gruntwork production readiness checklist | AWS-focused go-live checklist | https://gruntwork.io/devops-checklist/ |
| learnk8s Kubernetes production best practices | Practical Kubernetes checklist | https://learnkube.com/production-best-practices |
| Kubernetes docs: production environment and security checklists | Official guidance | https://kubernetes.io/docs/setup/production-environment/ · https://kubernetes.io/docs/concepts/security/ |
| **DORA** | Research on what makes delivery teams effective; the four key metrics | https://dora.dev/ |
| C4 model | Simple, consistent architecture diagrams | https://c4model.com/ |
| Architecture Decision Records | ADR templates and examples | https://adr.github.io/ |
| ThoughtWorks Technology Radar | Opinionated tool and technique guidance, twice a year | https://www.thoughtworks.com/radar |
| CNCF Landscape | Map of cloud-native tools and their maturity | https://landscape.cncf.io/ |

### D2. Free security standards

| Resource | Use it for | Link |
|---|---|---|
| **OWASP Top 10:2025** | The ten most critical web app risks, current edition | https://owasp.org/Top10/2025/ |
| **OWASP ASVS 5.0** | A detailed, testable security requirements checklist (Levels 1–3) | https://owasp.org/www-project-application-security-verification-standard/ |
| OWASP Cheat Sheet Series | How-to guides: authentication, sessions, SQL injection, Docker, Kubernetes, secrets | https://cheatsheetseries.owasp.org/ |
| **OWASP Top 10 for LLM Applications** | Risks for AI features (prompt injection, data leakage, excessive agency) | https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/ (a 2026 update is also published on the same site) |
| CIS Benchmarks | Hardening settings for Linux, Docker, Kubernetes, PostgreSQL, AWS | https://www.cisecurity.org/cis-benchmarks |
| NSA/CISA Kubernetes Hardening Guide | Government hardening guidance for clusters | https://www.cisa.gov/news-events/alerts/2022/03/15/updated-kubernetes-hardening-guide |
| SLSA | Software supply chain security levels | https://slsa.dev/ |
| NIST SSDF (SP 800-218) | Secure software development practices | https://csrc.nist.gov/pubs/sp/800/218/final |

### D3. Books (mostly paid; O'Reilly subscription covers most)

**Read first** (the foundation, in this order)

| Book | Authors | Why |
|---|---|---|
| *Site Reliability Engineering* (free online) | Beyer, Jones, Petoff, Murphy (Google) | How production is really run: SLOs, monitoring, incidents |
| *Release It!* (2nd ed.) | Michael Nygard | Why systems fail in production and the stability patterns that prevent it |
| *Designing Data-Intensive Applications* (2nd ed., 2026) | Martin Kleppmann, Chris Riccomini | How to choose and reason about databases, replication, consistency |
| *Accelerate* | Forsgren, Humble, Kim | Evidence for what good delivery looks like |

**Architecture and stack selection**

| Book | Authors |
|---|---|
| *Fundamentals of Software Architecture* | Mark Richards, Neal Ford |
| *Software Architecture: The Hard Parts* | Ford, Richards, Sadalage, Dehghani |
| *Production-Ready Microservices* | Susan J. Fowler |
| *System Design Interview* (vol. 1 & 2) | Alex Xu (good for scaling patterns) |

**Delivery and DevOps**

| Book | Authors |
|---|---|
| *The DevOps Handbook* (2nd ed.) | Kim, Humble, Debois, Willis, Forsgren |
| *Continuous Delivery* | Jez Humble, David Farley |
| *Terraform: Up & Running* (3rd ed.) | Yevgeniy Brikman |
| *Ansible for DevOps* | Jeff Geerling |
| *The Phoenix Project* (a novel, easy start) | Kim, Behr, Spafford |

**Kubernetes and containers**

| Book | Authors |
|---|---|
| *Kubernetes: Up and Running* | Burns, Beda, Hightower, Evenson |
| *Production Kubernetes* | Rosso, Lander, Brand, Harris |
| *Container Security* | Liz Rice |

**Security**

| Book | Authors |
|---|---|
| *Building Secure and Reliable Systems* (free online) | Adkins et al. (Google) |
| *Threat Modeling: Designing for Security* | Adam Shostack |

**Data and observability**

| Book | Authors |
|---|---|
| *Database Reliability Engineering* | Laine Campbell, Charity Majors |
| *Observability Engineering* | Majors, Fong-Jones, Miranda |
| *Prometheus: Up & Running* | Julien Pivotto, Brian Brazil |

**AI in production**

| Book | Authors |
|---|---|
| *AI Engineering* | Chip Huyen |

### D4. A suggested reading path

| Weeks | Focus | Read |
|---|---|---|
| 1–2 | Principles | Twelve-Factor App; AWS Well-Architected (skim all six pillars) |
| 3–5 | Reliability | SRE Book: Part II and chapters on monitoring, incidents, postmortems |
| 6–7 | Security | OWASP Top 10:2025 and ASVS Level 1; OWASP Docker and Kubernetes cheat sheets |
| 8–10 | Failure patterns | *Release It!* |
| 11–14 | Data | *Designing Data-Intensive Applications* chapters 1–3 and replication |
| 15–16 | Practice | Run the Mercari checklist against one of your own projects; write three ADRs |
| Ongoing | Delivery and teams | *Accelerate*, *The DevOps Handbook*, DORA reports |

---

*Links checked September 2026. Standards are revised over time (OWASP Top 10,
ASVS, CIS Benchmarks), so check the version number when you open them.*
