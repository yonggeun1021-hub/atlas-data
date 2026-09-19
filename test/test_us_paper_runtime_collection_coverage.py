#!/usr/bin/env python3
"""US PAPER runtime collection coverage: the producer must say how far behind the
capture it read is, and block past a bound taken from the committed history.

The incident this covers, measured: the decision evaluated at
2026-09-18T01:28:17Z read the 2026-09-15T23:39:00Z free-market-data capture,
because the collector's 2026-09-16 and 2026-09-17 scheduled runs produced no
committed capture.  The selection was correct -- that was the newest capture in
the repository at that instant -- but nothing in the published packet said the
collection was behind, and its two computed axes were stamped with the
RELEASE_CYCLE_LATEST_FETCH freshness form, which the ratified rule reports FRESH
for any DEFINED factor.

Every assertion is coverage-based: the unit is a collector cadence date (cron
"35 21 * * 0-5" -> Sunday..Friday), never an elapsed wall-clock day.  A Sunday
evaluation reading Friday's capture is two days and zero cadence dates behind and
must stay green.

Offline.  Reads only committed evidence; every evaluation instant is fixed.
"""

from __future__ import annotations

import datetime as dt
import glob
import importlib.util
import json
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import us_paper_runtime as RUNTIME  # noqa: E402
from regime import us_paper_runtime_publication as PUBLICATION  # noqa: E402

_spec = importlib.util.spec_from_file_location("us_runtime_coverage_fixtures",
                                               ROOT / "test" / "test_us_paper_runtime.py")
FIXTURES = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FIXTURES)

COLLECTOR = ROOT / ".github" / "workflows" / "free-market-data.yml"
DECISION_EVIDENCE = ROOT / "evidence" / "regime" / "us_paper_runtime"
CODE = "3" * 40

# The incident, by its committed bytes.
INCIDENT_EVALUATION_AT = "2026-09-18T01:28:17Z"
INCIDENT_SELECTED_OBSERVED_AT = "2026-09-15T23:39:00Z"
# The earlier committed collector failure, and the capture that closed the
# 2026-09-18 incident window by landing just after the evaluation instant.
# Anchors for membership assertions -- never the whole expected population,
# which grows every time the collector runs or a decision is published.
INCIDENT_PRIOR_EVALUATION_AT = "2026-09-15T22:51:56Z"
INCIDENT_NEXT_OBSERVED_AT = "2026-09-18T01:41:42Z"
INCIDENT_SELECTED_PATH = ("evidence/free_market_data/derived/2026-09-15/"
                          "2ea258a1ceda03df2c6f112c6fb0da34218779691d48316ce2c132a2919a67e0/manifest.json")
INCIDENT_EVIDENCE = (DECISION_EVIDENCE / "calendar-unknown-2026-09-18"
                     / "762d500c2307806e2d4ac9b7a2216c31ecbe85825f85ba2c9a07607f1a6ca6d1.json")

BEHIND = "US_FREE_MARKET_DATA_COLLECTION_BEHIND_SOURCE"
UNMEASURED = "US_FREE_MARKET_DATA_COLLECTION_COVERAGE_UNMEASURED"


def instant(text: str) -> dt.datetime:
    return RUNTIME.instant(text, "EVALUATION_TIME_INVALID")


def coverage_at(evaluation_at: str, index=None) -> dict:
    index = PUBLICATION.capture_index(ROOT) if index is None else index
    now = instant(evaluation_at)
    usable = [row["observed_at"] for row in index
              if row["observed_at"] is not None and row["observed_at"] <= now]
    return PUBLICATION.cadence_coverage(index, max(usable, default=None), now)


