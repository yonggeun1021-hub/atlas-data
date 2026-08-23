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

★ CIO closing-fix ruling on PR #212 (2026-08-23): this file used to also
  carry `SyntheticGate4NarrativeOnlyEvidenceTests`, proving `decision/
  alpha_review.py`'s narrative-only-core-evidence gate (old gate 4,
  `WAIT_FOR_EVIDENCE`) was reachable via a synthetic, tampered
  `reflection_status` packet. That gate's underlying positive-state
  classification logic has been removed entirely from `classify_
  opportunity_state()` (not merely made unreachable) -- `decision/price_
  reflection.py`'s `validate_packet()` now unconditionally rejects any
  packet whose `reflection_status != "UNKNOWN"`, so the tamper pattern that
  test relied on can no longer even be used as an input to `ALPHA_REVIEW.
  build_packet()` at all. See `test_alpha_review.py`'s own `Closing
  FixReducedScopeTests` for the current, equivalent "this is genuinely
  unreachable, not just untested" regressions. `RealPilotFixturePinningTests`
  below is completely unaffected -- it never depended on gate 4 or any
  synthetic/tampered packet, only the real `pilot_evidence_intake.py`
  pipeline.
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

# Expected post-CIO-Gate-Hardening results for the 4 real Pilot subjects.
#
# 298040.KS history: P8-10 round 1 (PR #212 initial) wired real KRX price
# history + a real chain-linked KOSPI benchmark and this subject briefly
# reached WAIT_FOR_EVIDENCE (price_reflection.status=PARTIALLY_REFLECTED).
# CIO review round 2 on the same PR found that classification a real defect
# -- momentum alone (no event/expectation reference point) was standing in
# for a reflection judgment. decision/price_reflection.py now splits
# price_state (pure momentum -- still real, still computed) from
# reflection_status (structurally always "UNKNOWN" -- see that module's own
# docstring for the CIO final integration ruling and closing-fix ruling),
# and none of the 4 real Pilots' price_reflection inputs currently carry a
# reference point regardless (see decision/pilot_evidence_intake.py's
# price_reflection builders) -- so reflection_status is honestly UNKNOWN
# for all four, and 298040.KS is back to gate 3 (WAIT_FOR_PRICE), same as
# TSM. TSM/267260.KS/034020.KS are unaffected by any of this (267260.KS's
# REJECTED and 034020.KS's BLOCKED are both reached via gates that run
# strictly before the price/reflection gate, independent of it).
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


if __name__ == "__main__":
    unittest.main()
