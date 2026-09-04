#!/usr/bin/env python3
"""Korea Leadership Live Proof workflow structural regression (2026-09-04).

Offline YAML structure checks only -- no KRX call, no tracked-file
mutation. Confirms the CIO-approved bounded P2-03 cadence slice: a real
weekday schedule that reuses korea-market-signals.yml's own established
evening cadence, a scheduled run resolving its trading-date pair via
korea_market_signals.py's discover_session_pair() (never a fabricated
"today"), the manual workflow_dispatch input path left completely
unchanged, the pre-existing --verify-existing-only idempotency path
reused before any provider call, and no touch to korea_leadership.py /
korea_capital_rotation.py's own calculation, threshold, or state
vocabulary.
"""
from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "korea-leadership-live-proof.yml"


class LeadershipLiveProofWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        with WORKFLOW.open(encoding="utf-8") as stream:
            self.workflow = yaml.safe_load(stream)
        self.job = self.workflow["jobs"]["korea-leadership-live-fetch"]
        self.steps_by_name = {
            step["name"]: step for step in self.job["steps"] if "name" in step
        }

    def test_manual_dispatch_inputs_unchanged(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertIn("workflow_dispatch", triggers)
        inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"prior_date", "current_date"})
        for spec in inputs.values():
            self.assertTrue(spec["required"])

    def test_schedule_reuses_korea_market_signals_evening_cadence(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertIn("schedule", triggers)
        crons = {entry["cron"] for entry in triggers["schedule"]}
        # Exact same two evening slots korea-market-signals.yml already
        # uses for its own post-18:00 KST recovery-capable cadence -- not
        # an invented time.
        self.assertEqual(crons, {"10 9 * * 1-5", "25 9 * * 1-5"})

    def test_concurrency_serializes_the_two_schedule_slots(self):
        concurrency = self.workflow.get("concurrency")
        self.assertIsNotNone(concurrency)
        self.assertEqual(concurrency["group"], "korea-leadership-live-proof")
        self.assertFalse(concurrency["cancel-in-progress"])

    def test_manual_path_binds_inputs_directly_no_resolver_call(self):
        step = self.steps_by_name["Bind manual workflow_dispatch trading dates"]
        self.assertEqual(step["if"], "github.event_name == 'workflow_dispatch'")
        self.assertIn("PRIOR_DATE_INPUT", step["env"])
        self.assertIn("CURRENT_DATE_INPUT", step["env"])
        self.assertEqual(step["env"]["PRIOR_DATE_INPUT"], "${{ inputs.prior_date }}")
        self.assertEqual(step["env"]["CURRENT_DATE_INPUT"], "${{ inputs.current_date }}")
        self.assertIn("PRIOR_DATE=$PRIOR_DATE_INPUT", step["run"])
        self.assertIn("CURRENT_DATE=$CURRENT_DATE_INPUT", step["run"])
        self.assertIn("$GITHUB_ENV", step["run"])

    def test_schedule_path_reuses_discover_session_pair_unchanged(self):
        step = self.steps_by_name[
            "Discover completed KRX trading-date pair for a scheduled run"
        ]
        self.assertEqual(step["if"], "github.event_name == 'schedule'")
        self.assertIn("KRX_API_KEY", step["env"])
        run = step["run"]
        # Reuses the real existing resolver module and function, never a
        # newly authored calendar/holiday policy.
        self.assertIn("korea_market_signals.py", run)
        self.assertIn("discover_session_pair", run)
        self.assertIn("KoreaMarketSignalsError", run)
        # Anchor is real KST wall-clock "today", never a stored/replayed
        # date -- the resolver itself walks backward from it.
        self.assertIn('ZoneInfo("Asia/Seoul")', run)
        self.assertNotIn("datetime.date(", run)

    def test_no_new_endpoint_or_calendar_file_introduced(self):
        self.assertNotIn("krx.co.kr", self.text)
        self.assertNotIn("import requests", self.text)
        self.assertNotIn("trading_calendar", self.text)
        # No new calendar/holiday config file is ever read -- the only
        # config path this workflow's steps touch is via the reused
        # korea_leadership_live_fetch.py / korea_market_signals.py
        # scripts themselves, never a literal config/*.json path inline.
        step_run_text = "\n".join(
            step.get("run", "") for step in self.job["steps"]
        )
        self.assertNotIn("config/", step_run_text)

    def test_existing_evidence_is_verified_before_any_provider_call(self):
        check = self.steps_by_name[
            "Reuse an exact committed Leadership observation instead of re-fetching the same date"
        ]
        self.assertEqual(check["id"], "existing_leadership")
        self.assertIn("--verify-existing-only", check["run"])
        self.assertIn("data/observations/korea_leadership_context", check["run"])
        for name in (
            "Korea Leadership real KRX index fetch attempt",
            "Commit Korea Leadership live-fetch attempt evidence",
        ):
            step = self.steps_by_name[name]
            self.assertEqual(step["if"], "steps.existing_leadership.outputs.exists != 'true'")

    def test_provider_fetch_still_uses_resolved_dates_verbatim(self):
        step = self.steps_by_name["Korea Leadership real KRX index fetch attempt"]
        self.assertEqual(step["env"]["PRIOR_DATE"], "${{ env.PRIOR_DATE }}")
        self.assertEqual(step["env"]["CURRENT_DATE"], "${{ env.CURRENT_DATE }}")
        self.assertIn("korea_leadership_live_fetch.py", step["run"])
        self.assertIn('--prior-date "$PRIOR_DATE"', step["run"])
        self.assertIn('--current-date "$CURRENT_DATE"', step["run"])

    def test_commit_step_resyncs_to_live_tip_and_tags_trigger_provenance(self):
        step = self.steps_by_name["Commit Korea Leadership live-fetch attempt evidence"]
        run = step["run"]
        fetch_index = run.find("git fetch origin main")
        reset_index = run.find("git reset --hard origin/main")
        add_index = run.find("git add data/observations/korea_leadership_context")
        commit_index = run.find("git commit")
        self.assertGreaterEqual(fetch_index, 0)
        self.assertGreaterEqual(reset_index, 0)
        self.assertLess(fetch_index, add_index)
        self.assertLess(reset_index, add_index)
        # NATURAL (schedule) vs MANUAL (workflow_dispatch) provenance is
        # preserved via GitHub's own real event_name, not a fabricated
        # field on the leadership packet's own schema.
        self.assertIn("[trigger=${{ github.event_name }}]", run)
        self.assertLess(commit_index, run.find("[trigger="))

    def test_commits_only_its_own_evidence_path(self):
        step = self.steps_by_name["Commit Korea Leadership live-fetch attempt evidence"]
        self.assertIn("git add data/observations/korea_leadership_context", step["run"])
        self.assertNotIn("git add data/observations/korea_breadth_context", step["run"])

    def test_no_calculation_or_state_vocabulary_files_invoked_as_targets(self):
        # This slice only ever invokes korea_leadership_live_fetch.py as
        # a command target -- it never invokes korea_leadership.py or
        # korea_capital_rotation.py directly (the prose comments above
        # may still name them to explain what stays untouched).
        step_run_text = "\n".join(
            step.get("run", "") for step in self.job["steps"]
        )
        self.assertNotIn("korea_capital_rotation.py", step_run_text)
        self.assertNotIn(".github/scripts/korea_leadership.py", step_run_text)
        self.assertIn("korea_leadership_live_fetch.py", step_run_text)


if __name__ == "__main__":
    unittest.main()
