#!/usr/bin/env python3
"""P1-US-07 offline US stress replay packet regression."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "us_stress_replay_packet.py"
WORKFLOWS = ROOT / ".github" / "workflows"

SPEC = importlib.util.spec_from_file_location("us_stress_replay_packet", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def write_json(path, payload):
    path = Path(path)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def case(
    case_id,
    context,
    dates,
    temporal_use="CAUSAL_RESEARCH_ONLY",
):
    return {
        "case_id": case_id,
        "context": context,
        "start_date": dates[0],
        "end_date": dates[-1],
        "expected_market_dates": dates,
        "temporal_use": temporal_use,
        "reason": "synthetic offline contract fixture",
    }


def cases():
    return [
        case(
            "stress-2008",
            "STRESS_2008",
            ["2008-09-15", "2008-09-16"],
        ),
        case(
            "recent-bull",
            "RECENT_BULL",
            ["2024-06-03", "2024-06-04"],
        ),
        case(
            "recent-bear",
            "RECENT_BEAR",
            ["2025-04-07", "2025-04-08"],
            temporal_use="REVISED_SENSITIVITY_ONLY",
        ),
        case(
            "recent-sideways",
            "RECENT_SIDEWAYS",
            ["2026-08-17", "2026-08-18"],
        ),
    ]


def write_policy(path, records=None, approval="RATIFIED"):
    return write_json(
        path,
        {
            "schema_version": 1,
            "policy_version": "us_stress_replay_cases/test-v1",
            "approval_status": approval,
            "effective_from": "2026-08-20" if approval == "RATIFIED" else None,
            "source_policy_version": (
                "us_regime_replay_source/v1"
                if approval == "RATIFIED"
                else None
            ),
            "cases": cases() if records is None else records,
        },
    )


def defined(day, available_at, character="a"):
    return {
        "status": "DEFINED",
        "observation_date": day,
        "available_at": available_at,
        "transform_version": "us_risk/v1",
        "evidence": {
            "uri": f"evidence/us-risk/{day}.json",
            "sha256": character * 64,
        },
        "warnings": ["STRESS_THRESHOLDS_UNCALIBRATED"],
    }


def regime_output(day, market="US"):
    generated = f"{day}T21:00:00Z"
    factors = None
    if market == "US":
        factors = {
            "RISK_VOL": defined(day, f"{day}T20:00:00Z")
        }
    return MODULE.REGIME.build_unknown_output(market, generated, factors)


def payload(records=None):
    records = cases() if records is None else records
    return {
        "schema_version": 1,
        "policy_version": "us_stress_replay_cases/test-v1",
        "cases": [
            {
                "case_id": item["case_id"],
                "outputs": [
                    regime_output(day)
                    for day in item["expected_market_dates"]
                ],
            }
            for item in records
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


class USStressReplayPacketTest(unittest.TestCase):
    def test_contract_and_default_policy_keep_replay_authority_closed(self):
        policy = MODULE.load_case_policy()

        self.assertEqual(
            CONTRACT["required_contexts"],
            [
                "STRESS_2008",
                "RECENT_BULL",
                "RECENT_BEAR",
                "RECENT_SIDEWAYS",
            ],
        )
        self.assertEqual(
            CONTRACT["authoritative_historical_pit_status"],
            "UNAVAILABLE",
        )
        self.assertEqual(policy["approval_status"], "UNRATIFIED")
        self.assertEqual(policy["cases"], [])
        with self.assertRaisesRegex(
            MODULE.StressReplayError, "CASE_POLICY_UNRATIFIED"
        ):
            MODULE.build_packet(payload())

    def test_complete_research_packet_is_deterministic_and_unclassified(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            result = MODULE.build_packet(payload(), case_policy_path=policy)

            self.assertEqual(
                result["status"], "RESEARCH_PACKET_AVAILABLE_UNCALIBRATED"
            )
            self.assertTrue(result["context_coverage"]["complete"])
            self.assertEqual(result["case_count"], 4)
            self.assertEqual(
                [item["context"] for item in result["cases"]],
                CONTRACT["required_contexts"],
            )
            self.assertEqual(
                result["cases"][0]["behavior_assessment"],
                "UNDEFINED_UNCALIBRATED",
            )
            self.assertEqual(
                result["cases"][0]["points"][0]["regime"], "UNKNOWN"
            )
            self.assertFalse(result["authoritative_historical_pit"])
            self.assertFalse(result["behavior_assessment_authorized"])
            self.assertFalse(result["thresholds_authorized"])
            self.assertFalse(result["weights_authorized"])
            self.assertFalse(result["production_wiring_authorized"])
            self.assertFalse(result["trading_action_authorized"])

    def test_case_policy_requires_every_context_and_2008_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = write_policy(
                Path(tmp) / "missing.json", records=cases()[:-1]
            )
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "required contexts incomplete"
            ):
                MODULE.load_case_policy(missing)

            wrong_year = cases()
            wrong_year[0] = case(
                "stress-2008",
                "STRESS_2008",
                ["2009-01-05", "2009-01-06"],
            )
            invalid = write_policy(
                Path(tmp) / "wrong-year.json", records=wrong_year
            )
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "STRESS_2008 dates must be in 2008"
            ):
                MODULE.load_case_policy(invalid)

    def test_input_case_order_and_exact_date_coverage_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            wrong_order = payload()
            wrong_order["cases"][0], wrong_order["cases"][1] = (
                wrong_order["cases"][1],
                wrong_order["cases"][0],
            )
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "CASE_SET_MISMATCH"
            ):
                MODULE.build_packet(
                    wrong_order, case_policy_path=policy
                )

            missing_point = payload()
            missing_point["cases"][0]["outputs"].pop()
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "CASE_DATE_COVERAGE_MISMATCH"
            ):
                MODULE.build_packet(
                    missing_point, case_policy_path=policy
                )

    def test_source_market_date_and_regime_authority_are_revalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            wrong_market = payload()
            wrong_market["cases"][0]["outputs"][0] = regime_output(
                "2008-09-15", market="CRYPTO"
            )
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "SOURCE_MARKET_INVALID"
            ):
                MODULE.build_packet(
                    wrong_market, case_policy_path=policy
                )

            wrong_date = payload()
            wrong_date["cases"][0]["outputs"][0] = regime_output(
                "2008-09-17"
            )
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "SOURCE_DATE_MISMATCH"
            ):
                MODULE.build_packet(
                    wrong_date, case_policy_path=policy
                )

            classified = payload()
            classified["cases"][0]["outputs"][0]["regime"] = "STRESS"
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "SOURCE_OUTPUT_INVALID.*REGIME_NOT_AUTHORIZED"
            ):
                MODULE.build_packet(
                    classified, case_policy_path=policy
                )

    def test_temporal_use_classes_remain_separate_and_do_not_grant_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            result = MODULE.build_packet(payload(), case_policy_path=policy)
            uses = {
                item["context"]: item["temporal_use"]
                for item in result["cases"]
            }
            self.assertEqual(
                uses["STRESS_2008"], "CAUSAL_RESEARCH_ONLY"
            )
            self.assertEqual(
                uses["RECENT_BEAR"], "REVISED_SENSITIVITY_ONLY"
            )
            self.assertNotIn(
                "AUTHORITATIVE_HISTORICAL_PIT", json.dumps(result)
            )
            self.assertFalse(result["regime_classification_authorized"])

    def test_float_rejection_derivation_validation_and_atomic_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            source = payload()
            first = MODULE.build_packet(source, case_policy_path=policy)
            second = MODULE.build_packet(source, case_policy_path=policy)
            output = Path(tmp) / "output" / "packet.json"
            MODULE.write_output(first, output)

            self.assertEqual(first, second)
            self.assertFalse(has_float(first))
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), first
            )
            self.assertFalse(list(output.parent.glob(".*.tmp.*")))
            self.assertEqual(
                MODULE.validate_packet(
                    first, source, case_policy_path=policy
                ),
                first,
            )
            tampered = copy.deepcopy(first)
            tampered["case_count"] = 3
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "PACKET_DERIVATION_MISMATCH"
            ):
                MODULE.validate_packet(
                    tampered, source, case_policy_path=policy
                )

            floating = payload()
            floating["cases"][0]["outputs"][0]["confidence"] = 0.5
            with self.assertRaisesRegex(
                MODULE.StressReplayError, "FLOAT_NOT_ALLOWED"
            ):
                MODULE.build_packet(
                    floating, case_policy_path=policy
                )

    def test_no_network_workflow_or_tracked_packet_wiring_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertNotIn("us_stress_replay_packet", workflows)


if __name__ == "__main__":
    unittest.main()
