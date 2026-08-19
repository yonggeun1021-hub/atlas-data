#!/usr/bin/env python3
"""P1-COM-04 pre-score Regime replay determinism regression."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "replay_harness.py"
WORKFLOWS = ROOT / ".github" / "workflows"

SPEC = importlib.util.spec_from_file_location("regime_replay_harness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def defined(day, available, transform, character):
    return {
        "status": "DEFINED",
        "observation_date": day,
        "available_at": available,
        "transform_version": transform,
        "evidence": {
            "uri": f"evidence/{transform}/{day}.json",
            "sha256": character * 64,
        },
        "warnings": [],
    }


def output(market, generated="2026-08-20T12:00:00Z", defined_axis=True):
    factors = None
    if defined_axis:
        day = {
            "US": "2026-08-19",
            "KR": "2026-08-20",
            "CRYPTO": "2026-08-20",
        }[market]
        factors = {
            "TREND": defined(
                day,
                "2026-08-20T10:00:00Z",
                "market_trend/v1",
                {"US": "a", "KR": "b", "CRYPTO": "c"}[market],
            )
        }
    return MODULE.OUTPUT.build_unknown_output(market, generated, factors)


def payload():
    cases = []
    for case_id, market in (
        ("crypto-current", "CRYPTO"),
        ("kr-current", "KR"),
        ("us-current", "US"),
    ):
        first = output(market)
        cases.append(
            {
                "case_id": case_id,
                "first": first,
                "rerun": copy.deepcopy(first),
            }
        )
    return {"schema_version": 1, "cases": cases}


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class RegimeReplayHarnessTest(unittest.TestCase):
    def test_contract_pins_pre_score_unknown_only_boundary(self):
        self.assertEqual(
            CONTRACT["source_contract_mode"], "PRE_SCORE_UNKNOWN_ONLY"
        )
        self.assertEqual(CONTRACT["required_markets"], ["US", "KR", "CRYPTO"])
        self.assertEqual(CONTRACT["minimum_coverage_policy"], "UNRATIFIED")
        self.assertEqual(
            CONTRACT["comparison_policy"],
            "canonical_byte_identical_first_and_rerun",
        )

    def test_all_markets_identical_reruns_produce_explainable_report(self):
        result = MODULE.build_report(payload())

        self.assertEqual(result["status"], "DETERMINISM_VERIFIED_PRE_SCORE")
        self.assertTrue(result["market_coverage"]["complete"])
        self.assertEqual(result["case_count"], 3)
        self.assertEqual(
            [item["case_id"] for item in result["cases"]],
            ["crypto-current", "kr-current", "us-current"],
        )
        self.assertTrue(all(item["regime"] == "UNKNOWN" for item in result["cases"]))
        self.assertTrue(
            all(item["coverage"]["ratio"] == "1/5" for item in result["cases"])
        )
        self.assertTrue(
            all(item["factor_evidence_sha256"]["TREND"] for item in result["cases"])
        )
        self.assertFalse(result["minimum_coverage_gate_ratified"])
        self.assertFalse(result["thresholds_authorized"])
        self.assertFalse(result["weights_authorized"])
        self.assertFalse(result["regime_classification_authorized"])
        self.assertFalse(result["hysteresis_authorized"])
        self.assertFalse(result["production_wiring_authorized"])
        self.assertFalse(result["trading_action_authorized"])

    def test_market_coverage_and_case_order_fail_closed(self):
        missing = payload()
        missing["cases"].pop()
        with self.assertRaisesRegex(
            MODULE.ReplayHarnessError, "MARKET_COVERAGE_INCOMPLETE"
        ):
            MODULE.build_report(missing)

        duplicate = payload()
        duplicate["cases"][1]["case_id"] = duplicate["cases"][0]["case_id"]
        with self.assertRaisesRegex(
            MODULE.ReplayHarnessError, "CASE_ORDER_OR_DUPLICATE"
        ):
            MODULE.build_report(duplicate)

        reordered = payload()
        reordered["cases"].reverse()
        with self.assertRaisesRegex(
            MODULE.ReplayHarnessError, "CASE_ORDER_OR_DUPLICATE"
        ):
            MODULE.build_report(reordered)

    def test_valid_but_different_rerun_is_replay_drift(self):
        source = payload()
        source["cases"][0]["rerun"] = output(
            "CRYPTO", generated="2026-08-20T12:00:01Z"
        )
        with self.assertRaisesRegex(
            MODULE.ReplayHarnessError, "REPLAY_DRIFT.*crypto-current"
        ):
            MODULE.build_report(source)

    def test_source_tamper_is_rejected_before_comparison(self):
        source = payload()
        source["cases"][0]["first"]["regime"] = "NEUTRAL"
        source["cases"][0]["rerun"]["regime"] = "NEUTRAL"
        with self.assertRaisesRegex(
            MODULE.ReplayHarnessError, "SOURCE_OUTPUT_INVALID.*REGIME_NOT_AUTHORIZED"
        ):
            MODULE.build_report(source)

    def test_zero_coverage_is_not_misreported_as_missing_replay(self):
        source = payload()
        empty = output("CRYPTO", defined_axis=False)
        source["cases"][0]["first"] = empty
        source["cases"][0]["rerun"] = copy.deepcopy(empty)
        result = MODULE.build_report(source)
        crypto = result["cases"][0]

        self.assertEqual(crypto["coverage"]["ratio"], "0/5")
        self.assertEqual(crypto["status"], "DETERMINISM_VERIFIED_PRE_SCORE")
        self.assertIn("AXIS_COVERAGE_INCOMPLETE", crypto["warnings"])
        self.assertFalse(result["minimum_coverage_gate_ratified"])

    def test_report_is_deterministic_float_free_atomic_and_tamper_evident(self):
        source = payload()
        first = MODULE.build_report(source)
        second = MODULE.build_report(source)
        self.assertEqual(first, second)
        self.assertFalse(has_float(first))

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "output" / "replay-report.json"
            MODULE.write_output(first, target)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), first)
            self.assertFalse(list(target.parent.glob(".*.tmp.*")))

        self.assertEqual(MODULE.validate_report(first, source), first)
        tampered = copy.deepcopy(first)
        tampered["cases"][0]["regime"] = "STRESS"
        with self.assertRaisesRegex(
            MODULE.ReplayHarnessError, "REPORT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_report(tampered, source)

        floating = payload()
        floating["cases"][0]["first"]["confidence"] = 0.5
        with self.assertRaisesRegex(
            MODULE.ReplayHarnessError, "FLOAT_NOT_ALLOWED"
        ):
            MODULE.build_report(floating)

    def test_no_network_workflow_or_tracked_report_wiring_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertNotIn("replay_harness.py", workflows)
        self.assertNotIn("regime_replay_harness", workflows)


if __name__ == "__main__":
    unittest.main()
