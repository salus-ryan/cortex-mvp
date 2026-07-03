import json
import subprocess


def test_mobile_e2e_script_helpful_syntax():
    subprocess.run(["python3", "-m", "py_compile", "scripts/mobile_e2e.py"], check=True)


def test_mobile_e2e_report_shape_without_network(monkeypatch):
    from scripts.mobile_e2e import Check, MobileE2E

    runner = MobileE2E("https://example.com")
    runner.checks = [Check("x", True, {"a": 1})]
    report = runner.report()
    assert report["status"] == "pass"
    assert report["checks"][0]["name"] == "x"
    assert report["token_tested"] is False
