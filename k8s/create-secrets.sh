#!/usr/bin/env bash
# Creates the "marathon-secrets" Secret that both apps and PostgreSQL read.
# Run ONCE per cluster, before the first deploy:
#
#   ./k8s/create-secrets.sh
#   ANTHROPIC_API_KEY=sk-ant-... ./k8s/create-secrets.sh     # to enable Claude mode
#
# Any value you don't pass is generated randomly. Secrets never go into git,
# the images or the Jenkinsfile.
set -euo pipefail

NS="${NS:-marathon}"
NAME="marathon-secrets"

command -v kubectl >/dev/null || { echo "kubectl is not installed or not on PATH." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is needed to generate passwords." >&2; exit 1; }

if kubectl -n "$NS" get secret "$NAME" >/dev/null 2>&1 && [ "${FORCE:-}" != "1" ]; then
  cat >&2 <<MSG
Secret $NS/$NAME already exists, so nothing was changed.

Replacing it would give the apps new database passwords that PostgreSQL
doesn't know (PostgreSQL reads them only when its volume is first created).
To replace it anyway, run with FORCE=1 and also reset the database:
  kubectl -n $NS delete statefulset postgres && kubectl -n $NS delete pvc data-postgres-0
MSG
  exit 1
fi

gen() { openssl rand -hex 24; }
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(gen)}"
DB_PASSWORD="${DB_PASSWORD:-$(gen)}"
ADMIN_TOKEN="${ADMIN_TOKEN:-$(gen)}"
WEBHOOK_TOKEN="${WEBHOOK_TOKEN:-$(gen)}"
SECRET_KEY="${SECRET_KEY:-$(gen)}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

kubectl -n "$NS" create secret generic "$NAME" \
  --from-literal=postgres-password="$POSTGRES_PASSWORD" \
  --from-literal=db-password="$DB_PASSWORD" \
  --from-literal=admin-token="$ADMIN_TOKEN" \
  --from-literal=webhook-token="$WEBHOOK_TOKEN" \
  --from-literal=secret-key="$SECRET_KEY" \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

cat <<MSG

Secret $NS/$NAME is ready.

Save these two in a password manager. You need them during the session:
  ADMIN_TOKEN   = $ADMIN_TOKEN     (console sign-in at /ops, Doubt Wall moderation)
  WEBHOOK_TOKEN = $WEBHOOK_TOKEN   (Alertmanager / SNS webhook URLs)

Read any value again later with:
  kubectl -n $NS get secret $NAME -o jsonpath='{.data.admin-token}' | base64 -d; echo
MSG
