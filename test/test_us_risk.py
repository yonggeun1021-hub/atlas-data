#!/usr/bin/env python3
"""P1-US-05 transient-only US Risk/Vol contract regression.

Every price row is synthetic and lives in memory or a temporary directory.
The tests make no live request and write no tracked vendor data or feature.
"""

from decimal import Decimal
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "us_risk.py"
WORKFLOWS = ROOT / ".github" / "workflows"

SPEC = importlib.util.spec_from_file_location("us_risk", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def write_json(path, payload):
    path = Path(path)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_policy(
    path,
    price_basis="RAW",
    run_modes=None,
    asset="SPY",
    source="tiingo_eod",
):
    payload = {
        "schema_version": 1,
        "policy_version": "us_risk_input/test-v1",
        "approval_status": "RATIFIED",
        "effective_from": "2026-01-01",
        "source_name": source,
        "asset": asset,
        "quote_currency": "USD",
        "market_timezone": "America/New_York",
        "price_basis": price_basis,
        "allowed_run_modes": sorted(
            run_modes or ["FORWARD_SHADOW", "HISTORICAL_BACKFILL"]
        ),
        "session_calendar_source": "synthetic_xnys_fixture/v1",
        "realized_vol_lookback_returns": 3,
        "annualization_sessions": 252,
        "drawdown_lookback_closes": 5,
        "split_window_policy": "no_split_events_required",
    }
    return write_json(path, payload)


def recent_weekdays(count, end=dt.date(2026, 8, 18)):
    dates = []
    current = end
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= dt.timedelta(days=1)
    return sorted(dates)


def payload(
    closes=None,
    run_mode="FORWARD_SHADOW",
    price_basis="RAW",
    fetched_at="2026-08-18T20:20:00-04:00",
    decision_at="2026-08-18T20:30:00-04:00",
    asset="SPY",
    source="tiingo_eod",
):
    closes = closes or ["100", "100", "100", "100", "100"]
    dates = recent_weekdays(len(closes))
    if run_mode == "HISTORICAL_BACKFILL":
        fetched_at = "2026-08-19T12:00:00Z"
        decision_at = None
    return {
        "schema_version": 1,
        "source_name": source,
        "asset": asset,
        "quote_currency": "USD",
        "market_timezone": "America/New_York",
        "run_mode": run_mode,
        "price_basis": price_basis,
        "observation_date": dates[-1].isoformat(),
        "fetched_at": fetched_at,
        "decision_at": decision_at,
        "expected_session_dates": [day.isoformat() for day in dates],
        "rows": [
            {
                "session_date": day.isoformat(),
                "close": close,
                "split_factor": "1",
            }
            for day, close in zip(dates, closes)
        ],
    }


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class USRiskTest(unittest.TestCase):
    def test_contract_and_default_policy_keep_authority_closed(self):
        policy = MODULE.load_input_policy()

        self.assertEqual(
            CONTRACT["input_retention_policy"],
            "transient_memory_or_stdin_only",
        )
        self.assertEqual(
            CONTRACT["output_retention_policy"],
            "non_reconstructive_derived_features_only",
        )
        self.assertEqual(policy["approval_status"], "UNRATIFIED")
        self.assertIsNone(policy["asset"])
        self.assertIsNone(policy["realized_vol_lookback_returns"])

        with self.assertRaisesRegex(
            MODULE.USRiskError, "INPUT_POLICY_UNRATIFIED"
        ):
            MODULE.build_transform(payload())

        with tempfile.TemporaryDirectory() as tmp:
            incomplete = json.loads(
                MODULE.INPUT_POLICY_PATH.read_text(encoding="utf-8")
            )
            incomplete["approval_status"] = "RATIFIED"
            incomplete_path = write_json(
                Path(tmp) / "incomplete.json", incomplete
            )
            with self.assertRaisesRegex(
                MODULE.USRiskError, "INPUT_POLICY_INVALID"
            ):
                MODULE.build_transform(
                    payload(), input_policy_path=incomplete_path
                )

    def test_forward_qualified_input_builds_uncalibrated_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            result = MODULE.build_transform(
                payload(["100", "120", "90", "80", "100"]),
                input_policy_path=policy,
            )

            self.assertEqual(result["market"], "US")
            self.assertEqual(result["asset"], "SPY")
            self.assertEqual(result["status"], "AVAILABLE_UNCALIBRATED")
            self.assertEqual(
                result["temporal_eligibility"]["eligibility"],
                "FORWARD_PIT_QUALIFIED",
            )
            self.assertEqual(
                result["available_at"], "2026-08-18T20:20:00-04:00"
            )
            decline = result["drawdown"]
            self.assertEqual(
                decline["current_fraction"],
                MODULE.render_decimal(Decimal(100) / Decimal(120) - 1, 12),
            )
            self.assertEqual(
                decline["maximum_fraction"],
                MODULE.render_decimal(Decimal(80) / Decimal(120) - 1, 12),
            )
            self.assertEqual(
                result["stress_features"]["classification"], "UNDEFINED"
            )
            self.assertFalse(result["stress_threshold_authorized"])
            self.assertFalse(result["stress_classification_authorized"])
            self.assertFalse(result["regime_score_authorized"])
            self.assertFalse(result["production_wiring_authorized"])
            self.assertFalse(result["trading_action_authorized"])

    def test_forward_cutoff_and_decision_order_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            early = payload(fetched_at="2026-08-18T20:14:59-04:00")
            with self.assertRaisesRegex(
                MODULE.USRiskError,
                "TEMPORAL_INPUT_NOT_QUALIFIED.*FETCH_BEFORE",
            ):
                MODULE.build_transform(early, input_policy_path=policy)

            reversed_time = payload(
                fetched_at="2026-08-18T20:20:00-04:00",
                decision_at="2026-08-18T20:19:59-04:00",
            )
            with self.assertRaisesRegex(
                MODULE.USRiskError,
                "TEMPORAL_INPUT_NOT_QUALIFIED.*DECISION_PRECEDES",
            ):
                MODULE.build_transform(
                    reversed_time, input_policy_path=policy
                )

    def test_historical_raw_and_adjusted_classes_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_policy = write_policy(Path(tmp) / "raw.json")
            raw = MODULE.build_transform(
                payload(run_mode="HISTORICAL_BACKFILL"),
                input_policy_path=raw_policy,
            )
            self.assertEqual(
                raw["temporal_eligibility"]["eligibility"],
                "CAUSAL_RESEARCH_ONLY",
            )
            self.assertEqual(raw["status"], "CAUSAL_RESEARCH_ONLY")
            self.assertFalse(
                raw["temporal_eligibility"]["authoritative_historical_pit"]
            )

            adjusted_policy = write_policy(
                Path(tmp) / "adjusted.json", price_basis="ADJUSTED"
            )
            adjusted = MODULE.build_transform(
                payload(
                    run_mode="HISTORICAL_BACKFILL",
                    price_basis="ADJUSTED",
                ),
                input_policy_path=adjusted_policy,
            )
            self.assertEqual(
                adjusted["temporal_eligibility"]["eligibility"],
                "REVISED_SENSITIVITY_ONLY",
            )
            self.assertEqual(
                adjusted["status"], "REVISED_SENSITIVITY_ONLY"
            )
            self.assertFalse(adjusted["regime_score_authorized"])

    def test_session_gap_duplicate_and_split_event_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            missing = payload()
            del missing["rows"][2]
            with self.assertRaisesRegex(
                MODULE.USRiskError, "SESSION_COVERAGE_MISMATCH"
            ):
                MODULE.build_transform(missing, input_policy_path=policy)

            duplicate = payload()
            duplicate["expected_session_dates"][1] = duplicate[
                "expected_session_dates"
            ][0]
            with self.assertRaisesRegex(
                MODULE.USRiskError, "SESSION_CALENDAR_INVALID"
            ):
                MODULE.build_transform(duplicate, input_policy_path=policy)

            split = payload()
            split["rows"][2]["split_factor"] = "2"
            with self.assertRaisesRegex(
                MODULE.USRiskError, "SPLIT_EVENT_IN_WINDOW"
            ):
                MODULE.build_transform(split, input_policy_path=policy)

    def test_source_asset_basis_and_string_number_policy_are_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            wrong_source = payload(source="another_vendor")
            with self.assertRaisesRegex(
                MODULE.USRiskError, "INPUT_POLICY_MISMATCH.*source_name"
            ):
                MODULE.build_transform(
                    wrong_source, input_policy_path=policy
                )

            wrong_asset = payload(asset="QQQ")
            with self.assertRaisesRegex(
                MODULE.USRiskError, "INPUT_POLICY_MISMATCH.*asset"
            ):
                MODULE.build_transform(wrong_asset, input_policy_path=policy)

            numeric_float = payload()
            numeric_float["rows"][0]["close"] = 100.0
            with self.assertRaisesRegex(
                MODULE.USRiskError, "INPUT_NUMBER_MUST_BE_STRING"
            ):
                MODULE.build_transform(
                    numeric_float, input_policy_path=policy
                )

    def test_output_is_non_reconstructive_deterministic_and_atomic(self):
        sentinel = "123.456789123456789"
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            source = payload([sentinel] * 5)
            first = MODULE.build_transform(source, input_policy_path=policy)
            second = MODULE.build_transform(source, input_policy_path=policy)
            output = Path(tmp) / "output" / "us-risk.json"
            MODULE.write_output(first, output)
            rendered = output.read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertFalse(has_float(first))
            self.assertNotIn(sentinel, rendered)
            self.assertFalse(first["retention"]["vendor_rows_emitted"])
            self.assertFalse(first["retention"]["vendor_prices_emitted"])
            self.assertFalse(
                first["retention"]["reconstructive_series_emitted"]
            )
            self.assertEqual(json.loads(rendered), first)
            self.assertFalse(list(output.parent.glob(".*.tmp*")))

    def test_no_network_workflow_or_vendor_input_file_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )

        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertNotIn("TIINGO_API_KEY", script)
        self.assertNotIn("input_file", script)
        self.assertNotIn("us_risk.py", workflows)
        self.assertNotIn("us_risk", workflows)


if __name__ == "__main__":
    unittest.main()
