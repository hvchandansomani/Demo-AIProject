#!/usr/bin/env bash
# Fire the sample Alertmanager and CloudWatch payloads at a running console.
# Usage: WEBHOOK_TOKEN=... ./scripts/send-sample-alerts.sh [base-url]
#   base-url defaults to http://localhost:8080/ops (the docker compose setup)
set -euo pipefail
BASE="${1:-http://localhost:8080/ops}"
Q=""
[ -n "${WEBHOOK_TOKEN:-}" ] && Q="?token=${WEBHOOK_TOKEN}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "-> Alertmanager webhook"
curl -sS -X POST "$BASE/webhook/alertmanager$Q" \
  -H 'Content-Type: application/json' --data @"$DIR/samples/alertmanager.json"
echo

echo "-> CloudWatch (SNS) webhook"
curl -sS -X POST "$BASE/webhook/cloudwatch$Q" \
  -H 'Content-Type: text/plain' -H 'x-amz-sns-message-type: Notification' \
  --data @"$DIR/samples/cloudwatch-sns.json" | head -c 300
echo
echo "Open $BASE to see the diagnoses."
