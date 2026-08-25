#!/usr/bin/env python3
"""P1-KR-03 provider-free release-timing observation regression."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "kofia_release_timing.py"
WORKFLOW = ROOT / ".github" / "workflows" / "p1-kr03-kofia-first-seen.yml"
EVIDENCE_ROOT = ROOT / "evidence" / "kofia" / "first_seen"
SPEC = importlib.util.spec_from_file_location("kofia_release_timing", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def timeline_row(captured_at, operation_payload):
    return {
        "captured_at_utc": captured_at,
        "bundle_path": "fixture",
        "manifest_sha256": "1" * 64,
        "observation_sha256": "2" * 64,
        "operations": {"investor_deposits": operation_payload},
    }


def operation_payload(observed=None, missing=None):
    return {
        "latest_observation_date": None,
        "observed_rows": observed or [],
        "missing_query_dates": missing or [],
    }


def row(day, digest):
    return {
        "observation_date": day,
        "row_sha256": digest,
        "atlas_first_seen_at_utc": "unused",
    }


class PureWindowTests(unittest.TestCase):
    def test_missing_then_present_yields_open_closed_window(self):
        day = "2026-08-20"
        sha = "a" * 64
        timeline = [
            timeline_row(
                "2026-08-21T00:00:00Z", operation_payload(missing=[day])
            ),
            timeline_row(
                "2026-08-21T04:00:00Z",
                operation_payload(observed=[row(day, sha)]),
            ),
        ]
        result = MODULE._build_observations(
            timeline, {("investor_deposits", day, sha): "2026-08-21T04:00:00Z"}
        )[0]
        self.assertEqual(
            result["last_verified_exact_row_absent_probe_at_utc"],
            "2026-08-21T00:00:00Z",
        )
        self.assertEqual(result["availability_window_seconds"], 14400)
        self.assertEqual(
            result["window_status"],
            "BOUNDED_BY_VERIFIED_EXACT_ROW_ABSENT_AND_PRESENT_PROBES",
        )

    def test_previous_revision_is_exact_row_absence_for_new_revision(self):
        day = "2026-08-20"
        old_sha = "a" * 64
        new_sha = "b" * 64
        timeline = [
            timeline_row(
                "2026-08-21T00:00:00Z",
                operation_payload(observed=[row(day, old_sha)]),
            ),
            timeline_row(
                "2026-08-21T04:00:00Z",
                operation_payload(observed=[row(day, new_sha)]),
            ),
        ]
        results = MODULE._build_observations(
            timeline,
            {
                ("investor_deposits", day, old_sha): "2026-08-21T00:00:00Z",
                ("investor_deposits", day, new_sha): "2026-08-21T04:00:00Z",
            },
        )
        latest = next(item for item in results if item["row_sha256"] == new_sha)
        self.assertEqual(
            latest["last_verified_exact_row_absent_probe_at_utc"],
            "2026-08-21T00:00:00Z",
        )
        summary = MODULE._summaries(results)["investor_deposits"]
        self.assertEqual(summary["revision_counts_by_observation_date"], {day: 2})

    def test_same_timestamp_is_not_claimed_as_prior_absence(self):
        day = "2026-08-20"
        sha = "c" * 64
        instant = "2026-08-21T04:00:00Z"
        timeline = [timeline_row(instant, operation_payload(missing=[day]))]
        result = MODULE._build_observations(
            timeline, {("investor_deposits", day, sha): instant}
        )[0]
        self.assertIsNone(result["last_verified_exact_row_absent_probe_at_utc"])
        self.assertIsNone(result["availability_window_seconds"])
        self.assertEqual(
            result["window_status"],
            "UPPER_BOUND_ONLY_NO_EARLIER_VERIFIED_EXACT_ROW_ABSENT_PROBE",
        )

    def test_first_seen_before_observation_date_fails_closed(self):
        with self.assertRaisesRegex(
            MODULE.ReleaseTimingError, "FIRST_SEEN_PRECEDES_OBSERVATION_DATE"
        ):
            MODULE._build_observations(
                [],
                {
                    ("investor_deposits", "2026-08-21", "d" * 64):
                    "2026-08-20T00:00:00Z"
                },
            )


class RealEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = MODULE.build_packet(EVIDENCE_ROOT)

    def test_real_committed_sequence_builds_and_independently_validates(self):
        packet = MODULE.validate_packet(copy.deepcopy(self.packet), EVIDENCE_ROOT)
        self.assertEqual(packet["schema_version"], "kofia_release_timing_observation/1")
        self.assertGreaterEqual(packet["source_bundle_count"], 1)
        self.assertGreaterEqual(len(packet["observations"]), 2)
        self.assertEqual(
            set(packet["summary_by_operation"]),
            {"credit_financing", "investor_deposits"},
        )

    def test_historical_packet_revalidates_after_later_bundles_exist(self):
        if self.packet["source_bundle_count"] < 2:
            self.skipTest("needs at least two committed captures")
        cutoff = self.packet["source_bundles"][-2]["captured_at_utc"]
        historical = MODULE.build_packet(
            EVIDENCE_ROOT, as_of_capture_utc=cutoff
        )
        self.assertEqual(historical["as_of_capture_utc"], cutoff)
        self.assertEqual(
            historical["source_bundle_count"], self.packet["source_bundle_count"] - 1
        )
        self.assertEqual(
            MODULE.validate_packet(historical, EVIDENCE_ROOT), historical
        )

    def test_real_windows_are_positive_or_explicit_upper_bounds(self):
        for item in self.packet["observations"]:
            seconds = item["availability_window_seconds"]
            if seconds is None:
                self.assertIsNone(
                    item["last_verified_exact_row_absent_probe_at_utc"]
                )
            else:
                self.assertGreater(seconds, 0)

    def test_no_available_at_or_policy_authority_is_created(self):
        self.assertIsNone(self.packet["available_at"])
        self.assertEqual(
            self.packet["release_timing_policy_status"], "UNRATIFIED"
        )
        self.assertEqual(
            self.packet["api_field_unit_status"],
            "conflicting_primary_evidence",
        )
        self.assertTrue(
            all(value is False for value in self.packet["authority"].values())
        )

    def test_source_lineage_is_explicit_and_hash_bound(self):
        self.assertEqual(
            len(self.packet["source_bundles"]), self.packet["source_bundle_count"]
        )
        for bundle in self.packet["source_bundles"]:
            self.assertTrue(bundle["bundle_path"].startswith("evidence/kofia/first_seen/"))
            self.assertEqual(len(bundle["manifest_sha256"]), 64)
            self.assertEqual(len(bundle["observation_sha256"]), 64)

    def test_output_path_is_derived_from_latest_source_run_identity(self):
        latest = Path(self.packet["source_bundles"][-1]["bundle_path"])
        self.assertEqual(
            MODULE.expected_output_relative(self.packet),
            Path(
                "data",
                "observations",
                "kofia_release_timing",
                latest.parts[3],
                latest.parts[4],
                "packet.json",
            ),
        )

    def test_wrong_tracked_output_path_is_rejected(self):
        wrong = ROOT / "data" / "observations" / "kofia_release_timing" / "wrong" / "packet.json"
        with self.assertRaisesRegex(
            MODULE.ReleaseTimingError, "OUTPUT_PATH_IDENTITY_MISMATCH"
        ):
            MODULE._enforce_tracked_output_identity(self.packet, wrong)

    def test_rehashed_semantic_tamper_is_rejected(self):
        altered = copy.deepcopy(self.packet)
        altered["release_timing_policy_status"] = "RATIFIED"
        altered.pop("payload_sha256")
        altered["payload_sha256"] = MODULE.digest(altered)
        with self.assertRaisesRegex(
            MODULE.ReleaseTimingError, "PACKET_SEMANTIC_MISMATCH"
        ):
            MODULE.validate_packet(altered, EVIDENCE_ROOT)

    def test_plain_hash_tamper_is_rejected(self):
        altered = copy.deepcopy(self.packet)
        altered["source_bundle_count"] += 1
        with self.assertRaisesRegex(
            MODULE.ReleaseTimingError, "PACKET_HASH_MISMATCH"
        ):
            MODULE.validate_packet(altered, EVIDENCE_ROOT)

    def test_noncanonical_evidence_root_cannot_be_mislabeled(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(
                MODULE.ReleaseTimingError, "EVIDENCE_ROOT_MISMATCH"
            ):
                MODULE.build_packet(Path(temp))

    def test_tampered_prior_bundle_is_rejected_by_raw_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "first_seen"
            shutil.copytree(EVIDENCE_ROOT, copied)
            target = next(copied.glob("*/*/_observation.json"))
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["available_at"] = "2026-01-01T00:00:00Z"
            target.write_bytes(MODULE.canonical_bytes(payload))
            with self.assertRaisesRegex(
                MODULE.capture.CaptureError, "PRIOR_EVIDENCE_"
            ):
                MODULE._validated_timeline(copied)

    def test_append_only_writer_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "packet.json"
            MODULE.write_packet(self.packet, target)
            with self.assertRaisesRegex(
                MODULE.ReleaseTimingError, "APPEND_ONLY_VIOLATION"
            ):
                MODULE.write_packet(self.packet, target)

    def test_cli_build_is_canonical_and_validatable(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "packet.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "build",
                    "--evidence-root",
                    str(EVIDENCE_ROOT),
                    "--out",
                    str(target),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(target.read_bytes(), MODULE.canonical_bytes(parsed))


class ContractAndWorkflowTests(unittest.TestCase):
    def test_contract_is_explicitly_diagnostic_and_unratified(self):
        contract = MODULE.load_contract()
        self.assertIsNone(contract["available_at"])
        self.assertEqual(contract["release_timing_policy_status"], "UNRATIFIED")
        self.assertTrue(
            all(
                contract[key] is False
                for key in (
                    "decision_eligible",
                    "regime_score_authorized",
                    "production_wiring_authorized",
                    "trading_action_authorized",
                )
            )
        )

    def test_no_provider_call_or_new_schedule_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("urlopen(", script)
        self.assertNotIn("capture.capture(", script)
        with WORKFLOW.open(encoding="utf-8") as stream:
            workflow = yaml.safe_load(stream)
        schedule = workflow[True]["schedule"]
        self.assertEqual(len(schedule), 5)

    def test_raw_commit_precedes_separate_derived_commit(self):
        with WORKFLOW.open(encoding="utf-8") as stream:
            workflow = yaml.safe_load(stream)
        steps = workflow["jobs"]["capture"]["steps"]
        names = [step.get("name") for step in steps]
        raw = names.index("Commit immutable evidence")
        build = names.index("Build provider-free release timing observation")
        derived = names.index("Commit release timing observation separately")
        self.assertLess(raw, build)
        self.assertLess(build, derived)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', steps[raw]["run"])
        self.assertIn("kofia_release_timing.py build", steps[build]["run"])
        self.assertEqual(
            steps[derived]["if"], "steps.release_timing.outputs.report_path != ''"
        )

    def test_full_coverage_does_not_fabricate_release_timing(self):
        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('if [ "$MODE" != "first_seen" ]; then', workflow_text)
        self.assertIn('echo "report_path="', workflow_text)

    def test_output_is_run_identity_append_only(self):
        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'data/observations/kofia_release_timing/$KST_DATE/$RUN_KEY/packet.json',
            workflow_text,
        )
        self.assertIn('test ! -e "$REPORT_PATH"', workflow_text)


if __name__ == "__main__":
    unittest.main()
