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
