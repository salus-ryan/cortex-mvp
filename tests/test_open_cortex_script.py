import os
import subprocess
from pathlib import Path


def test_open_cortex_script_syntax():
    subprocess.run(["bash", "-n", "scripts/open_cortex.sh"], check=True)


def test_open_cortex_script_dry_run(tmp_path: Path):
    env = {
        **os.environ,
        "DRY_RUN": "1",
        "NO_BROWSER": "1",
        "XDG_CACHE_HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", "scripts/open_cortex.sh"],
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert "Cortex Forge dashboard" in result.stdout
    assert "browser skipped" in result.stdout