class CadenceBindingTest(unittest.TestCase):
    """The cadence is the collector's own committed schedule, not a local guess."""

    def test_cadence_constants_are_the_committed_collector_schedule(self):
        workflow = yaml.safe_load(COLLECTOR.read_text(encoding="utf-8"))
        triggers = workflow.get("on", workflow.get(True)) or {}
        crons = [item["cron"] for item in triggers["schedule"]]
        self.assertEqual(crons, [PUBLICATION.SOURCE_CADENCE_CRON])
        minute, hour, dom, month, weekday = PUBLICATION.SOURCE_CADENCE_CRON.split()
        self.assertEqual((dom, month), ("*", "*"))
        self.assertEqual(int(hour), PUBLICATION.SOURCE_CADENCE_UTC_HOUR)
        self.assertEqual(int(minute), PUBLICATION.SOURCE_CADENCE_UTC_MINUTE)
        # cron day-of-week 0-5 is Sunday..Friday; date.weekday() is Monday..Sunday 0..6.
        first, last = (int(value) for value in weekday.split("-"))
        expected = tuple((value - 1) % 7 for value in range(first, last + 1))
        self.assertEqual(PUBLICATION.SOURCE_CADENCE_WEEKDAYS, expected)
        self.assertNotIn(5, PUBLICATION.SOURCE_CADENCE_WEEKDAYS, "Saturday is not a cadence date")
        self.assertEqual(PUBLICATION.SOURCE_CADENCE_WORKFLOW,
                         COLLECTOR.relative_to(ROOT).as_posix())

    def test_a_capture_is_attributed_to_the_slot_that_asked_for_it(self):
        # The committed 2026-09-18T01:41:42Z capture is the 2026-09-17T21:35Z slot
        # delayed past midnight UTC; it must not count as a 2026-09-18 capture.
        self.assertEqual(PUBLICATION.cadence_date(instant("2026-09-18T01:41:42Z")), dt.date(2026, 9, 17))
        self.assertEqual(PUBLICATION.cadence_date(instant("2026-09-15T23:39:00Z")), dt.date(2026, 9, 15))
        self.assertEqual(PUBLICATION.cadence_date(instant("2026-09-15T21:35:00Z")), dt.date(2026, 9, 15))
        self.assertEqual(PUBLICATION.cadence_date(instant("2026-09-15T21:34:59Z")), dt.date(2026, 9, 14))
        # Saturday and Sunday-before-the-slot both fall back to Friday.
        self.assertEqual(PUBLICATION.cadence_date(instant("2026-09-12T12:00:00Z")), dt.date(2026, 9, 11))
        self.assertEqual(PUBLICATION.cadence_date(instant("2026-09-13T12:00:00Z")), dt.date(2026, 9, 11))
        self.assertEqual(PUBLICATION.cadence_date(instant("2026-09-13T21:35:00Z")), dt.date(2026, 9, 13))


class CoverageMeasureTest(unittest.TestCase):
    """Coverage, not elapsed wall-clock -- the distinction #799 got wrong."""

    @classmethod
    def setUpClass(cls):
        cls.index = PUBLICATION.capture_index(ROOT)

    def test_sunday_reading_fridays_capture_is_two_days_and_zero_cadence_dates_behind(self):
        friday = instant("2026-09-11T21:41:28Z")     # committed capture
        for moment, elapsed_days in (("2026-09-12T12:00:00Z", 1),   # Saturday: not a cadence date
                                     ("2026-09-13T12:00:00Z", 2),   # Sunday before the 21:35Z slot
                                     ("2026-09-13T21:55:00Z", 2),   # Sunday primary producer slot
                                     ("2026-09-13T23:40:00Z", 2)):  # Sunday retry producer slot
            with self.subTest(moment=moment):
                result = PUBLICATION.cadence_coverage(self.index, friday, instant(moment))
                # A day-count bound would already be red here; a coverage bound is not.
                self.assertEqual((instant(moment).date() - friday.date()).days, elapsed_days)
                self.assertEqual(result["uncovered_cadence_dates"], [])
                self.assertEqual(result["status"], RUNTIME.SOURCE_CURRENT)

    def test_normal_next_day_operation_is_never_behind(self):
        # Every committed capture, read back at the producer's own two slots on
        # that capture's cadence date, is SOURCE_CURRENT.
        observed = sorted(row["observed_at"] for row in self.index if row["observed_at"] is not None)
        self.assertGreater(len(observed), 10)
        for moment in observed:
            day = PUBLICATION.cadence_date(moment)
            for hour, minute in ((21, 55), (23, 40)):
                slot = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=dt.timezone.utc)
                if slot < moment:
                    continue
                with self.subTest(capture=moment.isoformat(), slot=slot.isoformat()):
                    result = PUBLICATION.cadence_coverage(self.index, moment, slot)
                    self.assertEqual(result["uncovered_cadence_dates"], [])
                    self.assertEqual(result["status"], RUNTIME.SOURCE_CURRENT)

    def test_the_in_flight_cadence_date_is_granted_one_cycle_of_grace(self):
        # 2026-09-17T01:41:54Z read the 2026-09-15 capture while 2026-09-16 was
        # still the evaluation's own cadence date: in flight, not yet behind.
        # This is the one committed decision the bound deliberately lets pass.
        early = coverage_at("2026-09-17T01:41:54Z", self.index)
        self.assertEqual(early["evaluated_cadence_date"], "2026-09-16")
        self.assertEqual(early["uncovered_cadence_dates"], [])
        self.assertEqual(early["status"], RUNTIME.SOURCE_CURRENT)
        # Once the next slot passes, the same missing capture is reported -- one
        # cadence cycle before the 2026-09-18 evaluation that surfaced it.
        for moment in ("2026-09-17T21:55:00Z", "2026-09-17T23:40:00Z", "2026-09-18T01:28:17Z"):
            with self.subTest(moment=moment):
                later = coverage_at(moment, self.index)
                self.assertEqual(later["uncovered_cadence_dates"], ["2026-09-16"])
                self.assertEqual(later["status"], RUNTIME.COLLECTION_BEHIND_SOURCE)


