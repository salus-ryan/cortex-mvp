#!/usr/bin/env bash
set -euo pipefail

# Build a Debian live ISO that contains Cortex and starts cortex.pid1.
# Default mode runs live-build when available. DRY_RUN=1 only prepares config.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$REPO_ROOT/.build/live-usb}"
IMAGE_NAME="${IMAGE_NAME:-cortex-live-amd64.iso}"
DRY_RUN="${DRY_RUN:-0}"
ARCH="${ARCH:-amd64}"
DISTRIBUTION="${DISTRIBUTION:-bookworm}"

run() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then "$@"; fi
}

write_file() {
  local path="$1"
  echo "+ write $path"
  mkdir -p "$(dirname "$path")"
  cat > "$path"
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  cat <<EOF
Usage: image/live-usb/build.sh

Environment:
  DRY_RUN=1                 prepare config but do not invoke live-build
  BUILD_DIR=.build/live-usb build working directory
  IMAGE_NAME=cortex-live-amd64.iso
  ARCH=amd64
  DISTRIBUTION=bookworm
EOF
  exit 0
fi

echo "+ mkdir -p $BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

if [ "$DRY_RUN" != "1" ] && ! command -v lb >/dev/null 2>&1; then
  echo "refused: live-build is required. Install: sudo apt-get install live-build xorriso isolinux syslinux-utils rsync" >&2
  exit 3
fi

if [ ! -d config ]; then
  if [ "$DRY_RUN" = "1" ]; then
    echo "+ lb config --distribution $DISTRIBUTION --architectures $ARCH --binary-images iso-hybrid --debian-installer false"
    mkdir -p config
  else
    lb config \
      --distribution "$DISTRIBUTION" \
      --architectures "$ARCH" \
      --binary-images iso-hybrid \
      --debian-installer false \
      --archive-areas "main contrib non-free-firmware"
  fi
fi

mkdir -p \
  config/package-lists \
  config/includes.chroot/etc/systemd/system \
  config/includes.chroot/opt/cortex/bin \
  config/hooks/live \
  config/hooks/normal

write_file config/package-lists/cortex.list.chroot <<'EOF'
python3
python3-venv
python3-pip
python3-jsonschema
python3-pyrsistent
python3-pytest
git
curl
ca-certificates
network-manager
openssh-client
util-linux
e2fsprogs
EOF

cp "$SCRIPT_DIR/cortex-pid1.service" config/includes.chroot/etc/systemd/system/cortex-pid1.service
cp "$SCRIPT_DIR/cortex-state.service" config/includes.chroot/etc/systemd/system/cortex-state.service

# Copy the repo into the image. Exclude local/generated state and heavyweight caches.
run mkdir -p config/includes.chroot/opt/cortex
if command -v rsync >/dev/null 2>&1; then
  run rsync -a --delete \
    --exclude .git \
    --exclude .venv \
    --exclude .pytest_cache \
    --exclude __pycache__ \
    --exclude data/cortex.db \
    --exclude ledger/*.jsonl \
    --exclude .build \
    "$REPO_ROOT/" config/includes.chroot/opt/cortex/
else
  echo "+ copy repo with tar"
  if [ "$DRY_RUN" != "1" ]; then
    (cd "$REPO_ROOT" && tar \
      --exclude .git \
      --exclude .venv \
      --exclude .pytest_cache \
      --exclude __pycache__ \
      --exclude data/cortex.db \
      --exclude 'ledger/*.jsonl' \
      --exclude .build \
      -cf - .) | (cd config/includes.chroot/opt/cortex && tar -xf -)
  fi
fi

# Re-copy live-init helpers after rsync --delete so they cannot be pruned.
cp "$SCRIPT_DIR/cortex-init" config/includes.chroot/opt/cortex/bin/cortex-init
cp "$SCRIPT_DIR/mount-cortex-state" config/includes.chroot/opt/cortex/bin/mount-cortex-state
cp "$SCRIPT_DIR/verify-layout.sh" config/includes.chroot/opt/cortex/bin/verify-layout.sh
chmod +x \
  config/includes.chroot/opt/cortex/bin/cortex-init \
  config/includes.chroot/opt/cortex/bin/mount-cortex-state \
  config/includes.chroot/opt/cortex/bin/verify-layout.sh

write_file config/hooks/live/0100-cortex-service.hook.chroot <<'EOF'
#!/bin/sh
set -e
mkdir -p /var/lib/cortex/ledger /var/lib/cortex/runtime /var/lib/cortex/data /var/lib/cortex/memory
systemctl enable cortex-state.service
systemctl enable cortex-pid1.service
chmod +x /opt/cortex/bin/cortex-init /opt/cortex/bin/mount-cortex-state /opt/cortex/bin/verify-layout.sh
(cd /opt/cortex && find cortex image/live-usb -type f -print0 | sort -z | xargs -0 sha256sum > /opt/cortex/BOOT_ATTESTATION.sha256)
EOF
chmod +x config/hooks/live/0100-cortex-service.hook.chroot

write_file config/hooks/normal/0900-cortex-boot-menu.hook.binary <<'EOF'
#!/bin/sh
set -e
# Add an experimental boot hint/menu entry when live-build produced editable bootloader configs.
if [ -f binary/boot/grub/grub.cfg ] && ! grep -q 'Cortex literal PID 1' binary/boot/grub/grub.cfg; then
  cat >> binary/boot/grub/grub.cfg <<'MENU'

menuentry "Cortex literal PID 1 (experimental)" {
    linux /live/vmlinuz boot=live components quiet init=/opt/cortex/bin/cortex-init
    initrd /live/initrd.img
}
MENU
fi
if [ -f binary/isolinux/live.cfg ] && ! grep -q 'cortex-init' binary/isolinux/live.cfg; then
  cat >> binary/isolinux/live.cfg <<'MENU'

label cortex-init
    menu label Cortex literal PID 1 (experimental)
    kernel /live/vmlinuz
    append initrd=/live/initrd.img boot=live components quiet init=/opt/cortex/bin/cortex-init
MENU
fi
EOF
chmod +x config/hooks/normal/0900-cortex-boot-menu.hook.binary

if [ "$DRY_RUN" = "1" ]; then
  echo "live USB config ready at $BUILD_DIR/config"
  echo "dry run complete; install live-build and rerun without DRY_RUN=1 to build ISO"
  exit 0
fi

run lb build

FOUND_ISO="$(find "$BUILD_DIR" -maxdepth 1 -type f \( -name '*.iso' -o -name '*.hybrid.iso' \) | head -1 || true)"
if [ -n "$FOUND_ISO" ]; then
  run cp "$FOUND_ISO" "$BUILD_DIR/$IMAGE_NAME"
  echo "Cortex live USB ISO: $BUILD_DIR/$IMAGE_NAME"
else
  echo "build finished but no ISO was found in $BUILD_DIR" >&2
  exit 4
fi
