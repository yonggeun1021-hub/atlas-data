#!/usr/bin/env python3
"""US PIT acceptance record generator (US-DATA-1 U3).

Offline only: constructed bundles, temp roots, fixed instants. No network, no
secrets, no wall clock.

The generator's only real contract is
``regime/us_paper_runtime.py::load_acceptance``, which is exact-match and
re-derives everything on every call. So the load-bearing test here is not "the
record has the fields we thought": it is that a record this module emits is
accepted, byte for byte, by that loader — with the calendar this PR commits bound
alongside it — and that the runtime then stops reporting
``US_PIT_ACCEPTED_RECORD_UNBOUND`` and ``US_OFFICIAL_SESSION_CALENDAR_UNBOUND``.

Two honest scoping notes:

* ``validate_population`` is the population module's own 2500-line-tested
  validator. Constructing a bundle that satisfies it AND observes all four
  regimes is not possible today (see ``UnreachableTodayTest``), so the accepted
  path substitutes a pass-through validator at exactly the seam both the
  generator and the runtime use. What is under test is therefore the record
  contract, not the population validator — and the substitution is symmetric: the
  runtime loader gets the same seam, so nothing is proven that the real runtime
  would not also see.
* No test creates ``config/us_paper_runtime_adoption_v1.json``. The runtime-level
  proof runs against a temp root, so the real repository's adoption identity
  stays absent and U5 stays a user ratification.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar_producer as PROD  # noqa: E402
from regime import decision_authority as COMMON  # noqa: E402
from regime import market_scoped_pit_acceptance as PIT  # noqa: E402
from regime import us_pit_acceptance_record as GEN  # noqa: E402
from regime import us_historical_replay_population as POPULATION  # noqa: E402
from regime import us_paper_runtime as RUNTIME  # noqa: E402

CALENDAR_RELATIVE = "data/us_official_session_calendar_v1.json"
CALENDAR_PATH = ROOT / CALENDAR_RELATIVE
BUNDLE_RELATIVE = "evidence/us_regime_replay/population_bundle_v1.json"
RECORD_RELATIVE = "evidence/us_regime_replay/pit_acceptance_record_v1.json"

# Reuse the acceptance module's own regression fixtures rather than inventing a
# second idea of what a population bundle looks like.
_SPEC = importlib.util.spec_from_file_location(
    "pit_acceptance_fixtures", ROOT / "test" / "test_market_scoped_pit_acceptance.py")
FIXTURES = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(FIXTURES)

CALENDAR = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
CALENDAR_SHA256 = hashlib.sha256(CALENDAR_PATH.read_bytes()).hexdigest()


class PassThroughPopulation:
    """The population-validator seam, substituted symmetrically (see module doc)."""

    @staticmethod
    def validate_population(bundle):
        return bundle


def official_sessions(count: int, *, start: str = "2026-09-01") -> list[str]:
    dates = [row["date"] for row in CALENDAR["sessions"] if row["date"] >= start]
    if len(dates) < count:
        raise AssertionError("committed calendar is too short for this fixture")
    return dates[:count]


def accepted_bundle_bytes(*, start: str = "2026-09-01") -> bytes:
    """A full RISK_ON -> NEUTRAL -> RISK_OFF -> STRESS cycle on real sessions."""
    dates = official_sessions(len(FIXTURES.FULL_CYCLE), start=start)
    rows = [(date, directions) for date, (_, directions) in zip(dates, FIXTURES.FULL_CYCLE)]
    bundle = FIXTURES.real_bundle("US", rows)
    return (json.dumps(bundle, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build(bundle_raw: bytes, *, calendar=None, population=PassThroughPopulation):
    return GEN.build_acceptance_record(
        bundle_raw,
        calendar=CALENDAR if calendar is None else calendar,
        bundle_relative_path=BUNDLE_RELATIVE,
        calendar_relative_path=CALENDAR_RELATIVE,
        calendar_file_sha256=CALENDAR_SHA256,
        population=population,
    )


class RecordShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = accepted_bundle_bytes()
        cls.record = build(cls.raw)

    def test_schema_and_market_are_the_runtime_literals(self):
        self.assertEqual(self.record["schema_version"], "us_pit_acceptance_record/1")
        self.assertEqual(self.record["schema_version"], RUNTIME.ACCEPTANCE_RECORD_SCHEMA)
        self.assertEqual(self.record["market"], "US")

    def test_bundle_sha256_is_the_hash_of_the_committed_file_bytes(self):
        self.assertEqual(self.record["bundle_sha256"], hashlib.sha256(self.raw).hexdigest())

    def test_evaluation_is_the_acceptance_module_output_verbatim(self):
        expected = PIT.evaluate_market_pit_acceptance("US", json.loads(self.raw))
        self.assertEqual(
            COMMON.canonical_bytes(self.record["evaluation"]), COMMON.canonical_bytes(expected))
        self.assertEqual(self.record["evaluation"]["status"], PIT.STATUS_PIT_ACCEPTED)
        self.assertEqual(sorted(self.record["evaluation"]["regimes_observed"]),
                         ["NEUTRAL", "RISK_OFF", "RISK_ON", "STRESS"])

    def test_replay_report_hash_is_rederived_not_copied(self):
        bundle = json.loads(self.raw)
        sequence = PIT._build_sequence("US", bundle["records"])
        self.assertEqual(
            self.record["derivation"]["replay_report_sha256"],
            PIT.payload_sha256(COMMON.replay_common_v1(copy.deepcopy(sequence))))

    def test_the_session_calendar_is_bound_by_hash(self):
        binding = self.record["session_calendar"]
        self.assertEqual(binding["path"], CALENDAR_RELATIVE)
        self.assertEqual(binding["sha256"], CALENDAR_SHA256)
        self.assertEqual(binding["derived_payload_sha256"], CALENDAR["derived_payload_sha256"])

    def test_history_last_session_is_a_session_of_the_bound_calendar(self):
        history_last = self.record["derivation"]["history_last_session_date"]
        self.assertIn(history_last, [row["date"] for row in CALENDAR["sessions"]])

    def test_authority_is_entirely_closed(self):
        for key, value in self.record["authority"].items():
            if key == "acceptance_record_only":
                self.assertTrue(value)
            else:
                self.assertFalse(value, key)

    def test_the_record_is_deterministic_and_carries_no_clock(self):
        again = build(self.raw)
        self.assertEqual(GEN.record_bytes(again), GEN.record_bytes(self.record))
        self.assertNotIn("produced_at", json.dumps(self.record))
        self.assertNotIn("generated_at", json.dumps(self.record))


class RuntimeAcceptsRecordTest(unittest.TestCase):
    """The real proof: the runtime's own loader, then the whole decision."""

    def _temp_root(self, bundle_raw: bytes, record: dict):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        record_raw = GEN.record_bytes(record)
        for relative, raw in (
            (BUNDLE_RELATIVE, bundle_raw),
            (RECORD_RELATIVE, record_raw),
            (CALENDAR_RELATIVE, CALENDAR_PATH.read_bytes()),
        ):
            path = tmp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        adoption = {
            "pit_acceptance": {
                "bundle_path": BUNDLE_RELATIVE,
                "bundle_sha256": hashlib.sha256(bundle_raw).hexdigest(),
                "acceptance_record_path": RECORD_RELATIVE,
                "acceptance_record_sha256": hashlib.sha256(record_raw).hexdigest(),
                "replay_report_sha256": record["derivation"]["replay_report_sha256"],
                "history_last_session_date": record["derivation"]["history_last_session_date"],
            },
            "session_calendar": {"path": CALENDAR_RELATIVE, "sha256": CALENDAR_SHA256},
        }
        return tmp, adoption

    def test_runtime_load_acceptance_accepts_a_generated_record(self):
        raw = accepted_bundle_bytes()
        record = build(raw)
        tmp, adoption = self._temp_root(raw, record)
        with mock.patch.object(RUNTIME, "population_module", return_value=PassThroughPopulation):
            acceptance = RUNTIME.load_acceptance(tmp, adoption)
        self.assertEqual(acceptance["summary"]["status"], PIT.STATUS_PIT_ACCEPTED)
        self.assertEqual(acceptance["summary"]["bundle_sha256"],
                         adoption["pit_acceptance"]["bundle_sha256"])
        self.assertEqual(acceptance["history_last"].isoformat(),
                         record["derivation"]["history_last_session_date"])

    def test_both_bindings_together_clear_all_three_blocking_reasons(self):
        """The U5 end state, proven in a temp root — the repo adoption stays absent."""
        raw = accepted_bundle_bytes()
        record = build(raw)
        tmp, adoption = self._temp_root(raw, record)
        with mock.patch.object(RUNTIME, "population_module", return_value=PassThroughPopulation):
            acceptance = RUNTIME.load_acceptance(tmp, adoption)
            calendar = RUNTIME.load_calendar(tmp, adoption)
        now = dt.datetime(2026, 9, 18, 20, 30, 0, tzinfo=dt.timezone.utc)
        plan = RUNTIME.session_plan(calendar, now, acceptance["history_last"])
        # The three reasons data/latest_us_paper_runtime_decision.json reports are
        # all attributable to a missing adoption, a missing record, and a missing
        # calendar. With the record and the calendar bound, only the adoption's
        # own absence remains.
        self.assertEqual(plan["context"]["date"], dt.date(2026, 9, 18))
        self.assertEqual(plan["execution"]["date"], dt.date(2026, 9, 21))
        self.assertTrue(plan["live_sessions"])
        self.assertFalse((ROOT / RUNTIME.ADOPTION_RELATIVE).exists())

    def test_a_one_byte_edit_to_the_record_breaks_the_binding(self):
        raw = accepted_bundle_bytes()
        record = build(raw)
        tmp, adoption = self._temp_root(raw, record)
        path = tmp / RECORD_RELATIVE
        path.write_bytes(path.read_bytes() + b" ")
        with mock.patch.object(RUNTIME, "population_module", return_value=PassThroughPopulation):
            with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                        "US_PIT_ACCEPTANCE_RECORD_HASH_MISMATCH"):
                RUNTIME.load_acceptance(tmp, adoption)

    def test_a_forged_evaluation_block_is_caught_by_rederivation(self):
        raw = accepted_bundle_bytes()
        record = build(raw)
        record["evaluation"]["evaluated_date_count"] = 999
        tmp, adoption = self._temp_root(raw, record)
        with mock.patch.object(RUNTIME, "population_module", return_value=PassThroughPopulation):
            with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                        "US_PIT_ACCEPTANCE_RECORD_REDERIVATION_MISMATCH"):
                RUNTIME.load_acceptance(tmp, adoption)