class CommittedHistoryBoundTest(unittest.TestCase):
    """The bound is the maximum over the healthy committed population, recomputed."""

    @classmethod
    def setUpClass(cls):
        cls.index = PUBLICATION.capture_index(ROOT)
        cls.packets = []
        for path in sorted(glob.glob(str(DECISION_EVIDENCE / "*" / "*.json"))):
            packet = json.loads(Path(path).read_bytes())
            source = (packet.get("latest_source_diagnostic") or {}).get("source") or {}
            if source.get("observed_at_utc"):
                cls.packets.append((packet["evaluation_at"], source["observed_at_utc"]))
        cls.packets.sort()

    def test_committed_collector_failures_are_the_only_non_zero_scores(self):
        self.assertGreaterEqual(len(self.packets), 7, "committed decision history")
        scores = {}
        for evaluation_at, observed_at in self.packets:
            result = PUBLICATION.cadence_coverage(self.index, instant(observed_at), instant(evaluation_at))
            scores[evaluation_at] = result["uncovered_cadence_date_count"]
        behind = {key: value for key, value in scores.items() if value}
        # The two committed collector failures this module documents each score
        # exactly one uncovered cadence date.  Asserted by membership, not as the
        # whole dict: evidence/regime/us_paper_runtime gains a packet per
        # decision, so an exact-dict compare would have to be hand-edited the
        # next time the collector fails -- the very event this test exists to
        # make visible, turned into a test edit instead of a signal.  The
        # "only non-zero" half of the property is carried without a snapshot by
        # max(healthy) below and by
        # test_both_non_zero_scores_name_a_cadence_date_with_no_committed_capture,
        # which holds *every* non-zero score to a genuinely uncovered cadence
        # date -- so a spurious non-zero score still fails, it just fails there.
        for evaluation_at in (INCIDENT_PRIOR_EVALUATION_AT, INCIDENT_EVALUATION_AT):
            with self.subTest(evaluation_at=evaluation_at):
                self.assertEqual(behind.get(evaluation_at), 1)
        healthy = [value for key, value in scores.items() if key not in behind]
        self.assertGreaterEqual(len(healthy), 5, "non-degenerate healthy population")
        self.assertEqual(max(healthy), PUBLICATION.TOLERATED_UNCOVERED_CADENCE_DATES)

    def test_both_non_zero_scores_name_a_cadence_date_with_no_committed_capture(self):
        covered = {PUBLICATION.cadence_date(row["observed_at"]).isoformat()
                   for row in self.index if row["observed_at"] is not None}
        for evaluation_at, observed_at in self.packets:
            result = PUBLICATION.cadence_coverage(self.index, instant(observed_at), instant(evaluation_at))
            for date in result["uncovered_cadence_dates"]:
                with self.subTest(evaluation_at=evaluation_at, date=date):
                    self.assertNotIn(date, covered)
                    self.assertIn(dt.date.fromisoformat(date).weekday(), PUBLICATION.SOURCE_CADENCE_WEEKDAYS)


