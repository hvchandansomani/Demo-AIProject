#!/usr/bin/env bash
# Stage "Test": runs both test suites inside throwaway containers, so the
# Jenkins server needs only Docker (no Python installed on it).
# The Doubt Wall suite gets a real, temporary PostgreSQL; REQUIRE_DB=1 makes
# it fail (instead of silently skipping) if that database isn't reachable.
set -euo pipefail

PY_IMAGE="${PY_IMAGE:-python:3.12-slim}"
PG_IMAGE="${PG_IMAGE:-postgres:18-alpine}"
ID="$(printf '%s' "${BUILD_TAG:-local}-$$" | tr -c 'a-zA-Z0-9_.-' '-')"
NET="ci-net-$ID"
PG="ci-pg-$ID"

cleanup() {
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p reports
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER=doubtwall -e POSTGRES_PASSWORD=doubtwall -e POSTGRES_DB=doubtwall_test \
  "$PG_IMAGE" >/dev/null

echo "Waiting for PostgreSQL..."
for i in $(seq 1 60); do
  # -h 127.0.0.1: the image's temporary setup server listens only on a unix
  # socket, so a TCP check passes only once the real server is up.
  if docker exec "$PG" pg_isready -h 127.0.0.1 -U doubtwall -d doubtwall_test >/dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "PostgreSQL did not become ready in 60 s." >&2
    docker logs "$PG" >&2
    exit 1
  fi
  sleep 1
done

# Source is mounted read-only; results go to ./reports for Jenkins (JUnit).
# Running as the Jenkins user's UID keeps report files deletable by Jenkins.
run_suite() {
  local app="$1"; shift
  echo "=== Testing $app ==="
  docker run --rm --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -v "$PWD/$app:/src:ro" -v "$PWD/reports:/reports" -w /src \
    "$@" "$PY_IMAGE" \
    sh -c "pip install --user --quiet -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider --junitxml=/reports/$app.xml"
}

run_suite aiops-triage
run_suite doubt-wall --network "$NET" \
  -e "TEST_DATABASE_URL=postgresql://doubtwall:doubtwall@$PG:5432/doubtwall_test" \
  -e REQUIRE_DB=1
echo "All tests passed."
