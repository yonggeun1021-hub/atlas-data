#!/usr/bin/env python3
"""Focused KRX Gate 1 natural receipt regressions."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "krx_gate1_natural_receipt_module",
    ROOT / "krx_gate1_natural_receipt" / "receipt.py",
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
KST = ZoneInfo("Asia/Seoul")
CURRENT_MANIFEST = (
    ROOT / "evidence" / "krx_gate1_natural_receipt" / "2026-09-01" / "input_manifest.json"
)
CURRENT_RECEIPT = CURRENT_MANIFEST.with_name("receipt.json")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


class KrxGate1NaturalReceiptTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.contract = module.load_contract()

    def test_current_natural_inventory_is_exact_unknown_hold_and_mutation_zero(self):
        built = module.build_receipt(CURRENT_MANIFEST)
        retained = json.loads(CURRENT_RECEIPT.read_text(encoding="utf-8"))
        self.assertEqual(built, retained)
        self.assertEqual(built["gate1_status"], "UNKNOWN")
        self.assertEqual(built["source_status"], "INCOMPLETE")
        self.assertEqual(built["completed_series"]["15m"]["accepted_bar_count"], 0)
        self.assertEqual(built["completed_series"]["1h"]["accepted_bar_count"], 0)
        self.assertEqual(
            built["completed_series"]["1d"],
            {"status": "UNCONFIRMED_NOT_PROMOTED", "accepted_bar_count": 0},
        )
        self.assertIsNone(built["ttl_sla_governance"]["numeric_ttl_seconds"])
        self.assertEqual(built["ttl_sla_governance"]["repository_default"], "ABSENT")
        self.assertEqual(built["ttl_sla_governance"]["provider_sla"], "UNKNOWN")
        self.assertEqual(built["runtime"]["action"], "HOLD")
        self.assertEqual(built["runtime"]["writer_invocation_count"], 0)
        self.assertEqual(built["runtime"]["ledger_mutation_count"], 0)

    def _fixture(self, directory: Path, *, omit_last_minute: bool = False) -> Path:
        relative_root = directory.relative_to(ROOT)
        calendar_path = directory / "calendar.json"
        minutes_path = directory / "minutes.json"
        normalization_path = directory / "normalization.json"
        response_sha = "a" * 64
        calendar = {
            "schema_version": "krx_date_specific_session_source/1",
            "as_of_date": "2026-09-01",
            "official_response_ref": "fixture://ctca0903r/2026-09-01",
            "official_response_sha256": response_sha,
            "calendar": {
                "session_date": "2026-09-01",
                "status": "OPEN_REGULAR",
                "timezone": "Asia/Seoul",
                "open_at": "2026-09-01T09:00:00+09:00",
                "close_at": "2026-09-01T15:30:00+09:00",
                "observed_at": "2026-08-29T12:00:00+09:00",
                "available_at": "2026-08-29T12:00:01+09:00",
                "source_ref": "fixture://ctca0903r/2026-09-01",
                "source_sha256": response_sha,
                "provider_id": "KIS_OPEN_API_DOMESTIC_HOLIDAY_CTCA0903R",
                "market_rule_source": "KRX_EQUITY_MARKET_OPERATION_RULES",
            },
        }
        write_json(calendar_path, calendar)
        normalization = {
            "schema_version": "krx_minute_timestamp_normalization_receipt/1",
            "approval_status": "TEST_RATIFIED_NON_PROMOTABLE",
            "timestamp_semantics": "INTERVAL_START_RATIFIED",
            "effective_from": "2026-09-01",
            "source_ref": "fixture://normalization/not-operational",
            "source_sha256": "b" * 64,
            "fixture_promotion_authorized": False,
        }
        write_json(normalization_path, normalization)
        opened = dt.datetime(2026, 9, 1, 9, 0, tzinfo=KST)
        count = 389 if omit_last_minute else 390
        rows = []
        for index in range(count):
            value = str(1000 + index)
            rows.append({
                "interval_start": (opened + dt.timedelta(minutes=index)).isoformat(
                    timespec="seconds"
                ),
                "open": value,
                "high": value,
                "low": value,
                "close": value,
                "volume": "1",
            })
        minutes = {
            "asset_id": "KRX.TEST.STRUCTURAL_ONLY",
            "price_basis": "RAW",
            "timestamp_semantics": "INTERVAL_START_RATIFIED",
            "minutes": rows,
            "source": {
                "provider_id": "KIS_OPEN_API",
                "endpoint_id": "FHKST03010230",
                "observed_at": "2026-09-01T15:31:00+09:00",
                "available_at": "2026-09-01T15:31:01+09:00",
                "generated_at": "2026-09-01T15:31:02+09:00",
                "snapshot_ref": "fixture://minutes/not-natural",
                "snapshot_sha256": "c" * 64,
                "capture_kind": "ORIGINAL",
            },
        }
        write_json(minutes_path, minutes)
        manifest_path = directory / "manifest.json"
        manifest = {
            "schema_version": "krx_gate1_natural_input/1",
            "as_of_date": "2026-09-01",
            "decision_at": "2026-09-01T16:00:00+09:00",
            "evidence_class": "TEST_ONLY_NON_PROMOTABLE",
            "calendar_binding": {
                "path": (relative_root / calendar_path.name).as_posix(),
                "sha256": file_sha(calendar_path),
            },
            "normalized_minute_packet_binding": {
                "path": (relative_root / minutes_path.name).as_posix(),
                "sha256": file_sha(minutes_path),
            },
            "normalization_receipt_binding": {
                "path": (relative_root / normalization_path.name).as_posix(),
                "sha256": file_sha(normalization_path),
            },
            "source_inventory": {
                "official_date_specific_calendar": "TEST_ONLY",
                "natural_normalized_minutes": "TEST_ONLY",
                "minute_timestamp_normalization": "TEST_ONLY",
                "numeric_ttl_seconds": None,
                "repository_default": "ABSENT",
                "provider_sla": "UNKNOWN",
            },
            "authority": copy.deepcopy(self.contract["authority"]),
        }
        write_json(manifest_path, manifest)
        return manifest_path

    def test_complete_structural_fixture_proves_counts_but_cannot_promote(self):
        with tempfile.TemporaryDirectory(prefix="krx_gate1_test_", dir=ROOT) as raw:
            receipt = module.build_receipt(self._fixture(Path(raw)))
        self.assertEqual(receipt["source_status"], "TEST_ONLY")
        self.assertEqual(receipt["gate1_status"], "TEST_ONLY")
        self.assertEqual(receipt["completed_series"]["15m"]["accepted_bar_count"], 26)
        self.assertEqual(receipt["completed_series"]["1h"]["accepted_bar_count"], 6)
        self.assertEqual(receipt["runtime"]["writer_invocation_count"], 0)
        self.assertFalse(receipt["authority"]["fixture_promotion_authorized"])

    def test_partial_natural_minute_bucket_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="krx_gate1_test_", dir=ROOT) as raw:
            manifest = self._fixture(Path(raw), omit_last_minute=True)
            with self.assertRaisesRegex(
                module.KrxGate1ReceiptError,
                "15M_COMPLETED_SERIES_GAP",
            ):
                module.build_receipt(manifest)

    def test_exact_source_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="krx_gate1_test_", dir=ROOT) as raw:
            manifest_path = self._fixture(Path(raw))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["calendar_binding"]["sha256"] = "d" * 64
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(
                module.KrxGate1ReceiptError,
                "CALENDAR_SHA_MISMATCH",
            ):
                module.build_receipt(manifest_path)

    def test_numeric_ttl_and_authority_expansion_are_rejected(self):
        manifest = json.loads(CURRENT_MANIFEST.read_text(encoding="utf-8"))
        manifest["source_inventory"]["numeric_ttl_seconds"] = 60
        with tempfile.TemporaryDirectory(prefix="krx_gate1_test_", dir=ROOT) as raw:
            path = Path(raw) / "manifest.json"
            write_json(path, manifest)
            with self.assertRaisesRegex(
                module.KrxGate1ReceiptError,
                "TTL_SLA_AUTHORITY_EXPANSION_REJECTED",
            ):
                module.build_receipt(path)
        receipt = json.loads(CURRENT_RECEIPT.read_text(encoding="utf-8"))
        receipt["authority"]["order_authorized"] = True
        receipt["payload_sha256"] = module.payload_sha256({
            key: value for key, value in receipt.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            module.KrxGate1ReceiptError,
            "RECEIPT_CONTRACT_MISMATCH",
        ):
            module.validate_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