class IncidentTest(unittest.TestCase):
    """The 2026-09-18 packet, and what the fixed producer says about it."""

    @classmethod
    def setUpClass(cls):
        cls.index = PUBLICATION.capture_index(ROOT)

    def test_the_selection_itself_was_correct_and_no_capture_failed_validation(self):
        # Not a validation fallback: every committed capture is readable, so the
        # producer did not refuse a newer packet -- there was none.
        self.assertEqual([row["path"] for row in self.index if row["error"] is not None], [])
        record = PUBLICATION.latest_source_record(ROOT, self.index, instant(INCIDENT_EVALUATION_AT))
        self.assertEqual(record["revision_path"], INCIDENT_SELECTED_PATH)
        self.assertEqual(record["observed_at_utc"], INCIDENT_SELECTED_OBSERVED_AT)
        # Why the selection was correct, stated as a property of the capture
        # index rather than as a snapshot of it: the selected capture was the
        # newest one in the repository at the evaluation instant, so *every*
        # capture newer than it must postdate the evaluation.  That stays true
        # however many captures the collector commits later.  Pinning the list
        # to its one-element value would instead have gone stale on the
        # collector's next healthy run (cron "35 21 * * 0-5", Sun..Fri) -- a
        # staleness bomb inside the module that exists to make staleness visible.
        newer = [row["observed_at"] for row in self.index
                 if row["observed_at"] is not None and row["observed_at"] > instant(INCIDENT_SELECTED_OBSERVED_AT)]
        self.assertTrue(newer, "the next capture landed after evaluation_at")
        for observed_at in newer:
            with self.subTest(observed_at=observed_at.isoformat()):
                self.assertGreater(observed_at, instant(INCIDENT_EVALUATION_AT))
        # The capture that closed the incident window is still one of them.
        self.assertIn(instant(INCIDENT_NEXT_OBSERVED_AT), newer)

    def test_the_next_healthy_collector_run_does_not_change_these_conclusions(self):
        """Guard the guard: a later capture must not turn the incident tests red.

        The collector commits a new capture on every healthy ``35 21 * * 0-5``
        run.  Simulated in memory only -- nothing is written or committed -- so
        the assertions above are checked against a repository state that has not
        happened yet instead of only against today's.
        """
        template = max((row for row in self.index if row["observed_at"] is not None),
                       key=lambda row: row["observed_at"])
        future_at = instant("2026-09-18T21:35:00Z")
        index = self.index + [dict(template, path="evidence/free_market_data/derived/"
                                                  "2026-09-18/simulated/manifest.json",
                                   observed_at=future_at)]
        # The incident's selection is unchanged: the new capture postdates it.
        record = PUBLICATION.latest_source_record(ROOT, index, instant(INCIDENT_EVALUATION_AT))
        self.assertEqual(record["observed_at_utc"], INCIDENT_SELECTED_OBSERVED_AT)
        newer = [row["observed_at"] for row in index
                 if row["observed_at"] is not None and row["observed_at"] > instant(INCIDENT_SELECTED_OBSERVED_AT)]
        self.assertIn(future_at, newer)
        for observed_at in newer:
            with self.subTest(observed_at=observed_at.isoformat()):
                self.assertGreater(observed_at, instant(INCIDENT_EVALUATION_AT))
        # And the incident still scores exactly one uncovered cadence date:
        # 2026-09-16 holds no capture whatever lands on 2026-09-18.
        coverage = PUBLICATION.cadence_coverage(
            index, instant(INCIDENT_SELECTED_OBSERVED_AT), instant(INCIDENT_EVALUATION_AT))
        self.assertEqual(coverage["uncovered_cadence_dates"], ["2026-09-16"])

    def test_the_committed_packet_recorded_the_input_but_not_the_skew(self):
        packet = json.loads(INCIDENT_EVIDENCE.read_bytes())
        diagnostic = packet["latest_source_diagnostic"]
        self.assertEqual(packet["evaluation_at"], INCIDENT_EVALUATION_AT)
        # It did say which capture it read ...
        self.assertEqual(diagnostic["source"]["revision_path"], INCIDENT_SELECTED_PATH)
        self.assertEqual(diagnostic["source"]["observed_at_utc"], INCIDENT_SELECTED_OBSERVED_AT)
        # ... and said nothing about being behind, in the packet a consumer gates on.
        self.assertNotIn("collection_coverage", diagnostic)
        self.assertEqual(packet["reasons"], ["US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT",
                                             "US_PIT_ACCEPTED_RECORD_UNBOUND",
                                             "US_OFFICIAL_SESSION_CALENDAR_UNBOUND"])
        # The two axes it did compute were positively asserted fresh.
        for axis in ("RISK_VOL", "LIQUIDITY"):
            self.assertEqual(diagnostic["axis_observations"][axis]["freshness_form"],
                             RUNTIME.SEMANTIC.RELEASE_CYCLE_LATEST_FETCH)

    def test_the_fixed_producer_states_the_skew_and_blocks(self):
        record = PUBLICATION.latest_source_record(ROOT, self.index, instant(INCIDENT_EVALUATION_AT))
        coverage = record["collection_coverage"]
        self.assertEqual(coverage["measure"], PUBLICATION.COVERAGE_MEASURE)
        self.assertEqual(coverage["cadence_cron"], PUBLICATION.SOURCE_CADENCE_CRON)
        self.assertEqual(coverage["cadence_declared_in"], PUBLICATION.SOURCE_CADENCE_WORKFLOW)
        self.assertEqual(coverage["selected_capture_cadence_date"], "2026-09-15")
        self.assertEqual(coverage["evaluated_cadence_date"], "2026-09-17")
        self.assertEqual(coverage["uncovered_cadence_dates"], ["2026-09-16"])
        self.assertEqual(coverage["uncovered_cadence_date_count"], 1)
        self.assertEqual(coverage["tolerated_uncovered_cadence_dates"], 0)
        self.assertEqual(coverage["status"], RUNTIME.COLLECTION_BEHIND_SOURCE)
        packet = RUNTIME.evaluate_us_paper_runtime(
            evaluation_at=INCIDENT_EVALUATION_AT, code_revision=CODE, session_records={},
            latest_source_record=record, evidence_class=RUNTIME.LIVE_NATURAL, root=ROOT)
        self.assertIn(BEHIND, packet["reasons"])
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        # The packet keeps saying which capture it read, next to why it is behind.
        published = packet["latest_source_diagnostic"]
        self.assertEqual(published["source"]["revision_path"], INCIDENT_SELECTED_PATH)
        self.assertEqual(published["collection_coverage"], coverage)


