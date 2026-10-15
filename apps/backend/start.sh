#!/bin/sh
# Single-container entrypoint for hosts that bill per service (e.g. Railway): the API and the
# worker run as two forked children of one launcher that imports the code once (less memory
# than two separate processes). A crashed worker is restarted after 5 seconds; if the API
# exits, the container exits and the host restarts both. See src/worker/launcher.py.
exec python -m worker.launcher
