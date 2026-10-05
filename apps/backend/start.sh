#!/bin/sh
# Single-container entrypoint for hosts that bill per service (e.g. Railway): the API runs
# in the foreground, the worker beside it. A crashed worker is restarted after 5 seconds;
# if the API exits, the container exits and the host restarts both.
set -u

(
  while true; do
    python -m worker
    echo "worker exited with status $?, restarting in 5s" >&2
    sleep 5
  done
) &

exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
