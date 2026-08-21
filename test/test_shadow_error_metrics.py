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
COMPARISON_FIXTURE = load_module(
    "shadow_metrics_comparison_fixture",
    ROOT / "test" / "test_atlas_legacy_comparison.py",
)
CONTRACT = MODULE.load_contract()
COMPARISON_PACKET = COMPARISON_FIXTURE.MODULE.build_packet(
    COMPARISON_FIXTURE.shadow_ledger(),
    COMPARISON_FIXTURE.legacy(),
    COMPARISON_FIXTURE.outcomes(),
    "2026-08-21T03:05:00Z",
    COMPARISON_FIXTURE.CONTRACT,
)


def comparison():
    return copy.deepcopy(COMPARISON_PACKET)


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
        "comparison_sha256": COMPARISON_PACKET["packet_sha256"],
        "metrics": [metric(kind, statuses[kind]) for kind in CONTRACT["metric_types"]],
    }


def batch(rows=None):
    value = {
        "schema_version": CONTRACT["input_schema_version"],
        "contract_version": CONTRACT["contract_version"],
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
        packet = MODULE.build_packet(batch(), comparison(), CONTRACT)
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
        packet = MODULE.build_packet(batch([]), comparison(), CONTRACT)
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
        packet = MODULE.build_packet(batch(rows), comparison(), CONTRACT)
        metrics = by_metric(packet)
        self.assertEqual(metrics["FALSE_POSITIVE"]["rate"], "0.333333")
        self.assertEqual(metrics["MISS"]["rate"], "0.333333")
        self.assertEqual(packet["by_market"], {"COMMON": 0, "US": 1, "KOREA": 1, "CRYPTO": 1})

    def test_every_assessment_must_contain_all_metrics_in_fixed_order(self):
        missing = assessment()
        missing["metrics"] = missing["metrics"][:-1]
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "METRIC_SET_INVALID"):
            MODULE.build_packet(batch([missing]), comparison(), CONTRACT)
        reordered = assessment()
        reordered["metrics"] = list(reversed(reordered["metrics"]))
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "METRIC_SET_INVALID"):
            MODULE.build_packet(batch([reordered]), comparison(), CONTRACT)

    def test_verified_claim_requires_evidence_and_unverified_forbids_it(self):
        no_evidence = assessment()
        no_evidence["metrics"][0]["evidence_ref"] = None
        no_evidence["metrics"][0]["evidence_sha256"] = None
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "METRIC_EVIDENCE_REF_INVALID"):
            MODULE.build_packet(batch([no_evidence]), comparison(), CONTRACT)
        hidden = assessment()
        hidden["metrics"][-1]["evidence_ref"] = "test://hidden"
        hidden["metrics"][-1]["evidence_sha256"] = "f" * 64
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "UNVERIFIED_HAS_EVIDENCE"):
            MODULE.build_packet(batch([hidden]), comparison(), CONTRACT)

    def test_duplicate_population_and_authority_drift_fail_closed(self):
        duplicate = batch([assessment(), assessment("ASSESSMENT.US.002")])
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "ASSESSMENT_KEY_DUPLICATE"):
            MODULE.build_packet(duplicate, comparison(), CONTRACT)
        drift = batch()
        drift["authority"]["performance_interpretation_authorized"] = True
        drift["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in drift.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "BATCH_IDENTITY_INVALID"):
            MODULE.build_packet(drift, comparison(), CONTRACT)

    def test_assessments_are_bound_to_exact_comparison_key_window_and_sha(self):
        wrong_sha = assessment()
        wrong_sha["comparison_sha256"] = "f" * 64
        wrong_window = assessment()
        wrong_window["window_id"] = "WINDOW.OTHER.001"
        wrong_key = assessment()
        wrong_key["decision_id"] = "atlas-2026-08-20-morning"
        wrong_key["decision_date"] = "2026-08-20"
        cases = [
            (wrong_sha, "COMPARISON_SHA_MISMATCH"),
            (wrong_window, "COMPARISON_WINDOW_MISMATCH"),
            (wrong_key, "COMPARISON_KEY_MISSING"),
        ]
        for row, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.ShadowErrorMetricsError, error
            ):
                MODULE.build_packet(batch([row]), comparison(), CONTRACT)

    def test_self_rehashed_embedded_comparison_authority_tamper_fails_closed(self):
        packet = MODULE.build_packet(batch(), comparison(), CONTRACT)
        embedded = packet["source_packets"]["ATLAS_LEGACY_COMPARISON"]
        embedded["authority"]["winner_selection_authorized"] = True
        embedded["packet_sha256"] = COMPARISON_FIXTURE.MODULE.payload_sha256(
            {key: value for key, value in embedded.items() if key != "packet_sha256"}
        )
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ShadowErrorMetricsError, "COMPARISON_PACKET_INVALID"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_output_never_creates_cause_strategy_change_or_action(self):
        packet = MODULE.build_packet(batch(), comparison(), CONTRACT)
        self.assertIsNone(packet["summary"]["causal_conclusion"])
        self.assertIsNone(packet["summary"]["strategy_change"])
        self.assertFalse(packet["authority"]["action_generation_authorized"])
        tampered = copy.deepcopy(packet)
        tampered["summary"]["strategy_change"] = "REDUCE"
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "PACKET_CONTENT_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_build_is_deterministic_hash_bound_and_input_immutable(self):
        source = batch()
        before = MODULE.canonical_json(source)
        first = MODULE.build_packet(source, comparison(), CONTRACT)
        second = MODULE.build_packet(source, comparison(), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(source), before)
        self.assertEqual(first["lineage"]["assessment_batch_sha256"], source["packet_sha256"])
        self.assertEqual(
            first["lineage"]["comparison_packet_sha256"],
            COMPARISON_PACKET["packet_sha256"],
        )
        self.assertEqual(first["source_packets"]["ASSESSMENT_BATCH"], source)
        self.assertEqual(
            first["source_packets"]["ATLAS_LEGACY_COMPARISON"], COMPARISON_PACKET
        )
        digest = copy.deepcopy(first)
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ShadowErrorMetricsError, "PACKET_CONTENT_MISMATCH"):
            MODULE.validate_packet(digest, CONTRACT)

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
            comparison_path = temp / "comparison.json"
            input_path.write_text(json.dumps(batch()), encoding="utf-8")
            comparison_path.write_text(json.dumps(comparison()), encoding="utf-8")
            output = temp / "out" / "metrics.json"
            self.assertEqual(MODULE.run(input_path, comparison_path, output), 0)
            self.assertEqual(json.loads(output.read_text())["assessment_count"], 1)
            forbidden = ROOT / "data" / "shadow_error_metrics_test.json"
            self.assertEqual(MODULE.run(input_path, comparison_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
