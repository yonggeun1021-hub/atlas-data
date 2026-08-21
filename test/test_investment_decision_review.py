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
RATIFIED_FIXTURE = load("investment_ratified_fixture", ROOT / "test" / "test_ratified_rule_decision.py")
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

    def test_all_ratified_pass_creates_zero_capital_review_only_draft(self):
        rules = RATIFIED_FIXTURE.packet()
        value = thesis(rules)
        packet = MODULE.build_packet(value, rules, "2026-08-24T00:01:00Z")
        self.assertEqual(packet["buy_review"]["outcome"], "PASS")
        self.assertEqual(packet["buy_review"]["blockers"], [])
        proposal = packet["trade_proposal"]
        self.assertEqual(proposal["mode"], "REVIEW_ONLY_ZERO_CAPITAL")
        self.assertIsNone(proposal["position_size"])
        self.assertIsNone(proposal["risk_budget"])
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["broker_submission"])
        self.assertFalse(proposal["capital_authorized"])
        self.assertIsNone(proposal["order_intent"])

    def test_any_ratified_fail_rejects_without_proposal(self):
        rows = RATIFIED_FIXTURE.results()
        rows[0]["result"] = "FAIL"
        rules = RATIFIED_FIXTURE.packet(rows)
        packet = MODULE.build_packet(thesis(rules), rules, "2026-08-24T00:01:00Z")
        self.assertEqual(packet["buy_review"]["outcome"], "REJECTED")
        self.assertEqual(packet["buy_review"]["blockers"], [])
        self.assertIsNone(packet["trade_proposal"])

    def test_self_rehashed_pass_with_failed_rule_is_rejected(self):
        rules = RATIFIED_FIXTURE.packet()
        packet = MODULE.build_packet(thesis(rules), rules, "2026-08-24T00:01:00Z")
        packet["buy_review"]["required_rule_results"][0]["result"] = "FAIL"
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.InvestmentDecisionReviewError,
            "OUTPUT_OUTCOME_DERIVATION_INVALID",
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
