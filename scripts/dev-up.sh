#!/bin/sh
# One command for local development with real sign-in (mock model by default).
# Usage: scripts/dev-up.sh [env file, default .env]
set -eu
cd "$(dirname "$0")/.."
env_file=${1:-.env}
sh scripts/bootstrap-env.sh "$env_file"
docker compose --env-file "$env_file" -p toolsmith-dev -f docker-compose.yml -f docker-compose.dev.yml up --build -d --wait
echo
echo "Toolsmith is running: http://localhost:4180 (sign in as dev@example.test)"
echo "Stop with: docker compose -p toolsmith-dev -f docker-compose.yml -f docker-compose.dev.yml down"
