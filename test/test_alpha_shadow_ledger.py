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



# States 5-10 of alpha_review's decision table are only reachable once
# price_reflection.threshold_basis=="RATIFIED" (CIO round 3, required item
# 4) -- decision/price_reflection.py's own contract hardcodes PROVISIONAL,
# so these fixtures must be built inside AR_FIXTURE.ratified_thresholds(),
# which simulates a real ratification landing (see that context manager's
# own docstring in test_alpha_review.py). Deferred as lambdas so only the
# ONE case actually requested per review() call is ever built, under the
# correct ambient contract state.
_PLAIN_CASE_BUILDERS = {
    "blocked_no_evidence": lambda: (AR_FIXTURE.ft_no_evidence(), AR_FIXTURE.eg_positive_proxy(), AR_FIXTURE.pr_partially_reflected()),
    "blocked_triple_unknown": lambda: (AR_FIXTURE.ft_status("UNKNOWN"), AR_FIXTURE.eg_unknown(), AR_FIXTURE.pr_unknown()),
    "rejected_disappointed": lambda: (AR_FIXTURE.ft_status("CONVERSION_DISAPPOINTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_partially_reflected()),
    "rejected_negative_gap_unknown_earnings": lambda: (AR_FIXTURE.ft_status("UNKNOWN"), AR_FIXTURE.eg_negative_proxy(), AR_FIXTURE.pr_overextended()),
    "wait_for_thesis_repair": lambda: (AR_FIXTURE.ft_status("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_negative_proxy(), AR_FIXTURE.pr_overextended()),
    "wait_for_price": lambda: (AR_FIXTURE.ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_unknown()),
}
_RATIFIED_CASE_BUILDERS = {
    "anticipatory_review": lambda rc: (AR_FIXTURE.ft_status_with_exhibit("PRE_REVENUE_SIGNAL"), AR_FIXTURE.eg_positive_proxy(), AR_FIXTURE.pr_partially_reflected(contract=rc)),
    "expectation_exhausted": lambda rc: (AR_FIXTURE.ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_positive_proxy(), AR_FIXTURE.pr_fully_reflected(contract=rc)),
    "wait_for_pullback": lambda rc: (AR_FIXTURE.ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_overextended(contract=rc)),
    "confirmation_review": lambda rc: (AR_FIXTURE.ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), AR_FIXTURE.eg_neutral_consensus(), AR_FIXTURE.pr_partially_reflected(contract=rc)),
    "wait_for_evidence": lambda rc: (AR_FIXTURE.ft_status("PRE_REVENUE_SIGNAL"), AR_FIXTURE.eg_unknown(), AR_FIXTURE.pr_partially_reflected(contract=rc)),
    "early_discovery": lambda rc: (AR_FIXTURE.ft_status_with_exhibit("PRE_REVENUE_SIGNAL"), AR_FIXTURE.eg_unknown(), AR_FIXTURE.pr_under_reflected(contract=rc)),
}


def review(opportunity_state_case="anticipatory_review", p5_rule_status=None):
    kwargs = {} if p5_rule_status is None else {"p5_rule_status": p5_rule_status}
    if opportunity_state_case in _RATIFIED_CASE_BUILDERS:
        with AR_FIXTURE.ratified_thresholds() as rc:
            ft, eg, pr = _RATIFIED_CASE_BUILDERS[opportunity_state_case](rc)
            return AR_FIXTURE.build(ft, eg, pr, **kwargs)
    ft, eg, pr = _PLAIN_CASE_BUILDERS[opportunity_state_case]()
    return AR_FIXTURE.build(ft, eg, pr, **kwargs)


# Base (pre-p5-gate) opportunity_state -> action mapping, one representative
# fixture case per opportunity_state. p5_rule_status is left at its default
# (NOT_EVALUATED) here -- see P5GatedActionTests below for the dedicated
# PASS-vs-not-PASS regression on the three entry-eligible states.
CASE_BY_STATE = {
    "BLOCKED": "blocked_no_evidence",
    "REJECTED": "rejected_disappointed",
    "WAIT_FOR_THESIS_REPAIR": "wait_for_thesis_repair",
    "WAIT_FOR_PRICE": "wait_for_price",
    "ANTICIPATORY_REVIEW": "anticipatory_review",
    "EXPECTATION_EXHAUSTED": "expectation_exhausted",
    "WAIT_FOR_PULLBACK": "wait_for_pullback",
    "CONFIRMATION_REVIEW": "confirmation_review",
    "WAIT_FOR_EVIDENCE": "wait_for_evidence",
    "EARLY_DISCOVERY": "early_discovery",
}

# The three opportunity_state values whose BASE action is SHADOW_ENTRY_REVIEW
# -- these are the only ones the p5_rule_status gate can ever affect.
ENTRY_ELIGIBLE_STATES = ("ANTICIPATORY_REVIEW", "EARLY_DISCOVERY", "CONFIRMATION_REVIEW")


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
        # This proves the BASE table (config/alpha_shadow_ledger_contract.
        # json's opportunity_state_to_action) exhaustively covers all 10
        # opportunity_state values, one representative fixture each --
        # p5_rule_status is forced to PASS here so the base table's own
        # SHADOW_ENTRY_REVIEW entries are actually observable (see
        # P5GatedActionTests below for the p5-gating regression itself).
        self.assertEqual(set(CASE_BY_STATE), set(CONTRACT["opportunity_state_to_action"]))
        for state, case in CASE_BY_STATE.items():
            with self.subTest(state=state):
                packet = review(case, p5_rule_status="PASS")
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
        # p5_rule_status="PASS" so the record's real action is
        # SHADOW_ENTRY_REVIEW -- tampering it down to "WAIT" must then be a
        # genuine, hash-detectable change (with the default NOT_EVALUATED
        # p5_rule_status, the record's action would already BE "WAIT", so
        # this tamper would be a no-op and prove nothing).
        record = MODULE.build_record(review("anticipatory_review", p5_rule_status="PASS"), "2026-08-20T10:00:00Z", 1)
        self.assertEqual(record["shadow_proposal"]["action"], "SHADOW_ENTRY_REVIEW")
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
    """CIO Gate Hardening's single most important new regression: the same
    entry-eligible opportunity_state produces action=WAIT whenever
    p5_rule_status != "PASS", and action=SHADOW_ENTRY_REVIEW only when
    p5_rule_status == "PASS". This is what currently prevents ALL real
    Pilot subjects from ever reaching SHADOW_ENTRY_REVIEW -- p5_rule_status
    is NOT_EVALUATED for every one of them (no ratified P5 packet exists),
    regardless of any future evidence improvement -- until a real ratified
    P5 packet exists for that subject.
    """

    def test_same_opportunity_state_downgrades_to_wait_unless_p5_status_is_pass(self):
        for state, case in CASE_BY_STATE.items():
            if state not in ENTRY_ELIGIBLE_STATES:
                continue
            with self.subTest(state=state):
                for not_pass in ("NOT_EVALUATED", "UNKNOWN", "UNDEFINED", "FAIL"):
                    packet = review(case, p5_rule_status=not_pass)
                    self.assertEqual(packet["opportunity_state"], state)
                    record = MODULE.build_record(packet, "2026-08-20T10:00:00Z", 1)
                    self.assertEqual(
                        record["shadow_proposal"]["action"], "WAIT",
                        f"{state} with p5_rule_status={not_pass} must downgrade to WAIT, "
                        f"never silently drop the review and never raise.",
                    )

                packet_pass = review(case, p5_rule_status="PASS")
                self.assertEqual(packet_pass["opportunity_state"], state)
                record_pass = MODULE.build_record(packet_pass, "2026-08-20T10:00:00Z", 1)
                self.assertEqual(record_pass["shadow_proposal"]["action"], "SHADOW_ENTRY_REVIEW")

    def test_default_not_evaluated_p5_status_never_yields_shadow_entry_review(self):
        # No p5_rule_status supplied at all (AR_FIXTURE.build()'s own
        # default) -- alpha_review.py's own default is NOT_EVALUATED. Real
        # Pilot evidence intake explicitly passes p5_rule_status=
        # "NOT_EVALUATED" for all four subjects too (see
        # decision/pilot_evidence_intake.py:run_all_pilots()).
        for state, case in CASE_BY_STATE.items():
            if state not in ENTRY_ELIGIBLE_STATES:
                continue
            with self.subTest(state=state):
                packet = review(case)
                self.assertEqual(packet["p5_rule_status"], "NOT_EVALUATED")
                record = MODULE.build_record(packet, "2026-08-20T10:00:00Z", 1)
                self.assertEqual(record["shadow_proposal"]["action"], "WAIT")

    def test_non_entry_eligible_states_are_p5_independent(self):
        # p5_rule_status must never change the action for a state whose
        # BASE action already isn't SHADOW_ENTRY_REVIEW (REJECT/WAIT stay
        # REJECT/WAIT no matter what p5_rule_status says).
        for state, case in CASE_BY_STATE.items():
            if state in ENTRY_ELIGIBLE_STATES:
                continue
            with self.subTest(state=state):
                base_action = CONTRACT["opportunity_state_to_action"][state]
                for p5_status in ("PASS", "FAIL", "UNKNOWN", "UNDEFINED", "NOT_EVALUATED"):
                    packet = review(case, p5_rule_status=p5_status)
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
        # `decision/alpha_review.py`'s CIO round-4 `WAIT_FOR_RULE_RATIFICATION`
        # state (alpha_review/5, required item 6) is deliberately NOT added to
        # `shadow/alpha_shadow_ledger.py` -- that module is a real production
        # file this PR is explicitly forbidden from touching. This asserts
        # the CURRENT, correct, fail-closed consequence of that boundary: a
        # WAIT_FOR_RULE_RATIFICATION packet is not silently mapped to WAIT
        # (or anything else) -- it raises a loud, unambiguous
        # OPPORTUNITY_STATE_UNMAPPED error, exactly like any other genuinely
        # unmapped opportunity_state would. Wiring WAIT_FOR_RULE_RATIFICATION
        # into the shadow ledger's own action table is real, tracked
        # follow-up work for a future PR that is explicitly permitted to
        # touch shadow/alpha_shadow_ledger.py -- not silently absorbed here.
        self.assertNotIn("WAIT_FOR_RULE_RATIFICATION", CONTRACT["opportunity_state_to_action"])
        with self.assertRaisesRegex(
            MODULE.AlphaShadowLedgerError, "OPPORTUNITY_STATE_UNMAPPED:WAIT_FOR_RULE_RATIFICATION"
        ):
            MODULE.action_for_opportunity_state("WAIT_FOR_RULE_RATIFICATION", "NOT_EVALUATED", CONTRACT)


if __name__ == "__main__":
    unittest.main()