class FailClosedTest(unittest.TestCase):
    """An unmeasured or self-contradicting block never reads as current."""

    @classmethod
    def setUpClass(cls):
        cls.index = PUBLICATION.capture_index(ROOT)
        cls.record = PUBLICATION.latest_source_record(ROOT, cls.index, instant("2026-09-11T22:00:00Z"))
        cls.fixture = FIXTURES.Fixture(write_adoption=False)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def evaluate(self, record, evaluation_at="2026-09-11T22:00:00Z"):
        return RUNTIME.evaluate_us_paper_runtime(
            evaluation_at=evaluation_at, code_revision=CODE, session_records={},
            latest_source_record=record, evidence_class=RUNTIME.LIVE_NATURAL, root=self.fixture.root)

    def test_a_current_capture_adds_no_reason(self):
        self.assertEqual(self.record["collection_coverage"]["status"], RUNTIME.SOURCE_CURRENT)
        packet = self.evaluate(self.record)
        self.assertNotIn(BEHIND, packet["reasons"])
        self.assertNotIn(UNMEASURED, packet["reasons"])

    def test_a_missing_block_blocks(self):
        record = {key: value for key, value in self.record.items() if key != "collection_coverage"}
        packet = self.evaluate(record)
        self.assertIn(UNMEASURED, packet["reasons"])
        self.assertEqual(packet["latest_source_diagnostic"]["collection_coverage"],
                         {"status": RUNTIME.COLLECTION_COVERAGE_UNMEASURED})

    def test_a_block_claiming_current_against_its_own_count_blocks(self):
        forged = dict(self.record)
        forged["collection_coverage"] = {**self.record["collection_coverage"],
                                         "uncovered_cadence_dates": ["2026-09-16", "2026-09-17"],
                                         "uncovered_cadence_date_count": 2,
                                         "status": RUNTIME.SOURCE_CURRENT}
        self.assertIn(UNMEASURED, self.evaluate(forged)["reasons"])

    def test_a_count_that_disagrees_with_its_own_list_blocks(self):
        forged = dict(self.record)
        forged["collection_coverage"] = {**self.record["collection_coverage"],
                                         "uncovered_cadence_date_count": 7}
        self.assertIn(UNMEASURED, self.evaluate(forged)["reasons"])

    def test_an_unknown_status_or_extra_field_blocks(self):
        for edit in ({"status": "PROBABLY_FINE"}, {"surprise": True}):
            with self.subTest(edit=edit):
                forged = dict(self.record)
                forged["collection_coverage"] = {**self.record["collection_coverage"], **edit}
                self.assertIn(UNMEASURED, self.evaluate(forged)["reasons"])

    def test_no_usable_capture_is_missing_and_behind_not_silently_current(self):
        record = PUBLICATION.latest_source_record(ROOT, self.index, instant("2026-08-01T00:00:00Z"))
        self.assertEqual(record["error"], "SESSION_SOURCE_MISSING")
        self.assertIsNone(record["collection_coverage"]["selected_capture_cadence_date"])
        self.assertEqual(record["collection_coverage"]["status"], RUNTIME.COLLECTION_BEHIND_SOURCE)
        packet = self.evaluate(record, "2026-08-01T00:00:00Z")
        self.assertIn(BEHIND, packet["reasons"])

    def test_an_unverifiable_newest_capture_blocks_instead_of_an_older_one(self):
        index = list(self.index)
        index.append({"path": "evidence/free_market_data/derived/2026-09-11/broken/manifest.json",
                      "observed_at": None, "session_date": None, "error": "PACKET_SHA256_MISMATCH"})
        record = PUBLICATION.latest_source_record(ROOT, index, instant("2026-09-11T22:00:00Z"))
        self.assertEqual(record["error"], "SESSION_SOURCE_LATEST_CAPTURE_UNVERIFIABLE")
        self.assertIn("collection_coverage", record)
        packet = self.evaluate(record)
        self.assertIn("SESSION_SOURCE_LATEST_CAPTURE_UNVERIFIABLE",
                      packet["latest_source_diagnostic"]["reasons"])


