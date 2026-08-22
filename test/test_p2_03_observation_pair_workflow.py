#!/usr/bin/env python3
"""P2-03 dependency-ordered Breadth->Leadership observation-pair workflow
structural regression (2026-08-22).

Offline YAML structure checks only -- no KRX call, no tracked-file
mutation. Confirms: still workflow_dispatch-only (no new schedule/cron),
the real job dependency chain (Leadership needs the Breadth context
commit, which needs the Breadth live-proof job) that structurally
guarantees Breadth completes and commits before Leadership starts, and
that no new fetch logic/endpoint was introduced -- every step reuses the
exact scripts already approved in the two standalone workflows.
"""
from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "p2-03-korea-observation-pair.yml"


class ObservationPairWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        with WORKFLOW.open(encoding="utf-8") as stream:
            self.workflow = yaml.safe_load(stream)

    def test_workflow_dispatch_only_no_new_schedule(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertIn("workflow_dispatch", triggers)
        self.assertNotIn("schedule", triggers)
        inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(
            set(inputs),
            {
                "breadth_recent_previous", "breadth_recent_date",
                "leadership_prior_date", "leadership_current_date",
            },
        )
        for spec in inputs.values():
            self.assertTrue(spec["required"])

    def test_real_job_dependency_chain_breadth_before_leadership(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {
            "korea-breadth-live-proof",
            "korea-breadth-context-commit",
            "korea-leadership-live-fetch",
        })
        # Breadth's own internal two-step dependency is unchanged.
        self.assertEqual(jobs["korea-breadth-context-commit"]["needs"], "korea-breadth-live-proof")
        # The real dependency this workflow adds: Leadership cannot start
        # until Breadth's context commit has genuinely landed.
        self.assertEqual(jobs["korea-leadership-live-fetch"]["needs"], "korea-breadth-context-commit")

    def test_no_new_fetch_logic_reuses_existing_scripts_verbatim(self):
        # Same scripts as the two standalone, already-approved workflows
        # -- no new endpoint, no new fetch primitive.
        self.assertIn("korea_breadth_derived_outputs.py", self.text)
        self.assertIn("korea_breadth_context_populate.py", self.text)
        self.assertIn("korea_leadership_live_fetch.py", self.text)
        self.assertNotIn("krx.co.kr", self.text)
        self.assertNotIn("import requests", self.text)
        self.assertIn("--capture-mode forward_live", self.text)

    def test_permissions_are_least_privilege_per_job(self):
        jobs = self.workflow["jobs"]
        # Read-only live-proof job writes nothing.
        self.assertNotIn("permissions", jobs["korea-breadth-live-proof"])
        # The two commit jobs need write access to push their own evidence.
        self.assertEqual(jobs["korea-breadth-context-commit"]["permissions"]["contents"], "write")
        self.assertEqual(jobs["korea-leadership-live-fetch"]["permissions"]["contents"], "write")

    def test_leadership_job_commits_only_its_own_evidence_path(self):
        # The final job's commit step must only ever stage the Leadership
        # evidence tree, never the Breadth one (that job already committed
        # its own path in the prior job).
        leadership_section = self.text.split("korea-leadership-live-fetch:")[1]
        self.assertIn("git add data/observations/korea_leadership_context", leadership_section)
        self.assertNotIn("git add data/observations/korea_breadth_context", leadership_section)

    def test_write_jobs_resync_to_live_tip_before_committing(self):
        # Real fix (2026-08-22, run 32566229770 first-dispatch failure):
        # actions/checkout resolves to the SHA fixed at workflow_dispatch
        # start for every job in the run, not main's live tip -- so a
        # downstream write job does not see an upstream job's own commit
        # that landed moments earlier in the same run, and its push is
        # rejected as non-fast-forward even though nothing conflicts. Both
        # write jobs must re-sync to origin/main's real current tip
        # immediately before staging/committing their own evidence.
        jobs = self.workflow["jobs"]
        for job_name in ("korea-breadth-context-commit", "korea-leadership-live-fetch"):
            steps = "\n".join(
                step.get("run", "") for step in jobs[job_name]["steps"] if "run" in step
            )
            fetch_index = steps.find("git fetch origin main")
            reset_index = steps.find("git reset --hard origin/main")
            add_index = steps.find("git add data/observations")
            self.assertGreaterEqual(fetch_index, 0, f"{job_name} missing re-sync fetch")
            self.assertGreaterEqual(reset_index, 0, f"{job_name} missing re-sync reset")
            self.assertLess(
                fetch_index, add_index,
                f"{job_name} must fetch the live tip before staging evidence",
            )
            self.assertLess(
                reset_index, add_index,
                f"{job_name} must reset onto the live tip before staging evidence",
            )


if __name__ == "__main__":
    unittest.main()
