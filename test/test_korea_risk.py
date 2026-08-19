#!/usr/bin/env python3
"""P1-KR-06 transient Korea Risk/Vol regression (synthetic only)."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_risk.py"
SPEC = importlib.util.spec_from_file_location("korea_risk", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_policy(path: Path, **updates) -> Path:
    policy = {
        "schema_version": 1,
        "policy_version": "korea_risk_input/synthetic-test-v1",
        "approval_status": "RATIFIED",
        "effective_from": "2026-01-01",
        "source_name": "KRX_OPEN_API_KOSPI_INDEX_FIXTURE",
        "market": "KOREA",
        "index_identity": "KOSPI::코스피",
        "market_timezone": "Asia/Seoul",
        "allowed_run_modes": ["FORWARD_SHADOW", "HISTORICAL_REPLAY"],
        "session_calendar_source": "synthetic_xkrx_fixture/v1",
        "publication_timing_source": "synthetic_fixture_only/v1",
        "earliest_usable_time": "18:00:00",
        "realized_vol_lookback_returns": 3,
        "annualization_sessions": 252,
        "drawdown_lookback_closes": 5,
    }
    policy.update(updates)
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def payload(closes=None, mode="FORWARD_SHADOW"):
    closes = closes or ["100", "120", "90", "80", "100"]
    dates = ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-18"]
    return {
        "schema_version": 1,
        "source_name": "KRX_OPEN_API_KOSPI_INDEX_FIXTURE",
        "market": "KOREA",
        "index_identity": "KOSPI::코스피",
        "market_timezone": "Asia/Seoul",
        "run_mode": mode,
        "observation_date": dates[-1],
        "fetched_at": "2026-08-18T18:05:00+09:00" if mode == "FORWARD_SHADOW" else "2026-08-19T09:00:00+09:00",
        "available_at": "2026-08-18T18:00:00+09:00",
        "decision_at": "2026-08-18T18:10:00+09:00" if mode == "FORWARD_SHADOW" else None,
        "expected_session_dates": dates,
        "rows": [{"session_date": date, "close": close} for date, close in zip(dates, closes)],
    }


class KoreaRiskTest(unittest.TestCase):
    def build(self, data=None, **policy_updates):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", **policy_updates)
            return MODULE.build_transform(data or payload(), policy)

    def test_default_policy_is_unratified_and_authority_closed(self):
        self.assertEqual(MODULE.load_contract()["stress_calibration_status"], "UNRATIFIED")
        self.assertEqual(MODULE.load_input_policy()["approval_status"], "UNRATIFIED")
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "INPUT_POLICY_UNRATIFIED"):
            MODULE.build_transform(payload())

    def test_forward_features_are_deterministic_and_uncalibrated(self):
        result = self.build()
        expected_current = Decimal(100) / Decimal(120) - 1
        expected_max = Decimal(80) / Decimal(120) - 1
        self.assertEqual(result["status"], "AVAILABLE_UNCALIBRATED")
        self.assertEqual(result["temporal_eligibility"]["eligibility"], "FORWARD_PIT_QUALIFIED")
        self.assertEqual(result["drawdown"]["current_fraction"], MODULE.render(expected_current, 12))
        self.assertEqual(result["drawdown"]["maximum_fraction"], MODULE.render(expected_max, 12))
        self.assertEqual(result["stress_features"]["classification"], "UNDEFINED")
        for key in ("stress_threshold_authorized", "stress_classification_authorized", "regime_score_authorized", "production_wiring_authorized", "trading_action_authorized"):
            self.assertFalse(result[key])

    def test_replay_is_separate_and_has_no_decision(self):
        result = self.build(payload(mode="HISTORICAL_REPLAY"))
        self.assertEqual(result["status"], "CAUSAL_REPLAY_ONLY")
        self.assertFalse(result["temporal_eligibility"]["authoritative_historical_pit"])
        bad = payload(mode="HISTORICAL_REPLAY")
        bad["decision_at"] = "2026-08-19T09:01:00+09:00"
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "REPLAY_DECISION_MUST_BE_NULL"):
            self.build(bad)

    def test_session_gap_duplicate_and_order_fail_closed(self):
        for mutation in ("gap", "duplicate", "order"):
            data = payload()
            if mutation == "gap":
                del data["rows"][2]
            elif mutation == "duplicate":
                data["expected_session_dates"][2] = data["expected_session_dates"][1]
            else:
                data["rows"][1], data["rows"][2] = data["rows"][2], data["rows"][1]
            with self.assertRaisesRegex(MODULE.KoreaRiskError, "SESSION_"):
                self.build(data)

    def test_temporal_order_and_policy_cutoff_fail_closed(self):
        early = payload()
        early["available_at"] = "2026-08-18T17:59:59+09:00"
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "AVAILABLE_BEFORE_POLICY_CUTOFF"):
            self.build(early)
        reversed_fetch = payload()
        reversed_fetch["fetched_at"] = "2026-08-18T17:59:59+09:00"
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "FETCH_PRECEDES_AVAILABLE"):
            self.build(reversed_fetch)
        reversed_decision = payload()
        reversed_decision["decision_at"] = "2026-08-18T18:04:59+09:00"
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "DECISION_PRECEDES_FETCH"):
            self.build(reversed_decision)
        wrong_zone = payload()
        wrong_zone["fetched_at"] = "2026-08-18T09:05:00Z"
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "TIMESTAMP_INVALID"):
            self.build(wrong_zone)

    def test_identity_and_number_types_are_exact(self):
        wrong = payload()
        wrong["index_identity"] = "KOSDAQ::코스닥"
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "INPUT_IDENTITY_MISMATCH"):
            self.build(wrong)
        numeric = payload()
        numeric["rows"][0]["close"] = 100
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "NUMBER_MUST_BE_DECIMAL_STRING"):
            self.build(numeric)

    def test_insufficient_history_and_incomplete_ratification_stop(self):
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "INSUFFICIENT_HISTORY"):
            self.build(payload(), realized_vol_lookback_returns=6)
        with self.assertRaisesRegex(MODULE.KoreaRiskError, "INPUT_POLICY_INVALID"):
            self.build(payload(), publication_timing_source=None)

    def test_output_is_non_reconstructive(self):
        result = self.build()
        encoded = json.dumps(result)
        self.assertNotIn("rows", result)
        self.assertNotIn("expected_session_dates", result)
        self.assertNotIn('"120"', encoded)
        self.assertNotIn('"90"', encoded)


if __name__ == "__main__":
    unittest.main()
