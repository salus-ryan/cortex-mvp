"""
calibration.py — Calibrated Confidence for SCL

The problem with uncalibrated confidence
-----------------------------------------
A model can emit `@halt → answer [confidence: 0.95, ...]` even when its
internal token distribution is highly uncertain (high entropy). The stated
confidence is a learned pattern, not a calibrated epistemic claim.

This module makes confidence semantically meaningful by:

1. TemperatureScaler     — learns a scalar T that maps raw logits to
                           calibrated probabilities (Platt/temperature scaling).
                           After calibration, the model's stated confidence
                           should match its empirical accuracy.

2. EntropyEstimator      — computes token-level entropy from the model's
                           output distribution as a model-free uncertainty proxy.
                           High entropy → low confidence, regardless of what
                           the model states.

3. CalibratedVerifier    — extends the base Verifier with a confidence gate:
                           if the model's stated confidence exceeds its
                           calibrated confidence by more than a threshold,
                           the halt is rejected and the model must re-propose.

4. ConfidenceStore       — persists (stated_confidence, calibrated_confidence,
                           outcome) tuples to SQLite for continuous recalibration.

Philosophy
----------
Calibration is the property that when the model says "confidence: 0.9",
it is correct 90% of the time. Without calibration, the confidence field
is a decoration. With calibration, it is an epistemic commitment.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# ── Calibration result ────────────────────────────────────────────────────────

@dataclass
class CalibrationResult:
    stated_confidence: float        # what the model claimed
    calibrated_confidence: float    # what the calibrator estimates
    entropy: float                  # token-level entropy of the output
    temperature: float              # current temperature scalar
    admissible: bool                # True if stated ≈ calibrated within threshold
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "stated_confidence": self.stated_confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "entropy": self.entropy,
            "temperature": self.temperature,
            "admissible": self.admissible,
            "reason": self.reason,
        }


# ── Temperature scaler ────────────────────────────────────────────────────────

class TemperatureScaler:
    """
    Single-parameter post-hoc calibration via temperature scaling.

    After training, fit T on a held-out calibration set:
        calibrated_prob = softmax(logits / T)

    T > 1 → model is overconfident (soften)
    T < 1 → model is underconfident (sharpen)
    T = 1 → already calibrated

    We store T in a JSON file so it persists across runs and updates
    automatically as new calibration data arrives.
    """

    def __init__(self, path: Path = Path("data/calibration.json")):
        self.path = path
        self.temperature: float = 1.0
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.temperature = float(data.get("temperature", 1.0))
            except Exception:
                self.temperature = 1.0

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "temperature": self.temperature,
            "n_samples": getattr(self, "_n_samples", 0),
        }, indent=2))

    def calibrate(self, logit: float) -> float:
        """Apply temperature scaling to a single logit → calibrated probability."""
        return _sigmoid(logit / max(self.temperature, 1e-6))

    def calibrate_confidence(self, raw_confidence: float) -> float:
        """
        Map a raw stated confidence through temperature scaling.
        Converts confidence → logit → scale → probability.
        """
        # Clamp to avoid log(0)
        p = max(min(raw_confidence, 1 - 1e-7), 1e-7)
        logit = math.log(p / (1 - p))  # inverse sigmoid
        return self.calibrate(logit)

    def fit(self, confidences: List[float], outcomes: List[bool]) -> float:
        """
        Fit temperature T to minimise NLL on (confidence, outcome) pairs.
        Uses simple grid search over T ∈ [0.1, 10.0].
        Returns the fitted temperature.
        """
        if not confidences or not outcomes:
            return self.temperature

        best_T = 1.0
        best_nll = float("inf")

        for T in [t / 10 for t in range(1, 101)]:  # 0.1 to 10.0
            nll = 0.0
            for conf, outcome in zip(confidences, outcomes):
                p = max(min(conf, 1 - 1e-7), 1e-7)
                logit = math.log(p / (1 - p))
                cal_p = _sigmoid(logit / max(T, 1e-6))
                cal_p = max(min(cal_p, 1 - 1e-7), 1e-7)
                nll += -math.log(cal_p if outcome else (1 - cal_p))
            nll /= len(confidences)
            if nll < best_nll:
                best_nll = nll
                best_T = T

        self.temperature = best_T
        self._n_samples = len(confidences)
        self._save()
        return best_T

    def expected_calibration_error(
        self,
        confidences: List[float],
        outcomes: List[bool],
        n_bins: int = 10,
    ) -> float:
        """
        Compute Expected Calibration Error (ECE) — the standard metric.
        ECE = Σ_b (|B_b| / n) * |acc(B_b) - conf(B_b)|
        """
        if not confidences:
            return 0.0

        bins = [[] for _ in range(n_bins)]
        for conf, outcome in zip(confidences, outcomes):
            bin_idx = min(int(conf * n_bins), n_bins - 1)
            bins[bin_idx].append((conf, outcome))

        ece = 0.0
        n = len(confidences)
        for b in bins:
            if not b:
                continue
            acc = sum(o for _, o in b) / len(b)
            avg_conf = sum(c for c, _ in b) / len(b)
            ece += (len(b) / n) * abs(acc - avg_conf)

        return ece


# ── Entropy estimator ─────────────────────────────────────────────────────────

class EntropyEstimator:
    """
    Estimates model uncertainty from token-level entropy.

    When a model generates a token, the entropy of its output distribution
    measures how uncertain it is. High entropy = uncertain = low confidence.

    This provides a model-free uncertainty signal that doesn't rely on
    the model's stated confidence field.

    Usage (with HuggingFace model):
        estimator = EntropyEstimator()
        entropy = estimator.from_logits(logits_tensor)  # per-token entropy
        confidence = estimator.entropy_to_confidence(entropy)
    """

    def __init__(self, max_entropy_threshold: float = 4.0):
        """
        max_entropy_threshold: entropy above this value → confidence ≈ 0.
        For a vocabulary of size V, max entropy = log(V).
        For Qwen 0.5B (V≈150k): max = log(150000) ≈ 11.9
        We use 4.0 as a practical threshold (uniform over ~55 tokens).
        """
        self.max_entropy = max_entropy_threshold

    def from_logits(self, logits) -> float:
        """
        Compute mean token entropy from a logits tensor.
        logits: shape (seq_len, vocab_size) or (vocab_size,)
        Returns scalar entropy in nats.
        """
        try:
            import torch
            import torch.nn.functional as F

            if logits.dim() == 1:
                logits = logits.unsqueeze(0)

            probs = F.softmax(logits, dim=-1)
            log_probs = F.log_softmax(logits, dim=-1)
            entropy_per_token = -(probs * log_probs).sum(dim=-1)
            return entropy_per_token.mean().item()
        except Exception:
            return 0.0

    def from_probabilities(self, probs: List[float]) -> float:
        """Compute entropy from a probability distribution (pure Python)."""
        entropy = 0.0
        for p in probs:
            if p > 0:
                entropy -= p * math.log(p)
        return entropy

    def entropy_to_confidence(self, entropy: float) -> float:
        """
        Map entropy → confidence in [0, 1].
        Low entropy → high confidence.
        High entropy → low confidence.
        """
        # Linear mapping: entropy=0 → conf=1.0, entropy=max → conf=0.0
        conf = 1.0 - min(entropy / max(self.max_entropy, 1e-6), 1.0)
        return max(0.0, min(1.0, conf))

    def estimate_from_text_generation(
        self,
        model,
        tokenizer,
        prompt: str,
        generated_text: str,
        device: str = "cpu",
    ) -> float:
        """
        Re-run the model on the generated text to get per-token logits,
        then compute mean entropy as a calibrated confidence estimate.
        """
        try:
            import torch

            full_text = prompt + generated_text
            inputs = tokenizer(
                full_text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
            ).to(device)

            prompt_len = tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=512,
            )["input_ids"].shape[1]

            with torch.no_grad():
                outputs = model(**inputs)
                logits = outputs.logits[0, prompt_len - 1:-1]  # generated tokens

            if logits.shape[0] == 0:
                return 0.5

            entropy = self.from_logits(logits)
            return self.entropy_to_confidence(entropy)

        except Exception:
            return 0.5  # neutral fallback


# ── Confidence store ──────────────────────────────────────────────────────────

class ConfidenceStore:
    """
    Persists calibration data to SQLite.
    Stores (task_id, step, stated_confidence, calibrated_confidence, outcome)
    for continuous recalibration.
    """

    def __init__(self, db_path: Path = Path("data/cortex.db")):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                ts REAL NOT NULL,
                stated_confidence REAL NOT NULL,
                calibrated_confidence REAL NOT NULL,
                entropy REAL NOT NULL,
                temperature REAL NOT NULL,
                outcome TEXT,
                correct INTEGER
            )
        """)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        conn.close()

    def log(
        self,
        task_id: str,
        step: int,
        result: CalibrationResult,
        outcome: str = "",
        correct: Optional[bool] = None,
    ):
        import time
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            INSERT INTO calibration_log
            (task_id, step, ts, stated_confidence, calibrated_confidence,
             entropy, temperature, outcome, correct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_id, step, time.time(),
            result.stated_confidence, result.calibrated_confidence,
            result.entropy, result.temperature,
            outcome, int(correct) if correct is not None else None,
        ))
        conn.commit()
        conn.close()

    def load_for_fitting(self) -> Tuple[List[float], List[bool]]:
        """Load all rows with known outcomes for temperature fitting."""
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("""
            SELECT stated_confidence, correct
            FROM calibration_log
            WHERE correct IS NOT NULL
        """).fetchall()
        conn.close()
        confidences = [r[0] for r in rows]
        outcomes = [bool(r[1]) for r in rows]
        return confidences, outcomes

    def recalibrate(self, scaler: TemperatureScaler) -> float:
        """Fit the temperature scaler on all stored calibration data."""
        confidences, outcomes = self.load_for_fitting()
        if len(confidences) < 10:
            return scaler.temperature  # not enough data yet
        return scaler.fit(confidences, outcomes)


