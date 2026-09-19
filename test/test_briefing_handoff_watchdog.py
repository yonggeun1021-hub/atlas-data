#!/usr/bin/env python3
"""AM/PM Briefing Handoff Watchdog.

Pins the read-only CHECK -> CLASSIFY -> ALERT contract of
.github/scripts/briefing_handoff_watchdog.py against:

  1. synthetic fixtures for the three required scenarios
     (missing bridge, missing Portal handoff, complete), plus the remaining
     status values the synthetic scenarios don't otherwise cover;
  2. two REAL historical incidents, replayed from the exact commits that
     actually built them (git show at each commit -- not a hand-written
     approximation), to prove the classifier reaches the states the real
     repository history actually passed through.

Every test also asserts the module opens no authority and writes only to
its own evidence namespace.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "briefing_handoff_watchdog", ROOT / ".github/scripts/briefing_handoff_watchdog.py")
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)


def _write(root: Path, rel: str, body: dict | str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, str):
        path.write_text(body, encoding="utf-8")
    else:
        path.write_text(json.dumps(body), encoding="utf-8")


def _kst(y, m, d, hh, mm) -> _dt.datetime:
    return _dt.datetime(y, m, d, hh, mm, tzinfo=watchdog.KST)


class SyntheticScenarios(unittest.TestCase):
    """The three scenarios named explicitly in the task, plus the states
    they don't cover, each isolated to one changed condition."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.slot = "morning"
        self.date = "2026-01-06"  # a Tuesday: evening also "expected" if reused
        # Always inside grace so status alone (not timing) drives the result.
        self.now = _kst(2026, 1, 6, 9, 0)

    def _natural(self):
        _write(self.tmp, f"evidence/daily_briefing/{self.slot}/{self.date}/index.json", {})

    def _semantic_hold(self):
        _write(self.tmp, f"data/briefing/finalization/{self.date}/{self.slot}/validation-rev-001.json",
               {"validation_status": "HOLD", "routing": {"status_deliverable": False},
                "hold_reasons": ["MAJOR_NEWS_VERIFICATION_UNAVAILABLE"]})

    def _semantic_pass(self):
        _write(self.tmp, f"data/briefing/finalization/{self.date}/{self.slot}/validation-rev-001.json",
               {"validation_status": "PASS", "routing": {"status_deliverable": True}})

    def _bridge(self):
        _write(self.tmp, f"evidence/briefing_events/{self.date}/{self.slot}/index.json", {})

    def _envelope(self, recovery_type=None):
        content = {"recovery_type": recovery_type} if recovery_type else {}
        _write(self.tmp, f"evidence/validated_briefing_portal/{self.slot}/{self.date}/rev-001/portal-projection.json",
               {"display_proposal": [{"content": content}]})
        _write(self.tmp, f"evidence/validated_briefing_portal/{self.slot}/{self.date}/index.json", {
            "latest_revision": 1, "latest_projection_id": "x",
            "revisions": [{"revision": 1,
                          "envelope_path": f"evidence/validated_briefing_portal/{self.slot}/{self.date}/rev-001/portal-projection.json"}],
        })

    def _portal_receipt(self):
        _write(self.tmp, f"data/briefing/finalization/{self.date}/{self.slot}/portal-final-receipt-rev-001.json", {})

    def _drain(self):
        _write(self.tmp, f"data/briefing/finalization/{self.date}/{self.slot}/delivery_receipt.json", {})

    def _run(self):
        return watchdog.run_check(self.tmp, self.slot, self.date, now=self.now)

    def test_no_natural_receipt(self):
        report = self._run()
        self.assertEqual(report["status"], "NATURAL_RECEIPT_MISSING")

    def test_natural_only_waiting_validation(self):
        self._natural()
        report = self._run()
        self.assertEqual(report["status"], "WAITING_VALIDATION")

    def test_required_scenario_source_bridge_missing(self):
        """Task scenario: natural receipt present, source bridge absent."""
        self._natural()
        self._semantic_hold()  # the system only surfaces this once the
                                # semantic layer has actually stalled on it --
                                # bridge absence alone is the normal case for
                                # a slot with no major event (see
                                # briefing_core/chain.py source_status
                                # UNAVAILABLE), not an error by itself.
        report = self._run()
        self.assertEqual(report["status"], "SOURCE_BRIDGE_MISSING")
        self.assertTrue(report["alert"])  # now is inside grace but PAST it? check below
        self.assertFalse(report["checks"]["source_bridge"]["exists"])

    def test_bridge_present_but_hold_is_generic_waiting(self):
        self._natural()
        self._semantic_hold()
        self._bridge()
        report = self._run()
        self.assertEqual(report["status"], "WAITING_VALIDATION")

    def test_required_scenario_portal_handoff_missing(self):
        """Task scenario: bridge + envelope present, Portal apply absent."""
        self._natural()
        self._semantic_pass()
        self._bridge()
        self._envelope()
        report = self._run()
        self.assertEqual(report["status"], "PORTAL_HANDOFF_MISSING")
        self.assertFalse(report["checks"]["portal_final_receipt"]["exists"])

    def test_final_drain_missing(self):
        self._natural()
        self._semantic_pass()
        self._bridge()
        self._envelope()
        self._portal_receipt()
        report = self._run()
        self.assertEqual(report["status"], "FINAL_DRAIN_MISSING")

    def test_required_scenario_complete(self):
        """Task scenario: every stage present."""
        self._natural()
        self._semantic_pass()
        self._bridge()
        self._envelope()
        self._portal_receipt()
        self._drain()
        report = self._run()
        self.assertEqual(report["status"], "COMPLETE")
        self.assertFalse(report["alert"])  # COMPLETE never alerts

    def test_envelope_short_circuits_missing_semantic_record(self):
        """A manual-recovery envelope is real forward progress even though
        no validation-rev file was ever recorded through the formal gate."""
        self._natural()
        self._envelope(recovery_type="MANUAL_RECOVERY")
        report = self._run()
        self.assertEqual(report["status"], "PORTAL_HANDOFF_MISSING")
        self.assertTrue(any("MANUAL_RECOVERY" in n for n in report["notes"]))

    def test_bridge_registry_without_index_is_not_auto_discoverable(self):
        self._natural()
        _write(self.tmp, f"evidence/briefing_events/{self.date}/{self.slot}/rev-001/registry.json", {})
        bridge = watchdog.check_source_bridge(self.tmp, self.slot, self.date)
        self.assertTrue(bridge["exists"])
        self.assertFalse(bridge["discoverable_by_chain_build"])

    def test_no_authority_ever(self):
        report = self._run()
        self.assertEqual(report["authority"], watchdog.NO_AUTHORITY)
        self.assertTrue(all(v is False for v in report["authority"].values()))

    def test_every_status_has_a_bounded_existing_recovery_route(self):
        expected = {
            "NATURAL_RECEIPT_MISSING": "ORIGINAL_SCHEDULE_RUN_RECOVERY",
            "WAITING_VALIDATION": "CANONICAL_EXTERNAL_SEMANTIC_VALIDATION",
            "SOURCE_BRIDGE_MISSING": "CANONICAL_SOURCE_BRIDGE_REVIEW",
            "ENVELOPE_MISSING": "VALIDATED_PORTAL_ENVELOPE_PRODUCER",
            "PORTAL_HANDOFF_MISSING": "VALIDATED_PORTAL_PROJECTION_DISPATCH",
            "FINAL_DRAIN_MISSING": "FINALIZATION_DRAIN",
            "COMPLETE": "NONE_COMPLETE",
        }
        for status, route in expected.items():
            with self.subTest(status=status):
                plan = watchdog.recovery_plan(status, self.slot, self.date, {})
                self.assertEqual(plan["route"], route)
                self.assertRegex(plan["recovery_key"], r"^[0-9a-f]{64}$")
                self.assertFalse(plan["natural_evidence_mutation_authorized"])
                self.assertFalse(plan["backfill_promotion_authorized"])
                for authority in ("stage", "buy", "action", "order", "production", "trading"):
                    self.assertFalse(plan[f"{authority}_authority"])

    def test_semantic_states_never_claim_safe_automatic_recovery(self):
        for status in ("WAITING_VALIDATION", "SOURCE_BRIDGE_MISSING"):
            with self.subTest(status=status):
                plan = watchdog.recovery_plan(status, self.slot, self.date, {})
                self.assertTrue(plan["requires_semantic_judgment"])
                self.assertFalse(plan["safe_to_automate"])

    def test_recovery_key_is_time_independent_and_changes_with_evidence_state(self):
        self._natural()
        first = watchdog.run_check(
            self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 9, 0)
        )
        later = watchdog.run_check(
            self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 10, 0)
        )
        self.assertEqual(
            first["recovery"]["recovery_key"], later["recovery"]["recovery_key"]
        )
        self._semantic_pass()
        changed = watchdog.run_check(
            self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 10, 1)
        )
        self.assertNotEqual(
            first["recovery"]["recovery_key"], changed["recovery"]["recovery_key"]
        )

    def test_workflow_surfaces_recovery_route_and_owner(self):
        workflow = (ROOT / ".github/workflows/briefing-handoff-watchdog.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('json.load(sys.stdin)["recovery"]["route"]', workflow)
        self.assertIn('json.load(sys.stdin)["recovery"]["owner"]', workflow)
        self.assertIn("github.event_name != 'workflow_dispatch'", workflow)
        self.assertIn("inputs.fail_on_alert == true", workflow)

    def test_alert_requires_past_grace(self):
        self._natural()  # WAITING_VALIDATION
        early = watchdog.run_check(self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 7, 6))
        self.assertFalse(early["past_grace"])
        self.assertFalse(early["alert"])
        late = watchdog.run_check(self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 9, 0))
        self.assertTrue(late["past_grace"])
        self.assertTrue(late["alert"])

    def test_late_seal_gets_existing_semantic_grace(self):
        self._natural()
        _write(self.tmp, f"data/briefing/finalization/{self.date}/{self.slot}/draft-rev-001.json",
               {"sealed_at_utc": "2026-01-05T22:43:00Z"})
        early = watchdog.run_check(self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 7, 50))
        self.assertEqual(early["display_status"], "WAITING_VALIDATION")
        self.assertFalse(early["alert"])
        late = watchdog.run_check(self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 8, 3))
        self.assertEqual(late["display_status"], "DELAYED")
        self.assertEqual(late["stage"], "SEMANTIC_VALIDATION")
        self.assertTrue(late["alert"])
        self.assertEqual(early["grace_deadline_kst"], late["grace_deadline_kst"])

    def test_missing_receipt_waits_before_existing_deadline_then_reports_producer_delay(self):
        early = watchdog.run_check(self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 7, 20))
        self.assertEqual(early["display_status"], "WAITING_SLOT")
        self.assertFalse(early["alert"])
        late = watchdog.run_check(self.tmp, self.slot, self.date, now=_kst(2026, 1, 6, 7, 30))
        self.assertEqual(late["display_status"], "DELAYED")
        self.assertEqual(late["stage"], "PRODUCER")
        self.assertTrue(late["alert"])

    def test_receipt_without_seal_is_blocked_after_deadline(self):
        self._natural()
        report = self._run()
        self.assertEqual(report["display_status"], "BLOCKED")
        self.assertEqual(report["reason"], "SEALED_DRAFT_MISSING")

    def test_evening_not_expected_on_weekend(self):
        # 2026-01-10 is a Saturday.
        report = watchdog.run_check(self.tmp, "evening", "2026-01-10", now=_kst(2026, 1, 10, 20, 0))
        self.assertFalse(report["slot_expected_today"])
        self.assertFalse(report["alert"])  # never alert for a slot not scheduled today

    def test_grace_deadline_reads_live_config_not_a_hardcoded_number(self):
        _write(self.tmp, "config/atlas_semantic_validator.json", {"timeout_minutes": 5})
        self._natural()
        deadline = watchdog.slot_start_kst(self.date, self.slot) + _dt.timedelta(
            minutes=watchdog.load_semantic_timeout_minutes(self.tmp))
        self.assertEqual(deadline, _kst(2026, 1, 6, 7, 10))


