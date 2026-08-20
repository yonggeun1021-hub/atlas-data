#!/usr/bin/env python3
"""P1-CR-04 BTC Trend source, PIT, transform, and workflow regression.

All response fixtures live under temporary directories.  No live Kraken call
or tracked factor output is made by this test.
"""

import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "btc_trend.py"
CONTRACT_PATH = ROOT / "config" / "btc_price_contract.json"
WORKFLOW = ROOT / ".github" / "workflows" / "btc-price-capture.yml"

SPEC = importlib.util.spec_from_file_location("btc_trend", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract(CONTRACT_PATH)

with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)

STEPS = WF["jobs"]["capture"]["steps"]


def workflow_step(name):
    for item in STEPS:
        if item.get("name") == name:
            return item
    return None


def candle(day, close, *, timestamp_offset=0):
    close = int(close)
    timestamp = int(
        dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc).timestamp()
    ) + timestamp_offset
    return [
        timestamp,
        str(close - 1),
        str(close + 1),
        str(close - 2),
        str(close),
        str(close),
        "10.5",
        42,
    ]


def response(
    vintage="2026-08-19",
    finalized_count=205,
    current_close=999999,
):
    vintage_date = dt.date.fromisoformat(vintage)
    start = vintage_date - dt.timedelta(days=finalized_count)
    rows = [
        candle(start + dt.timedelta(days=index), 1001 + index)
        for index in range(finalized_count)
    ]
    rows.append(candle(vintage_date, current_close))
    return {
        "error": [],
        "result": {
            "BTC/USD": rows,
            "last": rows[-2][0],
        },
    }


