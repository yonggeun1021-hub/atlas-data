#!/usr/bin/env python3
"""P8-11 Anticipatory Alpha Review regression.

Every fixture below is assembled from the REAL `forward_thesis.build_packet`
/ `expectations_gap.build_packet` / `price_reflection.build_packet` builders
(reusing the existing test modules' own fixture helpers) so this regression
also catches integration breakage against those three upstream modules, not
just alpha_review.py in isolation.

★ CIO final integration ruling on PR #212 (2026-08-23): `decision/price_
  reflection.py`'s Event Evidence Authority engine was removed entirely --
  `reflection_status` became the literal constant `"UNKNOWN"` in every
  packet the real `PR.build_packet()` can ever produce.

★ CIO closing-fix ruling (2026-08-23, immediately after the above):
  `price_reflection.validate_packet()` now unconditionally REJECTS any
  packet whose `reflection_status != "UNKNOWN"` -- CIO's own direct repro
  was taking a real packet, editing it to `PARTIALLY_REFLECTED` +
  `confidence="LOW"` + `data_state="VALID"`, recomputing the hash, and
  finding `validate_packet()` still accepted it. This means the earlier
  `_with_synthetic_reflection_status()` tamper pattern this file used to
  reach `alpha_review.py`'s positive opportunity_states is now itself
  REJECTED at the price_reflection layer before it can even be used as an
  input to `build()` (which calls `PRICE_REFLECTION.validate_packet()` on
  every `price_reflection_packet` it's given) -- so it, and every preset
  built on it (`pr_under_reflected`/`pr_partially_reflected`/`pr_fully_
  reflected`/`pr_overextended`), and the `ratified_thresholds()` simulation
  that used to unlock the positive-state gate alongside it, are all RETIRED
  entirely, not merely updated.

  `decision/alpha_review.py` independently, defensively enforces the SAME
  boundary again on its own: `classify_opportunity_state()` now returns
  `WAIT_FOR_PRICE` unconditionally once gates 1-2 pass, regardless of what
  `reflection_status`/`price_state`/`threshold_basis` a `pr` dict claims --
  closing the path where a forged/hand-constructed `pr` handed DIRECTLY to
  `classify_opportunity_state()` (bypassing `build_packet()`'s own upstream
  `PRICE_REFLECTION.validate_packet()` call entirely) could still reach a
  positive state. `validate_packet()` also independently rejects any
  ALREADY-ASSEMBLED Alpha Review packet whose EMBEDDED `price_reflection.
  reflection_status != "UNKNOWN"`, closing the analogous "tamper the
  embedded sub-object and re-sign" bypass one level up. `WAIT_FOR_RULE_
  RATIFICATION` is retired from the vocabulary entirely (contract bumped to
  `alpha_review/6`) -- it named a "reflection known, policy unratified"
  state built on a ratification-authority concept with no real
  implementation anywhere in this repo. The other 6 reflection-status-
  dependent positive states remain legal vocabulary members (no further
  bump needed if reintroduced) but are now structurally unreachable through
  either `classify_opportunity_state()` or `validate_packet()` -- their
  classification logic was removed, not left dead, and is deferred to the
  same future, P5-Rule-Authority-co-designed Reflection Evidence Authority
  workstream `decision/price_reflection.py`'s own removal already deferred
  to. Real production behavior is UNCHANGED by any of this: none of the
  removed states were reachable through the real, unmocked pipeline even
  before this closing fix.
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

# A real, stable Korea subject with real, multi-week committed daily closes
# (deliberately NOT one of the four restricted "must remain unchanged"
# Korea Pilot tickers).
REAL_EVIDENCE_SUBJECT = PR_FIXTURE.REAL_EVIDENCE_SUBJECT


def forward_thesis_packet(**overrides):
    overrides.setdefault("subject", REAL_EVIDENCE_SUBJECT)
    return FT.build_packet(FT_FIXTURE.minimal_input(**overrides), FT_FIXTURE.CONTRACT)


def expectations_gap_packet(**category_overrides):
    category_overrides.setdefault("subject", REAL_EVIDENCE_SUBJECT)
    value = EG_FIXTURE.base_input(
        decision_date=DECISION_DATE, generated_at=GENERATED_AT, **category_overrides
    )
    return EG.build_packet(value, EG_FIXTURE.CONTRACT)


def price_reflection_packet(**overrides):
    kwargs = PR_FIXTURE.base_kwargs(
        subject=REAL_EVIDENCE_SUBJECT, decision_date=DECISION_DATE, generated_at=GENERATED_AT,
        contract=PR_FIXTURE.CONTRACT,
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


# ── price_reflection presets (all decision_date=2026-08-20, all REAL,
#    unmocked, untampered `PR.build_packet()` output) ───────────────────────
def pr_known_price(**overrides):
    """A real price_reflection packet with a genuine, non-UNKNOWN
    `price_state` (real momentum inputs) -- `reflection_status` is still
    always `"UNKNOWN"` (structural, see module docstring). This is the
    generic "some real price data exists" fixture for tests that don't
    care about opportunity_state classification specifics."""
    kwargs = dict(
        price_as_of=PR_FIXTURE.REAL_EVIDENCE_PRICE_AS_OF,
        recent_return_windows={"1m": "3"},
        relative_strength={"vs_market": "2"},
        data_source_scope="KRX_OFFICIAL",
    )
    kwargs.update(overrides)
    return price_reflection_packet(**kwargs)


def pr_overextended_no_reference_point():
    """Real price_state=OVEREXTENDED -- reflection_status stays UNKNOWN
    (structural, not merely "no reference point today")."""
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
    return forward_thesis_packet(
        earnings_conversion=FT_FIXTURE.earnings_conversion(status=status),
        observed_facts=[FT_FIXTURE.observed_fact(source_class="EXHIBIT_EXTRACTED")],
        evidence_lineage=[FT_FIXTURE.evidence_entry(source_type="SEC_EXHIBIT")],
    )


def ft_no_evidence():
    return forward_thesis_packet(observed_facts=[], evidence_lineage=[])


class OpportunityStateClassificationTests(unittest.TestCase):
    """One test per REACHABLE opportunity_state, built from real upstream
    packets. Only BLOCKED/REJECTED/WAIT_FOR_THESIS_REPAIR/WAIT_FOR_PRICE
    remain reachable in this reduced scope -- see module docstring."""

    def test_blocked_no_real_evidence(self):
        packet = build(ft_no_evidence(), eg_positive_proxy(), pr_known_price())
        self.assertEqual(packet["opportunity_state"], "BLOCKED")

    def test_blocked_triple_unknown(self):
        packet = build(ft_status("UNKNOWN"), eg_unknown(), pr_unknown())
        self.assertEqual(packet["opportunity_state"], "BLOCKED")

    def test_rejected_conversion_disappointed(self):
        packet = build(ft_status("CONVERSION_DISAPPOINTED"), eg_neutral_consensus(), pr_known_price())
        self.assertEqual(packet["opportunity_state"], "REJECTED")

    def test_rejected_negative_gap_with_unknown_earnings_conversion(self):
        # CIO Gate Hardening gate 2a: a NEGATIVE gap with NO live
        # earnings-conversion hypothesis (UNKNOWN) has nothing left to hold
        # onto -- REJECTED, not WAIT_FOR_THESIS_REPAIR. Mirrors the real
        # 267260.KS (HD Hyundai Electric) Pilot fact pattern.
        packet = build(ft_status("UNKNOWN"), eg_negative_proxy(), pr_overextended_no_reference_point())
        self.assertEqual(packet["opportunity_state"], "REJECTED")

    def test_wait_for_thesis_repair_negative_gap_with_live_earnings_conversion(self):
        # CIO Gate Hardening gate 2b: a NEGATIVE gap with a REAL
        # earnings-conversion hypothesis still standing may yet repair once
        # the market re-prices -- WAIT_FOR_THESIS_REPAIR, not REJECTED.
        # Price status is irrelevant to this gate.
        packet = build(
            ft_status("REVENUE_CONVERSION_EXPECTED"), eg_negative_proxy(), pr_overextended_no_reference_point(),
        )
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_THESIS_REPAIR")

    def test_wait_for_price_blocks_every_positive_state_when_price_is_unknown(self):
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_unknown())
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_PRICE")

    def test_overextended_price_with_no_reference_point_is_wait_for_price_not_a_positive_state(self):
        # CIO review round 2 core fix, still exercised end-to-end in the
        # reduced scope: a real rally (price_state=OVEREXTENDED) never
        # unlocks a positive state on its own -- WAIT_FOR_PRICE.
        pr = pr_overextended_no_reference_point()
        self.assertEqual(pr["price_reflection"]["price_state"], "OVEREXTENDED")
        self.assertEqual(pr["price_reflection"]["reflection_status"], "UNKNOWN")
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr)
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_PRICE")

    def test_a_real_strong_price_signal_still_only_reaches_wait_for_price(self):
        # Belt-and-suspenders: even a real, strong, positive price_state
        # (not just OVEREXTENDED) combined with an otherwise-strong thesis
        # and a positive gap never reaches anything but WAIT_FOR_PRICE now.
        pr = pr_known_price(recent_return_windows={"1m": "10"}, relative_strength={"vs_market": "9"})
        self.assertNotEqual(pr["price_reflection"]["price_state"], "UNKNOWN")
        self.assertEqual(pr["price_reflection"]["reflection_status"], "UNKNOWN")
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_positive_proxy(), pr)
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_PRICE")

    def test_reachable_opportunity_states_are_exactly_this_closed_set(self):
        # Belt-and-suspenders: the contract's closed vocabulary now has 10
        # members (WAIT_FOR_RULE_RATIFICATION retired), but only 4 are
        # actually reachable through classify_opportunity_state() any more.
        self.assertEqual(sorted(CONTRACT["opportunity_states"]), sorted([
            "EARLY_DISCOVERY", "ANTICIPATORY_REVIEW", "WAIT_FOR_PULLBACK",
            "WAIT_FOR_EVIDENCE", "CONFIRMATION_REVIEW", "EXPECTATION_EXHAUSTED",
            "REJECTED", "BLOCKED", "WAIT_FOR_PRICE", "WAIT_FOR_THESIS_REPAIR",
        ]))
        self.assertNotIn("WAIT_FOR_RULE_RATIFICATION", CONTRACT["opportunity_states"])


class ClosingFixReducedScopeTests(unittest.TestCase):
    """CIO closing-fix ruling (2026-08-23): defense-in-depth regressions
    proving alpha_review.py enforces the reduced-scope boundary
    independently of price_reflection.py, and that the retired positive-
    state machinery is genuinely gone (not merely unreachable by
    convention)."""

    def test_anticipatory_review_gates_function_no_longer_exists(self):
        self.assertFalse(hasattr(MODULE, "anticipatory_review_gates"))

    def test_forged_packet_fed_directly_to_classify_opportunity_state_cannot_reach_a_positive_state(self):
        """The exact bypass surface CIO's closing-fix ruling named: a
        forged `pr` dict claiming a confident reflection_status AND a
        RATIFIED threshold_basis AND OVEREXTENDED-avoiding price_state --
        everything a positive state would have required under the old
        design -- handed DIRECTLY to classify_opportunity_state() (as this
        module's own gate-level tests used to, bypassing build_packet()'s
        own PRICE_REFLECTION.validate_packet() call entirely) must still
        resolve to WAIT_FOR_PRICE, never a positive/differentiated state."""
        ft = ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED")
        gap = eg_positive_proxy()["expectations_gap"]
        decision_date = dt.date.fromisoformat(DECISION_DATE)
        for forged_reflection_status in ("UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED"):
            with self.subTest(reflection_status=forged_reflection_status):
                forged_pr = {
                    "reflection_status": forged_reflection_status,
                    "price_state": "MODERATE",
                    "threshold_basis": "RATIFIED",
                }
                state = MODULE.classify_opportunity_state(ft, gap, forged_pr, decision_date)
                self.assertEqual(state, "WAIT_FOR_PRICE")
                self.assertNotIn(state, (
                    "ANTICIPATORY_REVIEW", "EXPECTATION_EXHAUSTED", "WAIT_FOR_PULLBACK",
                    "CONFIRMATION_REVIEW", "EARLY_DISCOVERY", "WAIT_FOR_EVIDENCE",
                ))

    def test_forged_embedded_reflection_status_is_rejected_by_validate_packet(self):
        """The `validate_packet()`-level analogue of the CIO's own repro
        case: build a real, valid packet, then tamper its EMBEDDED
        `price_reflection.reflection_status` to a forged positive value
        (paired with a forged positive `opportunity_state`, both
        internally "consistent" with each other and re-signed) -- this is
        exactly the externally-injected forged packet CIO's report
        describes, and `validate_packet()` must reject it outright."""
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_positive_proxy(), pr_known_price())
        self.assertEqual(packet["opportunity_state"], "WAIT_FOR_PRICE")  # sanity: real, reachable state
        tampered = copy.deepcopy(packet)
        tampered["price_reflection"]["reflection_status"] = "FULLY_REFLECTED"
        tampered["price_reflection"]["confidence"] = "HIGH"
        tampered["price_reflection"]["data_state"] = "VALID"
        tampered["opportunity_state"] = "EXPECTATION_EXHAUSTED"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.AlphaReviewError, "PRICE_REFLECTION_REFLECTION_STATUS_MUST_BE_UNKNOWN_IN_THIS_REDUCED_SCOPE"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_claiming_a_now_unreachable_positive_state_with_a_genuinely_unknown_reflection_is_also_rejected(self):
        """Even if a forger leaves `reflection_status="UNKNOWN"` alone
        (passing the new blanket check) but still claims a positive
        `opportunity_state` like `ANTICIPATORY_REVIEW`, at least one
        independent invariant catches it -- here the non-RATIFIED-
        threshold_basis check fires first (`pr_known_price()`'s real
        `threshold_basis` is `"PROVISIONAL"`); the reflection_status
        invariant (`OUTPUT_UNKNOWN_REFLECTION_STATUS_UNLOCKED_OPPORTUNITY_
        STATE`) is exercised directly on `_check_opportunity_state_
        consistency` below, isolated from this one."""
        packet = build(ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_positive_proxy(), pr_known_price())
        self.assertEqual(packet["price_reflection"]["threshold_basis"], "PROVISIONAL")  # sanity
        tampered = copy.deepcopy(packet)
        tampered["opportunity_state"] = "ANTICIPATORY_REVIEW"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.AlphaReviewError, "OUTPUT_UNRATIFIED_THRESHOLD_BASIS_UNLOCKED_OPPORTUNITY_STATE"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

        # Isolate the reflection_status invariant directly, independent of
        # threshold_basis: neutralize the threshold-basis check by forging
        # threshold_basis="RATIFIED" too (still fails to legitimize
        # anything -- reflection_status=="UNKNOWN" alone must still block
        # any state outside the closed fail-set).
        tampered2 = copy.deepcopy(packet)
        tampered2["opportunity_state"] = "ANTICIPATORY_REVIEW"
        tampered2["price_reflection"]["threshold_basis"] = "RATIFIED"
        tampered2["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered2.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.AlphaReviewError, "OUTPUT_UNKNOWN_REFLECTION_STATUS_UNLOCKED_OPPORTUNITY_STATE"
        ):
            MODULE.validate_packet(tampered2, CONTRACT)


class InputCompositionTests(unittest.TestCase):
    def test_missing_sub_packets_are_rejected(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        pr = pr_known_price()
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
        pr = pr_known_price()
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
        pr = pr_known_price()
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "SUBJECT_MISMATCH_ACROSS_INPUT_PACKETS"):
            build(ft, eg, pr)

    def test_decision_date_mismatch_across_input_packets_is_rejected(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        pr = PR.build_packet(
            subject=REAL_EVIDENCE_SUBJECT, decision_date="2026-08-21", generated_at=GENERATED_AT,
            contract=PR_FIXTURE.CONTRACT,
        )
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "DECISION_DATE_MISMATCH_ACROSS_INPUT_PACKETS"):
            build(ft, eg, pr)


class PassThroughFieldTests(unittest.TestCase):
    def test_p5_rule_status_and_portfolio_status_default_to_not_evaluated(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price())
        self.assertEqual(packet["p5_rule_status"], "NOT_EVALUATED")
        self.assertEqual(packet["portfolio_status"], "NOT_EVALUATED")

    def test_p5_rule_status_and_portfolio_status_are_caller_pass_through(self):
        packet = build(
            ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price(),
            p5_rule_status="FAIL", portfolio_status="PASS",
        )
        self.assertEqual(packet["p5_rule_status"], "FAIL")
        self.assertEqual(packet["portfolio_status"], "PASS")

    def test_invalid_p5_rule_status_and_portfolio_status_are_rejected(self):
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "P5_RULE_STATUS_INVALID"):
            build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price(), p5_rule_status="MOONSHOT")
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "PORTFOLIO_STATUS_INVALID"):
            build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price(), portfolio_status="MOONSHOT")

    def test_thesis_status_and_earnings_conversion_status_are_verbatim_and_equal(self):
        packet = build(ft_status("BACKLOG_BUILDING"), eg_positive_proxy(), pr_known_price())
        self.assertEqual(packet["thesis_status"], "BACKLOG_BUILDING")
        self.assertEqual(packet["earnings_conversion_status"], "BACKLOG_BUILDING")

    def test_expectations_gap_and_price_reflection_embedded_verbatim(self):
        eg = eg_positive_proxy()
        pr = pr_known_price()
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg, pr)
        self.assertEqual(packet["expectations_gap"], eg["expectations_gap"])
        self.assertEqual(packet["price_reflection"], pr["price_reflection"])

    def test_why_now_cites_specific_fields(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        packet = build(ft, eg, pr_known_price())
        why_now = packet["why_now"]
        self.assertTrue(any(ft["catalysts"][0]["description"] in line for line in why_now))
        self.assertTrue(any(ft["forward_inferences"][0]["statement"] in line for line in why_now))
        self.assertTrue(any(eg["expectations_gap"]["gap_reasons"][0] in line for line in why_now))

    def test_what_market_may_be_missing_excludes_catalysts(self):
        ft = ft_status("PRE_REVENUE_SIGNAL")
        eg = eg_positive_proxy()
        packet = build(ft, eg, pr_known_price())
        for line in packet["what_market_may_be_missing"]:
            self.assertNotIn("forward_thesis.catalysts:", line)

    def test_invalidation_conditions_always_non_empty(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price())
        self.assertTrue(len(packet["invalidation_conditions"]) >= 2)


class NextReviewDateTests(unittest.TestCase):
    def test_uses_earliest_review_date_at_or_after_decision_date(self):
        ft = forward_thesis_packet(review_dates=["2026-09-01", "2026-08-25"])
        packet = build(ft, eg_positive_proxy(), pr_known_price())
        self.assertEqual(packet["next_review_date"], "2026-08-25")

    def test_falls_back_to_default_cadence_when_all_review_dates_are_stale(self):
        ft = forward_thesis_packet(review_dates=["2026-08-01"])
        packet = build(ft, eg_positive_proxy(), pr_known_price())
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
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price())
        self.assertEqual(packet["authority"], expected)
        # Only the two *_only flags are ever true.
        for key, value in expected.items():
            if key.endswith("_only"):
                self.assertTrue(value, key)
            else:
                self.assertFalse(value, key)

    def test_trade_proposal_is_null_across_every_reachable_opportunity_state(self):
        # Only 4 opportunity_states are reachable in this reduced scope --
        # see module docstring. trade_proposal must be null for all of them.
        cases = [
            (ft_status("UNKNOWN"), eg_unknown(), pr_unknown()),                                              # BLOCKED
            (ft_status("CONVERSION_DISAPPOINTED"), eg_neutral_consensus(), pr_known_price()),                 # REJECTED
            (ft_status("REVENUE_CONVERSION_EXPECTED"), eg_negative_proxy(), pr_overextended_no_reference_point()),  # WAIT_FOR_THESIS_REPAIR
            (ft_status_with_exhibit("REVENUE_CONVERSION_EXPECTED"), eg_neutral_consensus(), pr_unknown()),    # WAIT_FOR_PRICE
        ]
        seen_states = set()
        for ft, eg, pr in cases:
            packet = build(ft, eg, pr)
            self.assertIsNone(packet["trade_proposal"])
            seen_states.add(packet["opportunity_state"])
        self.assertEqual(seen_states, {"BLOCKED", "REJECTED", "WAIT_FOR_THESIS_REPAIR", "WAIT_FOR_PRICE"})

    def test_validate_packet_rejects_non_null_trade_proposal(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price())
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
        pr = pr_known_price()
        first = build(ft, eg, pr)
        second = build(copy.deepcopy(ft), copy.deepcopy(eg), copy.deepcopy(pr))
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])

    def test_tamper_detection_rejects_mutated_packet(self):
        packet = build(ft_status("PRE_REVENUE_SIGNAL"), eg_positive_proxy(), pr_known_price())
        tampered = copy.deepcopy(packet)
        tampered["opportunity_state"] = "REJECTED"
        with self.assertRaisesRegex(MODULE.AlphaReviewError, "OUTPUT_SHA_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

        # Rehashing alone can never legitimize a corrupted, inconsistent state.
        # (BLOCKED has no independent, reconstructable invariant -- REJECTED
        # does: this packet's real earnings_status/gap_status don't support
        # it, so claiming it must still be caught.)
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
        pr = pr_known_price()
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
