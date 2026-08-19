#!/usr/bin/env python3
"""P1-CR-05 BTC risk feature and as-captured replay regression.

All Kraken responses are generated under temporary directories.  This test
makes no live request and writes no tracked factor output.
"""

import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "btc_risk.py"
WORKFLOW = ROOT / ".github" / "workflows" / "btc-price-capture.yml"

SPEC = importlib.util.spec_from_file_location("btc_risk", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
RISK_CONTRACT = MODULE.load_contract()
PRICE_CONTRACT = MODULE.btc_trend.load_contract()

with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)

STEPS = WF["jobs"]["capture"]["steps"]


def workflow_step(name):
    for item in STEPS:
        if item.get("name") == name:
            return item
    return None


def text_number(value):
    return format(Decimal(str(value)), "f")


def candle(day, close):
    close = Decimal(str(close))
    return [
        int(dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc).timestamp()),
        text_number(close),
        text_number(close + Decimal(1)),
        text_number(close - Decimal(1)),
        text_number(close),
        text_number(close),
        "10.5",
        42,
    ]


def response(
    vintage="2026-08-20",
    finalized_closes=None,
    current_close=None,
):
    vintage_date = dt.date.fromisoformat(vintage)
    closes = (
        [Decimal(1000 + index) for index in range(205)]
        if finalized_closes is None
        else [Decimal(str(value)) for value in finalized_closes]
    )
    start = vintage_date - dt.timedelta(days=len(closes))
    rows = [
        candle(start + dt.timedelta(days=index), close)
        for index, close in enumerate(closes)
    ]
    current = closes[-1] if current_close is None else Decimal(str(current_close))
    rows.append(candle(vintage_date, current))
    return {
        "error": [],
        "result": {
            "BTC/USD": rows,
            "last": rows[-2][0],
        },
    }


