#!/usr/bin/env python3
"""P8-10 Price Reflection regression.

`price_reflection/6` (CIO review round 6 on PR #212) closed the gap round 5
left open: round 5 built a real, structured, content-verified Event
Evidence Envelope, but (1) `REGRESSION_FIXTURE` was still an accepted
production `capture_kind` value, so the committed test fixtures could drive
a real `build_packet()` call to a non-`UNKNOWN` verdict; (2) `captured_at`
was still a self-declared field the verifier trusted outright, which is the
exact retroactive-creation problem this workstream exists to prevent; (3)
`citation` was an unconstrained dict that only needed a free note, never a
real primary-source document.

Round 6 makes `REGRESSION_FIXTURE` an illegal envelope value AND makes the
real, operational `verify_event_reaction_claim`/`verify_expectations_gap_
canonical_record` functions hard-refuse any citation located under this
repo's `test/` directory at all -- a structural, path-based production/test
boundary, not a convention. It also replaces the self-declared `captured_at`
PIT gate with this repo's REAL git history (the earliest commit that added
the cited file), and requires a closed citation schema resolving/hashing/
verifying a real raw primary-source document.

Because of this, NO test fixture can ever again reach a confident verdict
through the real `decision.price_reflection.build_packet()` entry point --
this file's regressions PROVE that (every attempt, including a "disguised"
envelope that is otherwise perfectly well-formed, correctly rejects). To
still exercise the REAL classifier arithmetic (return computation from real
price data, real threshold classification -- CIO's own words: "positive
classifier mechanics may be unit-tested below the production evidence
boundary"), this file uses `mocked_event_evidence_verification()`/
`mocked_eg_canonical_verification()` -- explicit, test-only context managers
that patch ONLY this file's own loaded `MODULE.EVENT_EVIDENCE` function
references for the lifetime of a single `with` block, never touching the
real, unmocked module any other caller (including every regression in this
same file that tests production rejection) uses.
"""

import ast
import contextlib
import copy
import datetime as _dt
from decimal import Decimal
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
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
EVENT_EVIDENCE = MODULE.EVENT_EVIDENCE

# ── real, independently-verifiable PRICE evidence (CIO round 4/5) ──────────
# Unaffected by round 6 -- round 6 is about EVENT/CITATION evidence, not the
# real close-price lookups round 4 already made real. Its sha256 is
# recomputed HERE, from the file's real bytes.
REAL_EVIDENCE_SOURCE_REF = "data/2026-08-20/krx.json"
REAL_EVIDENCE_PATH = ROOT / REAL_EVIDENCE_SOURCE_REF
REAL_EVIDENCE_SHA256 = hashlib.sha256(REAL_EVIDENCE_PATH.read_bytes()).hexdigest()

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


# ── committed test-only fixtures (CIO round 5/6) ────────────────────────────
# All under test/fixtures/event_evidence/, never under data/. Round 5's three
# REGRESSION_FIXTURE-kind envelope files and the EG canonical record fixture
# now serve ONLY as round-6 REJECTION proof -- REGRESSION_FIXTURE is no
# longer a legal capture_kind value, and any source_ref under test/ is
# refused by the real production functions regardless of content.
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

# CIO round 6: a "disguised" envelope -- genuinely `capture_kind=
# LIVE_OFFICIAL_CAPTURE`, a complete, real, hash-verified closed-schema
# citation to a real committed raw source document, everything otherwise
# well-formed -- that MUST still be rejected purely because of its location
# under test/. Subject is deliberately NOT a real/plausible ticker format
# (unlike round 5's use of the real listed `329180.KS`, which the CIO
# correctly flagged as an incidental, not structural, safeguard).
TESTONLY_SUBJECT = "TESTONLY-EVENT-EVIDENCE-000"
TESTONLY_LIVE_FIXTURE = "live_official_capture_testonly_000.json"
TESTONLY_LIVE_EVENT_AT = "2026-08-10T09:30:00Z"
TESTONLY_RAW_SOURCE = "raw_source_testonly_000.json"
# CIO round 7, required item 4 (schema hardened round 8, defect 2): a
# SEPARATE raw source document whose own ratified `official_direction_
# field` maps to NEGATIVE (a "revenue decline"-style disclosure, the CIO's
# own example) -- used to prove a claimed `direction=POSITIVE` can never be
# backed by content that is itself genuinely, ratified-mapped NEGATIVE.
TESTONLY_RAW_SOURCE_NEGATIVE = "raw_source_testonly_000_negative.json"
# CIO round 8, defect 2: a raw source establishing direction via the OTHER
# closed route (RATIFIED_DERIVATION, a numeric rule) instead of a
# structured official field -- proves BOTH routes are genuinely usable, not
# just one.
TESTONLY_RAW_SOURCE_DERIVATION = "raw_source_testonly_000_derivation.json"
# CIO round 8, defect 2: a raw source carrying only the RETIRED round-7
# `observed_direction` shape -- neither closed route's required structure
# is present -- proving a bare human-curated claim alone is never enough.
TESTONLY_RAW_SOURCE_HUMAN_CURATED = "raw_source_testonly_000_human_curated.json"
# CIO round 8, defect 2: matches ONLY what the two new fixtures above
# declare; this module's own real RATIFIED_OFFICIAL_DIRECTION_FIELDS/
# RATIFIED_DIRECTION_RULES tables start and stay EMPTY -- these entries are
# overlaid transiently via mocked_ratified_direction_tables() and never
# committed to the module's own global state.
TESTONLY_RATIFIED_OFFICIAL_FIELDS = {
    ("GUIDANCE_CHANGE_EVENT", "guidance_flag", "RAISED"): "POSITIVE",
    ("GUIDANCE_CHANGE_EVENT", "guidance_flag", "LOWERED"): "NEGATIVE",
}
TESTONLY_RATIFIED_DERIVATION_RULES = {
    ("TESTONLY_REVENUE_YOY_SIGN", "1"): {
        "required_inputs": ("revenue_yoy_pct",),
        "derive": lambda inputs: "POSITIVE" if inputs["revenue_yoy_pct"] > 0 else "NEGATIVE",
    },
}
TESTONLY_OBSERVED_FACT = (
    "TESTONLY-EVENT-EVIDENCE-000 reports a fabricated positive test disclosure "
    "used only to exercise citation verification."
)
# Deliberately backdated relative to this file's real git first-commit date
# (this whole fixture set was added in the round-6 PR, so its real
# first-availability is whenever that commit lands) -- used to prove the
# captured_at/first-availability gates independently of any hardcoded date.
TESTONLY_DECLARED_CAPTURED_AT = "2026-08-01T00:00:00Z"


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
    """The CLAIM shape `event_reaction` needs. Under the REAL, unmocked
    `decision/event_evidence.py`, citing any file under `test/` (as every
    fixture here does) is refused outright regardless of content -- so this
    shape is only ever used successfully inside `mocked_event_evidence_
    verification()` below, which replaces the verification function
    entirely and does not care what `source_ref` points to."""
    ref = _fixture_ref(fixture_name)
    return {
        "event_at": event_at, "direction": direction, "reaction_magnitude_pct": "5",
        "source_class": "GUIDANCE_CHANGE_EVENT", "source_ref": ref, "source_sha256": _hash(ref),
    }


