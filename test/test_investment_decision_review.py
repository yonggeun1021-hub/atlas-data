#!/usr/bin/env python3
"""P8-07 Investment Decision Review regression."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load("investment_decision_review", ROOT / "decision" / "investment_decision_review.py")
RULE_FIXTURE = load("investment_rule_fixture", ROOT / "test" / "test_deterministic_rule_evaluator.py")
CONTRACT = MODULE.load_contract()


def rule_packet():
    return RULE_FIXTURE.MODULE.build_packet(
        RULE_FIXTURE.empty_binding_packet(), RULE_FIXTURE.RULES, RULE_FIXTURE.CONTRACT
    )


def thesis(packet=None):
    packet = rule_packet() if packet is None else packet
    evidence_set_sha256 = (
        packet["evidence_set_sha256"]
        if "evidence_set_sha256" in packet
        else packet["lineage"]["evidence_set_sha256"]
    )
    return {
        "thesis_id": "thesis:TSM:2026-08-24",
        "subject": "TSM",
        "as_of": "2026-08-24T00:00:00Z",
        "stage_observed": "READY",
        "statement": "Confirmed demand evidence may convert into TSM revenue.",
        "supporting_evidence_ids": ["ev:tsm:demand"],
        "counter_evidence_ids": ["ev:tsm:valuation"],
        "earnings_conversion": {
            "status": "SUPPORTED",
            "spend_owner": "Semiconductor customers",
            "revenue_recipient": "TSM",
            "conversion_window": "Next reported periods",
            "margin_visibility": "PARTIAL",
            "evidence_ids": ["ev:tsm:demand"],
        },
        "invalidation_conditions": ["Demand evidence is revised or withdrawn"],
        "evidence_set_sha256": evidence_set_sha256,
    }


class InvestmentDecisionReviewTests(unittest.TestCase):
    def test_current_p5_truthfully_blocks_and_never_emits_proposal(self):
        rules = rule_packet()
        packet = MODULE.build_packet(thesis(rules), rules, "2026-08-24T00:01:00Z")
        self.assertEqual(packet["buy_review"]["outcome"], "BLOCKED")
        self.assertIn("P5_PASS_FAIL_NOT_AUTHORIZED", packet["buy_review"]["blockers"])
        self.assertIn("P5_DOWNSTREAM_ACTION_NOT_AUTHORIZED", packet["buy_review"]["blockers"])
        self.assertIsNone(packet["trade_proposal"])
        self.assertFalse(packet["authority"]["stage_promotion_authorized"])
        self.assertFalse(packet["authority"]["trading_authorized"])

    def test_required_rule_unknown_is_not_offset(self):
        rules = rule_packet()
        packet = MODULE.build_packet(thesis(rules), rules, "2026-08-24T00:01:00Z")
        selected = packet["buy_review"]["required_rule_results"]
        self.assertEqual(
            [row["rule_id"] for row in selected],
            [f"RULE-{number:04d}" for number in range(3, 10)],
        )
        for row in selected:
            self.assertIn(row["result"], {"UNKNOWN", "UNDEFINED"})
            self.assertIn(
                f"RULE_{row['result']}:{row['rule_id']}",
                packet["buy_review"]["blockers"],
            )

    def test_evidence_lineage_mismatch_fails_closed(self):
        rules = rule_packet()
        value = thesis(rules)
        value["evidence_set_sha256"] = "f" * 64
        with self.assertRaisesRegex(MODULE.InvestmentDecisionReviewError, "EVIDENCE_SET_SHA_MISMATCH"):
            MODULE.build_packet(value, rules, "2026-08-24T00:01:00Z")

    def test_legacy_ratified_packet_without_exact_provenance_is_retired(self):
        forged = {"schema_version": "ratified_rule_decision_packet/1"}
        with self.assertRaisesRegex(
            MODULE.InvestmentDecisionReviewError,
            "RATIFIED_RULE_PACKET_V1_RETIRED_NO_PROVENANCE",
        ):
            MODULE.build_packet(thesis(), forged, "2026-08-24T00:01:00Z")

    def test_frozen_rule_packet_is_revalidated_not_just_hash_referenced(self):
        packet = MODULE.build_packet(thesis(), rule_packet(), "2026-08-24T00:01:00Z")
        packet["frozen_rule_packet"]["rules"][0]["result"] = "PASS"
        packet["frozen_rule_packet"]["packet_sha256"] = RULE_FIXTURE.MODULE.payload_sha256({
            key: value for key, value in packet["frozen_rule_packet"].items()
            if key != "packet_sha256"
        })
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(MODULE.InvestmentDecisionReviewError, "FROZEN_RULE_PACKET_INVALID"):
            MODULE.validate_packet(packet)

    def test_output_results_cannot_diverge_from_frozen_rule_packet_after_rehash(self):
        packet = MODULE.build_packet(thesis(), rule_packet(), "2026-08-24T00:01:00Z")
        packet["buy_review"]["required_rule_results"][0]["result"] = "PASS"
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.InvestmentDecisionReviewError,
            "OUTPUT_RULE_RESULTS_NOT_DERIVED_FROM_FROZEN_PACKET",
        ):
            MODULE.validate_packet(packet)

    def test_counter_evidence_and_invalidation_are_required(self):
        rules = rule_packet()
        value = thesis(rules)
        value["counter_evidence_ids"] = []
        with self.assertRaisesRegex(MODULE.InvestmentDecisionReviewError, "COUNTER_EVIDENCE_INVALID"):
            MODULE.build_packet(value, rules, "2026-08-24T00:01:00Z")
        value = thesis(rules)
        value["invalidation_conditions"] = []
        with self.assertRaisesRegex(MODULE.InvestmentDecisionReviewError, "INVALIDATION_CONDITIONS_INVALID"):
            MODULE.build_packet(value, rules, "2026-08-24T00:01:00Z")

    def test_output_hash_tamper_is_rejected(self):
        rules = rule_packet()
        packet = MODULE.build_packet(thesis(rules), rules, "2026-08-24T00:01:00Z")
        tampered = copy.deepcopy(packet)
        tampered["buy_review"]["blockers"].append("FAKE")
        with self.assertRaisesRegex(
            MODULE.InvestmentDecisionReviewError,
            "OUTPUT_(BOUNDARY_INVALID|PACKET_SHA_MISMATCH)",
        ):
            MODULE.validate_packet(tampered)

    def test_cli_writes_only_outside_repository(self):
        rules = rule_packet()
        envelope = {
            "thesis": thesis(rules),
            "rule_packet": rules,
            "generated_at": "2026-08-24T00:01:00Z",
        }
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.json"
            output = Path(folder) / "output.json"
            source.write_text(json.dumps(envelope), encoding="utf-8")
            self.assertEqual(MODULE.run(source, output), 0)
            self.assertEqual(
                MODULE.validate_packet(json.loads(output.read_text(encoding="utf-8"))),
                json.loads(output.read_text(encoding="utf-8")),
            )
            tracked = ROOT / "data" / "investment_decision_review_test.json"
            self.assertEqual(MODULE.run(source, tracked), 1)
            self.assertFalse(tracked.exists())


if __name__ == "__main__":
    unittest.main()
