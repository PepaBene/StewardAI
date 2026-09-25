# Always-on host

The API and the worker run on one small always-on host (Phase 1b: a dev host; Phase 6: the
live host with a static IP). Two ways to run them, both with "restart always":

| File | What it is |
| --- | --- |
| `compose.yaml` | Docker Compose stack: `api` and `worker` from one image (`apps/backend/Dockerfile`), `restart: always`, health checks |
| `systemd/steward-api.service`, `systemd/steward-worker.service` | The same two processes as systemd units (`Restart=always`, `RestartSec=5`, no start limit) |

## Docker Compose

```bash
cp .env.example .env                  # repository root; fill in the [backend] values
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml ps       # both "healthy" after ~1 minute
curl -s localhost:8000/health/ready            # {"status":"ok", ... "worker_alive":true}
```

The API listens on `127.0.0.1:8000`; put a TLS reverse proxy (Caddy, nginx) or a tunnel in
front of it and set `NEXT_PUBLIC_API_BASE_URL` on Vercel to its public URL, and
`CORS_ORIGINS` here to the Vercel URL(s).

- **Restart.** A killed or crashed process exits its container and Docker starts it again
  (tested: back within about 1 second after `kill -9` of either process).
- **Health.** `api` is unhealthy when `GET /health` stops answering; `worker` is unhealthy
  when its heartbeat row (written every 30 s) is older than 3 minutes
  (`python -m steward.heartbeat_check`).

## systemd

```bash
sudo useradd --system --home /opt/stewardai steward
sudo git clone <repo> /opt/stewardai && cd /opt/stewardai/apps/backend
sudo -u steward uv sync --locked --no-dev
sudo mkdir -p /etc/stewardai && sudo install -m 600 /dev/null /etc/stewardai/env  # then write the [backend] values
sudo cp /opt/stewardai/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now steward-api steward-worker
```

## External uptime check

Set up a free external monitor (UptimeRobot, Better Stack, healthchecks.io or similar) so an
outage alerts even when the host itself is gone:

1. **HTTP monitor on `https://<api host>/health`**, every 1 minute, alert after 2 failures:
   catches a stopped API or an unreachable host within about 3 minutes (the Phase 1b AC is
   5 minutes).
2. **HTTP monitor on `https://<api host>/health/ready`** (expects 200): also catches a lost
   database or a stuck or stopped worker (503 with the reason in the body).
3. Optional dead-man's switch: create a healthchecks.io check with a 2-minute period and set
   `HEARTBEAT_PING_URL` to its ping URL. The worker pings it with every heartbeat, so the
   check alerts when the worker stops, even if the API is still up.

## Vercel (web app)

Import the repository in Vercel with **Root Directory `apps/web`** (framework preset
Next.js). Set the `NEXT_PUBLIC_*` variables from `.env.example` in Project Settings →
Environment Variables, for Preview and Production. Every push to a branch gets a preview
deployment; `main` deploys to production (Phase 6).

## Phase 1 sign-off

After the host has run for 10 days:

```bash
cd apps/backend && uv run python -m steward.signoff --runs 10
```

It checks the consecutive scheduled syncs, one snapshot row per position per day, the
3-positions-in-3-currencies precondition and Trading212's total against its positions plus
cash, and prints the totals to compare with the Trading212 app. The Realtime check (a sync
started in a second tab updates the first within 2 seconds) is manual.
