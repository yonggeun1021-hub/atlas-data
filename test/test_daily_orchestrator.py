#!/usr/bin/env python3
"""P8 Atlas Daily Briefing Integration v1 regression.

Builds and publishes only against real, already-committed evidence and
already-existing production builders; introduces no synthetic sensor data.
Focuses on: honest component status classification, failure isolation,
determinism, tamper/mismatch fail-closed behaviour, atomic append-only
publication, and the false authority boundary.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "briefing" / "daily_orchestrator.py"
WORKFLOW = ROOT / ".github" / "workflows" / "daily-briefing.yml"
SPEC = importlib.util.spec_from_file_location("daily_orchestrator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)

# A recent date this repo has real committed evidence for across every
# LIVE_READY sensor exercised by the orchestrator.
DECISION_DATE = "2026-08-21"
MORNING_GENERATED_AT = "2026-08-21T12:00:00Z"
EVENING_GENERATED_AT = "2026-08-21T09:30:00Z"  # 18:30 KST


def _walk_authorized_keys(value, path=""):
    """Yield (path, value) for every key ending in _authorized anywhere."""
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if key.endswith("_authorized"):
                yield child, item
            yield from _walk_authorized_keys(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_authorized_keys(item, f"{path}[{index}]")


class DailyOrchestratorTest(unittest.TestCase):
    def test_morning_build_against_real_evidence_has_no_degraded_components(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        counts = packet["component_status_counts"]
        self.assertEqual(counts["DEGRADED"], 0)
        self.assertEqual(counts["UNKNOWN"], 0)
        self.assertGreater(counts["READY"], 0)
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(
            by_id["KRX_POST_CLOSE"]["status"], "PENDING"
        )
        self.assertEqual(
            by_id["KRX_POST_CLOSE"]["reason"],
            "MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY",
        )
        # A handful of components this repo genuinely has live evidence for.
        for component_id in (
            "STEP0_READ_MODEL_HEALTH", "KRX_PREOPEN_COMPACT",
            "US_BREADTH_MEMBERSHIP", "BTC_TREND", "BTC_RISK",
            "STABLECOIN_NET_ISSUANCE",
        ):
            self.assertEqual(
                by_id[component_id]["status"], "READY", component_id
            )
        # Genuinely unratified/unpopulated components must say so honestly,
        # never silently disappear or read as READY.
        self.assertEqual(by_id["PORTFOLIO_BUCKET"]["status"], "POLICY_BLOCKED")
        self.assertEqual(by_id["RULE_EVALUATION"]["status"], "POLICY_BLOCKED")
        self.assertIn(
            by_id["CRYPTO_BREADTH"]["status"], ("POLICY_BLOCKED", "READY")
        )

    def test_evening_slot_includes_observed_unconfirmed_krx_post_close(self):
        packet = MODULE.build_packet("evening", DECISION_DATE, EVENING_GENERATED_AT)
        by_id = {row["component_id"]: row for row in packet["components"]}
        row = by_id["KRX_POST_CLOSE"]
        self.assertEqual(row["status"], "READY")
        self.assertIsNotNone(row["packet"])
        self.assertEqual(row["packet"]["status"], "READY_OBSERVED_UNCONFIRMED")
        # The orchestrator itself never promotes an unconfirmed evening
        # observation to a decision input, regardless of the embedded
        # packet's own fields.
        self.assertFalse(row["decision_eligible"])
        self.assertFalse(row["action_eligible"])
        self.assertFalse(row["order_eligible"])

    def test_morning_never_builds_krx_post_close_even_if_bundle_exists(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertIsNone(by_id["KRX_POST_CLOSE"]["packet"])

    def test_rebuild_is_byte_identical_deterministic(self):
        first = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        second = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

    def test_validate_packet_round_trips_and_rejects_tamper(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

        sha_tamper = copy.deepcopy(packet)
        sha_tamper["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "OUTPUT_SHA_MISMATCH"
        ):
            MODULE.validate_packet(sha_tamper)

        # Self-rehashed semantic tamper: change a real value and recompute
        # the digest over the tampered payload. The rebuild-and-compare
        # step must still catch it because the rebuilt packet won't match.
        semantic_tamper = copy.deepcopy(packet)
        for row in semantic_tamper["components"]:
            if row["component_id"] == "BTC_TREND":
                row["packet"]["direction"] = "BELOW_200DMA"
        unsigned = copy.deepcopy(semantic_tamper)
        del unsigned["packet_sha256"]
        semantic_tamper["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"
        ):
            MODULE.validate_packet(semantic_tamper)

        # Source path/sha substitution: point a component at a path that
        # does not match what a real rebuild would produce.
        path_tamper = copy.deepcopy(packet)
        for row in path_tamper["components"]:
            if row["component_id"] == "US_BREADTH_MEMBERSHIP":
                row["source_packet_path"] = "evidence/us_breadth/raw/1999-01-01"
        unsigned = copy.deepcopy(path_tamper)
        del unsigned["packet_sha256"]
        path_tamper["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"
        ):
            MODULE.validate_packet(path_tamper)

    def test_generated_date_mismatch_isolates_unified_decision_not_whole_run(self):
        # decision_date deliberately does not match generated_at's own date.
        packet = MODULE.build_packet(
            "morning", "2026-08-20", MORNING_GENERATED_AT
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(by_id["UNIFIED_DECISION"]["status"], "DEGRADED")
        self.assertIn(
            "GENERATED_DATE_MISMATCH", by_id["UNIFIED_DECISION"]["reason"]
        )
        # Every other, unrelated component still built normally: one
        # component's failure must not crash or blank out the rest. (STEP0
        # legitimately reports DATA_BLOCKED here too, because the mismatched
        # decision_date genuinely does not match the real committed
        # collector evidence's own collected_for_kst_date -- that is
        # correct, honest behaviour, not a cascading crash.)
        self.assertEqual(by_id["BTC_TREND"]["status"], "READY")
        self.assertEqual(by_id["US_BREADTH_MEMBERSHIP"]["status"], "READY")
        # KRX_PREOPEN_COMPACT correctly reports DATA_BLOCKED (a collector
        # data failure -- the mismatched date -- not a read-model-only
        # DEGRADED), and ACTION_RISK_PORTFOLIO_SUMMARY cascades to DEGRADED
        # because its one required source (UNIFIED_DECISION) is unavailable.
        self.assertEqual(by_id["KRX_PREOPEN_COMPACT"]["status"], "DATA_BLOCKED")
        self.assertEqual(
            by_id["ACTION_RISK_PORTFOLIO_SUMMARY"]["status"], "DEGRADED"
        )
        self.assertEqual(packet["component_status_counts"]["DEGRADED"], 2)

    def test_single_component_failure_is_isolated(self):
        original = MODULE.BTC_TREND.build_transform

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated sensor failure")

        MODULE.BTC_TREND.build_transform = _boom
        try:
            row = MODULE.build_btc_trend()
        finally:
            MODULE.BTC_TREND.build_transform = original
        self.assertEqual(row["status"], "DEGRADED")
        self.assertIn("simulated sensor failure", row["reason"])

        # And the full pipeline still assembles every other component even
        # while BTC_TREND is broken.
        MODULE.BTC_TREND.build_transform = _boom
        try:
            packet = MODULE.build_packet(
                "morning", DECISION_DATE, MORNING_GENERATED_AT
            )
        finally:
            MODULE.BTC_TREND.build_transform = original
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(by_id["BTC_TREND"]["status"], "DEGRADED")
        self.assertEqual(by_id["STEP0_READ_MODEL_HEALTH"]["status"], "READY")
        self.assertEqual(by_id["US_BREADTH_MEMBERSHIP"]["status"], "READY")

    def test_no_action_order_production_or_trading_authority_is_ever_true(self):
        for slot, generated_at in (
            ("morning", MORNING_GENERATED_AT),
            ("evening", EVENING_GENERATED_AT),
        ):
            packet = MODULE.build_packet(slot, DECISION_DATE, generated_at)
            structural_true_allowed = {
                "aggregation_only", "component_build_authorized",
                "daily_decision_assembly_only", "briefing_read_model_only",
                "evidence_only",
            }
            for path, value in _walk_authorized_keys(packet):
                key = path.rsplit(".", 1)[-1]
                if key in structural_true_allowed:
                    continue
                self.assertFalse(
                    value, f"{slot}: {path} must remain false, got {value}"
                )
            for row in packet["components"]:
                self.assertFalse(row["decision_eligible"], row["component_id"])
                self.assertFalse(row["action_eligible"], row["component_id"])
                self.assertFalse(row["order_eligible"], row["component_id"])
            self.assertEqual(packet["decision"] if "decision" in packet else None, None)

    def test_render_markdown_covers_required_sections_and_hides_nothing(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        rendered = MODULE.render_markdown(packet)
        for required in (
            "Data / Read-model health", "3-Market Regime", "Rotation / Theme",
            "Rule status", "Portfolio / Risk", "Decision & action boundary",
            "PENDING / UNKNOWN / DEGRADED / BLOCKED components",
            "Unresolved boundaries",
        ):
            self.assertIn(required, rendered)
        # Every single component must appear somewhere in the render, so a
        # blocked/pending section is shown with its reason, never hidden.
        for row in packet["components"]:
            self.assertIn(row["component_id"], rendered)
            if row["reason"]:
                self.assertIn(row["reason"], rendered)
        self.assertIn(
            "No action, order, Production, or trading authority", rendered
        )

    def test_publish_is_atomic_append_only_and_preserves_existing_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            target = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
            )
            self.assertTrue((target / "packet.json").exists())
            self.assertTrue((target / "briefing.md").exists())
            original_bytes = (target / "packet.json").read_bytes()

            with self.assertRaisesRegex(
                MODULE.DailyOrchestratorError, "APPEND_ONLY_VIOLATION"
            ):
                MODULE.publish(
                    "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
                )
            # The existing good bundle must be untouched by the failed
            # republish attempt.
            self.assertEqual((target / "packet.json").read_bytes(), original_bytes)
            self.assertFalse(
                any(
                    p.name.startswith(".") for p in target.parent.iterdir()
                ),
                "no leftover temp directory after a rejected republish",
            )

    def test_publish_leaves_no_partial_bundle_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original = MODULE.validate_packet

            def _always_fail(*args, **kwargs):
                raise MODULE.DailyOrchestratorError("SIMULATED_VALIDATION_FAILURE")

            MODULE.validate_packet = _always_fail
            try:
                with self.assertRaisesRegex(
                    MODULE.DailyOrchestratorError, "SIMULATED_VALIDATION_FAILURE"
                ):
                    MODULE.publish(
                        "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
                    )
            finally:
                MODULE.validate_packet = original
            target = evidence_root / "morning" / DECISION_DATE
            self.assertFalse(target.exists())
            self.assertFalse(evidence_root.exists())

    def test_idempotent_rerun_produces_identical_published_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            target = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
            )
            first_bytes = (target / "packet.json").read_bytes()
            evidence_root_2 = Path(tmp) / "daily_briefing_2"
            target_2 = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root_2
            )
            second_bytes = (target_2 / "packet.json").read_bytes()
            self.assertEqual(first_bytes, second_bytes)

    def test_slot_and_generated_at_are_validated(self):
        with self.assertRaisesRegex(MODULE.DailyOrchestratorError, "SLOT_INVALID"):
            MODULE.build_packet("afternoon", DECISION_DATE, MORNING_GENERATED_AT)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "GENERATED_AT_INVALID"
        ):
            MODULE.build_packet("morning", DECISION_DATE, "not-a-timestamp")
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "GENERATED_AT_INVALID"
        ):
            MODULE.build_packet(
                "morning", DECISION_DATE, "2026-08-21T12:00:00"
            )  # missing tz offset

    def test_contract_authority_and_status_vocabulary_are_pinned(self):
        contract = MODULE.load_contract()
        self.assertEqual(
            set(contract["component_status_values"]), MODULE.STATUS_VALUES
        )
        for key, value in contract["authority"].items():
            if key in ("aggregation_only", "component_build_authorized"):
                self.assertTrue(value, key)
            else:
                self.assertFalse(value, key)

    def test_no_workflow_calls_a_live_provider_from_this_module(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("import requests", script)
        self.assertNotIn("urlopen", script)
        self.assertNotIn("urllib.request", script)

    def test_workflow_calls_the_real_orchestrator_before_committing(self):
        triggers = WF.get("on", WF.get(True))
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(WF["permissions"], {"contents": "write"})
        self.assertEqual(WF["concurrency"]["cancel-in-progress"], False)
        steps = WF["jobs"]["briefing"]["steps"]
        regression = next(
            step for step in steps
            if step.get("name") == "Offline daily orchestrator regression"
        )
        self.assertIn("test_daily_orchestrator.py", regression["run"])
        publish = next(
            step for step in steps
            if step.get("name") == "Publish provider-free daily briefing packet"
        )
        command = publish["run"]
        self.assertIn("briefing/daily_orchestrator.py publish", command)
        self.assertIn("briefing/daily_orchestrator.py validate", command)
        self.assertIn("skipped_existing", command)
        self.assertLess(steps.index(regression), steps.index(publish))
        commit = next(
            step for step in steps if step.get("name") == "Commit immutable daily briefing bundle"
        )
        self.assertEqual(commit.get("if"), "steps.briefing.outputs.result == 'published'")
        self.assertIn('git add "$CAPTURE_PATH"', commit["run"])

    def test_workflow_does_not_duplicate_or_alter_existing_collector_schedules(self):
        # The daily briefing workflow must never re-fetch anything the
        # collectors already fetched -- it is a separate, later, read-only
        # aggregation step over what they already committed.
        command = "\n".join(
            step.get("run", "") for step in WF["jobs"]["briefing"]["steps"]
        )
        for forbidden in ("curl ", "collectors/", ".github/scripts/build_briefing_inputs.py"):
            self.assertNotIn(forbidden, command)
        collect_yml = (ROOT / ".github" / "workflows" / "collect.yml").read_text(
            encoding="utf-8"
        )
        krx_post_close_yml = (
            ROOT / ".github" / "workflows" / "krx-post-close.yml"
        ).read_text(encoding="utf-8")
        # This test file's own existence and content must not have altered
        # either sibling collector workflow's schedule.
        self.assertIn("cron: '5 21 * * 0-4'", collect_yml)
        self.assertIn("cron: '5 7 * * 1-5'", krx_post_close_yml)


if __name__ == "__main__":
    unittest.main()
