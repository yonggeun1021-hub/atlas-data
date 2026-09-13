#!/usr/bin/env python3
"""Connect official Korea market observations to staged-symbol reviews."""
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


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "korea_symbol_market_review_contract.json"
MARKET_PATH = ROOT / "data" / "latest_korea_market_signals.json"
STAGE_PATH = ROOT / "data" / "stage_history.json"
BRIEFING_ROOT = ROOT / "data" / "briefing" / "krx"
OUTPUT_ROOT = ROOT / "evidence" / "korea_symbol_market_review"
LATEST_PATH = ROOT / "data" / "latest_korea_symbol_market_review.json"
OUTPUT_SCHEMA_VERSION = "korea_symbol_market_review_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class KoreaSymbolMarketReviewError(ValueError):
    """Fail-closed Korea review bridge violation."""


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KoreaSymbolMarketReviewError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _require_all_false(authority: object, code: str = "AUTHORITY_INVALID") -> None:
    if not isinstance(authority, dict) or not authority or any(value is not False for value in authority.values()):
        raise KoreaSymbolMarketReviewError(code)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if value.get("contract_version") != "korea_symbol_market_review/1":
        raise KoreaSymbolMarketReviewError("CONTRACT_VERSION_INVALID")
    if value.get("output_schema_version") != OUTPUT_SCHEMA_VERSION:
        raise KoreaSymbolMarketReviewError("OUTPUT_SCHEMA_VERSION_INVALID")
    if value.get("required_axes") != ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]:
        raise KoreaSymbolMarketReviewError("REQUIRED_AXES_INVALID")
    if value.get("supported_pipeline_subjects") != ["012450", "298040", "329180"]:
        raise KoreaSymbolMarketReviewError("SUPPORTED_SUBJECTS_INVALID")
    _require_all_false(value.get("authority"), "CONTRACT_AUTHORITY_INVALID")
    return value


