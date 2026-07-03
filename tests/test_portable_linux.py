from pathlib import Path
import os
import subprocess


SCRIPTS = [
    Path("image/portable-linux/install.sh"),
    Path("image/portable-linux/start.sh"),
    Path("image/portable-linux/start-forge.sh"),
    Path("image/portable-linux/status.sh"),
]


def test_portable_linux_scripts_exist_and_parse():
    for script in SCRIPTS:
        assert script.exists()
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_portable_linux_install_dry_run(tmp_path: Path):
    result = subprocess.run(
        ["bash", "image/portable-linux/install.sh", str(tmp_path / "usb")],
        env={**os.environ, "DRY_RUN": "1"},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert "portable Cortex USB layout ready" in result.stdout


def test_portable_linux_start_dry_run(tmp_path: Path):
    usb = tmp_path / "usb"
    (usb / "cortex-mvp").mkdir(parents=True)
    (usb / "state").mkdir()
    (usb / "env").mkdir()
    (usb / "env" / "cortex.env").write_text("PYTHONUNBUFFERED=1\n")
    result = subprocess.run(
        ["bash", "image/portable-linux/start.sh", str(usb)],
        env={**os.environ, "DRY_RUN": "1"},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert "docker run" in result.stdout
