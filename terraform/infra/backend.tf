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
