#!/bin/sh
# Run restore.sh in a throwaway postgres:17 container on this machine.
#
# Set these in your shell first (never put them on the command line):
#   SRC_URL               Supabase session-pooler connection string (the backend's DATABASE_URL)
#   DST_URL               postgresql://postgres:<POSTGRES_PASSWORD>@<proxy host>:<proxy port>/steward
#   APP_BACKEND_PASSWORD  the Postgres service's APP_BACKEND_PASSWORD variable
# Optional: WIPE=1 (default here) drops and recreates the target's public and private schemas.
set -eu
# Values can instead live in a file outside the repo (default ~/db-move.env), one NAME=value per line.
env_file="${DB_MOVE_ENV:-$HOME/db-move.env}"
if [ -f "$env_file" ]; then set -a; . "$env_file"; set +a; fi
: "${SRC_URL:?set SRC_URL}" "${DST_URL:?set DST_URL}" "${APP_BACKEND_PASSWORD:?set APP_BACKEND_PASSWORD}"
here=$(cd "$(dirname "$0")" && pwd)
MSYS_NO_PATHCONV=1 docker run --rm \
  -e SRC_URL -e DST_URL -e APP_BACKEND_PASSWORD -e WIPE="${WIPE:-1}" \
  -v "$here:/db-move:ro" \
  --entrypoint sh postgres:17 /db-move/restore.sh
