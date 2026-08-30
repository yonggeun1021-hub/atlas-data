#!/usr/bin/env python3
"""Connect current US evidence to pipeline-symbol review contexts.

This bridge uses only committed free-market evidence and the committed stage
history.  It emits observable price/return facts and honest WAIT/BLOCKED
review states.  It never classifies the US Regime and never creates an order.
"""
from __future__ import annotations

import argparse
import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo
import datetime as dt


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "us_symbol_market_review_contract.json"
MARKET_PATH = ROOT / "data" / "latest_free_market_data.json"
STAGE_PATH = ROOT / "data" / "stage_history.json"
OUTPUT_ROOT = ROOT / "evidence" / "us_symbol_market_review"
LATEST_PATH = ROOT / "data" / "latest_us_symbol_market_review.json"
OUTPUT_SCHEMA_VERSION = "us_symbol_market_review_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UsSymbolMarketReviewError(ValueError):
    """Fail-closed US review bridge violation."""


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsSymbolMarketReviewError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _require_all_false(authority: object, code: str = "AUTHORITY_INVALID") -> None:
    if not isinstance(authority, dict) or not authority or any(value is not False for value in authority.values()):
        raise UsSymbolMarketReviewError(code)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if value.get("contract_version") != "us_symbol_market_review/1":
        raise UsSymbolMarketReviewError("CONTRACT_VERSION_INVALID")
    if value.get("output_schema_version") != OUTPUT_SCHEMA_VERSION:
        raise UsSymbolMarketReviewError("OUTPUT_SCHEMA_VERSION_INVALID")
    if value.get("required_axes") != ["TREND", "RISK_VOL", "LIQUIDITY", "BREADTH", "LEADERSHIP"]:
        raise UsSymbolMarketReviewError("REQUIRED_AXES_INVALID")
    if value.get("supported_pipeline_subjects") != ["TSM", "SNDK"]:
        raise UsSymbolMarketReviewError("SUPPORTED_SUBJECTS_INVALID")
    if value.get("return_windows_sessions") != [5, 20, 60]:
        raise UsSymbolMarketReviewError("RETURN_WINDOWS_INVALID")
    _require_all_false(value.get("authority"), "CONTRACT_AUTHORITY_INVALID")
    return value


