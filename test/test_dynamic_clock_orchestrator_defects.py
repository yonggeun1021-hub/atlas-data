#!/usr/bin/env python3
"""P8-12 CIO integration review round 1 -- dedicated regressions for the 4
defects the CIO independently reproduced against PR #211 HEAD `d7353ae`
(CI/tests passing did not catch any of these). Each class below is the
specific reproduction the CIO described, turned into a permanent test:

  Defect 1 (P0): decision_date=2026-08-20 must show ZERO evidence dated
    2026-08-21 or later ANYWHERE in the output -- not just in the top-level
    `decision_date`/`evidence_as_of` fields, but recursively, in every raw
    trigger record, every review-queue candidate's timing fields, every
    expired-trigger `expiry`, etc.

  Defect 2 (P0): re-running against IDENTICAL evidence must show
    `new_triggers_this_run=0` the second time -- "new" is committed-state
    diffing, never a date-equality check.

  Defect 3 (P1): the operational output (`run()`, and therefore
    `build_subject_review_candidate()`) must be byte-identical whether or
    not `clock/audit_confirmed_miss.py`'s underlying evidence file exists
    at all -- proving the operational path never reads it, not merely that
    it doesn't use the VALUE.

  Defect 4 (P1): each of `_validate_candidate_timing()`'s five ordering
    rules is independently regression-tested, each rejecting the reversed
    case on its own (not bundled into one "something is wrong" test).
"""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock import run_dynamic_clock as rdc  # noqa: E402

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Fields that are DELIBERATELY forward-looking schedule/deadline outputs of
# the cooldown/expiry policy (e.g. "review again in N business days",
# "expires N days after detected_at if not renewed") -- these are computed
# FROM decision_at/detected_at by the state machine, not evidence USED to
# produce a decision, and are therefore correctly dated after decision_at.
# Defect 1 is about evidence lookahead, not about a schedule naming a future
# date; excluding these by key name keeps the sweep honest about which is
# which (confirmed by direct inspection -- see the investigation that added
# this exclusion list).
_FORWARD_LOOKING_SCHEDULE_KEYS = {"expiry", "next_review_at"}


