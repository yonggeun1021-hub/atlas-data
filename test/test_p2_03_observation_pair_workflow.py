#!/usr/bin/env python3
"""P2-03 dependency-ordered Breadth->Leadership observation-pair workflow
structural regression (2026-08-22).

Offline YAML structure checks only -- no KRX call, no tracked-file
mutation. Confirms: still workflow_dispatch-only (no new schedule/cron),
the real job dependency chain (Leadership needs the Breadth context
commit, which needs the Breadth live-proof job, and the current-ratified
producer needs Leadership) that structurally guarantees Breadth completes
and commits before Leadership starts and the packet is built, and that no
new fetch logic/endpoint was introduced -- every step reuses the exact
scripts already approved in the existing paths.
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
            "korea-current-ratified-rotation-proof",
        })
        # Breadth's own internal two-step dependency is unchanged.
        self.assertEqual(jobs["korea-breadth-context-commit"]["needs"], "korea-breadth-live-proof")
        # The real dependency this workflow adds: Leadership cannot start
        # until Breadth's context commit has genuinely landed.
        self.assertEqual(jobs["korea-leadership-live-fetch"]["needs"], "korea-breadth-context-commit")
        # The current-ratified producer cannot run until the exact committed
        # Breadth -> Leadership pair is available on main.
        self.assertEqual(
            jobs["korea-current-ratified-rotation-proof"]["needs"],
            "korea-leadership-live-fetch",
        )

    def test_no_new_fetch_logic_reuses_existing_scripts_verbatim(self):
        # Same scripts as the two standalone, already-approved workflows
        # -- no new endpoint, no new fetch primitive.
        self.assertIn("korea_breadth_derived_outputs.py", self.text)
        self.assertIn("korea_breadth_context_populate.py", self.text)
        self.assertIn("korea_leadership_live_fetch.py", self.text)
        self.assertNotIn("krx.co.kr", self.text)
        self.assertNotIn("import requests", self.text)
        self.assertIn("--capture-mode forward_live", self.text)

    def test_same_date_committed_context_is_verified_then_skips_provider_refetch(self):
        jobs = self.workflow["jobs"]
        proof = jobs["korea-breadth-live-proof"]
        self.assertEqual(
            proof["outputs"]["context_exists"],
            "${{ steps.existing_context.outputs.exists }}",
        )
        check = next(step for step in proof["steps"] if step.get("id") == "existing_context")
        self.assertIn("--verify-existing-date", check["run"])
        self.assertIn("data/observations/korea_breadth_context", check["run"])
        provider = next(step for step in proof["steps"] if step.get("name") == "P1-KR-05 historical and recent direct proof")
        self.assertEqual(provider["if"], "steps.existing_context.outputs.exists != 'true'")
        context_job = jobs["korea-breadth-context-commit"]
        for name in (
            "Download P1-KR-05 derived output artifact",
            "Populate committed Korea Breadth context lineage",
            "Commit Korea Breadth context lineage",
        ):
            step = next(item for item in context_job["steps"] if item.get("name") == name)
            self.assertEqual(step["if"], "needs.korea-breadth-live-proof.outputs.context_exists != 'true'")

    def test_same_date_leadership_is_independently_verified_and_skips_only_its_provider(self):
        job = self.workflow["jobs"]["korea-leadership-live-fetch"]
        check = next(step for step in job["steps"] if step.get("id") == "existing_leadership")
        self.assertIn("--verify-existing-only", check["run"])
        self.assertIn("data/observations/korea_leadership_context", check["run"])
        for name in (
            "Korea Leadership real KRX index fetch attempt",
            "Commit Korea Leadership live-fetch attempt evidence",
        ):
            step = next(item for item in job["steps"] if item.get("name") == name)
            self.assertEqual(step["if"], "steps.existing_leadership.outputs.exists != 'true'")

    def test_failed_derived_capture_still_retains_its_artifact(self):
        # Real failure-path defect: the upload step carried only the
        # context_exists guard, so a failed derived capture skipped it by
        # Actions' default success() and the metadata receipt that capture
        # had just emitted died with the runner -- unrecoverable, while
        # p1-kr05-korea-breadth-live.yml already retains the same artifact
        # with always(). Both conditions must hold together: retain on
        # failure, and still never upload on the verified-existing path.
        proof = self.workflow["jobs"]["korea-breadth-live-proof"]
        upload = next(step for step in proof["steps"] if step.get("id") == "upload_derived")
        self.assertEqual(
            upload["if"],
            "always() && steps.existing_context.outputs.exists != 'true'",
        )
        # Retention only -- the artifact itself is unchanged (same name,
        # same path, same action pin), no download/retry/raw-body policy.
        self.assertEqual(
            upload["with"]["name"],
            "p1-kr05-derived-outputs-${{ github.run_id }}-${{ github.run_attempt }}",
        )
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/p1-kr05-derived")
        self.assertEqual(upload["with"]["if-no-files-found"], "warn")

    def test_verified_existing_context_still_uploads_nothing(self):
        # The existing-context reuse path produces no derived directory, so
        # the guard that suppresses its upload must survive the always()
        # change -- always() alone would upload an empty directory there.
        proof = self.workflow["jobs"]["korea-breadth-live-proof"]
        upload = next(step for step in proof["steps"] if step.get("id") == "upload_derived")
        self.assertIn("steps.existing_context.outputs.exists != 'true'", upload["if"])
        # The provider capture step keeps its own unconditional-on-success
        # guard: retention must not make the failed capture look successful.
        provider = next(
            step for step in proof["steps"]
            if step.get("name") == "P1-KR-05 historical and recent direct proof"
        )
        self.assertEqual(provider["if"], "steps.existing_context.outputs.exists != 'true'")

    def test_retained_failure_artifact_does_not_release_downstream_jobs(self):
        # Retaining evidence must stay strictly fail-closed: the live-proof
        # job still fails, and neither the master/context commit job nor
        # Leadership may opt out of that failure. No downstream job or step
        # may carry always()/failure()/cancelled(), so `needs:` keeps them
        # skipped and no same-date master/commit/Leadership is written.
        jobs = self.workflow["jobs"]
        for job_name in (
            "korea-breadth-context-commit",
            "korea-leadership-live-fetch",
            "korea-current-ratified-rotation-proof",
        ):
            job = jobs[job_name]
            self.assertNotIn("if", job, f"{job_name} must inherit its needs failure")
            for step in job["steps"]:
                for override in ("always()", "failure()", "cancelled()", "!cancelled()"):
                    self.assertNotIn(
                        override, str(step.get("if", "")),
                        f"{job_name} step must not run past an upstream failure",
                    )
        # The same-date KRX Global Master population stays inside the
        # commit job's guarded step, so it cannot run off a failed capture.
        populate = next(
            step for step in jobs["korea-breadth-context-commit"]["steps"]
            if step.get("name") == "Populate committed Korea Breadth context lineage"
        )
        self.assertIn("korea_global_universe_populate.py", populate["run"])
        self.assertEqual(
            populate["if"], "needs.korea-breadth-live-proof.outputs.context_exists != 'true'"
        )

    def test_permissions_are_least_privilege_per_job(self):
        jobs = self.workflow["jobs"]
        # Read-only live-proof job writes nothing.
        self.assertNotIn("permissions", jobs["korea-breadth-live-proof"])
        # The two commit jobs need write access to push their own evidence.
        self.assertEqual(jobs["korea-breadth-context-commit"]["permissions"]["contents"], "write")
        self.assertEqual(jobs["korea-leadership-live-fetch"]["permissions"]["contents"], "write")
        # The packet handoff is an external artifact only and never pushes.
        self.assertEqual(
            jobs["korea-current-ratified-rotation-proof"]["permissions"]["contents"],
            "read",
        )

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
