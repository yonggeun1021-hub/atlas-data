#!/usr/bin/env python3
"""P8-10 Price Reflection regression.

`price_reflection/3` (CIO review round 3 on PR #212) fixed four further
defects round 2 left open — see decision/price_reflection.py's module
docstring for the full list. In short: (1) a bare `direction`/
`expectations_gap_status` string is not a real reference — `event_reaction`
now requires `source_ref`+`source_sha256` lineage, and
`reflection_reference.expectations_gap_packet` must be the FULL,
independently re-validated P8-09 packet with matching subject/decision_date;
(2) reflection grading now uses only a caller-computed, event-anchored
`post_event_return_pct`/`post_reference_return_pct`, never the generic
recent_return_windows/relative_strength figures; (3)
`price_state=UNKNOWN`+non-UNKNOWN `reflection_status` is now a hard,
structurally-enforced impossibility; (4) `threshold_basis` gates
`decision/alpha_review.py`'s operational output, tested there."""

import ast
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "price_reflection.py"
EG_SOURCE = ROOT / "decision" / "expectations_gap.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("price_reflection", SOURCE)
CONTRACT = MODULE.load_contract()
EG = load_module("price_reflection_eg_fixture", EG_SOURCE)
EG_CONTRACT = EG.load_contract()

SHA_A = "a" * 64
SHA_B = "b" * 64

# A fully lineage-verified, event-anchored positive reference -- the ONLY
# shape of event_reaction that can unlock a confident reflection_status
# post-round-3. Individual tests override post_event_return_pct to exercise
# UNDER/PARTIALLY/FULLY_REFLECTED.
VERIFIED_POSITIVE_EVENT = {
    "event_date": "2026-08-10", "direction": "POSITIVE", "reaction_magnitude_pct": "5",
    "source_ref": "REAL-FILING-CITATION-1", "source_sha256": SHA_A,
    "post_event_return_pct": "9",
}


def base_kwargs(**overrides):
    value = {
        "subject": "TSM",
        "decision_date": "2026-08-22",
        "generated_at": "2026-08-22T00:00:00Z",
        "contract": CONTRACT,
    }
    value.update(overrides)
    return value


def resign(tampered: dict) -> dict:
    tampered["packet_sha256"] = MODULE.payload_sha256(
        {k: v for k, v in tampered.items() if k != "packet_sha256"}
    )
    return tampered


def eg_packet(subject="TSM", decision_date="2026-08-22", generated_at="2026-08-22T00:00:00Z", direction="POSITIVE"):
    return EG.build_packet({
        "subject": subject, "decision_date": decision_date, "generated_at": generated_at,
        "guidance_changes": {"direction": direction, "evidence_note": "real guidance evidence"},
    }, EG_CONTRACT)


