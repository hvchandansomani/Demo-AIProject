#!/usr/bin/env bash
# Stage "Update GitOps repo" (DEPLOY_MODE=gitops): instead of deploying,
# record the new image tag in git. Argo CD notices the commit and deploys it.
#
#   1. Start from the latest commit on the GitOps branch (not Jenkins' copy,
#      which may be behind if someone pushed meanwhile).
#   2. Change only k8s/overlays/gitops/kustomization.yaml.
#   3. Commit with "[skip ci]" so this commit doesn't trigger another build.
#   4. Push using a token from Jenkins credentials. The token is handed to git
#      through GIT_ASKPASS, never put in the URL, so it can't leak into logs
#      or .git/config.
set -euo pipefail
: "${ECR_REGISTRY:?ECR_REGISTRY is not set}"
: "${IMAGE_TAG:?IMAGE_TAG is not set}"
: "${GIT_USER:?GIT_USER is not set (Jenkins credential git-push)}"
: "${GIT_TOKEN:?GIT_TOKEN is not set (Jenkins credential git-push)}"
BRANCH="${GITOPS_BRANCH:-main}"
FILE="k8s/overlays/gitops/kustomization.yaml"

work="$(mktemp -d)"
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT
cat > "$work/askpass.sh" <<'ASKPASS'
#!/bin/sh
case "$1" in
  Username*) printf '%s\n' "$GIT_USER" ;;
  *)         printf '%s\n' "$GIT_TOKEN" ;;
esac
ASKPASS
chmod 700 "$work/askpass.sh"
export GIT_ASKPASS="$work/askpass.sh" GIT_TERMINAL_PROMPT=0

git fetch --quiet origin "$BRANCH"
git checkout --quiet -B gitops-update "origin/$BRANCH"

# Update the registry, both image tags and the version shown in the UI.
# ("#" separates the sed parts, because "|" is used inside the pattern.)
sed -i -E \
  -e "s#(newName: )[^/]+/(doubt-wall|aiops-triage)\$#\1${ECR_REGISTRY}/\2#" \
  -e "s#(newTag: ).*#\1${IMAGE_TAG}#" \
  -e "s#(APP_VERSION=).*#\1${IMAGE_TAG}#" \
  "$FILE"

if grep -q REPLACE_ "$FILE"; then
  echo "Placeholders are still in $FILE; refusing to commit." >&2
  exit 1
fi

if git diff --quiet -- "$FILE"; then
  echo "$FILE already points at $IMAGE_TAG; nothing to commit."
  exit 0
fi

git -c user.name="Jenkins" -c user.email="jenkins@marathon.local" \
  commit --quiet -m "[skip ci] Deploy ${IMAGE_TAG}" -- "$FILE"
git push --quiet origin "HEAD:${BRANCH}"
echo "Pushed ${IMAGE_TAG} to ${BRANCH}. Argo CD will deploy it (within ~3 minutes, or press Refresh in the Argo CD UI)."
git --no-pager show --stat --oneline HEAD
