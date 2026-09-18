#!/usr/bin/env python3
"""Korea Leadership schedule/controller structural regression.

Offline YAML structure checks only -- no KRX call, no tracked-file
mutation. Confirms the CIO-approved bounded P2-03 cadence slice: a real
weekday schedule that reuses korea-market-signals.yml's established evening
cadence, resolves trading dates without inventing a calendar, synchronously
calls the existing dependency-ordered pair workflow, validates the final
artifact before dedupe, and preserves standalone manual Leadership behavior.

Also covers the Korea five-signal pointer producer this workflow hosts:
data/latest_korea_market_signals.json had no automated writer, so the KR
population observation's session-date source froze at 2026-09-10. The
structural checks below prove the pointer is produced and committed here,
that a partial fetch commits nothing, and that a repeat run for an already
committed session does not rewrite it. The one end-to-end check reads
already-committed repository data and writes only to a temporary directory
-- still no KRX call and no tracked-file mutation.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "korea-leadership-live-proof.yml"
POINTER_JOB = "publish-scheduled-five-signal-pointer"
RATIFIED_PRODUCER = ".github/scripts/korea_market_signals.py"
POINTER_PATH = "data/latest_korea_market_signals.json"
REVIEW_PATH = "data/latest_korea_symbol_market_review.json"
KR_OBSERVATION = ROOT / "decision" / "korea_population_symbol_observation.py"


def _load_producer():
    spec = importlib.util.spec_from_file_location(
        "korea_market_signals_for_pointer_regression", ROOT / RATIFIED_PRODUCER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LeadershipLiveProofWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        with WORKFLOW.open(encoding="utf-8") as stream:
            self.workflow = yaml.safe_load(stream)
        self.job = self.workflow["jobs"]["korea-leadership-live-fetch"]
        self.prepare = self.workflow["jobs"]["prepare-scheduled-observation-pair"]
        self.prepare_steps = {
            step["name"]: step for step in self.prepare["steps"] if "name" in step
        }
        self.steps_by_name = {
            step["name"]: step for step in self.job["steps"] if "name" in step
        }
        self.seed = self.workflow["jobs"]["seed-effective-date-leadership"]
        self.seed_steps = {
            step["name"]: step for step in self.seed["steps"] if "name" in step
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
        self.assertEqual(self.job["if"], "github.event_name == 'workflow_dispatch'")
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
        self.assertEqual(self.prepare["if"], "github.event_name == 'schedule'")
        step = self.prepare_steps["Discover completed KRX trading-date pair"]
        self.assertIn("KRX_API_KEY", step["env"])
        run = step["run"]
        # Reuses the real existing resolver module and function, never a
        # newly authored calendar/holiday policy.
        self.assertIn("korea_market_signals.py", run)
        self.assertIn("discover_session_pair", run)
        self.assertIn("KoreaMarketSignalsError", run)
        self.assertIn("ready=false", run)
        self.assertNotIn("raise SystemExit", run)
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
            step.get("run", "")
            for job in self.workflow["jobs"].values()
            for step in job.get("steps", [])
        )
        self.assertNotIn("config/", step_run_text)

    def test_schedule_calls_one_existing_reusable_workflow_synchronously(self):
        call = self.workflow["jobs"]["scheduled-observation-pair"]
        self.assertEqual(call["needs"], "prepare-scheduled-observation-pair")
        self.assertEqual(
            call["if"],
            "needs.prepare-scheduled-observation-pair.outputs.should_call == 'true'",
        )
        self.assertEqual(
            call["uses"], "./.github/workflows/p2-03-korea-observation-pair.yml"
        )
        self.assertEqual(
            call["with"]["breadth_recent_previous"],
            call["with"]["leadership_prior_date"],
        )
        self.assertEqual(
            call["with"]["breadth_recent_date"],
            call["with"]["leadership_current_date"],
        )
        self.assertEqual(call["secrets"], "inherit")

    def test_dedupe_requires_exact_final_artifact_not_green_run(self):
        load = self.prepare_steps["Load prior controller and manual-pair runs"]["run"]
        wait = self.prepare_steps["Wait for an already-active exact request"]["run"]
        verify = self.prepare_steps[
            "Accept dedupe only from an exact validated final artifact"
        ]["run"]
        decision = self.prepare_steps["Decide whether to call the combined workflow"]["run"]
        self.assertIn("korea-leadership-live-proof.yml/runs", load)
        self.assertIn("p2-03-korea-observation-pair.yml/runs", load)
        self.assertIn("successful-candidates.tsv", load)
        self.assertIn("gh run watch", wait)
        self.assertIn("did not reach a terminal state", wait)
        self.assertIn("gh run download", verify)
        self.assertIn("verify-handoff", verify)
        self.assertIn("EXACT_FINAL_ROTATION_HANDOFF_ALREADY_VALIDATED", decision)
        self.assertNotIn("github.event.schedule", decision)

    def test_policy_and_missing_input_wait_do_not_claim_natural_completion(self):
        readiness = self.prepare_steps[
            "Check policy effectivity and existing-pair chronology"
        ]["run"]
        decision = self.prepare_steps["Decide whether to call the combined workflow"]["run"]
        self.assertIn("request-readiness", readiness)
        self.assertIn("INPUT_NOT_READY", decision)
        self.assertIn("REQUEST_NOT_READY", decision)
        self.assertIn("SHOULD_CALL=false", decision)

    def test_effective_date_seed_uses_resolved_pair_without_new_schedule(self):
        self.assertEqual(self.seed["needs"], "prepare-scheduled-observation-pair")
        self.assertEqual(
            self.seed["if"],
            "needs.prepare-scheduled-observation-pair.outputs.seed_current_leadership == 'true'",
        )
        self.assertEqual(
            self.seed["env"]["PRIOR_DATE"],
            "${{ needs.prepare-scheduled-observation-pair.outputs.prior_date }}",
        )
        self.assertEqual(
            self.seed["env"]["CURRENT_DATE"],
            "${{ needs.prepare-scheduled-observation-pair.outputs.current_date }}",
        )
        readiness = self.prepare_steps[
            "Check policy effectivity and existing-pair chronology"
        ]["run"]
        self.assertIn("seed_current_leadership", readiness)

    def test_effective_date_seed_verifies_before_fetch_and_commits_only_leadership(self):
        existing = self.seed_steps[
            "Reuse an exact committed effective-date Leadership observation"
        ]
        self.assertIn("--verify-existing-only", existing["run"])
        fetch = self.seed_steps[
            "Korea Leadership effective-date real KRX index fetch attempt"
        ]
        commit = self.seed_steps["Commit effective-date Korea Leadership evidence"]
        self.assertEqual(fetch["if"], "steps.existing_leadership.outputs.exists != 'true'")
        self.assertEqual(commit["if"], "steps.existing_leadership.outputs.exists != 'true'")
        self.assertIn("git add data/observations/korea_leadership_context", commit["run"])
        self.assertNotIn("korea_breadth_context", commit["run"])

    def test_effective_date_seed_confirms_usable_seed_readiness_after_commit(self):
        step_names = [step["name"] for step in self.seed["steps"] if "name" in step]
        # Ordering: the opt-in readiness check runs last, after both the
        # reuse check and the conditional fetch+commit -- so a fresh
        # attempt's evidence is preserved/committed before readiness is
        # ever distinguished, and a reused attempt is re-checked too.
        self.assertEqual(
            step_names[-4:],
            [
                "Reuse an exact committed effective-date Leadership observation",
                "Korea Leadership effective-date real KRX index fetch attempt",
                "Commit effective-date Korea Leadership evidence",
                "Confirm effective-date Leadership seed usable-seed readiness",
            ],
        )
        readiness = self.seed_steps["Confirm effective-date Leadership seed usable-seed readiness"]
        # Never gated behind steps.existing_leadership.outputs.exists --
        # reused evidence is subject to this final check too.
        self.assertNotIn("if", readiness)
        # A faithfully preserved BLOCKED attempt is an expected, honest
        # outcome, not a workflow failure -- this step must never turn
        # the scheduled job red for that alone.
        self.assertTrue(readiness.get("continue-on-error"))
        run = readiness["run"]
        self.assertIn("korea_leadership_live_fetch.py", run)
        self.assertIn("--verify-existing-only", run)
        self.assertIn("--require-usable-seed", run)
        self.assertIn('--prior-date "$PRIOR_DATE"', run)
        self.assertIn('--current-date "$CURRENT_DATE"', run)

    def test_called_failure_propagates_and_success_handoff_is_reverified(self):
        verify = self.workflow["jobs"]["verify-scheduled-observation-pair-handoff"]
        self.assertEqual(
            set(verify["needs"]),
            {"prepare-scheduled-observation-pair", "scheduled-observation-pair"},
        )
        # No always(): a failed reusable producer prevents this verifier and
        # keeps the scheduled controller run failed.
        self.assertNotIn("always()", verify["if"])
        body = "\n".join(step.get("run", "") for step in verify["steps"])
        self.assertIn("verify-handoff", body)
        download = next(
            step for step in verify["steps"]
            if step.get("name") == "Download this run's final rotation handoff"
        )
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}", download["with"]["name"])

    def test_permissions_and_concurrency_do_not_create_recursive_deadlock(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read", "actions": "read"})
        self.assertEqual(
            self.prepare["permissions"], {"contents": "read", "actions": "read"}
        )
        self.assertEqual(
            self.workflow["jobs"]["verify-scheduled-observation-pair-handoff"]["permissions"],
            {"contents": "read", "actions": "read"},
        )
        self.assertEqual(
            self.workflow["jobs"]["scheduled-observation-pair"]["permissions"],
            {"contents": "write", "actions": "read"},
        )
        self.assertEqual(
            self.job["permissions"], {"contents": "write"}
        )
        self.assertEqual(self.seed["permissions"], {"contents": "write"})
        self.assertEqual(self.workflow["concurrency"]["group"], "korea-leadership-live-proof")
        pair_text = (
            ROOT / ".github" / "workflows" / "p2-03-korea-observation-pair.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("group: korea-leadership-live-proof", pair_text)
        with (
            ROOT / ".github" / "workflows" / "p2-03-korea-observation-pair.yml"
        ).open(encoding="utf-8") as stream:
            pair = yaml.safe_load(stream)
        for job in pair["jobs"].values():
            self.assertNotEqual(
                job.get("uses"),
                "./.github/workflows/korea-leadership-live-proof.yml",
            )

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


class FiveSignalPointerProducerTest(unittest.TestCase):
    """The host workflow is the five-signal pointer's automated writer."""

    def setUp(self):
        with WORKFLOW.open(encoding="utf-8") as stream:
            self.workflow = yaml.safe_load(stream)
        self.job = self.workflow["jobs"][POINTER_JOB]
        self.names = [step["name"] for step in self.job["steps"] if "name" in step]
        self.steps = {step["name"]: step for step in self.job["steps"] if "name" in step}
        self.reuse = self.steps[
            "Reuse an exact committed five-signal observation instead of re-fetching the same session"
        ]
        self.fetch = self.steps["Korea five-signal real KRX session-pair fetch attempt"]
        self.verify = self.steps[
            "Verify the produced pointer, its session couplings, and its closed authority boundary"
        ]
        self.commit = self.steps["Commit the five-signal pointer and its bounded review"]

    def test_pointer_is_produced_and_committed_by_this_workflow(self):
        # Writer, not artifact packager: the job needs contents: write and
        # commits the tracked pointer plus its append-only dated packet.
        self.assertEqual(self.job["permissions"], {"contents": "write"})
        self.assertIn(RATIFIED_PRODUCER, self.fetch["run"])
        run = self.commit["run"]
        self.assertIn(f"git add {POINTER_PATH}", run)
        self.assertIn("data/observations/korea_market_signals", run)
        self.assertIn(REVIEW_PATH, run)
        self.assertIn("evidence/korea_symbol_market_review", run)
        self.assertIn("git push origin", run)
        # No upload-artifact anywhere in this job: the pointer is committed,
        # and KRX-derived material is never published as a public artifact.
        self.assertFalse(
            [step for step in self.job["steps"] if "upload-artifact" in step.get("uses", "")]
        )

    def test_pointer_session_is_the_resolved_pair_never_an_invented_date(self):
        self.assertEqual(
            set(self.job["needs"]),
            {"prepare-scheduled-observation-pair", "scheduled-observation-pair"},
        )
        self.assertEqual(
            self.job["if"],
            "needs.prepare-scheduled-observation-pair.outputs.should_call == 'true'",
        )
        self.assertEqual(
            self.job["env"]["PRIOR_DATE"],
            "${{ needs.prepare-scheduled-observation-pair.outputs.prior_date }}",
        )
        self.assertEqual(
            self.job["env"]["CURRENT_DATE"],
            "${{ needs.prepare-scheduled-observation-pair.outputs.current_date }}",
        )
        self.assertIn('--previous-date "$PRIOR_DATE"', self.fetch["run"])
        self.assertIn('--current-date "$CURRENT_DATE"', self.fetch["run"])

    def test_exactly_one_additional_provider_call_and_the_ratified_producer(self):
        # Only one step in this job may hold a secret, and it is the single
        # collection call. The pykrx candidate script is never invoked.
        secret_steps = [
            name for name, step in self.steps.items()
            if "secrets." in json.dumps(step.get("env", {}))
        ]
        self.assertEqual(secret_steps, ["Korea five-signal real KRX session-pair fetch attempt"])
        self.assertEqual(self.fetch["env"]["KRX_API_KEY"], "${{ secrets.KRX_API_KEY }}")
        body = "\n".join(step.get("run", "") for step in self.job["steps"])
        self.assertNotIn("korea_market_signals_pykrx_candidate.py", body)
        self.assertEqual(body.count("--previous-date"), 2)  # reuse path + the one call
        # The reuse path cannot reach the provider: it carries no secret.
        self.assertNotIn("env", self.reuse)

    def test_partial_fetch_commits_nothing(self):
        # A failed or partial KRX fetch must fail the job before commit.
        self.assertNotIn("continue-on-error", self.fetch)
        self.assertNotIn("continue-on-error", self.verify)
        self.assertNotIn("continue-on-error", self.commit)
        for step in self.job["steps"]:
            self.assertNotIn("always()", str(step.get("if", "")))
        self.assertNotIn("always()", str(self.job["if"]))
        # Verification strictly precedes the commit, and is never skipped.
        self.assertNotIn("if", self.verify)
        self.assertLess(
            self.names.index(self.verify["name"]), self.names.index(self.commit["name"])
        )
        self.assertLess(
            self.names.index(self.fetch["name"]), self.names.index(self.verify["name"])
        )
        self.assertIn("--verify " + POINTER_PATH, self.verify["run"])

    def test_repeat_run_for_a_committed_session_does_not_rewrite(self):
        reuse = self.reuse["run"]
        self.assertEqual(self.reuse["id"], "existing_signals")
        self.assertIn("data/observations/korea_market_signals/$ISO_DATE/packet.json", reuse)
        self.assertIn("exists=true", reuse)
        self.assertEqual(self.fetch["if"], "steps.existing_signals.outputs.exists != 'true'")
        # Nothing is committed when the produced bytes are unchanged.
        self.assertIn("git diff --cached --quiet", self.commit["run"])
        self.assertIn("no change (reused) -- nothing to commit", self.commit["run"])

    def test_producer_reuse_is_append_only_not_a_rewrite(self):
        # The repository's token here is the producer's own reused=True /
        # PASS_KOREA_MARKET_SIGNALS_REUSED, not the "verified_existing"
        # string other populators return -- confirmed against the script.
        producer = _load_producer()
        source = (ROOT / RATIFIED_PRODUCER).read_text(encoding="utf-8")
        self.assertIn('"reused": True', source)
        self.assertIn("PASS_KOREA_MARKET_SIGNALS_{mode}", source)
        self.assertNotIn("verified_existing", source)
        self.assertIn("APPEND_ONLY_CONFLICT", source)
        session = "2026-09-10"
        committed = json.loads(
            (ROOT / "data/observations/korea_market_signals" / session / "packet.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            # Republishing the exact committed packet is a no-op rewrite.
            producer.publish(dict(committed), root=root)
            first = (root / "data/observations/korea_market_signals" / session / "packet.json").read_bytes()
            producer.publish(dict(committed), root=root)
            self.assertEqual(
                (root / "data/observations/korea_market_signals" / session / "packet.json").read_bytes(),
                first,
            )
            # Tampered bytes never even reach the append-only comparison.
            with self.assertRaises(producer.KoreaMarketSignalsError) as caught:
                producer.publish(dict(committed, available_at="2099-01-01T00:00:00Z"), root=root)
            self.assertIn("PACKET_HASH_INVALID", str(caught.exception))
        with tempfile.TemporaryDirectory() as raw:
            # A different, individually valid packet already committed for
            # this session is refused rather than rewritten.
            root = Path(raw)
            other = json.loads(
                (ROOT / "data/observations/korea_market_signals/2026-09-09/packet.json").read_text(
                    encoding="utf-8"
                )
            )
            target = root / "data/observations/korea_market_signals" / session / "packet.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(producer.KoreaMarketSignalsError) as caught:
                producer.publish(dict(committed), root=root)
            self.assertIn("APPEND_ONLY_CONFLICT", str(caught.exception))

    def test_registry_krx_source_owner_names_the_producer_this_job_invokes(self):
        # The registry's byte-frozen KRX source_owner already records the
        # ratified producer; this job must invoke that exact script and no
        # substitute. (The registry's workflow_path record is a separate,
        # byte-pinned question -- see the PR body.)
        registry = json.loads(
            (ROOT / "config/regime_source_owner_registry_v2.json").read_text(encoding="utf-8")
        )
        owner = registry["markets"]["KRX"]["source_owner"]
        self.assertEqual(owner["producer_path"], RATIFIED_PRODUCER)
        self.assertIn(owner["producer_path"], self.fetch["run"])
        self.assertIn(owner["producer_path"], self.reuse["run"])

    def test_no_kr_session_coupling_is_relaxed(self):
        source = KR_OBSERVATION.read_text(encoding="utf-8")
        self.assertIn('_fail("KR_UNIVERSE_SESSION_MISMATCH"', source)
        self.assertIn('_fail("KR_BOUNDED_REVIEW_SESSION_MISMATCH"', source)
        self.assertIn('_fail("KR_BOUNDED_REVIEW_NOT_REPRODUCIBLE")', source)
        self.assertIn('_fail("KR_UNIVERSE_FOR_SESSION_MISSING", session_date)', source)
        # This job never edits the consumer that enforces them.
        body = "\n".join(step.get("run", "") for step in self.job["steps"])
        self.assertNotIn("korea_population_symbol_observation", body)
        # Instead it satisfies them: the pointer's session must have a real
        # universe packet and its own review rebuild before the commit.
        self.assertIn("KRX_GLOBAL_UNIVERSE_FOR_SESSION_MISSING", self.verify["run"])
        self.assertIn('review["operational_date_kst"] == expected', self.verify["run"])
        self.assertIn(
            "Rebuild the bounded Korea symbol review from the produced pointer", self.names
        )
        self.assertLess(
            self.names.index("Rebuild the bounded Korea symbol review from the produced pointer"),
            self.names.index(self.verify["name"]),
        )

    def test_pointer_carries_no_raw_krx_rows(self):
        packet = json.loads((ROOT / POINTER_PATH).read_text(encoding="utf-8"))
        self.assertEqual(packet["source"]["raw_persistence"], 0)
        self.assertEqual(packet["source"]["per_symbol_persistence"], 0)
        self.assertIn("raw_persistence", self.verify["run"])
        self.assertIn("per_symbol_persistence", self.verify["run"])


class FiveSignalPointerAdvancesKrPopulationTest(unittest.TestCase):
    """End-to-end on already-committed data: a newer pointer plus its own
    review rebuild is what lets load_context() resolve a later session.
    Nothing tracked is written; only a temporary directory."""

    SESSION = "2026-08-28"

    def setUp(self):
        self.pointer = (
            ROOT / "data/observations/korea_market_signals" / self.SESSION / "packet.json"
        )
        self.universe = (
            ROOT / "data/observations/krx_global_universe" / self.SESSION / "packet.json"
        )
        if not (self.pointer.is_file() and self.universe.is_file()):
            self.skipTest(f"committed {self.SESSION} pointer/universe not in this checkout")
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from decision import korea_population_symbol_observation as KR
        from decision import korea_symbol_market_review as REVIEW
        from decision import population_symbol_observation as CORE
        self.KR, self.REVIEW, self.CORE = KR, REVIEW, CORE

    def _inputs(self, review_path):
        return {
            "session_date": self.SESSION,
            "universe_path": self.universe,
            "market_signals_path": self.pointer,
            "stage_history_path": ROOT / "data" / "stage_history.json",
            "bounded_review_path": review_path,
            "watchlist_root": ROOT / "data" / "briefing" / "krx",
            "capture_dir": None,
            "price_history_root": self.KR.price_history_root(),
        }

    def test_rebuilt_review_lets_the_lookup_reach_the_pointers_session(self):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            review_path = work / "review.json"
            outcome = self.REVIEW.populate(
                market_path=self.pointer,
                stage_path=ROOT / "data" / "stage_history.json",
                briefing_root=ROOT / "data" / "briefing" / "krx",
                output_root=work / "observations",
                latest_path=review_path,
            )
            self.assertIn(outcome["outcome"], {"populated", "verified_existing"})
            rebuilt = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertEqual(rebuilt["operational_date_kst"], self.SESSION)
            context = self.KR.load_context(
                self._inputs(review_path),
                generated_at="2026-09-18T09:30:00Z",
                contract=self.CORE.load_contract(),
            )
            self.assertEqual(context["session_date"], self.SESSION)
            self.assertTrue(context["population_records"])

    def test_a_stale_committed_review_blocks_the_advance(self):
        # Proof that rebuilding the review is required, not cosmetic: with
        # the live committed review left alone, the same newer pointer is
        # refused -- the coupling is intact.
        committed = json.loads((ROOT / REVIEW_PATH).read_text(encoding="utf-8"))
        if committed["operational_date_kst"] == self.SESSION:
            self.skipTest("committed review already describes this session")
        with self.assertRaises(self.KR.PopulationSymbolObservationError) as caught:
            self.KR.load_context(
                self._inputs(ROOT / REVIEW_PATH),
                generated_at="2026-09-18T09:30:00Z",
                contract=self.CORE.load_contract(),
            )
        self.assertIn("KR_BOUNDED_REVIEW_NOT_REPRODUCIBLE", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
