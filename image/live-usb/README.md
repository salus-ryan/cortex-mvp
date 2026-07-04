# Cortex Live USB Image

This directory is the next step beyond `image/portable-linux`: a reproducible path toward a thumb-drive bootable Cortex system.

Current status: **scaffolded live Linux image builder**, not yet a hardened production OS.

## Goal

```text
firmware/bootloader
  -> Linux kernel + initramfs
  -> Cortex init profile
  -> python -m cortex.pid1
  -> SCL-governed runtime children
  -> local web/mobile UI
  -> persistent ledger/runtime/data on USB
```

Cortex already supports PID 1 inside a container. This image track adds the missing boot media layer.

## Profiles

### Practical profile: systemd launches Cortex PID supervisor

The default generated image boots a small Debian live system and starts:

```text
systemd PID 1
  -> cortex-pid1.service
      -> python -m cortex.pid1
```

This is the safest first live USB milestone because networking, filesystems, logs, shutdown, and recovery remain handled by Linux/systemd.

### Experimental profile: Cortex as literal userspace init

`cortex-init` is an experimental `/sbin/init`-compatible handoff script. If the kernel is booted with an init override such as:

```text
init=/opt/cortex/bin/cortex-init
```

then the script mounts essential pseudo-filesystems and execs:

```bash
python3 -m cortex.pid1
```

That makes Cortex the userspace PID 1 process. This is for lab validation only until storage, networking, recovery shell, and shutdown semantics are hardened.

## Build prerequisites

On a Debian/Ubuntu builder:

```bash
sudo apt-get update
sudo apt-get install -y live-build xorriso isolinux syslinux-utils rsync
```

## Generate config only

```bash
DRY_RUN=1 image/live-usb/build.sh
```

## Build ISO

```bash
sudo image/live-usb/build.sh
```

The default output path is:

```text
.build/live-usb/live-image-amd64.hybrid.iso
```

Override with:

```bash
BUILD_DIR=/tmp/cortex-live IMAGE_NAME=cortex-live.iso sudo image/live-usb/build.sh
```

## Write to a thumb drive

Dangerous command; replace `/dev/sdX` with the correct whole device:

```bash
sudo dd if=.build/live-usb/live-image-amd64.hybrid.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

## Persistence target

The runtime expects mutable state under:

```text
/var/lib/cortex/{ledger,runtime,data,memory}
```

`cortex-state.service` runs before `cortex-pid1.service`. It calls `/opt/cortex/bin/mount-cortex-state`, which mounts a writable partition labeled:

```text
CORTEX_STATE
```

If that partition is absent, Cortex still boots with the live filesystem's `/var/lib/cortex` directories, but persistence depends on the live OS overlay. The mount helper bind-mounts persistent `ledger`, `runtime`, `data`, and `memory` back under `/opt/cortex` because the current runtime resolves those paths relative to `CORTEX_ROOT`.

## Write to USB and add persistence

```bash
sudo image/live-usb/write-usb.sh .build/live-usb/cortex-live-amd64.iso /dev/sdX
```

The writer is guarded and requires explicit confirmation:

```bash
YES=1 sudo image/live-usb/write-usb.sh .build/live-usb/cortex-live-amd64.iso /dev/sdX
```

After creating a second partition manually with `fdisk`, `parted`, or your disk tool, format it for Cortex state:

```bash
YES=1 STATE_PARTITION=/dev/sdX2 sudo image/live-usb/write-usb.sh .build/live-usb/cortex-live-amd64.iso /dev/sdX
```

Or format only the state partition yourself:

```bash
sudo mkfs.ext4 -F -L CORTEX_STATE /dev/sdX2
```

## Boot modes

The generated image enables the safe systemd path by default:

```text
systemd -> cortex-state.service -> cortex-pid1.service -> python3 -m cortex.pid1
```

The builder also attempts to add an experimental bootloader entry named:

```text
Cortex literal PID 1 (experimental)
```

That entry passes:

```text
init=/opt/cortex/bin/cortex-init
```

Use it only for lab validation. The systemd mode is the operational default.

## Boot attestation

During image build, Cortex writes:

```text
/opt/cortex/BOOT_ATTESTATION.sha256
```

This is a simple file hash manifest for the embedded Cortex runtime. It is not secure boot by itself. A complete secure/verified boot story still requires signed bootloader/kernel artifacts, measured boot or dm-verity, and key management.

## Validation checklist

After boot:

```bash
systemctl status cortex-pid1
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/pid1
python3 -m pytest /opt/cortex/tests -q
```
