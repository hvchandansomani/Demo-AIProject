#!/usr/bin/env bash
# Stage "Security scan": checks each image for known vulnerabilities (Trivy).
#
# Two security choices worth explaining in class:
#  1. Trivy is PINNED. Versions 0.69.4-0.69.6, and the "latest" tag during
#     19-23 March 2026, were malicious builds that stole CI secrets
#     (advisory GHSA-69fq-xp46-6x23). 0.69.3 is the last known clean release.
#     Read the advisory before changing this version. Never use "latest".
#  2. Trivy never gets the Docker socket. The image is exported to a file and
#     scanned from there. Mounting /var/run/docker.sock would give the scanner
#     full control of the build server.
set -euo pipefail
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
REGISTRY="${ECR_REGISTRY:-local}"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:0.69.3}"
SEVERITY="${TRIVY_SEVERITY:-CRITICAL}"
EXIT_CODE=1
[ "${TRIVY_FAIL:-true}" = "true" ] || EXIT_CODE=0

mkdir -p reports .trivy-cache
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

status=0
for app in doubt-wall aiops-triage; do
  ref="$REGISTRY/$app:$IMAGE_TAG"
  echo "=== Scanning $ref (severity: $SEVERITY, unfixed ignored) ==="
  docker save "$ref" -o "$tmp/$app.tar"
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$tmp:/scan:ro" -v "$PWD/.trivy-cache:/cache" -v "$PWD/reports:/reports" \
    "$TRIVY_IMAGE" image \
      --cache-dir /cache --input "/scan/$app.tar" \
      --severity "$SEVERITY" --ignore-unfixed --no-progress \
      --exit-code "$EXIT_CODE" --format table --output "/reports/trivy-$app.txt" \
    || status=1
  cat "reports/trivy-$app.txt" 2>/dev/null || true
done

if [ "$status" -ne 0 ]; then
  echo "Trivy found fixable $SEVERITY vulnerabilities (or failed to run). See reports/trivy-*.txt." >&2
fi
exit "$status"