# ── Calibrated verifier ───────────────────────────────────────────────────────

class CalibratedConfidenceGate:
    """
    A gate that rejects @halt actions where the stated confidence
    is inconsistent with the calibrated confidence.

    Used by the runtime to validate halt actions before accepting them.

    The gate passes if:
      1. stated_confidence >= min_confidence (hard floor)
      2. |stated - calibrated| <= max_gap (calibration consistency)
      3. entropy <= max_entropy (model is not wildly uncertain)
    """

    def __init__(
        self,
        scaler: Optional[TemperatureScaler] = None,
        estimator: Optional[EntropyEstimator] = None,
        min_confidence: float = 0.7,
        max_gap: float = 0.3,
        max_entropy: float = 4.0,
    ):
        self.scaler = scaler or TemperatureScaler()
        self.estimator = estimator or EntropyEstimator(max_entropy)
        self.min_confidence = min_confidence
        self.max_gap = max_gap
        self.max_entropy = max_entropy

    def check(
        self,
        stated_confidence: float,
        entropy: Optional[float] = None,
    ) -> CalibrationResult:
        """
        Check whether a stated confidence is admissible.

        Parameters
        ----------
        stated_confidence : float
            The confidence value from the SCL @halt action.
        entropy : float, optional
            Token-level entropy of the model's output. If None, entropy
            check is skipped (used when model logits are unavailable).
        """
        calibrated = self.scaler.calibrate_confidence(stated_confidence)
        ent = entropy if entropy is not None else 0.0
        gap = abs(stated_confidence - calibrated)

        # Hard floor
        if stated_confidence < self.min_confidence:
            return CalibrationResult(
                stated_confidence=stated_confidence,
                calibrated_confidence=calibrated,
                entropy=ent,
                temperature=self.scaler.temperature,
                admissible=False,
                reason=f"stated confidence {stated_confidence:.2f} < minimum {self.min_confidence}",
            )

        # Calibration consistency
        if gap > self.max_gap:
            return CalibrationResult(
                stated_confidence=stated_confidence,
                calibrated_confidence=calibrated,
                entropy=ent,
                temperature=self.scaler.temperature,
                admissible=False,
                reason=(
                    f"stated confidence {stated_confidence:.2f} deviates from "
                    f"calibrated {calibrated:.2f} by {gap:.2f} > max_gap {self.max_gap}"
                ),
            )

        # Entropy gate (only if entropy is provided)
        if entropy is not None and entropy > self.max_entropy:
            return CalibrationResult(
                stated_confidence=stated_confidence,
                calibrated_confidence=calibrated,
                entropy=ent,
                temperature=self.scaler.temperature,
                admissible=False,
                reason=f"token entropy {entropy:.2f} > max_entropy {self.max_entropy}",
            )

        return CalibrationResult(
            stated_confidence=stated_confidence,
            calibrated_confidence=calibrated,
            entropy=ent,
            temperature=self.scaler.temperature,
            admissible=True,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0
