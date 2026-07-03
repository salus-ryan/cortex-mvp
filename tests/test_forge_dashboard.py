import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from cortex_forge.server import ForgeState, make_handler
from tests.test_forge_server import make_repo


def test_forge_dashboard_served(tmp_path: Path):
    state = ForgeState(tmp_path / "state", make_repo(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, None))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/ui", timeout=5) as r:
            html = r.read().decode()
        assert "Cortex Forge Control Room" in html
        with urllib.request.urlopen(base + "/forge/apps", timeout=5) as r:
            apps = json.loads(r.read().decode())
        assert "cortex" in apps["apps"]
    finally:
        server.shutdown()
