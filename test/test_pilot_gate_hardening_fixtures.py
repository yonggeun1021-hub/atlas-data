#!/usr/bin/env python3
"""P8-11 CIO Gate Hardening — real Pilot fixture-pinning regression.

This is the load-bearing regression for the whole CIO Gate Hardening fix:
it calls the REAL `decision/pilot_evidence_intake.py:run_all_pilots()`
(same fixed `PILOT_DECISION_DATE`/`PILOT_GENERATED_AT` already defined
there, unchanged) against the real, already-committed evidence files for
the four Pilot subjects, and pins the exact post-hardening
`opportunity_state`/shadow `action` for each one.

The CIO review that triggered this hardening found: real Pilot runs
produced `SHADOW_ENTRY_REVIEW` even though `price_reflection.status==
UNKNOWN` for every Pilot, and even for a subject (267260.KS / HD Hyundai
Electric) with `expectations_gap.status==NEGATIVE`. This file's assertions
are the concrete proof that can never happen again without a test failure.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PILOT = load_module("gate_hardening_pilot_evidence_intake", ROOT / "decision" / "pilot_evidence_intake.py")
FORWARD_THESIS = load_module("gate_hardening_forward_thesis", ROOT / "decision" / "forward_thesis.py")
EXPECTATIONS_GAP = load_module("gate_hardening_expectations_gap", ROOT / "decision" / "expectations_gap.py")
PRICE_REFLECTION = load_module("gate_hardening_price_reflection", ROOT / "decision" / "price_reflection.py")
ALPHA_REVIEW = load_module("gate_hardening_alpha_review", ROOT / "decision" / "alpha_review.py")

# Reuse test_forward_thesis.py's/test_expectations_gap.py's/
# test_price_reflection.py's own fixture helpers -- same pattern
# test_alpha_review.py already uses -- so the gate-4 synthetic fixture below
# is built via the REAL forward_thesis.build_packet/expectations_gap.
# build_packet/price_reflection.build_packet functions, never a hand-rolled
# dict standing in for a validated packet.
FT_FIXTURE = load_module("gate_hardening_ft_fixture", ROOT / "test" / "test_forward_thesis.py")
EG_FIXTURE = load_module("gate_hardening_eg_fixture", ROOT / "test" / "test_expectations_gap.py")
PR_FIXTURE = load_module("gate_hardening_pr_fixture", ROOT / "test" / "test_price_reflection.py")

# Expected post-CIO-Gate-Hardening results for the 4 real Pilot subjects.
EXPECTED = {
    "TSM": ("WAIT_FOR_PRICE", "WAIT"),
    "298040.KS": ("WAIT_FOR_PRICE", "WAIT"),
    "267260.KS": ("REJECTED", "REJECT"),
    "034020.KS": ("BLOCKED", "REJECT"),
}


class RealPilotFixturePinningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = PILOT.run_all_pilots()

    def test_opportunity_state_and_shadow_action_pinned_per_subject(self):
        for subject, (expected_state, expected_action) in EXPECTED.items():
            with self.subTest(subject=subject):
                bundle = self.results[subject]
                alpha = bundle["alpha_review"]
                shadow = bundle["shadow_ledger_entry"]
                self.assertEqual(alpha["opportunity_state"], expected_state)
                self.assertEqual(shadow["shadow_proposal"]["action"], expected_action)

    def test_trade_proposal_always_null_capital_always_zero_human_approval_always_true(self):
        for subject in PILOT.PILOT_SUBJECTS:
            with self.subTest(subject=subject):
                bundle = self.results[subject]
                alpha = bundle["alpha_review"]
                shadow = bundle["shadow_ledger_entry"]
                self.assertIsNone(alpha["trade_proposal"])
                self.assertEqual(shadow["shadow_proposal"]["capital"], 0)
                self.assertIs(shadow["shadow_proposal"]["human_approval_required"], True)

    def test_blanket_no_real_pilot_ever_reaches_shadow_entry_review(self):
        # Deliberately redundant with the per-row table above -- a separate,
        # blanket assertion over ALL FOUR subjects' shadow_proposal.action,
        # so this regression is maximally hard to accidentally weaken later
        # (e.g. by someone only updating the per-row EXPECTED table but not
        # noticing a 5th subject was added that slipped through).
        for subject in PILOT.PILOT_SUBJECTS:
            with self.subTest(subject=subject):
                action = self.results[subject]["shadow_ledger_entry"]["shadow_proposal"]["action"]
                self.assertNotEqual(action, "SHADOW_ENTRY_REVIEW")

    def test_p5_rule_status_is_not_evaluated_for_every_real_pilot(self):
        # The precondition for the blanket assertion above: no ratified P5
        # packet exists for any of the four real Pilot subjects yet.
        for subject in PILOT.PILOT_SUBJECTS:
            with self.subTest(subject=subject):
                self.assertEqual(self.results[subject]["alpha_review"]["p5_rule_status"], "NOT_EVALUATED")


class SyntheticGate4NarrativeOnlyEvidenceTests(unittest.TestCase):
    """Proves gate 4 (narrative-only-core-evidence -> WAIT_FOR_EVIDENCE) is
    real and reachable, not dead code -- none of the 4 real Pilots exercise
    it on its own (they're all caught by gate 2 or gate 3 first), so this
    is a dedicated synthetic case with price KNOWN (non-UNKNOWN) and gap
    NOT NEGATIVE, but only NARRATIVE_SOURCED observed_facts.
    """

    def test_gate4_fires_when_price_known_gap_not_negative_and_no_exhibit_extracted_fact(self):
        decision_date = "2026-08-20"
        generated_at = "2026-08-20T09:00:00Z"

        ft_input = FT_FIXTURE.minimal_input(
            generated_at=generated_at,
            decision_date=decision_date,
            earnings_conversion=FT_FIXTURE.earnings_conversion(status="PRE_REVENUE_SIGNAL"),
            observed_facts=[FT_FIXTURE.observed_fact(source_class="NARRATIVE_SOURCED")],
            evidence_lineage=[FT_FIXTURE.evidence_entry(source_type="NARRATIVE_SOURCED")],
        )
        ft_packet = FORWARD_THESIS.build_packet(ft_input, FT_FIXTURE.CONTRACT)
        self.assertTrue(ft_packet["observed_facts"])
        self.assertTrue(all(f["source_class"] != "EXHIBIT_EXTRACTED" for f in ft_packet["observed_facts"]))

        eg_input = EG_FIXTURE.base_input(
            subject="TSM",
            decision_date=decision_date,
            generated_at=generated_at,
            guidance_changes=EG_FIXTURE.category("POSITIVE"),
        )
        eg_packet = EXPECTATIONS_GAP.build_packet(eg_input, EG_FIXTURE.CONTRACT)
        self.assertNotEqual(eg_packet["expectations_gap"]["status"], "NEGATIVE")

        pr_packet = PRICE_REFLECTION.build_packet(
            subject="TSM",
            decision_date=decision_date,
            generated_at=generated_at,
            price_as_of="2026-08-19T20:00:00Z",
            recent_return_windows={"1m": "3"},
            relative_strength={"vs_market": "2"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
            contract=PR_FIXTURE.CONTRACT,
        )
        self.assertNotEqual(pr_packet["price_reflection"]["status"], "UNKNOWN")

        alpha_packet = ALPHA_REVIEW.build_packet(
            forward_thesis_packet=ft_packet,
            expectations_gap_packet=eg_packet,
            price_reflection_packet=pr_packet,
            generated_at=generated_at,
        )
        self.assertEqual(alpha_packet["opportunity_state"], "WAIT_FOR_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