def write_snapshot(root, payload=None, manifest=True):
    vintage = "2026-08-20"
    payload = response(vintage) if payload is None else payload
    snapshot = Path(root) / vintage
    snapshot.mkdir(parents=True)
    (snapshot / "_downloaded_at.txt").write_text(
        "2026-08-20T00:20:00Z\n",
        encoding="utf-8",
    )
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    raw_name = Path(PRICE_CONTRACT["raw_file"]).name.removesuffix(".gz")
    (snapshot / "_sha256.txt").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {raw_name}\n",
        encoding="utf-8",
    )
    with gzip.open(snapshot / PRICE_CONTRACT["raw_file"], "wb") as stream:
        stream.write(raw)
    if manifest:
        MODULE.btc_trend.build_manifest(
            snapshot,
            "btc-price-capture/v1",
            contract=PRICE_CONTRACT,
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


class BtcRiskTest(unittest.TestCase):
    def require_step(self, name):
        found = workflow_step(name)
        self.assertIsNotNone(found, f"missing workflow step: {name}")
        return found

    def test_contract_pins_unclassified_risk_semantics(self):
        self.assertEqual(RISK_CONTRACT["transform_version"], "btc_risk/v1")
        self.assertEqual(
            RISK_CONTRACT["return_semantics"],
            "simple_close_to_close",
        )
        self.assertEqual(RISK_CONTRACT["realized_vol_lookback_returns"], 30)
        self.assertEqual(RISK_CONTRACT["drawdown_lookback_closes"], 90)
        self.assertEqual(
            RISK_CONTRACT["stress_calibration_status"],
            "UNDEFINED_UNCALIBRATED",
        )
        self.assertEqual(
            RISK_CONTRACT["replay_mode"],
            "as_captured_prefix_only",
        )

    def test_flat_prices_have_zero_volatility_and_drawdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp, response(finalized_closes=[100] * 205))
            result = MODULE.build_transform(snapshot, RISK_CONTRACT)
            point = result["risk_point"]

            self.assertEqual(
                point["realized_volatility"]["annualized_fraction"],
                "0",
            )
            self.assertEqual(point["drawdown"]["current_fraction"], "0")
            self.assertEqual(point["drawdown"]["maximum_fraction"], "0")
            self.assertEqual(result["latest_finalized_day"], "2026-08-19")
            self.assertEqual(result["current_candle"]["date"], "2026-08-20")
            self.assertTrue(result["current_candle"]["excluded"])

    def test_drawdown_keeps_exact_peak_trough_and_no_stress_class(self):
        closes = [100] * 205
        closes[-70] = 120
        closes[-40] = 60
        closes[-1] = 90

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp, response(finalized_closes=closes))
            result = MODULE.build_transform(snapshot, RISK_CONTRACT)
            point = result["risk_point"]
            decline = point["drawdown"]

            self.assertEqual(decline["current_fraction"], "-0.25")
            self.assertEqual(decline["maximum_fraction"], "-0.5")
            self.assertLess(
                decline["maximum_peak_day"],
                decline["maximum_trough_day"],
            )
            self.assertEqual(
                point["stress_features"]["classification"],
                "UNDEFINED",
            )
            self.assertFalse(point["stress_features"]["thresholds_applied"])
            self.assertFalse(result["stress_threshold_authorized"])
            self.assertFalse(result["stress_classification_authorized"])
            self.assertFalse(result["regime_score_authorized"])
            self.assertFalse(result["production_wiring_authorized"])
            self.assertFalse(result["trading_action_authorized"])

    def test_current_incomplete_candle_cannot_change_risk_features(self):
        closes = [100 + index for index in range(205)]
        with tempfile.TemporaryDirectory() as tmp:
            low = write_snapshot(
                Path(tmp) / "low",
                response(finalized_closes=closes, current_close=2),
            )
            high = write_snapshot(
                Path(tmp) / "high",
                response(finalized_closes=closes, current_close=999999999),
            )
            first = MODULE.build_transform(low, RISK_CONTRACT)
            second = MODULE.build_transform(high, RISK_CONTRACT)

            self.assertEqual(first["risk_point"], second["risk_point"])
            self.assertNotEqual(
                first["lineage"]["source_sha256"],
                second["lineage"]["source_sha256"],
            )

    def test_replay_prefix_does_not_read_future_rows(self):
        baseline = [100 + index for index in range(205)]
        changed = list(baseline)
        changed[150:190] = [5000 + index for index in range(40)]

        with tempfile.TemporaryDirectory() as tmp:
            first_snapshot = write_snapshot(
                Path(tmp) / "first",
                response(finalized_closes=baseline),
            )
            second_snapshot = write_snapshot(
                Path(tmp) / "second",
                response(finalized_closes=changed),
            )
            first = MODULE.build_replay(first_snapshot, RISK_CONTRACT)
            second = MODULE.build_replay(second_snapshot, RISK_CONTRACT)
            first_points = {item["as_of_date"]: item for item in first["points"]}
            second_points = {item["as_of_date"]: item for item in second["points"]}
            target = sorted(first_points)[120 - 89]

            self.assertEqual(first_points[target], second_points[target])
            self.assertFalse(first["historical_pit_authorized"])
            self.assertFalse(first["threshold_research_authorized"])

    def test_replay_exposes_shock_features_without_classifying_them(self):
        stable = [100] * 205
        shocked = list(stable)
        shocked[-20:] = [50] * 20

        with tempfile.TemporaryDirectory() as tmp:
            stable_snapshot = write_snapshot(
                Path(tmp) / "stable",
                response(finalized_closes=stable),
            )
            shock_snapshot = write_snapshot(
                Path(tmp) / "shock",
                response(finalized_closes=shocked),
            )
            normal = MODULE.build_replay(stable_snapshot, RISK_CONTRACT)
            shock = MODULE.build_replay(shock_snapshot, RISK_CONTRACT)
            normal_last = normal["points"][-1]
            shock_last = shock["points"][-1]

            self.assertGreater(
                Decimal(
                    shock_last["realized_volatility"]["annualized_fraction"]
                ),
                Decimal(
                    normal_last["realized_volatility"]["annualized_fraction"]
                ),
            )
            self.assertEqual(
                shock_last["drawdown"]["maximum_fraction"],
                "-0.5",
            )
            self.assertEqual(
                shock_last["stress_features"]["classification"],
                "UNDEFINED",
            )

    def test_gap_anywhere_in_replay_source_fails_closed(self):
        payload = response()
        payload["result"]["BTC/USD"].pop(2)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp, payload)
            with self.assertRaisesRegex(MODULE.RiskError, "RISK_HISTORY_GAP"):
                MODULE.build_replay(snapshot, RISK_CONTRACT)

    def test_source_checksum_and_manifest_tamper_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            (snapshot / "_sha256.txt").write_text(
                f"{'0' * 64}  kraken_ohlc_xbtusd.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MODULE.btc_trend.TrendError,
                "CHECKSUM_MISMATCH",
            ):
                MODULE.build_transform(snapshot, RISK_CONTRACT)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            path = snapshot / "_manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["source"]["current_candle_policy"] = "include"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.btc_trend.TrendError,
                "MANIFEST_MISMATCH",
            ):
                MODULE.build_transform(snapshot, RISK_CONTRACT)

    def test_transform_and_replay_are_deterministic_without_float(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            first = MODULE.build_transform(snapshot, RISK_CONTRACT)
            second = MODULE.build_transform(snapshot, RISK_CONTRACT)
            replay = MODULE.build_replay(
                snapshot,
                RISK_CONTRACT,
                start_date="2026-08-17",
                end_date="2026-08-19",
            )

            self.assertEqual(first, second)
            self.assertFalse(has_float(first))
            self.assertFalse(has_float(replay))
            self.assertEqual(replay["point_count"], 3)
            self.assertEqual(replay["first_as_of_date"], "2026-08-17")
            self.assertEqual(replay["last_as_of_date"], "2026-08-19")

    def test_workflow_reuses_one_capture_and_publishes_no_risk_factor(self):
        triggers = WF.get("on", WF.get(True))
        self.assertEqual(
            {item["cron"] for item in triggers["schedule"]},
            {"20 0 * * *"},
        )
        capture = self.require_step("Capture immutable Kraken BTC/USD daily OHLC")
        commit = self.require_step("Commit BTC price evidence")
        command = capture.get("run", "")

        self.assertEqual(command.count("api.kraken.com/0/public/OHLC"), 1)
        self.assertIn('btc_risk.py" transform', command)
        self.assertIn('btc_risk.py" replay', command)
        self.assertIn("$RUNNER_TEMP/btc_risk.json", command)
        self.assertIn("$RUNNER_TEMP/btc_risk_replay.json", command)
        commit_command = commit.get("run", "")
        self.assertIn("git add evidence/crypto/btc/raw", commit_command)
        self.assertNotIn("btc_risk", commit_command)
        self.assertNotIn("data/", commit_command)


if __name__ == "__main__":
    unittest.main()
