#!/usr/bin/env python3
"""P3-08 DART evidence-only observation regression."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery/dart_event_observation.py"
WORKFLOW = ROOT / ".github/workflows/collect.yml"
RUN_ALL = ROOT / "run_all.py"
DECISION_AT = "2026-08-27T07:50:00Z"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = load_module("dart_event_observation_test", SOURCE)


class DartEventObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = MODULE.build_packet(decision_at=DECISION_AT)

    def test_real_dart_population_records_two_facts_without_interpretation(self):
        self.assertEqual(self.packet["status"], "DART_OBSERVATIONS_RECORDED_ESCALATION_BLOCKED")
        self.assertEqual(self.packet["summary"]["relevant_filing_count"], 2)
        self.assertEqual(self.packet["summary"]["raw_bytes_verified_count"], 1)
        self.assertEqual(self.packet["summary"]["metadata_only_count"], 1)
        self.assertEqual(self.packet["summary"]["source_failed_count"], 0)
        self.assertEqual(self.packet["summary"]["content_failure_count"], 0)
        self.assertEqual(self.packet["source_failures"], [])
        self.assertEqual({row["subject_id"] for row in self.packet["observations"]}, {"034020", "329180"})
        for row in self.packet["observations"]:
            self.assertIsNone(row["event_at"])
            self.assertEqual(row["time_precision"], "DATE_ONLY")
            self.assertIsNone(row["event_type"])
            self.assertIsNone(row["direction"])
            self.assertIsNone(row["importance"])
            self.assertEqual(row["status"], "OBSERVED_ESCALATION_BLOCKED")

    def test_real_retained_zip_and_member_bytes_are_independently_revalidated(self):
        linked = next(row for row in self.packet["observations"] if row["subject_id"] == "329180")
        self.assertEqual(linked["evidence"]["status"], "RAW_BYTES_VERIFIED_ITEM_EXTRACTION_UNRATIFIED")
        self.assertEqual(
            linked["evidence"]["source_sha256"],
            "ced60979d27b6971dbc2bb20dfd1ecbfec6ff9a98cfd8469e0d0bab949f35225",
        )
        self.assertIn("DART_ITEM_EXTRACTION_POLICY_UNRATIFIED", linked["blocked_reasons"])

    def test_metadata_only_row_cannot_be_presented_as_content_verified(self):
        row = next(row for row in self.packet["observations"] if row["subject_id"] == "034020")
        self.assertEqual(row["evidence"]["status"], "METADATA_ONLY_STAGE_NOT_ASSIGNED")
        self.assertIsNone(row["evidence"]["source_sha256"])
        self.assertIn("DART_CONTENT_NOT_APPLICABLE_STAGE_NOT_ASSIGNED", row["blocked_reasons"])

    def test_all_authority_and_side_effect_counts_remain_closed(self):
        self.assertTrue(self.packet["authority"]["observation_recording_only"])
        self.assertTrue(
            all(
                value is False
                for key, value in self.packet["authority"].items()
                if key != "observation_recording_only"
            )
        )
        for key in (
            "event_type_inferred_count", "importance_classified_count",
            "notification_sent_count", "action_count", "order_count",
        ):
            self.assertEqual(self.packet["summary"][key], 0)

    def test_self_rehashed_packet_tamper_fails_independent_rebuild(self):
        tampered = copy.deepcopy(self.packet)
        tampered["authority"]["trading_authorized"] = True
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.DartEventObservationError, "PACKET_DRIFT_OR_TAMPER"):
            MODULE.validate_packet(tampered)

    def test_future_source_and_content_times_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = json.loads(MODULE.DEFAULT_DART.read_text(encoding="utf-8"))
            content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
            source["collected_at_utc"] = "2026-08-27T08:00:00+00:00"
            source_path = root / "latest_dart.json"
            content_path = root / "latest_dart_content.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            content["source_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
            content_path.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.DartEventObservationError, "DART_SOURCE_FROM_FUTURE"):
                MODULE.build_packet(
                    decision_at=DECISION_AT, source_path=source_path,
                    content_path=content_path,
                )

    def test_missing_or_extra_content_records_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "latest_dart.json"
            content_path = root / "latest_dart_content.json"
            shutil.copy2(MODULE.DEFAULT_DART, source_path)
            content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
            content["records"] = content["records"][:1]
            content["counts"] = {"captured": 0, "failed": 0, "not_applicable": 1, "skipped": 0}
            content_path.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.DartEventObservationError, "DART_CONTENT_RECORD_MISSING"):
                MODULE.build_packet(
                    decision_at=DECISION_AT, source_path=source_path,
                    content_path=content_path,
                )

    def test_partial_source_failure_is_isolated_to_that_symbol(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = json.loads(MODULE.DEFAULT_DART.read_text(encoding="utf-8"))
            content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
            failed = source["stocks"]["034020"]
            source["stocks"]["034020"] = {
                "name": failed["name"], "atlas_stage": failed["atlas_stage"],
                "coverage": failed["coverage"], "db_state": failed["db_state"],
                "in_notion": failed["in_notion"], "status": "FAILED",
                "error": "ConnectionError: injected",
            }
            source["summary"] = {"ok": 6, "failed": 1}
            content["records"] = [
                row for row in content["records"]
                if row["filing_identity"]["stock_code"] != "034020"
            ]
            content["counts"] = {"captured": 0, "failed": 0, "not_applicable": 0, "skipped": 1}
            source_path = root / "latest_dart.json"
            content_path = root / "latest_dart_content.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            content["source_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
            content_path.write_text(json.dumps(content), encoding="utf-8")

            packet = MODULE.build_packet(
                decision_at=DECISION_AT, source_path=source_path,
                content_path=content_path,
            )
            self.assertEqual(
                packet["status"],
                "DART_OBSERVATIONS_RECORDED_WITH_PARTIAL_FAILURES_ESCALATION_BLOCKED",
            )
            self.assertEqual(packet["summary"]["source_ok_count"], 6)
            self.assertEqual(packet["summary"]["source_failed_count"], 1)
            self.assertEqual({row["subject_id"] for row in packet["observations"]}, {"329180"})
            self.assertEqual(packet["source_failures"][0]["ticker"], "034020")
            self.assertNotIn("ConnectionError", json.dumps(packet["source_failures"]))

    def test_all_source_failures_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = json.loads(MODULE.DEFAULT_DART.read_text(encoding="utf-8"))
            for ticker, stock in source["stocks"].items():
                source["stocks"][ticker] = {
                    "name": stock["name"], "atlas_stage": stock["atlas_stage"],
                    "coverage": stock["coverage"], "db_state": stock["db_state"],
                    "in_notion": stock["in_notion"], "status": "FAILED",
                    "error": "ConnectionError: injected",
                }
            source["summary"] = {"ok": 0, "failed": len(source["stocks"])}
            source_path = root / "latest_dart.json"
            content_path = root / "latest_dart_content.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
            content["source_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
            content_path.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.DartEventObservationError, "DART_ALL_STOCKS_FAILED"):
                MODULE.build_packet(
                    decision_at=DECISION_AT, source_path=source_path,
                    content_path=content_path,
                )

    def test_degraded_content_failure_isolated_to_one_filing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "latest_dart.json"
            content_path = root / "latest_dart_content.json"
            source_path.write_bytes(MODULE.DEFAULT_DART.read_bytes())
            content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
            failed = next(
                row for row in content["records"]
                if row["filing_identity"]["stock_code"] == "329180"
            )
            failed["operation"] = "failed"
            failed["publication_status"] = "FAILED"
            failed["reasons"] = ["PERSIST_OR_CACHE_FAILED:ConnectionError:injected"]
            content["run_status"] = "DEGRADED"
            content["counts"] = {"captured": 0, "failed": 1, "not_applicable": 1, "skipped": 0}
            content_path.write_text(json.dumps(content), encoding="utf-8")

            packet = MODULE.build_packet(
                decision_at=DECISION_AT, source_path=source_path,
                content_path=content_path,
            )
            failed_observation = next(
                row for row in packet["observations"] if row["subject_id"] == "329180"
            )
            self.assertEqual(failed_observation["evidence"]["status"], "CONTENT_CAPTURE_FAILED")
            self.assertIn("DART_CONTENT_CAPTURE_FAILED", failed_observation["blocked_reasons"])
            self.assertEqual(packet["summary"]["content_failure_count"], 1)

    def test_failed_content_run_preserves_metadata_observations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "latest_dart.json"
            content_path = root / "latest_dart_content.json"
            source_path.write_bytes(MODULE.DEFAULT_DART.read_bytes())
            content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
            content["run_status"] = "FAILED"
            content["counts"] = {"captured": 0, "failed": 1, "not_applicable": 0, "skipped": 0}
            content["records"] = []
            content["reasons"] = ["DartContentError:injected"]
            content_path.write_text(json.dumps(content), encoding="utf-8")

            packet = MODULE.build_packet(
                decision_at=DECISION_AT, source_path=source_path,
                content_path=content_path,
            )
            self.assertEqual(packet["summary"]["relevant_filing_count"], 2)
            self.assertEqual(packet["summary"]["content_failure_count"], 2)
            self.assertEqual(
                {row["evidence"]["status"] for row in packet["observations"]},
                {"CONTENT_RUN_FAILED"},
            )
            self.assertNotIn("injected", json.dumps(packet["observations"]))

    def test_retained_raw_member_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            shutil.copytree(ROOT / "data/dart_content", data_root / "dart_content")
            member = next((data_root / "dart_content").rglob("*.gz"))
            member.write_bytes(member.read_bytes() + b"tamper")
            with self.assertRaisesRegex(MODULE.DartEventObservationError, "DART_RAW_CONTENT_INVALID"):
                MODULE.build_packet(decision_at=DECISION_AT, data_root=data_root)

    def test_append_only_is_idempotent_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, created = MODULE.publish_append_only(self.packet, out_root=Path(temporary))
            second, repeated = MODULE.publish_append_only(self.packet, out_root=Path(temporary))
            self.assertEqual(first, second)
            self.assertTrue(created)
            self.assertFalse(repeated)
            first.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.DartEventObservationError, "CONTENT_ADDRESSED_PACKET_DRIFT"):
                MODULE.publish_append_only(self.packet, out_root=Path(temporary))

    def test_module_is_provider_free_and_has_no_policy_or_delivery_client(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports & {"requests", "urllib", "httpx", "aiohttp", "socket"})
        text = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn('"RATIFIED"', text)
        self.assertNotIn("send_notification", text)


class DartEventObservationWiringTests(unittest.TestCase):
    def test_workflow_runs_after_dart_content_and_before_sec_case_population(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        content = text.index("Capture DART filing content (P4-03)")
        observation = text.index("Populate DART Event Observations (P3-08)")
        sec = text.index("Populate SEC Event Discovery Cases (P3-08)")
        commit = text.index("- name: Commit data")
        self.assertLess(content, observation)
        self.assertLess(observation, sec)
        self.assertLess(sec, commit)
        block = text[observation:sec]
        self.assertIn("dart_event_observation.py", block)
        self.assertNotIn("DART_API_KEY", block)

    def test_authoritative_runner_registers_test_once(self):
        text = RUN_ALL.read_text(encoding="utf-8")
        self.assertEqual(text.count('"test/test_dart_event_observation.py"'), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
