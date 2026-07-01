"""Tests for calibration.py — temperature scaling, entropy, and confidence gate."""

import math
import tempfile
from pathlib import Path

import pytest
from cortex.calibration import (
    TemperatureScaler,
    EntropyEstimator,
    CalibratedConfidenceGate,
    ConfidenceStore,
    CalibrationResult,
    _sigmoid,
)


# ── _sigmoid ──────────────────────────────────────────────────────────────────

class TestSigmoid:

    def test_zero_returns_half(self):
        assert abs(_sigmoid(0.0) - 0.5) < 1e-9

    def test_large_positive_approaches_one(self):
        assert _sigmoid(100.0) > 0.999

    def test_large_negative_approaches_zero(self):
        assert _sigmoid(-100.0) < 0.001

    def test_overflow_handled(self):
        # Should not raise
        result = _sigmoid(1e308)
        assert 0.0 <= result <= 1.0


# ── TemperatureScaler ─────────────────────────────────────────────────────────

class TestTemperatureScaler:

    def test_default_temperature_is_one(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            assert scaler.temperature == 1.0

    def test_calibrate_at_T1_is_identity(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            scaler.temperature = 1.0
            # confidence 0.9 → logit 2.197 → sigmoid(2.197) ≈ 0.9
            result = scaler.calibrate_confidence(0.9)
            assert abs(result - 0.9) < 0.01

    def test_high_temperature_softens_confidence(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            scaler.temperature = 3.0
            # High T → confidence closer to 0.5
            result = scaler.calibrate_confidence(0.9)
            assert result < 0.9
            assert result > 0.5

    def test_low_temperature_sharpens_confidence(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            scaler.temperature = 0.5
            # Low T → confidence closer to 1.0
            result = scaler.calibrate_confidence(0.9)
            assert result > 0.9

    def test_fit_returns_temperature(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            # Overconfident model: always says 0.9, correct 60% of the time
            confidences = [0.9] * 100
            outcomes = [True] * 60 + [False] * 40
            T = scaler.fit(confidences, outcomes)
            assert T > 1.0  # should increase T to soften

    def test_fit_saves_and_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cal.json"
            scaler1 = TemperatureScaler(path)
            confidences = [0.9] * 50 + [0.6] * 50
            outcomes = [True] * 40 + [False] * 10 + [True] * 30 + [False] * 20
            T = scaler1.fit(confidences, outcomes)

            # Load from disk
            scaler2 = TemperatureScaler(path)
            assert abs(scaler2.temperature - T) < 1e-6

    def test_fit_empty_data_returns_current_temperature(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            scaler.temperature = 1.5
            T = scaler.fit([], [])
            assert T == 1.5

    def test_ece_perfect_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            # Perfect calibration: 0.9 conf → 90% correct
            confidences = [0.9] * 100
            outcomes = [True] * 90 + [False] * 10
            ece = scaler.expected_calibration_error(confidences, outcomes)
            assert ece < 0.05  # near-perfect

    def test_ece_bad_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            # Very bad: 0.9 conf but only 10% correct
            confidences = [0.9] * 100
            outcomes = [True] * 10 + [False] * 90
            ece = scaler.expected_calibration_error(confidences, outcomes)
            assert ece > 0.5

    def test_ece_empty_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            scaler = TemperatureScaler(Path(d) / "cal.json")
            assert scaler.expected_calibration_error([], []) == 0.0


# ── EntropyEstimator ──────────────────────────────────────────────────────────

class TestEntropyEstimator:

    def test_zero_entropy_gives_max_confidence(self):
        est = EntropyEstimator(max_entropy_threshold=4.0)
        conf = est.entropy_to_confidence(0.0)
        assert conf == 1.0

    def test_max_entropy_gives_zero_confidence(self):
        est = EntropyEstimator(max_entropy_threshold=4.0)
        conf = est.entropy_to_confidence(4.0)
        assert conf == 0.0

    def test_mid_entropy_gives_mid_confidence(self):
        est = EntropyEstimator(max_entropy_threshold=4.0)
        conf = est.entropy_to_confidence(2.0)
        assert abs(conf - 0.5) < 0.01

    def test_entropy_above_max_clamps_to_zero(self):
        est = EntropyEstimator(max_entropy_threshold=4.0)
        conf = est.entropy_to_confidence(10.0)
        assert conf == 0.0

    def test_from_probabilities_uniform(self):
        est = EntropyEstimator()
        # Uniform over 4 tokens: entropy = log(4) ≈ 1.386
        probs = [0.25, 0.25, 0.25, 0.25]
        entropy = est.from_probabilities(probs)
        assert abs(entropy - math.log(4)) < 0.001

    def test_from_probabilities_certain(self):
        est = EntropyEstimator()
        # Certain: entropy = 0
        probs = [1.0, 0.0, 0.0, 0.0]
        entropy = est.from_probabilities(probs)
        assert abs(entropy) < 1e-9

    def test_from_probabilities_empty_returns_zero(self):
        est = EntropyEstimator()
        assert est.from_probabilities([]) == 0.0


# ── CalibratedConfidenceGate ──────────────────────────────────────────────────

class TestCalibratedConfidenceGate:

    def setup_method(self):
        with tempfile.TemporaryDirectory() as d:
            self.gate = CalibratedConfidenceGate(
                scaler=TemperatureScaler(Path(d) / "cal.json"),
                estimator=EntropyEstimator(),
                min_confidence=0.7,
                max_gap=0.3,
                max_entropy=4.0,
            )

    def test_high_confidence_passes(self):
        result = self.gate.check(0.9)
        assert result.admissible
        assert result.stated_confidence == 0.9

    def test_low_confidence_fails(self):
        result = self.gate.check(0.5)
        assert not result.admissible
        assert "minimum" in result.reason

    def test_exact_minimum_passes(self):
        result = self.gate.check(0.7)
        assert result.admissible

    def test_just_below_minimum_fails(self):
        result = self.gate.check(0.69)
        assert not result.admissible

    def test_high_entropy_fails(self):
        result = self.gate.check(0.9, entropy=5.0)
        assert not result.admissible
        assert "entropy" in result.reason

    def test_low_entropy_passes(self):
        result = self.gate.check(0.9, entropy=0.5)
        assert result.admissible

    def test_result_contains_calibrated_confidence(self):
        result = self.gate.check(0.9)
        assert 0.0 <= result.calibrated_confidence <= 1.0

    def test_result_contains_temperature(self):
        result = self.gate.check(0.9)
        assert result.temperature > 0

    def test_to_dict_has_all_fields(self):
        result = self.gate.check(0.9)
        d = result.to_dict()
        assert "stated_confidence" in d
        assert "calibrated_confidence" in d
        assert "entropy" in d
        assert "temperature" in d
        assert "admissible" in d
        assert "reason" in d


# ── ConfidenceStore ───────────────────────────────────────────────────────────

class TestConfidenceStore:

    def test_init_creates_table(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfidenceStore(Path(d) / "test.db")
            # Should not raise

    def test_log_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfidenceStore(Path(d) / "test.db")
            result = CalibrationResult(
                stated_confidence=0.9,
                calibrated_confidence=0.85,
                entropy=1.0,
                temperature=1.0,
                admissible=True,
            )
            store.log("task-1", 0, result, outcome="success", correct=True)
            store.log("task-1", 1, result, outcome="success", correct=False)

            confidences, outcomes = store.load_for_fitting()
            assert len(confidences) == 2
            assert outcomes[0] == True
            assert outcomes[1] == False

    def test_recalibrate_with_insufficient_data(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfidenceStore(Path(d) / "test.db")
            scaler = TemperatureScaler(Path(d) / "cal.json")
            scaler.temperature = 1.5
            # Only 3 rows — below threshold of 10
            result = CalibrationResult(0.9, 0.85, 1.0, 1.0, True)
            for i in range(3):
                store.log("t", i, result, correct=True)
            T = store.recalibrate(scaler)
            assert T == 1.5  # unchanged

    def test_recalibrate_with_sufficient_data(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfidenceStore(Path(d) / "test.db")
            scaler = TemperatureScaler(Path(d) / "cal.json")
            result = CalibrationResult(0.9, 0.85, 1.0, 1.0, True)
            # 20 rows: overconfident model
            for i in range(12):
                store.log("t", i, result, correct=True)
            for i in range(8):
                store.log("t", i + 12, result, correct=False)
            T = store.recalibrate(scaler)
            assert T > 1.0  # should soften

    def test_log_without_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConfidenceStore(Path(d) / "test.db")
            result = CalibrationResult(0.8, 0.75, 0.5, 1.0, True)
            store.log("task-x", 0, result)  # no outcome — should not raise
