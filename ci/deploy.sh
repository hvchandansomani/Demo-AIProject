#!/usr/bin/env bash
# Stage "Deploy to k3s": applies k8s/overlays/aws with this build's images,
# waits for the rollout, and rolls back automatically if it fails.
set -euo pipefail
: "${ECR_REGISTRY:?ECR_REGISTRY is not set}"
: "${AWS_REGION:?AWS_REGION is not set}"
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
NS="${NS:-marathon}"
TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"

kubectl -n "$NS" get secret marathon-secrets >/dev/null 2>&1 || {
  echo "Secret $NS/marathon-secrets not found. Run ./k8s/create-secrets.sh once against this cluster first." >&2
  exit 1
}

work="$(mktemp -d)"
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT

# 1. Refresh the image pull secret (ECR tokens expire after 12 hours).
#    The token goes through a private temp file, never on a command line,
#    so it can't be seen in the server's process list.
echo "Refreshing the ECR pull secret"
token="$(aws ecr get-login-password --region "$AWS_REGION")"
auth="$(printf 'AWS:%s' "$token" | base64 | tr -d '\n')"
printf '{"auths":{"%s":{"auth":"%s"}}}' "$ECR_REGISTRY" "$auth" > "$work/dockerconfig.json"
kubectl -n "$NS" create secret generic ecr-pull \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson="$work/dockerconfig.json" \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. Fill in the overlay's placeholders in a temporary copy (git stays clean).
cp -r k8s "$work/k8s"
overlay="$work/k8s/overlays/aws/kustomization.yaml"
sed -i "s|REPLACE_ECR_REGISTRY|$ECR_REGISTRY|g; s|REPLACE_IMAGE_TAG|$IMAGE_TAG|g" "$overlay"
if grep -q REPLACE_ "$overlay"; then
  echo "Placeholders left in the overlay; refusing to deploy." >&2
  exit 1
fi

# 3. Apply and wait.
echo "Deploying $IMAGE_TAG"
kubectl apply -k "$work/k8s/overlays/aws"
kubectl -n "$NS" rollout status statefulset/postgres --timeout="$TIMEOUT"

failed=""
for d in doubt-wall aiops-triage; do
  kubectl -n "$NS" rollout status "deployment/$d" --timeout="$TIMEOUT" || failed="$failed $d"
done

# 4. Roll back what didn't come up. With maxUnavailable: 0 the old pods kept
#    serving the whole time, so learners never saw the broken version.
if [ -n "$failed" ]; then
  echo "Rollout failed for:$failed. Rolling back." >&2
  kubectl -n "$NS" get pods -o wide >&2 || true
  kubectl -n "$NS" get events --sort-by=.lastTimestamp 2>/dev/null | tail -20 >&2 || true
  for d in $failed; do
    kubectl -n "$NS" rollout undo "deployment/$d" || echo "No previous revision of $d to roll back to." >&2
  done
  exit 1
fi

kubectl -n "$NS" get pods -o wide
echo "Deployed $IMAGE_TAG"
