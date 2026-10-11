#!/bin/sh
# Runs the backend and sandbox suites in the production base image, as root,
# against a throwaway PostgreSQL server on a private Docker network.
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
test_image=${BACKEND_TEST_IMAGE:-python:3.12-slim}
postgres_image=${BACKEND_TEST_POSTGRES_IMAGE:-postgres:18.6-alpine}
skip_install=${BACKEND_TEST_SKIP_INSTALL:-0}
case "$skip_install" in
  0|1) ;;
  *) printf '%s\n' 'BACKEND_TEST_SKIP_INSTALL must be 0 or 1.' >&2; exit 2 ;;
esac
suffix="$$-$(date +%s)"
network="toolsmith-test-$suffix"
database="toolsmith-test-pg-$suffix"
cleanup() {
  docker rm -f "$database" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker network create "$network" >/dev/null
docker run -d --name "$database" --network "$network" --tmpfs /var/lib/postgresql:rw \
  --env POSTGRES_PASSWORD=test --env POSTGRES_DB=toolsmith_test "$postgres_image" >/dev/null
attempt=0
# TCP readiness: the image's temporary init server listens only on a socket.
until docker exec "$database" pg_isready -q -h 127.0.0.1 -U postgres -d toolsmith_test; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    printf '%s\n' 'The test PostgreSQL server did not start.' >&2
    exit 1
  fi
  sleep 1
done
docker run --rm \
  --network "$network" \
  --mount "type=bind,source=$repo_root,target=/workspace" \
  --workdir /workspace/backend \
  --env "BACKEND_TEST_SKIP_INSTALL=$skip_install" \
  --env "TEST_DATABASE_URL=postgresql+psycopg://postgres:test@$database:5432/toolsmith_test" \
  --env REQUIRE_POSTGRES_TESTS=1 \
  --env PYTHONDONTWRITEBYTECODE=1 \
  "$test_image" sh -c '
    set -eu
    if [ "$BACKEND_TEST_SKIP_INSTALL" != 1 ]; then
      python -m pip install -r requirements.txt -r requirements-dev.txt -r ../sandbox/requirements.txt
    fi
    python -m pytest tests ../sandbox/tests "$@"
  ' sh "$@"
