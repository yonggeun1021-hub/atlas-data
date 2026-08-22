#!/usr/bin/env python3
"""P8-10 Price Reflection regression.

`price_reflection/4` (CIO review round 4 on PR #212) closed the gap round 3
left open: round 3's "evidence verification" was only a FORMAT check --
`source_ref`/`source_sha256` were regex-validated but never cross-checked
against a real committed file, and `post_event_return_pct`/
`post_reference_return_pct` were still trusted caller-supplied numbers with
no real price lookup or PIT check behind them. Confirmed reproducible:
`source_ref="MADE-UP"`, `source_sha256="a"*64`, `post_event_return_pct="99"`
(all fabricated) produced a confident `FULLY_REFLECTED`.

Round 4 retires `post_event_return_pct`/`post_reference_return_pct` as
accepted input entirely, cross-checks `source_ref`/`source_sha256` against a
REAL committed repo file's REAL recomputed sha256, and computes every
reflection return internally from two real, PIT-verified close prices
(`decision/price_evidence.py`'s `real_close_on_date`/
`latest_real_close_at_or_before`, built on PR #210's `replay/price_series.py`
-- reused, not reimplemented). This regression file follows the CIO's
explicit instruction: it rewrites every fixture that used to fabricate a
`"a"*64`-style hash and a hand-picked return, and instead uses a REAL
committed evidence file (`data/2026-08-20/krx.json`), that file's REAL
recomputed sha256, and REAL close prices for a real KRX subject
(`329180.KS`, HD Hyundai Heavy Industries -- deliberately NOT one of the
four restricted "must remain unchanged" Korea Pilot tickers: 298040.KS/
267260.KS/005930.KS/000660.KS) to derive every UNDER/PARTIALLY/
FULLY_REFLECTED expectation asserted below. No test in this file trusts a
fabricated hash or a caller-authored return as if it were verified evidence.
"""

import ast
import copy
from decimal import Decimal
import hashlib
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

# ── real, independently-verifiable evidence (CIO round 4) ──────────────────
# A REAL, already-committed repo file -- not a fabricated citation -- whose
# sha256 is recomputed HERE, from the file's real bytes, exactly the way a
# legitimate caller would have to. `decision/price_reflection.py`
# independently recomputes the SAME hash from the SAME real file at
# verification time (`_verify_evidence_citation`); if that recomputation
# ever diverges from what a real caller can produce, this whole file's
# "FULLY/PARTIALLY/UNDER_REFLECTED" fixtures stop working, which is the
# point -- there is no shortcut here that "always passes".
REAL_EVIDENCE_SOURCE_REF = "data/2026-08-20/krx.json"
REAL_EVIDENCE_PATH = ROOT / REAL_EVIDENCE_SOURCE_REF
REAL_EVIDENCE_SHA256 = hashlib.sha256(REAL_EVIDENCE_PATH.read_bytes()).hexdigest()

# A real KRX subject with multiple weeks of real, committed daily closes
# (`data/<date>/krx.json`'s embedded `daily` window) -- NOT one of the four
# restricted "must remain unchanged" Korea Pilot tickers.
REAL_EVIDENCE_SUBJECT = "329180.KS"
REAL_EVIDENCE_DECISION_DATE = "2026-08-20"
REAL_EVIDENCE_GENERATED_AT = "2026-08-20T00:00:00Z"
REAL_EVIDENCE_PRICE_AS_OF = "2026-08-19T21:58:30Z"

# Real, PIT-verified close prices independently recomputed here via the same
# `decision/price_evidence.py` primitives `price_reflection.py` itself uses,
# so the expected returns below are DERIVED, not hand-picked:
#   end anchor  = latest real close PIT-live at/before 2026-08-20 -> 2026-08-19 (474000.0)
#   FULLY:     event_date=2026-07-29, close=434000.0 -> +9.22%  (>= strong_momentum_min_pct=8)
#   PARTIALLY: event_date=2026-07-20, close=448500.0 -> +5.69%  (in [mild=2, strong=8))
#   UNDER:     event_date=2026-08-18, close=489500.0 -> -3.17%  (direction POSITIVE, return negative -> disagrees)
_PRICE_EVIDENCE = MODULE.PRICE_EVIDENCE
_END_DATE, _END_CLOSE = _PRICE_EVIDENCE.latest_real_close_at_or_before(
    REAL_EVIDENCE_SUBJECT, REAL_EVIDENCE_DECISION_DATE
)