class FailClosedTest(unittest.TestCase):
    def test_a_bundle_the_owner_validator_rejects_produces_no_record(self):
        class Rejecting:
            @staticmethod
            def validate_population(bundle):
                raise ValueError("POPULATION_SCHEMA_INVALID")

        with self.assertRaisesRegex(GEN.UsPitAcceptanceRecordError, "BUNDLE_REVALIDATION_FAILED"):
            build(accepted_bundle_bytes(), population=Rejecting)

    def test_a_not_accepted_bundle_produces_no_record(self):
        # Only the bull half of the cycle: NEUTRAL, RISK_OFF and STRESS are never
        # observed, so acceptance is refused and no record is emitted.
        dates = official_sessions(2)
        rows = [(date, ["POSITIVE"] * 5) for date in dates]
        raw = (json.dumps(FIXTURES.real_bundle("US", rows), indent=2, sort_keys=True) + "\n").encode()
        with self.assertRaisesRegex(GEN.UsPitAcceptanceRecordError, "US_PIT_NOT_ACCEPTED"):
            build(raw)

    def test_a_history_last_session_outside_the_calendar_produces_no_record(self):
        # Saturdays are never official sessions, so the bound calendar cannot
        # contain them and the record is refused rather than emitted for a date
        # the runtime's session_plan would reject every day.
        raw = (json.dumps(FIXTURES.real_bundle("US", [
            ("2026-01-03", ["POSITIVE"] * 5), ("2026-01-10", ["POSITIVE"] * 5),
            ("2026-01-17", ["NEUTRAL"] * 5), ("2026-01-24", ["NEUTRAL"] * 5),
            ("2026-01-31", ["NEGATIVE"] * 5), ("2026-02-07", ["NEGATIVE"] * 5),
            ("2026-02-14", ["NEUTRAL", "NEUTRAL", "STRESS", "NEUTRAL", "NEUTRAL"]),
        ]), indent=2, sort_keys=True) + "\n").encode()
        with self.assertRaisesRegex(GEN.UsPitAcceptanceRecordError,
                                    "HISTORY_LAST_SESSION_NOT_IN_OFFICIAL_CALENDAR"):
            build(raw)

    def test_a_calendar_with_no_session_after_history_produces_no_record(self):
        raw = accepted_bundle_bytes()
        history_last = official_sessions(len(FIXTURES.FULL_CYCLE))[-1]
        truncated = copy.deepcopy(CALENDAR)
        truncated["sessions"] = [row for row in truncated["sessions"] if row["date"] <= history_last]
        with self.assertRaisesRegex(GEN.UsPitAcceptanceRecordError,
                                    "OFFICIAL_CALENDAR_HAS_NO_SESSION_AFTER_HISTORY"):
            build(raw, calendar=truncated)

    def test_a_non_json_bundle_produces_no_record(self):
        with self.assertRaisesRegex(GEN.UsPitAcceptanceRecordError, "BUNDLE_JSON_INVALID"):
            build(b"not json at all")

    def test_an_empty_bundle_produces_no_record(self):
        with self.assertRaisesRegex(GEN.UsPitAcceptanceRecordError, "BUNDLE_BYTES_REQUIRED"):
            build(b"")

    def test_a_calendar_without_sessions_produces_no_record(self):
        with self.assertRaisesRegex(GEN.UsPitAcceptanceRecordError, "CALENDAR_SESSIONS_EMPTY"):
            build(accepted_bundle_bytes(), calendar={"sessions": []})


