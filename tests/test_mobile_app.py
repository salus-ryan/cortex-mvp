import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from cortex_forge.server import ForgeState, make_handler
from cortex.web import Handler
from tests.test_forge_server import make_repo


def test_mobile_files_exist():
    assert Path("mobile/index.html").exists()
    assert Path("mobile/manifest.json").exists()
    assert Path("mobile/service-worker.js").exists()
    html = Path("mobile/index.html").read_text()
    assert "mobile witness console" in html
    assert "localStorage.cortexToken" in html


def test_cortex_mobile_served(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("cortex.web.ROOT", Path.cwd())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/mobile", timeout=5) as r:
            html = r.read().decode()
        assert "Cortex" in html
        with urllib.request.urlopen(base + "/mobile/manifest.json", timeout=5) as r:
            manifest = json.loads(r.read().decode())
        assert manifest["name"] == "Cortex"
    finally:
        server.shutdown()
