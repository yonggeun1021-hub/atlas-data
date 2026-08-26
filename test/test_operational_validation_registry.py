#!/usr/bin/env python3
"""Server-side live-validation inventory and P4-04 probe regression."""
from __future__ import annotations

import hashlib
import contextlib
import io
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "operational_validation_registry.json"
PROBE_PATH = ROOT / "collectors" / "tsmc_official_release_probe.py"
SEC_PROBE_PATH = ROOT / "collectors" / "tsmc_sec_monthly_probe.py"
TSMC_WORKFLOW = ROOT / ".github" / "workflows" / "tsmc-live-fetch.yml"

SPEC = importlib.util.spec_from_file_location("tsmc_official_release_probe", PROBE_PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

REGISTRY = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True)) or {}


_PY_SCRIPT_PATH_RE = re.compile(r"[\w./-]+\.py")


def _step_bodies_text(workflow: dict) -> str:
    """Concatenated name/run/uses/with text of every step in every job.

    Built from the *parsed* workflow doc, not the raw file text, so a
    comment that merely mentions a filename (as opposed to a step actually
    producing/consuming it) can never satisfy a binding-contract check --
    comments aren't part of the parsed doc at all.
    """
    parts = []
    for job in (workflow.get("jobs") or {}).values():
        for step in (job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            parts.append(str(step.get("name", "")))
            parts.append(str(step.get("run", "")))
            parts.append(str(step.get("uses", "")))
            parts.append(json.dumps(step.get("with") or {}, ensure_ascii=False))
    return "\n".join(parts)


def _invoked_script_sources(step_bodies: str) -> str:
    """Concatenated source of every local .py script referenced in step
    bodies (e.g. `python3 collectors/foo.py ...`).

    A required token can legitimately live one layer deeper than the
    workflow YAML: the step only invokes a collector script, and the
    script's own source is what actually names the token (an output
    filename it writes, for instance). This resolves that one hop so such
    tokens are verified against the real execution chain instead of being
    invisible to a workflow-text-only check.
    """
    sources = []
    for match in sorted(set(_PY_SCRIPT_PATH_RE.findall(step_bodies))):
        candidate = ROOT / match
        if candidate.is_file():
            sources.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(sources)


def _token_is_realized(token: str, workflow: dict) -> bool:
    """True if `token` is bound to something the workflow actually executes:
    present in a step's name/run/uses/with, or in the source of a local
    .py collector script that a step invokes. A YAML comment mentioning the
    token does not count."""
    step_bodies = _step_bodies_text(workflow)
    if token in step_bodies:
        return True
    return token in _invoked_script_sources(step_bodies)


class OperationalValidationRegistryTest(unittest.TestCase):
    def test_active_wbs_snapshot_is_complete_unique_and_classified(self):
        items = REGISTRY["items"]
        ids = [item["work_item_id"] for item in items]
        self.assertEqual(len(ids), REGISTRY["active_wbs_snapshot_count"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            set(ids),
            {
                "P0-02", "P0-04", "P1-US-03", "P1-US-04", "P1-US-05",
                "P1-US-06", "P1-US-07", "P1-KR-03", "P1-KR-04",
                "P1-KR-06", "P1-KR-07", "P1-CR-04", "P1-CR-05",
                "P1-CR-06", "P1-CR-07", "P1-COM-03", "P1-COM-04",
                "P3-09", "P3-10", "P4-02", "P4-03", "P4-04", "P4-06", "P5-02",
            },
        )
        allowed = set(REGISTRY["automation_states"])
        self.assertTrue(all(item["automation_state"] in allowed for item in items))
        self.assertEqual(REGISTRY["execution_platform"], "github_actions")
        self.assertFalse(REGISTRY["local_machine_required"])

    def test_every_scheduled_item_has_real_server_workflow_and_exact_crons(self):
        scheduled = [
            item for item in REGISTRY["items"]
            if item["automation_state"].startswith("SCHEDULED_")
        ]
        self.assertTrue(scheduled)
        for item in scheduled:
            with self.subTest(work_item_id=item["work_item_id"]):
                path = ROOT / item["workflow"]
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                workflow = yaml.safe_load(text)
                event = triggers(workflow)
                actual = {entry["cron"] for entry in event.get("schedule", [])}
                self.assertEqual(actual, set(item["crons"]))
                self.assertIn("workflow_dispatch", event)
                for token in item["required_workflow_tokens"]:
                    self.assertTrue(
                        _token_is_realized(token, workflow),
                        f"{item['work_item_id']}: {token!r} is not bound to any "
                        "step (name/run/uses/with) or invoked collector script "
                        "source in this workflow -- a comment mentioning it "
                        "does not satisfy the binding contract.",
                    )

    def test_policy_blocked_items_do_not_claim_an_automatic_execution(self):
        blocked_states = {"POLICY_BLOCKED", "PAID_RECONFIRMATION_REQUIRED"}
        blocked = [
            item for item in REGISTRY["items"]
            if item["automation_state"] in blocked_states
        ]
        self.assertTrue(blocked)
        for item in blocked:
            with self.subTest(work_item_id=item["work_item_id"]):
                self.assertIsNone(item["workflow"])
                self.assertEqual(item["crons"], [])
                self.assertFalse(item["automatic_execution_authorized"])
                self.assertTrue(item["blocking_basis"])

    def test_tsmc_live_bytes_reach_adapter_but_remain_policy_blocked(self):
        raw = (ROOT / "collectors" / "fixtures" / "tsmc_ir_monthly_2026.html").read_bytes()
        report = PROBE.run_probe(
            2026,
            retrieved_at_utc="2026-08-20T00:00:00Z",
            fetcher=lambda year: raw,
        )
        self.assertEqual(report["status"], PROBE.STATUS_BLOCKED)
        self.assertTrue(report["live_fetch_succeeded"])
        self.assertFalse(report["operating_gate_closed"])
        self.assertEqual(report["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertIsNone(report["availability_policy"]["available_at"])
        self.assertTrue(
            report["availability_policy"][
                "first_seen_is_not_promoted_to_published_at"
            ]
        )
        envelopes = report["evidence_bundle"]["envelopes"]
        self.assertTrue(envelopes)
        for envelope in envelopes:
            self.assertEqual(envelope["status"], "EVIDENCE_BLOCKED")
            self.assertFalse(envelope["consumable"])
            self.assertIsNone(envelope["observation"])
            self.assertIn("AVAILABLE_AT_UNOBSERVED", envelope["blocked_by"])
            self.assertIn("COLLECTOR_NOT_DECISION_READY", envelope["blocked_by"])

    def test_probe_failure_is_reported_without_tracked_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "failure.json"
            with mock.patch.object(
                PROBE.TSMC, "fetch_source_bytes", side_effect=RuntimeError("offline")
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = PROBE.main(["--year", "2026", "--out", str(out)])
            self.assertEqual(exit_code, 1)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], PROBE.STATUS_FAILED)
            self.assertFalse(report["live_fetch_succeeded"])
            self.assertFalse(report["operating_gate_closed"])
        self.assertFalse((ROOT / "data" / "latest_tsmc_monthly.json").exists())

    def test_tsmc_schedule_is_read_only_and_artifact_backed(self):
        text = TSMC_WORKFLOW.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        event = triggers(workflow)
        self.assertEqual(
            {entry["cron"] for entry in event["schedule"]},
            {"17 1 * * 1"},
        )
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertTrue(SEC_PROBE_PATH.is_file())
        self.assertIn("tsmc_sec_monthly_probe.py", text)
        self.assertIn("$RUNNER_TEMP/tsmc-sec-monthly-live-probe.json", text)
        self.assertIn("p4-04-tsmc-sec-primary", text)
        self.assertIn("tsmc_official_release_probe.py", text)
        self.assertIn("$RUNNER_TEMP/tsmc-official-release-live-probe.json", text)
        self.assertIn("p4-04-tsmc-ir-secondary", text)
        self.assertIn("actions/upload-artifact@043fb46", text)
        self.assertNotIn("git commit", text)
        self.assertNotIn("git push", text)

        steps = workflow["jobs"]["live-fetch"]["steps"]
        primary = next(
            step for step in steps
            if step.get("name") == "TSMC SEC 6-K primary monthly-revenue live validation"
        )
        secondary = next(
            step for step in steps
            if step.get("name") == "TSMC IR secondary human-verification reachability"
        )
        self.assertNotIn("continue-on-error", primary)
        self.assertEqual(secondary["if"], "always()")
        self.assertTrue(secondary["continue-on-error"])


if __name__ == "__main__":
    unittest.main()
