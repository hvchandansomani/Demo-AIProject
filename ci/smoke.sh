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
