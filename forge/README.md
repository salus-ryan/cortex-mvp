# Cortex Forge

A minimal self-owned deployment substrate for Cortex.

Forge is not a full Railway clone. It is a small lawful PaaS pattern:

```text
git checkout → docker build → docker run → healthcheck → rollback/log
```

## Components

```text
forge/deploy.sh                       Build and run a Docker deployment with rollback
forge/healthcheck.sh                  Verify live HTTP endpoints
forge/rollback.sh                     Restore a previous retained container when available
forge/install.sh                      Prepare persistent directories on a Linux host
forge/Caddyfile                       Example HTTPS reverse proxy
forge/systemd/cortex-forge.service    Optional Forge API systemd service
cortex_forge/server.py                HTTP control plane: check/deploy/rollback/job
```

## Host assumptions

- Linux VPS or home server
- Docker installed
- Optional Caddy for TLS/routing
- Git checkout of `cortex-mvp`

## Persistent directories

```text
/var/lib/cortex/ledger
/var/lib/cortex/runtime
/var/lib/cortex/memory
/var/lib/cortex/data
/var/log/cortex-forge
```

## Quick deploy

From the repo root on the host:

```bash
sudo forge/install.sh
PUBLIC_URL=http://127.0.0.1:8080 forge/deploy.sh
```

With confirmation and witness:

```bash
WITNESS=ryan CONFIRMED=true PUBLIC_URL=https://cortex.example.com forge/deploy.sh
```

## Forge API

Run locally on the host:

```bash
FORGE_ROOT=/var/lib/cortex-forge \
FORGE_REPO=/opt/cortex-mvp \
FORGE_TOKEN='choose-a-token' \
python -m cortex_forge.server
```

Open the dashboard:

```text
http://127.0.0.1:8765/ui
```

Endpoints:

```http
GET  /ui
GET  /forge/status
GET  /forge/apps
GET  /forge/apps/{app}/status
GET  /forge/apps/{app}/check
GET  /forge/apps/{app}/logs
GET  /forge/apps/{app}/health
GET  /forge/check
GET  /forge/job?id=latest
GET  /forge/jobs/{job_id}
GET  /forge/logs?lines=200
GET  /forge/health
POST /forge/apps/{app}/update
POST /forge/apps/{app}/deploy
POST /forge/apps/{app}/rollback
POST /forge/update
POST /forge/deploy
POST /forge/rollback
```

Mutation endpoints require `Authorization: Bearer $FORGE_TOKEN` when `FORGE_TOKEN` is set.

`/forge/update` is intentionally narrow: it only runs `git pull --ff-only`, with optional expected branch validation.

App-scoped mutation endpoints create jobs and return `202` immediately. Poll them at `/forge/jobs/{job_id}`.

Configure multiple apps with `FORGE_APPS=/etc/cortex/apps.json`; see `forge/apps.example.json`.

Example:

```bash
curl -X POST http://127.0.0.1:8765/forge/deploy \
  -H "authorization: Bearer $FORGE_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"witness":"ryan","confirmed":true,"public_url":"https://cortex.example.com"}'
```

## Safety model

Forge refuses deploy unless:

- `CONFIRMED=true`
- `WITNESS` is non-empty
- Docker is available
- healthcheck passes after container start

It keeps the previous container until the new one passes healthcheck. If healthcheck fails, the previous container is restored.

## Caddy

Copy or adapt `forge/Caddyfile`:

```bash
sudo cp forge/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Set your domain and reverse proxy target.