def _validate_market_packet(packet: dict) -> None:
    if packet.get("schema_version") != "korea_market_signals_observation/1" or packet.get("contract_version") != "korea_market_signals/1":
        raise KoreaSymbolMarketReviewError("MARKET_CONTRACT_INVALID")
    if packet.get("status") != "OBSERVED_UNCLASSIFIED" or packet.get("market") != "KOREA":
        raise KoreaSymbolMarketReviewError("MARKET_STATUS_INVALID")
    claimed = packet.get("payload_sha256")
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise KoreaSymbolMarketReviewError("MARKET_PACKET_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        raise KoreaSymbolMarketReviewError("MARKET_PACKET_SHA256_MISMATCH")
    authority = packet.get("authority")
    if not isinstance(authority, dict) or authority.get("observation_only") is not True:
        raise KoreaSymbolMarketReviewError("MARKET_OBSERVATION_AUTHORITY_INVALID")
    if any(value is not False for key, value in authority.items() if key.endswith("_authorized")):
        raise KoreaSymbolMarketReviewError("MARKET_DECISION_AUTHORITY_OPEN")


def _decimal(value: object, code: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise KoreaSymbolMarketReviewError(code) from exc
    if not parsed.is_finite():
        raise KoreaSymbolMarketReviewError(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _pct(numerator: Decimal, denominator: Decimal) -> str:
    if denominator == 0:
        raise KoreaSymbolMarketReviewError("PERCENT_DENOMINATOR_ZERO")
    return _decimal_text((((numerator / denominator) - Decimal("1")) * Decimal("100")).quantize(Decimal("0.0001")))


def _compact_source(market: dict, stages: dict, contract: dict, briefing_root: Path) -> dict:
    stage_dates = sorted(stages)
    if not stage_dates:
        raise KoreaSymbolMarketReviewError("STAGE_HISTORY_EMPTY")
    stage_as_of = stage_dates[-1]
    latest_stage = stages[stage_as_of]
    if not isinstance(latest_stage, dict):
        raise KoreaSymbolMarketReviewError("LATEST_STAGE_INVALID")
    subjects = {}
    for symbol in contract["supported_pipeline_subjects"]:
        stage_row = latest_stage.get(symbol)
        if not isinstance(stage_row, dict) or not isinstance(stage_row.get("name"), str) or not isinstance(stage_row.get("stage"), str):
            raise KoreaSymbolMarketReviewError(f"PIPELINE_SUBJECT_MISSING:{symbol}")
        observed = _read_json(briefing_root / f"{symbol}.json")
        if observed.get("symbol") != symbol or observed.get("name") != stage_row["name"]:
            raise KoreaSymbolMarketReviewError(f"SUBJECT_IDENTITY_MISMATCH:{symbol}")
        if observed.get("atlas_stage") != stage_row["stage"] or observed.get("status") != "ok":
            raise KoreaSymbolMarketReviewError(f"SUBJECT_STAGE_OR_STATUS_INVALID:{symbol}")
        if observed.get("latest_confirmed_row", {}).get("confirmed") is not True:
            raise KoreaSymbolMarketReviewError(f"SUBJECT_PRICE_NOT_CONFIRMED:{symbol}")
        source_sha = observed.get("source", {}).get("source_sha256")
        if not isinstance(source_sha, str) or SHA256_RE.fullmatch(source_sha) is None:
            raise KoreaSymbolMarketReviewError(f"SUBJECT_SOURCE_SHA256_INVALID:{symbol}")
        subjects[symbol] = copy.deepcopy(observed)
    return {
        "contract": copy.deepcopy(contract),
        "market_observation": copy.deepcopy(market),
        "stage_snapshot": {"as_of": stage_as_of, "subjects": subjects},
        "stage_history_sha256": payload_sha256(stages),
    }


def _five_axis(source: dict, contract: dict) -> dict:
    market = source["market_observation"]
    axes = market.get("axes")
    if not isinstance(axes, dict):
        raise KoreaSymbolMarketReviewError("MARKET_AXES_INVALID")
    observed = [axis for axis in contract["required_axes"] if axes.get(axis, {}).get("status") == "OBSERVED"]
    if observed != contract["required_axes"] or market.get("coverage", {}).get("ratio") != "5/5":
        raise KoreaSymbolMarketReviewError("MARKET_FIVE_AXIS_INCOMPLETE")
    return {
        "ratio": "5/5",
        "required_count": 5,
        "observed_count": 5,
        "observed_axes": observed,
        "aggregate_regime": "UNKNOWN",
        "final_policy": "PENDING_POLICY_RATIFICATION",
        "market_facts": {
            "kospi_one_session_return_pct": axes["TREND"]["measurement"]["benchmarks"]["KOSPI"]["one_session_return_pct"],
            "kosdaq_one_session_return_pct": axes["TREND"]["measurement"]["benchmarks"]["KOSDAQ"]["one_session_return_pct"],
            "advancing_count": axes["BREADTH"]["measurement"]["combined"]["advancing_count"],
            "declining_count": axes["BREADTH"]["measurement"]["combined"]["declining_count"],
            "mean_absolute_move_pct": axes["RISK_VOL"]["measurement"]["combined_mean_absolute_stock_move_pct"],
            "trading_value_change_pct": axes["LIQUIDITY"]["measurement"]["combined"]["trading_value_change_pct"],
            "strongest_observed_sectors": copy.deepcopy(axes["LEADERSHIP"]["measurement"]["largest_relative_returns"][:3]),
        },
    }


def _symbol_row(
    symbol: str,
    observed: dict,
    stage_as_of: str,
    contract: dict,
    *,
    missing_policy: dict | None = None,
) -> dict:
    """Build one staged-symbol review row from its observed watchlist record.

    With ``missing_policy=None`` this is the original fail-closed path used by
    the bounded review: every input (confirmed close, SMA20, investor flows)
    must be present or the row raises.  A population caller may pass a
    ``missing_policy`` (``{"missing_input_state": "BLOCKED", ...}``) to get an
    explicit row instead of an exception when SMA20, flows, or the pipeline
    stage are absent; missing values are reported as ``None`` with reason
    codes and are never estimated.
    """
    latest = observed["latest_confirmed_row"]
    metrics = observed.get("confirmed_metrics", {}) or {}
    if missing_policy is None:
        close = _decimal(latest.get("close"), "PRICE_CLOSE_INVALID")
        sma20 = _decimal(metrics.get("sma20"), "SMA20_INVALID")
        foreign = _decimal(latest.get("net_value", {}).get("외국인합계"), "FOREIGN_FLOW_INVALID")
        institution = _decimal(latest.get("net_value", {}).get("기관합계"), "INSTITUTION_FLOW_INVALID")
        individual = _decimal(latest.get("net_value", {}).get("개인"), "INDIVIDUAL_FLOW_INVALID")
        change_pct = _decimal(latest.get("change_pct"), "CHANGE_PCT_INVALID")
        volume = _decimal(latest.get("volume"), "VOLUME_INVALID")
        stage = observed["atlas_stage"]
    else:
        if latest.get("close") is None:
            return _missing_price_row(symbol, observed, stage_as_of, contract, missing_policy)
        close = _decimal(latest.get("close"), "PRICE_CLOSE_INVALID")
        sma20 = _decimal(metrics.get("sma20"), "SMA20_INVALID") if metrics.get("sma20") is not None else None
        net_value = latest.get("net_value") or {}
        flows_present = all(net_value.get(key) is not None for key in ("외국인합계", "기관합계", "개인"))
        foreign = _decimal(net_value.get("외국인합계"), "FOREIGN_FLOW_INVALID") if flows_present else None
        institution = _decimal(net_value.get("기관합계"), "INSTITUTION_FLOW_INVALID") if flows_present else None
        individual = _decimal(net_value.get("개인"), "INDIVIDUAL_FLOW_INVALID") if flows_present else None
        change_pct = _decimal(latest.get("change_pct"), "CHANGE_PCT_INVALID") if latest.get("change_pct") is not None else None
        volume = _decimal(latest.get("volume"), "VOLUME_INVALID") if latest.get("volume") is not None else None
        stage = observed.get("atlas_stage")

    facts = ["KOREA_FIVE_MARKET_AXES_CONNECTED"]
    if sma20 is not None:
        facts.append("PRICE_ABOVE_20_DAY_AVERAGE" if close >= sma20 else "PRICE_BELOW_20_DAY_AVERAGE")
    if foreign is not None and institution is not None:
        if foreign > 0 and institution > 0:
            facts.append("FOREIGN_AND_INSTITUTION_NET_BUY")
        elif foreign > 0:
            facts.append("FOREIGN_NET_BUY_INSTITUTION_NET_SELL")
        elif institution > 0:
            facts.append("INSTITUTION_NET_BUY_FOREIGN_NET_SELL")
        else:
            facts.append("FOREIGN_AND_INSTITUTION_NET_SELL")

    complete = sma20 is not None and foreign is not None
    if complete:
        entry_state = contract["entry_policy"]["observed_price_state"]
        entry_reasons = ["KOREA_FIVE_MARKET_AXES_CONNECTED", "CONFIRMED_PRICE_SMA20_AND_INVESTOR_FLOW_CONNECTED", "FINAL_KOREA_REGIME_POLICY_PENDING"]
    else:
        entry_state = missing_policy["missing_input_state"]
        entry_reasons = ["KOREA_FIVE_MARKET_AXES_CONNECTED", "CONFIRMED_PRICE_CONNECTED"]
        if sma20 is None:
            entry_reasons.append("SMA20_NOT_COMPUTABLE:" + str(metrics.get("reason") or "SMA20_MISSING"))
        if foreign is None:
            entry_reasons.append("INVESTOR_FLOW_NOT_AVAILABLE")
        entry_reasons.append("FINAL_KOREA_REGIME_POLICY_PENDING")
    entry_reasons.append("PIPELINE_STAGE_IS_NOT_BUY_AUTHORITY" if stage is not None else "PIPELINE_STAGE_NOT_ASSIGNED")

    price_context = {
        "status": "OBSERVED_CONFIRMED" if sma20 is not None else "OBSERVED_CONFIRMED_NO_SMA20",
        "as_of_session_date": observed["latest_confirmed_day"],
        "close_krw": int(close),
        "one_session_return_pct": _decimal_text(change_pct.quantize(Decimal("0.0001"))) if change_pct is not None else None,
        "sma20_krw": _decimal_text(sma20) if sma20 is not None else None,
        "distance_from_sma20_pct": _pct(close, sma20) if sma20 is not None else None,
        "volume": int(volume) if volume is not None else None,
    }
    flow_context = (
        {
            "foreign_net_value_krw": int(foreign),
            "institution_net_value_krw": int(institution),
            "individual_net_value_krw": int(individual),
        }
        if foreign is not None else None
    )
    return {
        "symbol": symbol,
        "name": observed["name"],
        "pipeline_stage": stage,
        "pipeline_as_of": stage_as_of,
        "price_context": price_context,
        "flow_context": flow_context,
        "observed_facts": facts,
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
    }


def _missing_price_row(symbol: str, observed: dict, stage_as_of: str, contract: dict, missing_policy: dict) -> dict:
    stage = observed.get("atlas_stage")
    return {
        "symbol": symbol,
        "name": observed["name"],
        "pipeline_stage": stage,
        "pipeline_as_of": stage_as_of,
        "price_context": {
            "status": "UNAVAILABLE",
            "as_of_session_date": observed.get("latest_confirmed_day"),
            "close_krw": None,
            "one_session_return_pct": None,
            "sma20_krw": None,
            "distance_from_sma20_pct": None,
            "volume": None,
        },
        "flow_context": None,
        "observed_facts": ["KOREA_FIVE_MARKET_AXES_CONNECTED"],
        "entry_review": {
            "state": missing_policy["missing_price_state"],
            "reasons": [
                "PIPELINE_SYMBOL_PRICE_UNAVAILABLE",
                "FINAL_KOREA_REGIME_POLICY_PENDING",
                "PIPELINE_STAGE_IS_NOT_BUY_AUTHORITY" if stage is not None else "PIPELINE_STAGE_NOT_ASSIGNED",
            ],
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
    }


def _symbol_reviews(source: dict, contract: dict) -> list[dict]:
    return [
        _symbol_row(symbol, source["stage_snapshot"]["subjects"][symbol], source["stage_snapshot"]["as_of"], contract)
        for symbol in contract["supported_pipeline_subjects"]
    ]


def _build_from_source(source: dict) -> dict:
    contract = source.get("contract")
    if contract != load_contract():
        raise KoreaSymbolMarketReviewError("EMBEDDED_CONTRACT_MISMATCH")
    coverage = _five_axis(source, contract)
    symbols = _symbol_reviews(source, contract)
    market = source["market_observation"]
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "generated_at": market["available_at"],
        "operational_date_kst": market["as_of_date"],
        "source_market_packet_sha256": market["payload_sha256"],
        "source_stage_history_sha256": source["stage_history_sha256"],
        "five_axis": coverage,
        "symbols": symbols,
        "summary": {"symbol_count": len(symbols), "price_connected_count": len(symbols), "flow_connected_count": len(symbols), "entry_wait_count": len(symbols), "automatic_entry_count": 0, "automatic_exit_count": 0},
        "authority": copy.deepcopy(contract["authority"]),
        "source": copy.deepcopy(source),
    }
    _require_all_false(packet["authority"])
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_review(market: dict, stages: dict, *, contract: dict | None = None, briefing_root: Path = BRIEFING_ROOT) -> dict:
    contract = load_contract() if contract is None else contract
    _require_all_false(contract.get("authority"))
    _validate_market_packet(market)
    return _build_from_source(_compact_source(market, stages, contract, briefing_root))


def validate_output(packet: dict) -> dict:
    expected = {"schema_version", "contract_version", "mode", "generated_at", "operational_date_kst", "source_market_packet_sha256", "source_stage_history_sha256", "five_axis", "symbols", "summary", "authority", "source", "packet_sha256"}
    if not isinstance(packet, dict) or set(packet) != expected:
        raise KoreaSymbolMarketReviewError("OUTPUT_SCHEMA_MISMATCH")
    claimed = packet.get("packet_sha256")
    if packet.get("schema_version") != OUTPUT_SCHEMA_VERSION or not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise KoreaSymbolMarketReviewError("PACKET_HEADER_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise KoreaSymbolMarketReviewError("PACKET_SHA256_MISMATCH")
    _require_all_false(packet.get("authority"))
    if _build_from_source(packet.get("source")) != packet:
        raise KoreaSymbolMarketReviewError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def _atomic_write(path: Path, packet: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.replace(temp, path)


def populate(market_path: Path = MARKET_PATH, stage_path: Path = STAGE_PATH, *, briefing_root: Path = BRIEFING_ROOT, output_root: Path = OUTPUT_ROOT, latest_path: Path = LATEST_PATH) -> dict:
    packet = build_review(_read_json(market_path), _read_json(stage_path), briefing_root=briefing_root)
    validate_output(packet)
    target = output_root / packet["operational_date_kst"] / packet["source_market_packet_sha256"] / "packet.json"
    outcome = "populated"
    if target.exists():
        existing = _read_json(target)
        validate_output(existing)
        if existing != packet:
            raise KoreaSymbolMarketReviewError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{target}")
        outcome = "verified_existing"
    else:
        _atomic_write(target, packet)
    _atomic_write(latest_path, packet)
    return {"outcome": outcome, "path": str(target), "latest_path": str(latest_path), "packet_sha256": packet["packet_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-packet", type=Path, default=MARKET_PATH)
    parser.add_argument("--stage-history", type=Path, default=STAGE_PATH)
    parser.add_argument("--briefing-root", type=Path, default=BRIEFING_ROOT)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--latest-path", type=Path, default=LATEST_PATH)
    args = parser.parse_args()
    print(json.dumps(populate(args.market_packet, args.stage_history, briefing_root=args.briefing_root, output_root=args.output_root, latest_path=args.latest_path), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