@contextlib.contextmanager
def mocked_event_evidence_verification():
    """CIO round 6: "positive classifier mechanics may be unit-tested below
    the production evidence boundary." Patches ONLY `MODULE.EVENT_EVIDENCE.
    verify_event_reaction_claim` (this test file's own loaded
    `decision/price_reflection.py` instance's reference to
    `decision/event_evidence.py`) to return a canned, structurally-valid
    verification result -- for the lifetime of a single `with` block only.
    Does NOT touch the real `decision/event_evidence.py` module object, its
    `ALLOWED_CAPTURE_KIND`, its `_resolve_repo_file`/test-root guard, or any
    other test's or caller's view of it; every regression in this file that
    proves production REJECTION runs with this function completely
    unpatched. `_compute_verified_return`/`select_pre_event_reference_date`
    are never touched by this mock -- the return figure asserted by tests
    using this context manager is always the REAL, independently
    recomputable number derived from real committed price data."""
    original = MODULE.EVENT_EVIDENCE.verify_event_reaction_claim

    def _fake(*, subject, event_at, direction, source_class, source_ref, source_sha256, decision_at):
        return {
            "capture_kind": "LIVE_OFFICIAL_CAPTURE",
            "first_authoritative_seen_at": "2026-08-01T00:00:00Z",
            "raw_source_ref": source_ref,
            "raw_source_sha256": source_sha256,
            "published_at": event_at,
            "locator": "TEST-ONLY-MOCKED-LOCATOR-BELOW-PRODUCTION-BOUNDARY",
        }

    MODULE.EVENT_EVIDENCE.verify_event_reaction_claim = _fake
    try:
        yield
    finally:
        MODULE.EVENT_EVIDENCE.verify_event_reaction_claim = original


@contextlib.contextmanager
def mocked_eg_canonical_verification(reference_decision_date: str = "2026-07-29"):
    """The `reflection_reference` analogue of `mocked_event_evidence_
    verification()` above -- same rationale, same scope, same restore-on-
    exit discipline. Builds the returned packet via the REAL `EG.build_
    packet()` (still genuinely hash-consistent, still real classification
    logic), anchored to `reference_decision_date` (a real, earlier
    reference point so `_compute_verified_return` has a genuine forward
    date gap to measure, exactly matching the real committed EG canonical
    record fixture's own `2026-07-29` reference date) -- only the
    CITATION/PROVENANCE verification step is mocked."""
    original = MODULE.EVENT_EVIDENCE.verify_expectations_gap_canonical_record

    def _fake(*, expectations_gap_module, eg_contract, subject, decision_date, decision_at, packet_ref, packet_sha256):
        packet = expectations_gap_module.build_packet({
            "subject": subject, "decision_date": reference_decision_date,
            "generated_at": f"{reference_decision_date}T00:00:00Z",
            "guidance_changes": {"direction": "POSITIVE", "evidence_note": "real guidance evidence"},
        }, eg_contract)
        result = dict(packet)
        result["first_authoritative_seen_at"] = "2026-08-01T00:00:00Z"
        return result

    MODULE.EVENT_EVIDENCE.verify_expectations_gap_canonical_record = _fake
    try:
        yield
    finally:
        MODULE.EVENT_EVIDENCE.verify_expectations_gap_canonical_record = original