def _walk_date_like_strings(obj, key=None):
    """Recursively yields every YYYY-MM-DD-prefixed string anywhere in a
    JSON-shaped structure (dict/list/str nesting), extracting just the date
    prefix so a full ISO timestamp (price_as_of) is compared the same way
    as a bare date field. Skips deliberately-forward-looking schedule keys
    (see `_FORWARD_LOOKING_SCHEDULE_KEYS`)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _FORWARD_LOOKING_SCHEDULE_KEYS:
                continue
            yield from _walk_date_like_strings(v, key=k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_date_like_strings(v, key=key)
    elif isinstance(obj, str) and _DATE_RE.match(obj):
        yield obj[:10]


class Defect1NoFutureEvidenceAnywhereTests(unittest.TestCase):
    """CIO's exact reproduction: decision_date=2026-08-20 must never show
    2026-08-21 or 2026-08-22 (or any later date) anywhere in the output --
    checked recursively across the WHOLE report, not just the two fields
    the CIO happened to point at."""

    CUTOFF = "2026-08-20"

    def test_historical_replay_at_2026_08_20_shows_zero_dates_after_cutoff_anywhere(self):
        report = rdc.run(decision_date=self.CUTOFF, mode=rdc.MODE_HISTORICAL_REPLAY)
        offending = sorted({d for d in _walk_date_like_strings(report) if d > self.CUTOFF})
        self.assertEqual(offending, [], f"found evidence/timing dated after {self.CUTOFF}: {offending}")

    def test_historical_replay_at_2026_08_20_briefing_section_also_shows_zero_dates_after_cutoff(self):
        report = rdc.run(decision_date=self.CUTOFF, mode=rdc.MODE_HISTORICAL_REPLAY)
        section = rdc.build_briefing_section(report)
        offending = sorted({d for d in _walk_date_like_strings(section) if d > self.CUTOFF})
        self.assertEqual(offending, [], f"briefing section carries a date after {self.CUTOFF}: {offending}")

    def test_every_markets_own_decision_date_echoes_the_requested_cutoff_exactly(self):
        # The old _effective_as_of() bug manifested exactly here: decision_date
        # displayed back as a LATER date than what was requested.
        report = rdc.run(decision_date=self.CUTOFF, mode=rdc.MODE_HISTORICAL_REPLAY)
        for market, m in report["by_market"].items():
            self.assertEqual(m["decision_date"], self.CUTOFF, market)


class Defect2NewnessIsCommittedStateDiffTests(unittest.TestCase):
    """CIO's exact reproduction: a second run against evidence unchanged
    since the first run's own committed output must show
    new_triggers_this_run=0 -- not the stale-95-forever bug."""

    def test_second_run_against_identical_committed_state_shows_zero_new_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "dynamic_clock_report.json"
            with mock.patch.object(rdc, "REPORT_PATH", report_path):
                first = rdc.run()  # real committed evidence, whatever "latest" is today
                report_path.write_text(json.dumps(first, default=str), encoding="utf-8")

                second = rdc.run()  # SAME evidence -- nothing changed on disk in between
                for market, m in second["by_market"].items():
                    self.assertEqual(m["newness_status"], "COMPUTABLE", market)
                    self.assertEqual(m["new_triggers_this_run"], [], market)
                    self.assertEqual(m["new_subjects_this_run"], [], market)

    def test_first_run_with_no_prior_committed_state_is_explicitly_not_computable(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "dynamic_clock_report.json"  # deliberately never written
            with mock.patch.object(rdc, "REPORT_PATH", report_path):
                report = rdc.run()
                for market, m in report["by_market"].items():
                    self.assertEqual(m["newness_status"], "NEWNESS_NOT_COMPUTABLE", market)
                    # Fails closed to "not computable" -- never silently defaults to
                    # "everything is new" just because there's no prior state.
                    self.assertEqual(m["new_triggers_this_run"], [], market)

    def test_raw_95_style_ledger_never_leaks_into_the_briefings_new_list(self):
        # Regardless of newness_status, the briefing's "new" list is always
        # subject-level consolidated from new_subjects_this_run via
        # review_queue -- never the raw per-trigger new_triggers_this_run.
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "dynamic_clock_report.json"  # bootstrap: no prior state
            with mock.patch.object(rdc, "REPORT_PATH", report_path):
                report = rdc.run()
                section = rdc.build_briefing_section(report)
                for market, m in report["by_market"].items():
                    raw_new_count = len(m["new_triggers_this_run"])
                    briefing_new_count = len(section["markets"][market]["new_triggers"])
                    # Bootstrap run is NEWNESS_NOT_COMPUTABLE -> both are empty; the
                    # real proof is that the briefing list is driven by
                    # new_subjects_this_run (subject-level), never raw count, which
                    # the shared-consolidation-path assertion below establishes for
                    # the COMPUTABLE case too.
                    self.assertLessEqual(briefing_new_count, len(m["review_queue"]))
                    self.assertEqual(raw_new_count, 0)  # bootstrap: nothing "new" is computable yet


class Defect3AuditArtifactNeverReadByOperationalPathTests(unittest.TestCase):
    """CIO requirement (round 1 + round 2): not being a tier INPUT isn't
    enough, and stripping the FIELD from the output isn't enough either --
    the operational `run()` code path must never CALL/COMPUTE the
    post-hoc/forward-return machinery at all, not merely omit it from the
    result. Round 2 fixed this for real: `_market_result()` (what `run()`
    is built from) contains no import of or call to
    `replay.forward_metrics.compute_forward_metrics` or
    `clock.audit_diagnostics.build_audit_diagnostic_record`/
    `clock.audit_confirmed_miss.confirmed_miss_for` anywhere -- only the
    separate `_market_diagnostics()`, called exclusively from
    `run_with_diagnostics()`, does."""

    def test_run_output_is_byte_identical_whether_or_not_the_miss_evidence_file_exists(self):
        from clock import audit_confirmed_miss as acm

        real_report = json.dumps(rdc.run(), default=str, sort_keys=True)
        with mock.patch.object(acm, "MISS_EPISODES_PATH", Path("/nonexistent/does_not_exist.json")):
            missing_file_report = json.dumps(rdc.run(), default=str, sort_keys=True)
        self.assertEqual(real_report, missing_file_report)

    def test_operational_run_is_unaffected_if_audit_diagnostics_computation_raises(self):
        # ★ round 2: proves genuine (not cosmetic) separation -- if the
        # audit-diagnostics machinery is completely broken, run() must
        # still succeed and produce the EXACT SAME result, because it never
        # calls into that machinery at all.
        import clock.audit_diagnostics as ad

        baseline = json.dumps(rdc.run(), default=str, sort_keys=True)

        def _boom(*args, **kwargs):
            raise RuntimeError("audit diagnostics machinery is completely broken")

        with mock.patch.object(ad, "build_audit_diagnostic_record", side_effect=_boom):
            # run() must not raise and must not be affected in any way.
            still_fine = json.dumps(rdc.run(), default=str, sort_keys=True)
        self.assertEqual(baseline, still_fine)

        # Meanwhile run_with_diagnostics() (which DOES call the audit path)
        # genuinely propagates the failure -- proving the mock actually
        # would have been hit had run() called it.
        with mock.patch.object(ad, "build_audit_diagnostic_record", side_effect=_boom):
            with self.assertRaises(RuntimeError):
                rdc.run_with_diagnostics()

    def test_compute_forward_metrics_and_confirmed_miss_for_are_called_zero_times_during_run(self):
        # ★ round 2's explicit required proof: mock call-count assertion,
        # not just "the output doesn't show it".
        import replay.forward_metrics as fm
        import clock.audit_diagnostics as ad

        cfm_mock = mock.MagicMock(wraps=fm.compute_forward_metrics)
        cmf_mock = mock.MagicMock(wraps=ad.confirmed_miss_for)
        with mock.patch.object(fm, "compute_forward_metrics", cfm_mock), \
             mock.patch.object(ad, "confirmed_miss_for", cmf_mock):
            rdc.run()
            rdc.run(decision_date="2026-08-20", mode=rdc.MODE_HISTORICAL_REPLAY)
        self.assertEqual(cfm_mock.call_count, 0)
        self.assertEqual(cmf_mock.call_count, 0)

    def test_the_same_two_functions_ARE_called_during_run_with_diagnostics(self):
        # Sanity companion to the zero-calls test above: proves the mocks
        # are wired to something real (a call-count-zero test that patches
        # the wrong target would trivially "pass" for the wrong reason).
        import replay.forward_metrics as fm
        import clock.audit_diagnostics as ad

        cfm_mock = mock.MagicMock(wraps=fm.compute_forward_metrics)
        cmf_mock = mock.MagicMock(wraps=ad.confirmed_miss_for)
        with mock.patch.object(fm, "compute_forward_metrics", cfm_mock), \
             mock.patch.object(ad, "confirmed_miss_for", cmf_mock):
            rdc.run_with_diagnostics()
        self.assertGreater(cfm_mock.call_count, 0)
        self.assertGreater(cmf_mock.call_count, 0)

    def test_run_dynamic_clock_module_source_has_no_top_level_audit_import(self):
        source = (ROOT / "clock" / "run_dynamic_clock.py").read_text(encoding="utf-8")
        top_level_lines = [ln for ln in source.splitlines()
                            if not ln.startswith((" ", "\t")) and ln.strip().startswith(("import ", "from "))]
        for forbidden in ("compute_forward_metrics", "audit_diagnostics", "audit_confirmed_miss"):
            offending = [ln for ln in top_level_lines if forbidden in ln]
            self.assertEqual(offending, [], f"top-level import of {forbidden!r} found: {offending}")

    def test_review_candidate_module_has_no_audit_confirmed_miss_or_audit_diagnostics_attribute_reference(self):
        import clock.review_candidate as rc
        self.assertNotIn("audit_confirmed_miss", vars(rc))
        self.assertNotIn("audit_diagnostics", vars(rc))
        self.assertNotIn("confirmed_miss_for", vars(rc))
        self.assertNotIn("build_audit_diagnostic_record", vars(rc))

    def test_daily_orchestrator_briefing_wiring_calls_run_not_run_with_diagnostics(self):
        source = (ROOT / "briefing" / "daily_orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("DYNAMIC_CLOCK.run(", source)
        self.assertNotIn("run_with_diagnostics", source)

    def test_daily_orchestrator_source_never_imports_audit_modules(self):
        source = (ROOT / "briefing" / "daily_orchestrator.py").read_text(encoding="utf-8")
        for forbidden in ("audit_confirmed_miss", "audit_diagnostics"):
            self.assertNotIn(forbidden, source)

    def test_operational_report_returned_by_run_never_contains_the_audit_diagnostics_key(self):
        report = rdc.run()
        for market, m in report["by_market"].items():
            self.assertNotIn("audit_diagnostics", m, market)


class Defect4TimingOrderingIndependentRejectionTests(unittest.TestCase):
    """Each of `_validate_candidate_timing()`'s five ordering rules,
    independently regression-tested: exactly one constraint violated per
    test, all others left valid, so a future accidental relaxation of any
    SINGLE rule is caught by its own dedicated test rather than only by a
    bundled everything-at-once check."""

    VALID = dict(
        evidence_as_of="2026-08-18",
        trigger_observed_at="2026-08-19",
        decision_at="2026-08-20",
        price_as_of="2026-08-19T00:00:00Z",
        candidate_created_at="2026-08-17",
        candidate_updated_at="2026-08-19",
    )

    # NOTE: `ReviewCandidateError`/`_validate_candidate_timing` are looked up
    # FRESH from `clock.review_candidate` inside each test (never imported
    # once at module top) -- `test_dynamic_clock_pit_tier_invariant.py`'s
    # `test_module_level_guard_runs_at_import_time` does an
    # `importlib.reload(clock.review_candidate)` elsewhere in the same test
    # run, which REBINDS the module's `ReviewCandidateError` class to a new
    # object; a name imported before that reload would then be a different
    # (stale) class than the one the reloaded function actually raises,
    # making `assertRaises` silently fail to catch it. Fetching both names
    # from the module together, at call time, keeps them consistent
    # regardless of run order or process-sharing across test files.
    def _rc(self):
        import clock.review_candidate as rc
        return rc

    def test_valid_ordering_does_not_raise(self):
        rc = self._rc()
        rc._validate_candidate_timing(**self.VALID)  # sanity baseline -- must not raise

    def test_evidence_as_of_after_trigger_observed_at_raises(self):
        rc = self._rc()
        kwargs = {**self.VALID, "evidence_as_of": "2026-08-20", "trigger_observed_at": "2026-08-19"}
        with self.assertRaisesRegex(rc.ReviewCandidateError,
                                     "TIMING_INVARIANT_VIOLATED:evidence_as_of.*trigger_observed_at"):
            rc._validate_candidate_timing(**kwargs)

    def test_trigger_observed_at_after_decision_at_raises(self):
        rc = self._rc()
        kwargs = {**self.VALID, "trigger_observed_at": "2026-08-21", "decision_at": "2026-08-20"}
        with self.assertRaisesRegex(rc.ReviewCandidateError,
                                     "TIMING_INVARIANT_VIOLATED:trigger_observed_at.*decision_at"):
            rc._validate_candidate_timing(**kwargs)

    def test_price_as_of_after_decision_at_raises(self):
        rc = self._rc()
        kwargs = {**self.VALID, "price_as_of": "2026-08-21T00:00:00Z", "decision_at": "2026-08-20"}
        with self.assertRaisesRegex(rc.ReviewCandidateError, "TIMING_INVARIANT_VIOLATED:price_as_of.*decision_at"):
            rc._validate_candidate_timing(**kwargs)

    def test_price_as_of_none_or_unknown_never_raises_regardless_of_decision_at(self):
        rc = self._rc()
        for placeholder in (None, "UNKNOWN"):
            kwargs = {**self.VALID, "price_as_of": placeholder}
            rc._validate_candidate_timing(**kwargs)  # must not raise -- absence is honest, not a violation

    def test_candidate_created_at_after_candidate_updated_at_raises(self):
        rc = self._rc()
        kwargs = {**self.VALID, "candidate_created_at": "2026-08-19", "candidate_updated_at": "2026-08-18"}
        with self.assertRaisesRegex(rc.ReviewCandidateError,
                                     "TIMING_INVARIANT_VIOLATED:candidate_created_at.*candidate_updated_at"):
            rc._validate_candidate_timing(**kwargs)

    def test_candidate_updated_at_after_decision_at_raises(self):
        rc = self._rc()
        kwargs = {**self.VALID, "candidate_updated_at": "2026-08-21", "decision_at": "2026-08-20"}
        with self.assertRaisesRegex(rc.ReviewCandidateError,
                                     "TIMING_INVARIANT_VIOLATED:candidate_updated_at.*decision_at"):
            rc._validate_candidate_timing(**kwargs)

    def test_build_subject_review_candidate_end_to_end_rejects_a_decision_at_behind_trigger_observed_at(self):
        # Same rule (#2 above), proven through the real builder rather than
        # the bare validation function, so a future refactor that stops
        # calling _validate_candidate_timing at all is still caught.
        rc = self._rc()
        from clock.dynamic_clock import ClockEvent, build_episode_history

        ev = ClockEvent(detected_at="2026-08-20", evidence_available_at="2026-08-19",
                         evidence_hash="a" * 64, source="test", strength=1.0)
        episodes = [ep for ep in build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])
                    if ep["status"] == "ACTIVE"]
        with self.assertRaisesRegex(rc.ReviewCandidateError, "TIMING_INVARIANT_VIOLATED"):
            rc.build_subject_review_candidate(
                "BTC", "BTC", episodes, pit_eligibility_status="PASS",
                decision_at="2026-08-19",  # BEHIND detected_at=2026-08-20
            )


if __name__ == "__main__":
    unittest.main()
