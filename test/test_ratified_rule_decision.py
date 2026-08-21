#!/usr/bin/env python3
"""P5-02 externally ratified TSM Rule result validation regression."""
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ratified_rule_decision", ROOT / "rules" / "ratified_rule_decision.py"
)
MODULE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MODULE)
RULES = MODULE.load_rules()
REGISTRY = {row["rule_id"]: row for row in RULES["rules"]}


def results(result="PASS"):
    return [{
        "rule_id": rule_id,
        "subject": "TSM",
        "condition_text_sha256": REGISTRY[rule_id]["condition_text_sha256"],
        "result": result,
        "evidence_reference_ids": [f"evidence:{rule_id.lower()}"],
        "reason": "Externally reviewed against the canonical condition.",
    } for rule_id in [f"RULE-{number:04d}" for number in range(3, 10)]]


def packet(rows=None):
    return MODULE.build_packet(
        results() if rows is None else rows,
        "a" * 64,
        "2026-08-24T00:00:00Z",
        "cio:human-review",
        "authority:p5-02-tsm-v1",
        RULES,
    )


class RatifiedRuleDecisionTests(unittest.TestCase):
    def test_complete_external_slice_is_hash_bound(self):
        value = packet()
        self.assertEqual(value["summary"], {"total": 7, "PASS": 7, "FAIL": 0})
        self.assertEqual(MODULE.validate_packet(value, RULES), value)
        self.assertFalse(value["authority"]["action_authorized"])

    def test_partial_or_unknown_slice_is_rejected(self):
        with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "RESULT_RULE_SET_INVALID"):
            packet(results()[:-1])
        rows = results()
        rows[0]["result"] = "UNKNOWN"
        with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "RESULT_INVALID"):
            packet(rows)

    def test_subject_and_condition_hash_drift_are_rejected(self):
        for key, value in (("subject", "MU"), ("condition_text_sha256", "f" * 64)):
            rows = results()
            rows[0][key] = value
            with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "RESULT_INVALID"):
                packet(rows)

    def test_self_rehashed_result_change_is_rejected_by_summary(self):
        value = packet()
        value["results"][0]["result"] = "FAIL"
        value["packet_sha256"] = MODULE.payload_sha256({
            key: item for key, item in value.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "SUMMARY_MISMATCH"):
            MODULE.validate_packet(value, RULES)


if __name__ == "__main__":
    unittest.main()
