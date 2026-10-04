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