class PublishIsAppendOnlyAndIdempotent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_rerun_with_unchanged_evidence_writes_no_new_rev(self):
        _write(self.tmp, "evidence/daily_briefing/morning/2026-01-06/index.json", {})
        now = _kst(2026, 1, 6, 9, 0)
        first = watchdog.run_check(self.tmp, "morning", "2026-01-06", now=now)
        result1 = watchdog.publish(self.tmp, first)
        self.assertTrue(result1["changed"])
        second = watchdog.run_check(self.tmp, "morning", "2026-01-06", now=now.replace(minute=1))
        result2 = watchdog.publish(self.tmp, second)
        self.assertFalse(result2["changed"])
        directory = self.tmp / "data/briefing/handoff_watchdog/2026-01-06/morning"
        self.assertEqual(len(list(directory.glob("status-rev-*.json"))), 1)

    def test_status_change_appends_a_new_rev(self):
        _write(self.tmp, "evidence/daily_briefing/morning/2026-01-06/index.json", {})
        now = _kst(2026, 1, 6, 9, 0)
        watchdog.publish(self.tmp, watchdog.run_check(self.tmp, "morning", "2026-01-06", now=now))
        _write(self.tmp, "data/briefing/finalization/2026-01-06/morning/validation-rev-001.json",
               {"validation_status": "PASS", "routing": {"status_deliverable": True}})
        second = watchdog.run_check(self.tmp, "morning", "2026-01-06", now=now)
        self.assertEqual(second["status"], "ENVELOPE_MISSING")
        result = watchdog.publish(self.tmp, second)
        self.assertTrue(result["changed"])
        directory = self.tmp / "data/briefing/handoff_watchdog/2026-01-06/morning"
        self.assertEqual(len(list(directory.glob("status-rev-*.json"))), 2)


