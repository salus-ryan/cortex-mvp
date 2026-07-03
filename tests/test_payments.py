from pathlib import Path

from cortex.payments import PaymentService


def test_payment_intent_records_without_charge(tmp_path: Path):
    svc = PaymentService(tmp_path)
    rec = svc.intent(500, "VPS fund", "usd", "alice")
    assert rec["status"] == "intent_prepared"
    assert rec["may_execute"] is False
    assert "No charge" in rec["statement"]
    assert (tmp_path / "ledger" / "payments.jsonl").exists()


def test_payment_checkout_requires_witness_and_confirmation(tmp_path: Path):
    svc = PaymentService(tmp_path)
    assert svc.checkout(500, "VPS fund", witness=None, confirmed=True)["status"] == "refused"
    assert svc.checkout(500, "VPS fund", witness="alice", confirmed=False)["status"] == "refused"


def test_payment_limits(tmp_path: Path):
    svc = PaymentService(tmp_path)
    assert svc.intent(50, "too low")["status"] == "refused"
    assert svc.intent(1_000_000, "too high")["status"] == "refused"
    assert svc.intent(500, "bad currency", "btc")["status"] == "refused"


def test_payment_checkout_refuses_without_provider(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PAYMENT_PROVIDER", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    svc = PaymentService(tmp_path)
    rec = svc.checkout(500, "VPS fund", witness="alice", confirmed=True)
    assert rec["status"] == "refused"
    assert "provider unavailable" in rec["reason"]
