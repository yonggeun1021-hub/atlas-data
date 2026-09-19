#!/usr/bin/env python3
"""US PAPER runtime (U4): adoption/acceptance gates, session and FRED freshness,
expiry, deterministic rebuild and mutation proofs.

Offline.  Axis inputs are the committed 2026-09-08..2026-09-11 free-market-data
captures, re-derived from their retained raw bytes.  The session calendar,
population bundle, acceptance record and adoption identity are synthetic
fixtures written to a temporary root; every evaluation time is fixed.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import decision_authority as COMMON  # noqa: E402
from regime import market_scoped_pit_acceptance as PIT  # noqa: E402
from regime import us_paper_runtime as RUNTIME  # noqa: E402
from regime import us_paper_runtime_publication as PUBLICATION  # noqa: E402

CODE = "0" * 40
EVAL = "2026-09-12T00:00:00Z"          # context session 2026-09-11, expires 2026-09-14 close
HISTORY = [                           # synthetic accepted history ending 2026-09-04
    ("2026-08-26", {"RISK_VOL": "STRESS"}, "NEGATIVE"),
    ("2026-08-27", {}, "POSITIVE"),
    ("2026-08-28", {}, "POSITIVE"),
    ("2026-08-31", {}, "NEGATIVE"),
    ("2026-09-01", {}, "NEGATIVE"),
    ("2026-09-02", {}, "NEUTRAL"),
    ("2026-09-03", {}, "NEUTRAL"),
    ("2026-09-04", {}, "NEUTRAL"),
]
LIVE = ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
NO_POPULATION_REVALIDATION = types.SimpleNamespace(validate_population=lambda bundle: bundle)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dump(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def calendar() -> dict:
    sessions, cursor = [], dt.date(2026, 8, 3)
    while cursor <= dt.date(2026, 10, 30):
        if cursor.weekday() < 5 and cursor != dt.date(2026, 9, 7):
            sessions.append({"date": cursor.isoformat(), "close_time_et": "16:00"})
        cursor += dt.timedelta(days=1)
    return {"schema_version": RUNTIME.CALENDAR_SCHEMA, "market": "US", "timezone": "America/New_York",
            "source": {"source_id": "SYNTHETIC_OFFLINE_FIXTURE_NOT_OFFICIAL"},
            "coverage_start": "2026-08-03", "coverage_end": "2026-10-30", "sessions": sessions}


# The acceptance module's own record-status vocabulary, never a literal here:
# before PR #739 it accepts only a bare "OBSERVED" for every market; #739 adds
# POPULATION_RECORD_STATUS_OBSERVED (US -> "FREE_AXES_OBSERVED", the literal
# regime.us_historical_replay_population really publishes).  Following the
# constant keeps this fixture valid on either base.
US_POPULATION_RECORD_STATUS_OBSERVED = getattr(
    PIT, "POPULATION_RECORD_STATUS_OBSERVED", {"US": "OBSERVED"})["US"]


def bundle(history=HISTORY) -> dict:
    records = []
    for date, overrides, default in history:
        records.append({
            "status": US_POPULATION_RECORD_STATUS_OBSERVED, "evidence_class": PIT.POPULATION_EVIDENCE_CLASS,
            "effective_session_date": date, "no_lookahead_attestation": {"fixture": True},
            "candidate_normalized_result": {"axes": [
                {"axis": axis, "direction": overrides.get(axis, default)} for axis in RUNTIME.AXES]},
        })
    return {"schema_version": PIT.US_SCHEMA_VERSION, "mode": PIT.POPULATION_MODE, "wbs": "P1-COM-05",
            "market": "US", "records": records}


class Fixture:
    """Temporary root holding the adoption identity and its bound artifacts."""

    def __init__(self, *, history=HISTORY, adoption_edit=None, record_edit=None, write_adoption=True):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        bundle_raw = dump(bundle(history))
        (fixtures / "bundle.json").write_bytes(bundle_raw)
        evaluation = PIT.evaluate_market_pit_acceptance("US", json.loads(bundle_raw))
        record = {"schema_version": RUNTIME.ACCEPTANCE_RECORD_SCHEMA, "market": "US",
                  "bundle_sha256": sha(bundle_raw), "evaluation": evaluation}
        if record_edit:
            record_edit(record)
        record_raw = dump(record)
        (fixtures / "record.json").write_bytes(record_raw)
        calendar_raw = dump(calendar())
        (fixtures / "calendar.json").write_bytes(calendar_raw)
        adoption = json.loads((ROOT / RUNTIME.TEMPLATE_RELATIVE).read_bytes())
        adoption.update(status=RUNTIME.ADOPTION_ACTIVE_STATUS, effective_at_utc="2026-09-05T00:00:00Z")
        adoption["bindings"].update(contract_sha256=RUNTIME.CONTRACT_SHA256,
                                    implementation_sha256=RUNTIME.implementation_sha256())
        adoption["pit_acceptance"] = {
            "bundle_path": "fixtures/bundle.json", "bundle_sha256": sha(bundle_raw),
            "acceptance_record_path": "fixtures/record.json", "acceptance_record_sha256": sha(record_raw),
            "replay_report_sha256": evaluation["replay_report_sha256"] or "f" * 64,
            "history_last_session_date": history[-1][0],
        }
        adoption["session_calendar"] = {"path": "fixtures/calendar.json", "sha256": sha(calendar_raw)}
        if adoption_edit:
            adoption_edit(adoption)
        if write_adoption:
            (self.root / "config").mkdir()
            (self.root / RUNTIME.ADOPTION_RELATIVE).write_bytes(dump(adoption))
        self.adoption = adoption

    def close(self):
        self.directory.cleanup()


def records(root: Path, evaluation_at: str = EVAL) -> tuple[dict, dict]:
    return PUBLICATION.collect(root, evaluation_at, ROOT)


def evaluate(root: Path, *, evaluation_at: str = EVAL, session_records=None, latest=None,
             evidence_class: str = RUNTIME.LIVE_NATURAL, population=NO_POPULATION_REVALIDATION) -> dict:
    collected, latest_default = records(root, evaluation_at)
    with mock.patch.object(RUNTIME, "population_module", return_value=population):
        return RUNTIME.evaluate_us_paper_runtime(
            evaluation_at=evaluation_at, code_revision=CODE,
            session_records=collected if session_records is None else session_records,
            latest_source_record=latest_default if latest is None else latest,
            evidence_class=evidence_class, root=root)


class CollectedRecords:
    cache: dict = {}

    @classmethod
    def get(cls, fixture: Fixture, evaluation_at: str = EVAL) -> dict:
        if evaluation_at not in cls.cache:
            cls.cache[evaluation_at] = records(fixture.root, evaluation_at)[0]
        return copy.deepcopy(cls.cache[evaluation_at])


def closed(packet: dict) -> bool:
    return all(v is False for k, v in packet["authority"].items() if k != "paper_runtime_display_authorized")


class RatifiedBindingTest(unittest.TestCase):
    def test_normalization_identity_is_the_ratified_commit_bytes_and_us_block(self):
        bindings = RUNTIME.verify_ratified_bindings()
        self.assertEqual(bindings["normalization_ratifying_commit"][:8], "ba82906a")
        self.assertEqual(sha((ROOT / RUNTIME.NORMALIZATION_RELATIVE).read_bytes()), RUNTIME.NORMALIZATION_SHA256)
        contract = json.loads((ROOT / RUNTIME.CONTRACT_RELATIVE).read_bytes())
        self.assertEqual(contract["ratified_bindings"]["normalization"]["ratifying_commit"],
                         RUNTIME.NORMALIZATION_RATIFYING_COMMIT)
        template = json.loads((ROOT / RUNTIME.TEMPLATE_RELATIVE).read_bytes())
        self.assertEqual(template["bindings"]["common_v1_binding_payload_sha256"],
                         bindings["common_v1_binding_payload_sha256"])

    def test_drifted_us_block_blocks(self):
        real = RUNTIME.json_object

        def drift(raw, code):
            value = real(raw, code)
            if code == "REFERENCE_POLICY_INVALID":
                value["markets"]["US"]["BREADTH"]["positive_min"] = "0.50"
            return value

        with mock.patch.object(RUNTIME, "json_object", side_effect=drift):
            with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError, "NORMALIZATION_NOT_VERBATIM"):
                RUNTIME.verify_ratified_bindings()

    def test_template_is_not_active_and_active_path_is_never_the_template(self):
        template = json.loads((ROOT / RUNTIME.TEMPLATE_RELATIVE).read_bytes())
        self.assertEqual(template["status"], RUNTIME.ADOPTION_TEMPLATE_STATUS)
        self.assertIsNone(template["pit_acceptance"]["bundle_sha256"])
        active = ROOT / RUNTIME.ADOPTION_RELATIVE
        if active.is_file():
            self.assertNotEqual(json.loads(active.read_bytes()).get("status"), RUNTIME.ADOPTION_TEMPLATE_STATUS)


class GateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()
        cls.known = evaluate(cls.fixture.root)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def test_fully_bound_fixture_classifies_through_common_v1(self):
        packet = self.known
        self.assertEqual(packet["reasons"], [])
        self.assertEqual(packet["decision_status"], "PAPER_RUNTIME_CLASSIFIED")
        self.assertEqual(packet["runtime_regime"], "NEUTRAL")
        self.assertTrue(packet["runtime_decision_available"])
        self.assertTrue(packet["authority"]["paper_runtime_display_authorized"])
        self.assertTrue(closed(packet))
        self.assertEqual(packet["session"], {
            "context_session_date": "2026-09-11", "context_session_close_at": "2026-09-11T20:00:00Z",
            "execution_session_date": "2026-09-14", "expires_at": "2026-09-14T20:00:00Z"})
        self.assertEqual([row["session_date"] for row in packet["chain"]], LIVE)
        self.assertTrue(all(row["complete"] for row in packet["chain"]))
        self.assertEqual(packet["pit_acceptance"]["status"], "PIT_ACCEPTED")
        self.assertEqual(packet["current_observation"]["source"]["observed_at_utc"], "2026-09-11T21:41:28Z")

    def test_without_adoption_identity_is_unknown_with_explicit_reasons(self):
        fixture = Fixture(write_adoption=False)
        try:
            packet = evaluate(fixture.root)
        finally:
            fixture.close()
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["reasons"], ["US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT",
                                             "US_PIT_ACCEPTED_RECORD_UNBOUND",
                                             "US_OFFICIAL_SESSION_CALENDAR_UNBOUND"])
        self.assertEqual(packet["adoption"]["status"], "ABSENT")
        # The two UNBOUND reasons are consequences of the absent adoption, not
        # two further gaps a reader could go and close somewhere else: both
        # bindings live inside the adoption identity.  The packet has to say so,
        # or it reads as three separable wiring problems.
        self.assertEqual(packet["adoption"]["blocking_reason"],
                         "US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT")
        self.assertEqual(packet["adoption"]["derived_reasons"],
                         ["US_PIT_ACCEPTED_RECORD_UNBOUND", "US_OFFICIAL_SESSION_CALENDAR_UNBOUND"])
        self.assertEqual(packet["reasons"],
                         [packet["adoption"]["blocking_reason"], *packet["adoption"]["derived_reasons"]])
        self.assertFalse(packet["authority"]["paper_runtime_display_authorized"])
        self.assertEqual(packet["latest_source_diagnostic"]["source"]["observed_at_utc"], "2026-09-11T21:41:28Z")

    def test_template_copied_to_active_path_is_unknown(self):
        fixture = Fixture(write_adoption=False)
        try:
            (fixture.root / "config").mkdir()
            (fixture.root / RUNTIME.ADOPTION_RELATIVE).write_bytes((ROOT / RUNTIME.TEMPLATE_RELATIVE).read_bytes())
            packet = evaluate(fixture.root)
        finally:
            fixture.close()
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("ADOPTION_IDENTITY_IS_TEMPLATE_NOT_ACTIVE", packet["reasons"])
        self.assertEqual(packet["adoption"]["status"], "INVALID")

    def test_adoption_gates(self):
        cases = {
            "ADOPTION_IDENTITY_NOT_ACTIVE": lambda a: a.update(status="CIO_TECHNICAL_PROPOSED"),
            "ADOPTION_NOT_YET_EFFECTIVE": lambda a: a.update(effective_at_utc="2026-09-12T00:00:01Z"),
            "ADOPTION_AUTHORITY_ESCALATION": lambda a: a["authority"].update(order_authorized=True),
            "ADOPTION_BINDING_MISMATCH_IMPLEMENTATION_SHA256":
                lambda a: a["bindings"]["implementation_sha256"].update({"regime/us_paper_runtime.py": "0" * 64}),
            "ADOPTION_BINDING_MISMATCH_NORMALIZATION":
                lambda a: a["bindings"]["normalization"].update(ratifying_commit="0" * 40),
            "ADOPTION_SCOPE_INVALID": lambda a: a.update(evidence_class="HISTORICAL_REPLAY"),
        }
        for reason, edit in cases.items():
            with self.subTest(reason=reason):
                fixture = Fixture(adoption_edit=edit)
                try:
                    packet = evaluate(fixture.root, session_records=CollectedRecords.get(self.fixture))
                finally:
                    fixture.close()
                self.assertEqual(packet["runtime_regime"], "UNKNOWN")
                self.assertEqual(packet["reasons"][0], reason)
                self.assertIn("US_PIT_ACCEPTED_RECORD_UNBOUND", packet["reasons"])

    def test_acceptance_must_be_bound_and_reaccepted(self):
        def other_bundle(record):
            record["bundle_sha256"] = "e" * 64

        def claimed(record):
            record["evaluation"]["status"] = "PIT_ACCEPTED"

        no_stress = [(d, {}, v) for d, _, v in HISTORY]
        cases = [
            ("US_PIT_ACCEPTANCE_RECORD_BUNDLE_UNBOUND", {"record_edit": other_bundle}),
            ("US_PIT_ACCEPTANCE_RECORD_HASH_MISMATCH",
             {"adoption_edit": lambda a: a["pit_acceptance"].update(acceptance_record_sha256="d" * 64)}),
            ("US_PIT_POPULATION_BUNDLE_HASH_MISMATCH",
             {"adoption_edit": lambda a: a["pit_acceptance"].update(bundle_sha256="c" * 64)}),
            ("US_PIT_POPULATION_BUNDLE_ABSENT",
             {"adoption_edit": lambda a: a["pit_acceptance"].update(bundle_path="fixtures/missing.json")}),
            ("US_PIT_ACCEPTED_RECORD_UNBOUND",
             {"adoption_edit": lambda a: a["pit_acceptance"].update(replay_report_sha256=None)}),
            ("US_PIT_ACCEPTANCE_RECORD_REDERIVATION_MISMATCH", {"history": no_stress, "record_edit": claimed}),
            ("US_PIT_NOT_ACCEPTED", {"history": no_stress}),
            ("US_PIT_HISTORY_LAST_SESSION_MISMATCH",
             {"adoption_edit": lambda a: a["pit_acceptance"].update(history_last_session_date="2026-09-03")}),
        ]
        for reason, kwargs in cases:
            with self.subTest(reason=reason):
                fixture = Fixture(**kwargs)
                try:
                    packet = evaluate(fixture.root, session_records=CollectedRecords.get(self.fixture))
                finally:
                    fixture.close()
                self.assertEqual(packet["runtime_regime"], "UNKNOWN")
                self.assertIn(reason, packet["reasons"])
                self.assertNotEqual(packet["pit_acceptance"]["status"], "PIT_ACCEPTED")

    def test_population_owner_validator_is_required(self):
        # The synthetic bundle is not a real population; the unpatched owner
        # validator must reject it and keep the runtime UNKNOWN.
        from regime import us_historical_replay_population as POPULATION

        packet = evaluate(self.fixture.root, population=POPULATION)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("US_PIT_POPULATION_BUNDLE_REVALIDATION_FAILED", packet["reasons"])

    def test_calendar_gates(self):
        cases = {
            "US_OFFICIAL_SESSION_CALENDAR_HASH_MISMATCH": lambda a: a["session_calendar"].update(sha256="a" * 64),
            "US_OFFICIAL_SESSION_CALENDAR_UNBOUND": lambda a: a.pop("session_calendar"),
        }
        for reason, edit in cases.items():
            with self.subTest(reason=reason):
                fixture = Fixture(adoption_edit=edit)
                try:
                    packet = evaluate(fixture.root, session_records=CollectedRecords.get(self.fixture))
                finally:
                    fixture.close()
                self.assertEqual(packet["runtime_regime"], "UNKNOWN")
                self.assertIn(reason, packet["reasons"])
                # The adoption itself loaded here, so US_OFFICIAL_SESSION_CALENDAR_UNBOUND
                # is an independent observation about a valid adoption that omits
                # the binding -- the opposite of the derived case above, and the
                # distinction the two fields exist to carry.
                self.assertEqual(packet["adoption"]["status"], RUNTIME.ADOPTION_ACTIVE_STATUS)
                self.assertIsNone(packet["adoption"]["blocking_reason"])
                self.assertEqual(packet["adoption"]["derived_reasons"], [])
        beyond = evaluate(self.fixture.root, evaluation_at="2026-10-31T00:00:00Z", session_records={})
        self.assertIn("EXPECTED_COMPLETED_SESSION_CALENDAR_UNKNOWN", beyond["reasons"])

    def test_non_live_evidence_is_unknown(self):
        packet = evaluate(self.fixture.root, session_records=CollectedRecords.get(self.fixture),
                          evidence_class="SYNTHETIC_OFFLINE_FIXTURE")
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["reasons"], ["EVIDENCE_CLASS_NOT_LIVE_NATURAL"])


class FreshnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()
        cls.records = CollectedRecords.get(cls.fixture)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def run_with(self, edited: dict, evaluation_at: str = EVAL) -> dict:
        return evaluate(self.fixture.root, evaluation_at=evaluation_at, session_records=edited)

    def test_session_mismatch_is_source_not_advanced(self):
        edited = copy.deepcopy(self.records)
        stale = copy.deepcopy(edited["2026-09-10"])
        stale.update(session_date="2026-09-11", observed_at_utc="2026-09-11T21:41:28Z")
        stale["vix_evidence"]["captured_at_utc"] = "2026-09-11T21:41:28Z"
        stale["reference_input"]["fred_liquidity"]["captured_at_utc"] = "2026-09-11T21:41:28Z"
        edited["2026-09-11"] = stale
        packet = self.run_with(edited)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        for axis in ("TREND", "BREADTH", "LEADERSHIP"):
            self.assertIn(f"{axis}_SOURCE_NOT_ADVANCED_EXPECTED_SESSION", packet["reasons"])
        self.assertIn("CURRENT_SESSION_OBSERVATION_INCOMPLETE", packet["reasons"])
        observation = packet["current_observation"]["axis_observations"]
        self.assertNotIn("missing_reason", observation["RISK_VOL"])  # release axis never coerced to a session

    def test_selection_reports_not_advanced_when_only_older_session_captured(self):
        index = [row for row in PUBLICATION.capture_index(ROOT) if row["observed_at"] is not None
                 and row["observed_at"] < RUNTIME.instant("2026-09-11T21:00:00Z", "x")]
        index.append({"path": "evidence/free_market_data/derived/2026-09-11/x/manifest.json",
                      "observed_at": RUNTIME.instant("2026-09-11T21:41:28Z", "x"),
                      "session_date": "2026-09-10", "error": None})
        close = RUNTIME.instant("2026-09-11T20:00:00Z", "x")
        session = {"date": dt.date(2026, 9, 11), "close_at": close,
                   "expires_at": RUNTIME.instant("2026-09-14T20:00:00Z", "x")}
        self.assertEqual(PUBLICATION.select_session_record(ROOT, index, session, RUNTIME.instant(EVAL, "x")),
                         {"error": "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"})

    def test_fred_vintage_and_observation_lookahead(self):
        def vix_vintage(record):
            record["reference_input"]["fred"]["realtime_start"] = "2026-09-12"
            record["vix_evidence"]["observation"]["realtime_start"] = "2026-09-12"

        def vix_after_own_vintage(record):
            record["reference_input"]["fred"].update(observation_date="2026-09-11", realtime_start="2026-09-10")
            record["vix_evidence"]["observation"].update(observation_date="2026-09-11", realtime_start="2026-09-10")

        def liquidity_vintage(record):
            record["reference_input"]["fred_liquidity"]["series"][0]["realtime_start"] = "2026-09-12"
            series = record["reference_input"]["fred_liquidity"]["series"]
            record["reference_input"]["fred_liquidity"]["derived_payload_sha256"] = sha(COMMON.canonical_bytes(series))

        def vix_unretained(record):
            record["vix_evidence"]["observation"]["value"] = "31.00"

        def liquidity_rehashed(record):
            record["reference_input"]["fred_liquidity"]["series"][0]["change"] = "1"

        cases = {
            "RISK_VOL_VINTAGE_LOOKAHEAD": vix_vintage,
            "RISK_VOL_VINTAGE_LOOKAHEAD ": vix_after_own_vintage,
            "LIQUIDITY_VINTAGE_LOOKAHEAD": liquidity_vintage,
            "RISK_VOL_RAW_REDERIVATION_MISMATCH": vix_unretained,
            "LIQUIDITY_DERIVED_HASH_MISMATCH": liquidity_rehashed,
        }
        for reason, edit in cases.items():
            with self.subTest(reason=reason):
                edited = copy.deepcopy(self.records)
                edit(edited["2026-09-11"])
                packet = self.run_with(edited)
                self.assertEqual(packet["runtime_regime"], "UNKNOWN")
                self.assertIn(reason.strip(), packet["reasons"])
                self.assertIn("CURRENT_SESSION_OBSERVATION_INCOMPLETE", packet["reasons"])

        # A capture two days after the session (the 2026-09-13 run for the
        # 2026-09-11 session) may carry a newer vintage, but never an
        # observation dated after the session being decided.
        later = "2026-09-14T08:00:00Z"
        edited = CollectedRecords.get(self.fixture, later)
        self.assertEqual(edited["2026-09-11"]["observed_at_utc"], "2026-09-13T21:42:41Z")
        baseline = self.run_with(copy.deepcopy(edited), later)
        self.assertEqual(baseline["runtime_regime"], "NEUTRAL")
        for block in (edited["2026-09-11"]["reference_input"]["fred"], edited["2026-09-11"]["vix_evidence"]["observation"]):
            block.update(observation_date="2026-09-12", realtime_start="2026-09-13", realtime_end="2026-09-13")
        packet = self.run_with(edited, later)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("RISK_VOL_OBSERVATION_LOOKAHEAD", packet["reasons"])

    def test_capture_before_close_or_after_evaluation_is_rejected(self):
        early = copy.deepcopy(self.records)
        early["2026-09-11"]["observed_at_utc"] = "2026-09-11T19:59:59Z"
        self.assertIn("SESSION_SOURCE_CAPTURED_BEFORE_SESSION_CLOSE", self.run_with(early)["reasons"])
        future = copy.deepcopy(self.records)
        future["2026-09-11"]["observed_at_utc"] = "2026-09-12T00:00:01Z"
        self.assertIn("SESSION_SOURCE_LOOKAHEAD", self.run_with(future)["reasons"])

    def test_capture_at_or_after_session_window_end_is_rejected_by_the_runtime(self):
        # The publication selector already filters by window, so this proves
        # the runtime's own end-of-window check independently: the 2026-09-10
        # session window closes at the 2026-09-11 close (20:00Z).
        def moved(observed_at):
            edited = copy.deepcopy(self.records)
            row = edited["2026-09-10"]
            row["observed_at_utc"] = observed_at
            row["vix_evidence"]["captured_at_utc"] = observed_at
            row["reference_input"]["fred_liquidity"]["captured_at_utc"] = observed_at
            return self.run_with(edited)

        for observed_at in ("2026-09-11T20:00:00Z", "2026-09-11T21:00:00Z"):
            with self.subTest(observed_at=observed_at):
                packet = moved(observed_at)
                step = [row for row in packet["chain"] if row["session_date"] == "2026-09-10"][0]
                self.assertIn("SESSION_SOURCE_OUTSIDE_SESSION_WINDOW", json.dumps(step))
                self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        inside = moved("2026-09-11T19:59:59Z")
        step = [row for row in inside["chain"] if row["session_date"] == "2026-09-10"][0]
        self.assertNotIn("SESSION_SOURCE_OUTSIDE_SESSION_WINDOW", json.dumps(step))

    def test_mixed_session_generation_inside_a_session_axis_is_unknown(self):
        reference_path = ("reference_input", "us_market_reference")

        def trend_row(reference):
            reference["trend_etfs"][-1]["as_of_session_date"] = "2026-09-10"

        def breadth_row(reference):
            reference["proxy_axes"]["BREADTH"]["measurement"]["observations"][0]["as_of_session_date"] = "2026-09-10"

        def breadth_top(reference):
            reference["proxy_axes"]["BREADTH"]["measurement"]["as_of_session_date"] = "2026-09-10"

        def leadership_group(reference):
            groups = reference["proxy_axes"]["LEADERSHIP"]["measurement"]["ordered_groups"]
            smh = [row for row in groups if row["symbol"] == "SMH"][0]
            smh["as_of_session_date"] = "2026-09-10"

        cases = {"TREND": trend_row, "BREADTH": breadth_row, "BREADTH ": breadth_top,
                 "LEADERSHIP": leadership_group}
        for axis, edit in cases.items():
            with self.subTest(axis=axis):
                edited = copy.deepcopy(self.records)
                reference = edited["2026-09-11"]
                for key in reference_path:
                    reference = reference[key]
                edit(reference)
                packet = self.run_with(edited)
                self.assertEqual(packet["runtime_regime"], "UNKNOWN")
                self.assertIn(f"{axis.strip()}_MIXED_SESSION_GENERATION", packet["reasons"])
                self.assertIn("CURRENT_SESSION_OBSERVATION_INCOMPLETE", packet["reasons"])

    def test_stale_or_missing_axis_is_immediate_unknown_without_carry(self):
        missing = copy.deepcopy(self.records)
        missing["2026-09-11"]["reference_input"]["us_market_reference"]["proxy_axes"]["BREADTH"]["status"] = "UNAVAILABLE"
        packet = self.run_with(missing)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("BREADTH_PROXY_NOT_OBSERVED", packet["reasons"])
        row = packet["aggregation"]["live_steps"][-1]
        self.assertEqual(row["confirmed_regime"], "UNKNOWN")
        self.assertEqual(row["hysteresis"]["rule"], "UNKNOWN_IMMEDIATE")
        self.assertIsNone(packet["chain"][-1]["axis_directions"]["BREADTH"])

        dropped = copy.deepcopy(self.records)
        del dropped["2026-09-11"]
        packet = self.run_with(dropped)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("SESSION_SOURCE_MISSING", packet["reasons"])

    def test_gap_session_resets_hysteresis(self):
        gap = copy.deepcopy(self.records)
        del gap["2026-09-10"]
        packet = self.run_with(gap)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["reasons"], ["COMMON_CONFIRMATION_PENDING"])
        self.assertEqual([row["confirmed_regime"] for row in packet["aggregation"]["live_steps"]],
                         ["NEUTRAL", "NEUTRAL", "UNKNOWN", "UNKNOWN"])

    def test_expiry_at_next_session_close(self):
        known = evaluate(self.fixture.root, evaluation_at="2026-09-14T19:59:59Z")
        self.assertEqual(known["session"]["context_session_date"], "2026-09-11")
        self.assertEqual(known["runtime_regime"], "NEUTRAL")
        self.assertTrue(RUNTIME.is_current(known, "2026-09-14T19:59:59Z"))
        self.assertFalse(RUNTIME.is_current(known, "2026-09-14T20:00:00Z"))
        expired = evaluate(self.fixture.root, evaluation_at="2026-09-14T20:00:00Z")
        self.assertEqual(expired["session"]["context_session_date"], "2026-09-14")
        self.assertEqual(expired["runtime_regime"], "UNKNOWN")
        self.assertEqual(expired["chain"][-1]["session_date"], "2026-09-14")
        self.assertIn("SESSION_SOURCE_MISSING", expired["reasons"])
        self.assertFalse(RUNTIME.is_current(expired, "2026-09-14T20:00:00Z"))


class DeterminismAndMutationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()
        cls.records = CollectedRecords.get(cls.fixture)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def test_rebuild_is_byte_identical_and_tamper_is_rejected(self):
        first = evaluate(self.fixture.root, session_records=copy.deepcopy(self.records))
        second = evaluate(self.fixture.root, session_records=copy.deepcopy(self.records))
        self.assertEqual(RUNTIME.pretty_bytes(first), RUNTIME.pretty_bytes(second))
        inputs = dict(evaluation_at=EVAL, code_revision=CODE, session_records=copy.deepcopy(self.records),
                      latest_source_record=records(self.fixture.root)[1],
                      evidence_class=RUNTIME.LIVE_NATURAL, root=self.fixture.root)
        with mock.patch.object(RUNTIME, "population_module", return_value=NO_POPULATION_REVALIDATION):
            RUNTIME.validate_us_paper_runtime(copy.deepcopy(first), **inputs)
            forged = copy.deepcopy(first)
            forged["runtime_regime"] = "RISK_ON"
            with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError, "RUNTIME_REDERIVATION_MISMATCH"):
                RUNTIME.validate_us_paper_runtime(forged, **inputs)

    def test_mutation_carry_on_missing_axis_is_refused(self):
        # Mutation: a previous session's axes substituted for a missing current
        # capture.  The runtime must report the gap, never the carried state.
        carried = copy.deepcopy(self.records)
        carried["2026-09-11"] = copy.deepcopy(carried["2026-09-10"])
        packet = evaluate(self.fixture.root, session_records=carried)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("SESSION_SOURCE_DATE_MISMATCH", packet["reasons"])
        self.assertIsNone(packet["current_observation"]["hysteresis"]["pending_regime"])

    def test_mutation_accept_unbound_acceptance_is_refused(self):
        # Mutation: a PIT_ACCEPTED record for a different bundle, with every
        # hash re-signed to match, must still be refused.
        other = [(d, {"RISK_VOL": "STRESS"} if i == 0 else {}, v) for i, (d, _, v) in enumerate(HISTORY)]
        donor = Fixture(history=other[:-1] + [("2026-09-04", {}, "POSITIVE")])
        try:
            donor_record = (donor.root / "fixtures/record.json").read_bytes()
        finally:
            donor.close()

        fixture = Fixture()
        try:
            (fixture.root / "fixtures/record.json").write_bytes(donor_record)
            adoption = json.loads((fixture.root / RUNTIME.ADOPTION_RELATIVE).read_bytes())
            adoption["pit_acceptance"]["acceptance_record_sha256"] = sha(donor_record)
            (fixture.root / RUNTIME.ADOPTION_RELATIVE).write_bytes(dump(adoption))
            packet = evaluate(fixture.root, session_records=copy.deepcopy(self.records))
        finally:
            fixture.close()
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("US_PIT_ACCEPTANCE_RECORD_BUNDLE_UNBOUND", packet["reasons"])

    def test_mutation_forked_aggregation_is_not_possible(self):
        # The only aggregator is the shared common-v1 replay: replacing it
        # changes the runtime result, and the published aggregation is exactly
        # that replay over history + live chain.
        packet = evaluate(self.fixture.root, session_records=copy.deepcopy(self.records))
        history = PIT._build_sequence("US", bundle()["records"])["steps"]
        sequence = {"schema_version": 1, "market": "US", "case_id": "us-paper-runtime", "steps": history + [
            {"packet_id": "us-live-" + row["session_date"], "as_of_date": row["session_date"],
             "axes": {a: {"status": "DEFINED", "direction": d} for a, d in row["axis_directions"].items()}}
            for row in packet["chain"]]}
        report = COMMON.validate_common_v1_replay(COMMON.replay_common_v1(sequence), sequence)
        self.assertEqual(packet["aggregation"]["report_payload_sha256"], COMMON.payload_sha256(report))
        self.assertEqual(packet["aggregation"]["live_steps"], report["steps"][len(history):])

        real = COMMON.replay_common_v1

        def forked_runtime_only(seq, policy=None):
            result = real(seq, policy)
            if seq["case_id"] == "us-paper-runtime":
                result["final_regime"] = "RISK_ON"
            return result

        with mock.patch.object(RUNTIME.COMMON, "replay_common_v1", side_effect=forked_runtime_only):
            mutated = evaluate(self.fixture.root, session_records=copy.deepcopy(self.records))
        self.assertEqual(mutated["runtime_regime"], "RISK_ON")  # the shared replay is the only aggregator

        def forked_everywhere(seq, policy=None):
            result = real(seq, policy)
            result["thresholds"] = dict(result["thresholds"], risk_on_min_score=2)
            return result

        with mock.patch.object(RUNTIME.COMMON, "replay_common_v1", side_effect=forked_everywhere):
            refused = evaluate(self.fixture.root, session_records=copy.deepcopy(self.records))
        self.assertEqual(refused["runtime_regime"], "UNKNOWN")  # accepted replay binding catches a fork
        self.assertIn("US_PIT_ACCEPTANCE_RECORD_REDERIVATION_MISMATCH", refused["reasons"])

        source = (ROOT / "regime/us_paper_runtime.py").read_text(encoding="utf-8")
        for forbidden in ("risk_on_min_score", "risk_off_max_score", "confirmation_required =",
                          "def replay_common", "ordinary_transition_finalized_packets"):
            self.assertNotIn(forbidden, source)
        self.assertIsNone(re.search(r"def\s+(classify|band|hysteresis)\b", source))

    def test_runtime_regime_never_outside_authorized_set_and_authority_closed_when_unknown(self):
        fixture = Fixture(write_adoption=False)
        try:
            packet = evaluate(fixture.root, session_records={})
        finally:
            fixture.close()
        self.assertIn(packet["runtime_regime"], RUNTIME.RUNTIME_REGIMES)
        self.assertEqual(packet["authority"], RUNTIME.AUTHORITY_CLOSED)
        self.assertTrue(packet["decision_id"].startswith("us-paper-regime:"))


if __name__ == "__main__":
    unittest.main()
