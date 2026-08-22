#!/usr/bin/env python3
"""P10-07 zero-capital Alpha Shadow Ledger regression."""
from __future__ import annotations

import copy
import importlib.util
import inspect
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load("alpha_shadow_ledger", ROOT / "shadow" / "alpha_shadow_ledger.py")
CONTRACT = MODULE.load_contract()
AR_FIXTURE = load("alpha_shadow_ar_fixture", ROOT / "test" / "test_alpha_review.py")


def review(opportunity_state_case="anticipatory_review"):
    cases = {
        "blocked_no_evidence": (AR_FIXTURE.ft_no_evidence(), AR_FIXTURE.eg_positive_proxy(), AR_FIXTURE.pr_partially_reflected()),
        "blocked_triple_unknown": (AR_FIXTURE.ft_status("UNKNOWN"), AR_FIXTURE.eg_unknown(), AR_FIXTURE.pr_unknown()),
        "rejected_overextended": (AR_FIXTURE.ft_status("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_negative_proxy(), AR_FIXTURE.pr_overextended()),
        "rejected_disappointed": (AR_FIXTURE.ft_status("CONVERSION_DISAPPOINTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_partially_reflected()),
        "anticipatory_review": (AR_FIXTURE.ft_status("PRE_REVENUE_SIGNAL"), AR_FIXTURE.eg_positive_proxy(), AR_FIXTURE.pr_partially_reflected()),
        "expectation_exhausted": (AR_FIXTURE.ft_status("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_positive_proxy(), AR_FIXTURE.pr_fully_reflected()),
        "wait_for_pullback": (AR_FIXTURE.ft_status("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_overextended()),
        "confirmation_review": (AR_FIXTURE.ft_status("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_partially_reflected()),
        "wait_for_evidence": (AR_FIXTURE.ft_status("PRE_REVENUE_SIGNAL"), AR_FIXTURE.eg_unknown(), AR_FIXTURE.pr_partially_reflected()),
        "early_discovery": (AR_FIXTURE.ft_status("PRE_REVENUE_SIGNAL"), AR_FIXTURE.eg_unknown(), AR_FIXTURE.pr_under_reflected()),
    }
    ft, eg, pr = cases[opportunity_state_case]
    return AR_FIXTURE.build(ft, eg, pr)


CASE_BY_STATE = {
    "BLOCKED": "blocked_no_evidence",
    "REJECTED": "rejected_overextended",
    "ANTICIPATORY_REVIEW": "anticipatory_review",
    "EXPECTATION_EXHAUSTED": "expectation_exhausted",
    "WAIT_FOR_PULLBACK": "wait_for_pullback",
    "CONFIRMATION_REVIEW": "confirmation_review",
    "WAIT_FOR_EVIDENCE": "wait_for_evidence",
    "EARLY_DISCOVERY": "early_discovery",
}


class AlphaShadowLedgerTests(unittest.TestCase):
    def test_authority_dict_exact_values(self):
        expected = {
            "append_only_alpha_observation": True,
            "stage_change_authorized": False,
            "shadow_eligibility_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
        self.assertEqual(CONTRACT["authority"], expected)
        record = MODULE.build_record(review(), "2026-08-20T10:00:00Z", 1)
        self.assertEqual(record["authority"], expected)

    def test_action_mapping_is_exhaustive_over_every_opportunity_state(self):
        self.assertEqual(set(CASE_BY_STATE), set(CONTRACT["opportunity_state_to_action"]))
        for state, case in CASE_BY_STATE.items():
            with self.subTest(state=state):
                packet = review(case)
                self.assertEqual(packet["opportunity_state"], state)
                record = MODULE.build_record(packet, "2026-08-20T10:00:00Z", 1)
                expected_action = CONTRACT["opportunity_state_to_action"][state]
                self.assertEqual(record["shadow_proposal"]["action"], expected_action)

    def test_capital_is_always_zero_and_cannot_be_overridden(self):
        # No parameter in build_record()'s live signature can ever set capital.
        params = set(inspect.signature(MODULE.build_record).parameters)
        self.assertNotIn("capital", params)
        for case in CASE_BY_STATE.values():
            record = MODULE.build_record(review(case), "2026-08-20T10:00:00Z", 1)
            self.assertEqual(record["shadow_proposal"]["capital"], 0)
            self.assertNotIsInstance(record["shadow_proposal"]["capital"], bool)

    def test_human_approval_required_is_always_true_and_cannot_be_overridden(self):
        params = set(inspect.signature(MODULE.build_record).parameters)
        self.assertNotIn("human_approval_required", params)
        for case in CASE_BY_STATE.values():
            record = MODULE.build_record(review(case), "2026-08-20T10:00:00Z", 1)
            self.assertIs(record["shadow_proposal"]["human_approval_required"], True)

    def test_validate_record_rejects_nonzero_capital_and_false_human_approval(self):
        record = MODULE.build_record(review(), "2026-08-20T10:00:00Z", 1)
        for key, value, code in (
            ("capital", 1, "SHADOW_PROPOSAL_CAPITAL_MUST_BE_ZERO"),
            ("human_approval_required", False, "SHADOW_PROPOSAL_HUMAN_APPROVAL_MUST_BE_TRUE"),
        ):
            tampered = copy.deepcopy(record)
            tampered["shadow_proposal"][key] = value
            tampered["entry_hash"] = MODULE.payload_sha256(
                {k: v for k, v in tampered.items() if k != "entry_hash"}
            )
            with self.assertRaisesRegex(MODULE.AlphaShadowLedgerError, code):
                MODULE.validate_record(tampered, CONTRACT)

    def test_signal_date_and_expiry_and_lineage(self):
        packet = review()
        record = MODULE.build_record(packet, "2026-08-20T10:00:00Z", 1)
        self.assertEqual(record["signal_date"], packet["decision_date"])
        self.assertEqual(record["subject"], packet["subject"])
        self.assertEqual(record["alpha_review_packet_sha256"], packet["packet_sha256"])
        self.assertEqual(record["shadow_proposal"]["expiry"], packet["next_review_date"])

    def test_chain_requires_previous_hash_after_genesis(self):
        first = MODULE.build_record(review(), "2026-08-20T10:00:00Z", 1)
        second = MODULE.build_record(review(), "2026-08-21T10:00:00Z", 2, first["entry_hash"])
        self.assertEqual(second["previous_entry_hash"], first["entry_hash"])
        with self.assertRaisesRegex(MODULE.AlphaShadowLedgerError, "PREVIOUS_ENTRY_HASH_INVALID"):
            MODULE.build_record(review(), "2026-08-21T10:00:00Z", 2)

    def test_genesis_record_rejects_a_previous_hash(self):
        with self.assertRaisesRegex(MODULE.AlphaShadowLedgerError, "GENESIS_PREVIOUS_HASH_MUST_BE_NULL"):
            MODULE.build_record(review(), "2026-08-20T10:00:00Z", 1, "a" * 64)

    def test_tamper_detection_rejects_mutated_record(self):
        record = MODULE.build_record(review(), "2026-08-20T10:00:00Z", 1)
        tampered = copy.deepcopy(record)
        tampered["shadow_proposal"]["action"] = "WAIT"
        with self.assertRaisesRegex(MODULE.AlphaShadowLedgerError, "ENTRY_HASH_MISMATCH"):
            MODULE.validate_record(tampered, CONTRACT)

    def test_determinism_same_input_yields_byte_identical_output(self):
        packet = review()
        first = MODULE.build_record(packet, "2026-08-20T10:00:00Z", 1)
        second = MODULE.build_record(copy.deepcopy(packet), "2026-08-20T10:00:00Z", 1)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(first["entry_hash"], second["entry_hash"])

    def test_ledger_module_exposes_no_update_or_delete_api(self):
        public_names = [name for name in dir(MODULE) if not name.startswith("_")]
        forbidden_substrings = ("update", "delete", "mutate", "amend", "remove")
        offending = [
            name for name in public_names
            if any(bad in name.lower() for bad in forbidden_substrings)
        ]
        self.assertEqual(offending, [])

    def test_retrospective_evaluation_fields_are_not_present(self):
        record = MODULE.build_record(review(), "2026-08-20T10:00:00Z", 1)
        out_of_scope_fields = (
            "catalyst_date", "hypothetical_return", "benchmark_relative_return",
            "maximum_adverse_excursion", "maximum_favorable_excursion",
            "invalidation_hit", "thesis_confirmation",
        )
        for field in out_of_scope_fields:
            self.assertNotIn(field, record)
            self.assertNotIn(field, record["shadow_proposal"])


if __name__ == "__main__":
    unittest.main()
