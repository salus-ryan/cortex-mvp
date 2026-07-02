# Cortex Forge

A minimal self-owned deployment substrate for Cortex.

Forge is not a full Railway clone. It is a small lawful PaaS pattern:

```text
git checkout → docker build → docker run → healthcheck → rollback/log
```

## Components

```text
forge/deploy.sh       Build and run a Docker deployment with rollback
forge/healthcheck.sh  Verify live HTTP endpoints
forge/install.sh      Prepare persistent directories on a Linux host
forge/Caddyfile       Example HTTPS reverse proxy
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
