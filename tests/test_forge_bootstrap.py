from pathlib import Path
import os
import subprocess


def test_forge_bootstrap_syntax():
    script = Path("forge/bootstrap.sh")
    assert script.exists()
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_forge_bootstrap_dry_run():
    result = subprocess.run(
        ["bash", "forge/bootstrap.sh"],
        env={**os.environ, "DRY_RUN": "1", "DOMAIN": "cortex.example.com", "FORGE_TOKEN": "test"},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert "forge bootstrap: pass" in result.stdout
    assert "forge.cortex.example.com/ui" in result.stdout
