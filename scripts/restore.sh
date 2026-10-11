#!/bin/sh
# Restore a backup made by scripts/backup.sh. DESTRUCTIVE: replaces the
# database and the /data volume of the target deployment.
#
#   COMPOSE="docker compose -p toolsmith -f docker-compose.yml" scripts/restore.sh backups/<stamp> [--yes]
set -eu
cd "$(dirname "$0")/.."
compose=${COMPOSE:-docker compose}
backup=${1:?Usage: scripts/restore.sh <backup dir> [--yes]}
[ -f "$backup/manifest.json" ] || { echo "No manifest.json in $backup." >&2; exit 2; }

echo "Checking file integrity..."
(cd "$backup" && { sha256sum -c SHA256SUMS 2>/dev/null || shasum -a 256 -c SHA256SUMS; })

if [ "${2:-}" != "--yes" ]; then
  printf 'This replaces the database and all tenant data with %s. Type "restore" to continue: ' "$backup"
  read -r answer
  [ "$answer" = "restore" ] || { echo "Aborted."; exit 1; }
fi

echo "Stopping the backend (queued runs stay in the database)..."
$compose stop backend

echo "Restoring PostgreSQL..."
$compose exec -T postgres pg_restore --username toolsmith --dbname toolsmith --clean --if-exists --no-owner \
  --single-transaction < "$backup/database.dump"

echo "Restoring the data volume..."
$compose run --rm -T --no-deps --entrypoint sh backend -c 'find /data -mindepth 1 -delete && tar xzf - -C /data' \
  < "$backup/data.tar.gz"

echo "Applying newer migrations, if any..."
$compose run --rm migrate

echo "Verifying audit chains after the restore..."
$compose run --rm -T --no-deps --entrypoint python backend scripts/verify_audit.py /data

echo "Starting the backend..."
$compose up -d --wait backend
echo "Restore complete. Runs that were active at backup time were closed as failed and retried on start."
