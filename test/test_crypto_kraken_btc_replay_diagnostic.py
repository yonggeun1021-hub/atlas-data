#!/usr/bin/env python3
"""Kraken bulk BTC-only replay diagnostic (crypto acceptance condition 6).

Fixture archives are produced by the real importer from a small synthetic ZIP
whose XBTUSD closes are copied from a retained, committed Kraken OHLC API
capture, so the exact overlap check is exercised on real bytes.  Nothing is
downloaded.
"""

from __future__ import annotations

import copy
import calendar
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import crypto_kraken_btc_replay_diagnostic as KRAKEN  # noqa: E402
from regime import crypto_paper_runtime as RUNTIME  # noqa: E402

API_SNAPSHOT = ROOT / "evidence/crypto/btc/raw/2026-09-13"
RECEIPT_PATH = ROOT / "evidence/crypto/kraken_bulk_btc_replay_diagnostic/receipt.json"


class KrakenReplayDiagnosticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api, _ = KRAKEN.api_candles(API_SNAPSHOT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def archive(self, *, last="2025-12-31", start="2019-01-01", end="2025-12-31",
                drop=(), change=None, name="out", prefix=()) -> Path:
        rows = []
        for candle in [*prefix, *self.api]:
            day = candle["date"].isoformat()
            if day > last or day in drop:
                continue
            close = candle["close"]
            if change and day in change:
                close = Decimal(change[day])
            stamp = calendar.timegm(candle["date"].timetuple())
            rows.append(f"{stamp},{close},{close},{close},{close},1,1")
        zip_path = self.base / f"{name}.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("master_q4/XBTUSD_1440.csv", "\n".join(rows) + "\n")
            archive.writestr("master_q4/XBTUSD_60.csv", "")
        out = self.base / name
        KRAKEN.IMPORTER.import_archive(zip_path, out, start_date=dt.date.fromisoformat(start),
                                       end_date=dt.date.fromisoformat(end))
        return out

    def test_joins_bulk_and_retained_api_after_exact_overlap(self):
        receipt = KRAKEN.build_receipt(self.archive(), API_SNAPSHOT)
        overlap = receipt["overlap_check"]
        self.assertEqual(overlap["close_mismatch_count"], 0)
        self.assertEqual(overlap["last_shared_date"], "2025-12-31")
        self.assertEqual(overlap["extension_row_count"], 255)
        self.assertEqual(receipt["range"]["last_close_date"], "2026-09-12")
        self.assertEqual(receipt["api_extension"]["snapshot_path"], "evidence/crypto/btc/raw/2026-09-13")
        self.assertEqual(sum(receipt["risk_vol_counts"].values()), receipt["risk_point_count"])
        self.assertEqual(KRAKEN.validate_receipt(copy.deepcopy(receipt)), receipt)
        self.assertTrue(all(v is False for k, v in receipt["authority"].items() if k != "diagnostic_only"))

    def test_overlap_close_mismatch_fails_closed(self):
        archive = self.archive(change={"2025-06-01": "1"})
        with self.assertRaisesRegex(KRAKEN.KrakenReplayDiagnosticError, "API_BULK_OVERLAP_CLOSE_MISMATCH"):
            KRAKEN.build_receipt(archive, API_SNAPSHOT)

    def test_range_selection_is_rejected(self):
        with self.assertRaisesRegex(KRAKEN.KrakenReplayDiagnosticError, "RANGE_SELECTION_START_INVALID"):
            KRAKEN.build_receipt(self.archive(start="2020-01-01", name="late"), API_SNAPSHOT)
        with self.assertRaisesRegex(KRAKEN.KrakenReplayDiagnosticError, "RANGE_SELECTION_END_TRUNCATED"):
            KRAKEN.build_receipt(self.archive(end="2025-06-30", name="short"), API_SNAPSHOT)

    def test_tampered_archive_outputs_fail_closed(self):
        archive = self.archive()
        (archive / "SHA256SUMS").write_text("0" * 64 + "  manifest.json\n", encoding="utf-8")
        with self.assertRaisesRegex(KRAKEN.KrakenReplayDiagnosticError, "ARCHIVE_SHA256SUMS_MISMATCH"):
            KRAKEN.validate_archive(archive)

    def test_missing_interval_is_disclosed_never_filled(self):
        bulk_only = self.archive(drop={"2024-12-01"}, name="gap2")
        with self.assertRaisesRegex(KRAKEN.KrakenReplayDiagnosticError, "API_BULK_OVERLAP_DATE_MISSING"):
            KRAKEN.build_receipt(bulk_only, API_SNAPSHOT)
        joined, _ = KRAKEN.join_history(
            [c for c in self.api if c["date"].isoformat() not in {"2024-12-01"} and c["date"].isoformat() < "2024-12-10"],
            [c for c in self.api if c["date"].isoformat() >= "2024-12-05"])
        self.assertEqual(KRAKEN.missing_dates(joined), ["2024-12-01"])
        index = next(i for i, c in enumerate(joined) if c["date"].isoformat() == "2024-12-02")
        self.assertFalse(KRAKEN.contiguous_window(joined, index, 2))

    def test_end_to_end_bulk_gap_is_undefined_never_filled(self):
        """Mirrors the real archive: XBTUSD has no 2024-03-31 bulk row."""
        first_api = self.api[0]["date"]
        synthetic, cursor, index = [], dt.date(2024, 1, 1), 0
        while cursor < first_api:
            synthetic.append({"date": cursor, "close": Decimal(40000 + 37 * index + (index % 11) * 250)})
            cursor += dt.timedelta(days=1)
            index += 1
        archive = self.archive(prefix=synthetic, drop={"2024-03-31"}, name="realgap")
        receipt = KRAKEN.build_receipt(archive, API_SNAPSHOT)
        self.assertEqual(receipt["range"]["missing_calendar_dates"], ["2024-03-31"])
        self.assertEqual(receipt["range"]["first_close_date"], "2024-01-01")
        self.assertEqual(receipt["undefined_risk_point_count"], 89)
        self.assertEqual(receipt["undefined_risk_point_first_date"], "2024-04-01")
        self.assertEqual(receipt["undefined_risk_point_last_date"], "2024-06-28")
        self.assertEqual(receipt["overlap_check"]["close_mismatch_count"], 0)
        validated = KRAKEN.validate_archive(archive)
        closes = KRAKEN.btc_candles(archive, validated["pair"])
        self.assertNotIn(dt.date(2024, 3, 31), {row["date"] for row in closes})
        # Every close after the first full lookback is either a defined point or
        # a disclosed UNDEFINED point; none is silently dropped or synthesized.
        joined, _ = KRAKEN.join_history(closes, KRAKEN.api_candles(API_SNAPSHOT)[0])
        self.assertEqual(receipt["risk_point_count"] + receipt["undefined_risk_point_count"], len(joined) - 89)
        self.assertEqual(KRAKEN.validate_receipt(copy.deepcopy(receipt)), receipt)

    def test_required_results_missing_is_fail(self):
        with mock.patch.object(RUNTIME, "risk_vol_direction", return_value="NEUTRAL"):
            receipt = KRAKEN.build_receipt(self.archive(), API_SNAPSHOT)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["missing_required_results"], ["STRESS", "NEGATIVE", "POSITIVE"])
        self.assertEqual(KRAKEN.validate_receipt(receipt)["status"], "FAIL")

    def test_receipt_tampering_is_rejected(self):
        receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
        forged = copy.deepcopy(receipt)
        forged["risk_vol_counts"]["STRESS"] += 1
        with self.assertRaisesRegex(KRAKEN.KrakenReplayDiagnosticError, "RECEIPT_PAYLOAD_HASH_MISMATCH"):
            KRAKEN.validate_receipt(forged)
        forged = copy.deepcopy(receipt)
        forged["authority"]["runtime_authorized"] = True
        forged.pop("payload_sha256")
        forged["payload_sha256"] = KRAKEN.payload_sha256(forged)
        with self.assertRaisesRegex(KRAKEN.KrakenReplayDiagnosticError, "RECEIPT_AUTHORITY_ESCALATION"):
            KRAKEN.validate_receipt(forged)

    def test_committed_receipt_is_the_bound_trust_anchor(self):
        policy = RUNTIME.load_policy()
        replaced = policy["acceptance"]["replaced_condition_6"]
        self.assertEqual(ROOT / replaced["receipt_path"], RECEIPT_PATH)
        receipt = RUNTIME.validate_kraken_receipt(RECEIPT_PATH.read_bytes(), policy)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["source"]["bulk_first_date"], "2019-01-01")
        self.assertEqual(receipt["source"]["source_name"], "kraken_official_downloadable_ohlcvt")
        self.assertEqual(receipt["missing_required_results"], [])
        self.assertTrue((ROOT / receipt["api_extension"]["snapshot_path"]).is_dir())


if __name__ == "__main__":
    unittest.main()
