#!/usr/bin/env python3
"""P8-10 Price Reflection regression.

`price_reflection/5` (CIO review round 5 on PR #212) closed the gap round 4
left open: round 4's evidence verification proved a hash-matching FILE
existed, never that it was actually evidence OF the claimed event/direction.
Confirmed reproducible: `data/2026-08-20/krx.json` (a plain KRX price
snapshot, zero event semantics) was cited as "evidence" of a POSITIVE event
on `329180.KS`, and the hash-only check accepted it -- any tracked file, of
any kind, could authorize an arbitrary claimed direction as long as its real
hash was supplied.

Round 5 requires every `event_reaction` citation to resolve to a real
committed file whose PARSED CONTENT is itself a structured, closed-
vocabulary Event Evidence Envelope (`decision/event_evidence.py`,
`event_evidence_envelope/1`) independently asserting the SAME subject/
event_at/direction/source_class the caller claims; requires that envelope's
own `captured_at` to be at-or-before the decision instant (PIT availability
of the EVIDENCE ITSELF, not just the return's price endpoints); retires a
caller-supplied, possibly freshly-fabricated-in-memory P8-09 packet dict in
favor of a REAL COMMITTED canonical wrapper record
(`expectations_gap_packet_ref`/`_sha256`); uses a full `event_at` timestamp
(not just a date) to decide the correct pre-event reference close; and makes
a SUPPLIED-but-corrupt citation RAISE `PriceReflectionError` instead of
silently downgrading to `UNKNOWN` (genuine absence of a citation is still a
soft `UNKNOWN`). This file follows the CIO's explicit instruction: the old
round-4 "misuse a price file as event evidence" test is REPURPOSED below to
prove that exact misuse is now REJECTED, not removed or left demonstrating
something that no longer reflects reality.
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
FIXTURES_DIR = ROOT / "test" / "fixtures" / "event_evidence"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("price_reflection", SOURCE)
CONTRACT = MODULE.load_contract()
EG = load_module("price_reflection_eg_fixture", EG_SOURCE)
EG_CONTRACT = EG.load_contract()

# ── real, independently-verifiable evidence (CIO round 4/5) ────────────────
# A REAL, already-committed repo file this file's return-computation tests
# still use as the PRICE source (unaffected by round 5 -- round 5 is about
# what counts as EVENT evidence, not the price-endpoint lookups round 4
# already made real). Its sha256 is recomputed HERE, from the file's real
# bytes -- see `_hash` below for the same treatment of the round-5 Event
# Evidence Envelope fixtures.
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

_PRICE_EVIDENCE = MODULE.PRICE_EVIDENCE
_END_DATE, _END_CLOSE = _PRICE_EVIDENCE.latest_real_close_at_or_before(
    REAL_EVIDENCE_SUBJECT, REAL_EVIDENCE_DECISION_DATE
)


def _hash(relpath: str) -> str:
    return hashlib.sha256((ROOT / relpath).read_bytes()).hexdigest()


def _fixture_ref(name: str) -> str:
    return f"test/fixtures/event_evidence/{name}"


# ── round-5 committed Event Evidence Envelope fixtures ──────────────────────
# Three REAL, committed, closed-vocabulary Event Evidence Envelope records
# (`test/fixtures/event_evidence/*.json`) -- never under `data/` (production
# evidence), always subject=329180.KS (not a restricted Pilot ticker),
# always `capture_kind=REGRESSION_FIXTURE`, and each explicitly self-labeled
# "TEST FIXTURE ONLY" in its own `citation.note`. `event_at` uses a genuine
# (non-midnight) time-of-day so `decision/event_evidence.py`'s
# `select_pre_event_reference_date` rolls the reference date back to the
# latest real, PIT-live trading date STRICTLY BEFORE the event's own
# calendar date (round 5, required item 4) -- so the expected returns below
# are derived from THAT rolled-back date, not the event's own calendar date
# (unlike round 4, which anchored directly to a bare date):
#   end anchor = latest real close PIT-live at/before 2026-08-20 -> 2026-08-19 (474000.0)
#   FULLY:     event_at=2026-07-30T09:30Z -> ref_date=2026-07-29, close=434000.0 -> +9.22%
#   PARTIALLY: event_at=2026-07-29T09:30Z -> ref_date=2026-07-28, close=451000.0 -> +5.10%
#   UNDER:     event_at=2026-07-20T09:30Z -> ref_date=2026-07-16, close=484000.0 -> -2.07% (disagrees with POSITIVE)
FULLY_FIXTURE = "regression_fixture_329180_fully.json"
FULLY_EVENT_AT = "2026-07-30T09:30:00Z"
PARTIALLY_FIXTURE = "regression_fixture_329180_partially.json"
PARTIALLY_EVENT_AT = "2026-07-29T09:30:00Z"
UNDER_FIXTURE = "regression_fixture_329180_under.json"
UNDER_EVENT_AT = "2026-07-20T09:30:00Z"
DATE_ONLY_FIXTURE = "regression_fixture_329180_date_only.json"
DATE_ONLY_EVENT_AT = "2026-07-30T00:00:00Z"
MALFORMED_FIXTURE = "malformed_missing_field.json"
EG_CANONICAL_RECORD_FIXTURE = "eg_canonical_record_329180.json"


def _real_verified_return(ref_date: str):
    """Independently recomputes the SAME return `price_reflection.py` will
    compute internally, anchored to the ALREADY-rolled-back reference date
    -- used only to assert the module's own output matches real, derivable
    arithmetic, never to shortcut verification."""
    start_close = _PRICE_EVIDENCE.real_close_on_date(
        REAL_EVIDENCE_SUBJECT, ref_date, REAL_EVIDENCE_DECISION_DATE
    )
    return (_END_CLOSE / start_close - 1) * 100


def verified_event_reaction(fixture_name: str, event_at: str, direction: str = "POSITIVE") -> dict:
    """A fully real, content-matched, PIT-verified `event_reaction` citing a
    committed Event Evidence Envelope fixture -- the ONLY shape of
    `event_reaction` that can unlock a confident `reflection_status`
    post-round-5. `source_class="GUIDANCE_CHANGE_EVENT"` matches every
    fixture committed under `test/fixtures/event_evidence/`."""
    ref = _fixture_ref(fixture_name)
    return {
        "event_at": event_at, "direction": direction, "reaction_magnitude_pct": "5",
        "source_class": "GUIDANCE_CHANGE_EVENT", "source_ref": ref, "source_sha256": _hash(ref),
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
            event_reaction=verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT),
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
            event_reaction={"event_at": "2026-08-10T09:30:00Z", "direction": "POSITIVE", "reaction_magnitude_pct": "5"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertEqual(pr["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")
        # The specific old (wrong) combination must never appear.
        self.assertFalse(pr["price_state"] == "UNKNOWN" and pr["reflection_status"] == "FULLY_REFLECTED")

    # ── CIO round 4, exact reproduction case (verbatim, still correctly rejected) ──
    def test_cio_round4_reproduction_case_fabricated_source_and_hash_still_rejected(self):
        """Verbatim repro (HEAD 323f03b): source_ref="MADE-UP",
        source_sha256="a"*64. Round 5 now RAISES for this (a supplied
        citation that doesn't even resolve to a real file is corruption,
        not absence -- required item 5), rather than round 4's soft
        downgrade to UNKNOWN. Either way, it must never unlock a confident
        verdict."""
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_EVIDENCE_INVALID"):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction={
                    "event_at": "2026-08-20T00:00:00Z", "direction": "POSITIVE",
                    "source_class": "GUIDANCE_CHANGE_EVENT",
                    "source_ref": "MADE-UP", "source_sha256": "a" * 64,
                },
                data_source_scope="KRX_OFFICIAL",
            ))

    # ── CIO round 5, exact reproduction case: a hash-matching PRICE FILE is not event evidence ──
    def test_cio_round5_reproduction_case_price_file_misused_as_event_evidence_is_rejected(self):
        """Verbatim repro (round-4 HEAD): `data/2026-08-20/krx.json` (a real,
        hash-matching, but plain KRX PRICE snapshot with zero event
        semantics) was cited as "evidence" of a POSITIVE event on
        `329180.KS`, and round 4's hash-only check accepted it. This is
        the EXACT round-4 regression test this file used to contain under
        the name `test_cio_round4_real_verified_event_unlocks_fully_
        reflected_with_exact_computed_return`-style fixtures, repurposed
        (not removed) to prove that exact misuse is now REJECTED: the price
        file has no `schema_version`/`event_at`/`direction`/... fields at
        all, so it can never structurally satisfy the Event Evidence
        Envelope schema, regardless of its real, matching hash."""
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError,
            "EVENT_REACTION_EVIDENCE_INVALID:EVENT_EVIDENCE_SOURCE_NOT_AN_EVENT_ENVELOPE",
        ):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction={
                    "event_at": "2026-07-29T09:30:00Z", "direction": "POSITIVE",
                    "source_class": "GUIDANCE_CHANGE_EVENT",
                    "source_ref": REAL_EVIDENCE_SOURCE_REF, "source_sha256": REAL_EVIDENCE_SHA256,
                },
                data_source_scope="KRX_OFFICIAL",
            ))

    def test_cio_round5_content_mismatch_is_rejected(self):
        # The fixture genuinely asserts direction=POSITIVE -- claiming
        # NEGATIVE against the SAME real, hash-verified file must raise a
        # distinguishable claim-mismatch error, not silently accept it.
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_EVIDENCE_CLAIM_MISMATCH:direction"):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction=dict(verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT), direction="NEGATIVE"),
                data_source_scope="KRX_OFFICIAL",
            ))
        # Same for subject: a caller citing this fixture for a DIFFERENT
        # subject must also be rejected -- the envelope only ever speaks
        # for 329180.KS.
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_EVIDENCE_CLAIM_MISMATCH:subject"):
            MODULE.build_packet(**base_kwargs(
                subject="298040.KS", decision_date=REAL_EVIDENCE_DECISION_DATE, generated_at=REAL_EVIDENCE_GENERATED_AT,
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"}, relative_strength={"vs_market": "3"},
                event_reaction=verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT),
                data_source_scope="KRX_OFFICIAL",
            ))

    def test_cio_round5_malformed_envelope_raises(self):
        # A real, hash-matching file that IS present but is missing
        # required Event Evidence Envelope fields (`captured_at`/
        # `citation`) must be rejected as not-a-valid-envelope, not
        # silently treated as absent evidence.
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError,
            "EVENT_REACTION_EVIDENCE_INVALID:EVENT_EVIDENCE_SOURCE_NOT_AN_EVENT_ENVELOPE",
        ):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction=verified_event_reaction(MALFORMED_FIXTURE, FULLY_EVENT_AT),
                data_source_scope="KRX_OFFICIAL",
            ))

    def test_cio_round5_wrong_hash_for_a_real_envelope_does_not_unlock_reflection(self):
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "EVENT_REACTION_EVIDENCE_INVALID:EVENT_EVIDENCE_SOURCE_HASH_MISMATCH"
        ):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction={
                    "event_at": FULLY_EVENT_AT, "direction": "POSITIVE",
                    "source_class": "GUIDANCE_CHANGE_EVENT",
                    "source_ref": _fixture_ref(FULLY_FIXTURE), "source_sha256": "f" * 64,  # real file, WRONG hash
                },
                data_source_scope="KRX_OFFICIAL",
            ))

    def test_cio_round5_nonexistent_file_does_not_unlock_reflection(self):
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "EVENT_REACTION_EVIDENCE_INVALID:EVENT_EVIDENCE_SOURCE_FILE_NOT_FOUND"
        ):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction={
                    "event_at": FULLY_EVENT_AT, "direction": "POSITIVE",
                    "source_class": "GUIDANCE_CHANGE_EVENT",
                    "source_ref": _fixture_ref("does_not_exist.json"), "source_sha256": REAL_EVIDENCE_SHA256,
                },
                data_source_scope="KRX_OFFICIAL",
            ))

    def test_cio_round5_path_traversal_source_ref_is_rejected(self):
        traversal_ref = "a/../../../../../../etc/passwd"
        self.assertIsNotNone(MODULE.SOURCE_REF_RE.fullmatch(traversal_ref))  # sanity: format alone would pass
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError,
            "EVENT_REACTION_EVIDENCE_INVALID:EVENT_EVIDENCE_SOURCE_REF_ESCAPES_REPO_ROOT",
        ):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction={
                    "event_at": FULLY_EVENT_AT, "direction": "POSITIVE",
                    "source_class": "GUIDANCE_CHANGE_EVENT",
                    "source_ref": traversal_ref, "source_sha256": REAL_EVIDENCE_SHA256,
                },
                data_source_scope="KRX_OFFICIAL",
            ))

    # ── CIO round 5, required item 2: PIT availability of the EVIDENCE ITSELF ──
    def test_cio_round5_envelope_not_yet_available_as_of_decision_is_rejected(self):
        """Required regression (b): a file that EXISTS TODAY (committed
        `captured_at=2026-08-14`) but wasn't available as of an earlier
        historical `decision_date` (2026-08-10) must be rejected -- not
        merely "file exists in the current checkout", but "was this
        genuinely knowable as of the decision instant being evaluated"."""
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError,
            "EVENT_REACTION_EVIDENCE_INVALID:EVENT_EVIDENCE_NOT_YET_AVAILABLE_AS_OF_DECISION",
        ):
            MODULE.build_packet(
                subject=REAL_EVIDENCE_SUBJECT, decision_date="2026-08-10", generated_at="2026-08-10T00:00:00Z",
                contract=CONTRACT,
                price_as_of="2026-08-07T21:58:30Z",
                recent_return_windows={"1m": "4"}, relative_strength={"vs_market": "3"},
                event_reaction=verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT),
                data_source_scope="KRX_OFFICIAL",
            )

    # ── CIO round 5, required item 4: event_at timing ────────────────────
    def test_cio_round5_date_only_event_at_keeps_timing_not_computable(self):
        # A genuinely real, hash-verified, content-matched, PIT-available
        # envelope whose event_at is the 00:00:00Z "date only" sentinel
        # still cannot unlock a confident verdict -- this repo's real price
        # evidence is daily-granularity only, so no genuine pre-market/
        # intraday/after-hours placement can be established from a bare
        # date. Must stay UNKNOWN (soft), never raise.
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction(DATE_ONLY_FIXTURE, DATE_ONLY_EVENT_AT),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
        self.assertIn("REFERENCE_POINT_PRESENT_BUT_NOT_RECONSTRUCTABLE_FROM_REAL_EVIDENCE", pr["reasons"])

    # ── CIO round 5: real, fully-verified Event Evidence Envelope DOES unlock reflection ──
    def test_cio_round5_real_verified_event_unlocks_fully_reflected_with_exact_computed_return(self):
        expected_return = _real_verified_return("2026-07-29")  # rolled-back reference date, not event_at's own date
        strong_threshold = Decimal(CONTRACT["classification_thresholds"]["strong_momentum_min_pct"])
        self.assertGreaterEqual(abs(expected_return), strong_threshold)
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertEqual(pr["data_state"], "VALID")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], str(expected_return))
        self.assertEqual(pr["event_reaction"]["event_at"], FULLY_EVENT_AT)
        self.assertEqual(pr["event_reaction"]["source_class"], "GUIDANCE_CHANGE_EVENT")
        self.assertTrue(any(f"verified_return_pct:{expected_return}" in r for r in pr["reasons"]))

    def test_cio_round5_real_verified_event_unlocks_partially_reflected_with_exact_computed_return(self):
        expected_return = _real_verified_return("2026-07-28")
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction(PARTIALLY_FIXTURE, PARTIALLY_EVENT_AT),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "PARTIALLY_REFLECTED")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], str(expected_return))

    def test_cio_round5_real_verified_event_with_disagreeing_move_is_under_reflected(self):
        expected_return = _real_verified_return("2026-07-16")
        self.assertLess(expected_return, 0)  # sanity: real move DISAGREES with the claimed POSITIVE direction
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction(UNDER_FIXTURE, UNDER_EVENT_AT),
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "UNDER_REFLECTED")
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], str(expected_return))

    # ── CIO round 5, required item 3: real committed P8-09 canonical record ──
    def test_cio_round5_real_committed_eg_canonical_record_unlocks_reflection(self):
        ref = _fixture_ref(EG_CANONICAL_RECORD_FIXTURE)
        expected_return = _real_verified_return("2026-07-29")
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            reflection_reference={
                "expectations_gap_packet_ref": ref, "expectations_gap_packet_sha256": _hash(ref),
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertEqual(pr["reflection_reference"]["expectations_gap_reference_date"], "2026-07-29")
        self.assertEqual(pr["reflection_reference"]["verified_post_reference_return_pct"], str(expected_return))
        self.assertEqual(pr["event_reaction"]["verified_post_event_return_pct"], "UNKNOWN")  # source discrimination

    def test_cio_round5_eg_canonical_record_backdated_decision_date_is_rejected(self):
        """Required regression (c): the SAME real, committed, hash-verified
        canonical record (captured_at=2026-08-14) cannot be used to unlock
        reflection for a decision_date BEFORE it was ever committed
        (2026-08-01) -- exactly the "freshly-fabricated/backdated P8-09
        packet" defect, closed structurally: this record was genuinely
        committed on 2026-08-14, so it can never legitimately back-date
        earlier than that, no matter what `decision_date` value a caller
        supplies."""
        ref = _fixture_ref(EG_CANONICAL_RECORD_FIXTURE)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError,
            "REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_INVALID:EG_CANONICAL_RECORD_NOT_YET_AVAILABLE_AS_OF_DECISION",
        ):
            MODULE.build_packet(
                subject=REAL_EVIDENCE_SUBJECT, decision_date="2026-08-01", generated_at="2026-08-01T00:00:00Z",
                contract=CONTRACT,
                price_as_of="2026-07-31T21:58:30Z",
                recent_return_windows={"1m": "4"}, relative_strength={"vs_market": "3"},
                reflection_reference={
                    "expectations_gap_packet_ref": ref, "expectations_gap_packet_sha256": _hash(ref),
                },
                data_source_scope="KRX_OFFICIAL",
            )

    def test_cio_round5_freshly_fabricated_in_memory_eg_packet_is_no_longer_an_accepted_field(self):
        """Required item 3, the in-memory half of the defect: a P8-09
        packet built fresh RIGHT NOW (however internally hash-consistent)
        is not even a structurally acceptable `reflection_reference` shape
        any more -- `expectations_gap_packet` (the raw dict field) is
        retired; only `expectations_gap_packet_ref`/`_sha256` (a real
        committed file this module reads itself) are accepted."""
        fake_packet = eg_packet(
            subject=REAL_EVIDENCE_SUBJECT, decision_date="2026-07-29", generated_at="2026-07-29T00:00:00Z",
        )
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "REFLECTION_REFERENCE_FIELDS_MISMATCH"):
            MODULE.build_packet(**real_evidence_kwargs(
                reflection_reference={"expectations_gap_packet": fake_packet},
            ))

    def test_cio_round5_event_reaction_preferred_over_expectations_gap_when_both_satisfiable(self):
        eg_ref = _fixture_ref(EG_CANONICAL_RECORD_FIXTURE)  # would independently give FULLY_REFLECTED too
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "4"},
            relative_strength={"vs_market": "3"},
            event_reaction=verified_event_reaction(PARTIALLY_FIXTURE, PARTIALLY_EVENT_AT),  # PARTIALLY_REFLECTED anchor
            reflection_reference={
                "expectations_gap_packet_ref": eg_ref, "expectations_gap_packet_sha256": _hash(eg_ref),
            },
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "PARTIALLY_REFLECTED")
        self.assertIn("reflection_basis_source:EVENT_REACTION", pr["reasons"])
        self.assertNotEqual(pr["event_reaction"]["verified_post_event_return_pct"], "UNKNOWN")
        self.assertEqual(pr["reflection_reference"]["verified_post_reference_return_pct"], "UNKNOWN")

    # ── Required item 1: bare direction/status is not a real reference ──
    def test_bare_event_direction_without_evidence_lineage_does_not_unlock_reflection(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction={"event_at": "2026-08-10T09:30:00Z", "direction": "POSITIVE", "reaction_magnitude_pct": "5"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")  # generic momentum still computable
        self.assertEqual(pr["reflection_status"], "UNKNOWN")  # but no lineage -> no verdict
        self.assertIn(
            "REFERENCE_POINT_PRESENT_BUT_NOT_RECONSTRUCTABLE_FROM_REAL_EVIDENCE",
            pr["reasons"],
        )

    def test_bare_expectations_gap_status_string_is_no_longer_an_accepted_field(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "REFLECTION_REFERENCE_FIELDS_MISMATCH"):
            MODULE.build_packet(**base_kwargs(
                reflection_reference={"expectations_gap_status": "POSITIVE"},
            ))

    def test_expectations_gap_packet_ref_requires_sha256_and_vice_versa(self):
        ref = _fixture_ref(EG_CANONICAL_RECORD_FIXTURE)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_REF_AND_SHA256_BOTH_REQUIRED"
        ):
            MODULE.build_packet(**base_kwargs(reflection_reference={"expectations_gap_packet_ref": ref}))
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_REF_AND_SHA256_BOTH_REQUIRED"
        ):
            MODULE.build_packet(**base_kwargs(reflection_reference={"expectations_gap_packet_sha256": _hash(ref)}))

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

    # ── Required item 3 (round 3): price_state=UNKNOWN blocks any reflection verdict ──
    def test_price_state_unknown_forces_reflection_unknown_even_with_full_lineage_and_verified_return(self):
        packet = MODULE.build_packet(**real_evidence_kwargs(
            price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
            recent_return_windows={"1m": "10"},
            event_reaction=verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT),
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
            event_reaction=verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT),
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

    def test_allowed_event_source_class_vocabulary_matches_event_evidence_module(self):
        self.assertEqual(
            sorted(CONTRACT["allowed_event_source_class"]),
            sorted(MODULE.EVENT_EVIDENCE.ALLOWED_SOURCE_CLASS),
        )

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
                "event_at": "2026-08-01T00:00:00Z", "direction": "MOONSHOT", "reaction_magnitude_pct": "5",
            }))

    def test_event_reaction_source_class_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_SOURCE_CLASS_INVALID"):
            MODULE.build_packet(**base_kwargs(event_reaction={"source_class": "MADE_UP_CLASS"}))

    def test_event_reaction_future_event_at_rejected(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_EVENT_AT_IN_FUTURE"):
            MODULE.build_packet(**base_kwargs(event_reaction={
                "event_at": "2026-08-25T00:00:00Z", "direction": "POSITIVE", "reaction_magnitude_pct": "5",
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
        self.assertEqual(packet["schema_version"], "price_reflection_packet/5")
        self.assertEqual(packet["contract_version"], "price_reflection/5")
        self.assertEqual(set(packet["price_reflection"]), {
            "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
            "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
            "reflection_reference", "valuation_context", "reasons", "missing_inputs",
            "data_source_scope",
        })
        self.assertEqual(set(packet["price_reflection"]["event_reaction"]), {
            "event_at", "direction", "reaction_magnitude_pct", "source_class",
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
            event_reaction=verified_event_reaction(FULLY_FIXTURE, FULLY_EVENT_AT),
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
