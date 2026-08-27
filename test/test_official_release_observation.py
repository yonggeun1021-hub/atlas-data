#!/usr/bin/env python3
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "discovery" / "official_release_observation.py"
SPEC = importlib.util.spec_from_file_location("official_release_observation", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


DECISION_AT = "2026-08-28T00:00:00Z"


class OfficialReleaseObservationTests(unittest.TestCase):
    def real_packet(self):
        return MODULE.build_packet(data_root=ROOT / "data", decision_at=DECISION_AT)

    def copy_tsm_data(self, target: Path) -> Path:
        data_root = target / "data"
        shutil.copytree(ROOT / "data" / "sec_content" / "TSM", data_root / "sec_content" / "TSM")
        return data_root

    def test_real_retained_population_records_three_months_without_meaning(self):
        packet = self.real_packet()
        self.assertGreaterEqual(packet["counts"]["pit_eligible_manifests"], 8)
        self.assertGreaterEqual(packet["counts"]["observed_monthly_revenue"], 3)
        self.assertGreaterEqual(packet["counts"]["excluded_non_monthly_revenue"], 5)
        self.assertEqual(
            packet["counts"]["pit_eligible_manifests"],
            packet["counts"]["observed_monthly_revenue"]
            + packet["counts"]["excluded_non_monthly_revenue"],
        )
        by_period = {row["economic_period"]: row for row in packet["observations"]}
        self.assertTrue(
            {"2026-05", "2026-06", "2026-07"}.issubset(by_period),
        )
        july = by_period["2026-07"]
        self.assertEqual(july["published_values"]["monthly_yoy_pct_published"], "44.7")
        self.assertEqual(july["published_values"]["cumulative_yoy_pct_published"], "37.0")
        self.assertEqual(july["interpretation_status"], "UNDETERMINED")
        self.assertEqual(july["rule_impact"], "NONE")
        self.assertIsNone(july["stage_change"])
        self.assertIsNone(july["trade_proposal"])

    def test_non_monthly_filings_are_explicitly_excluded_without_values(self):
        packet = self.real_packet()
        self.assertEqual(len(packet["excluded_filings"]), 5)
        for row in packet["excluded_filings"]:
            self.assertEqual(row["status"], "NOT_MONTHLY_REVENUE_REPORT")
            self.assertEqual(row["reason"], "APPROVED_MONTHLY_REVENUE_IDENTITY_ABSENT")
            self.assertNotIn("published_values", row)
            self.assertNotIn("interpretation_status", row)

    def test_authority_is_closed(self):
        packet = self.real_packet()
        self.assertTrue(packet["authority"]["observation_recording_only"])
        self.assertTrue(
            all(value is False for key, value in packet["authority"].items() if key != "observation_recording_only")
        )
        self.assertEqual(packet["source_hierarchy_status"], "UNRATIFIED_NO_GLOBAL_RANKING")

    def test_packet_contains_no_filing_prose_or_company_name(self):
        text = json.dumps(self.real_packet(), ensure_ascii=False)
        self.assertNotIn("Taiwan Semiconductor Manufacturing Company Limited", text)
        self.assertNotIn("today announced", text.lower())
        self.assertNotIn("BUY", text)
        self.assertNotIn("SELL", text)

    def test_module_has_no_network_or_rule_delivery_client(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "httpx", "subprocess", "slack", "notion"):
            self.assertNotIn(f"import {forbidden}", source)
        self.assertNotIn("event_classifier", source)

    def test_later_wall_clock_with_same_inputs_is_byte_identical(self):
        later = MODULE.build_packet(data_root=ROOT / "data", decision_at="2100-01-01T00:00:00Z")
        first = MODULE.build_packet(
            data_root=ROOT / "data", decision_at=later["evidence_as_of"]
        )
        self.assertEqual(first, later)

    def test_build_never_calls_live_probe_or_fetcher(self):
        with mock.patch.object(MODULE.TSMC, "run_probe", side_effect=AssertionError("network")) as probe:
            packet = self.real_packet()
        probe.assert_not_called()
        self.assertGreaterEqual(packet["counts"]["observed_monthly_revenue"], 3)

    def test_historical_decision_filters_future_retained_manifests(self):
        packet = MODULE.build_packet(
            data_root=ROOT / "data", decision_at="2026-08-20T22:00:00Z"
        )
        self.assertLess(packet["counts"]["pit_eligible_manifests"], 8)
        self.assertNotIn(
            "0001046179-26-000545",
            json.dumps(packet, sort_keys=True),
        )
        MODULE.validate_packet(packet, data_root=ROOT / "data")

    def test_future_raw_corruption_cannot_change_historical_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_tsm_data(Path(tmp))
            before = MODULE.build_packet(
                data_root=data_root, decision_at="2026-08-20T22:00:00Z"
            )
            future = data_root / "sec_content" / "TSM" / "0001046179-26-000545"
            raw_path = next(future.glob("*.gz"))
            raw_path.write_bytes(b"not-a-gzip")
            after = MODULE.build_packet(
                data_root=data_root, decision_at="2026-08-20T22:00:00Z"
            )
            self.assertEqual(before, after)

    def test_future_decision_at_and_invalid_time_precision_fail_closed(self):
        with self.assertRaisesRegex(MODULE.OfficialReleaseObservationError, "DECISION_AT_INVALID"):
            MODULE.build_packet(data_root=ROOT / "data", decision_at="2026-08-28")

    def test_raw_cache_tamper_is_rejected_before_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_tsm_data(Path(tmp))
            raw_path = next((data_root / "sec_content" / "TSM").glob("*/tsm-revenue20260810.htm.gz"))
            raw_path.write_bytes(gzip.compress(gzip.decompress(raw_path.read_bytes()) + b"tamper", mtime=0))
            with self.assertRaisesRegex(MODULE.OfficialReleaseObservationError, "SEC_MANIFEST_INVALID"):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_manifest_tamper_is_rejected_before_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_tsm_data(Path(tmp))
            path = data_root / "sec_content" / "TSM" / "0001046179-26-000471" / "_manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["action"] = "BUY"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.OfficialReleaseObservationError, "SEC_MANIFEST_INVALID"):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_identified_but_invalid_monthly_report_is_not_silently_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = self.copy_tsm_data(Path(tmp))
            directory = data_root / "sec_content" / "TSM" / "0001046179-26-000459"
            manifest_path = directory / "_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            row = manifest["documents"][0]
            raw_path = directory / f"{row['document_name']}.gz"
            raw = gzip.decompress(raw_path.read_bytes()) + b"<h2>TSMC July 2026 Revenue Report</h2>"
            row["content_sha256"] = hashlib.sha256(raw).hexdigest()
            row["content_bytes"] = len(raw)
            raw_path.write_bytes(gzip.compress(raw, mtime=0))
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.OfficialReleaseObservationError, "IDENTIFIED_MONTHLY_REPORT_INVALID"
            ):
                MODULE.build_packet(data_root=data_root, decision_at=DECISION_AT)

    def test_hash_only_packet_tamper_fails(self):
        packet = self.real_packet()
        packet["counts"]["observed_monthly_revenue"] = 99
        with self.assertRaisesRegex(MODULE.OfficialReleaseObservationError, "PACKET_HASH_MISMATCH"):
            MODULE.validate_packet(packet, data_root=ROOT / "data")

    def test_self_rehashed_semantic_tamper_fails_independent_rebuild(self):
        packet = self.real_packet()
        packet["observations"][0]["published_values"]["monthly_yoy_pct_published"] = "999.9"
        unsigned = {key: value for key, value in packet.items() if key != "packet_sha256"}
        packet["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseObservationError, "PACKET_INDEPENDENT_REBUILD_MISMATCH"
        ):
            MODULE.validate_packet(packet, data_root=ROOT / "data")

    def test_self_rehashed_authority_tamper_fails(self):
        packet = self.real_packet()
        packet["authority"]["trading_authorized"] = True
        unsigned = {key: value for key, value in packet.items() if key != "packet_sha256"}
        packet["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.OfficialReleaseObservationError, "PACKET_AUTHORITY_MISMATCH"):
            MODULE.validate_packet(packet, data_root=ROOT / "data")

    def test_publication_is_content_addressed_append_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            packet = self.real_packet()
            first = MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)
            before = first.read_bytes()
            second = MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)
            self.assertEqual(first, second)
            self.assertEqual(before, second.read_bytes())
            self.assertIn(packet["packet_sha256"][:16], first.name)

    def test_existing_content_addressed_path_drift_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            packet = self.real_packet()
            target = MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)
            target.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.OfficialReleaseObservationError, "APPEND_ONLY_PACKET_DRIFT"):
                MODULE.publish_packet(packet, data_root=ROOT / "data", out_root=out)


class OfficialReleaseObservationWiringTests(unittest.TestCase):
    def test_workflow_runs_after_sec_capture_and_before_event_cases(self):
        text = (ROOT / ".github" / "workflows" / "collect.yml").read_text(encoding="utf-8")
        self.assertLess(
            text.index("Capture SEC filing content (P4-02)"),
            text.index("Populate TSMC Official Release Observations (P4-04)"),
        )
        self.assertLess(
            text.index("Populate TSMC Official Release Observations (P4-04)"),
            text.index("Populate SEC Event Discovery Cases (P3-08)"),
        )
        self.assertIn("discovery/official_release_observation.py", text)

    def test_authoritative_runner_registers_test_once(self):
        text = (ROOT / "run_all.py").read_text(encoding="utf-8")
        self.assertEqual(text.count('"test/test_official_release_observation.py"'), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
