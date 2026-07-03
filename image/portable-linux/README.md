# Cortex Portable Linux USB

This is the first practical step toward a bootable Cortex thumb drive.

It does **not** build a full bootable ISO yet. Instead, it lets a Linux machine run Cortex from a mounted USB directory with persistent state on the drive.

## Layout on the USB

```text
cortex-usb/
├── cortex-mvp/              # repo clone or copied repo
├── state/
│   ├── ledger/
│   ├── memory/
│   ├── runtime/
│   └── data/
└── env/
    ├── cortex.env
    └── forge.env
```

## Install to a mounted USB

From the repo root:

```bash
sudo image/portable-linux/install.sh /media/$USER/CORTEX
```

Or dry-run:

```bash
DRY_RUN=1 image/portable-linux/install.sh /tmp/cortex-usb
```

## Start Cortex from the USB

```bash
/media/$USER/CORTEX/cortex-mvp/image/portable-linux/start.sh /media/$USER/CORTEX
```

Then open:

```text
http://127.0.0.1:8080/health
http://127.0.0.1:8080/pid1
```

## Start Forge dashboard from the USB

```bash
/media/$USER/CORTEX/cortex-mvp/image/portable-linux/start-forge.sh /media/$USER/CORTEX
```

Then open:

```text
http://127.0.0.1:8765/ui
```

## What this gives you

```text
portable repo
portable ledger/memory/runtime/data
Cortex container with PID 1 inside the container
Forge dashboard backed by USB state
no cloud required
```

## Requirements on host machine

```text
Linux
Docker
Python 3 for Forge dashboard
Git optional
```

## Toward true bootable USB

A true bootable image will add:

```text
minimal Debian/Ubuntu base
Docker preinstalled
systemd services enabled
this portable layout preloaded
persistent partition mounted at /mnt/cortex
```
