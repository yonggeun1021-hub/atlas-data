#!/usr/bin/env python3
from __future__ import annotations

import copy
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "discovery" / "official_release_summary_observation.py"
SPEC = importlib.util.spec_from_file_location(
    "official_release_summary_observation", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

DECISION_AT = "2026-08-28T00:00:00Z"


class OfficialReleaseSummaryObservationTests(unittest.TestCase):
    def real_packet(self):
        return MODULE.build_packet(data_root=ROOT / "data", decision_at=DECISION_AT)

    def copy_data(self, target: Path) -> Path:
        data_root = target / "data"
        shutil.copytree(
            ROOT / "data" / "sec_content" / "SNDK",
            data_root / "sec_content" / "SNDK",
        )
        return data_root

    def manifest_path(self, data_root: Path) -> Path:
        return (
            data_root
            / "sec_content"
            / "SNDK"
            / MODULE.REGISTERED_RELEASE["accession"]
            / "_manifest.json"
        )

    def release_path(self, data_root: Path) -> Path:
        return self.manifest_path(data_root).parent / (
            MODULE.REGISTERED_RELEASE["document_name"] + ".gz"
        )

    def rewrite_release(self, data_root: Path, transform) -> None:
        manifest_path = self.manifest_path(data_root)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        release_path = self.release_path(data_root)
        raw = transform(gzip.decompress(release_path.read_bytes()))
        for row in manifest["documents"]:
            if row["document_name"] == MODULE.REGISTERED_RELEASE["document_name"]:
                row["content_sha256"] = MODULE.hashlib.sha256(raw).hexdigest()
                row["content_bytes"] = len(raw)
        release_path.write_bytes(gzip.compress(raw, mtime=0))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_real_release_records_complete_ordered_summary_without_interpretation(self):
        packet = self.real_packet()
        self.assertEqual(packet["counts"], {
            "pit_eligible_manifests": 2,
            "observed_registered_releases": 1,
            "excluded_unregistered_filings": 1,
            "observed_summary_items": 5,
        })
        observation = packet["observations"][0]
        self.assertEqual(observation["subject"], "SNDK")
        self.assertEqual(
            observation["release_title"],
            "Sandisk Reports Fiscal Fourth Quarter 2026 Financial Results",
        )
        self.assertEqual(observation["published_at"], "2026-08-05")
        self.assertEqual(
            [row["ordinal"] for row in observation["summary_items"]],
            [1, 2, 3, 4, 5],
        )
        self.assertIn("$8.97 billion", observation["summary_items"][0]["text"])
        self.assertIn("$10.30 billion", observation["summary_items"][4]["text"])
        self.assertEqual(observation["interpretation_status"], "UNDETERMINED")
        self.assertEqual(observation["rule_impact"], "NONE")
        self.assertIsNone(observation["stage_change"])
        self.assertIsNone(observation["trade_proposal"])

    def test_every_summary_item_locator_matches_retained_exact_bytes(self):
        observation = self.real_packet()["observations"][0]
        raw = gzip.decompress(
            (ROOT / observation["lineage"]["release_document_ref"]).read_bytes()
        )
        text = MODULE.BASE.SEC.normalized_visible_text(raw)
        for item in observation["summary_items"]:
            self.assertEqual(
                text[item["normalized_text_start"] : item["normalized_text_end"]],
                item["text"],
            )

    def test_unregistered_retained_filing_is_explicitly_excluded(self):
        packet = self.real_packet()
        self.assertEqual(len(packet["excluded_filings"]), 1)
        row = packet["excluded_filings"][0]
        self.assertEqual(row["status"], "NOT_REGISTERED_OFFICIAL_RELEASE")
        self.assertEqual(row["reason"], "NO_APPROVED_RELEASE_SUMMARY_ADAPTER")
        self.assertNotIn("summary_items", row)

    def test_authority_is_observation_only(self):
        packet = self.real_packet()
        self.assertTrue(packet["authority"]["observation_recording_only"])
        self.assertTrue(
            all(
                value is False
                for key, value in packet["authority"].items()
                if key != "observation_recording_only"
            )
        )
        self.assertEqual(
            packet["registration_scope"],
            "NARROW_IMPLEMENTATION_SCOPE_NOT_SOURCE_AUTHORITY",
        )
        self.assertEqual(
            packet["source_hierarchy_status"], "UNRATIFIED_NO_GLOBAL_RANKING"
        )

    def test_module_has_no_network_rule_or_delivery_client(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "urllib",
            "requests",
            "httpx",
            "subprocess",
            "slack",
            "notion",
            "event_classifier",
        ):
            self.assertNotIn(forbidden, source)

    def test_same_inputs_are_deterministic_after_evidence_as_of(self):
        later = MODULE.build_packet(
            data_root=ROOT / "data", decision_at="2100-01-01T00:00:00Z"
        )
        exact = MODULE.build_packet(
            data_root=ROOT / "data", decision_at=later["evidence_as_of"]
        )
        self.assertEqual(exact, later)

    def test_invalid_decision_precision_fails_closed(self):
        with self.assertRaisesRegex(MODULE.OfficialReleaseSummaryError, "DECISION_AT_INVALID"):
            MODULE.build_packet(data_root=ROOT / "data", decision_at="2026-08-28")

    def test_decision_before_all_retained_manifests_fails_closed(self):
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseSummaryError, "NO_PIT_ELIGIBLE_SUBJECT_MANIFESTS"
        ):
            MODULE.build_packet(
                data_root=ROOT / "data", decision_at="2026-08-01T00:00:00Z"
            )

    def test_future_unregistered_corruption_does_not_change_historical_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_data(Path(tmp))
            future_manifest = (
                data_root
                / "sec_content"
                / "SNDK"
                / "0001628280-26-057406"
                / "_manifest.json"
            )
            future_manifest_value = json.loads(
                future_manifest.read_text(encoding="utf-8")
            )
            future_manifest_value["retrieved_at_utc"] = "2026-08-21T00:00:00Z"
            future_manifest.write_text(
                json.dumps(future_manifest_value), encoding="utf-8"
            )
            decision_at = "2026-08-20T21:59:19Z"
            before = MODULE.build_packet(data_root=data_root, decision_at=decision_at)
            future = (
                data_root
                / "sec_content"
                / "SNDK"
                / "0001628280-26-057406"
                / "sndk-20260703.htm.gz"
            )
            future.write_bytes(b"not-gzip")
            after = MODULE.build_packet(data_root=data_root, decision_at=decision_at)
            self.assertEqual(before, after)

    def test_retained_release_raw_tamper_fails_before_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_data(Path(tmp))
            path = self.release_path(data_root)
            path.write_bytes(
                gzip.compress(gzip.decompress(path.read_bytes()) + b"tamper", mtime=0)
            )
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseSummaryError, "SEC_MANIFEST_INVALID"
            ):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_title_change_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_data(Path(tmp))
            self.rewrite_release(
                data_root,
                lambda raw: raw.replace(
                    b"Sandisk Reports Fiscal Fourth Quarter 2026 Financial Results",
                    b"Sandisk Results",
                    1,
                ),
            )
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseSummaryError,
                "RELEASE_TITLE_CARDINALITY_INVALID",
            ):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_summary_heading_duplicate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_data(Path(tmp))
            self.rewrite_release(
                data_root,
                lambda raw: raw.replace(b"News Summary", b"News Summary News Summary", 1),
            )
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseSummaryError,
                "RELEASE_SUMMARY_HEADING_CARDINALITY_INVALID",
            ):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_missing_summary_item_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_data(Path(tmp))
            self.rewrite_release(
                data_root,
                lambda raw: raw.rsplit(b"&#8226;", 1)[0]
                + raw.rsplit(b"&#8226;", 1)[1],
            )
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseSummaryError,
                "RELEASE_SUMMARY_ITEM_COUNT_INVALID",
            ):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_published_after_capture_is_rejected(self):
        manifest_path = (
            ROOT
            / "data"
            / "sec_content"
            / "SNDK"
            / MODULE.REGISTERED_RELEASE["accession"]
            / "_manifest.json"
        )
        manifest, raw_by_name, manifest_bytes, _ = MODULE.BASE._load_validated_filing(
            manifest_path,
            decision_time=MODULE.BASE._utc(DECISION_AT, "DECISION_AT_INVALID"),
        )
        manifest["retrieved_at_utc"] = "2026-08-04T23:59:59Z"
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseSummaryError,
            "RELEASE_PUBLISHED_AFTER_CAPTURE",
        ):
            MODULE._release_observation(
                manifest_path, manifest, raw_by_name, manifest_bytes
            )

    def test_publication_and_filing_date_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_data(Path(tmp))
            manifest_path = self.manifest_path(data_root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["filing_date"] = "2026-08-06"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseSummaryError,
                "RELEASE_PUBLICATION_FILING_DATE_MISMATCH",
            ):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_manifest_authority_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_data(Path(tmp))
            manifest_path = self.manifest_path(data_root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["action"] = "BUY"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseSummaryError, "SEC_MANIFEST_INVALID"
            ):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_hash_only_packet_tamper_is_rejected(self):
        packet = self.real_packet()
        packet["counts"]["observed_summary_items"] = 99
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseSummaryError, "PACKET_HASH_MISMATCH"
        ):
            MODULE.validate_packet(packet, data_root=ROOT / "data")

    def test_self_rehashed_summary_tamper_is_rejected_by_rebuild(self):
        packet = self.real_packet()
        packet["observations"][0]["summary_items"][0]["text"] = "BUY"
        unsigned = {key: value for key, value in packet.items() if key != "packet_sha256"}
        packet["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseSummaryError,
            "PACKET_INDEPENDENT_REBUILD_MISMATCH",
        ):
            MODULE.validate_packet(packet, data_root=ROOT / "data")

    def test_self_rehashed_authority_tamper_is_rejected(self):
        packet = self.real_packet()
        packet["authority"]["trading_authorized"] = True
        unsigned = {key: value for key, value in packet.items() if key != "packet_sha256"}
        packet["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseSummaryError, "PACKET_AUTHORITY_MISMATCH"
        ):
            MODULE.validate_packet(packet, data_root=ROOT / "data")

    def test_publication_is_content_addressed_append_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = self.real_packet()
            out = Path(tmp) / "out"
            first = MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)
            before = first.read_bytes()
            second = MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)
            self.assertEqual(first, second)
            self.assertEqual(before, second.read_bytes())
            self.assertIn(packet["packet_sha256"][:16], first.name)

    def test_existing_content_addressed_path_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = self.real_packet()
            out = Path(tmp) / "out"
            target = MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)
            target.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseSummaryError, "APPEND_ONLY_PACKET_DRIFT"
            ):
                MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)


class OfficialReleaseSummaryWiringTests(unittest.TestCase):
    def test_workflow_runs_after_sec_capture_and_before_event_cases(self):
        text = (ROOT / ".github" / "workflows" / "collect.yml").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            text.index("Capture SEC filing content (P4-02)"),
            text.index("Populate Sandisk Official Release Summary (P4-04)"),
        )
        self.assertLess(
            text.index("Populate Sandisk Official Release Summary (P4-04)"),
            text.index("Populate SEC Event Discovery Cases (P3-08)"),
        )
        self.assertIn("discovery/official_release_summary_observation.py", text)

    def test_authoritative_runner_registers_test_once(self):
        text = (ROOT / "run_all.py").read_text(encoding="utf-8")
        self.assertEqual(
            text.count('"test/test_official_release_summary_observation.py"'), 1
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
