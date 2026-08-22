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

import contextlib
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


# CIO round 3, required item 4: states 4-10 of alpha_review's decision table
# only unlock once price_reflection.threshold_basis=="RATIFIED" -- see
# test_alpha_review.py's own ratified_thresholds() docstring for why this is
# a context-manager simulation (real ratification is a hardcoded literal in
# decision/price_reflection.py's own _expected_contract(), not a caller
# parameter). This file loads its OWN independent PRICE_REFLECTION/
# ALPHA_REVIEW module instances above, so it needs its own copy of the same
# patch, scoped to those specific instances.
@contextlib.contextmanager
def ratified_thresholds():
    top_level_original = PRICE_REFLECTION._expected_contract
    alpha_pr_original = ALPHA_REVIEW.PRICE_REFLECTION._expected_contract
    alpha_pr_contract_original = ALPHA_REVIEW.PR_CONTRACT

    def _patched(original):
        def _fn():
            value = original()
            value["classification_thresholds_approval_status"] = "RATIFIED"
            return value
        return _fn

    PRICE_REFLECTION._expected_contract = _patched(top_level_original)
    ALPHA_REVIEW.PRICE_REFLECTION._expected_contract = _patched(alpha_pr_original)
    ALPHA_REVIEW.PR_CONTRACT = ALPHA_REVIEW.PRICE_REFLECTION._expected_contract()
    try:
        yield PRICE_REFLECTION._expected_contract()
    finally:
        PRICE_REFLECTION._expected_contract = top_level_original
        ALPHA_REVIEW.PRICE_REFLECTION._expected_contract = alpha_pr_original
        ALPHA_REVIEW.PR_CONTRACT = alpha_pr_contract_original

# Expected post-CIO-Gate-Hardening results for the 4 real Pilot subjects.
#
# 298040.KS history: P8-10 round 1 (PR #212 initial) wired real KRX price
# history + a real chain-linked KOSPI benchmark and this subject briefly
# reached WAIT_FOR_EVIDENCE (price_reflection.status=PARTIALLY_REFLECTED).
# CIO review round 2 on the same PR found that classification a real defect
# -- momentum alone (no event/expectation reference point) was standing in
# for a reflection judgment. decision/price_reflection.py now splits
# price_state (pure momentum -- still real, still computed) from
# reflection_status (only ever non-UNKNOWN with a real reference point),
# and none of the 4 real Pilots' price_reflection inputs currently carry one
# (see decision/pilot_evidence_intake.py's price_reflection builders) -- so
# reflection_status is honestly UNKNOWN for all four, and 298040.KS is back
# to gate 3 (WAIT_FOR_PRICE), same as TSM. This is the CORRECT, retracted
# state per CIO instruction, not a regression: real, evidence-backed
# reflection verdicts require a real reference point, which does not yet
# exist for any Pilot. TSM/267260.KS/034020.KS are unaffected by this round
# (267260.KS's REJECTED and 034020.KS's BLOCKED are both reached via gates
# that run strictly before the price/reflection gate, independent of it).
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
            subject=PR_FIXTURE.REAL_EVIDENCE_SUBJECT,
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
            subject=PR_FIXTURE.REAL_EVIDENCE_SUBJECT,
            decision_date=decision_date,
            generated_at=generated_at,
            guidance_changes=EG_FIXTURE.category("POSITIVE"),
        )
        eg_packet = EXPECTATIONS_GAP.build_packet(eg_input, EG_FIXTURE.CONTRACT)
        self.assertNotEqual(eg_packet["expectations_gap"]["status"], "NEGATIVE")

        with ratified_thresholds() as ratified_contract:
            # CIO round 4: a real, HASH-VERIFIED evidence citation resolving
            # to a real, internally-computed return is required for
            # reflection_status to leave UNKNOWN -- decision/price_
            # reflection.py no longer accepts a caller-supplied
            # post_event_return_pct or a fabricated "a"*64 hash at all (see
            # that module's and test_price_reflection.py's own docstrings).
            # Without a genuinely real citation this synthetic fixture would
            # itself now hit gate 3 (WAIT_FOR_PRICE) and never reach gate 4,
            # defeating the point of this test. A RATIFIED threshold_basis
            # is also required (round 3/4, required item 4/6) for the same
            # reason -- this module's OWN loaded PRICE_REFLECTION instance
            # is used to build the event_reaction, matching
            # PR_FIXTURE.verified_event_reaction()'s real committed Event
            # Evidence Envelope fixture. CIO round 6: REGRESSION_FIXTURE is
            # no longer a legal capture_kind and any source_ref under
            # test/ is hard-refused by the real, unmocked citation
            # verifier -- this module's OWN loaded EVENT_EVIDENCE instance
            # (a FOURTH independent module load, separate from PR_FIXTURE's
            # own) needs its own copy of the same test-only mock PR_FIXTURE
            # defines, scoped to this one call only.
            original_verify = PRICE_REFLECTION.EVENT_EVIDENCE.verify_event_reaction_claim

            def _fake_verify(*, subject, event_at, direction, source_class, source_ref, source_sha256, decision_at):
                return {
                    "capture_kind": "LIVE_OFFICIAL_CAPTURE",
                    "first_authoritative_seen_at": "2026-08-01T00:00:00Z",
                    "raw_source_ref": source_ref, "raw_source_sha256": source_sha256,
                    "published_at": event_at, "locator": "TEST-ONLY-MOCKED-LOCATOR-BELOW-PRODUCTION-BOUNDARY",
                }

            PRICE_REFLECTION.EVENT_EVIDENCE.verify_event_reaction_claim = _fake_verify
            try:
                pr_packet = PRICE_REFLECTION.build_packet(
                    subject=PR_FIXTURE.REAL_EVIDENCE_SUBJECT,
                    decision_date=decision_date,
                    generated_at=generated_at,
                    price_as_of=PR_FIXTURE.REAL_EVIDENCE_PRICE_AS_OF,
                    recent_return_windows={"1m": "3"},
                    relative_strength={"vs_market": "2"},
                    event_reaction=PR_FIXTURE.verified_event_reaction(
                        PR_FIXTURE.PARTIALLY_FIXTURE, PR_FIXTURE.PARTIALLY_EVENT_AT,
                    ),  # real +5.10%
                    data_source_scope="KRX_OFFICIAL",
                    contract=ratified_contract,
                )
            finally:
                PRICE_REFLECTION.EVENT_EVIDENCE.verify_event_reaction_claim = original_verify
            self.assertNotEqual(pr_packet["price_reflection"]["reflection_status"], "UNKNOWN")

            alpha_packet = ALPHA_REVIEW.build_packet(
                forward_thesis_packet=ft_packet,
                expectations_gap_packet=eg_packet,
                price_reflection_packet=pr_packet,
                generated_at=generated_at,
            )
        self.assertEqual(alpha_packet["opportunity_state"], "WAIT_FOR_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
