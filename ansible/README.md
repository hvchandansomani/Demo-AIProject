# Ansible

Installs and configures the software on the two servers Terraform created.
Terraform writes `inventory/hosts.ini`, so Ansible always targets the right IPs.

| Role | Server | What it does |
|---|---|---|
| `common` | both | Hostname, base packages, automatic security updates |
| `awscli` | both | AWS CLI v2 (uses the server's IAM role, no keys) |
| `k3s` | k3s | k3s (pinned version) with Secrets encryption; waits for the node and Traefik; installs a timer that refreshes the ECR pull secret every 6 h; saves two kubeconfigs to `artifacts/` |
| `docker` | Jenkins | Docker with log-size limits and a nightly image clean-up |
| `kubectl` | Jenkins | kubectl from the official Kubernetes repository |
| `jenkins` | Jenkins | Jenkins LTS + Java 21, plugins, and Configuration as Code: admin user, `k3s-kubeconfig` credential, `ECR_REGISTRY`/`AWS_REGION`, and the `marathon` pipeline job. No setup wizard |
| `argocd` | k3s | Optional (`install_argocd: true`). Learners usually install it themselves |

## Prerequisites

- Linux, macOS or **WSL** (Ansible doesn't run natively on Windows).
- `pipx install ansible-core` (or `pip install ansible-core`), version 2.16+.
- `terraform apply` finished, so `inventory/hosts.ini` exists.

## Run it

```bash
cd ansible
# 1. Point Jenkins at YOUR fork: edit app_repo_url in group_vars/all.yml
# 2. Secrets come from environment variables, never from files in git
export JENKINS_ADMIN_PASSWORD='choose-a-long-password'
export GIT_PUSH_USER=your-github-user GIT_PUSH_TOKEN=github_pat_...   # optional, GitOps only
# 3. Go
ansible-playbook site.yml
```

It takes about 10 minutes the first time. Re-running is safe: tasks only change
what isn't already in the desired state.

At the end it prints the Jenkins URL. Sign in as `admin` with your password;
the `marathon` job is already there.

## Files it creates on your machine

| File | Use |
|---|---|
| `artifacts/kubeconfig-admin.yaml` | `export KUBECONFIG=$PWD/artifacts/kubeconfig-admin.yaml` to use kubectl from your laptop (public IP, port 6443 open to `admin_cidr` only) |
| `artifacts/kubeconfig-jenkins.yaml` | Copied into Jenkins as the `k3s-kubeconfig` credential (private IP) |

Both give full admin access to the cluster. `artifacts/` is in `.gitignore`.

## Useful variations

```bash
ansible-playbook site.yml --limit k3s-1                  # only the k3s server
ansible-playbook site.yml -e install_argocd=true         # also install Argo CD
ansible-playbook site.yml --check --diff                 # show what would change
ansible all -m ping                                      # can Ansible reach both servers?
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `UNREACHABLE ... Permission denied (publickey)` | `ansible_ssh_private_key_file` in the inventory must point at the key matching `ssh_public_key_path` in Terraform |
| `UNREACHABLE ... timed out` | Your IP changed: update `admin_cidr` in `terraform.tfvars` and `terraform apply` |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | A new server reused an old IP: `ssh-keygen -R <ip>` |
| `Set a Jenkins admin password of at least 12 characters` | `export JENKINS_ADMIN_PASSWORD=...` in the same terminal |
| Jenkins starts but shows the setup wizard | `jenkins_configure_as_code` is false, or a plugin download failed: re-run the playbook and check `sudo journalctl -u jenkins` |
| Jenkins log: `ConfiguratorException` | A setting in `roles/jenkins/templates/casc.yaml.j2` doesn't match an installed plugin; the log names the key |
| "WARNING: unprotected private key file" (WSL) | Keep the key inside WSL (`~/.ssh`), not under `/mnt/c`, and `chmod 600` it |
