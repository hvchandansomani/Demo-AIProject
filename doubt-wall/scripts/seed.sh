#!/usr/bin/env bash
# Put a few realistic doubts on the wall for a rehearsal or a screenshot.
# Usage: ./scripts/seed.sh [base-url]
set -euo pipefail
BASE="${1:-http://localhost:8000}"
post() {
  curl -sS -o /dev/null -w "%{http_code} " -X POST "$BASE/api/doubts" \
    -H 'Content-Type: application/json' -H "X-Client-Id: seed-$RANDOM-$RANDOM" \
    -d "{\"nickname\":\"$1\",\"question\":\"$2\"}"
}
post "Priya"  "Why does my pod go into CrashLoopBackOff right after a deploy?"
post "Rahul"  "What is the difference between a Deployment and a StatefulSet?"
post "Asha"   "Terraform says the resource already exists. How do I import it into state?"
post "Vikram" "Why do we need Ansible if Terraform can run scripts with user_data?"
post "Neha"   "How does the HPA decide how many replicas to add?"
echo; echo "Seeded. Open $BASE"
