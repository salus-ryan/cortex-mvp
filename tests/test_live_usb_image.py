from pathlib import Path
import os
import subprocess


LIVE_USB_FILES = [
    Path("image/live-usb/build.sh"),
    Path("image/live-usb/cortex-init"),
    Path("image/live-usb/mount-cortex-state"),
    Path("image/live-usb/write-usb.sh"),
    Path("image/live-usb/verify-layout.sh"),
]


def test_live_usb_scripts_exist_and_parse():
    for script in LIVE_USB_FILES:
        assert script.exists()
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_live_usb_build_dry_run(tmp_path: Path):
    result = subprocess.run(
        ["bash", "image/live-usb/build.sh"],
        env={**os.environ, "DRY_RUN": "1", "BUILD_DIR": str(tmp_path / "live")},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "live USB config ready" in result.stdout
    config = tmp_path / "live" / "config"
    assert (config / "package-lists" / "cortex.list.chroot").exists()
    assert (config / "includes.chroot" / "etc" / "systemd" / "system" / "cortex-pid1.service").exists()
    assert (config / "includes.chroot" / "etc" / "systemd" / "system" / "cortex-state.service").exists()
    assert (config / "includes.chroot" / "opt" / "cortex" / "bin" / "cortex-init").exists()
    assert (config / "includes.chroot" / "opt" / "cortex" / "bin" / "mount-cortex-state").exists()
    assert (config / "hooks" / "normal" / "0900-cortex-boot-menu.hook.binary").exists()


def test_live_usb_service_runs_cortex_pid1():
    service = Path("image/live-usb/cortex-pid1.service").read_text()
    assert "ExecStart=/usr/bin/python3 -m cortex.pid1" in service
    assert "CORTEX_ROOT=/opt/cortex" in service


def test_live_usb_init_execs_cortex_pid1():
    init = Path("image/live-usb/cortex-init").read_text()
    assert "exec python3 -m cortex.pid1" in init
    assert "CORTEX_STATE" in init


def test_live_usb_state_service_mounts_before_pid1():
    state_service = Path("image/live-usb/cortex-state.service").read_text()
    pid1_service = Path("image/live-usb/cortex-pid1.service").read_text()
    mount_script = Path("image/live-usb/mount-cortex-state").read_text()
    assert "Before=cortex-pid1.service" in state_service
    assert "ExecStart=/opt/cortex/bin/mount-cortex-state" in state_service
    assert "blkid -L" in mount_script
    assert "CORTEX_STATE" in mount_script
    assert "mount --bind" in mount_script
    assert "permissions.json" in mount_script
    assert "After=network-online.target" in pid1_service


def test_live_usb_write_usb_guarded_dry_run():
    result = subprocess.run(
        ["bash", "image/live-usb/write-usb.sh", "fake.iso", "/dev/sdz"],
        env={**os.environ, "DRY_RUN": "1"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 6
    assert "Set YES=1" in result.stderr

    result_yes = subprocess.run(
        ["bash", "image/live-usb/write-usb.sh", "fake.iso", "/dev/sdz"],
        env={**os.environ, "DRY_RUN": "1", "YES": "1", "STATE_PARTITION": "/dev/sdz2"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result_yes.returncode == 0, result_yes.stderr
    assert "mkfs.ext4 -F -L CORTEX_STATE /dev/sdz2" in result_yes.stdout
