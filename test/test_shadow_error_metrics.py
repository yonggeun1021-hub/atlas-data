#!/usr/bin/env python3
"""P10-03 Shadow error metrics regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "error_metrics.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("shadow_error_metrics", SOURCE)
CONTRACT = MODULE.load_contract()


def metric(metric_type, status):
    verified = status != "UNVERIFIED"
    marker = {
        "FALSE_POSITIVE": "a", "MISS": "b", "STALE": "c", "SILENT_ERROR": "d"
    }[metric_type]
    return {
        "metric_type": metric_type,
        "status": status,
        "assessed_at": "2026-08-21T04:00:00Z",
        "evidence_ref": f"test://audit/{metric_type}" if verified else None,
        "evidence_sha256": marker * 64 if verified else None,
    }


def assessment(
    assessment_id="ASSESSMENT.US.001",
    market="US",
    statuses=None,
):
    statuses = statuses or {
        "FALSE_POSITIVE": "PRESENT",
        "MISS": "ABSENT",
        "STALE": "ABSENT",
        "SILENT_ERROR": "UNVERIFIED",
    }
    return {
        "assessment_id": assessment_id,
        "decision_id": "atlas-2026-08-21-morning",
        "decision_date": "2026-08-21",
        "market": market,
        "window_id": "WINDOW.SAME.DAY.001",
        "assessed_at": "2026-08-21T04:00:00Z",
        "comparison_ref": "test://comparison/packet",
        "comparison_sha256": "e" * 64,
        "metrics": [metric(kind, statuses[kind]) for kind in CONTRACT["metric_types"]],
    }


def batch(rows=None):
    value = {
        "schema_version": "shadow_error_assessment_batch/1",
        "contract_version": "shadow_error_metrics/1",
        "batch_id": "SHADOW.AUDIT.BATCH.001",
        "observed_at": "2026-08-21T04:05:00Z",
        "assessments": [assessment()] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def by_metric(packet):
    return {row["metric_type"]: row for row in packet["metrics"]}


class ShadowErrorMetricsTests(unittest.TestCase):
    def test_contract_requires_all_four_metrics_and_closes_interpretation(self):
        self.assertEqual(CONTRACT["metric_types"], [
            "FALSE_POSITIVE", "MISS", "STALE", "SILENT_ERROR"
        ])
        self.assertEqual(CONTRACT["zero_denominator_policy"], "NULL_NOT_ZERO")
        self.assertTrue(CONTRACT["authority"]["error_metric_aggregation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "error_metric_aggregation_only":
                self.assertFalse(value, key)

    def test_present_absent_and_unverified_counts_use_verified_denominator(self):
        packet = MODULE.build_packet(batch(), CONTRACT)
        metrics = by_metric(packet)
        self.assertEqual(metrics["FALSE_POSITIVE"]["present_count"], 1)
        self.assertEqual(metrics["FALSE_POSITIVE"]["verified_denominator"], 1)
        self.assertEqual(metrics["FALSE_POSITIVE"]["rate"], "1.000000")
        self.assertEqual(metrics["MISS"]["absent_count"], 1)
        self.assertEqual(metrics["MISS"]["rate"], "0.000000")
        self.assertEqual(metrics["SILENT_ERROR"]["unverified_count"], 1)
        self.assertEqual(metrics["SILENT_ERROR"]["verified_denominator"], 0)
        self.assertIsNone(metrics["SILENT_ERROR"]["rate"])

    def test_zero_assessments_never_report_zero_percent(self):
        packet = MODULE.build_packet(batch([]), CONTRACT)
        self.assertEqual(packet["assessment_count"], 0)
        self.assertEqual(packet["summary"]["zero_denominator_metric_count"], 4)
        self.assertTrue(all(row["rate"] is None for row in packet["metrics"]))

    def test_mixed_assessments_have_deterministic_half_even_rates(self):
        rows = [
            assessment("ASSESSMENT.US.001", "US"),
            assessment(
                "ASSESSMENT.KR.001", "KOREA",
                {
                    "FALSE_POSITIVE": "ABSENT", "MISS": "PRESENT",
                    "STALE": "ABSENT", "SILENT_ERROR": "ABSENT",
                },
            ),
            assessment(
                "ASSESSMENT.CR.001", "CRYPTO",
                {
                    "FALSE_POSITIVE": "ABSENT", "MISS": "ABSENT",
                    "STALE": "PRESENT", "SILENT_ERROR": "UNVERIFIED",
                },
            ),
        ]
        packet = MODULE.build_packet(batch(rows), CONTRACT)
        metrics = by_metric(packet)
        self.assertEqual(metrics["FALSE_POSITIVE"]["rate"], "0.333333")
        self.assertEqual(metrics["MISS"]["rate"], "0.333333")
        self.assertEqual(packet["by_market"], {"COMMON": 0, "US": 1, "KOREA": 1, "CRYPTO": 1})

    def test_every_assessment_must_contain_all_metrics_in_fixed_order(self):
        missing = assessment()
        missing["metrics"] = missing["metrics"][:-1]
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "METRIC_SET_INVALID"):
            MODULE.build_packet(batch([missing]), CONTRACT)
        reordered = assessment()
        reordered["metrics"] = list(reversed(reordered["metrics"]))
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "METRIC_SET_INVALID"):
            MODULE.build_packet(batch([reordered]), CONTRACT)

    def test_verified_claim_requires_evidence_and_unverified_forbids_it(self):
        no_evidence = assessment()
        no_evidence["metrics"][0]["evidence_ref"] = None
        no_evidence["metrics"][0]["evidence_sha256"] = None
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "METRIC_EVIDENCE_REF_INVALID"):
            MODULE.build_packet(batch([no_evidence]), CONTRACT)
        hidden = assessment()
        hidden["metrics"][-1]["evidence_ref"] = "test://hidden"
        hidden["metrics"][-1]["evidence_sha256"] = "f" * 64
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "UNVERIFIED_HAS_EVIDENCE"):
            MODULE.build_packet(batch([hidden]), CONTRACT)

    def test_duplicate_population_and_authority_drift_fail_closed(self):
        duplicate = batch([assessment(), assessment("ASSESSMENT.US.002")])
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "ASSESSMENT_KEY_DUPLICATE"):
            MODULE.build_packet(duplicate, CONTRACT)
        drift = batch()
        drift["authority"]["performance_interpretation_authorized"] = True
        drift["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in drift.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "BATCH_IDENTITY_INVALID"):
            MODULE.build_packet(drift, CONTRACT)

    def test_output_never_creates_cause_strategy_change_or_action(self):
        packet = MODULE.build_packet(batch(), CONTRACT)
        self.assertIsNone(packet["summary"]["causal_conclusion"])
        self.assertIsNone(packet["summary"]["strategy_change"])
        self.assertFalse(packet["authority"]["action_generation_authorized"])
        tampered = copy.deepcopy(packet)
        tampered["summary"]["strategy_change"] = "REDUCE"
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "PACKET_CONTENT_MISMATCH"):
            MODULE.validate_packet(tampered, batch(), CONTRACT)

    def test_build_is_deterministic_hash_bound_and_input_immutable(self):
        source = batch()
        before = MODULE.canonical_json(source)
        first = MODULE.build_packet(source, CONTRACT)
        second = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(source), before)
        self.assertEqual(first["lineage"]["assessment_batch_sha256"], source["packet_sha256"])
        digest = copy.deepcopy(first)
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "PACKET_CONTENT_MISMATCH"):
            MODULE.validate_packet(digest, source, CONTRACT)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps(batch()), encoding="utf-8")
            output = temp / "out" / "metrics.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertEqual(json.loads(output.read_text())["assessment_count"], 1)
            forbidden = ROOT / "data" / "shadow_error_metrics_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
