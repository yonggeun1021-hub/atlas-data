#!/usr/bin/env python3
"""P8-11 Anticipatory Alpha Review regression.

Every opportunity_state fixture below is assembled from the REAL
`forward_thesis.build_packet` / `expectations_gap.build_packet` /
`price_reflection.build_packet` builders (reusing the existing test modules'
own fixture helpers) so this regression also catches integration breakage
against those three upstream modules, not just alpha_review.py in isolation.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "alpha_review.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("alpha_review", SOURCE)
CONTRACT = MODULE.load_contract()

# Reuse the existing upstream test modules' own fixture helpers rather than
# duplicating input scaffolding -- same pattern already used by
# test_investment_review_shadow_ledger.py (P8_FIXTURE = load(...)).
FT_FIXTURE = load_module("alpha_review_ft_fixture", ROOT / "test" / "test_forward_thesis.py")
EG_FIXTURE = load_module("alpha_review_eg_fixture", ROOT / "test" / "test_expectations_gap.py")
PR_FIXTURE = load_module("alpha_review_pr_fixture", ROOT / "test" / "test_price_reflection.py")

FT = FT_FIXTURE.MODULE
EG = EG_FIXTURE.MODULE
PR = PR_FIXTURE.MODULE

DECISION_DATE = "2026-08-20"
GENERATED_AT = "2026-08-20T09:00:00Z"


def forward_thesis_packet(**overrides):
    return FT.build_packet(FT_FIXTURE.minimal_input(**overrides), FT_FIXTURE.CONTRACT)


def expectations_gap_packet(**category_overrides):
    value = EG_FIXTURE.base_input(
        subject="TSM", decision_date=DECISION_DATE, generated_at=GENERATED_AT, **category_overrides
    )
    return EG.build_packet(value, EG_FIXTURE.CONTRACT)


def price_reflection_packet(**overrides):
    kwargs = PR_FIXTURE.base_kwargs(
        subject="TSM", decision_date=DECISION_DATE, generated_at=GENERATED_AT, contract=PR_FIXTURE.CONTRACT,
    )
    kwargs.update(overrides)
    return PR.build_packet(**kwargs)


def build(ft_packet, eg_packet, pr_packet, **kwargs):
    return MODULE.build_packet(
        forward_thesis_packet=ft_packet,
        expectations_gap_packet=eg_packet,
        price_reflection_packet=pr_packet,
        generated_at=GENERATED_AT,
        contract=CONTRACT,
        **kwargs,
    )


# ── price_reflection status presets (all decision_date=2026-08-20) ─────────
# `price_reflection/2` (CIO round 2): `reflection_status` only ever leaves
# UNKNOWN when a real reference point is supplied -- every preset below that
# needs a confident (non-UNKNOWN) reflection_status carries a real
# `event_reaction` with a POSITIVE direction the momentum can be compared
# against. Momentum magnitude alone (no reference) would otherwise leave
# EVERY one of these at reflection_status=UNKNOWN regardless of how extreme
# the price move is -- see decision/price_reflection.py's module docstring.
_REFERENCE = {"event_date": "2026-08-10", "direction": "POSITIVE", "reaction_magnitude_pct": "5"}


def pr_under_reflected():
    return price_reflection_packet(
        price_as_of="2026-08-19T20:00:00Z",
        recent_return_windows={"1m": "1"},
        relative_strength={"vs_market": "1"},
        event_reaction=_REFERENCE,
        data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
    )


def pr_partially_reflected():
    return price_reflection_packet(
        price_as_of="2026-08-19T20:00:00Z",
        recent_return_windows={"1m": "3"},
        relative_strength={"vs_market": "2"},
        event_reaction=_REFERENCE,
        data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
    )


def pr_fully_reflected():
    return price_reflection_packet(
        price_as_of="2026-08-19T20:00:00Z",
        recent_return_windows={"1m": "10"},
        relative_strength={"vs_market": "9"},
        event_reaction=_REFERENCE,
        data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
    )


def pr_overextended():
    return price_reflection_packet(
        price_as_of="2026-08-19T20:00:00Z",
        recent_return_windows={"1m": "20"},
        relative_strength={"vs_market": "18", "position_vs_recent_high_pct": "1"},
        event_reaction=_REFERENCE,
        data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
    )


def pr_overextended_no_reference_point():
    """Real price_state=OVEREXTENDED with NO reference point at all --
    reflection_status must stay UNKNOWN (round-2 core fix: a rally alone is
    never a reflection verdict)."""
    return price_reflection_packet(
        price_as_of="2026-08-19T20:00:00Z",
        recent_return_windows={"1m": "20"},
        relative_strength={"vs_market": "18", "position_vs_recent_high_pct": "1"},
        data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
    )


def pr_unknown():
    return price_reflection_packet()  # no price_as_of supplied at all


# ── expectations_gap status presets (all decision_date=2026-08-20) ─────────
def eg_positive_proxy():
    return expectations_gap_packet(
        guidance_changes=EG_FIXTURE.category("POSITIVE"),
        backlog_or_new_orders=EG_FIXTURE.category("POSITIVE"),
    )


def eg_negative_proxy():
    return expectations_gap_packet(
        guidance_changes=EG_FIXTURE.category("NEGATIVE"),
        backlog_or_new_orders=EG_FIXTURE.category("NEGATIVE"),
    )


def eg_neutral_consensus():
    return expectations_gap_packet(public_estimates=EG_FIXTURE.category("NEUTRAL"))


def eg_unknown():
    return expectations_gap_packet()


# ── forward_thesis presets ──────────────────────────────────────────────
def ft_status(status):
    return forward_thesis_packet(earnings_conversion=FT_FIXTURE.earnings_conversion(status=status))


def ft_status_with_exhibit(status):
    """Same as ft_status(), but with at least one EXHIBIT_EXTRACTED observed
    fact instead of the default single NARRATIVE_SOURCED one.

    Post CIO-Gate-Hardening, gate 4 (narrative-only-core-evidence) forces
    WAIT_FOR_EVIDENCE whenever observed_facts is non-empty but none of it is
    EXHIBIT_EXTRACTED -- BEFORE any positive-state logic ever runs. Every
    positive-state fixture (ANTICIPATORY_REVIEW, EXPECTATION_EXHAUSTED,
    WAIT_FOR_PULLBACK, CONFIRMATION_REVIEW, EARLY_DISCOVERY) therefore needs
    a real EXHIBIT_EXTRACTED fact to actually reach that state, not just the
    default plain ft_status() fixture.
    """
    return forward_thesis_packet(
        earnings_conversion=FT_FIXTURE.earnings_conversion(status=status),
        observed_facts=[FT_FIXTURE.observed_fact(source_class="EXHIBIT_EXTRACTED")],
        evidence_lineage=[FT_FIXTURE.evidence_entry(source_type="SEC_EXHIBIT")],
    )


def ft_no_evidence():
    return forward_thesis_packet(observed_facts=[], evidence_lineage=[])


class OpportunityStateClassificationTests(unittest.TestCase):
    """One test per opportunity_state, built from real upstream packets."""

    def test_blocked_no_real_evidence(self):
        packet = build(ft_no_evidence(), eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["opportunity_state"], "BLOCKED")

    def test_blocked_triple_unknown(self):
        packet = build(ft_status("UNKNOWN"), eg_unknown(), pr_unknown())
        self.assertEqual(packet["opportunity_state"], "BLOCKED")

    def test_rejected_conversion_disappointed(self):
        packet = build(ft_status("CONVERSION_DISAPPOINTED"), eg_neutral_consensus(), pr_partially_reflected())
        self.assertEqual(packet["opportunity_state"], "REJECTED")

    def test_rejected_negative_gap_with_unknown_earnings_conversion(self):
        # CIO Gate Hardening gate 2a: a NEGATIVE gap with NO live
        # earnings-conversion hypothesis (UNKNOWN) has nothing left to hold
        # onto -- REJECTED, not WAIT_FOR_THESIS_REPAIR. Mirrors the real
        # 267260.KS (HD Hyundai Electric) Pilot fact pattern.
        packet = build(ft_status("UNKNOWN"), eg_negative_proxy(), pr_overextended())
        self.assertEqual(packet["opportunity_state"], "REJECTED")

    def test_wait_for_thesis_repair_negative_gap_with_live_earnings_conversion(self):
        # CIO Gate Hardening gate 2b (NEW): supersedes the pre-hardening
        # test_rejected_overextended_and_negative_gap fixture -- a NEGATIVE
        # gap with a REAL earnings-conversion hypothesis still standing
        # (REVENUE_CONVERSION_EXPECTED, not UNKNOWN) is now
        # WAIT_FOR_THESIS_REPAIR, not REJECTED. OVEREXTENDED+NEGATIVE is a
        # strict subset of this broader NEGATIVE-gap rule -- price status is
        # irrelevant to this gate.
        packet = build(ft_status("REVENUE_CONVERSION_EXPECTED"), eg_negative_proxy(), pr_overextended())
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_THESIS_REPAIR")

    def test_wait_for_price_blocks_every_positive_state_when_price_is_unknown(self):
        # CIO Gate Hardening gate 3 (NEW, blanket rule): price UNKNOWN wins
        # over an otherwise-strong thesis/gap. This is the exact real-world
        # bug the CIO review found -- TSM reached CONFIRMATION_REVIEW with
        # price_reflection.status==UNKNOWN pre-hardening.
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_unknown())
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_PRICE")

    def test_wait_for_evidence_narrative_only_core_evidence_gate(self):
        # CIO Gate Hardening gate 4 (NEW): price known, gap not negative, but
        # every observed_fact is NARRATIVE_SOURCED/PRICE_FEED only -- no
        # EXHIBIT_EXTRACTED fact backs the thesis. Mirrors the real
        # 298040.KS (Hyosung) Pilot fact pattern's evidence shape (though
        # 298040.KS itself is intercepted by gate 3 first, since its
        # price_reflection.status is also UNKNOWN).
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_EVIDENCE")
        self.assertTrue(packet["price_reflection"]["reflection_status"] != "UNKNOWN")
        self.assertEqual(packet["expectations_gap"]["status"], "POSITIVE")

    def test_anticipatory_review(self):
        packet = build(ft_status_with_exhibit("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["opportunity_state"], "ANTICIPATORY_REVIEW")

    def test_expectation_exhausted(self):
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_positive_proxy(), pr_fully_reflected())
        self.assertEqual(packet["opportunity_state"], "EXPECTATION_EXHAUSTED")

    def test_wait_for_pullback(self):
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_overextended())
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_PULLBACK")

    def test_overextended_price_with_no_reference_point_is_wait_for_price_not_wait_for_pullback(self):
        # CIO review round 2 core fix, exercised end-to-end: a real rally
        # (price_state=OVEREXTENDED) with NO event/expectation reference
        # point must NOT be treated as a reflection verdict -- it must still
        # hit gate 3 (WAIT_FOR_PRICE), never reach gate 5's WAIT_FOR_PULLBACK.
        pr = pr_overextended_no_reference_point()
        self.assertEqual(pr["price_reflection"]["price_state"], "OVEREXTENDED")
        self.assertEqual(pr["price_reflection"]["reflection_status"], "UNKNOWN")
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr)
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_PRICE")

    def test_confirmation_review(self):
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_partially_reflected())
        self.assertEqual(packet["opportunity_state"], "CONFIRMATION_REVIEW")

    def test_wait_for_evidence(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_unknown(), pr_partially_reflected())
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_EVIDENCE")

    def test_early_discovery(self):
        packet = build(ft_status_with_exhibit("PRE_REVENUE_SIGNAL"), eg_unknown(), pr_under_reflected())
        self.assertEqual(packet["opportunity_state"], "EARLY_DISCOVERY")

    def test_all_ten_opportunity_states_are_exercised(self):
        # Belt-and-suspenders: the contract's closed vocabulary must exactly
        # equal the 10 states asserted above (8 pre-hardening + the 2 CIO
        # Gate Hardening additions, WAIT_FOR_PRICE / WAIT_FOR_THESIS_REPAIR).
        self.assertEqual(sorted(CONTRACT["opportunity_states"]), sorted([
            "EARLY_DISCOVERY", "ANTICIPATORY_REVIEW", "WAIT_FOR_PULLBACK",
            "WAIT_FOR_EVIDENCE", "CONFIRMATION_REVIEW", "EXPECTATION_EXHAUSTED",
            "REJECTED", "BLOCKED", "WAIT_FOR_PRICE", "WAIT_FOR_THESIS_REPAIR",
        ]))


class AnticipatoryReviewGateTests(unittest.TestCase):
    """The 7 ANTICIPATORY_REVIEW gates, each individually violated.

    Gates 2 (revenue_recipient/atlas_linked_ticker present), 6's
    invalidation-conditions clause, and 7 (no future-dated evidence) are
    structurally guaranteed by forward_thesis.py's OWN contract for any
    validly-BUILT packet -- there is no way to construct a real, validated
    forward_thesis packet that violates them. Those three tests mutate a
    plain dict copy of an already-valid packet directly (bypassing
    forward_thesis re-validation) purely to exercise the gate function in
    isolation; the other four gates are violated via real, fully valid
    upstream packets.
    """

    def setUp(self):
        # ft_status_with_exhibit (not plain ft_status) so gate 4
        # (narrative-only-core-evidence) never pre-empts these tests -- see
        # that fixture's docstring.
        self.ft = ft_status_with_exhibit("PRE_REVENUE_SIGNAL")
        self.eg = eg_positive_proxy()
        self.pr = pr_partially_reflected()
        self.gap = self.eg["expectations_gap"]
        self.pr_inner = self.pr["price_reflection"]
        self.decision_date = dt.date.fromisoformat(DECISION_DATE)

    def test_baseline_all_seven_gates_hold_and_state_is_anticipatory_review(self):
        gates = MODULE.anticipatory_review_gates(self.ft, self.gap, self.pr_inner, self.decision_date)
        self.assertTrue(all(gates.values()), gates)
        self.assertEqual(len(gates), 7)
        state = MODULE.classify_opportunity_state(self.ft, self.gap, self.pr_inner, self.decision_date)
        self.assertEqual(state, "ANTICIPATORY_REVIEW")

    def _assert_exactly_one_gate_false(self, gates: dict, expected_false_key: str):
        for key, value in gates.items():
            if key == expected_false_key:
                self.assertFalse(value, key)
            else:
                self.assertTrue(value, key)

    def test_gate1_violated_zero_observed_facts(self):
        ft = FT.build_packet(FT_FIXTURE.minimal_input(
            earnings_conversion=FT_FIXTURE.earnings_conversion(status="PRE_REVENUE_SIGNAL"),
            observed_facts=[],
        ), FT_FIXTURE.CONTRACT)
        gates = MODULE.anticipatory_review_gates(ft, self.gap, self.pr_inner, self.decision_date)
        self._assert_exactly_one_gate_false(gates, "gate1_catalyst_and_observed_evidence")
        self.assertNotEqual(
            MODULE.classify_opportunity_state(ft, self.gap, self.pr_inner, self.decision_date),
            "ANTICIPATORY_REVIEW",
        )

    def test_gate2_violated_empty_revenue_recipient(self):
        ft = copy.deepcopy(self.ft)
        ft["revenue_recipient"] = ""
        gates = MODULE.anticipatory_review_gates(ft, self.gap, self.pr_inner, self.decision_date)
        self._assert_exactly_one_gate_false(gates, "gate2_recipient_and_ticker_present")
        self.assertNotEqual(
            MODULE.classify_opportunity_state(ft, self.gap, self.pr_inner, self.decision_date),
            "ANTICIPATORY_REVIEW",
        )

    def test_gate3_violated_earnings_conversion_unknown(self):
        ft = FT.build_packet(FT_FIXTURE.minimal_input(
            earnings_conversion=FT_FIXTURE.earnings_conversion(status="UNKNOWN"),
        ), FT_FIXTURE.CONTRACT)
        gates = MODULE.anticipatory_review_gates(ft, self.gap, self.pr_inner, self.decision_date)
        self._assert_exactly_one_gate_false(gates, "gate3_conversion_hypothesis_exists")
        self.assertNotEqual(
            MODULE.classify_opportunity_state(ft, self.gap, self.pr_inner, self.decision_date),
            "ANTICIPATORY_REVIEW",
        )

    def test_gate4_violated_negative_expectations_gap(self):
        eg = eg_negative_proxy()
        gap = eg["expectations_gap"]
        gates = MODULE.anticipatory_review_gates(self.ft, gap, self.pr_inner, self.decision_date)
        self._assert_exactly_one_gate_false(gates, "gate4_expectations_gap_supportive")
        self.assertNotEqual(
            MODULE.classify_opportunity_state(self.ft, gap, self.pr_inner, self.decision_date),
            "ANTICIPATORY_REVIEW",
        )

    def test_gate5_violated_price_fully_reflected(self):
        pr = pr_fully_reflected()
        pr_inner = pr["price_reflection"]
        gates = MODULE.anticipatory_review_gates(self.ft, self.gap, pr_inner, self.decision_date)
        self._assert_exactly_one_gate_false(gates, "gate5_price_not_stretched_or_unknown")
        self.assertNotEqual(
            MODULE.classify_opportunity_state(self.ft, self.gap, pr_inner, self.decision_date),
            "ANTICIPATORY_REVIEW",
        )

    def test_gate6_violated_empty_invalidation_conditions(self):
        ft = copy.deepcopy(self.ft)
        ft["invalidation_conditions"] = []
        gates = MODULE.anticipatory_review_gates(ft, self.gap, self.pr_inner, self.decision_date)
        self._assert_exactly_one_gate_false(gates, "gate6_catalysts_and_invalidation_nonempty")
        self.assertNotEqual(
            MODULE.classify_opportunity_state(ft, self.gap, self.pr_inner, self.decision_date),
            "ANTICIPATORY_REVIEW",
        )

    def test_gate7_violated_future_dated_observed_fact(self):
        ft = copy.deepcopy(self.ft)
        future_fact = dict(ft["observed_facts"][0])
        future_fact["as_of"] = "2026-08-25"
        ft["observed_facts"] = [future_fact]
        gates = MODULE.anticipatory_review_gates(ft, self.gap, self.pr_inner, self.decision_date)
        self._assert_exactly_one_gate_false(gates, "gate7_no_future_dated_evidence")
        self.assertNotEqual(
            MODULE.classify_opportunity_state(ft, self.gap, self.pr_inner, self.decision_date),
            "ANTICIPATORY_REVIEW",
        )


class InputCompositionTests(unittest.TestCase):
    def test_missing_sub_packets_are_rejected(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        pr = pr_partially_reflected()
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "FORWARD_THESIS_PACKET_MISSING"):
            build(None, eg, pr)
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "EXPECTATIONS_GAP_PACKET_MISSING"):
            build(ft, None, pr)
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "PRICE_REFLECTION_PACKET_MISSING"):
            build(ft, eg, None)

    def test_invalid_sub_packet_fails_its_own_module_validation(self):
        # ★ alpha_review.py loads forward_thesis.py under its own module name
        #   (no package structure in this repo -- see module docstring), so
        #   the raised exception is a distinct class object from FT's own
        #   ForwardThesisError even though both subclass ValueError with the
        #   same message. Assert on ValueError + message, not exact type.
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        pr = pr_partially_reflected()
        broken_ft = copy.deepcopy(ft)
        broken_ft["invalidation_conditions"] = []
        with self.assertRaises(ValueError) as cm:
            build(broken_ft, eg, pr)
        self.assertIn("INVALIDATION_CONDITIONS_EMPTY", str(cm.exception))

    def test_subject_mismatch_across_input_packets_is_rejected(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = EG.build_packet(
            EG_FIXTURE.base_input(subject="AAPL", decision_date=DECISION_DATE, generated_at=GENERATED_AT),
            EG_FIXTURE.CONTRACT,
        )
        pr = pr_partially_reflected()
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "SUBJECT_MISMATCH_ACROSS_INPUT_PACKETS"):
            build(ft, eg, pr)

    def test_decision_date_mismatch_across_input_packets_is_rejected(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        pr = PR.build_packet(
            subject="TSM", decision_date="2026-08-21", generated_at=GENERATED_AT, contract=PR_FIXTURE.CONTRACT,
        )
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "DECISION_DATE_MISMATCH_ACROSS_INPUT_PACKETS"):
            build(ft, eg, pr)


class PassThroughFieldTests(unittest.TestCase):
    def test_p5_rule_status_and_portfolio_status_default_to_not_evaluated(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["p5_rule_status"], "NOT_EVALUATED")
        self.assertEqual(packet["portfolio_status"], "NOT_EVALUATED")

    def test_p5_rule_status_and_portfolio_status_are_caller_pass_through(self):
        packet = build(
            ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected(),
            p5_rule_status="FAIL", portfolio_status="PASS",
        )
        self.assertEqual(packet["p5_rule_status"], "FAIL")
        self.assertEqual(packet["portfolio_status"], "PASS")

    def test_invalid_p5_rule_status_and_portfolio_status_are_rejected(self):
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "P5_RULE_STATUS_INVALID"):
            build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected(), p5_rule_status="MOONSHOT")
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "PORTFOLIO_STATUS_INVALID"):
            build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected(), portfolio_status="MOONSHOT")

    def test_thesis_status_and_earnings_conversion_status_are_verbatim_and_equal(self):
        packet = build(ft_status("BACKLOG_BUILDING"), eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["thesis_status"], "BACKLOG_BUILDING")
        self.assertEqual(packet["earnings_conversion_status"], "BACKLOG_BUILDING")

    def test_expectations_gap_and_price_reflection_embedded_verbatim(self):
        eg = eg_positive_proxy()
        pr = pr_partially_reflected()
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg, pr)
        self.assertEqual(packet["expectations_gap"], eg["expectations_gap"])
        self.assertEqual(packet["price_reflection"], pr["price_reflection"])

    def test_why_now_cites_specific_fields(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        packet = build(ft, eg, pr_partially_reflected())
        why_now = packet["why_now"]
        self.assertTrue(any(ft["catalysts"][0]["description"] in line for line in why_now))
        self.assertTrue(any(ft["forward_inferences"][0]["statement"] in line for line in why_now))
        self.assertTrue(any(eg["expectations_gap"]["gap_reasons"][0] in line for line in why_now))

    def test_what_market_may_be_missing_excludes_catalysts(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        packet = build(ft, eg, pr_partially_reflected())
        for line in packet["what_market_may_be_missing"]:
            self.assertNotIn("forward_thesis.catalysts:", line)

    def test_invalidation_conditions_always_non_empty(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected())
        self.assertTrue(len(packet["invalidation_conditions"]) >= 2)


class NextReviewDateTests(unittest.TestCase):
    def test_uses_earliest_review_date_at_or_after_decision_date(self):
        ft = forward_thesis_packet(review_dates=["2026-09-01", "2026-08-25"])
        packet = build(ft, eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["next_review_date"], "2026-08-25")

    def test_falls_back_to_default_cadence_when_all_review_dates_are_stale(self):
        ft = forward_thesis_packet(review_dates=["2026-08-01"])
        packet = build(ft, eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["next_review_date"], "2026-09-19")
        self.assertGreaterEqual(
            dt.date.fromisoformat(packet["next_review_date"]), dt.date.fromisoformat(packet["decision_date"])
        )


class TradeProposalAndAuthorityTests(unittest.TestCase):
    def test_authority_dict_matches_exact_specified_values(self):
        expected = {
            "alpha_review_assembly_only": True,
            "opportunity_state_classification_only": True,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "rule_result_generation_authorized": False,
            "portfolio_decision_authorized": False,
            "trade_proposal_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
        self.assertEqual(CONTRACT["authority"], expected)
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected())
        self.assertEqual(packet["authority"], expected)
        # Only the two *_only flags are ever true.
        for key, value in expected.items():
            if key.endswith("_only"):
                self.assertTrue(value, key)
            else:
                self.assertFalse(value, key)

    def test_trade_proposal_is_null_across_every_opportunity_state(self):
        cases = [
            (ft_no_evidence(), eg_positive_proxy(), pr_partially_reflected()),                                    # BLOCKED
            (ft_status("UNKNOWN"), eg_unknown(), pr_unknown()),                                                    # BLOCKED
            (ft_status("CONVERSION_DISAPPOINTED"), eg_neutral_consensus(), pr_partially_reflected()),              # REJECTED
            (ft_status("UNKNOWN"), eg_negative_proxy(), pr_overextended()),                                        # REJECTED
            (ft_status("REVENUE_CONVERSION_EXPECTED"), eg_negative_proxy(), pr_overextended()),                    # WAIT_FOR_THESIS_REPAIR
            (ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_unknown()),         # WAIT_FOR_PRICE
            (ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected()),                      # WAIT_FOR_EVIDENCE (narrative-only)
            (ft_status_with_exhibit("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected()),         # ANTICIPATORY_REVIEW
            (ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_positive_proxy(), pr_fully_reflected()),    # EXPECTATION_EXHAUSTED
            (ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_overextended()),    # WAIT_FOR_PULLBACK
            (ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_partially_reflected()), # CONFIRMATION_REVIEW
            (ft_status_with_exhibit("PRE_REVENUE_SIGNAL"), eg_unknown(), pr_under_reflected()),                    # EARLY_DISCOVERY
        ]
        seen_states = set()
        for ft, eg, pr in cases:
            packet = build(ft, eg, pr)
            self.assertIsNone(packet["trade_proposal"])
            seen_states.add(packet["opportunity_state"])
        self.assertEqual(seen_states, set(CONTRACT["opportunity_states"]))

    def test_validate_packet_rejects_non_null_trade_proposal(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected())
        tampered = copy.deepcopy(packet)
        tampered["trade_proposal"] = {"schema_version": "trade_proposal_draft/1"}
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "TRADE_PROPOSAL_MUST_BE_NULL"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_source_never_assigns_a_non_none_literal_to_trade_proposal(self):
        # Static guard: the only literal assignment of the trade_proposal key
        # anywhere in the module source must be the hard-coded None.
        text = SOURCE.read_text(encoding="utf-8")
        assignments = re.findall(r'"trade_proposal"\s*:\s*([^,\n]+)', text)
        self.assertTrue(assignments)
        for value in assignments:
            self.assertEqual(value.strip(), "None")


class TamperDetectionAndDeterminismTests(unittest.TestCase):
    def test_determinism_same_input_yields_byte_identical_output(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        pr = pr_partially_reflected()
        first = build(ft, eg, pr)
        second = build(copy.deepcopy(ft), copy.deepcopy(eg), copy.deepcopy(pr))
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])

    def test_tamper_detection_rejects_mutated_packet(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_partially_reflected())
        tampered = copy.deepcopy(packet)
        tampered["opportunity_state"] = "CONFIRMATION_REVIEW"
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "OUTPUT_SHA_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

        # Rehashing alone can never legitimize a corrupted, inconsistent state.
        corrupted = copy.deepcopy(packet)
        corrupted["opportunity_state"] = "REJECTED"
        corrupted["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in corrupted.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "OPPORTUNITY_STATE_INCONSISTENT"):
            MODULE.validate_packet(corrupted, CONTRACT)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        import ast
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        pr = pr_partially_reflected()
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps({
                "forward_thesis_packet": ft,
                "expectations_gap_packet": eg,
                "price_reflection_packet": pr,
                "generated_at": GENERATED_AT,
            }), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "alpha_review_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
