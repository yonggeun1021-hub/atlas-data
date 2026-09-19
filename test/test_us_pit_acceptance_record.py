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
        # The repo adoption is no longer absent: it landed 2026-09-20 in
        # COMMITTED_REPLAY_RECEIPT_HASH_BOUND_UNCOMMITTED_BUNDLE mode. This temp
        # root still proves the BUNDLE mode independently of it, so the real
        # adoption's own mode is asserted here rather than its absence.
        self.assertTrue((ROOT / RUNTIME.ADOPTION_RELATIVE).exists())
        real = json.loads((ROOT / RUNTIME.ADOPTION_RELATIVE).read_text(encoding="utf-8"))
        self.assertEqual(RUNTIME.pit_binding_mode(real["pit_acceptance"]),
                         RUNTIME.PIT_BINDING_MODE_RECEIPT)
        self.assertEqual(RUNTIME.pit_binding_mode(adoption["pit_acceptance"]),
                         RUNTIME.PIT_BINDING_MODE_BUNDLE)

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


class ReceiptBindingFailsClosedTest(unittest.TestCase):
    """RECEIPT mode over the REAL committed artifacts, then every way to break it.

    The point of the mode is that it re-derives rather than reads. Each test below
    re-signs its tampering (updates the hashes the adoption pins) so that a merely
    hash-checking binding would accept it; the binding must still refuse, because
    the value is recomputed from the committed replay's own inputs.
    """

    def setUp(self):
        self.adoption = json.loads(
            (ROOT / RUNTIME.ADOPTION_RELATIVE).read_text(encoding="utf-8"))
        self.binding = copy.deepcopy(self.adoption["pit_acceptance"])

    def _temp_root(self, *, receipt=None, replay=None):
        """A root carrying just the two bound files, optionally mutated+re-signed."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        binding = copy.deepcopy(self.binding)
        replay_value = replay if replay is not None else json.loads(
            (ROOT / binding["replay_path"]).read_text(encoding="utf-8"))
        replay_raw = GEN.record_bytes(replay_value)
        receipt_value = receipt if receipt is not None else json.loads(
            (ROOT / binding["receipt_path"]).read_text(encoding="utf-8"))
        if replay is not None:
            # Re-sign: point the receipt at the mutated replay bytes.
            receipt_value["replay_report_file_sha256"] = hashlib.sha256(replay_raw).hexdigest()
        receipt_raw = GEN.record_bytes(receipt_value)
        for relative, raw in ((binding["replay_path"], replay_raw),
                              (binding["receipt_path"], receipt_raw)):
            out = tmp / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(raw)
        binding["replay_sha256"] = hashlib.sha256(replay_raw).hexdigest()
        binding["receipt_sha256"] = hashlib.sha256(receipt_raw).hexdigest()
        return tmp, {"pit_acceptance": binding}

    def test_the_real_committed_artifacts_load_and_are_pit_accepted(self):
        acceptance = RUNTIME.load_acceptance(ROOT, self.adoption)
        summary = acceptance["summary"]
        self.assertEqual(summary["status"], PIT.STATUS_PIT_ACCEPTED)
        self.assertEqual(summary["binding_mode"], RUNTIME.PIT_BINDING_MODE_RECEIPT)
        self.assertIs(summary["bundle_committed"], False)
        self.assertEqual(summary["evaluated_date_count"], 1480)
        self.assertEqual(summary["regimes_observed"],
                         ["NEUTRAL", "RISK_OFF", "RISK_ON", "STRESS"])
        self.assertEqual(summary["history_first_session_date"], "2020-10-20")
        self.assertEqual(summary["history_last_session_date"], "2026-09-11")
        self.assertEqual(len(acceptance["history_steps"]), 1480)

    def test_a_one_byte_edit_to_the_receipt_breaks_the_binding(self):
        path = ROOT / self.binding["receipt_path"]
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for relative in (self.binding["receipt_path"], self.binding["replay_path"]):
            out = tmp / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes((ROOT / relative).read_bytes())
        (tmp / self.binding["receipt_path"]).write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_ACCEPTANCE_RECEIPT_HASH_MISMATCH"):
            RUNTIME.load_acceptance(tmp, {"pit_acceptance": self.binding})

    def test_a_one_byte_edit_to_the_replay_breaks_the_binding(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for relative in (self.binding["receipt_path"], self.binding["replay_path"]):
            out = tmp / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes((ROOT / relative).read_bytes())
        path = tmp / self.binding["replay_path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_REPLAY_REPORT_HASH_MISMATCH"):
            RUNTIME.load_acceptance(tmp, {"pit_acceptance": self.binding})

    def test_a_resigned_forged_evaluation_is_caught_by_rederivation(self):
        receipt = json.loads((ROOT / self.binding["receipt_path"]).read_text(encoding="utf-8"))
        receipt["evaluation"]["evaluated_date_count"] = 9999
        tmp, adoption = self._temp_root(receipt=receipt)
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_ACCEPTANCE_RECEIPT_REDERIVATION_MISMATCH"):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_a_resigned_tampered_replay_verdict_is_caught_by_the_rerun(self):
        """Flip one step's verdict and re-sign everything: the rerun still refuses."""
        replay = json.loads((ROOT / self.binding["replay_path"]).read_text(encoding="utf-8"))
        for step in replay["steps"]:
            if step["confirmed_regime"] == "NEUTRAL":
                step["confirmed_regime"] = "RISK_ON"
                break
        else:
            self.fail("no NEUTRAL step to flip")
        tmp, adoption = self._temp_root(replay=replay)
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_REPLAY_REPORT_NOT_REPRODUCIBLE"):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_a_resigned_axis_direction_edit_is_caught_by_the_rerun(self):
        """Editing the INPUT the replay records also fails: the verdict no longer follows."""
        replay = json.loads((ROOT / self.binding["replay_path"]).read_text(encoding="utf-8"))
        replay["steps"][5]["axis_directions"]["TREND"] = (
            "NEGATIVE" if replay["steps"][5]["axis_directions"]["TREND"] != "NEGATIVE"
            else "POSITIVE")
        tmp, adoption = self._temp_root(replay=replay)
        with self.assertRaises(RUNTIME.UsPaperRuntimeError):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_claiming_the_bundle_is_committed_fails_closed(self):
        receipt = json.loads((ROOT / self.binding["receipt_path"]).read_text(encoding="utf-8"))
        receipt["population"]["committed"] = True
        tmp, adoption = self._temp_root(receipt=receipt)
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_POPULATION_BUNDLE_MUST_NOT_BE_COMMITTED"):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_an_unbound_population_hash_fails_closed(self):
        receipt = json.loads((ROOT / self.binding["receipt_path"]).read_text(encoding="utf-8"))
        receipt["population"]["file_sha256"] = "0" * 64
        tmp, adoption = self._temp_root(receipt=receipt)
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_POPULATION_BUNDLE_UNBOUND"):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_a_failed_population_validation_cannot_be_adopted(self):
        receipt = json.loads((ROOT / self.binding["receipt_path"]).read_text(encoding="utf-8"))
        receipt["population"]["validate_population"] = "FAIL"
        tmp, adoption = self._temp_root(receipt=receipt)
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_POPULATION_BUNDLE_NOT_VALIDATED"):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_both_binding_modes_at_once_is_ambiguous_not_a_free_choice(self):
        binding = copy.deepcopy(self.binding)
        binding["bundle_path"] = "evidence/us_regime_replay/population_bundle_v1.json"
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_BINDING_MODE_AMBIGUOUS"):
            RUNTIME.pit_binding_mode(binding)

    def test_neither_binding_mode_is_unbound_not_a_default(self):
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_BINDING_MODE_AMBIGUOUS"):
            RUNTIME.pit_binding_mode({"replay_report_sha256": "0" * 64})
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_ACCEPTED_RECORD_UNBOUND"):
            RUNTIME.pit_binding_mode(None)

    def test_a_replay_missing_a_regime_is_not_accepted(self):
        """Drop every STRESS step and re-sign: condition 6 still refuses."""
        replay = json.loads((ROOT / self.binding["replay_path"]).read_text(encoding="utf-8"))
        replay["steps"] = [s for s in replay["steps"] if s["confirmed_regime"] != "STRESS"]
        tmp, adoption = self._temp_root(replay=replay)
        with self.assertRaises(RUNTIME.UsPaperRuntimeError):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_an_incomplete_axis_set_in_a_step_fails_closed(self):
        replay = json.loads((ROOT / self.binding["replay_path"]).read_text(encoding="utf-8"))
        del replay["steps"][3]["axis_directions"]["LEADERSHIP"]
        tmp, adoption = self._temp_root(replay=replay)
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_REPLAY_STEP_AXES_INCOMPLETE"):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_reordered_or_duplicated_steps_fail_closed(self):
        replay = json.loads((ROOT / self.binding["replay_path"]).read_text(encoding="utf-8"))
        replay["steps"][4], replay["steps"][5] = replay["steps"][5], replay["steps"][4]
        tmp, adoption = self._temp_root(replay=replay)
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_REPLAY_STEPS_NOT_STRICTLY_ORDERED"):
            RUNTIME.load_acceptance(tmp, adoption)

    def test_a_wrong_history_last_session_fails_closed(self):
        tmp, adoption = self._temp_root()
        adoption["pit_acceptance"]["history_last_session_date"] = "2026-09-10"
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_PIT_HISTORY_LAST_SESSION_MISMATCH"):
            RUNTIME.load_acceptance(tmp, adoption)