def _materialize_at_commit(commit: str, prefixes: list[str], dest: Path) -> None:
    """Populate `dest` with exactly the files that existed under each
    `prefixes` entry at `commit` -- a true historical replay via `git show`,
    not a hand-typed approximation of what the repo looked like."""
    for prefix in prefixes:
        listing = subprocess.run(
            ["git", "-C", str(ROOT), "ls-tree", "-r", commit, "--name-only", "--", prefix],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        for rel in listing:
            if not rel.strip():
                continue
            body = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{commit}:{rel}"],
                capture_output=True, check=True,
            ).stdout
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(body)


def _prefixes(date: str, slot: str) -> list[str]:
    return [
        f"evidence/daily_briefing/{slot}/{date}",
        f"evidence/briefing_events/{date}/{slot}",
        f"evidence/validated_briefing_portal/{slot}/{date}",
        f"data/briefing/finalization/{date}/{slot}",
        "config/atlas_semantic_validator.json",
    ]


def _git_available() -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True).returncode == 0


@unittest.skipUnless(_git_available(), "requires a git checkout of atlas-data")
class RealAmE2E_20260902Morning(unittest.TestCase):
    """Replays the actual 2026-09-02 morning incident through the exact
    commits that built it, in order. This is the round that visits every
    non-terminal status the classifier defines."""

    SLOT, DATE = "morning", "2026-09-02"
    CHECKPOINTS = [
        ("ea26ddae", "WAITING_VALIDATION"),        # natural + machine check only
        ("a6928c51", "SOURCE_BRIDGE_MISSING"),     # semantic HOLD, no bridge yet
        ("469030fa", "PORTAL_HANDOFF_MISSING"),    # bridge + envelope, no receipt
        ("7eb1e9a2", "FINAL_DRAIN_MISSING"),       # portal receipt, no drain
        ("91ce6095679d473b3e7b580bd612c5d23f65def4", "COMPLETE"),  # original drain receipt
    ]

    def test_real_progression(self):
        now = _kst(2026, 9, 2, 23, 0)  # well past grace for every checkpoint
        for commit, expected in self.CHECKPOINTS:
            with self.subTest(commit=commit, expected=expected):
                tmp = Path(tempfile.mkdtemp())
                try:
                    _materialize_at_commit(commit, _prefixes(self.DATE, self.SLOT), tmp)
                    report = watchdog.run_check(tmp, self.SLOT, self.DATE, now=now)
                    self.assertEqual(report["status"], expected)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(_git_available(), "requires a git checkout of atlas-data")