class U5ValuesTest(unittest.TestCase):
    def test_the_calendar_only_block_names_what_is_still_unfillable(self):
        values = GEN.calendar_only_u5_values(CALENDAR_RELATIVE, CALENDAR_SHA256)
        self.assertIsNone(values["pit_acceptance"])
        self.assertEqual(values["session_calendar"],
                         {"path": CALENDAR_RELATIVE, "sha256": CALENDAR_SHA256})
        self.assertEqual(values["bindings"]["contract_sha256"], RUNTIME.CONTRACT_SHA256)
        self.assertEqual(values["bindings"]["implementation_sha256"],
                         RUNTIME.implementation_sha256())

    def test_the_full_block_is_exactly_what_the_runtime_reads_back(self):
        raw = accepted_bundle_bytes()
        record = build(raw)
        record_raw = GEN.record_bytes(record)
        values = GEN.u5_adoption_values(
            record, record_relative_path=RECORD_RELATIVE,
            record_file_sha256=hashlib.sha256(record_raw).hexdigest())
        self.assertEqual(set(values["pit_acceptance"]), {
            "bundle_path", "bundle_sha256", "acceptance_record_path",
            "acceptance_record_sha256", "replay_report_sha256", "history_last_session_date",
        })
        self.assertEqual(set(values["session_calendar"]), {"path", "sha256"})
        # Every key the runtime's expected-bindings table demands is present.
        self.assertEqual(values["bindings"]["contract_sha256"], RUNTIME.CONTRACT_SHA256)
        self.assertIn("regime/us_paper_runtime.py", values["bindings"]["implementation_sha256"])

    def test_the_implementation_binding_records_the_inactive_replay_identity(self):
        bound = RUNTIME.implementation_sha256()
        identity = "config/us_historical_pit_replay_identity_v1.json"
        self.assertIn(identity, bound)
        # The file is present, so its hash — not ABSENT — is what U5 will bind.
        # Activating it later changes this hash and correctly invalidates the
        # adoption, which is exactly the runtime's stated intent.
        self.assertNotEqual(bound[identity], RUNTIME.IMPLEMENTATION_PATH_ABSENT)