class BothBlockersResolvedTest(unittest.TestCase):
    """Both blockers the generator named are resolved. Was ``UnreachableTodayTest``.

    (a) the 3-axis replay identity — resolved 2026-09-20 by
    ``USER_RATIFICATION_US_REPLAY_FLAG_20260920``; the five-axis replay is active
    and a 1,480-session population reaches ``PIT_ACCEPTED``.

    (b) no bundle bytes in the repository to hash — resolved by giving the
    contract a second binding mode rather than by yielding on either side of the
    contradiction. ``_forbid_tracked_output`` is UNCHANGED and the bundle is still
    not committed; what changed is that an adoption may now bind the committed
    ``replay_common_v1`` output plus a receipt and hash-bind the uncommitted
    bundle, which is the shape KR has always used.

    These tests are deliberately kept rather than deleted: they assert that the
    resolution did NOT come from relaxing the guard or from committing the bundle.
    If someone later takes either shortcut, these fail.
    """

    def test_the_five_axis_replay_identity_is_now_active(self):
        identity = json.loads(
            (ROOT / "config" / "us_historical_pit_replay_identity_v1.json").read_text(encoding="utf-8"))
        self.assertIs(identity["replay_population_wiring_activated"], True)

    def test_the_output_guard_was_not_relaxed(self):
        """The guard still refuses every path inside the checkout. Unchanged."""
        for candidate in (ROOT / BUNDLE_RELATIVE, ROOT / "data" / "x.json",
                          ROOT / "evidence" / "us_regime_replay" / "history" / "x.json"):
            with self.assertRaises(POPULATION.ReplayPopulationError):
                POPULATION._forbid_tracked_output(ROOT, candidate)
        # And BUNDLE mode still binds only a repo-relative file, so the
        # contradiction is still real for that mode -- it was routed around, not
        # argued away.
        with self.assertRaises(RUNTIME.UsPaperRuntimeError):
            RUNTIME._bound_file(ROOT, "/tmp/us_replay_bundle.json", "US_PIT_POPULATION_BUNDLE")
        with self.assertRaises(RUNTIME.UsPaperRuntimeError):
            RUNTIME._bound_file(ROOT, BUNDLE_RELATIVE, "US_PIT_POPULATION_BUNDLE")

    def test_the_real_adoption_binds_receipt_mode_and_the_bundle_stays_out(self):
        adoption = json.loads(
            (ROOT / RUNTIME.ADOPTION_RELATIVE).read_text(encoding="utf-8"))
        binding = adoption["pit_acceptance"]
        self.assertEqual(RUNTIME.pit_binding_mode(binding), RUNTIME.PIT_BINDING_MODE_RECEIPT)
        self.assertIs(binding["bundle_committed"], False)
        # The bundle it hash-binds is genuinely not in the tree, under any name.
        self.assertNotIn("bundle_path", binding)
        receipt = json.loads((ROOT / binding["receipt_path"]).read_text(encoding="utf-8"))
        self.assertIs(receipt["population"]["committed"], False)
        self.assertIs(receipt["population"]["raw_provider_rows_committed"], False)
        self.assertEqual(receipt["population"]["file_sha256"], binding["bundle_sha256"])

    def test_the_real_adoption_still_records_the_unverified_gate(self):
        """GATE_ITSELF_UNVERIFIED must survive the adoption, per the ratification."""
        for path in (RUNTIME.ADOPTION_RELATIVE,
                     "evidence/us_regime_replay/history/final-receipt.json"):
            value = json.loads((ROOT / path).read_text(encoding="utf-8"))
            gate = value["gate"]
            self.assertEqual(gate["status"], "GATE_ITSELF_UNVERIFIED", path)
            self.assertIs(gate["regime_gate_value_confirmed"], False, path)
            self.assertEqual(gate["sealed_verification_verdict_20260920"], "PARTIAL", path)

    def test_the_real_adoption_authorizes_display_only(self):
        adoption = json.loads(
            (ROOT / RUNTIME.ADOPTION_RELATIVE).read_text(encoding="utf-8"))
        authority = adoption["authority"]
        self.assertEqual(set(authority), set(RUNTIME.AUTHORITY_CLOSED))
        self.assertIs(authority["paper_runtime_display_authorized"], True)
        for key, value in sorted(authority.items()):
            if key != "paper_runtime_display_authorized":
                self.assertIs(value, False, key)
        self.assertEqual(
            sorted(k for k, v in authority.items() if v is False),
            ["action_authorized", "buy_authorized", "capital_authorized", "order_authorized",
             "production_authorized", "real_authorized", "stage_authorized",
             "strategy_authorized", "trading_authorized"])

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
