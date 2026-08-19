#!/usr/bin/env python3
"""Build deterministic BTC risk features from a qualified Kraken capture.

The transform reuses the P1-CR-04 source validator.  It makes no network call,
never includes the current incomplete UTC candle, and emits no calibrated
stress class, Regime score, threshold, Production wiring, or trading action.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import importlib.util
import json
from pathlib import Path
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
RISK_CONTRACT_PATH = ROOT / "config" / "btc_risk_contract.json"
PRICE_CONTRACT_PATH = ROOT / "config" / "btc_price_contract.json"
TREND_SCRIPT = Path(__file__).resolve().with_name("btc_trend.py")


def load_trend_module():
    spec = importlib.util.spec_from_file_location("atlas_btc_trend", TREND_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"PRICE_MODULE_INVALID: {TREND_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


btc_trend = load_trend_module()


class RiskError(RuntimeError):
    """Fail-closed BTC risk contract or transform violation."""


def fail(code: str, detail: str) -> None:
    raise RiskError(f"{code}: {detail}")


def load_contract(path: Path = RISK_CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("RISK_CONTRACT_INVALID", str(exc))

    expected = {
        "schema_version",
        "source_contract",
        "source_transform_version",
        "transform_version",
        "stress_feature_version",
        "replay_version",
        "return_semantics",
        "realized_vol_lookback_returns",
        "realized_vol_estimator",
        "realized_vol_annualization_days",
        "drawdown_lookback_closes",
        "drawdown_semantics",
        "replay_mode",
        "missing_data_policy",
        "stress_calibration_status",
        "output_decimal_places",
        "rounding",
    }
    if set(contract) != expected or contract.get("schema_version") != 1:
        fail("RISK_CONTRACT_INVALID", "schema or fields")
    pinned = {
        "source_contract": "config/btc_price_contract.json",
        "source_transform_version": "btc_trend/v1",
        "transform_version": "btc_risk/v1",
        "stress_feature_version": "btc_stress_features/v1",
        "replay_version": "btc_risk_replay/v1",
        "return_semantics": "simple_close_to_close",
        "realized_vol_lookback_returns": 30,
        "realized_vol_estimator": "sqrt_mean_squared_simple_returns",
        "realized_vol_annualization_days": 365,
        "drawdown_lookback_closes": 90,
        "drawdown_semantics": "close_peak_to_trough",
        "replay_mode": "as_captured_prefix_only",
        "missing_data_policy": "unknown_fail_closed_no_fill",
        "stress_calibration_status": "UNDEFINED_UNCALIBRATED",
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
    }
    if any(contract.get(key) != value for key, value in pinned.items()):
        fail("RISK_CONTRACT_INVALID", "pinned semantics")
    return contract


def render_decimal(value: Decimal, places: int) -> str:
    if not value.is_finite():
        fail("RISK_NUMBER_INVALID", str(value))
    quantum = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        fail("RISK_NUMBER_INVALID", str(exc))
    if rounded == 0:
        rounded = Decimal(0)
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def require_contiguous(candles: list) -> None:
    for before, after in zip(candles, candles[1:]):
        if after["date"] - before["date"] != dt.timedelta(days=1):
            fail(
                "RISK_HISTORY_GAP",
                f"{before['date'].isoformat()} -> {after['date'].isoformat()}",
            )


def qualified_input(
    snapshot_dir: Path,
    risk_contract: Optional[dict] = None,
) -> dict:
    risk_contract = load_contract() if risk_contract is None else risk_contract
    price_contract = btc_trend.load_contract(PRICE_CONTRACT_PATH)
    if price_contract["transform_version"] != risk_contract[
        "source_transform_version"
    ]:
        fail("SOURCE_CONTRACT_MISMATCH", price_contract["transform_version"])

    core = btc_trend.snapshot_core(snapshot_dir, price_contract)
    manifest = btc_trend.validate_manifest(
        snapshot_dir,
        core,
        price_contract,
    )
    finalized = core["series"]["finalized"]
    required = max(
        risk_contract["realized_vol_lookback_returns"] + 1,
        risk_contract["drawdown_lookback_closes"],
    )
    if len(finalized) < required:
        fail("INSUFFICIENT_RISK_HISTORY", f"{len(finalized)} < {required}")
    require_contiguous(finalized)
    return {
        "core": core,
        "manifest": manifest,
        "price_contract": price_contract,
        "finalized": finalized,
    }


def realized_volatility(candles: list, contract: dict) -> dict:
    count = contract["realized_vol_lookback_returns"]
    window = candles[-(count + 1) :]
    with localcontext() as context:
        context.prec = 50
        returns = []
        for before, after in zip(window, window[1:]):
            returns.append((after["close"] / before["close"]) - Decimal(1))
        mean_square = sum((value * value for value in returns), Decimal(0))
        mean_square /= Decimal(count)
        annualized = (
            mean_square * Decimal(contract["realized_vol_annualization_days"])
        ).sqrt()
    return {
        "lookback_returns": count,
        "window_start": window[0]["date"].isoformat(),
        "window_end": window[-1]["date"].isoformat(),
        "return_semantics": contract["return_semantics"],
        "estimator": contract["realized_vol_estimator"],
        "annualization_days": contract["realized_vol_annualization_days"],
        "annualized_fraction": render_decimal(
            annualized,
            contract["output_decimal_places"],
        ),
    }


def drawdown(candles: list, contract: dict) -> dict:
    count = contract["drawdown_lookback_closes"]
    window = candles[-count:]
    current_peak = max(window, key=lambda item: item["close"])
    with localcontext() as context:
        context.prec = 50
        current = (window[-1]["close"] / current_peak["close"]) - Decimal(1)

        running_peak = window[0]
        maximum = Decimal(0)
        maximum_peak = window[0]
        maximum_trough = window[0]
        for candle in window:
            if candle["close"] > running_peak["close"]:
                running_peak = candle
            value = (candle["close"] / running_peak["close"]) - Decimal(1)
            if value < maximum:
                maximum = value
                maximum_peak = running_peak
                maximum_trough = candle

    places = contract["output_decimal_places"]
    return {
        "lookback_closes": count,
        "window_start": window[0]["date"].isoformat(),
        "window_end": window[-1]["date"].isoformat(),
        "semantics": contract["drawdown_semantics"],
        "current_fraction": render_decimal(current, places),
        "current_peak_day": current_peak["date"].isoformat(),
        "maximum_fraction": render_decimal(maximum, places),
        "maximum_peak_day": maximum_peak["date"].isoformat(),
        "maximum_trough_day": maximum_trough["date"].isoformat(),
    }


def risk_point(candles: list, contract: dict) -> dict:
    required = max(
        contract["realized_vol_lookback_returns"] + 1,
        contract["drawdown_lookback_closes"],
    )
    if len(candles) < required:
        fail("INSUFFICIENT_RISK_HISTORY", f"{len(candles)} < {required}")
    realized = realized_volatility(candles, contract)
    decline = drawdown(candles, contract)
    return {
        "as_of_date": candles[-1]["date"].isoformat(),
        "realized_volatility": realized,
        "drawdown": decline,
        "stress_features": {
            "version": contract["stress_feature_version"],
            "calibration_status": contract["stress_calibration_status"],
            "thresholds_applied": False,
            "classification": "UNDEFINED",
            "feature_vector": {
                "realized_vol_30d_annualized_fraction": realized[
                    "annualized_fraction"
                ],
                "current_drawdown_90d_fraction": decline["current_fraction"],
                "maximum_drawdown_90d_fraction": decline["maximum_fraction"],
            },
        },
    }


def authority_boundary() -> dict:
    return {
        "stress_threshold_authorized": False,
        "stress_classification_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def build_transform(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    source = qualified_input(snapshot_dir, contract)
    core = source["core"]
    point = risk_point(source["finalized"], contract)
    return {
        "schema_version": 1,
        "transform_version": contract["transform_version"],
        "market": "CRYPTO",
        "asset": "BTC",
        "quote_currency": source["price_contract"]["quote_currency"],
        "market_timezone": source["price_contract"]["market_timezone"],
        "measurement": "btc_risk_features",
        "status": "AVAILABLE_UNCALIBRATED",
        "latest_finalized_day": core["latest_finalized_day"],
        "risk_point": point,
        "current_candle": {
            "date": core["current_excluded_day"],
            "excluded": True,
            "reason": "source_documents_not_yet_committed_timeframe",
        },
        "lineage": {
            "pit_status": "qualified_direct_capture",
            "vintage_date": core["snapshot_date"],
            "available_at": core["fetched_at_utc"],
            "source_name": source["price_contract"]["source_name"],
            "source_sha256": core["response_sha256"],
            "capture_version": source["manifest"]["capture_version"],
            "source_transform_version": contract["source_transform_version"],
            "missing_data_policy": contract["missing_data_policy"],
        },
    } | authority_boundary()


def parse_optional_date(value: Optional[str], label: str) -> Optional[dt.date]:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail("REPLAY_DATE_INVALID", f"{label}={value}")


def build_replay(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    source = qualified_input(snapshot_dir, contract)
    start = parse_optional_date(start_date, "start")
    end = parse_optional_date(end_date, "end")
    if start is not None and end is not None and start > end:
        fail("REPLAY_DATE_INVALID", "start after end")

    required = max(
        contract["realized_vol_lookback_returns"] + 1,
        contract["drawdown_lookback_closes"],
    )
    points = []
    for index in range(required - 1, len(source["finalized"])):
        prefix = source["finalized"][: index + 1]
        as_of = prefix[-1]["date"]
        if start is not None and as_of < start:
            continue
        if end is not None and as_of > end:
            continue
        points.append(risk_point(prefix, contract))
    if not points:
        fail("REPLAY_RANGE_EMPTY", f"{start_date}..{end_date}")

    core = source["core"]
    return {
        "schema_version": 1,
        "replay_version": contract["replay_version"],
        "transform_version": contract["transform_version"],
        "mode": contract["replay_mode"],
        "source_vintage": core["snapshot_date"],
        "source_available_at": core["fetched_at_utc"],
        "source_sha256": core["response_sha256"],
        "capture_version": source["manifest"]["capture_version"],
        "point_count": len(points),
        "first_as_of_date": points[0]["as_of_date"],
        "last_as_of_date": points[-1]["as_of_date"],
        "historical_pit_authorized": False,
        "threshold_research_authorized": False,
        "points": points,
    } | authority_boundary()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    transform = sub.add_parser("transform")
    transform.add_argument("snapshot_dir", type=Path)
    transform.add_argument("--out", type=Path)

    replay = sub.add_parser("replay")
    replay.add_argument("snapshot_dir", type=Path)
    replay.add_argument("--start-date")
    replay.add_argument("--end-date")
    replay.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    if args.command == "transform":
        payload = build_transform(args.snapshot_dir)
    else:
        payload = build_replay(
            args.snapshot_dir,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    if args.out:
        print(btc_trend.write_output(payload, args.out))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RiskError, btc_trend.TrendError) as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