def _real_verified_return(event_date: str):
    """Independently recomputes the SAME return `price_reflection.py` will
    compute internally -- used only to assert the module's own output
    matches real, derivable arithmetic, never to shortcut verification."""
    start_close = _PRICE_EVIDENCE.real_close_on_date(
        REAL_EVIDENCE_SUBJECT, event_date, REAL_EVIDENCE_DECISION_DATE
    )
    return (_END_CLOSE / start_close - 1) * 100


def verified_event_reaction(event_date: str, direction: str = "POSITIVE", reaction_magnitude_pct: str = "5") -> dict:
    """A fully real, lineage-verified `event_reaction` -- a real committed
    file, that file's real recomputed sha256, and an `event_date` that is
    genuinely PIT-live-known by `REAL_EVIDENCE_DECISION_DATE`. This is the
    ONLY shape of `event_reaction` that can unlock a confident
    `reflection_status` post-round-4 -- no `post_event_return_pct` field
    exists any more (see `decision/price_reflection.py`'s module docstring:
    it is RETIRED as an accepted input and its presence here would raise
    `EVENT_REACTION_FIELDS_MISMATCH`)."""
    return {
        "event_date": event_date, "direction": direction, "reaction_magnitude_pct": reaction_magnitude_pct,
        "source_ref": REAL_EVIDENCE_SOURCE_REF, "source_sha256": REAL_EVIDENCE_SHA256,
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


def real_evidence_kwargs(**overrides):
    value = {
        "subject": REAL_EVIDENCE_SUBJECT,
        "decision_date": REAL_EVIDENCE_DECISION_DATE,
        "generated_at": REAL_EVIDENCE_GENERATED_AT,
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
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of="2026-07-30T19:59:00Z",  # >5 days before decision_date
            recent_return_windows={"1m": "25", "3m": "30", "6m": "40"},
            relative_strength={"vs_market": "20", "position_vs_recent_high_pct": "1"},
            valuation_context={"position_in_range": "HIGH"},
            event_reaction=verified_event_reaction("2026-07-29"),
            data_source_scope="KRX_OFFICIAL",
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
        Round 3+ must produce BOTH fields UNKNOWN, never that combination."""
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

    # ── CIO round 4, exact reproduction case (verbatim) ──────────────────
    def test_cio_round4_reproduction_case_fabricated_source_and_hash_no_longer_unlocks_reflection(self):
        """Verbatim repro (HEAD 323f03b): source_ref="MADE-UP",
        source_sha256="a"*64 (an arbitrary 64-char string, not resolved
        against any real artifact), 1m=4, vs_market=3. Round 3 (this exact
        input, minus a caller-supplied return field which round 4 retires
        outright) produced price_state=MODERATE / reflection_status=
        FULLY_REFLECTED / data_state=VALID -- fabricated evidence unlocking
        a confident verdict. Round 4 must produce reflection_status=UNKNOWN,
        data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE, and the specific
        real-evidence-not-reconstructable reason, never a confident status."""
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction={
                "event_date": REAL_EVIDENCE_DECISION_DATE, "direction": "POSITIVE",
                "source_ref": "MADE-UP", "source_sha256": "a" * 64,
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "MODERATE")  # sanity: momentum read is real and unaffected
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")
        self.assertIn("REFERENCE_POINT_PRESENT_BUT_NOT_RECONSTRUCTABLE_FROM_REAL_EVIDENCE", pr["reasons"])
        # The specific old (wrong) combination must never appear.
        self.assertFalse(pr["price_state"] == "MODERATE" and pr["reflection_status"] == "FULLY_REFLECTED")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], "UNKNOWN")

    def test_cio_round4_retired_caller_supplied_return_field_is_rejected_outright(self):
        """`post_event_return_pct` is not merely ignored -- supplying it at
        all is a structural EVENT_REACTION_FIELDS_MISMATCH, proving there is
        no remaining code path where a caller-authored return number is
        accepted."""
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_FIELDS_MISMATCH"):
            MODULE.build_packet(**real_evidence_kwargs(
                event_reaction=dict(verified_event_reaction("2026-07-29"), post_event_return_pct="99"),
            ))
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "REFLECTION_REFERENCE_FIELDS_MISMATCH"):
            MODULE.build_packet(**real_evidence_kwargs(
                reflection_reference={"reference_event_id": "EARNINGS-2026Q2", "post_reference_return_pct": "99"},
            ))

    def test_cio_round4_wrong_hash_for_a_real_file_does_not_unlock_reflection(self):
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction={
                "event_date": "2026-07-29", "direction": "POSITIVE",
                "source_ref": REAL_EVIDENCE_SOURCE_REF, "source_sha256": "f" * 64,  # real file, WRONG hash
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")

    def test_cio_round4_nonexistent_file_does_not_unlock_reflection(self):
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction={
                "event_date": "2026-07-29", "direction": "POSITIVE",
                "source_ref": "data/2026-08-20/does_not_exist.json", "source_sha256": REAL_EVIDENCE_SHA256,
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")

    def test_cio_round4_path_traversal_source_ref_is_rejected_not_resolved_outside_repo(self):
        # Passes SOURCE_REF_RE's format check (alnum-start, real charset) but
        # resolves outside ROOT once ".." is applied -- must be blocked by
        # _verify_evidence_citation's relative_to() check, not merely by
        # format validation, and must soft-downgrade to UNKNOWN, never raise.
        traversal_ref = "a/../../../../../../etc/passwd"
        self.assertIsNotNone(MODULE.SOURCE_REF_RE.fullmatch(traversal_ref))  # sanity: format alone would pass
        self.assertFalse(MODULE._verify_evidence_citation(traversal_ref, REAL_EVIDENCE_SHA256))
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction={
                "event_date": "2026-07-29", "direction": "POSITIVE",
                "source_ref": traversal_ref, "source_sha256": REAL_EVIDENCE_SHA256,
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")

    def test_cio_round4_pit_violation_real_event_not_yet_live_known_does_not_unlock_reflection(self):
        # `event_date=2026-07-29` is a REAL trading date with a REAL close,
        # cited via a REAL file + REAL hash -- but as of decision_date
        # 2026-08-01 that trading date is not yet PIT-live-known in this
        # repo's real committed evidence (it only becomes live once the
        # 2026-08-13 snapshot is captured). Real evidence existing
        # eventually is not enough -- it must have been knowable AS OF the
        # decision timestamp (PR #210/#211 PIT discipline, reused unchanged).
        self.assertIsNone(
            MODULE.PRICE_EVIDENCE.real_close_on_date(REAL_EVIDENCE_SUBJECT, "2026-07-29", "2026-08-01")
        )
        packet = MODULE.build_packet(**real_evidence_kwargs(
            decision_date="2026-08-01",
            generated_at="2026-08-01T00:00:00Z",
            price_as_of="2026-07-31T21:58:30Z",
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction={
                "event_date": "2026-07-29", "direction": "POSITIVE",
                "source_ref": REAL_EVIDENCE_SOURCE_REF, "source_sha256": REAL_EVIDENCE_SHA256,
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertIn("REFERENCE_POINT_PRESENT_BUT_NOT_RECONSTRUCTABLE_FROM_REAL_EVIDENCE", pr["reasons"])

    def test_cio_round4_end_endpoint_captured_after_decision_date_is_unavailable_not_used(self):
        # Required CIO round-4 regression: "endpoint availability after
        # decision fails". A real trading date (2026-07-06) and a real close
        # for it genuinely exist in this repo's committed evidence, but that
        # evidence row was only CAPTURED (committed) by the 2026-08-13
        # snapshot -- as of an earlier decision_date, neither the event's
        # START close nor the END close ("latest real close at/before
        # decision_date") is available yet, even though the underlying
        # trading session itself is real and in the past. Evidence existing
        # eventually must never be used as if it were knowable earlier.
        self.assertEqual(
            MODULE.PRICE_EVIDENCE.latest_real_close_at_or_before(REAL_EVIDENCE_SUBJECT, "2026-08-12"),
            (None, None),
        )
        packet = MODULE.build_packet(**real_evidence_kwargs(
            decision_date="2026-08-12",
            generated_at="2026-08-12T00:00:00Z",
            price_as_of="2026-08-11T21:58:30Z",
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction={
                "event_date": "2026-07-06", "direction": "POSITIVE",
                "source_ref": REAL_EVIDENCE_SOURCE_REF, "source_sha256": REAL_EVIDENCE_SHA256,
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], "UNKNOWN")

    # ── CIO round 4: real, fully-verified evidence DOES unlock reflection ──
    def test_cio_round4_real_verified_event_unlocks_fully_reflected_with_exact_computed_return(self):
        expected_return = _real_verified_return("2026-07-29")
        strong_threshold = Decimal(CONTRACT["classification_thresholds"]["strong_momentum_min_pct"])
        self.assertGreaterEqual(abs(expected_return), strong_threshold)
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction("2026-07-29"),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertEqual(pr["data_state"], "VALID")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], str(expected_return))
        self.assertTrue(any(f"verified_return_pct:{expected_return}" in r for r in pr["reasons"]))

    def test_cio_round4_real_verified_event_unlocks_partially_reflected_with_exact_computed_return(self):
        expected_return = _real_verified_return("2026-07-20")
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction("2026-07-20"),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "PARTIALLY_REFLECTED")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], str(expected_return))

    def test_cio_round4_real_verified_event_with_disagreeing_move_is_under_reflected(self):
        expected_return = _real_verified_return("2026-08-18")
        self.assertLess(expected_return, 0)  # sanity: real move DISAGREES with the claimed POSITIVE direction
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction("2026-08-18"),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "UNDER_REFLECTED")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], str(expected_return))

    def test_cio_round4_real_verified_expectations_gap_reference_unlocks_reflection_anchored_to_its_own_decision_date(self):
        # Required item 4: the return's start point is anchored to the
        # validated P8-09 packet's OWN decision_date (echoed as
        # `expectations_gap_reference_date`), not an independently chosen
        # window.
        gap = eg_packet(
            subject=REAL_EVIDENCE_SUBJECT, decision_date="2026-07-29", generated_at="2026-07-29T00:00:00Z",
        )
        expected_return = _real_verified_return("2026-07-29")
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            reflection_reference={"expectations_gap_packet": gap},
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertEqual(pr["reflection_reference"]["expectations_gap_reference_date"], "2026-07-29")
        self.assertEqual(pr["reflection_reference"]["verified_post_reference_return_pct"], str(expected_return))
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], "UNKNOWN")  # source discrimination

    def test_cio_round4_event_reaction_preferred_over_expectations_gap_when_both_satisfiable(self):
        gap = eg_packet(
            subject=REAL_EVIDENCE_SUBJECT, decision_date="2026-07-20", generated_at="2026-07-20T00:00:00Z",
        )
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction("2026-07-29"),  # FULLY_REFLECTED anchor
            reflection_reference={"expectations_gap_packet": gap},  # would independently give PARTIALLY_REFLECTED
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertIn("reflection_basis_source:EVENT_REACTION", pr["reasons"])
        self.assertNotEqual(pr["event_reaction"]["verified_post_event_return_pct"], "UNKNOWN")
        self.assertEqual(pr["reflection_reference"]["verified_post_reference_return_pct"], "UNKNOWN")

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
            "REFERENCE_POINT_PRESENT_BUT_NOT_RECONSTRUCTABLE_FROM_REAL_EVIDENCE",
            pr["reasons"],
        )

    def test_event_direction_with_lineage_but_unresolvable_source_still_does_not_unlock(self):
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction={
                "event_date": "2026-07-29", "direction": "POSITIVE", "reaction_magnitude_pct": "5",
                "source_ref": "data/2026-08-20/does_not_exist.json", "source_sha256": REAL_EVIDENCE_SHA256,
            },
            data_source_scope="KRX_OFFICIAL",
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
        gap = eg_packet(
            subject=REAL_EVIDENCE_SUBJECT, decision_date="2026-07-29", generated_at="2026-07-29T00:00:00Z",
        )
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            reflection_reference={"expectations_gap_packet": gap},
            data_source_scope="KRX_OFFICIAL",
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
                reflection_reference={"expectations_gap_packet": gap},
            ))

    def test_expectations_gap_packet_decision_date_in_future_is_rejected(self):
        gap = eg_packet(decision_date="2026-08-23", generated_at="2026-08-23T00:00:00Z")
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_DECISION_DATE_IN_FUTURE"
        ):
            MODULE.build_packet(**base_kwargs(
                price_as_of="2026-08-21T19:59:00Z",
                reflection_reference={"expectations_gap_packet": gap},
            ))

    def test_tampered_expectations_gap_packet_is_rejected(self):
        gap = copy.deepcopy(eg_packet())
        gap["expectations_gap"]["gap_reasons"].append("INJECTED")  # invalidates packet_sha256
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_INVALID"
        ):
            MODULE.build_packet(**base_kwargs(
                price_as_of="2026-08-21T19:59:00Z",
                reflection_reference={"expectations_gap_packet": gap},
            ))

    def test_expectations_gap_packet_not_a_dict_is_rejected(self):
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_INVALID"
        ):
            MODULE.build_packet(**base_kwargs(
                reflection_reference={"expectations_gap_packet": "not-a-dict"},
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

    # ── Required item 3: price_state=UNKNOWN blocks any reflection verdict ──
    def test_price_state_unknown_forces_reflection_unknown_even_with_full_lineage_and_verified_return(self):
        # A fully lineage-verified, hash-checked event with a REAL,
        # internally-computed return exists, but there is only ONE generic
        # price signal (m1 alone, scored_signals=1 < 2) so price_state
        # itself comes out UNKNOWN. reflection_status must be forced UNKNOWN
        # too -- never FULLY_REFLECTED, no matter how real the return is.
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "10"},
            event_reaction=verified_event_reaction("2026-07-29"),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertIn("PRICE_STATE_UNKNOWN_BLOCKS_REFLECTION_VERDICT", pr["reasons"])
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], "UNKNOWN")

    def test_price_state_unknown_reflection_status_contradiction_blocked_on_tamper(self):
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction=verified_event_reaction("2026-07-29"),
            data_source_scope="KRX_OFFICIAL",
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "FULLY_REFLECTED")  # sanity
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["price_state"] = "UNKNOWN"
        tampered = resign(tampered)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_CONTRADICTION"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_tampered_verified_return_requires_non_unknown_reflection_status(self):
        # A verified return can never be present on a packet whose
        # reflection_status is UNKNOWN -- structurally re-asserted in
        # validate_packet, independent of how the packet was constructed.
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")  # sanity
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["event_reaction"]["verified_post_event_return_pct"] = "12.5"
        tampered = resign(tampered)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "OUTPUT_VERIFIED_RETURN_REQUIRES_NON_UNKNOWN_REFLECTION_STATUS"
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

    def test_event_reaction_source_ref_rejects_empty_and_leading_dot(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_SOURCE_REF_INVALID"):
            MODULE.build_packet(**base_kwargs(event_reaction={
                "source_ref": ".hidden", "source_sha256": REAL_EVIDENCE_SHA256,
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
        self.assertEqual(packet["schema_version"], "price_reflection_packet/4")
        self.assertEqual(packet["contract_version"], "price_reflection/4")
        self.assertEqual(set(packet["price_reflection"]), {
            "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
            "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
            "reflection_reference", "valuation_context", "reasons", "missing_inputs",
            "data_source_scope",
        })
        self.assertEqual(set(packet["price_reflection"]["event_reaction"]), {
            "event_date", "direction", "reaction_magnitude_pct",
            "source_ref", "source_sha256", "verified_post_event_return_pct",
        })
        self.assertEqual(set(packet["price_reflection"]["reflection_reference"]), {
            "reference_event_id", "expectation_as_of", "expectations_gap_status",
            "expectations_gap_packet_sha256", "expectations_gap_reference_date",
            "verified_post_reference_return_pct",
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
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction=verified_event_reaction("2026-07-29"),
            data_source_scope="KRX_OFFICIAL",
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
