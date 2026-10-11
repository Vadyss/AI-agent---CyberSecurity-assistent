#!/bin/sh
# Ensures an env file (default .env) holds the secrets the stack needs. Missing
# secrets are generated; existing values are never changed or printed.
# Production secrets belong in your deployment's secret store.
set -eu
cd "$(dirname "$0")/.."
file=${1:-.env}
umask 077
[ -e "$file" ] || cp .env.example "$file"
random() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }
ensure() {
  if grep -q "^$1=." "$file"; then
    return
  fi
  # Drop an empty placeholder line, then append the generated value.
  grep -v "^$1=\$" "$file" > "$file.tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$file.tmp"
  mv "$file.tmp" "$file"
  echo "Generated $1 in $file."
}
ensure POSTGRES_PASSWORD "$(random 24)"
# 32 hex characters = 32 bytes, the cookie key length oauth2-proxy expects.
ensure OAUTH2_PROXY_COOKIE_SECRET "$(random 16)"
