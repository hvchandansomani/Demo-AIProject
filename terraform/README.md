# Terraform

Creates everything in AWS that the marathon needs. Two stacks:

| Stack | Creates | State kept in |
|---|---|---|
| `bootstrap/` | The S3 bucket that stores Terraform state (versioned, encrypted, private, HTTPS-only). Optional DynamoDB lock table | A local file (it can't store its own state in a bucket that doesn't exist yet) |
| `infra/` | VPC, subnet, security groups, IAM roles, 2 ECR repositories, Jenkins and k3s EC2 servers with Elastic IPs, a budget alarm, and the Ansible inventory | The S3 bucket from `bootstrap/`, **with locking** |

```
infra/
  versions.tf      Terraform >= 1.11, AWS provider 6.x
  backend.tf       S3 backend (settings in backend.hcl)
  variables.tf     inputs, with validation (admin_cidr can't be 0.0.0.0/0)
  network.tf       VPC, internet gateway, public subnet, routes
  security.tf      firewall rules (table at the top of the file)
  iam.tf           server roles: Jenkins can push to ECR, k3s can pull
  ecr.tf           immutable tags, scan on push, lifecycle clean-up
  ec2.tf           Ubuntu 24.04, encrypted disks, IMDSv2, Elastic IPs
  budget.tf        email alarm at 80% / 100% of the monthly limit
  inventory.tf     writes ../../ansible/inventory/hosts.ini
  outputs.tf       URLs, IPs, ECR registry, SSH commands
```

## Prerequisites

- Terraform **1.11 or newer** (`terraform version`).
- AWS CLI v2 signed in to your account (`aws sts get-caller-identity` shows your account).
- An SSH key: `ssh-keygen -t ed25519 -f ~/.ssh/marathon -N ""`.
- Your public IP: `curl -s https://checkip.amazonaws.com`.

On Windows, run everything from **WSL (Ubuntu)**; Ansible needs it anyway.

## Steps

```bash
# 1. State bucket (once per AWS account)
cd terraform/bootstrap
terraform init
terraform apply
terraform output -raw backend_hcl > ../infra/backend.hcl

# 2. Infrastructure
cd ../infra
cp terraform.tfvars.example terraform.tfvars      # set admin_cidr and budget_email
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan
terraform apply tfplan
```

`apply` prints the Jenkins URL, the app URL and the next step (Ansible).

## State locking, explained

`use_lockfile = true` (in `backend.hcl`) makes Terraform create
`terraform.tfstate.tflock` next to the state in S3 while a command runs. A
second `plan` or `apply` at the same time fails with "Error acquiring the state
lock" instead of two people overwriting each other's changes.

**See it yourself:** start `terraform apply` in one terminal and leave it at the
"Enter a value:" prompt. In a second terminal run `terraform plan`: it refuses
with the lock holder's details. Answer "no" in the first terminal and the lock
is released.

| Method | Status | How |
|---|---|---|
| S3 lock file | Recommended (Terraform 1.11+) | `use_lockfile = true` |
| DynamoDB table | Deprecated since 1.11, still works | `create_dynamodb_lock_table = true` in bootstrap, then `dynamodb_table = "marathon-tf-lock"` in `backend.hcl` |

A stuck lock (a crashed run) can be removed with `terraform force-unlock <LOCK_ID>`,
but only when you're sure nobody else is running Terraform.

## Tear down

```bash
cd terraform/infra && terraform destroy     # after every session
```

The bootstrap bucket stays (it costs a few cents a month and keeps the state
history). To remove it too: set `prevent_destroy = false` in
`bootstrap/main.tf`, `terraform apply`, empty the bucket (all versions) in the
S3 console, then `terraform destroy` in `bootstrap/`.

## Cost

While running: two servers (t3.medium + t3.large), two public IPs and 60 GB of
gp3 disk. In ap-south-1 that's roughly US$0.15 per hour in total; check the
[AWS Pricing Calculator](https://calculator.aws/) for your region. Stopped
servers still pay for disks and Elastic IPs, so destroy instead of stopping.

## Security choices

| Choice | Why |
|---|---|
| State bucket versioned, encrypted, private, HTTPS-only, `prevent_destroy` | State can contain secrets and is hard to rebuild |
| `admin_cidr` validation | SSH, Jenkins and the Kubernetes API are never opened to the internet |
| IAM roles, no access keys on servers | Nothing long-lived to leak |
| Least-privilege ECR policies | Jenkins can push only to our two repos; k3s can only pull |
| IMDSv2 required, hop limit 1 | Blocks credential theft through the metadata service; pods can't borrow the node role |
| Encrypted gp3 disks | Data at rest is encrypted |
| Immutable ECR tags + scan on push | A tag always means the same tested image |
| Default security group emptied | No accidental "allow everything inside the VPC" |
| Budget alarm | Catches forgotten servers |

[Checkov](https://www.checkov.io/) scan: 145 checks passed, 0 failed. Nine
checks are skipped on purpose; each skip has a `#checkov:skip` comment with the
reason (KMS keys, flow logs, access logs, replication, and public port 80).
They're the list of what to add for production.
