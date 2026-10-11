#!/bin/sh
# Back up a Toolsmith deployment: PostgreSQL (pg_dump custom format) and the
# /data volume (tool registries, rules, audit chains, usage files).
#
#   COMPOSE="docker compose -p toolsmith -f docker-compose.yml" scripts/backup.sh [output dir]
#
# Backups contain tenant data. They are written with mode 600; encrypt them
# (for example with age or gpg) before they leave the host.
set -eu
cd "$(dirname "$0")/.."
compose=${COMPOSE:-docker compose}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out=${1:-backups/$stamp}
umask 077
mkdir -p "$out"

echo "Verifying audit chains before the backup..."
$compose run --rm -T --no-deps --entrypoint python backend scripts/verify_audit.py /data > "$out/audit-verification.txt"

echo "Dumping PostgreSQL..."
$compose exec -T postgres pg_dump --username toolsmith --dbname toolsmith --format custom --no-owner > "$out/database.dump"
revision=$($compose exec -T postgres psql --username toolsmith --dbname toolsmith --tuples-only --no-align \
  --command "SELECT version_num FROM alembic_version")

echo "Archiving the data volume..."
$compose run --rm -T --no-deps --entrypoint tar backend czf - -C /data . > "$out/data.tar.gz"

version=$($compose run --rm -T --no-deps --entrypoint printenv backend APP_VERSION || echo unknown)
sums=$(cd "$out" && sha256sum database.dump data.tar.gz audit-verification.txt 2>/dev/null || shasum -a 256 database.dump data.tar.gz audit-verification.txt)
printf '%s\n' "$sums" > "$out/SHA256SUMS"
cat > "$out/manifest.json" <<JSON
{"created_at": "$stamp", "schema_revision": "$(echo "$revision" | tr -d '[:space:]')", "app_version": "$(echo "$version" | tr -d '[:space:]')", "files": ["database.dump", "data.tar.gz", "audit-verification.txt"]}
JSON
echo "Backup written to $out"