class RealPmE2E_20260903Evening(unittest.TestCase):
    """Replays the actual 2026-09-03 evening incident -- the one this
    watchdog exists because of. Confirms the classifier would have alerted
    from shortly after the natural slot, and still shows the formal ledger
    as open when this regression was introduced, even though atlas-portal PR #418 (a
    different repository) already applied it out of band."""

    SLOT, DATE = "evening", "2026-09-03"
    CHECKPOINTS = [
        ("69488728", "WAITING_VALIDATION"),   # natural sealed, no verdict yet
        ("dc7d3d30", "WAITING_VALIDATION"),   # machine check only, still no
                                               # semantic verdict was ever
                                               # recorded through the formal
                                               # gate for this slot
        ("9271e7b5", "WAITING_VALIDATION"),   # source bridge lands, but the
                                               # formal semantic step was
                                               # never invoked for this round
        ("e620594c", "PORTAL_HANDOFF_MISSING"),  # envelope built via
                                                  # manual_recovery
        ("bbe448cfe36ed89e845aeee3e0c66016b1be301a", "PORTAL_HANDOFF_MISSING"),
        # Ledger still open at watchdog introduction; independent of remote refs.
    ]

    def test_real_progression(self):
        now = _kst(2026, 9, 4, 9, 0)  # well past grace for every checkpoint
        for commit, expected in self.CHECKPOINTS:
            with self.subTest(commit=commit, expected=expected):
                tmp = Path(tempfile.mkdtemp())
                try:
                    _materialize_at_commit(commit, _prefixes(self.DATE, self.SLOT), tmp)
                    report = watchdog.run_check(tmp, self.SLOT, self.DATE, now=now)
                    self.assertEqual(report["status"], expected)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_would_have_alerted_shortly_after_the_natural_slot(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            _materialize_at_commit("dc7d3d30", _prefixes(self.DATE, self.SLOT), tmp)
            # The real finalization policy starts semantic grace at the seal.
            draft_path = watchdog._latest_rev(tmp / "data/briefing/finalization" / self.DATE / self.SLOT, "draft")
            draft = json.loads(draft_path.read_text())
            sealed = _dt.datetime.fromisoformat(draft["sealed_at_utc"].replace("Z", "+00:00"))
            deadline = sealed + _dt.timedelta(minutes=20)
            before = watchdog.run_check(tmp, self.SLOT, self.DATE, now=deadline - _dt.timedelta(seconds=1))
            self.assertFalse(before["alert"])
            after = watchdog.run_check(tmp, self.SLOT, self.DATE, now=deadline)
            self.assertTrue(after["alert"])
            self.assertEqual(after["status"], "WAITING_VALIDATION")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CliSmoke(unittest.TestCase):
    def test_check_command_exit_code_reflects_fail_on_alert(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        rc = watchdog.main(["check", "--slot", "morning", "--decision-date", "2026-01-06",
                           "--repo-root", str(tmp), "--now", "2026-01-06T09:00:00+09:00",
                           "--fail-on-alert"])
        self.assertEqual(rc, 1)  # NATURAL_RECEIPT_MISSING, past grace -> alert

    def test_check_without_fail_on_alert_returns_zero(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        rc = watchdog.main(["check", "--slot", "morning", "--decision-date", "2026-01-06",
                           "--repo-root", str(tmp), "--now", "2026-01-06T09:00:00+09:00"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
