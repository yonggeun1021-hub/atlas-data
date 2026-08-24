#!/usr/bin/env python3

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / ".github/scripts/check_briefing_readiness.py"
BUILDER = ROOT / ".github/scripts/build_briefing_inputs.py"
DATA = ROOT / "data"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BriefingReadinessTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data = Path(self.tempdir.name) / "data"
        self.data.mkdir()

        for name in ("krx", "dart", "sec"):
            shutil.copy2(
                DATA / f"latest_{name}.json",
                self.data / f"latest_{name}.json",
            )

        self.checker = load_module("briefing_readiness", CHECKER)
        self.builder = load_module("briefing_builder", BUILDER)
        self.builder.DATA = self.data
        self.builder.OUT = self.data / "briefing"
        self.builder.HEALTH = self.data / "briefing_status.json"

        krx = json.loads((self.data / "latest_krx.json").read_text())
        self.today = krx["collected_for_kst_date"]
        self.builder.run(self.today)

    def tearDown(self):
        self.tempdir.cleanup()

    def evaluate(self):
        return self.checker.evaluate(self.today, self.data)

    def test_complete_bundle_is_ready(self):
        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_ready",
        )
        self.assertTrue(result["data_ready"])
        self.assertTrue(result["read_model_ready"])
        self.assertEqual(result["recovery_action"], "none")
        self.assertEqual(result["reasons"], [])

    def test_missing_health_is_read_model_degraded_not_data_failure(self):
        (self.data / "briefing_status.json").unlink()

        result = self.evaluate()
        self.checker.persist_health(result, self.data)
        persisted = json.loads(
            (self.data / "briefing_status.json").read_text()
        )

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertTrue(result["data_ready"])
        self.assertFalse(result["read_model_ready"])
        self.assertEqual(
            result["recovery_action"],
            "repair_read_model_only",
        )
        self.assertEqual(
            persisted["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertTrue(persisted["data_ready"])
        self.assertFalse(persisted["read_model_ready"])
        self.assertEqual(
            persisted["recovery_action"],
            "repair_read_model_only",
        )

    def test_stale_published_status_uses_current_raw_fallback(self):
        status_path = self.data / "briefing" / "step0_status.json"
        status = json.loads(status_path.read_text())
        status["expected_kst_date"] = "1999-12-31"
        status_path.write_text(json.dumps(status), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertEqual(
            result["recovery_action"],
            "repair_read_model_only",
        )
        self.assertIn(
            "read_model:expected_kst_date_mismatch",
            result["reasons"],
        )

    def test_source_hash_mismatch_is_read_model_degraded(self):
        compact_path = self.data / "briefing" / "krx" / "005930.json"
        compact = json.loads(compact_path.read_text())
        compact["source"]["source_sha256"] = "0" * 64
        compact_path.write_text(json.dumps(compact), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn(
            "krx:005930:source_sha_mismatch",
            result["reasons"],
        )

    def test_inventory_mismatch_is_read_model_degraded(self):
        status_path = self.data / "briefing" / "step0_status.json"
        status = json.loads(status_path.read_text())
        status["read_model_inventory"]["health_path"] = "wrong.json"
        status_path.write_text(json.dumps(status), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn("read_model:inventory_mismatch", result["reasons"])

    def test_operations_telemetry_is_not_readiness_authority(self):
        status_path = self.data / "briefing" / "step0_status.json"
        status = json.loads(status_path.read_text())
        telemetry_path = self.data / "operations" / "collect_runs"
        self.assertFalse(telemetry_path.exists())

        status["read_model_inventory"]["operations_telemetry_sources"] = [
            "data/operations/collect_runs/missing/index.json"
        ]
        status_path.write_text(json.dumps(status), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_ready",
        )
        self.assertEqual(result["reasons"], [])

    def test_unknown_inventory_field_remains_read_model_drift(self):
        status_path = self.data / "briefing" / "step0_status.json"
        status = json.loads(status_path.read_text())
        status["read_model_inventory"]["unexpected_authority"] = True
        status_path.write_text(json.dumps(status), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn("read_model:inventory_mismatch", result["reasons"])

    def test_confirmed_stale_collector_requires_collection_recovery(self):
        krx_path = self.data / "latest_krx.json"
        krx = json.loads(krx_path.read_text())
        krx["collected_for_kst_date"] = "1999-12-31"
        krx_path.write_text(json.dumps(krx), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(result["classification"], "data_not_ready")
        self.assertFalse(result["data_ready"])
        self.assertEqual(result["recovery_action"], "workflow_dispatch")

    def test_truncated_raw_requires_manual_inspection(self):
        (self.data / "latest_krx.json").write_text(
            '{"summary":{"ok":5,"failed":0},',
            encoding="utf-8",
        )

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "unknown_manual_inspection_required",
        )
        self.assertTrue(result["manual_inspection_required"])
        self.assertEqual(result["recovery_action"], "manual_inspection")

    def test_invalid_expected_date_requires_manual_inspection(self):
        result = self.checker.evaluate("not-a-date", self.data)

        self.assertEqual(
            result["classification"],
            "unknown_manual_inspection_required",
        )
        self.assertEqual(
            result["reasons"],
            ["expected_kst_date_invalid"],
        )

    def test_ready_health_is_stable_when_checked_again(self):
        first = self.evaluate()
        self.checker.persist_health(first, self.data)
        second = self.evaluate()

        self.assertEqual(
            second["classification"],
            "data_ready_read_model_ready",
        )
        self.assertEqual(second["reasons"], [])

    def test_compact_inventory_mismatch_is_degraded(self):
        (self.data / "briefing" / "krx" / "005930.json").unlink()

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn("krx:compact_inventory_mismatch", result["reasons"])

    # ── P0-05A: Read Model Generation Contract ──────────────────────────

    def test_consistent_generation_is_ready_and_persisted(self):
        result = self.evaluate()
        self.checker.persist_health(result, self.data)
        persisted = json.loads(
            (self.data / "briefing_status.json").read_text()
        )

        step0 = json.loads(
            (self.data / "briefing" / "step0_status.json").read_text()
        )

        self.assertEqual(result["classification"], "data_ready_read_model_ready")
        self.assertTrue(result["generation"]["generation_id"])
        self.assertEqual(
            result["generation"]["generation_id"],
            step0["generation"]["generation_id"],
        )
        self.assertEqual(
            persisted["generation"]["generation_id"],
            result["generation"]["generation_id"],
        )

    def test_generation_id_is_stable_across_repeat_evaluation(self):
        first = self.evaluate()
        second = self.evaluate()

        self.assertEqual(
            first["generation"]["generation_id"],
            second["generation"]["generation_id"],
        )

    def test_compact_generation_mismatch_is_degraded(self):
        compact_path = self.data / "briefing" / "krx" / "005930.json"
        compact = json.loads(compact_path.read_text())
        compact["generation"]["generation_id"] = "0" * 64
        compact_path.write_text(json.dumps(compact), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn(
            "krx:005930:generation_id_mismatch",
            result["reasons"],
        )

    def test_compact_missing_generation_id_is_degraded(self):
        compact_path = self.data / "briefing" / "krx" / "005930.json"
        compact = json.loads(compact_path.read_text())
        del compact["generation"]
        compact_path.write_text(json.dumps(compact), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn(
            "krx:005930:generation_id_missing",
            result["reasons"],
        )

    def test_health_generation_mismatch_is_degraded(self):
        # Simulates the build step's own health canary disagreeing with
        # what it published in step0 -- the "step0 gen A / health gen B"
        # case.
        health_path = self.data / "briefing_status.json"
        health = json.loads(health_path.read_text())
        health["generation"]["generation_id"] = "0" * 64
        health_path.write_text(json.dumps(health), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn("health:generation_id_mismatch", result["reasons"])

    def test_step0_generation_id_tampered_alone_is_degraded(self):
        # "Generation manifest tampered": every other field (collectors,
        # dates, hashes, compact views) is left perfectly consistent --
        # only the declared generation_id itself is forged. No other
        # existing per-field check would catch this; only independent
        # recomputation does.
        status_path = self.data / "briefing" / "step0_status.json"
        status = json.loads(status_path.read_text())
        status["generation"]["generation_id"] = "0" * 64
        status_path.write_text(json.dumps(status), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn("read_model:generation_id_mismatch", result["reasons"])

    def test_step0_missing_generation_is_degraded(self):
        status_path = self.data / "briefing" / "step0_status.json"
        status = json.loads(status_path.read_text())
        del status["generation"]
        status_path.write_text(json.dumps(status), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn("read_model:generation_id_missing", result["reasons"])

    def test_same_date_different_source_content_breaks_generation_binding(self):
        # A collector re-ran and produced different bytes for the same
        # collected_for_kst_date without the read model being rebuilt --
        # source_sha256 changes, generation_id no longer matches either.
        krx_path = self.data / "latest_krx.json"
        krx = json.loads(krx_path.read_text())
        krx["stocks"]["005930"]["name"] = "recollected-same-day"
        krx_path.write_text(json.dumps(krx), encoding="utf-8")

        result = self.evaluate()

        self.assertEqual(
            result["classification"],
            "data_ready_read_model_degraded",
        )
        self.assertIn("read_model:krx:source_sha_mismatch", result["reasons"])
        self.assertIn("read_model:generation_id_mismatch", result["reasons"])

    def test_self_consistent_old_generation_is_stale_not_ready(self):
        # Generation consistency != freshness. Every file on disk agrees
        # with every other file -- but they all agree on YESTERDAY's
        # generation. A newer expected_kst_date must still fail as
        # data_not_ready, not be waved through because the generation
        # binding itself checks out.
        result = self.checker.evaluate("2099-01-01", self.data)

        self.assertEqual(result["classification"], "data_not_ready")
        self.assertFalse(result["data_ready"])
        # Never reached generation validation -- confirms the two concerns
        # are independent, not that generation happened to "pass".
        self.assertIsNone(result["generation"])


if __name__ == "__main__":
    unittest.main()