def write_snapshot(
    root,
    vintage="2026-08-19",
    payload=None,
    capture_version="btc-price-capture/v1",
    manifest=True,
):
    payload = response(vintage) if payload is None else payload
    snapshot = Path(root) / vintage
    snapshot.mkdir(parents=True)
    (snapshot / "_downloaded_at.txt").write_text(
        f"{vintage}T00:20:00Z\n", encoding="utf-8"
    )
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    raw_name = Path(CONTRACT["raw_file"]).name.removesuffix(".gz")
    (snapshot / "_sha256.txt").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {raw_name}\n",
        encoding="utf-8",
    )
    with gzip.open(snapshot / CONTRACT["raw_file"], "wb") as stream:
        stream.write(raw)
    if manifest:
        MODULE.build_manifest(
            snapshot,
            capture_version,
            contract=CONTRACT,
        )
    return snapshot


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class BtcTrendTest(unittest.TestCase):
    def require_step(self, name):
        found = workflow_step(name)
        self.assertIsNotNone(found, f"missing workflow step: {name}")
        return found

    def test_contract_pins_primary_source_and_finality_semantics(self):
        self.assertEqual(CONTRACT["source_name"], "kraken_spot_ohlc")
        self.assertEqual(CONTRACT["response_pair"], "BTC/USD")
        self.assertEqual(CONTRACT["interval_minutes"], 1440)
        self.assertEqual(CONTRACT["market_timezone"], "UTC")
        self.assertEqual(
            CONTRACT["current_candle_policy"], "exclude_last_row_always"
        )
        self.assertEqual(CONTRACT["minimum_finalized_candles"], 200)
        self.assertEqual(
            CONTRACT["gap_policy"], "exact_contiguous_utc_calendar_days"
        )

    def test_manifest_preserves_sha_and_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            validated = MODULE.validate_snapshot(snapshot, CONTRACT)
            manifest = json.loads(
                (snapshot / "_manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(validated["capture_version"], "btc-price-capture/v1")
            self.assertEqual(validated["finalized_rows"], 205)
            self.assertEqual(validated["latest_finalized_day"], "2026-08-18")
            self.assertEqual(validated["current_excluded_day"], "2026-08-19")
            self.assertEqual(
                manifest["raw"]["response_sha256"],
                validated["response_sha256"],
            )
            with self.assertRaisesRegex(
                MODULE.TrendError, "APPEND_ONLY_VIOLATION"
            ):
                MODULE.build_manifest(
                    snapshot,
                    "btc-price-capture/v1",
                    contract=CONTRACT,
                )

    def test_transform_uses_exactly_200_finalized_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            result = MODULE.build_transform(snapshot, CONTRACT)

            self.assertEqual(result["transform_version"], "btc_trend/v1")
            self.assertEqual(result["status"], "AVAILABLE")
            self.assertEqual(result["latest_finalized_day"], "2026-08-18")
            self.assertEqual(result["latest_finalized_close"], "1205")
            self.assertEqual(result["dma_200"], "1105.5")
            self.assertEqual(result["direction"], "ABOVE_200DMA")
            self.assertEqual(result["window"]["count"], 200)
            self.assertEqual(result["window"]["start"], "2026-01-31")
            self.assertEqual(result["window"]["end"], "2026-08-18")
            self.assertTrue(result["current_candle"]["excluded"])
            self.assertEqual(result["current_candle"]["date"], "2026-08-19")
            self.assertFalse(result["regime_score_authorized"])
            self.assertFalse(result["production_wiring_authorized"])
            self.assertFalse(has_float(result))

    def test_current_incomplete_candle_cannot_change_trend(self):
        with tempfile.TemporaryDirectory() as tmp:
            low = write_snapshot(
                Path(tmp) / "low",
                payload=response(current_close=100),
            )
            high = write_snapshot(
                Path(tmp) / "high",
                payload=response(current_close=999999999),
            )
            a = MODULE.build_transform(low, CONTRACT)
            b = MODULE.build_transform(high, CONTRACT)

            for field in (
                "latest_finalized_day",
                "latest_finalized_close",
                "dma_200",
                "direction",
                "window",
            ):
                self.assertEqual(a[field], b[field])
            self.assertNotEqual(
                a["lineage"]["source_sha256"],
                b["lineage"]["source_sha256"],
            )

    def test_missing_day_fails_closed_without_shorter_window(self):
        payload = response()
        payload["result"]["BTC/USD"].pop(-50)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp, payload=payload, manifest=False)
            with self.assertRaisesRegex(
                MODULE.TrendError, "MISSING_EXACT_DAILY_CANDLE"
            ):
                MODULE.snapshot_core(snapshot, CONTRACT)

    def test_insufficient_history_and_stale_latest_day_fail_closed(self):
        too_short = response(finalized_count=199)
        too_long = response(finalized_count=721)
        stale = response()
        stale["result"]["BTC/USD"].pop(-2)

        cases = (
            (too_short, "INSUFFICIENT_FINALIZED_HISTORY"),
            (too_long, "FINALIZED_HISTORY_EXCEEDS_SOURCE_BOUND"),
            (stale, "MISSING_LATEST_FINALIZED_DAY"),
        )
        for payload, error in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                snapshot = write_snapshot(tmp, payload=payload, manifest=False)
                with self.assertRaisesRegex(MODULE.TrendError, error):
                    MODULE.snapshot_core(snapshot, CONTRACT)

    def test_source_error_current_date_and_timestamp_fail_closed(self):
        source_error = response()
        source_error["error"] = ["EGeneral:Unavailable"]
        wrong_current = response()
        wrong_current["result"]["BTC/USD"][-1] = candle(
            dt.date(2026, 8, 18), 999999
        )
        bad_time = response()
        bad_time["result"]["BTC/USD"][0][0] += 60

        cases = (
            (source_error, "SOURCE_ERROR"),
            (wrong_current, "CANDLE_ORDER_OR_DUPLICATE"),
            (bad_time, "CANDLE_TIME_INVALID"),
        )
        for payload, error in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                snapshot = write_snapshot(tmp, payload=payload, manifest=False)
                with self.assertRaisesRegex(MODULE.TrendError, error):
                    MODULE.snapshot_core(snapshot, CONTRACT)

    def test_checksum_or_manifest_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            (snapshot / "_sha256.txt").write_text(
                f"{'0' * 64}  kraken_ohlc_xbtusd.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.TrendError, "CHECKSUM_MISMATCH"):
                MODULE.build_transform(snapshot, CONTRACT)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            manifest_path = snapshot / "_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"]["current_candle_policy"] = "include"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.TrendError, "MANIFEST_MISMATCH"):
                MODULE.build_transform(snapshot, CONTRACT)

    def test_output_is_deterministic_and_optional_path_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(Path(tmp) / "raw")
            first = MODULE.build_transform(snapshot, CONTRACT)
            second = MODULE.build_transform(snapshot, CONTRACT)
            output = Path(tmp) / "output" / "btc_trend.json"
            MODULE.write_output(first, output)

            self.assertEqual(first, second)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), first
            )
            self.assertFalse(list(output.parent.glob(".*.tmp.*")))

    def test_workflow_is_append_only_one_call_and_no_factor_publication(self):
        triggers = WF.get("on", WF.get(True))
        self.assertEqual(
            {item["cron"] for item in triggers["schedule"]},
            {"20 0 * * *"},
        )
        capture = self.require_step(
            "Capture immutable Kraken BTC/USD daily OHLC"
        )
        validation = self.require_step(
            "Validate BTC Trend and Risk from immutable snapshot"
        )
        upload = self.require_step("Upload BTC Trend and Risk live validation")
        commit = self.require_step("Commit BTC price evidence")
        command = capture.get("run", "")

        self.assertEqual(command.count("api.kraken.com/0/public/OHLC"), 1)
        self.assertIn("_manifest.json", command)
        self.assertIn("btc-price-capture/v1", command)
        self.assertIn("SNAPSHOT_DIR=$DIR", command)
        validation_command = validation.get("run", "")
        self.assertIn("btc_trend.py validate", validation_command)
        self.assertIn("btc_trend.py transform", validation_command)
        self.assertIn("$RUNNER_TEMP/btc_trend.json", validation_command)
        self.assertEqual(upload.get("if"), "always() && env.SNAPSHOT_DIR != ''")
        upload_paths = upload.get("with", {}).get("path", "")
        self.assertIn("btc_trend.json", upload_paths)
        self.assertIn("btc_risk.json", upload_paths)
        self.assertIn("btc_risk_replay.json", upload_paths)
        self.assertEqual(commit.get("if"), "always()")
        commit_command = commit.get("run", "")
        self.assertIn(
            'git add "evidence/crypto/btc/raw/$SNAPSHOT_DATE"',
            commit_command,
        )
        self.assertNotIn("git add evidence/crypto/btc/raw\n", commit_command)
        self.assertIn("data/operations/btc_capture_runs", commit_command)
        self.assertNotIn("btc_trend.json", commit_command)
        self.assertNotIn("regime", commit_command.lower())


if __name__ == "__main__":
    unittest.main()