def _validate_market_packet(packet: dict) -> None:
    if packet.get("schema_version") != "free_market_data_capture/5":
        raise UsSymbolMarketReviewError("MARKET_SCHEMA_INVALID")
    if packet.get("contract_version") != "free_market_data/3":
        raise UsSymbolMarketReviewError("MARKET_CONTRACT_INVALID")
    claimed = packet.get("packet_sha256")
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise UsSymbolMarketReviewError("MARKET_PACKET_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise UsSymbolMarketReviewError("MARKET_PACKET_SHA256_MISMATCH")
    _require_all_false({
        key: value for key, value in packet.get("authority", {}).items()
        if key != "evidence_capture_only"
    }, "MARKET_AUTHORITY_INVALID")
    if packet.get("authority", {}).get("evidence_capture_only") is not True:
        raise UsSymbolMarketReviewError("MARKET_EVIDENCE_AUTHORITY_INVALID")


def _decimal(value: object, code: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise UsSymbolMarketReviewError(code) from exc
    if not parsed.is_finite():
        raise UsSymbolMarketReviewError(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _session_return(rows: list[dict], sessions: int) -> str:
    if len(rows) <= sessions:
        raise UsSymbolMarketReviewError(f"PRICE_HISTORY_INSUFFICIENT:{sessions}")
    latest = _decimal(rows[-1].get("close"), "PRICE_CLOSE_INVALID")
    prior = _decimal(rows[-(sessions + 1)].get("close"), "PRICE_CLOSE_INVALID")
    if prior == 0:
        raise UsSymbolMarketReviewError("PRICE_CLOSE_ZERO")
    value = ((latest / prior) - Decimal("1")) * Decimal("100")
    return _decimal_text(value.quantize(Decimal("0.0001")))


def _compact_source(market: dict, stages: dict, contract: dict) -> dict:
    stage_dates = sorted(stages)
    if not stage_dates:
        raise UsSymbolMarketReviewError("STAGE_HISTORY_EMPTY")
    stage_as_of = stage_dates[-1]
    latest_stage = stages[stage_as_of]
    if not isinstance(latest_stage, dict):
        raise UsSymbolMarketReviewError("LATEST_STAGE_INVALID")
    subjects = {}
    for symbol in contract["supported_pipeline_subjects"]:
        row = latest_stage.get(symbol)
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not isinstance(row.get("stage"), str):
            raise UsSymbolMarketReviewError(f"PIPELINE_SUBJECT_MISSING:{symbol}")
        subjects[symbol] = {"name": row["name"], "stage": row["stage"]}

    grouped = {symbol: [] for symbol in contract["supported_pipeline_subjects"]}
    daily = market.get("alpaca", {}).get("daily_bars")
    if market.get("alpaca", {}).get("status") == "READY" and not isinstance(daily, list):
        raise UsSymbolMarketReviewError("DAILY_BARS_INVALID")
    for row in daily or []:
        symbol = row.get("symbol") if isinstance(row, dict) else None
        if symbol in grouped:
            grouped[symbol].append(copy.deepcopy(row))
    for rows in grouped.values():
        rows.sort(key=lambda row: row.get("opened_at", ""))

    return {
        "contract": copy.deepcopy(contract),
        "market_capture": {
            "schema_version": market["schema_version"],
            "contract_version": market["contract_version"],
            "observed_at_utc": market.get("observed_at_utc"),
            "packet_sha256": market["packet_sha256"],
            "alpaca_status": market.get("alpaca", {}).get("status"),
            "alpaca_scope": market.get("alpaca", {}).get("source_scope"),
        },
        "us_market_reference": copy.deepcopy(market.get("us_market_reference")),
        "fred": copy.deepcopy(market.get("fred")),
        "fred_liquidity": copy.deepcopy(market.get("fred_liquidity")),
        "symbol_daily_bars": grouped,
        "stage_snapshot": {"as_of": stage_as_of, "subjects": subjects},
        "stage_history_sha256": payload_sha256(stages),
    }


def _axes(source: dict, contract: dict) -> dict:
    reference = source.get("us_market_reference") or {}
    fred = source.get("fred") or {}
    liquidity = source.get("fred_liquidity") or {}
    trend_defined = reference.get("status") == "READY" and len(reference.get("trend_etfs") or []) == 3
    risk_defined = fred.get("status") == "READY" and fred.get("value") is not None
    liquidity_defined = liquidity.get("status") == "READY" and len(liquidity.get("series") or []) == 2
    axes = {
        "TREND": {
            "status": "DEFINED" if trend_defined else "UNDEFINED",
            "as_of": reference.get("as_of_session_date"),
            "facts": copy.deepcopy(reference.get("trend_etfs") or []),
            "reason": None if trend_defined else "US_TREND_REFERENCE_UNAVAILABLE",
        },
        "RISK_VOL": {
            "status": "DEFINED" if risk_defined else "UNDEFINED",
            "as_of": fred.get("observation_date"),
            "facts": {"series_id": fred.get("series_id"), "value": fred.get("value")} if risk_defined else None,
            "reason": None if risk_defined else "VIX_REFERENCE_UNAVAILABLE",
        },
        "LIQUIDITY": {
            "status": "DEFINED" if liquidity_defined else "UNDEFINED",
            "as_of": liquidity.get("captured_at_utc"),
            "facts": copy.deepcopy(liquidity.get("series") or []),
            "reason": None if liquidity_defined else "US_LIQUIDITY_REFERENCE_UNAVAILABLE",
        },
        "BREADTH": {"status": "UNDEFINED", "as_of": None, "facts": None, "reason": "US_BREADTH_NOT_IMPLEMENTED"},
        "LEADERSHIP": {"status": "UNDEFINED", "as_of": None, "facts": None, "reason": "CANONICAL_US_LEADERSHIP_NOT_IMPLEMENTED"},
    }
    defined = [axis for axis in contract["required_axes"] if axes[axis]["status"] == "DEFINED"]
    missing = [axis for axis in contract["required_axes"] if axes[axis]["status"] != "DEFINED"]
    return {
        "required_count": len(contract["required_axes"]),
        "defined_count": len(defined),
        "ratio": f"{len(defined)}/{len(contract['required_axes'])}",
        "missing_axes": missing,
        "axes": axes,
        "aggregate_regime": "UNKNOWN",
    }


def _symbol_reviews(source: dict, coverage: dict, contract: dict) -> list[dict]:
    rows = []
    stage_snapshot = source["stage_snapshot"]
    for symbol in contract["supported_pipeline_subjects"]:
        identity = stage_snapshot["subjects"][symbol]
        prices = source["symbol_daily_bars"][symbol]
        if prices:
            price = {
                "status": "OBSERVED",
                "as_of_session_date": str(prices[-1].get("opened_at"))[:10],
                "close_usd": _decimal_text(_decimal(prices[-1].get("close"), "PRICE_CLOSE_INVALID")),
                "available_session_count": len(prices),
                "returns": {
                    f"{window}_session_pct": _session_return(prices, window)
                    for window in contract["return_windows_sessions"]
                },
                "source_scope": source["market_capture"]["alpaca_scope"],
            }
            entry_state = contract["entry_policy"]["observed_price_state"]
            entry_reasons = [
                "CURRENT_PRICE_AND_RETURN_CONTEXT_CONNECTED",
                "OFFICIAL_AXES_INCOMPLETE:" + ",".join(coverage["missing_axes"]),
                "FINAL_US_REGIME_NOT_AVAILABLE",
                "PIPELINE_STAGE_IS_NOT_BUY_AUTHORITY",
            ]
        else:
            price = {
                "status": "UNAVAILABLE",
                "as_of_session_date": None,
                "close_usd": None,
                "available_session_count": 0,
                "returns": None,
                "source_scope": source["market_capture"]["alpaca_scope"],
            }
            entry_state = contract["entry_policy"]["missing_price_state"]
            entry_reasons = [
                "PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE",
                "OFFICIAL_AXES_INCOMPLETE:" + ",".join(coverage["missing_axes"]),
                "FINAL_US_REGIME_NOT_AVAILABLE",
            ]
        rows.append({
            "symbol": symbol,
            "name": identity["name"],
            "pipeline_stage": identity["stage"],
            "pipeline_as_of": stage_snapshot["as_of"],
            "price_context": price,
            "entry_review": {
                "state": entry_state,
                "reasons": entry_reasons,
                "automatic_entry_generated": False,
                "order_draft": None,
            },
            "holding_review": {
                "state": contract["holding_policy"]["state_without_account_position"],
                "reason": "ACCOUNT_POSITION_NOT_INCLUDED_IN_PUBLIC_MARKET_EVIDENCE",
                "automatic_holding_action_generated": False,
            },
            "exit_review": {
                "state": contract["exit_policy"]["state_without_account_position"],
                "reason": "ACCOUNT_POSITION_NOT_INCLUDED_IN_PUBLIC_MARKET_EVIDENCE",
                "automatic_exit_generated": False,
            },
        })
    return rows


def _build_from_source(source: dict) -> dict:
    contract = source.get("contract")
    if contract != load_contract():
        raise UsSymbolMarketReviewError("EMBEDDED_CONTRACT_MISMATCH")
    coverage = _axes(source, contract)
    symbols = _symbol_reviews(source, coverage, contract)
    observed_text = source["market_capture"].get("observed_at_utc")
    try:
        observed = dt.datetime.fromisoformat(str(observed_text).replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsSymbolMarketReviewError("OBSERVED_AT_INVALID") from exc
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "generated_at": observed_text,
        "operational_date_kst": observed.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "source_market_packet_sha256": source["market_capture"]["packet_sha256"],
        "source_stage_history_sha256": source["stage_history_sha256"],
        "five_axis": coverage,
        "symbols": symbols,
        "summary": {
            "symbol_count": len(symbols),
            "price_connected_count": sum(row["price_context"]["status"] == "OBSERVED" for row in symbols),
            "entry_wait_count": sum(row["entry_review"]["state"] == "WAIT" for row in symbols),
            "entry_blocked_count": sum(row["entry_review"]["state"] == "BLOCKED" for row in symbols),
            "automatic_entry_count": 0,
            "automatic_exit_count": 0,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "source": copy.deepcopy(source),
    }
    _require_all_false(packet["authority"])
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_review(market: dict, stages: dict, *, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    _validate_market_packet(market)
    return _build_from_source(_compact_source(market, stages, contract))


def validate_output(packet: dict) -> dict:
    expected = {
        "schema_version", "contract_version", "mode", "generated_at", "operational_date_kst",
        "source_market_packet_sha256", "source_stage_history_sha256", "five_axis", "symbols",
        "summary", "authority", "source", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected:
        raise UsSymbolMarketReviewError("OUTPUT_SCHEMA_MISMATCH")
    if packet.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise UsSymbolMarketReviewError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    claimed = packet.get("packet_sha256")
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise UsSymbolMarketReviewError("PACKET_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise UsSymbolMarketReviewError("PACKET_SHA256_MISMATCH")
    _require_all_false(packet.get("authority"))
    rebuilt = _build_from_source(packet.get("source"))
    if rebuilt != packet:
        raise UsSymbolMarketReviewError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def _atomic_write(path: Path, packet: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.replace(temp, path)


def populate(
    market_path: Path = MARKET_PATH,
    stage_path: Path = STAGE_PATH,
    *,
    output_root: Path = OUTPUT_ROOT,
    latest_path: Path = LATEST_PATH,
) -> dict:
    packet = build_review(_read_json(market_path), _read_json(stage_path))
    validate_output(packet)
    target = output_root / packet["operational_date_kst"] / packet["source_market_packet_sha256"] / "packet.json"
    outcome = "populated"
    if target.exists():
        existing = _read_json(target)
        validate_output(existing)
        if existing != packet:
            raise UsSymbolMarketReviewError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{target}")
        outcome = "verified_existing"
    else:
        _atomic_write(target, packet)
    _atomic_write(latest_path, packet)
    return {"outcome": outcome, "path": str(target), "latest_path": str(latest_path), "packet_sha256": packet["packet_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-packet", type=Path, default=MARKET_PATH)
    parser.add_argument("--stage-history", type=Path, default=STAGE_PATH)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--latest-path", type=Path, default=LATEST_PATH)
    args = parser.parse_args()
    result = populate(args.market_packet, args.stage_history, output_root=args.output_root, latest_path=args.latest_path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
