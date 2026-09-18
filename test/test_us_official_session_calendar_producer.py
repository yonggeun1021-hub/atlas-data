#!/usr/bin/env python3
"""US official session calendar producer (US-DATA-1 U3).

Offline only: the synthetic page fixtures test/test_us_official_session_calendar.py
already uses, injected fake openers, fixed instants. No test reads the wall clock
or the network.

The load-bearing guarantees are:

1. The produced file is accepted by ``regime/us_paper_runtime.py::load_calendar``
   ITSELF, not by a restatement of it — the validator is exact-match, so a
   near-miss would only ever surface as another daily runtime UNKNOWN.
2. Every date comes from the official basis. A window that leaves the officially
   published years, a date the ratified rule cannot classify, a cross-check that
   attests nothing, and a source-rule file whose hash moved all refuse the WHOLE
   file — never one date, and never a weekday inference.
3. The committed bytes are stable across re-runs over an unchanged page, so the
   sha256 a U5 adoption binds does not silently move.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar as CAL  # noqa: E402
from market_data import us_official_session_calendar_producer as PROD  # noqa: E402
from regime import us_paper_runtime as RUNTIME  # noqa: E402

FIXTURES = ROOT / "test" / "fixtures" / "us_session_calendar"
NYSE_HTML = (FIXTURES / "nyse_hours_calendars_synthetic.html").read_bytes()
NASDAQ_HTML = (FIXTURES / "nasdaq_holiday_schedule_synthetic.html").read_bytes()

FIXED = dt.datetime(2026, 9, 14, 21, 0, 0, tzinfo=dt.timezone.utc)
COMMITTED = ROOT / "data" / "us_official_session_calendar_v1.json"
WORKFLOW = ROOT / ".github" / "workflows" / "us-official-session-calendar.yml"


def fixed_clock():
    return FIXED


def page_opener(body: bytes, final_url: str, status: int = 200):
    def opener(url, headers):
        return status, "text/html; charset=utf-8", final_url, 0, body
    return opener


def synthetic_captures(*, nyse: bytes = NYSE_HTML, nasdaq: bytes = NASDAQ_HTML) -> dict:
    return {
        CAL.NYSE_SOURCE_ID: CAL.capture_page(
            CAL.NYSE_SOURCE_ID,
            opener=page_opener(nyse, CAL.NYSE_URL),
            clock=fixed_clock,
        ),
        CAL.NASDAQ_SOURCE_ID: CAL.capture_page(
            CAL.NASDAQ_SOURCE_ID,
            opener=page_opener(nasdaq, CAL.NASDAQ_URL),
            clock=fixed_clock,
        ),
    }


class ProducedShapeTest(unittest.TestCase):
    """The shape the runtime demands, built from the synthetic fixtures."""

    @classmethod
    def setUpClass(cls):
        cls.calendar = PROD.build_runtime_calendar(synthetic_captures())

    def test_schema_market_timezone_and_source_are_the_runtime_literals(self):
        self.assertEqual(self.calendar["schema_version"], "us_official_session_calendar/1")
        self.assertEqual(self.calendar["schema_version"], RUNTIME.CALENDAR_SCHEMA)
        self.assertEqual(self.calendar["market"], "US")
        self.assertEqual(self.calendar["timezone"], "America/New_York")
        self.assertIsInstance(self.calendar["source"], dict)
        self.assertEqual(
            sorted(self.calendar["source"]), ["cross_check", "primary"],
        )

    def test_every_session_row_has_exactly_the_two_required_keys(self):
        for row in self.calendar["sessions"]:
            self.assertEqual(set(row), {"date", "close_time_et"})
            self.assertIn(row["close_time_et"], ("16:00", "13:00"))
            self.assertIn(row["close_time_et"], RUNTIME.CLOSE_TIMES)

    def test_sessions_are_weekdays_strictly_increasing_and_inside_coverage(self):
        start = dt.date.fromisoformat(self.calendar["coverage_start"])
        end = dt.date.fromisoformat(self.calendar["coverage_end"])
        previous = None
        for row in self.calendar["sessions"]:
            day = dt.date.fromisoformat(row["date"])
            self.assertLess(day.weekday(), 5)
            self.assertTrue(start <= day <= end)
            if previous is not None:
                self.assertGreater(day, previous)
            previous = day

    def test_coverage_defaults_to_the_officially_published_years(self):
        self.assertEqual(self.calendar["published_years"], [2026, 2027, 2028])
        self.assertEqual(self.calendar["coverage_start"], "2026-01-01")
        self.assertEqual(self.calendar["coverage_end"], "2028-12-31")

    def test_official_closures_are_absent_and_early_closes_carry_1pm(self):
        rows = {row["date"]: row["close_time_et"] for row in self.calendar["sessions"]}
        for closure in ("2026-01-01", "2026-04-03", "2027-12-24", "2028-12-25"):
            self.assertNotIn(closure, rows)
        for early in ("2026-11-27", "2026-12-24", "2027-11-26", "2028-11-24"):
            self.assertEqual(rows[early], "13:00")
        self.assertEqual(rows["2026-09-08"], "16:00")

    def test_source_block_carries_url_retrieval_time_and_raw_sha256(self):
        primary = self.calendar["source"]["primary"]
        self.assertEqual(primary["source_id"], CAL.NYSE_SOURCE_ID)
        self.assertEqual(primary["request_url"], CAL.NYSE_URL)
        self.assertEqual(primary["retrieved_at_utc"], "2026-09-14T21:00:00Z")
        self.assertEqual(primary["raw_sha256"], hashlib.sha256(NYSE_HTML).hexdigest())
        cross = self.calendar["source"]["cross_check"]
        self.assertEqual(cross["source_id"], CAL.NASDAQ_SOURCE_ID)
        self.assertEqual(cross["request_url"], CAL.NASDAQ_URL)
        self.assertEqual(cross["raw_sha256"], hashlib.sha256(NASDAQ_HTML).hexdigest())

    def test_the_ratified_source_rule_is_recorded_and_weekday_inference_is_shut(self):
        rule = self.calendar["source_rule"]
        self.assertEqual(rule["ratification_id"], "US-SESSION-CALENDAR-SOURCE-V1-20260914")
        self.assertTrue(self.calendar["weekday_inference_prohibited"])
        self.assertEqual(self.calendar["conflict_or_missing_result"], "US_FINISHED_SESSION_UNKNOWN")
        self.assertEqual(self.calendar["basis"], CAL.BASIS_OFFICIAL)

    def test_authority_is_entirely_closed(self):
        self.assertEqual(self.calendar["authority"], CAL.AUTHORITY)
        for key, value in self.calendar["authority"].items():
            if key != "market_calendar_observation_only":
                self.assertFalse(value, key)

    def test_cross_check_actually_attested_dates(self):
        self.assertGreater(self.calendar["counts"]["cross_checked_date_count"], 200)


class RuntimeAcceptsProducedCalendarTest(unittest.TestCase):
    """The producer's real contract: the runtime's own loader, not a restatement."""

    def _bound(self, calendar: dict):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        relative = "data/us_official_session_calendar_v1.json"
        path = tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = PROD.calendar_bytes(calendar)
        path.write_bytes(raw)
        adoption = {"session_calendar": {"path": relative,
                                         "sha256": hashlib.sha256(raw).hexdigest()}}
        return tmp, adoption

    def test_runtime_load_calendar_accepts_the_synthetic_build(self):
        calendar = PROD.build_runtime_calendar(synthetic_captures())
        tmp, adoption = self._bound(calendar)
        loaded = RUNTIME.load_calendar(tmp, adoption)
        self.assertEqual(len(loaded["sessions"]), calendar["counts"]["session_count"])
        self.assertEqual(loaded["coverage_start"].isoformat(), calendar["coverage_start"])
        self.assertEqual(loaded["coverage_end"].isoformat(), calendar["coverage_end"])

    def test_runtime_session_plan_names_context_execution_and_expiry(self):
        calendar = PROD.build_runtime_calendar(synthetic_captures())
        tmp, adoption = self._bound(calendar)
        loaded = RUNTIME.load_calendar(tmp, adoption)
        now = dt.datetime(2026, 9, 18, 20, 30, 0, tzinfo=dt.timezone.utc)
        plan = RUNTIME.session_plan(loaded, now, dt.date(2026, 9, 11))
        self.assertEqual(plan["context"]["date"], dt.date(2026, 9, 18))
        self.assertEqual(plan["execution"]["date"], dt.date(2026, 9, 21))
        self.assertEqual([row["date"] for row in plan["live_sessions"]], [
            dt.date(2026, 9, 14), dt.date(2026, 9, 15), dt.date(2026, 9, 16),
            dt.date(2026, 9, 17), dt.date(2026, 9, 18),
        ])

    def test_a_one_byte_edit_breaks_the_runtime_hash_binding(self):
        calendar = PROD.build_runtime_calendar(synthetic_captures())
        tmp, adoption = self._bound(calendar)
        path = tmp / adoption["session_calendar"]["path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(RUNTIME.UsPaperRuntimeError,
                                    "US_OFFICIAL_SESSION_CALENDAR_HASH_MISMATCH"):
            RUNTIME.load_calendar(tmp, adoption)

    def test_the_committed_calendar_is_a_live_natural_official_capture(self):
        """The file this PR commits, checked by the runtime and by the producer."""
        self.assertTrue(COMMITTED.is_file(), "the committed calendar is missing")
        calendar = PROD.load_calendar_file(COMMITTED)
        summary = PROD.validate_runtime_calendar(calendar)
        self.assertGreater(summary["session_count"], 500)
        self.assertEqual(calendar["source"]["primary"]["request_url"], CAL.NYSE_URL)
        self.assertEqual(calendar["source"]["cross_check"]["request_url"], CAL.NASDAQ_URL)
        raw = COMMITTED.read_bytes()
        relative = str(COMMITTED.relative_to(ROOT))
        adoption = {"session_calendar": {"path": relative,
                                         "sha256": hashlib.sha256(raw).hexdigest()}}
        loaded = RUNTIME.load_calendar(ROOT, adoption)
        self.assertEqual(len(loaded["sessions"]), summary["session_count"])

    def test_the_committed_calendar_is_canonical_bytes(self):
        calendar = PROD.load_calendar_file(COMMITTED)
        self.assertEqual(COMMITTED.read_bytes(), PROD.calendar_bytes(calendar))


class FailClosedTest(unittest.TestCase):
    def test_a_window_outside_the_published_years_refuses_the_whole_file(self):
        for start, end in (("2025-01-01", "2026-12-31"), ("2026-01-01", "2029-01-02")):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(PROD.UsOfficialSessionCalendarProducerError,
                                            "COVERAGE_OUTSIDE_PUBLISHED_YEARS"):
                    PROD.build_runtime_calendar(
                        synthetic_captures(), coverage_start=start, coverage_end=end,
                    )

    def test_an_inverted_window_fails(self):
        with self.assertRaisesRegex(PROD.UsOfficialSessionCalendarProducerError,
                                    "COVERAGE_RANGE_INVALID"):
            PROD.build_runtime_calendar(
                synthetic_captures(), coverage_start="2026-06-01", coverage_end="2026-05-01",
            )

    def test_a_nasdaq_disagreement_refuses_the_whole_file(self):
        # Nasdaq moves Good Friday a week later (also a Friday, so the page's own
        # weekday check still passes). NYSE and Nasdaq then disagree about two
        # dates, and ONE such date refuses the whole file rather than being
        # skipped or silently turned into a session.
        broken = NASDAQ_HTML.replace(b"April 3, 2026", b"April 10, 2026")
        self.assertNotEqual(broken, NASDAQ_HTML)
        with self.assertRaisesRegex(PROD.UsOfficialSessionCalendarProducerError,
                                    "OFFICIAL_CALENDAR_DATE_UNKNOWN:2026-04-03:NYSE_NASDAQ_CONFLICT"):
            PROD.build_runtime_calendar(synthetic_captures(nasdaq=broken))

    def test_an_ambiguous_official_page_never_produces_a_calendar(self):
        no_statement = NYSE_HTML.replace(b"1:00 p.m.", b"one o'clock")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "NYSE_EARLY_CLOSE_YEAR_MISSING"):
            PROD.build_runtime_calendar(synthetic_captures(nyse=no_statement))

    def test_a_transport_failure_never_produces_a_calendar(self):
        captures = synthetic_captures()
        captures[CAL.NASDAQ_SOURCE_ID]["transport_error"] = "HTTP_STATUS:503"
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "CAPTURE_TRANSPORT_FAILED"):
            PROD.build_runtime_calendar(captures)

    def test_a_tampered_raw_body_never_produces_a_calendar(self):
        captures = synthetic_captures()
        response = captures[CAL.NYSE_SOURCE_ID]["response"]
        response["raw_base64"] = base64.b64encode(NYSE_HTML + b" ").decode("ascii")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "CAPTURE_RAW_HASH_MISMATCH"):
            PROD.build_runtime_calendar(captures)

    def test_an_off_source_final_url_never_produces_a_calendar(self):
        captures = {
            CAL.NYSE_SOURCE_ID: CAL.capture_page(
                CAL.NYSE_SOURCE_ID,
                opener=page_opener(NYSE_HTML, "https://nyse.example.com/mirror"),
                clock=fixed_clock,
            ),
            CAL.NASDAQ_SOURCE_ID: synthetic_captures()[CAL.NASDAQ_SOURCE_ID],
        }
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "CAPTURE_FINAL_URL_OFF_SOURCE"):
            PROD.build_runtime_calendar(captures)

    def test_a_weakened_source_rule_never_produces_a_calendar(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for rel in (
            "config/us_session_calendar_source_v1.json",
            "config/regime_source_owner_registry_v2.json",
            "evidence/authority/us_session_calendar_source_user_ratification_20260914.json",
        ):
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, tmp / rel)
        config_path = tmp / "config/us_session_calendar_source_v1.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["weekday_inference_prohibited"] = False
        config_path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "WEEKDAY_INFERENCE_OPEN"):
            PROD.build_runtime_calendar(
                synthetic_captures(), source_config_path=config_path, root=tmp,
            )

    def test_validate_rejects_a_row_with_an_extra_key(self):
        calendar = PROD.build_runtime_calendar(synthetic_captures())
        calendar["sessions"][0]["note"] = "extra"
        with self.assertRaisesRegex(PROD.UsOfficialSessionCalendarProducerError,
                                    "CALENDAR_ROW_FIELDS_INVALID"):
            PROD.validate_runtime_calendar(calendar)

    def test_validate_rejects_a_hand_edited_session_list(self):
        # A hand-edited close time is shape-legal but is not what the official
        # page stated, so the derived-payload hash catches it.
        calendar = PROD.build_runtime_calendar(synthetic_captures())
        regular = next(row for row in calendar["sessions"] if row["close_time_et"] == "16:00")
        regular["close_time_et"] = "13:00"
        with self.assertRaisesRegex(PROD.UsOfficialSessionCalendarProducerError,
                                    "CALENDAR_DERIVED_PAYLOAD_SHA256_MISMATCH"):
            PROD.validate_runtime_calendar(calendar)

    def test_validate_requires_a_completed_and_a_forward_session_at_as_of(self):
        calendar = PROD.build_runtime_calendar(synthetic_captures())
        with self.assertRaisesRegex(PROD.UsOfficialSessionCalendarProducerError,
                                    "CALENDAR_NO_COMPLETED_SESSION_AT"):
            PROD.validate_runtime_calendar(
                calendar, as_of=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc))
        with self.assertRaisesRegex(PROD.UsOfficialSessionCalendarProducerError,
                                    "CALENDAR_NO_FORWARD_SESSION_AT"):
            PROD.validate_runtime_calendar(
                calendar, as_of=dt.datetime(2029, 1, 1, tzinfo=dt.timezone.utc))


class StableCommittedBytesTest(unittest.TestCase):
    def test_a_rerun_over_an_unchanged_page_does_not_move_the_committed_sha256(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = tmp / "data" / "us_official_session_calendar_v1.json"
        first = PROD.write_calendar(PROD.build_runtime_calendar(synthetic_captures()), path)
        self.assertEqual(first["action"], PROD.WROTE)
        # A later capture of the same page: different retrieval instants, same
        # publication. The committed bytes must not move, or the U5 binding dies.
        later = synthetic_captures()
        for capture in later.values():
            capture["capture_started_at"] = "2026-10-01T21:00:00Z"
            capture["response_received_at"] = "2026-10-01T21:00:01Z"
        second = PROD.write_calendar(PROD.build_runtime_calendar(later), path)
        self.assertEqual(second["action"], PROD.UNCHANGED)
        self.assertEqual(second["file_sha256"], first["file_sha256"])

    def test_a_changed_official_page_does_rewrite(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = tmp / "data" / "us_official_session_calendar_v1.json"
        first = PROD.write_calendar(PROD.build_runtime_calendar(synthetic_captures()), path)
        narrowed = PROD.build_runtime_calendar(
            synthetic_captures(), coverage_start="2026-01-01", coverage_end="2026-12-31")
        second = PROD.write_calendar(narrowed, path)
        self.assertEqual(second["action"], PROD.WROTE)
        self.assertNotEqual(second["file_sha256"], first["file_sha256"])

    def test_the_evidence_copy_is_keyed_by_the_derived_payload_hash(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        calendar = PROD.build_runtime_calendar(synthetic_captures())
        result = PROD.write_calendar(
            calendar, tmp / "data" / "cal.json", evidence_dir=tmp / "evidence")
        evidence = Path(result["evidence_path"])
        self.assertEqual(evidence.name, calendar["derived_payload_sha256"] + ".json")
        self.assertEqual(evidence.read_bytes(), PROD.calendar_bytes(calendar))


class WorkflowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = WORKFLOW.read_text(encoding="utf-8")
        cls.document = yaml.safe_load(cls.raw)

    def test_dispatch_only_with_the_cadence_left_in_a_comment(self):
        triggers = self.document[True] if True in self.document else self.document["on"]
        self.assertEqual(sorted(triggers), ["workflow_dispatch"])
        self.assertNotIn("schedule", triggers)
        self.assertIn("#   '20 8 * * 1'", self.raw)

    def test_least_privilege_and_serialised(self):
        self.assertEqual(self.document["permissions"], {"contents": "write"})
        self.assertEqual(self.document["concurrency"]["group"],
                         "atlas-us-official-session-calendar")
        self.assertFalse(self.document["concurrency"]["cancel-in-progress"])

    def test_actions_are_pinned_to_the_runtime_contract_shas(self):
        contract = json.loads(
            (ROOT / "config" / "github_actions_runtime_contract.json").read_text(encoding="utf-8"))
        uses = [step["uses"] for step in self.document["jobs"]["capture"]["steps"] if "uses" in step]
        self.assertEqual(uses, [
            f"actions/checkout@{contract['actions']['actions/checkout']['commit_sha']}",
            f"actions/setup-python@{contract['actions']['actions/setup-python']['commit_sha']}",
        ])

    def test_it_runs_the_offline_regressions_before_touching_the_network(self):
        steps = self.document["jobs"]["capture"]["steps"]
        names = [step.get("name", "") for step in steps]
        regression = next(index for index, name in enumerate(names) if "Offline regression" in name)
        capture = next(index for index, name in enumerate(names) if "Capture the official pages" in name)
        self.assertLess(regression, capture)
        self.assertIn("test/test_us_official_session_calendar_producer.py", steps[regression]["run"])
        self.assertIn("test/test_us_pit_acceptance_record.py", steps[regression]["run"])

    def test_it_names_both_official_sources_and_why_they_are_authoritative(self):
        self.assertIn(CAL.NYSE_URL, self.raw)
        self.assertIn(CAL.NASDAQ_URL, self.raw)
        self.assertIn("US-SESSION-CALENDAR-SOURCE-V1-20260914", self.raw)
        self.assertIn("Weekday inference is prohibited", self.raw)

    def test_it_uses_no_secret_at_all(self):
        self.assertNotIn("secrets.", self.raw)

    def test_it_never_claims_to_unblock_the_runtime_by_itself(self):
        self.assertIn("config/us_paper_runtime_adoption_v1.json", self.raw)
        self.assertIn("U5", self.raw)


class NoAdoptionSideEffectTest(unittest.TestCase):
    def test_u3_does_not_create_or_activate_the_adoption_identity(self):
        self.assertFalse((ROOT / RUNTIME.ADOPTION_RELATIVE).exists(),
                         "U5 is a user ratification; U3 must not create the active adoption")
        template = json.loads((ROOT / RUNTIME.TEMPLATE_RELATIVE).read_text(encoding="utf-8"))
        self.assertEqual(template["status"], RUNTIME.ADOPTION_TEMPLATE_STATUS)
        self.assertIsNone(template["session_calendar"]["path"])

    def test_the_runtime_still_refuses_today_and_names_the_calendar_reason(self):
        packet = RUNTIME.evaluate_us_paper_runtime(
            evaluation_at="2026-09-18T20:30:00Z",
            code_revision="0" * 40,
            session_records={},
            latest_source_record=None,
            evidence_class=RUNTIME.LIVE_NATURAL,
            root=ROOT,
        )
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertIn("US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT", packet["reasons"])
        self.assertIn("US_OFFICIAL_SESSION_CALENDAR_UNBOUND", packet["reasons"])
        self.assertIn("US_PIT_ACCEPTED_RECORD_UNBOUND", packet["reasons"])
        self.assertFalse(packet["authority"]["paper_runtime_display_authorized"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
