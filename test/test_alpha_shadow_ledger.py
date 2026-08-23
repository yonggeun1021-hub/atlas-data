#!/usr/bin/env python3
"""P10-07 zero-capital Alpha Shadow Ledger regression.

★ CIO closing-fix ruling on PR #212 (2026-08-23): `decision/alpha_review.py`
  now independently, unconditionally rejects any packet claiming an
  opportunity_state whose reflection_status isn't genuinely `"UNKNOWN"` --
  `shadow/alpha_shadow_ledger.py`'s own `build_record()` calls `ALPHA_
  REVIEW.validate_packet()` on its input, so it can no longer be exercised
  with any of the 6 states this closes off (`ANTICIPATORY_REVIEW`/
  `EXPECTATION_EXHAUSTED`/`WAIT_FOR_PULLBACK`/`CONFIRMATION_REVIEW`/
  `WAIT_FOR_EVIDENCE`/`EARLY_DISCOVERY`) through any REAL, validated
  packet -- not because `shadow/alpha_shadow_ledger.py` (a real production
  file this PR is explicitly forbidden from touching) or its own contract
  changed, but because the upstream packet those states would require can
  no longer legitimately exist.

  `shadow/alpha_shadow_ledger.py`'s own `opportunity_state_to_action`
  mapping table (`config/alpha_shadow_ledger_contract.json`, untouched)
  still names all 10 states -- this file now tests that table's
  COMPLETENESS via `action_for_opportunity_state()` directly (a pure
  lookup+gate function needing no validated packet at all), and tests the
  full, real, end-to-end `build_record()` pipeline only for the 4
  opportunity_states that remain reachable through a real, validated Alpha
  Review packet: `BLOCKED`/`REJECTED`/`WAIT_FOR_THESIS_REPAIR`/
  `WAIT_FOR_PRICE`.
"""
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