@contextlib.contextmanager
def mocked_ratified_direction_tables(**kwargs):
    """Thin wrapper around `decision/event_evidence.py`'s own `mocked_
    ratified_direction_tables()`, always applied to THIS test file's own
    loaded `EVENT_EVIDENCE` module instance -- never the real module object
    any other caller sees."""
    with EVENT_EVIDENCE.mocked_ratified_direction_tables(EVENT_EVIDENCE, **kwargs):
        yield


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
        with mocked_event_evidence_verification():
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
        self.assertFalse(pr["price_state"] == "UNKNOWN" and pr["reflection_status"] == "FULLY_REFLECTED")

    # ── CIO round 4, exact reproduction case (still correctly rejected) ──
    def test_cio_round4_reproduction_case_fabricated_source_and_hash_still_rejected(self):
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

    # ── CIO round 5, exact reproduction case (still correctly rejected) ──
    def test_cio_round5_reproduction_case_price_file_misused_as_event_evidence_is_rejected(self):
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

    # ══════════════════════ CIO round 6 regressions ════════════════════════

    def test_cio_round6_regression_fixture_is_not_a_legal_capture_kind_value(self):
        self.assertEqual(EVENT_EVIDENCE.ALLOWED_CAPTURE_KIND, ("LIVE_OFFICIAL_CAPTURE",))
        self.assertNotIn("REGRESSION_FIXTURE", EVENT_EVIDENCE.ALLOWED_CAPTURE_KIND)

    def test_cio_round6_every_committed_regression_fixture_is_rejected_by_the_real_production_path(self):
        """Required regression (a): "production build rejects every
        REGRESSION_FIXTURE." Cites all 3 real, committed Event Evidence
        Envelope fixtures (still declaring `capture_kind=REGRESSION_
        FIXTURE`, round 5) plus the EG canonical record fixture through the
        REAL, unmocked `MODULE.build_packet()` -- every single one must
        raise, none may silently downgrade to UNKNOWN via any other path
        (they must never even be "considered", let alone produce a
        confident verdict)."""
        for fixture, event_at in (
            (FULLY_FIXTURE, FULLY_EVENT_AT),
            (PARTIALLY_FIXTURE, PARTIALLY_EVENT_AT),
            (UNDER_FIXTURE, UNDER_EVENT_AT),
        ):
            with self.subTest(fixture=fixture):
                ref = _fixture_ref(fixture)
                with self.assertRaisesRegex(
                    MODULE.PriceReflectionError,
                    "EVENT_EVIDENCE_SOURCE_REF_UNDER_TEST_ROOT_FORBIDDEN_IN_PRODUCTION",
                ):
                    MODULE.build_packet(**real_evidence_kwargs(
                        price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                        recent_return_windows={"1m": "4"},
                        relative_strength={"vs_market": "3"},
                        event_reaction={
                            "event_at": event_at, "direction": "POSITIVE",
                            "source_class": "GUIDANCE_CHANGE_EVENT",
                            "source_ref": ref, "source_sha256": _hash(ref),
                        },
                        data_source_scope="KRX_OFFICIAL",
                    ))
        eg_ref = _fixture_ref(EG_CANONICAL_RECORD_FIXTURE)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError,
            "EVENT_EVIDENCE_SOURCE_REF_UNDER_TEST_ROOT_FORBIDDEN_IN_PRODUCTION",
        ):
            MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                reflection_reference={
                    "expectations_gap_packet_ref": eg_ref, "expectations_gap_packet_sha256": _hash(eg_ref),
                },
                data_source_scope="KRX_OFFICIAL",
            ))

    def test_cio_round6_disguised_live_official_capture_envelope_under_test_root_still_rejected(self):
        """Required confirmation (a), the "smuggle by disguising/relabeling"
        case: this envelope is genuinely `capture_kind=LIVE_OFFICIAL_
        CAPTURE`, has a complete, real, hash-verified closed-schema citation
        pointing at a real committed raw source document -- structurally
        indistinguishable from a legitimate production envelope in every
        way EXCEPT its location. It must still be rejected, proving the
        production/test boundary is path-based, not merely a `capture_kind`
        label check that a relabeling attempt could defeat."""
        ref = _fixture_ref(TESTONLY_LIVE_FIXTURE)
        # Sanity: this envelope really would pass the capture_kind/citation
        # schema checks below the production boundary -- see
        # test_cio_round6_below_boundary_citation_mechanics below. Its
        # rejection here is caused SOLELY by the test-root path guard.
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError,
            "EVENT_EVIDENCE_SOURCE_REF_UNDER_TEST_ROOT_FORBIDDEN_IN_PRODUCTION",
        ):
            MODULE.build_packet(**base_kwargs(
                subject=TESTONLY_SUBJECT, decision_date="2026-08-20", generated_at="2026-08-20T00:00:00Z",
                price_as_of="2026-08-19T00:00:00Z",
                recent_return_windows={"1m": "4"}, relative_strength={"vs_market": "3"},
                event_reaction={
                    "event_at": TESTONLY_LIVE_EVENT_AT, "direction": "POSITIVE",
                    "source_class": "GUIDANCE_CHANGE_EVENT",
                    "source_ref": ref, "source_sha256": _hash(ref),
                },
                data_source_scope="UNKNOWN",
            ))

    def test_cio_round6_below_boundary_citation_schema_and_raw_source_verification(self):
        """Required confirmation (c): the closed citation contract now
        requires and verifies a real primary-source document. Exercised
        directly against `_verify_raw_source_citation` (below the
        production boundary, per the CIO's explicit allowance) since the
        real production path is, by design, never reachable with a `test/`
        citation at all (see the disguised-envelope test above)."""
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        raw_path = FIXTURES_DIR / TESTONLY_RAW_SOURCE
        first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(raw_path)
        self.assertIsNotNone(first_seen, "fixture must be committed for this regression to be meaningful")
        decision_at = first_seen + _dt.timedelta(days=1)
        citation = dict(envelope["citation"])
        # genuinely consistent, re-derived (round 8: both published_at and
        # captured_at are checked, captured_at is the one that touches git).
        citation["published_at"] = first_seen.strftime("%Y-%m-%dT%H:%M:%SZ")
        citation["captured_at"] = first_seen.strftime("%Y-%m-%dT%H:%M:%SZ")

        with mocked_ratified_direction_tables(official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS):
            lineage = EVENT_EVIDENCE._verify_raw_source_citation(
                citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
            )
            self.assertEqual(lineage["raw_source_ref"], _fixture_ref(TESTONLY_RAW_SOURCE))

            # note-only citation (missing every required field) fails.
            with self.assertRaisesRegex(EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_CITATION_FIELDS_MISMATCH"):
                EVENT_EVIDENCE._verify_raw_source_citation(
                    {"note": "just a free-text assertion, nothing else"}, "POSITIVE", "GUIDANCE_CHANGE_EVENT",
                    decision_at, forbid_test_root=False,
                )

            # arbitrary real, hash-matching, but semantically unrelated raw file fails --
            # it's real JSON, but has no `official_direction_field` at all (round 8,
            # defect 2: co-presence of a hash-verified file is not enough).
            arbitrary_citation = dict(citation)
            arbitrary_citation["raw_source_ref"] = REAL_EVIDENCE_SOURCE_REF
            arbitrary_citation["raw_source_sha256"] = REAL_EVIDENCE_SHA256
            with self.assertRaisesRegex(
                EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_CITATION_OFFICIAL_DIRECTION_FIELD_INVALID"
            ):
                EVENT_EVIDENCE._verify_raw_source_citation(
                    arbitrary_citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
                )

            # wrong hash for the real raw source file fails.
            wrong_hash_citation = dict(citation)
            wrong_hash_citation["raw_source_sha256"] = "f" * 64
            with self.assertRaisesRegex(EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_SOURCE_HASH_MISMATCH"):
                EVENT_EVIDENCE._verify_raw_source_citation(
                    wrong_hash_citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
                )

    # ══════════════════════ CIO round 7 regressions ════════════════════════

    def test_cio_round7_content_addressed_first_seen_reflects_current_content_not_original_path_add(self):
        """Required item 1: "editing an old file today must not let it
        inherit the old file's original first-seen date." `raw_source_
        testonly_000.json`'s PATH was first added in the round-6 commit,
        but its CONTENT was edited in round 7 and again in round 8. The
        exact-content first-seen must reflect the LATEST edit, strictly
        LATER than the path's own original first-add commit -- computed
        here independently (never hardcoded) via the same git primitive
        the retired round-6 path-level function used, purely for
        comparison."""
        path = FIXTURES_DIR / TESTONLY_RAW_SOURCE
        content_first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(path)
        self.assertIsNotNone(content_first_seen, "fixture must be committed for this regression to be meaningful")

        path_first_add_log = subprocess.run(
            ["git", "log", "--follow", "--diff-filter=A", "--format=%cI", "--", str(path)],
            cwd=str(ROOT), capture_output=True, text=True, check=True,
        )
        path_first_add_lines = [ln.strip() for ln in path_first_add_log.stdout.splitlines() if ln.strip()]
        self.assertTrue(path_first_add_lines)
        path_first_add = EVENT_EVIDENCE._parse_git_iso(path_first_add_lines[-1])
        self.assertIsNotNone(path_first_add)

        self.assertGreater(
            content_first_seen, path_first_add,
            "editing a file's content must produce a NEW, strictly-later first-seen date -- "
            "otherwise the edited content incorrectly inherited the original path's first-seen date",
        )

    def test_cio_round7_editing_old_path_content_today_does_not_pass_as_old_evidence(self):
        """The end-to-end version of item 1: verify the exact CURRENT bytes
        of the raw source cannot be treated as available as of a
        `decision_at` BEFORE the edit actually landed -- i.e. the
        content-addressed check genuinely blocks "edit today, claim it was
        old evidence", not just in isolation but through the real
        `_verify_first_availability` gate."""
        path = FIXTURES_DIR / TESTONLY_RAW_SOURCE
        content_first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(path)
        self.assertIsNotNone(content_first_seen)
        decision_before_edit = content_first_seen - _dt.timedelta(days=30)
        with self.assertRaisesRegex(
            EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_NOT_YET_AVAILABLE_AS_OF_DECISION"
        ):
            EVENT_EVIDENCE._verify_first_availability(
                path, content_first_seen, decision_before_edit, "EVENT_EVIDENCE",
            )

    def test_cio_round7_raw_source_has_its_own_git_availability_gate(self):
        """Required item 2: "the raw source has no git-availability check
        at all" -- closed. `_verify_raw_source_citation` runs
        `_verify_first_availability` on the raw source file itself, using
        the raw source's OWN `captured_at` (round 8, defect 1: NOT
        `published_at` any more -- see module docstring), completely
        independent of the envelope's own `captured_at` check. A
        `decision_at` BEFORE the raw source's real first-availability is
        rejected here even though the envelope's own gate would have
        nothing to say about it."""
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        raw_path = FIXTURES_DIR / TESTONLY_RAW_SOURCE
        first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(raw_path)
        self.assertIsNotNone(first_seen)
        citation = dict(envelope["citation"])
        citation["published_at"] = TESTONLY_DECLARED_CAPTURED_AT
        citation["captured_at"] = TESTONLY_DECLARED_CAPTURED_AT  # 2026-08-01, always earlier than first_seen
        decision_at = first_seen - _dt.timedelta(days=5)
        with mocked_ratified_direction_tables(official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS):
            with self.assertRaisesRegex(
                EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_RAW_SOURCE_NOT_YET_AVAILABLE_AS_OF_DECISION"
            ):
                EVENT_EVIDENCE._verify_raw_source_citation(
                    citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
                )

    def test_cio_round7_negative_direction_field_with_positive_direction_claim_rejected(self):
        """Required item 4 (round 7) / defect 2 (round 8): a raw source
        whose own ratified `official_direction_field` maps to NEGATIVE must
        never be usable to back an envelope claiming `direction=POSITIVE`,
        never merely because a POSITIVE-sounding phrase happens to be
        absent, but because the raw source's own ratified-mapped direction
        genuinely disagrees."""
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        neg_ref = _fixture_ref(TESTONLY_RAW_SOURCE_NEGATIVE)
        citation = dict(envelope["citation"])
        citation["raw_source_ref"] = neg_ref
        citation["raw_source_sha256"] = _hash(neg_ref)
        decision_at = MODULE._end_of_day_utc(MODULE._date("2026-08-20", "x"))
        with mocked_ratified_direction_tables(official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS):
            with self.assertRaisesRegex(
                EVENT_EVIDENCE.EventEvidenceError,
                "EVENT_EVIDENCE_CITATION_DIRECTION_MISMATCH_WITH_RAW_SOURCE:claimed=POSITIVE!=derived=NEGATIVE",
            ):
                EVENT_EVIDENCE._verify_raw_source_citation(
                    citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
                )

    def test_cio_round7_bogus_locator_rejected(self):
        """Required item 5: "actually verify locator against the real
        document... not just non-empty." A `locator` that names a key that
        does not exist in the raw source, and a `locator` that names a real
        key whose value does NOT contain `observed_fact`, both fail (the
        ratified-direction table must be mocked in so these tests reach the
        locator check at all -- direction is verified first)."""
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        decision_at = MODULE._end_of_day_utc(MODULE._date("2026-08-20", "x"))

        with mocked_ratified_direction_tables(official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS):
            missing_key_citation = dict(envelope["citation"])
            missing_key_citation["locator"] = "this_key_does_not_exist_in_the_raw_source"
            with self.assertRaisesRegex(
                EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_CITATION_LOCATOR_NOT_FOUND_IN_RAW_SOURCE"
            ):
                EVENT_EVIDENCE._verify_raw_source_citation(
                    missing_key_citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
                )

            # "note" is a real top-level key in the raw source, but its content
            # is the fixture's own self-description, not `observed_fact`.
            wrong_key_citation = dict(envelope["citation"])
            wrong_key_citation["locator"] = "note"
            with self.assertRaisesRegex(
                EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_CITATION_OBSERVED_FACT_NOT_FOUND_AT_LOCATOR"
            ):
                EVENT_EVIDENCE._verify_raw_source_citation(
                    wrong_key_citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
                )

    def test_cio_round7_committer_time_used_not_author_time(self):
        """Required item 6: author time (`%aI`) is a field the commit's
        author can freely backdate; committer time (`%cI`) is set by the
        git client actually recording the commit. Static-inspects the
        actual subprocess invocation to prove `%cI` is what's queried and
        that the retired, freely-backdatable `%aI` is not used anywhere in
        this function."""
        source = inspect.getsource(EVENT_EVIDENCE._git_exact_content_first_seen)
        # Check the ACTUAL git argument, not any prose mention of %aI in the
        # function's own docstring (which legitimately explains why author
        # time was retired) -- "--format=%cI" must be the real git log
        # format specifier used, and "--format=%aI" must never appear.
        self.assertIn('"--format=%H|%cI"', source)
        self.assertNotIn("--format=%aI", source)
        self.assertNotIn("format=%H|%aI", source)

    def test_cio_round6_git_provenance_not_computable_for_uncommitted_file(self):
        """Required confirmation: "missing git/registry provenance is
        NOT_COMPUTABLE." Uses a genuinely fresh, never-committed file
        (created and deleted entirely within this test) so the result is
        guaranteed `None` regardless of this repo's real commit state.
        Round 8: the error code is now explicitly `PROVENANCE_NOT_
        COMPUTABLE`, distinct from any plain missing-price-data code."""
        with tempfile.NamedTemporaryFile(dir=FIXTURES_DIR, suffix=".json", delete=True) as fh:
            fh.write(b'{"note": "genuinely uncommitted, never in git history"}')
            fh.flush()
            path = Path(fh.name)
            self.assertIsNone(EVENT_EVIDENCE._git_exact_content_first_seen(path))
            decision_at = MODULE._end_of_day_utc(MODULE._date("2026-08-20", "x"))
            captured_at = MODULE._utc("2026-08-01T00:00:00Z", "x")
            with self.assertRaisesRegex(
                EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_PROVENANCE_NOT_COMPUTABLE"
            ):
                EVENT_EVIDENCE._verify_first_availability(path, captured_at, decision_at, "EVENT_EVIDENCE")

    def test_cio_round6_git_provenance_rejects_decision_date_before_real_first_availability(self):
        """Required confirmation (b): "a file first added after decision_at
        fails even when its embedded captured_at is backdated." Uses this
        PR's own real, committed fixture -- its REAL first-availability is
        independently re-derived here (never hardcoded), so this proves the
        rejection against whatever the file's true git history says, not an
        assumed date. Unaffected by round 8: `effective_available_at =
        max(captured_at, first_seen) = first_seen` here (since `captured_at`
        is backdated before `first_seen`), and `first_seen` alone is
        already after `decision_at`."""
        path = FIXTURES_DIR / TESTONLY_LIVE_FIXTURE
        first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(path)
        self.assertIsNotNone(first_seen, "fixture must be committed for this regression to be meaningful")
        captured_at = MODULE._utc(TESTONLY_DECLARED_CAPTURED_AT, "x")  # 2026-08-01, always earlier than first_seen
        decision_at = first_seen - _dt.timedelta(days=5)
        with self.assertRaisesRegex(EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_NOT_YET_AVAILABLE_AS_OF_DECISION"):
            EVENT_EVIDENCE._verify_first_availability(path, captured_at, decision_at, "EVENT_EVIDENCE")

    # ══════════════════════ CIO round 8 regressions ════════════════════════
    # Both the round-6/7 test-only mock design AND round 7's exact-content-
    # addressed first-seen direction were approved outright this round.
    # Stress-testing round 7's time-ordering rule against how evidence is
    # ACTUALLY collected in the real world found 2 further P1 defects,
    # covered below.

    def test_cio_round8_required_regression_a_realistic_ordering_now_passes(self):
        """Required regression (a): "a real source published before
        capture/commit passes when effective_available_at is before
        decision." Under round 7's INVERTED rule this exact scenario
        (published_at < captured_at < git commit, the normal real-world
        order) was wrongly rejected -- proven here to now succeed."""
        raw_path = FIXTURES_DIR / TESTONLY_RAW_SOURCE
        first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(raw_path)
        self.assertIsNotNone(first_seen, "fixture must be committed for this regression to be meaningful")
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        citation = dict(envelope["citation"])
        # Realistic order: published well before the file was ever
        # committed, captured shortly after publication, still before the
        # commit -- exactly what round 7 wrongly rejected.
        citation["published_at"] = (first_seen - _dt.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        citation["captured_at"] = (first_seen - _dt.timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
        decision_at = first_seen + _dt.timedelta(days=1)
        with mocked_ratified_direction_tables(official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS):
            lineage = EVENT_EVIDENCE._verify_raw_source_citation(
                citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
            )
        # effective_available_at is clamped up to the real git floor, since
        # the self-declared captured_at is (necessarily, in this fixture)
        # earlier than the commit that actually put these bytes in git.
        self.assertEqual(lineage["effective_available_at"], first_seen)

    def test_cio_round8_required_regression_b_backdated_captured_at_is_clamped_not_rejected(self):
        """Required regression (b): "captured before commit cannot backdate
        effective availability" -- rephrased from round 6/7's version, which
        REJECTED a `captured_at` preceding `first_seen` outright. Round 8:
        this must no longer RAISE (that was the bug) -- it must SUCCEED,
        with `effective_available_at` clamped to the real git floor,
        proving backdating still cannot make evidence look earlier than
        reality even though it is no longer treated as an error."""
        path = FIXTURES_DIR / TESTONLY_LIVE_FIXTURE
        first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(path)
        self.assertIsNotNone(first_seen)
        captured_at = MODULE._utc(TESTONLY_DECLARED_CAPTURED_AT, "x")  # 2026-08-01, always earlier than first_seen
        decision_at = first_seen + _dt.timedelta(days=1)
        result = EVENT_EVIDENCE._verify_first_availability(path, captured_at, decision_at, "EVENT_EVIDENCE")
        self.assertEqual(result, first_seen, "backdated captured_at must be clamped UP to the real git floor")

    def test_cio_round8_required_regression_c_decision_between_capture_and_first_seen_fails(self):
        """Required regression (c): "decision between capture and git
        first-seen fails." Even though `captured_at` alone would be
        before `decision_at`, `effective_available_at = max(captured_at,
        first_seen)` is `first_seen` here (captured_at is backdated), and
        `first_seen` itself is AFTER `decision_at` -- must still fail."""
        path = FIXTURES_DIR / TESTONLY_LIVE_FIXTURE
        first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(path)
        self.assertIsNotNone(first_seen)
        captured_at = MODULE._utc(TESTONLY_DECLARED_CAPTURED_AT, "x")  # well before first_seen
        decision_at = first_seen - _dt.timedelta(hours=1)  # between captured_at and first_seen
        self.assertLess(captured_at, decision_at)
        self.assertLess(decision_at, first_seen)
        with self.assertRaisesRegex(
            EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_NOT_YET_AVAILABLE_AS_OF_DECISION"
        ):
            EVENT_EVIDENCE._verify_first_availability(path, captured_at, decision_at, "EVENT_EVIDENCE")

    def test_cio_round8_published_at_after_captured_at_rejected(self):
        """`source_published_at <= captured_at` is independently enforced
        -- you cannot have captured/fetched something before its real-world
        publication."""
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        citation = dict(envelope["citation"])
        citation["published_at"] = "2026-08-10T10:00:00Z"
        citation["captured_at"] = "2026-08-10T09:00:00Z"  # BEFORE published_at
        decision_at = MODULE._end_of_day_utc(MODULE._date("2026-08-20", "x"))
        with self.assertRaisesRegex(
            EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_CITATION_PUBLISHED_AT_AFTER_CAPTURED_AT"
        ):
            EVENT_EVIDENCE._verify_raw_source_citation(
                citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
            )

    def test_cio_round8_envelope_captured_at_precedes_event_at_rejected(self):
        """Mirrors the citation's `published_at <= captured_at` check one
        level up: an envelope cannot claim to have captured evidence of an
        event before the event itself occurred. `verify_event_reaction_
        claim` hardcodes `forbid_test_root=True` (no parameter -- CIO round
        6), so this check is exercised directly against an envelope loaded
        via `_load_envelope` (which has no such restriction), exactly like
        every other "below the production boundary" test in this file."""
        envelope = dict(EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE))
        envelope["captured_at"] = "2026-08-10T09:00:00Z"  # BEFORE event_at (2026-08-10T09:30:00Z)
        with self.assertRaisesRegex(
            EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_ENVELOPE_CAPTURED_AT_PRECEDES_EVENT_AT"
        ):
            EVENT_EVIDENCE._verify_envelope_captured_at_not_before_event_at(envelope)

        # The real, committed fixture's own genuine captured_at (AFTER
        # event_at) passes.
        real_envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        result = EVENT_EVIDENCE._verify_envelope_captured_at_not_before_event_at(real_envelope)
        self.assertEqual(result, MODULE._utc(real_envelope["captured_at"], "x"))

    def test_cio_round8_required_regression_d_human_curated_direction_alone_fails(self):
        """Required regression (d): "human-curated POSITIVE direction
        fails." `raw_source_testonly_000_human_curated.json` carries only
        the RETIRED round-7 `observed_direction` shape -- neither closed
        round-8 structure (`official_direction_field`/`direction_
        derivation`) is present, so it fails regardless of which
        `direction_origin` the citation declares, and regardless of
        whether the ratified tables happen to be mocked in (the raw
        document itself lacks the required shape -- no table lookup is
        even reached)."""
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        human_ref = _fixture_ref(TESTONLY_RAW_SOURCE_HUMAN_CURATED)
        decision_at = MODULE._end_of_day_utc(MODULE._date("2026-08-20", "x"))

        for origin, expected_code in (
            ("OFFICIAL_STRUCTURED_FIELD", "EVENT_EVIDENCE_CITATION_OFFICIAL_DIRECTION_FIELD_INVALID"),
            ("RATIFIED_DERIVATION", "EVENT_EVIDENCE_CITATION_DIRECTION_DERIVATION_INVALID"),
        ):
            with self.subTest(direction_origin=origin):
                citation = dict(envelope["citation"])
                citation["raw_source_ref"] = human_ref
                citation["raw_source_sha256"] = _hash(human_ref)
                citation["direction_origin"] = origin
                with mocked_ratified_direction_tables(
                    official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS,
                    derivation_rules=TESTONLY_RATIFIED_DERIVATION_RULES,
                ):
                    with self.assertRaisesRegex(EVENT_EVIDENCE.EventEvidenceError, expected_code):
                        EVENT_EVIDENCE._verify_raw_source_citation(
                            citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
                        )

    def test_cio_round8_required_regression_e_official_field_and_ratified_derivation_are_the_only_positive_routes(self):
        """Required regression (e): "official-field or ratified numeric
        derivation is the only production-positive route." Both closed
        `direction_origin` routes independently succeed when their table
        entry is genuinely present (mocked in, below the production
        boundary) -- proving both mechanisms actually work, not just that
        they reject everything."""
        envelope = EVENT_EVIDENCE._load_envelope(FIXTURES_DIR / TESTONLY_LIVE_FIXTURE)
        # decision_at must be derived from BOTH raw sources' real git
        # first-seen (never hardcoded) -- this test exercises two different
        # raw source files, each with its own real commit history.
        official_first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(FIXTURES_DIR / TESTONLY_RAW_SOURCE)
        derivation_first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(
            FIXTURES_DIR / TESTONLY_RAW_SOURCE_DERIVATION
        )
        self.assertIsNotNone(official_first_seen, "fixture must be committed for this regression to be meaningful")
        self.assertIsNotNone(derivation_first_seen, "fixture must be committed for this regression to be meaningful")
        decision_at = max(official_first_seen, derivation_first_seen) + _dt.timedelta(days=1)

        # Route 1: OFFICIAL_STRUCTURED_FIELD.
        with mocked_ratified_direction_tables(official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS):
            citation = dict(envelope["citation"])
            lineage = EVENT_EVIDENCE._verify_raw_source_citation(
                citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
            )
            self.assertEqual(lineage["direction_origin"], "OFFICIAL_STRUCTURED_FIELD")

        # Without the mock, the identical citation fails -- the table is
        # genuinely empty in this module's real, unmocked state.
        with self.assertRaisesRegex(
            EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_CITATION_OFFICIAL_DIRECTION_FIELD_NOT_RATIFIED"
        ):
            EVENT_EVIDENCE._verify_raw_source_citation(
                dict(envelope["citation"]), "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
            )

        # Route 2: RATIFIED_DERIVATION.
        deriv_ref = _fixture_ref(TESTONLY_RAW_SOURCE_DERIVATION)
        deriv_citation = dict(envelope["citation"])
        deriv_citation["raw_source_ref"] = deriv_ref
        deriv_citation["raw_source_sha256"] = _hash(deriv_ref)
        deriv_citation["direction_origin"] = "RATIFIED_DERIVATION"
        deriv_citation["observed_fact"] = (
            "TESTONLY-EVENT-EVIDENCE-000 reports a fabricated positive numeric derivation "
            "used only to exercise citation verification."
        )
        with mocked_ratified_direction_tables(derivation_rules=TESTONLY_RATIFIED_DERIVATION_RULES):
            lineage = EVENT_EVIDENCE._verify_raw_source_citation(
                deriv_citation, "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
            )
            self.assertEqual(lineage["direction_origin"], "RATIFIED_DERIVATION")

        # Without the mock, the identical citation fails too.
        with self.assertRaisesRegex(
            EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_CITATION_DIRECTION_RULE_NOT_RATIFIED"
        ):
            EVENT_EVIDENCE._verify_raw_source_citation(
                dict(deriv_citation), "POSITIVE", "GUIDANCE_CHANGE_EVENT", decision_at, forbid_test_root=False,
            )

    def test_cio_round8_required_regression_f_ratified_tables_start_empty_in_real_module(self):
        """Required regression (f), the structural half: this module's own
        real, committed `RATIFIED_OFFICIAL_DIRECTION_FIELDS`/`RATIFIED_
        DIRECTION_RULES` tables are empty -- nothing from
        `mocked_ratified_direction_tables()` leaks into the real module
        state once its `with` block exits."""
        self.assertEqual(EVENT_EVIDENCE.RATIFIED_OFFICIAL_DIRECTION_FIELDS, {})
        self.assertEqual(EVENT_EVIDENCE.RATIFIED_DIRECTION_RULES, {})
        with mocked_ratified_direction_tables(
            official_fields=TESTONLY_RATIFIED_OFFICIAL_FIELDS, derivation_rules=TESTONLY_RATIFIED_DERIVATION_RULES,
        ):
            self.assertTrue(EVENT_EVIDENCE.RATIFIED_OFFICIAL_DIRECTION_FIELDS)
            self.assertTrue(EVENT_EVIDENCE.RATIFIED_DIRECTION_RULES)
        self.assertEqual(EVENT_EVIDENCE.RATIFIED_OFFICIAL_DIRECTION_FIELDS, {})
        self.assertEqual(EVENT_EVIDENCE.RATIFIED_DIRECTION_RULES, {})

    def test_cio_round6_git_provenance_succeeds_when_genuinely_consistent(self):
        """The positive path of `_verify_first_availability` -- proves the
        gate is a real, working comparison, not a function that always
        raises. Uses `first_seen` itself (independently re-derived) as both
        the declared timestamp and `decision_at`, the most conservative
        genuinely-consistent case (declared_at == first_seen ==
        decision_at)."""
        path = FIXTURES_DIR / TESTONLY_LIVE_FIXTURE
        first_seen = EVENT_EVIDENCE._git_exact_content_first_seen(path)
        self.assertIsNotNone(first_seen)
        result = EVENT_EVIDENCE._verify_first_availability(path, first_seen, first_seen, "EVENT_EVIDENCE")
        self.assertEqual(result, first_seen)

    def test_cio_round6_test_root_forbidden_applies_to_eg_canonical_record_too(self):
        """Required item 2 / item 1: "apply the same separation to the
        P8-09 canonical-record fixture." Directly exercises
        `_resolve_repo_file` with `forbid_test_root=True` (what the real
        `verify_expectations_gap_canonical_record` always passes) against a
        real, committed, otherwise-legitimate wrapper record under test/."""
        with self.assertRaisesRegex(
            EVENT_EVIDENCE.EventEvidenceError, "EVENT_EVIDENCE_SOURCE_REF_UNDER_TEST_ROOT_FORBIDDEN_IN_PRODUCTION"
        ):
            EVENT_EVIDENCE._resolve_repo_file(_fixture_ref(EG_CANONICAL_RECORD_FIXTURE), forbid_test_root=True)
        # The identical path resolves fine below the production boundary.
        resolved = EVENT_EVIDENCE._resolve_repo_file(_fixture_ref(EG_CANONICAL_RECORD_FIXTURE), forbid_test_root=False)
        self.assertTrue(resolved.is_file())

    def test_cio_round6_all_real_subjects_still_unknown(self):
        """Required confirmation (d): BTC, all 4 real Pilot subjects
        (TSM/298040.KS/267260.KS/034020.KS), and the 4 restricted Korea
        tickers all still resolve `reflection_status=UNKNOWN` through the
        REAL, unmocked production evidence-assembly + build_packet() chain
        -- unaffected by anything in this round, since none of them have,
        or can structurally ever get, a committed production-eligible
        citation."""
        for subject in ("BTC", "298040.KS", "267260.KS", "005930.KS", "000660.KS", "TSM", "034020.KS"):
            with self.subTest(subject=subject):
                kwargs = {
                    "subject": subject, "decision_date": "2026-08-22", "generated_at": "2026-08-22T00:00:00Z",
                    **MODULE.PRICE_EVIDENCE.assemble_price_evidence(subject, "2026-08-22"),
                }
                packet = MODULE.build_packet(**kwargs)
                self.assertEqual(packet["price_reflection"]["reflection_status"], "UNKNOWN")

    def test_cio_round6_output_capture_lineage_all_unknown_together_for_minimal_packet(self):
        """Required item 7: the output packet persists capture_kind/
        first_authoritative_seen_at/raw-source lineage, and `validate_
        packet` re-asserts them as an all-or-nothing field group. A
        packet with no event_reaction citation at all must show every one
        of these fields as `UNKNOWN`, together."""
        packet = MODULE.build_packet(**base_kwargs())
        er = packet["price_reflection"]["event_reaction"]
        for field in (
            "capture_kind", "first_authoritative_seen_at", "raw_source_ref",
            "raw_source_sha256", "published_at", "locator",
        ):
            self.assertEqual(er[field], "UNKNOWN", field)

    def test_cio_round6_output_capture_lineage_partial_tamper_is_rejected(self):
        packet = MODULE.build_packet(**base_kwargs())
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["event_reaction"]["capture_kind"] = "LIVE_OFFICIAL_CAPTURE"
        tampered = resign(tampered)
        with self.assertRaisesRegex(
            MODULE.PriceReflectionError, "OUTPUT_EVENT_REACTION_CAPTURE_LINEAGE_PARTIALLY_PRESENT"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_cio_round6_output_capture_kind_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs())
        tampered = resign(copy.deepcopy(packet))
        tampered["price_reflection"]["event_reaction"]["capture_kind"] = "REGRESSION_FIXTURE"
        # capture_kind alone changing (without the rest of the lineage group
        # also changing) is caught by the partial-presence invariant first;
        # assert the packet is rejected either way, since REGRESSION_FIXTURE
        # must never be a legal output value regardless of which specific
        # invariant catches it.
        tampered = resign(tampered)
        with self.assertRaises(MODULE.PriceReflectionError):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── classifier mechanics, below the production evidence boundary ────
    # CIO round 6: "positive classifier mechanics may be unit-tested below
    # the production evidence boundary." Every test below wraps a REAL
    # build_packet() call in mocked_event_evidence_verification()/
    # mocked_eg_canonical_verification() -- the RETURN COMPUTATION and
    # THRESHOLD CLASSIFICATION are 100% real and independently re-derived;
    # only the citation-authenticity step is a test-only stand-in, and it
    # touches nothing any other test in this file (all proving real
    # rejection) relies on.
    def test_cio_round6_classifier_mechanics_fully_reflected_with_exact_computed_return(self):
        expected_return = _real_verified_return("2026-07-29")
        strong_threshold = Decimal(CONTRACT["classification_thresholds"]["strong_momentum_min_pct"])
        self.assertGreaterEqual(abs(expected_return), strong_threshold)
        with mocked_event_evidence_verification():
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
        self.assertEqual(pr["event_reaction"]["capture_kind"], "LIVE_OFFICIAL_CAPTURE")
        self.assertEqual(pr["event_reaction"]["first_authoritative_seen_at"], "2026-08-01T00:00:00Z")
        self.assertTrue(any(f"verified_return_pct:{expected_return}" in r for r in pr["reasons"]))

    def test_cio_round6_classifier_mechanics_partially_reflected_with_exact_computed_return(self):
        expected_return = _real_verified_return("2026-07-28")
        with mocked_event_evidence_verification():
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

    def test_cio_round6_classifier_mechanics_disagreeing_move_is_under_reflected(self):
        expected_return = _real_verified_return("2026-07-16")
        self.assertLess(expected_return, 0)
        with mocked_event_evidence_verification():
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

    def test_cio_round6_classifier_mechanics_date_only_event_at_keeps_timing_not_computable(self):
        with mocked_event_evidence_verification():
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

    def test_cio_round6_classifier_mechanics_eg_canonical_record_path(self):
        eg_ref = _fixture_ref(EG_CANONICAL_RECORD_FIXTURE)
        expected_return = _real_verified_return("2026-07-29")
        with mocked_eg_canonical_verification():
            packet = MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                reflection_reference={
                    "expectations_gap_packet_ref": eg_ref, "expectations_gap_packet_sha256": _hash(eg_ref),
                },
                data_source_scope="KRX_OFFICIAL",
            ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["reflection_status"], "FULLY_REFLECTED")
        self.assertEqual(pr["reflection_reference"]["expectations_gap_reference_date"], "2026-07-29")
        self.assertEqual(pr["reflection_reference"]["verified_post_reference_return_pct"], str(expected_return))
        self.assertEqual(
            pr["reflection_reference"]["expectations_gap_first_authoritative_seen_at"], "2026-08-01T00:00:00Z",
        )

    def test_cio_round6_classifier_mechanics_event_reaction_preferred_over_expectations_gap(self):
        eg_ref = _fixture_ref(EG_CANONICAL_RECORD_FIXTURE)
        with mocked_event_evidence_verification(), mocked_eg_canonical_verification():
            packet = MODULE.build_packet(**real_evidence_kwargs(
                price_as_of=REAL_EVIDENCE_PRICE_AS_OF,
                recent_return_windows={"1m": "4"},
                relative_strength={"vs_market": "3"},
                event_reaction=verified_event_reaction(PARTIALLY_FIXTURE, PARTIALLY_EVENT_AT),
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

    def test_cio_round6_classifier_mechanics_price_state_unknown_forces_reflection_unknown(self):
        with mocked_event_evidence_verification():
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
        # The structural invariant forces the whole capture-lineage group
        # back to UNKNOWN too, not just the return figure.
        self.assertEqual(pr["event_reaction"]["capture_kind"], "UNKNOWN")

    def test_cio_round6_classifier_mechanics_price_state_unknown_reflection_status_contradiction_blocked_on_tamper(self):
        with mocked_event_evidence_verification():
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

    def test_cio_round6_classifier_mechanics_data_state_reflection_status_consistency_on_tamper(self):
        with mocked_event_evidence_verification():
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

    # ── Required item 1 (round 3): bare direction/status is not a real reference ──
    def test_bare_event_direction_without_evidence_lineage_does_not_unlock_reflection(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "12"},
            relative_strength={"vs_market": "10"},
            event_reaction={"event_at": "2026-08-10T09:30:00Z", "direction": "POSITIVE", "reaction_magnitude_pct": "5"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")
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
            sorted(EVENT_EVIDENCE.ALLOWED_SOURCE_CLASS),
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
            relative_strength={"position_vs_recent_high_pct": "10"},
            data_source_scope="KRX_OFFICIAL",
        ))
        pr = packet["price_reflection"]
        self.assertNotEqual(pr["price_state"], "UNKNOWN")
        self.assertEqual(pr["reflection_status"], "UNKNOWN")

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
        self.assertEqual(pr["reflection_status"], "UNKNOWN")

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
        self.assertEqual(packet["schema_version"], "price_reflection_packet/6")
        self.assertEqual(packet["contract_version"], "price_reflection/6")
        self.assertEqual(set(packet["price_reflection"]), {
            "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
            "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
            "reflection_reference", "valuation_context", "reasons", "missing_inputs",
            "data_source_scope",
        })
        self.assertEqual(set(packet["price_reflection"]["event_reaction"]), {
            "event_at", "direction", "reaction_magnitude_pct", "source_class",
            "source_ref", "source_sha256", "verified_post_event_return_pct",
            "capture_kind", "first_authoritative_seen_at", "raw_source_ref",
            "raw_source_sha256", "published_at", "locator",
        })
        self.assertEqual(set(packet["price_reflection"]["reflection_reference"]), {
            "reference_event_id", "expectation_as_of", "expectations_gap_status",
            "expectations_gap_packet_sha256", "expectations_gap_reference_date",
            "expectations_gap_first_authoritative_seen_at", "verified_post_reference_return_pct",
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
