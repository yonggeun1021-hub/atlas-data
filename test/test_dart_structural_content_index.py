#!/usr/bin/env python3
"""P4-03 retained DART document structural-index regression."""
from __future__ import annotations

import ast
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery/dart_structural_content_index.py"
WORKFLOW = ROOT / ".github/workflows/collect.yml"
RUN_ALL = ROOT / "run_all.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = load_module("dart_structural_content_index_test", SOURCE)


def current_decision_at() -> str:
    source = json.loads(MODULE.DEFAULT_SOURCE.read_text(encoding="utf-8"))
    content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
    values = [source["collected_at_utc"], content["observed_at_utc"]]
    return max(
        dt.datetime.fromisoformat(value.replace("Z", "+00:00")) for value in values
    ).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


DECISION_AT = current_decision_at()
RETAINED_DECISION_AT = "2026-08-31T21:00:39Z"


def retained_raw_fixture(root: Path) -> tuple[Path, Path, Path]:
    """Bind raw-index assertions to one retained historical filing.

    Rolling latest inputs may legitimately be metadata-only.  This bounded
    fixture retains the matching historical source snapshot plus exactly one
    manifest, ZIP, and member payload for raw-byte tests.
    """
    subject_id = "012450"
    rcept_no = "20260831800137"
    source = json.loads((ROOT / "data/2026-09-01/dart.json").read_text(encoding="utf-8"))
    source["stocks"] = {subject_id: source["stocks"][subject_id]}
    source["summary"] = {"ok": 1, "failed": 0}
    source_path = root / "dart.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")

    record = json.loads((
        ROOT / "data/dart_content" / subject_id / rcept_no / "_manifest.json"
    ).read_text(encoding="utf-8"))
    record["publication_status"] = "OK"
    content = json.loads(MODULE.DEFAULT_CONTENT.read_text(encoding="utf-8"))
    content.update({
        "source_file": str(source_path),
        "collected_for_kst_date": source["collected_for_kst_date"],
        "observed_at_utc": record["retrieved_at_utc"],
        "run_status": "OK",
        "records": [record],
        "counts": {"captured": 0, "failed": 0, "not_applicable": 0, "skipped": 1},
    })
    content["source_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    content_path = root / "dart_content.json"
    content_path.write_text(json.dumps(content), encoding="utf-8")

    data_root = root / "data"
    shutil.copytree(
        ROOT / "data/dart_content" / subject_id / rcept_no,
        data_root / "dart_content" / subject_id / rcept_no,
    )
    return source_path, content_path, data_root


def build_retained_raw_packet(root: Path) -> tuple[dict, Path, Path, Path]:
    source_path, content_path, data_root = retained_raw_fixture(root)
    return (
        MODULE.build_packet(
            decision_at=RETAINED_DECISION_AT, source_path=source_path,
            content_path=content_path, data_root=data_root,
        ),
        source_path,
        content_path,
        data_root,
    )


class DartStructuralContentIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = MODULE.build_packet(decision_at=DECISION_AT)

    def test_real_retained_filings_are_indexed_without_semantic_items(self):
        with tempfile.TemporaryDirectory() as temporary:
            packet, source_path, content_path, data_root = build_retained_raw_packet(Path(temporary))
            self.assertEqual(packet["schema_version"], "dart_structural_content_index_packet/1")
            self.assertEqual(packet["status"], "STRUCTURAL_INDEX_RECORDED_ITEM_EXTRACTION_UNRATIFIED")
            source_observations = MODULE.DART_OBSERVATION.build_packet(
                decision_at=RETAINED_DECISION_AT, source_path=source_path,
                content_path=content_path, data_root=data_root,
            )["observations"]
            raw_source_identities = {(row["subject_id"], row["rcept_no"]) for row in source_observations}
            indexed_identities = {(row["subject_id"], row["rcept_no"]) for row in packet["indexed_filings"]}
            self.assertEqual(packet["summary"]["source_observation_count"], len(source_observations))
            self.assertEqual(packet["summary"]["raw_bytes_verified_count"], len(raw_source_identities))
            self.assertEqual(packet["summary"]["indexed_filing_count"], len(packet["indexed_filings"]))
            self.assertEqual(indexed_identities, raw_source_identities)
            self.assertEqual(packet["summary"]["indexed_document_count"], len(packet["documents"]))
            self.assertEqual(packet["summary"]["text_document_count"], sum(
                document["status"] == "STRUCTURE_ONLY_ITEM_EXTRACTION_UNRATIFIED" for document in packet["documents"]
            ))
            self.assertGreater(len(packet["indexed_filings"]), 0)
            self.assertGreater(packet["summary"]["table_count"], 0)
            self.assertGreater(packet["summary"]["row_count"], 0)
            self.assertGreater(packet["summary"]["cell_count"], 0)
            self.assertEqual(packet["summary"]["semantic_item_count"], 0)
            self.assertTrue(all(document["semantic_items"] == [] for document in packet["documents"]))

    def test_metadata_only_filing_is_not_presented_as_content_indexed(self):
        # Latest may contain no filings; retain a nonempty, exact identity here.
        with tempfile.TemporaryDirectory() as temporary:
            source_path, content_path, data_root = retained_raw_fixture(Path(temporary))
            source = json.loads(source_path.read_text(encoding="utf-8"))
            content = json.loads(content_path.read_text(encoding="utf-8"))
            record = content["records"][0]
            subject_id = record["filing_identity"]["stock_code"]
            rcept_no = record["filing_identity"]["rcept_no"]
            source["stocks"][subject_id]["atlas_stage"] = None
            record.update({
                "atlas_stage": None,
                "capture_policy": "index_only",
                "content_status": "NOT_APPLICABLE",
                "evidence_status": "NOT_APPLICABLE",
                "documents": [],
                "extracted": [],
                "publication_status": "NOT_APPLICABLE",
                "reasons": ["STAGE_NOT_ASSIGNED_FOR_AUTO_CONSUMPTION"],
                "source_archive": None,
            })
            for key in ("operation", "retrieved_at_utc", "skip_reason"):
                record.pop(key, None)
            content["counts"] = {"captured": 0, "failed": 0, "not_applicable": 1, "skipped": 0}
            source_path.write_text(json.dumps(source), encoding="utf-8")
            content["source_sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
            content_path.write_text(json.dumps(content), encoding="utf-8")
            inputs = dict(decision_at=RETAINED_DECISION_AT, source_path=source_path,
                          content_path=content_path, data_root=data_root)
            observations = MODULE.DART_OBSERVATION.build_packet(**inputs)["observations"]
            self.assertEqual(
                {(row["subject_id"], row["rcept_no"], row["evidence"]["status"])
                 for row in observations},
                {(subject_id, rcept_no, "METADATA_ONLY_STAGE_NOT_ASSIGNED")},
            )
            packet = MODULE.build_packet(**inputs)
            self.assertEqual(packet["summary"]["source_observation_count"], 1)
            self.assertEqual(packet["summary"]["raw_bytes_verified_count"], 0)
            self.assertEqual(packet["indexed_filings"], [])
            self.assertEqual(packet["documents"], [])
            self.assertEqual(packet["summary"]["semantic_item_count"], 0)

    def test_packet_retains_no_filing_text_attribute_values_or_company_name(self):
        rendered = MODULE.canonical_json(self.packet)
        for forbidden in (
            "HD현대중공업",
            "단일판매",
            "XFormD1_Form0_Table0",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_structure_helper_discards_text_and_attribute_values(self):
        raw = (
            b'<html><table id="PRIVATE_VALUE"><tr><td name="SECRET">'
            b'999999 SECRET_TEXT</td></tr></table></html>'
        )
        indexed = MODULE.structural_index(raw, "sample.html", {".html"})
        rendered = MODULE.canonical_json(indexed)
        self.assertEqual(indexed["table_count"], 1)
        self.assertEqual(indexed["row_count"], 1)
        self.assertEqual(indexed["cell_count"], 1)
        self.assertEqual(indexed["locator_attribute_count"], 2)
        self.assertNotIn("PRIVATE_VALUE", rendered)
        self.assertNotIn("SECRET_TEXT", rendered)
        self.assertNotIn("999999", rendered)

    def test_binary_member_is_explicitly_not_applicable(self):
        indexed = MODULE.structural_index(b"\x00\x01", "image.png", {".xml", ".html"})
        self.assertEqual(indexed["status"], "NOT_APPLICABLE_BINARY")
        self.assertIsNone(indexed["structure_sha256"])
        self.assertIsNone(indexed["table_count"])

    def test_all_authority_and_effect_counts_remain_closed(self):
        self.assertTrue(self.packet["authority"]["structural_evidence_only"])
        self.assertTrue(all(
            value is False
            for key, value in self.packet["authority"].items()
            if key != "structural_evidence_only"
        ))
        for key in (
            "semantic_item_count",
            "rule_evaluation_count",
            "stage_promotion_count",
            "action_count",
            "order_count",
        ):
            self.assertEqual(self.packet["summary"][key], 0)

    def test_append_only_publication_keeps_exact_input_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packet, source_path, content_path, data_root = build_retained_raw_packet(root)
            source_bytes = source_path.read_bytes()
            content_bytes = content_path.read_bytes()
            out = root / "out"
            first, created = MODULE.publish_append_only(
                packet,
                source_bytes=source_bytes,
                content_bytes=content_bytes,
                data_root=data_root,
                out_root=out,
            )
            second, repeated = MODULE.publish_append_only(
                packet,
                source_bytes=source_bytes,
                content_bytes=content_bytes,
                data_root=data_root,
                out_root=out,
            )
            self.assertTrue(created)
            self.assertFalse(repeated)
            self.assertEqual(first, second)
            lineage = packet["lineage"]
            self.assertEqual(
                (first.parent / lineage["source_snapshot_file"]).read_bytes(),
                source_bytes,
            )
            self.assertEqual(
                (first.parent / lineage["content_run_snapshot_file"]).read_bytes(),
                content_bytes,
            )
            filing = packet["indexed_filings"][0]
            manifest_snapshot = first.parent / filing["manifest_snapshot_file"]
            manifest_path = (
                data_root
                / "dart_content"
                / filing["subject_id"]
                / filing["rcept_no"]
                / "_manifest.json"
            )
            self.assertEqual(manifest_snapshot.read_bytes(), manifest_path.read_bytes())
            self.assertEqual(
                MODULE.validate_packet(
                    packet,
                    snapshot_dir=first.parent,
                    data_root=data_root,
                ),
                packet,
            )

    def test_same_inputs_at_later_workflow_time_are_byte_identical_no_op(self):
        later = (
            dt.datetime.fromisoformat(DECISION_AT.replace("Z", "+00:00"))
            + dt.timedelta(days=7)
        ).isoformat().replace("+00:00", "Z")
        rebuilt = MODULE.build_packet(decision_at=later)
        self.assertEqual(rebuilt, self.packet)
        self.assertEqual(
            rebuilt["decision_time_basis"],
            "MAX_EXACT_SOURCE_AND_CONTENT_TIMESTAMPS",
        )
        source_bytes = MODULE.DEFAULT_SOURCE.read_bytes()
        content_bytes = MODULE.DEFAULT_CONTENT.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            first, first_created = MODULE.publish_append_only(
                self.packet,
                source_bytes=source_bytes,
                content_bytes=content_bytes,
                out_root=Path(temporary),
            )
            second, second_created = MODULE.publish_append_only(
                rebuilt,
                source_bytes=source_bytes,
                content_bytes=content_bytes,
                out_root=Path(temporary),
            )
            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first, second)

    def test_snapshot_tamper_fails_even_when_packet_is_unchanged(self):
        source_bytes = MODULE.DEFAULT_SOURCE.read_bytes()
        content_bytes = MODULE.DEFAULT_CONTENT.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            target, _ = MODULE.publish_append_only(
                self.packet,
                source_bytes=source_bytes,
                content_bytes=content_bytes,
                out_root=Path(temporary),
            )
            source_snapshot = target.parent / self.packet["lineage"]["source_snapshot_file"]
            source_snapshot.write_bytes(source_snapshot.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                MODULE.DartStructuralIndexError, "SNAPSHOT_HASH_MISMATCH"
            ):
                MODULE.validate_packet(
                    self.packet,
                    snapshot_dir=target.parent,
                    data_root=MODULE.DEFAULT_DATA_ROOT,
                )

    def test_exact_manifest_snapshot_not_mutable_current_manifest_is_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / "out"
            packet, source_path, content_path, data_root = build_retained_raw_packet(root)
            target, _ = MODULE.publish_append_only(
                packet,
                source_bytes=source_path.read_bytes(),
                content_bytes=content_path.read_bytes(),
                data_root=data_root,
                out_root=out,
            )
            filing = packet["indexed_filings"][0]
            current_manifest = (
                data_root / "dart_content" / filing["subject_id"]
                / filing["rcept_no"] / "_manifest.json"
            )
            mutated = json.loads(current_manifest.read_text(encoding="utf-8"))
            mutated["atlas_stage"] = "Ready"
            current_manifest.write_text(json.dumps(mutated), encoding="utf-8")
            self.assertEqual(
                MODULE.validate_packet(
                    packet, snapshot_dir=target.parent, data_root=data_root
                ),
                packet,
            )

    def test_manifest_snapshot_tamper_fails_before_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packet, source_path, content_path, data_root = build_retained_raw_packet(root)
            target, _ = MODULE.publish_append_only(
                packet,
                source_bytes=source_path.read_bytes(),
                content_bytes=content_path.read_bytes(),
                data_root=data_root,
                out_root=root / "out",
            )
            filing = packet["indexed_filings"][0]
            manifest_snapshot = target.parent / filing["manifest_snapshot_file"]
            manifest_snapshot.write_bytes(manifest_snapshot.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                MODULE.DartStructuralIndexError, "MANIFEST_SNAPSHOT_HASH_MISMATCH"
            ):
                MODULE.validate_packet(
                    packet,
                    snapshot_dir=target.parent,
                    data_root=data_root,
                )

    def test_self_rehashed_semantic_or_count_tamper_fails_independent_rebuild(self):
        source_bytes = MODULE.DEFAULT_SOURCE.read_bytes()
        content_bytes = MODULE.DEFAULT_CONTENT.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            target, _ = MODULE.publish_append_only(
                self.packet,
                source_bytes=source_bytes,
                content_bytes=content_bytes,
                out_root=Path(temporary),
            )
            tampered = copy.deepcopy(self.packet)
            tampered["summary"]["table_count"] += 1
            tampered["packet_sha256"] = MODULE.payload_sha256(
                {key: value for key, value in tampered.items() if key != "packet_sha256"}
            )
            with self.assertRaisesRegex(
                MODULE.DartStructuralIndexError, "PACKET_DRIFT_OR_TAMPER"
            ):
                MODULE.validate_packet(
                    tampered,
                    snapshot_dir=target.parent,
                    data_root=MODULE.DEFAULT_DATA_ROOT,
                )

    def test_retained_member_tamper_fails_before_indexing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packet, source_path, content_path, data_root = build_retained_raw_packet(root)
            document = packet["documents"][0]
            member = (
                data_root
                / "dart_content"
                / document["subject_id"]
                / document["rcept_no"]
                / document["cache_name"]
            )
            raw = bytearray(member.read_bytes())
            raw[-1] ^= 1
            member.write_bytes(bytes(raw))
            with self.assertRaises((
                MODULE.DART.DartContentError,
                MODULE.DART_OBSERVATION.DartEventObservationError,
                MODULE.DartStructuralIndexError,
            )):
                MODULE.build_packet(
                    decision_at=RETAINED_DECISION_AT,
                    source_path=source_path,
                    content_path=content_path,
                    data_root=data_root,
                )

    def test_future_source_or_content_is_rejected_by_shared_validator(self):
        current = dt.datetime.fromisoformat(DECISION_AT.replace("Z", "+00:00"))
        earlier = (current - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        with self.assertRaisesRegex(
            MODULE.DartStructuralIndexError,
            "EVIDENCE_AVAILABLE_AFTER_DECISION",
        ):
            MODULE.build_packet(decision_at=earlier)

    def test_publication_rejects_input_bytes_not_bound_by_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                MODULE.DartStructuralIndexError, "PUBLICATION_SNAPSHOT_HASH_MISMATCH"
            ):
                MODULE.publish_append_only(
                    self.packet,
                    source_bytes=MODULE.DEFAULT_SOURCE.read_bytes() + b"\n",
                    content_bytes=MODULE.DEFAULT_CONTENT.read_bytes(),
                    out_root=Path(temporary),
                )

    def test_publication_rejects_self_rehashed_packet_before_writing_any_file(self):
        tampered = copy.deepcopy(self.packet)
        tampered["summary"]["table_count"] += 1
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with tempfile.TemporaryDirectory() as temporary:
            out_root = Path(temporary) / "out"
            with self.assertRaisesRegex(
                MODULE.DartStructuralIndexError,
                "PUBLICATION_PACKET_DRIFT_OR_TAMPER",
            ):
                MODULE.publish_append_only(
                    tampered,
                    source_bytes=MODULE.DEFAULT_SOURCE.read_bytes(),
                    content_bytes=MODULE.DEFAULT_CONTENT.read_bytes(),
                    out_root=out_root,
                )
            self.assertFalse(out_root.exists())

    def test_module_has_no_provider_or_delivery_client(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & {"requests", "urllib", "httpx", "socket"})


class DartStructuralContentIndexWiringTests(unittest.TestCase):
    def test_workflow_indexes_after_content_and_before_sec_capture(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        content = text.index("Capture DART filing content (P4-03)")
        structural = text.index("Populate DART Structural Content Index (P4-03)")
        sec = text.index("Capture SEC filing content (P4-02)")
        self.assertLess(content, structural)
        self.assertLess(structural, sec)
        block = text[structural:sec]
        self.assertIn("discovery/dart_structural_content_index.py", block)
        self.assertNotIn("secrets.", block)

    def test_authoritative_runner_registers_test_once(self):
        text = RUN_ALL.read_text(encoding="utf-8")
        self.assertEqual(text.count('"test/test_dart_structural_content_index.py"'), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
