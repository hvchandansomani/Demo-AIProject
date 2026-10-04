#!/usr/bin/env bash
# Learner-like traffic (mostly reads, some upvotes) so Grafana has data.
# Usage: ./scripts/load.sh [base-url] [requests-per-second]   Ctrl+C to stop
BASE="${1:-http://localhost:8000}"
RPS="${2:-10}"
echo "Sending ~${RPS} req/s to $BASE"
while true; do
  for i in $(seq "$RPS"); do
    if (( i % 10 == 0 )); then
      id=$(( (RANDOM % 5) + 1 ))
      curl -s -o /dev/null -w '%{http_code} ' -X POST -H "X-Client-Id: load-$RANDOM-$RANDOM" "$BASE/api/doubts/$id/upvote" &
    else
      curl -s -o /dev/null -w '%{http_code} ' "$BASE/api/doubts?sort=top" &
    fi
  done
  wait; echo; sleep 1
done
