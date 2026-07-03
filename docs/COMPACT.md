# Cortex Compact Mode

Less is more.

Cortex can now run with fewer organs while preserving the core law shape.

## Profiles

### full

Default. Starts every child service:

```text
web guardian scribe oracle prophet memory tool planner deliberator immune repo patch build deploy
```

### compact

Starts only the core loop:

```text
web guardian scribe oracle memory immune
```

Use for demos, laptops, USB runtime, and low-resource hosts.

```bash
CORTEX_PROFILE=compact python -m cortex.pid1
```

Docker:

```bash
docker run -e CORTEX_PROFILE=compact -p 8080:8080 cortex-mvp:usb
```

### tiny

Starts only web:

```text
web
```

Most endpoints use in-process fallback where possible. Useful for smoke tests.

```bash
CORTEX_PROFILE=tiny python -m cortex.pid1
```

## Exact child allowlist

Override profiles with a comma-separated child list:

```bash
CORTEX_CHILDREN=web,oracle,memory python -m cortex.pid1
```

## Recommended default

For actual hosted Cortex:

```text
full
```

For thumb drive / local machine:

```text
compact
```

For smoke tests:

```text
tiny
```
