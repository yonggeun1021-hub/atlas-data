#!/usr/bin/env python3
"""Kraken bulk OHLCVT replay-import regression."""

import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_historical_ohlcvt_import.py"


def load_module():
    spec = importlib.util.spec_from_file_location("crypto_history_import", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def timestamp(day):
    return int(
        dt.datetime.combine(
            dt.date.fromisoformat(day), dt.time(), tzinfo=dt.timezone.utc
        ).timestamp()
    )


def candle(day, open_price="10", high="12", low="9", close="11", volume="2", trades="3"):
    return ",".join(
        [str(timestamp(day)), open_price, high, low, close, volume, trades]
    )


def write_archive(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, rows in entries.items():
            archive.writestr(name, ("\n".join(rows) + "\n") if rows else "")
    return path


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class HistoricalOHLCVTImportTest(unittest.TestCase):
    def test_real_contract_is_replay_only_and_authority_false(self):
        contract = MODULE.load_contract()
        self.assertEqual(contract["interval_minutes"], 1440)
        self.assertEqual(contract["turnover_policy"], "no_turnover_metric_derived")
        self.assertEqual(
            contract["identity_policy"],
            "source_alias_preserved_no_historical_canonical_backfill",
        )
        self.assertTrue(all(value is False for value in MODULE.authority_boundary().values()))

    def test_import_filters_daily_usd_and_preserves_alias_and_missing_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = write_archive(
                root / "Kraken_OHLCVT.zip",
                {
                    "master_q4/XBTUSD_1440.csv": [
                        candle("2023-12-31"),
                        candle("2024-01-01", close="11.5000"),
                        candle("2024-01-03", high="14", close="13"),
                    ],
                    "master_q4/ETHUSD_1440.csv": [candle("2024-01-02")],
                    "master_q4/ETHUSD_60.csv": [candle("2024-01-02")],
                    "master_q4/ETHEUR_1440.csv": [candle("2024-01-02")],
                    "__MACOSX/master_q4/._ETHUSD_1440.csv": ["ignored"],
                },
            )
            output = root / "out"
            manifest = MODULE.import_archive(
                archive,
                output,
                start_date=dt.date(2024, 1, 1),
                end_date=dt.date(2024, 1, 3),
            )
            rows = read_rows(output / "daily_usd_1440.ndjson.gz")
            self.assertEqual(
                [(row["source_pair_id"], row["date"]) for row in rows],
                [
                    ("ETHUSD", "2024-01-02"),
                    ("XBTUSD", "2024-01-01"),
                    ("XBTUSD", "2024-01-03"),
                ],
            )
            self.assertEqual(rows[1]["source_base_alias"], "XBT")
            self.assertEqual(rows[1]["close"], "11.5")
            self.assertNotIn("canonical_asset_id", rows[1])
            self.assertNotIn("quote_turnover", rows[1])
            self.assertEqual(manifest["selected_range"]["row_count"], 3)
            self.assertEqual(manifest["selected_range"]["pair_count"], 2)
            self.assertEqual(
                manifest["missing_interval_policy"],
                "preserve_absence_no_synthesis",
            )
            self.assertTrue(
                all(value is False for value in manifest["authority"].values())
            )
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                sorted(MODULE.load_contract()["output_files"]),
            )

    def test_output_is_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = write_archive(
                root / "same.zip",
                {
                    "master_q4/BTCUSD_1440.csv": [
                        candle("2024-01-01"),
                        candle("2024-01-02"),
                    ]
                },
            )
            outputs = []
            for name in ("a", "b"):
                output = root / name
                MODULE.import_archive(
                    archive,
                    output,
                    start_date=dt.date(2024, 1, 1),
                    end_date=dt.date(2024, 1, 2),
                )
                outputs.append(output)
            for filename in MODULE.load_contract()["output_files"]:
                self.assertEqual(
                    (outputs[0] / filename).read_bytes(),
                    (outputs[1] / filename).read_bytes(),
                )

    def test_malformed_ohlc_fails_without_final_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = write_archive(
                root / "bad.zip",
                {
                    "master_q4/BTCUSD_1440.csv": [
                        candle("2024-01-01", high="8", low="9")
                    ]
                },
            )
            output = root / "out"
            with self.assertRaisesRegex(MODULE.HistoricalImportError, "CSV_OHLC_INVALID"):
                MODULE.import_archive(
                    archive,
                    output,
                    start_date=dt.date(2024, 1, 1),
                    end_date=dt.date(2024, 1, 2),
                )
            self.assertFalse(output.exists())

    def test_exact_duplicate_is_disclosed_and_conflicting_pair_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = write_archive(
                root / "duplicate.zip",
                {
                    "master_q4/BTCUSD_1440.csv": [
                        candle("2024-01-01"),
                        candle("2024-01-01"),
                    ],
                    "master_q4/ALCHUSD_1440.csv": [
                        candle("2024-01-01", volume="2"),
                        candle("2024-01-01", volume="2.0000000001"),
                    ],
                    "master_q4/EMPTYUSD_1440.csv": [],
                },
            )
            output = root / "out"
            manifest = MODULE.import_archive(
                archive,
                output,
                start_date=dt.date(2024, 1, 1),
                end_date=dt.date(2024, 1, 2),
            )
            rows = read_rows(output / "daily_usd_1440.ndjson.gz")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_pair_id"], "BTCUSD")
            inventory = json.loads(
                (output / "pair_inventory.json").read_text(encoding="utf-8")
            )
            self.assertEqual(inventory["excluded_pair_count"], 2)
            self.assertEqual(
                [item["reason"] for item in inventory["excluded_pairs"]],
                ["SOURCE_DUPLICATE_TIMESTAMP_CONFLICT", "SOURCE_EMPTY_ENTRY"],
            )
            self.assertEqual(
                inventory["pairs"][0]["exact_duplicate_dates"],
                ["2024-01-01"],
            )
            self.assertEqual(manifest["selected_range"]["excluded_pair_count"], 2)

    def test_source_archive_hash_and_output_checksums_are_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = write_archive(
                root / "source.zip",
                {"master_q4/BTCUSD_1440.csv": [candle("2024-01-01")]},
            )
            output = root / "out"
            manifest = MODULE.import_archive(
                archive,
                output,
                start_date=dt.date(2024, 1, 1),
                end_date=dt.date(2024, 1, 1),
            )
            self.assertEqual(
                manifest["archive"]["sha256"],
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            checksums = dict(
                line.split("  ", 1)
                for line in (output / "SHA256SUMS").read_text().splitlines()
            )
            for digest, name in checksums.items():
                self.assertEqual(
                    digest,
                    hashlib.sha256((output / name).read_bytes()).hexdigest(),
                )


if __name__ == "__main__":
    unittest.main()