# Only 4 of alpha_review's 10 opportunity_state vocabulary members remain
# reachable through a real, validated packet in this reduced scope -- see
# module docstring and decision/alpha_review.py's own docstring.
_REACHABLE_CASE_BUILDERS = {
    "BLOCKED": lambda: (AR_FIXTURE.ft_no_evidence(), AR_FIXTURE.eg_positive_proxy(), AR_FIXTURE.pr_known_price()),
    "REJECTED": lambda: (AR_FIXTURE.ft_status("CONVERSION_DISAPPOINTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_known_price()),
    "WAIT_FOR_THESIS_REPAIR": lambda: (
        AR_FIXTURE.ft_status("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_negative_proxy(),
        AR_FIXTURE.pr_overextended_no_reference_point(),
    ),
    "WAIT_FOR_PRICE": lambda: (
        AR_FIXTURE.ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_neutral_consensus(),
        AR_FIXTURE.pr_unknown(),
    ),
}

# All 10 vocabulary members the config-level opportunity_state_to_action
# table (shadow/alpha_shadow_ledger.py, untouched) still names -- used only
# for direct, packet-free action_for_opportunity_state() lookups below.
ALL_TABLE_STATES = tuple(CONTRACT["opportunity_state_to_action"])

# The three opportunity_state values whose BASE action is SHADOW_ENTRY_REVIEW
# -- these are the only ones the p5_rule_status gate can ever affect. None
# of them remain reachable through a real, validated packet any more (see
# module docstring) -- P5GatedActionTests below exercises them via the pure
# action_for_opportunity_state() function directly instead.
ENTRY_ELIGIBLE_STATES = ("ANTICIPATORY_REVIEW", "EARLY_DISCOVERY", "CONFIRMATION_REVIEW")


def review(state="BLOCKED", p5_rule_status=None):
    kwargs = {} if p5_rule_status is None else {"p5_rule_status": p5_rule_status}
    ft, eg, pr = _REACHABLE_CASE_BUILDERS[state]()
    return AR_FIXTURE.build(ft, eg, pr, **kwargs)


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

    def test_action_mapping_table_is_complete_for_all_ten_opportunity_states(self):
        # config/alpha_shadow_ledger_contract.json (untouched, off-limits)
        # still names all 10 opportunity_state vocabulary members -- exactly
        # matching alpha_review's own (WAIT_FOR_RULE_RATIFICATION was never
        # in this table even before it was retired from alpha_review's own
        # vocabulary) -- proven directly via the pure action_for_
        # opportunity_state() lookup, which needs no validated packet at all
        # (most of these 10 states can no longer legitimately appear on one
        # -- see module docstring).
        self.assertEqual(set(ALL_TABLE_STATES), set(AR_FIXTURE.CONTRACT["opportunity_states"]))
        for state in ALL_TABLE_STATES:
            with self.subTest(state=state):
                action = MODULE.action_for_opportunity_state(state, "PASS", CONTRACT)
                self.assertEqual(action, CONTRACT["opportunity_state_to_action"][state])

    def test_reachable_states_produce_the_correct_action_end_to_end(self):
        # The 4 states that CAN still appear on a real, validated packet --
        # exercised through the full build_record() pipeline (p5_rule_status
        # forced to PASS so WAIT_FOR_PRICE's own base action, not a p5-gated
        # one, is what's actually being checked -- none of these 4 are
        # entry-eligible, so PASS has no effect, see ENTRY_ELIGIBLE_STATES).
        for state in _REACHABLE_CASE_BUILDERS:
            with self.subTest(state=state):
                packet = review(state, p5_rule_status="PASS")
                self.assertEqual(packet["opportunity_state"], state)
                record = MODULE.build_record(packet, "2026-08-20T10:00:00Z", 1)
                expected_action = CONTRACT["opportunity_state_to_action"][state]
                self.assertEqual(record["shadow_proposal"]["action"], expected_action)

    def test_capital_is_always_zero_and_cannot_be_overridden(self):
        # No parameter in build_record()'s live signature can ever set capital.
        params = set(inspect.signature(MODULE.build_record).parameters)
        self.assertNotIn("capital", params)
        for state in _REACHABLE_CASE_BUILDERS:
            record = MODULE.build_record(review(state), "2026-08-20T10:00:00Z", 1)
            self.assertEqual(record["shadow_proposal"]["capital"], 0)
            self.assertNotIsInstance(record["shadow_proposal"]["capital"], bool)

    def test_human_approval_required_is_always_true_and_cannot_be_overridden(self):
        params = set(inspect.signature(MODULE.build_record).parameters)
        self.assertNotIn("human_approval_required", params)
        for state in _REACHABLE_CASE_BUILDERS:
            record = MODULE.build_record(review(state), "2026-08-20T10:00:00Z", 1)
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
        self.assertEqual(record["shadow_proposal"]["action"], "REJECT")
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


class P5GatedActionTests(unittest.TestCase):
    """CIO Gate Hardening's single most important regression: the same
    entry-eligible opportunity_state produces action=WAIT whenever
    p5_rule_status != "PASS", and action=SHADOW_ENTRY_REVIEW only when
    p5_rule_status == "PASS". None of the 3 entry-eligible states
    (ENTRY_ELIGIBLE_STATES) remain reachable through a real, validated
    packet in this reduced scope (see module docstring), so this is
    exercised via `action_for_opportunity_state()` directly -- a pure
    lookup+gate function that needs no packet at all, proving the GATE
    LOGIC itself (not just the mapping table) is still correct and ready
    for whenever a future Reflection Evidence Authority workstream makes
    these states reachable again. This is what currently prevents ALL real
    Pilot subjects from ever reaching SHADOW_ENTRY_REVIEW regardless: no
    real Pilot's opportunity_state is even entry-eligible any more, let
    alone p5-approved.
    """

    def test_same_opportunity_state_downgrades_to_wait_unless_p5_status_is_pass(self):
        for state in ENTRY_ELIGIBLE_STATES:
            with self.subTest(state=state):
                for not_pass in ("NOT_EVALUATED", "UNKNOWN", "UNDEFINED", "FAIL"):
                    self.assertEqual(
                        MODULE.action_for_opportunity_state(state, not_pass, CONTRACT), "WAIT",
                        f"{state} with p5_rule_status={not_pass} must downgrade to WAIT, "
                        f"never silently drop the review and never raise.",
                    )
                self.assertEqual(
                    MODULE.action_for_opportunity_state(state, "PASS", CONTRACT), "SHADOW_ENTRY_REVIEW"
                )

    def test_non_entry_eligible_states_are_p5_independent(self):
        # p5_rule_status must never change the action for a state whose
        # BASE action already isn't SHADOW_ENTRY_REVIEW (REJECT/WAIT stay
        # REJECT/WAIT no matter what p5_rule_status says).
        for state in ALL_TABLE_STATES:
            if state in ENTRY_ELIGIBLE_STATES:
                continue
            with self.subTest(state=state):
                base_action = CONTRACT["opportunity_state_to_action"][state]
                for p5_status in ("PASS", "FAIL", "UNKNOWN", "UNDEFINED", "NOT_EVALUATED"):
                    action = MODULE.action_for_opportunity_state(state, p5_status, CONTRACT)
                    self.assertEqual(action, base_action)

    def test_reachable_states_are_confirmed_p5_independent_end_to_end(self):
        # The integration-level confirmation for the 4 states that DO still
        # reach build_record() through a real, validated packet -- none of
        # them are entry-eligible, so p5_rule_status must have zero effect.
        for state in _REACHABLE_CASE_BUILDERS:
            with self.subTest(state=state):
                base_action = CONTRACT["opportunity_state_to_action"][state]
                for p5_status in ("PASS", "FAIL", "UNKNOWN", "UNDEFINED", "NOT_EVALUATED"):
                    packet = review(state, p5_rule_status=p5_status)
                    record = MODULE.build_record(packet, "2026-08-20T10:00:00Z", 1)
                    self.assertEqual(record["shadow_proposal"]["action"], base_action)

    def test_action_for_opportunity_state_function_directly(self):
        for not_pass in ("NOT_EVALUATED", "UNKNOWN", "UNDEFINED", "FAIL"):
            self.assertEqual(
                MODULE.action_for_opportunity_state("ANTICIPATORY_REVIEW", not_pass, CONTRACT), "WAIT"
            )
        self.assertEqual(
            MODULE.action_for_opportunity_state("ANTICIPATORY_REVIEW", "PASS", CONTRACT), "SHADOW_ENTRY_REVIEW"
        )
        # p5_rule_status has zero effect on a REJECT/WAIT-mapped state.
        for p5_status in ("PASS", "FAIL", "UNKNOWN", "UNDEFINED", "NOT_EVALUATED"):
            self.assertEqual(MODULE.action_for_opportunity_state("BLOCKED", p5_status, CONTRACT), "REJECT")
            self.assertEqual(MODULE.action_for_opportunity_state("WAIT_FOR_PRICE", p5_status, CONTRACT), "WAIT")

    def test_wait_for_rule_ratification_is_unmapped_and_fails_closed(self):
        # `decision/alpha_review.py`'s WAIT_FOR_RULE_RATIFICATION state has
        # been retired from the vocabulary entirely (CIO closing-fix ruling,
        # 2026-08-23) -- it was never added to shadow/alpha_shadow_ledger.py
        # (a real production file this PR is explicitly forbidden from
        # touching) even while it existed. This asserts the still-correct,
        # fail-closed consequence for any string not in the mapping table --
        # a loud, unambiguous OPPORTUNITY_STATE_UNMAPPED error, never a
        # silent WAIT/REJECT default.
        self.assertNotIn("WAIT_FOR_RULE_RATIFICATION", CONTRACT["opportunity_state_to_action"])
        self.assertNotIn("WAIT_FOR_RULE_RATIFICATION", AR_FIXTURE.CONTRACT["opportunity_states"])
        with self.assertRaisesRegex(
            MODULE.AlphaShadowLedgerError, "OPPORTUNITY_STATE_UNMAPPED:WAIT_FOR_RULE_RATIFICATION"
        ):
            MODULE.action_for_opportunity_state("WAIT_FOR_RULE_RATIFICATION", "NOT_EVALUATED", CONTRACT)


if __name__ == "__main__":
    unittest.main()
