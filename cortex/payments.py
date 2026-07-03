"""Lawful payment rails for Cortex.

Cortex may prepare funding intents and, when explicitly witnessed/confirmed,
create a third-party checkout session for a human to complete. Cortex never
charges cards directly, never stores card data, and never performs hidden or
unsupervised financial action.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PaymentService:
    MIN_AMOUNT_CENTS = 100
    MAX_AMOUNT_CENTS = 50_000
    ALLOWED_CURRENCIES = {"usd", "eur", "gbp"}

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.ledger = self.root / "ledger"
        self.runtime = self.root / "runtime" / "payments"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def intent(self, amount_cents: int, purpose: str, currency: str = "usd", witness: str | None = None) -> dict[str, Any]:
        currency = currency.lower()
        refusal = self._validate_amount(amount_cents, currency)
        if refusal:
            return self._refuse(refusal, {"amount_cents": amount_cents, "currency": currency})
        if not purpose.strip():
            return self._refuse("payment purpose is required")
        rec = {
            "id": "pay_" + uuid.uuid4().hex[:12],
            "status": "intent_prepared",
            "timestamp": self.now(),
            "amount_cents": amount_cents,
            "currency": currency,
            "purpose": purpose,
            "witness": witness,
            "provider": self.provider(),
            "may_execute": False,
            "statement": "This is a funding intent only. No charge has occurred.",
        }
        self._record("intent", rec)
        return rec

    def checkout(self, amount_cents: int, purpose: str, currency: str = "usd", witness: str | None = None, confirmed: bool = False) -> dict[str, Any]:
        if not witness:
            return self._refuse("checkout requires witness")
        if not confirmed:
            return self._refuse("checkout requires confirmed=true")
        intent = self.intent(amount_cents, purpose, currency, witness)
        if intent.get("status") != "intent_prepared":
            return intent
        if self.provider() != "stripe":
            return self._refuse("payment provider unavailable; set PAYMENT_PROVIDER=stripe and STRIPE_SECRET_KEY", intent)
        try:
            session = self._stripe_checkout(amount_cents, purpose, currency.lower(), intent["id"])
        except Exception as exc:
            return self._refuse(f"stripe checkout failed: {exc}", intent)
        rec = {
            **intent,
            "status": "checkout_created",
            "checkout_url": session.get("url"),
            "provider_session_id": session.get("id"),
            "statement": "Human must complete checkout with the provider. Cortex has not charged a card directly.",
            "may_execute": False,
        }
        self._record("checkout", rec)
        return rec

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": self.provider(),
            "stripe_configured": bool(os.environ.get("STRIPE_SECRET_KEY")),
            "limits": {"min_amount_cents": self.MIN_AMOUNT_CENTS, "max_amount_cents": self.MAX_AMOUNT_CENTS, "currencies": sorted(self.ALLOWED_CURRENCIES)},
            "latest": self.latest(),
            "may_execute": False,
        }

    def latest(self) -> dict[str, Any]:
        path = self.runtime / "latest.json"
        if not path.exists():
            return {"status": "none", "may_execute": False}
        return json.loads(path.read_text())

    def provider(self) -> str:
        return os.environ.get("PAYMENT_PROVIDER", "none").lower()

    def _validate_amount(self, amount_cents: int, currency: str) -> str | None:
        if currency not in self.ALLOWED_CURRENCIES:
            return f"unsupported currency: {currency}"
        if amount_cents < self.MIN_AMOUNT_CENTS:
            return "amount below minimum"
        if amount_cents > self.MAX_AMOUNT_CENTS:
            return "amount above safety cap"
        return None

    def _stripe_checkout(self, amount_cents: int, purpose: str, currency: str, intent_id: str) -> dict[str, Any]:
        key = os.environ.get("STRIPE_SECRET_KEY")
        if not key:
            raise ValueError("STRIPE_SECRET_KEY missing")
        success_url = os.environ.get("PAYMENT_SUCCESS_URL", "https://example.com/payment/success")
        cancel_url = os.environ.get("PAYMENT_CANCEL_URL", "https://example.com/payment/cancel")
        form = urllib.parse.urlencode(
            {
                "mode": "payment",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "client_reference_id": intent_id,
                "line_items[0][quantity]": "1",
                "line_items[0][price_data][currency]": currency,
                "line_items[0][price_data][unit_amount]": str(amount_cents),
                "line_items[0][price_data][product_data][name]": purpose[:120],
                "metadata[purpose]": purpose[:500],
                "metadata[intent_id]": intent_id,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.stripe.com/v1/checkout/sessions",
            data=form,
            headers={"authorization": f"Bearer {key}", "content-type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def _refuse(self, reason: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        rec = {"status": "refused", "reason": reason, "detail": detail or {}, "timestamp": self.now(), "may_execute": False}
        self._record("refuse", rec)
        return rec

    def _record(self, phase: str, rec: dict[str, Any]) -> None:
        enriched = {"phase": phase, **rec}
        (self.runtime / "latest.json").write_text(json.dumps(enriched, indent=2, sort_keys=True))
        with (self.ledger / "payments.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(enriched, sort_keys=True) + "\n")
