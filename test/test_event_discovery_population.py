#!/usr/bin/env python3
"""P3-08 committed SEC event population and operational wiring regression."""
from __future__ import annotations

import ast
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery/event_population.py"
WORKFLOW = ROOT / ".github/workflows/collect.yml"
ORCHESTRATOR = ROOT / "briefing/daily_orchestrator.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POP = load_module("event_population_test", SOURCE)


class EventDiscoveryPopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = POP.build_population_inputs(
            repo_root=ROOT, decision_at="2026-08-25T01:00:00Z"
        )

    def _one_record_fixture(self, root: Path):
        live_record = next(
            row for row in self.live["records"]
            if row["ticker"] == "SNDK"
            and row["accession"] == "0001628280-26-053346"
        )
        records = root / "event_records.jsonl"
        records.write_text(json.dumps(live_record, ensure_ascii=False) + "\n", encoding="utf-8")
        source_dir = ROOT / "data/sec_content/SNDK/0001628280-26-053346"
        target_dir = root / "sec_content/SNDK/0001628280-26-053346"
        target_dir.mkdir(parents=True)
        for path in source_dir.iterdir():
            shutil.copy2(path, target_dir / path.name)
        return records, root, live_record, target_dir

    def test_real_committed_population_is_nine_cases_with_two_linked(self):
        packet = self.live["packet"]
        self.assertEqual(packet["summary"]["source_records"], 108)
        self.assertEqual(packet["summary"]["cases"], 9)
        self.assertEqual(packet["summary"][POP.CASE.EVIDENCE_LINKED], 2)
        self.assertEqual(packet["summary"][POP.CASE.EVIDENCE_UNRESOLVED], 7)
        linked = [row for row in packet["cases"] if row["evidence_status"] == "EVIDENCE_LINKED"]
        self.assertEqual({row["subject"] for row in linked}, {"SNDK"})
        self.assertEqual({row["event_type"] for row in linked}, {"Financial Results", "Other"})
        self.assertTrue(all(row["evidence_lineage"]["source_accession"] == "0001628280-26-053346" for row in linked))

    def test_real_primary_gzip_bytes_equal_manifest_hash_and_length(self):
        manifest_path = ROOT / "data/sec_content/SNDK/0001628280-26-053346/_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document = next(row for row in manifest["documents"] if row["kind"] == "primary")
        with gzip.open(manifest_path.parent / f"{document['document_name']}.gz", "rb") as handle:
            raw = handle.read()
        self.assertEqual(len(raw), document["content_bytes"])
        self.assertEqual(POP.hashlib.sha256(raw).hexdigest(), document["content_sha256"])
        linked = self.live["evidence_bindings"]["bindings"][0]["evidence"]
        self.assertEqual(linked["source_identity"]["source_sha256"], document["content_sha256"])

    def test_retained_content_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, data_root, _, target = self._one_record_fixture(root)
            gzip_path = next(path for path in target.iterdir() if path.name.endswith(".htm.gz"))
            gzip_path.write_bytes(gzip.compress(b"tampered"))
            with self.assertRaisesRegex(POP.EventPopulationError, "SEC_CONTENT_BYTES_INVALID"):
                POP.build_population_inputs(
                    repo_root=ROOT,
                    records_path=records,
                    data_root=data_root,
                    decision_at="2026-08-25T01:00:00Z",
                )

    def test_missing_retained_primary_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, data_root, _, target = self._one_record_fixture(root)
            gzip_path = next(path for path in target.iterdir() if path.name.endswith(".htm.gz"))
            gzip_path.unlink()
            with self.assertRaisesRegex(
                POP.EventPopulationError, "RETAINED_CONTENT_READ_FAILED"
            ):
                POP.build_population_inputs(
                    repo_root=ROOT,
                    records_path=records,
                    data_root=data_root,
                    decision_at="2026-08-25T01:00:00Z",
                )

    def test_manifest_authority_tamper_is_rejected_by_canonical_validator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, data_root, _, target = self._one_record_fixture(root)
            manifest_path = target / "_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["action"] = "PROMOTE"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                POP.EventPopulationError, "SEC_CONTENT_MANIFEST_INVALID"
            ):
                POP.build_population_inputs(
                    repo_root=ROOT,
                    records_path=records,
                    data_root=data_root,
                    decision_at="2026-08-25T01:00:00Z",
                )

    def test_manifest_identity_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, data_root, _, target = self._one_record_fixture(root)
            manifest_path = target / "_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["filing_identity"]["accession"] = "0001628280-26-000001"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                POP.EventPopulationError,
                "SEC_CONTENT_MANIFEST_INVALID|MANIFEST_ACCESSION_MISMATCH",
            ):
                POP.build_population_inputs(
                    repo_root=ROOT,
                    records_path=records,
                    data_root=data_root,
                    decision_at="2026-08-25T01:00:00Z",
                )

    def test_future_retrieval_never_links_early_decision(self):
        early = POP.build_population_inputs(
            repo_root=ROOT, decision_at="2026-08-15T01:00:00Z"
        )
        self.assertGreater(early["packet"]["summary"]["cases"], 0)
        self.assertEqual(early["packet"]["summary"][POP.CASE.EVIDENCE_LINKED], 0)
        self.assertTrue(all(row["evidence_status"] == "EVIDENCE_UNRESOLVED" for row in early["packet"]["cases"]))

    def test_records_known_after_decision_are_excluded(self):
        early = POP.build_population_inputs(
            repo_root=ROOT, decision_at="2026-08-19T01:00:00Z"
        )
        keys = {POP.CASE.D1.record_key(row) for row in early["records"]}
        self.assertNotIn("SNDK|0001363249-26-000066|1.0|d1_v1", keys)
        self.assertLess(len(early["records"]), len(self.live["records"]))

    def test_unsafe_subject_cannot_escape_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = copy.deepcopy(self.live["records"][0])
            record["ticker"] = "../SNDK"
            record["resolution"] = "resolved"
            record["event_types"] = ["Contract"]
            records = root / "records.jsonl"
            records.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(POP.EventPopulationError, "D1_SUBJECT_PATH_UNSAFE"):
                POP.build_population_inputs(
                    repo_root=ROOT,
                    records_path=records,
                    data_root=root,
                    decision_at="2026-08-25T01:00:00Z",
                )

    def test_missing_manifest_is_unresolved_not_fabricated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, _, _, _ = self._one_record_fixture(root)
            empty_data = root / "empty"
            empty_data.mkdir()
            result = POP.build_population_inputs(
                repo_root=ROOT,
                records_path=records,
                data_root=empty_data,
                decision_at="2026-08-25T01:00:00Z",
            )
            self.assertEqual(result["packet"]["summary"][POP.CASE.EVIDENCE_LINKED], 0)
            self.assertEqual(result["packet"]["summary"][POP.CASE.EVIDENCE_UNRESOLVED], 2)

    def test_append_only_same_packet_is_noop_and_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            path, created = POP.publish_append_only(
                out_root=out,
                decision_at="2026-08-25T01:00:00Z",
                packet=self.live["packet"],
            )
            self.assertTrue(created)
            same_path, created_again = POP.publish_append_only(
                out_root=out,
                decision_at="2026-08-25T01:00:00Z",
                packet=self.live["packet"],
            )
            self.assertEqual(path, same_path)
            self.assertFalse(created_again)
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(POP.EventPopulationError, "CONTENT_ADDRESSED_PACKET_DRIFT"):
                POP.publish_append_only(
                    out_root=out,
                    decision_at="2026-08-25T01:00:00Z",
                    packet=self.live["packet"],
                )

    def test_different_packets_are_preserved_as_distinct_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            path1, _ = POP.publish_append_only(
                out_root=out, decision_at="2026-08-25T01:00:00Z", packet=self.live["packet"]
            )
            empty = POP.CASE.build_packet(
                records=[],
                evidence_bindings={
                    "schema_version": POP.CASE.BINDING_SCHEMA_VERSION,
                    "binding_set_id": "empty-test-set",
                    "bindings": [],
                },
            )
            path2, _ = POP.publish_append_only(
                out_root=out, decision_at="2026-08-25T02:00:00Z", packet=empty
            )
            self.assertNotEqual(path1, path2)
            self.assertTrue(path1.exists())
            self.assertTrue(path2.exists())

    def test_all_investment_and_trading_authority_remains_closed(self):
        authority = self.live["packet"]["authority"]
        self.assertTrue(authority["case_recording_only"])
        self.assertTrue(all(value is False for key, value in authority.items() if key != "case_recording_only"))
        for case in self.live["packet"]["cases"]:
            self.assertEqual(case["importance_status"], "IMPORTANCE_UNRATIFIED")
            self.assertEqual(case["interpretation_status"], "INTERPRETATION_NOT_AUTHORIZED")
            self.assertEqual(case["promotion_status"], "PROMOTION_NOT_AUTHORIZED")
            self.assertIsNone(case["stage_transition"])
            self.assertIsNone(case["investment_action"])

    def test_population_module_has_no_provider_or_process_client(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "subprocess", "httpx"):
            self.assertNotIn(prohibited, imported)

    def test_daily_collect_wires_population_after_content_before_briefing(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        content_index = source.index("Capture SEC filing content (P4-02)")
        population_index = source.index("Populate SEC Event Discovery Cases (P3-08)")
        briefing_index = source.index("Build briefing read model (P0-03)")
        self.assertLess(content_index, population_index)
        self.assertLess(population_index, briefing_index)
        block = source[population_index:briefing_index]
        self.assertIn("discovery/event_population.py", block)
        self.assertNotIn("curl ", block)
        self.assertNotIn("workflow_dispatch", block)

    def test_daily_orchestrator_consumes_real_population_not_empty_fixture(self):
        source = ORCHESTRATOR.read_text(encoding="utf-8")
        self.assertIn("EVENT_POPULATION.build_population_inputs", source)
        rotation_body = source.split("def build_rotation_discovery", 1)[1].split(
            "def build_rule_evaluation", 1
        )[0]
        self.assertNotIn("DAILY_ORCHESTRATOR_NO_LIVE_BINDINGS", rotation_body)
        orchestrator = load_module("daily_orchestrator_event_population_test", ORCHESTRATOR)
        row = orchestrator.build_rotation_discovery("morning", "2026-08-25T01:00:00Z")
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(
            row["reason"], "EVENT_CASES_RECORDED_NO_IMPORTANCE_OR_PROMOTION_AUTHORITY"
        )
        discovery = row["packet"]["discovery"]
        self.assertEqual(discovery["case_count"], 9)
        self.assertEqual(discovery["evidence_counts"]["EVIDENCE_LINKED"], 2)
        self.assertEqual(discovery["new_candidates"], [])
        self.assertEqual(discovery["existing_candidate_changes"], [])


if __name__ == "__main__":
    unittest.main()
