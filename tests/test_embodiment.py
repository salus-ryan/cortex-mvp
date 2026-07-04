from pathlib import Path

from cortex.embodiment import EmbodimentService


def seed_live_usb(root: Path) -> None:
    files = [
        "image/live-usb/build.sh",
        "image/live-usb/write-usb.sh",
        "image/live-usb/verify-layout.sh",
        "image/live-usb/cortex-init",
        "image/live-usb/mount-cortex-state",
        "image/live-usb/cortex-pid1.service",
        "image/live-usb/cortex-state.service",
    ]
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "seed"
        if rel.endswith("mount-cortex-state"):
            text = "CORTEX_STATE ledger runtime data memory"
        if rel.endswith("build.sh"):
            text = "BOOT_ATTESTATION.sha256 experimental"
        if rel.endswith("cortex-init"):
            text = "CORTEX_RECOVERY safe-shell exec /bin/sh"
        path.write_text(text)


def test_embodiment_reports_layout_persistence_and_recovery(tmp_path: Path):
    seed_live_usb(tmp_path)
    svc = EmbodimentService(tmp_path)

    layout = svc.live_layout_report()
    state = svc.persistent_state_plan()
    recovery = svc.recovery_secure_boot_report()

    assert layout["valid"] is True
    assert state["valid"] is True
    assert recovery["valid"] is True
    assert recovery["secure_boot_status"] == "not_enabled_report_only"
    assert layout["may_execute"] is False
    assert state["may_execute"] is False
    assert recovery["may_execute"] is False


def test_iso_report_hashes_existing_artifact(tmp_path: Path):
    seed_live_usb(tmp_path)
    iso = tmp_path / "cortex.iso"
    iso.write_bytes(b"iso")

    report = EmbodimentService(tmp_path).iso_report("cortex.iso")

    assert report["validated_iso_artifact"] is True
    assert report["artifacts"][0]["bytes"] == 3
    assert len(report["artifacts"][0]["sha256"]) == 64
    assert report["may_execute"] is False