class PublishedPacketTest(unittest.TestCase):
    """The producer's own output carries the measurement on every build."""

    def setUp(self):
        self.fixture = FIXTURES.Fixture(write_adoption=False)

    def tearDown(self):
        self.fixture.close()

    def test_every_build_carries_a_coverage_block_and_the_basis_moves_with_it(self):
        current = PUBLICATION.build_decision(evaluation_at="2026-09-11T22:00:00Z", code_revision=CODE,
                                             root=self.fixture.root, evidence_root=ROOT)
        behind = PUBLICATION.build_decision(evaluation_at=INCIDENT_EVALUATION_AT, code_revision=CODE,
                                            root=self.fixture.root, evidence_root=ROOT)
        self.assertEqual(current["latest_source_diagnostic"]["collection_coverage"]["status"],
                         RUNTIME.SOURCE_CURRENT)
        self.assertEqual(behind["latest_source_diagnostic"]["collection_coverage"]["status"],
                         RUNTIME.COLLECTION_BEHIND_SOURCE)
        self.assertNotIn(BEHIND, current["reasons"])
        self.assertIn(BEHIND, behind["reasons"])
        self.assertNotEqual(current["basis_sha256"], behind["basis_sha256"])
        # No new top-level field: the decision contract is untouched.
        contract = json.loads((ROOT / RUNTIME.CONTRACT_RELATIVE).read_bytes())
        self.assertEqual(sorted(behind), sorted(contract["decision"]["required_fields"]))
        self.assertEqual(behind["authority"], RUNTIME.AUTHORITY_CLOSED)

    def test_the_reason_survives_a_full_adoption_so_the_stale_read_fails_closed(self):
        # With every gate satisfied, a behind-source capture must still block
        # rather than classify a regime off an old packet.
        fixture = FIXTURES.Fixture()
        try:
            packet = FIXTURES.evaluate(fixture.root, evaluation_at=FIXTURES.EVAL)
            self.assertEqual(packet["latest_source_diagnostic"]["collection_coverage"]["status"],
                             RUNTIME.SOURCE_CURRENT)
            self.assertNotIn(BEHIND, packet["reasons"])
            stale = PUBLICATION.latest_source_record(ROOT, PUBLICATION.capture_index(ROOT),
                                                     instant(INCIDENT_EVALUATION_AT))
            blocked = FIXTURES.evaluate(fixture.root, evaluation_at=FIXTURES.EVAL, latest=stale)
            self.assertIn(BEHIND, blocked["reasons"])
            self.assertEqual(blocked["decision_status"], "BLOCKED")
            self.assertEqual(blocked["runtime_regime"], "UNKNOWN")
            self.assertFalse(blocked["authority"]["paper_runtime_display_authorized"])
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()
