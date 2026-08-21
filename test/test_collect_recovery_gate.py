#!/usr/bin/env python3
"""P0-02 06:57 Recovery Action Gate regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".github" / "scripts" / "evaluate_collect_recovery_gate.py"
BUILDER = ROOT / ".github" / "scripts" / "build_briefing_inputs.py"
DATA = ROOT / "data"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("collect_recovery_gate", SOURCE)
CONTRACT = MODULE.load_contract()


class CollectRecoveryGateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data = Path(self.tempdir.name) / "data"
        self.data.mkdir()
        for name in ("krx", "dart", "sec"):
            shutil.copy2(DATA / f"latest_{name}.json", self.data / f"latest_{name}.json")
        self.today = json.loads((self.data / "latest_krx.json").read_text())["collected_for_kst_date"]
        builder = load_module(f"recovery_gate_builder_{id(self)}", BUILDER)
        builder.DATA = self.data
        builder.OUT = self.data / "briefing"
        builder.HEALTH = self.data / "briefing_status.json"
        builder.run(self.today)

    def tearDown(self):
        self.tempdir.cleanup()

    def at(self, time_value):
        return f"{self.today}T{time_value}+09:00"

    def build(self, time_value="06:57:00", readiness_module=None):
        return MODULE.build_packet(
            self.today,
            self.at(time_value),
            self.data,
            CONTRACT,
            readiness_module=readiness_module,
        )

    def test_contract_forbids_dispatch_schedule_and_pre_gate_recovery(self):
        self.assertEqual(CONTRACT["gate_open_time_kst"], "06:57:00")
        self.assertEqual(CONTRACT["automatic_dispatch_policy"], "PROHIBITED")
        self.assertTrue(CONTRACT["authority"]["source_read_only_classification_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "source_read_only_classification_only":
                self.assertFalse(value, key)

    def test_before_0657_defers_without_reading_or_declaring_failure(self):
        class ReadinessMustNotRun:
            @staticmethod
            def evaluate(*_args):
                raise AssertionError("readiness must not run before 06:57")

        packet = self.build("06:56:59", ReadinessMustNotRun)
        self.assertEqual(packet["classification"], "RECOVERY_WINDOW_OPEN")
        self.assertEqual(packet["gate_timing"]["timing_status"], "NOT_OPEN")
        self.assertEqual(packet["gate_timing"]["delay_seconds"], -1)
        self.assertIsNone(packet["readiness"])
        self.assertFalse(packet["alert"]["required"])
        self.assertFalse(packet["manual_recovery"]["required"])
        self.assertFalse(packet["automatic_workflow_dispatch_authorized"])
        self.assertFalse(packet["workflow_dispatch_executed"])

    def test_ready_current_files_at_gate_require_no_action(self):
        packet = self.build()
        self.assertEqual(packet["classification"], "DATA_READY")
        self.assertTrue(packet["data_ready"])
        self.assertTrue(packet["read_model_ready"])
        self.assertEqual(packet["recovery_action"], "none")
        self.assertEqual(packet["gate_timing"]["timing_status"], "WITHIN_CANDIDATE_WINDOW")
        self.assertEqual(packet["readiness"]["classification"], "data_ready_read_model_ready")

    def test_read_model_degradation_never_requests_collector_rerun(self):
        (self.data / "briefing_status.json").unlink()
        packet = self.build("06:58:30")
        self.assertEqual(packet["classification"], "DATA_READY_BRIEFING_READ_MODEL_DEGRADED")
        self.assertTrue(packet["data_ready"])
        self.assertFalse(packet["read_model_ready"])
        self.assertTrue(packet["read_model_repair_candidate"])
        self.assertEqual(packet["alert"], {"required": True, "kind": "BRIEFING_READ_MODEL_DEGRADED"})
        self.assertFalse(packet["manual_recovery"]["required"])
        self.assertFalse(packet["manual_recovery"]["guidance_allowed"])
        self.assertIn("collector_rerun_prohibited", packet["recovery_action"])
        self.assertFalse(packet["workflow_dispatch_executed"])

    def test_confirmed_collector_stale_allows_guidance_but_never_dispatches(self):
        path = self.data / "latest_krx.json"
        value = json.loads(path.read_text())
        value["collected_for_kst_date"] = "1999-12-31"
        path.write_text(json.dumps(value), encoding="utf-8")
        packet = self.build("06:58:31")
        self.assertEqual(packet["classification"], "DATA_NOT_READY")
        self.assertFalse(packet["data_ready"])
        self.assertTrue(packet["manual_recovery"]["required"])
        self.assertTrue(packet["manual_recovery"]["guidance_allowed"])
        self.assertTrue(packet["manual_recovery"]["cio_approval_required"])
        self.assertEqual(packet["recovery_action"], "manual_recovery_requires_cio_approval")
        self.assertFalse(packet["automatic_workflow_dispatch_authorized"])
        self.assertFalse(packet["workflow_dispatch_executed"])

    def test_unreadable_raw_is_unknown_not_data_not_ready(self):
        (self.data / "latest_sec.json").write_text('{"summary":', encoding="utf-8")
        packet = self.build()
        self.assertEqual(packet["classification"], "UNKNOWN_MANUAL_INSPECTION_REQUIRED")
        self.assertIsNone(packet["data_ready"])
        self.assertIsNone(packet["read_model_ready"])
        self.assertEqual(packet["alert"]["kind"], "MANUAL_INSPECTION_REQUIRED")
        self.assertEqual(packet["recovery_action"], "manual_inspection")
        self.assertFalse(packet["manual_recovery"]["guidance_allowed"])

    def test_actual_gate_time_is_classified_at_exact_boundaries(self):
        cases = [
            ("06:58:30", "WITHIN_CANDIDATE_WINDOW", "GATE_ROLE_MAINTAIN_CANDIDATE", 90),
            ("06:58:31", "LATE_WARNING_REVIEW_REQUIRED", "GATE_TIMING_WARNING_REVIEW_REQUIRED", 91),
            ("07:00:00", "LATE_WARNING_REVIEW_REQUIRED", "GATE_TIMING_WARNING_REVIEW_REQUIRED", 180),
            ("07:00:01", "ROLE_UNSUITABLE", "GATE_ROLE_UNSUITABLE", 181),
        ]
        for time_value, status, assessment, delay in cases:
            with self.subTest(time=time_value):
                timing = self.build(time_value)["gate_timing"]
                self.assertEqual(timing["timing_status"], status)
                self.assertEqual(timing["role_assessment"], assessment)
                self.assertEqual(timing["delay_seconds"], delay)

    def test_kst_offset_date_and_timestamp_are_exact(self):
        with self.assertRaisesRegex(MODULE.RecoveryGateError, "EVALUATED_AT_KST_INVALID"):
            MODULE.build_packet(self.today, f"{self.today}T06:57:00Z", self.data, CONTRACT)
        with self.assertRaisesRegex(MODULE.RecoveryGateError, "EVALUATED_DATE_MISMATCH"):
            MODULE.build_packet("2026-08-20", "2026-08-21T06:57:00+09:00", self.data, CONTRACT)
        with self.assertRaisesRegex(MODULE.RecoveryGateError, "EXPECTED_KST_DATE_INVALID"):
            MODULE.build_packet("not-a-date", self.at("06:57:00"), self.data, CONTRACT)

    def test_packet_is_deterministic_tamper_evident_and_append_only(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(MODULE.validate_packet(first, CONTRACT), first)
        tampered = copy.deepcopy(first)
        tampered["workflow_dispatch_executed"] = True
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.RecoveryGateError, "PACKET_CONTENT_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)
        target = MODULE.record_packet(self.data, first)
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text()), first)
        self.assertFalse(list(target.parent.glob(".*")))
        with self.assertRaisesRegex(MODULE.RecoveryGateError, "APPEND_ONLY_VIOLATION"):
            MODULE.record_packet(self.data, first)

    def test_helper_is_offline_and_does_not_import_dispatch_or_network_clients(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git", "github"):
            self.assertNotIn(prohibited, imported)
        text = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("gh workflow run", text)
        self.assertNotIn("curl ", text)
        self.assertNotIn("workflow_dispatch(", text)


if __name__ == "__main__":
    unittest.main()
