#!/usr/bin/env bash
set -euo pipefail

# Write a Cortex live ISO to a USB block device and optionally format a state partition.
# This is intentionally guarded. Use DRY_RUN=1 to inspect commands.

ISO="${1:-}"
DEVICE="${2:-}"
DRY_RUN="${DRY_RUN:-0}"
YES="${YES:-0}"
STATE_PARTITION="${STATE_PARTITION:-}"

if [ -z "$ISO" ] || [ -z "$DEVICE" ]; then
  echo "usage: $0 path/to/cortex-live.iso /dev/sdX" >&2
  echo "optional: STATE_PARTITION=/dev/sdX2 YES=1 $0 iso /dev/sdX" >&2
  exit 2
fi

run() {
  echo "+ $*"
  if [ "$DRY_RUN" != "1" ]; then "$@"; fi
}

if [ "$DRY_RUN" != "1" ]; then
  [ -f "$ISO" ] || { echo "refused: ISO not found: $ISO" >&2; exit 3; }
  [ -b "$DEVICE" ] || { echo "refused: block device not found: $DEVICE" >&2; exit 4; }
fi

case "$DEVICE" in
  /dev/sd[a-z]|/dev/vd[a-z]|/dev/nvme[0-9]n[0-9]|/dev/mmcblk[0-9]) ;;
  *) echo "refused: pass a whole removable-like block device, not a partition: $DEVICE" >&2; exit 5 ;;
esac

if [ "$YES" != "1" ]; then
  echo "DANGER: this will overwrite $DEVICE with $ISO" >&2
  echo "Set YES=1 to continue." >&2
  exit 6
fi

run sync
run dd "if=$ISO" "of=$DEVICE" bs=4M status=progress conv=fsync
run sync

if [ -n "$STATE_PARTITION" ]; then
  if [ "$DRY_RUN" != "1" ] && [ ! -b "$STATE_PARTITION" ]; then
    echo "refused: state partition does not exist: $STATE_PARTITION" >&2
    echo "Create a second partition with fdisk/parted, then rerun STATE_PARTITION=$STATE_PARTITION" >&2
    exit 7
  fi
  run mkfs.ext4 -F -L CORTEX_STATE "$STATE_PARTITION"
fi

echo "Cortex USB write complete. Boot the device and check http://127.0.0.1:8080/health"