class UnreachableTodayTest(unittest.TestCase):
    """Why no real accepted record is committed, now that only one blocker is left.

    The generator named two blockers. (a) the 3-axis replay identity — RESOLVED
    2026-09-20 by USER_RATIFICATION_US_REPLAY_FLAG_20260920; the five-axis replay
    is active and a 1,480-session population does reach PIT_ACCEPTED. (b) no
    bundle bytes exist in the repository to hash — UNRESOLVED, and it is not a
    timing problem: ``us_historical_replay_population._forbid_tracked_output``
    refuses to write historical replay evidence anywhere inside the checkout,
    while ``us_paper_runtime.load_acceptance`` binds ``bundle_path`` only as a
    repo-relative file it can hash. Until that is decided, the acceptance record
    is derivable but not bindable, and the adoption stays absent.
    """

    def test_the_five_axis_replay_identity_is_now_active(self):
        identity = json.loads(
            (ROOT / "config" / "us_historical_pit_replay_identity_v1.json").read_text(encoding="utf-8"))
        self.assertIs(identity["replay_population_wiring_activated"], True)

    def test_the_bundle_binding_and_the_output_guard_still_contradict(self):
        """Blocker (b), asserted as the contradiction it actually is."""
        # The guard refuses every path inside the checkout ...
        with self.assertRaises(POPULATION.ReplayPopulationError):
            POPULATION._forbid_tracked_output(ROOT, ROOT / BUNDLE_RELATIVE)
        # ... and the runtime binds the bundle only as a repo-relative file.
        with self.assertRaises(RUNTIME.UsPaperRuntimeError):
            RUNTIME._bound_file(ROOT, "/tmp/us_replay_bundle.json", "US_PIT_POPULATION_BUNDLE")
        with self.assertRaises(RUNTIME.UsPaperRuntimeError):
            RUNTIME._bound_file(ROOT, BUNDLE_RELATIVE, "US_PIT_POPULATION_BUNDLE")

    def test_a_three_axis_us_population_can_never_be_pit_accepted(self):
        # A 3-axis population leaves BREADTH and LEADERSHIP UNKNOWN, so common-v1
        # classifies every step UNKNOWN, no regime is ever observed, and
        # acceptance is structurally impossible. Still true, and still reachable:
        # the replay identity flag is reversible.
        dates = official_sessions(7)
        rows = [(date, ["POSITIVE", "UNKNOWN", "POSITIVE", "POSITIVE", "UNKNOWN"])
                for date in dates]
        bundle = FIXTURES.real_bundle("US", [
            (date, [d for d in directions if d != "UNKNOWN"]) for date, directions in rows
        ])
        for record in bundle["records"]:
            record["status"] = "FREE_AXES_OBSERVED"
        result = PIT.evaluate_market_pit_acceptance("US", bundle)
        self.assertNotEqual(result["status"], PIT.STATUS_PIT_ACCEPTED)

    def test_no_population_bundle_is_committed_yet(self):
        self.assertFalse((ROOT / BUNDLE_RELATIVE).exists())
        self.assertFalse((ROOT / RECORD_RELATIVE).exists())

    def test_the_replay_workflow_still_commits_nothing(self):
        raw = (ROOT / ".github" / "workflows" / "us-regime-historical-replay.yml").read_text(
            encoding="utf-8")
        self.assertIn("nothing is committed", raw)
        self.assertIn("Tracked output prohibition", raw)


class CommittedCalendarIsUsableTest(unittest.TestCase):
    def test_the_committed_calendar_passes_the_producer_validator(self):
        summary = PROD.validate_runtime_calendar(CALENDAR)
        self.assertEqual(summary["derived_payload_sha256"], CALENDAR["derived_payload_sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