class PriceReflectionTests(unittest.TestCase):
    # ── authority ────────────────────────────────────────────────────
    def test_authority_dict_exact_values(self):
        self.assertEqual(CONTRACT["authority"], {
            "price_reflection_assembly_only": True,
            "rule_authority_substitution_authorized": False,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })
        packet = MODULE.build_packet(**base_kwargs())
        self.assertEqual(packet["authority"], CONTRACT["authority"])

    # ── Rule 1: staleness forces both fields UNKNOWN unconditionally ────
    def test_stale_price_as_of_forces_both_fields_unknown_despite_strong_positive_inputs(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-01T19:59:00Z",  # 21 days before decision_date
            recent_return_windows={"1m": "25", "3m": "30", "6m": "40"},
            relative_strength={"vs_market": "20", "position_vs_recent_high_pct": "1"},
            valuation_context={"position_in_range": "HIGH"},
            event_reaction=VERIFIED_POSITIVE_EVENT,
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["confidence"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "PRICE_STALE")
        self.assertTrue(any("STALE" in reason for reason in pr["reasons"]))

    def test_missing_price_as_of_forces_both_fields_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            recent_return_windows={"1m": "25"},
            relative_strength={"vs_market": "20"},
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["confidence"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "PRICE_DATA_MISSING")
        self.assertIn("price_as_of", pr["missing_inputs"])

    def test_fresh_price_within_ceiling_is_not_forced_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",  # 1 day before decision_date
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertNotEqual(packet["price_reflection"]["price_state"], "UNKNOWN")

    def test_price_as_of_in_future_is_rejected(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "PRICE_AS_OF_IN_FUTURE"):
            MODULE.build_packet(**base_kwargs(price_as_of="2026-08-23T00:00:00Z"))

    def test_custom_freshness_ceiling_is_honored(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-01T19:59:00Z",
            freshness_ceiling_days=30,
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertNotEqual(packet["price_reflection"]["price_state"], "UNKNOWN")

    # ── OVEREXTENDED documented as timing risk, not "bad company" ───────
    def test_overextended_documented_as_timing_not_business_quality(self):
        doc = (ROOT / "docs" / "price_reflection_contract.md").read_text(encoding="utf-8")
        self.assertIn("does not mean the", doc.lower())
        self.assertIn("entry-timing risk", doc.lower())

    # ── Rule 2: structurally no thesis/fundamental parameter ───────────
    def test_builder_signature_has_no_thesis_or_fundamental_parameter(self):
        params = list(inspect.signature(MODULE.build_packet).parameters)
        for bad in MODULE.FORBIDDEN_PARAMETER_SUBSTRINGS:
            self.assertFalse(
                any(bad in name.lower() for name in params),
                f"forbidden substring {bad!r} found in builder parameters {params}",
            )
        MODULE.assert_no_fundamental_parameters()

    # ── Core CIO round-2 fix: momentum alone never yields a reflection verdict ──
    def test_sharp_rally_near_high_with_no_reference_point_is_overextended_price_state_but_unknown_reflection(self):
        self.assertNotIn("REJECTED", CONTRACT["allowed_price_state"])
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "25"},
            relative_strength={"vs_market": "20", "position_vs_recent_high_pct": "1"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "OVEREXTENDED")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")
        self.assertIn("NO_REFLECTION_REFERENCE_POINT", pr["reasons"])

    def test_strong_momentum_alone_never_produces_fully_reflected(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10", "position_vs_recent_high_pct": "50"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "STRONG_MOMENTUM")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertNotIn(pr["reflection_status"], ("FULLY_REFLECTED", "PARTIALLY_REFLECTED", "UNDER_REFLECTED"))

    # ── CIO round 3, exact reproduction case (verbatim) ─────────────────
    def test_cio_round3_reproduction_case_no_longer_contradicts(self):
        """Verbatim repro: 1-month return +10%, 1 positive event/reference
        point (bare, no lineage/anchored return), no other price signal.
        Round 2 produced price_state=UNKNOWN / reflection_status=
        FULLY_REFLECTED / data_state=VALID -- an impossible contradiction.
        Round 3 must produce BOTH fields UNKNOWN, never that combination."""
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "10"},
            event_reaction={"event_date": "2026-08-10", "direction": "POSITIVE", "reaction_magnitude_pct": "5"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")
        # The specific old (wrong) combination must never appear.
        self.assertFalse(pr["price_state"] == "UNKNOWN" and pr["reflection_status"] == "FULLY_REFLECTED")

    # ── Required item 1: bare direction/status is not a real reference ──
    def test_bare_event_direction_without_evidence_lineage_does_not_unlock_reflection(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction={"event_date": "2026-08-10", "direction": "POSITIVE", "reaction_magnitude_pct": "5"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")  # generic momentum still computable
        self.assertEqual(pr["reflection_status"], "UNKNOWN")  # but no lineage -> no verdict
        self.assertIn(
            "REFERENCE_POINT_PRESENT_BUT_NOT_LINEAGE_VERIFIED_OR_POST_REFERENCE_RETURN_NOT_COMPUTABLE",
            pr["reasons"],
        )

    def test_event_direction_with_lineage_but_no_post_event_return_still_does_not_unlock(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction={
                "event_date": "2026-08-10", "direction": "POSITIVE", "reaction_magnitude_pct": "5",
                "source_ref": "REAL-CITATION", "source_sha256": SHA_A,
                # post_event_return_pct deliberately omitted
            },
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")

    def test_bare_expectations_gap_status_string_is_no_longer_an_accepted_field(self):
        # Round 2's bare `expectations_gap_status` string field is retired;
        # only the full `expectations_gap_packet` is accepted now.
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "REFLECTION_REFERENCE_FIELDS_MISMATCH"):
            MODULE.build_packet(**base_kwargs(
                reflection_reference={"expectations_gap_status": "POSITIVE"},
            ))

    # ── Required item 1: full P8-09 packet verification ─────────────────
    def test_reflection_reference_via_validated_expectations_gap_packet_unlocks_reflection(self):
        gap = eg_packet()
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            reflection_reference={"expectations_gap_packet": gap, "post_reference_return_pct": "9"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertEqual(pr["reflection_reference"]["expectations_gap_status"], "POSITIVE")
        self.assertEqual(pr["reflection_reference"]["expectations_gap_packet_sha256"], gap["packet_sha256"])

    def test_expectations_gap_packet_subject_mismatch_is_rejected(self):
        gap = eg_packet(subject="OTHER_SUBJECT")
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_SUBJECT_MISMATCH"
        ):
            MODULE.build_packet(**base_kwargs(
                price_as_of="2026-08-21T19:59:00Z",
                reflection_reference={"expectations_gap_packet": gap, "post_reference_return_pct": "9"},
            ))

    def test_expectations_gap_packet_decision_date_mismatch_is_rejected(self):
        gap = eg_packet(decision_date="2026-08-21", generated_at="2026-08-21T00:00:00Z")
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_DECISION_DATE_MISMATCH"
        ):
            MODULE.build_packet(**base_kwargs(
                price_as_of="2026-08-21T19:59:00Z",
                reflection_reference={"expectations_gap_packet": gap, "post_reference_return_pct": "9"},
            ))

    def test_tampered_expectations_gap_packet_is_rejected(self):
        gap = copy.deepcopy(eg_packet())
        gap["expectations_gap"]["gap_reasons"].append("INJECTED")  # invalidates packet_sha256
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_INVALID"
        ):
            MODULE.build_packet(**base_kwargs(
                price_as_of="2026-08-21T19:59:00Z",
                reflection_reference={"expectations_gap_packet": gap, "post_reference_return_pct": "9"},
            ))

    def test_expectations_gap_packet_not_a_dict_is_rejected(self):
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_INVALID"
        ):
            MODULE.build_packet(**base_kwargs(
                reflection_reference={"expectations_gap_packet": "not-a-dict", "post_reference_return_pct": "9"},
            ))

    def test_bare_reference_event_id_with_no_direction_still_leaves_reflection_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            reflection_reference={"reference_event_id": "EARNINGS-2026Q2"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")

    # ── Required item 2: only a post-reference-anchored return can grade ──
    def test_reference_point_via_event_reaction_unlocks_reflection_status(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction=VERIFIED_POSITIVE_EVENT,  # post_event_return_pct=9
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertEqual(pr["data_state"], "VALID")

    def test_reflection_grades_off_post_event_return_not_generic_1m_return(self):
        # Generic 1m return is huge (25%, would be FULLY_REFLECTED under the
        # old, wrong logic) but the real, event-anchored post_event_return
        # is small (1%, almost entirely PRE-event movement) -- must grade
        # UNDER_REFLECTED off the real anchored figure, not the generic one.
        # relative_strength.position_vs_recent_high_pct is supplied purely
        # so price_state itself has >=2 scored signals and is NOT UNKNOWN
        # (item 3's structural invariant would otherwise force
        # reflection_status back to UNKNOWN regardless of this test's point).
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "25"},
            relative_strength={"position_vs_recent_high_pct": "5"},
            event_reaction={
                "event_date": "2026-08-20", "direction": "POSITIVE", "reaction_magnitude_pct": "5",
                "source_ref": "REAL-CITATION", "source_sha256": SHA_A,
                "post_event_return_pct": "1",
            },
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")  # sanity: item 3 invariant not the cause here
        self.assertEqual(pr["reflection_status"], "UNDER_REFLECTED")
        self.assertTrue(any("post_reference_return_pct:1" in r for r in pr["reasons"]))

    def test_reference_point_with_disagreeing_momentum_is_under_reflected(self):
        event = dict(VERIFIED_POSITIVE_EVENT, post_event_return_pct="0.5")
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "1"},
            relative_strength={"vs_market": "0.5"},
            event_reaction=event,
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "UNDER_REFLECTED")

    def test_reference_point_with_moderate_agreeing_momentum_is_partially_reflected(self):
        event = dict(VERIFIED_POSITIVE_EVENT, post_event_return_pct="4")
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=event,
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "PARTIALLY_REFLECTED")

    # ── Required item 3: price_state=UNKNOWN blocks any reflection verdict ──
    def test_price_state_unknown_forces_reflection_unknown_even_with_full_lineage_and_anchored_return(self):
        # A fully lineage-verified event + a real post_event_return_pct
        # exist, but there is only ONE generic price signal (m1 alone,
        # scored_signals=1 < 2) so price_state itself comes out UNKNOWN.
        # reflection_status must be forced UNKNOWN too -- never FULLY_REFLECTED.
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "10"},
            event_reaction=VERIFIED_POSITIVE_EVENT,
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertIn("PRICE_STATE_UNKNOWN_BLOCKS_REFLECTION_VERDICT", pr["reasons"])

    def test_price_state_unknown_reflection_status_contradiction_blocked_on_tamper(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction=VERIFIED_POSITIVE_EVENT,
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "FULLY_REFLECTED")  # sanity
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["price_state"] = "UNKNOWN"
        tampered = resign(tampered)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_CONTRADICTION"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── price_state vocabulary is closed and has no REJECTED-like value ──
    def test_price_state_and_reflection_status_vocabularies_have_no_rejected_value(self):
        self.assertEqual(sorted(CONTRACT["allowed_price_state"]), sorted([
            "OVEREXTENDED", "STRONG_MOMENTUM", "MODERATE", "WEAK", "UNKNOWN",
        ]))
        self.assertEqual(sorted(CONTRACT["allowed_reflection_status"]), sorted([
            "UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED", "UNKNOWN",
        ]))

    def test_data_state_allowed_vocabulary(self):
        self.assertEqual(sorted(CONTRACT["allowed_data_state"]), sorted([
            "PRICE_DATA_MISSING", "PRICE_STALE",
            "REFLECTION_UNCERTAIN_WITH_VALID_PRICE", "VALID",
        ]))

    # ── threshold approval status (item 7 / round 3 item 4) ─────────────
    def test_classification_thresholds_are_declared_provisional(self):
        self.assertEqual(CONTRACT["classification_thresholds_approval_status"], "PROVISIONAL")
        self.assertIn(
            CONTRACT["classification_thresholds_approval_status"], CONTRACT["allowed_threshold_basis"],
        )

    def test_every_packet_echoes_threshold_basis_verbatim(self):
        packet = MODULE.build_packet(**base_kwargs())
        self.assertEqual(
            packet["price_reflection"]["threshold_basis"],
            CONTRACT["classification_thresholds_approval_status"],
        )

    def test_threshold_basis_mismatch_is_rejected(self):
        packet = MODULE.build_packet(**base_kwargs())
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["threshold_basis"] = "RATIFIED"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_THRESHOLD_BASIS_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── Korea data no longer has a bespoke triple-required gate ─────────
    def test_korea_partial_fields_still_yields_a_price_state_from_momentum_alone(self):
        packet = MODULE.build_packet(**base_kwargs(
            subject="298040",
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"position_vs_recent_high_pct": "10"},  # no vs_market
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")  # still no reference point

    def test_data_source_scope_propagates_verbatim(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["data_source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")

    def test_default_data_source_scope_is_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(packet["price_reflection"]["data_source_scope"], "UNKNOWN")

    def test_kraken_ohlc_scope_is_accepted_for_crypto_subjects(self):
        packet = MODULE.build_packet(**base_kwargs(
            subject="BTC",
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "18"},
            relative_strength={"position_vs_recent_high_pct": "0"},
            data_source_scope="KRAKEN_OHLC",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["data_source_scope"], "KRAKEN_OHLC")
        self.assertNotEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")  # no reference point supplied

    def test_allowed_data_source_scope_vocabulary(self):
        self.assertEqual(sorted(CONTRACT["allowed_data_source_scope"]), sorted([
            "IEX_ONLY_PARTIAL_US_MARKET", "KRX_OFFICIAL", "KRAKEN_OHLC", "UNKNOWN",
        ]))

    # ── closed enums reject out-of-vocabulary values ────────────────────
    def test_price_state_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["price_state"] = "MOONING"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_PRICE_STATE_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_reflection_status_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["reflection_status"] = "TOTALLY_PRICED_IN"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_REFLECTION_STATUS_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_data_source_scope_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "DATA_SOURCE_SCOPE_INVALID"):
            MODULE.build_packet(**base_kwargs(data_source_scope="BLOOMBERG_TERMINAL"))

    def test_valuation_position_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "VALUATION_CONTEXT_POSITION_INVALID"):
            MODULE.build_packet(**base_kwargs(valuation_context={"position_in_range": "SKY_HIGH"}))

    def test_event_reaction_direction_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_DIRECTION_INVALID"):
            MODULE.build_packet(**base_kwargs(event_reaction={
                "event_date": "2026-08-01", "direction": "MOONSHOT", "reaction_magnitude_pct": "5",
            }))

    def test_event_reaction_future_event_date_rejected(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_EVENT_DATE_IN_FUTURE"):
            MODULE.build_packet(**base_kwargs(event_reaction={
                "event_date": "2026-08-25", "direction": "POSITIVE", "reaction_magnitude_pct": "5",
            }))

    def test_event_reaction_source_sha256_format_is_validated(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_SOURCE_SHA256_INVALID"):
            MODULE.build_packet(**base_kwargs(event_reaction={
                "source_ref": "X", "source_sha256": "not-a-real-hash",
            }))

    def test_reflection_reference_expectation_as_of_future_is_rejected(self):
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATION_AS_OF_IN_FUTURE"
        ):
            MODULE.build_packet(**base_kwargs(
                reflection_reference={"expectation_as_of": "2026-08-25"},
            ))

    # ── minimal packet builds and validates ─────────────────────────────
    def test_minimal_packet_builds_and_validates(self):
        packet = MODULE.build_packet(**base_kwargs())
        MODULE.validate_packet(packet, CONTRACT)
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "PRICE_DATA_MISSING")

    def test_output_fields_are_exactly_the_specified_set(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(set(packet), {
            "schema_version", "contract_version", "generated_at", "subject",
            "decision_date", "price_reflection", "authority", "packet_sha256",
        })
        self.assertEqual(packet["schema_version"], "price_reflection_packet/3")
        self.assertEqual(packet["contract_version"], "price_reflection/3")
        self.assertEqual(set(packet["price_reflection"]), {
            "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
            "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
            "reflection_reference", "valuation_context", "reasons", "missing_inputs",
            "data_source_scope",
        })
        self.assertEqual(set(packet["price_reflection"]["event_reaction"]), {
            "event_date", "direction", "reaction_magnitude_pct",
            "source_ref", "source_sha256", "post_event_return_pct",
        })
        self.assertEqual(set(packet["price_reflection"]["reflection_reference"]), {
            "reference_event_id", "expectation_as_of", "expectations_gap_status",
            "expectations_gap_packet_sha256", "post_reference_return_pct",
        })

    # ── determinism + tamper detection ──────────────────────────────────
    def test_deterministic_and_tamper_evident(self):
        kwargs = base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5", "3m": "8"},
            relative_strength={"vs_market": "3", "position_vs_recent_high_pct": "12"},
            valuation_context={"position_in_range": "MID"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        )
        first = MODULE.build_packet(**kwargs)
        second = MODULE.build_packet(**kwargs)
        self.assertEqual(first, second)

        tampered = copy.deepcopy(first)
        tampered["price_reflection"]["reasons"].append("INJECTED")
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_SHA_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_data_state_reflection_status_consistency_is_enforced_on_tamper(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction=VERIFIED_POSITIVE_EVENT,
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["data_state"], "VALID")  # sanity
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["data_state"] = "PRICE_DATA_MISSING"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_DATA_STATE_REFLECTION_STATUS_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_data_state_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs())
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["data_state"] = "TOTALLY_FINE_TRUST_ME"
        tampered = resign(tampered)
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_DATA_STATE_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── CLI is offline and write-outside-repo only ──────────────────────
    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps({
                "subject": "TSM", "decision_date": "2026-08-22",
                "generated_at": "2026-08-22T00:00:00Z",
                "price_as_of": "2026-08-21T19:59:00Z",
                "recent_return_windows": {"1m": "5"},
                "relative_strength": {"vs_market": "3"},
            }), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "price_reflection_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
