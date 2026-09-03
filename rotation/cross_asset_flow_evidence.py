#!/usr/bin/env python3
"""P2-COM-01 policy-neutral cross-asset flow evidence contract.

The adapter classifies existing Daily Orchestrator read models into the four
CIO-defined evidence classes.  It preserves raw observations and lineage, but
does not normalize incomparable units, compare different dates, infer a flow
direction, rank markets, or create an investment action.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/cross_asset_flow_evidence_contract.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class CrossAssetFlowEvidenceError(ValueError):
    """Fail-closed cross-asset flow evidence contract violation."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: object, context: str) -> dt.datetime:
    if not isinstance(value, str):
        raise CrossAssetFlowEvidenceError(f"TIME_INVALID:{context}")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CrossAssetFlowEvidenceError(f"TIME_INVALID:{context}") from exc
    if parsed.tzinfo is None:
        raise CrossAssetFlowEvidenceError(f"TIME_NOT_AWARE:{context}")
    return parsed.astimezone(dt.timezone.utc)


def _date(value: object, context: str) -> dt.date:
    if not isinstance(value, str):
        raise CrossAssetFlowEvidenceError(f"DATE_INVALID:{context}")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CrossAssetFlowEvidenceError(f"DATE_INVALID:{context}") from exc
    if parsed.isoformat() != value:
        raise CrossAssetFlowEvidenceError(f"DATE_INVALID:{context}")
    return parsed


def _validate_contract(contract: object) -> dict:
    if not isinstance(contract, dict):
        raise CrossAssetFlowEvidenceError("CONTRACT_NOT_OBJECT")
    required = {
        "schema_version", "contract_version", "source_contract_version",
        "source_output_schema_version", "output_schema_version",
        "evidence_classes", "evidence_grades", "evidence_status_values", "freshness_values",
        "invalidation_values", "source_bindings", "explicit_unknowns",
        "policy_status", "authority",
    }
    if set(contract) != required:
        raise CrossAssetFlowEvidenceError("CONTRACT_FIELDS_MISMATCH")
    if contract.get("schema_version") != 1 or contract.get("contract_version") != "cross_asset_flow_evidence/1":
        raise CrossAssetFlowEvidenceError("CONTRACT_VERSION_MISMATCH")
    if contract.get("source_contract_version") != "daily_orchestrator/6":
        raise CrossAssetFlowEvidenceError("SOURCE_CONTRACT_VERSION_MISMATCH")
    if contract.get("source_output_schema_version") != "daily_briefing_packet/1":
        raise CrossAssetFlowEvidenceError("SOURCE_OUTPUT_SCHEMA_MISMATCH")
    if contract.get("output_schema_version") != "cross_asset_flow_evidence_packet/1":
        raise CrossAssetFlowEvidenceError("OUTPUT_SCHEMA_MISMATCH")
    if contract.get("evidence_classes") != [
        "DIRECT_FLOW", "MARKET_IMPLIED_FLOW", "MACRO_CONTEXT", "UNKNOWN"
    ]:
        raise CrossAssetFlowEvidenceError("EVIDENCE_CLASSES_MISMATCH")
    if contract.get("evidence_grades") != [
        "PIPELINE_VALIDATED", "OBSERVED_UNCONFIRMED", "UNKNOWN"
    ]:
        raise CrossAssetFlowEvidenceError("EVIDENCE_GRADES_MISMATCH")
    if contract.get("evidence_status_values") != ["AVAILABLE", "OBSERVED_UNCONFIRMED", "UNKNOWN"]:
        raise CrossAssetFlowEvidenceError("EVIDENCE_STATUS_VALUES_MISMATCH")
    bindings = contract.get("source_bindings")
    expected_bindings = {
        "STABLECOIN_NET_ISSUANCE": {
            "evidence_class": "DIRECT_FLOW", "market": "CRYPTO",
            "subject": "USD_PEG_STABLECOIN_SUPPLY",
        },
        "KRX_POST_CLOSE": {
            "evidence_class": "DIRECT_FLOW", "market": "KOREA",
            "subject": "KRX_WATCHLIST_INVESTOR_NET_DEMAND",
        },
        "FREE_MARKET_DATA": {
            "evidence_class": "MACRO_CONTEXT", "market": "US", "subject": "VIXCLS",
        },
    }
    if bindings != expected_bindings:
        raise CrossAssetFlowEvidenceError("SOURCE_BINDINGS_MISMATCH")
    for component_id, binding in bindings.items():
        if not isinstance(binding, dict) or set(binding) != {"evidence_class", "market", "subject"}:
            raise CrossAssetFlowEvidenceError(f"SOURCE_BINDING_INVALID:{component_id}")
        if binding["evidence_class"] not in contract["evidence_classes"]:
            raise CrossAssetFlowEvidenceError(f"SOURCE_BINDING_CLASS_INVALID:{component_id}")
    if contract.get("explicit_unknowns") != [{
        "evidence_class": "MARKET_IMPLIED_FLOW",
        "market": "COMMON",
        "subject": "CROSS_MARKET_RELATIVE_FLOW",
        "reason": "COMPARABLE_MULTI_DATE_MARKET_SERIES_NOT_AVAILABLE",
    }]:
        raise CrossAssetFlowEvidenceError("EXPLICIT_UNKNOWNS_MISMATCH")
    if contract.get("policy_status") != {
        "freshness_windows": "UNRATIFIED",
        "lag_contract": "UNRATIFIED",
        "normalization": "UNRATIFIED",
        "cross_market_comparison": "UNRATIFIED",
        "direction_interpretation": "UNRATIFIED",
        "evidence_weighting": "UNRATIFIED",
    }:
        raise CrossAssetFlowEvidenceError("POLICY_STATUS_MISMATCH")
    authority = contract.get("authority")
    if not isinstance(authority, dict) or authority.get("raw_evidence_presentation_only") is not True:
        raise CrossAssetFlowEvidenceError("CONTRACT_AUTHORITY_INVALID")
    if any(value is not False for key, value in authority.items() if key != "raw_evidence_presentation_only"):
        raise CrossAssetFlowEvidenceError("CONTRACT_AUTHORITY_EXPANDED")
    return copy.deepcopy(contract)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossAssetFlowEvidenceError("CONTRACT_READ_FAILED") from exc
    return _validate_contract(value)


def _validate_dynamic_clock_frozen_source(packet: dict, decision_date: dt.date) -> None:
    """Bind a present P8-12 source while keeping legacy packets readable."""
    frozen_sources = packet.get("frozen_sources")
    if frozen_sources is None:
        return
    if type(frozen_sources) is not dict:  # noqa: E721 - exact JSON boundary
        raise CrossAssetFlowEvidenceError(
            "SOURCE_DYNAMIC_CLOCK_INVALID:frozen_sources_type"
        )
    if "DYNAMIC_CLOCK" not in frozen_sources:
        return
    source = frozen_sources["DYNAMIC_CLOCK"]
    if type(source) is not dict:  # noqa: E721 - exact JSON boundary
        raise CrossAssetFlowEvidenceError("SOURCE_DYNAMIC_CLOCK_INVALID:source_type")
    kind = source.get("kind")
    if type(kind) is not str:  # noqa: E721 - reject bool/string aliases
        raise CrossAssetFlowEvidenceError("SOURCE_DYNAMIC_CLOCK_INVALID:kind_type")
    if kind == "unavailable":
        if set(source) != {"kind"}:
            raise CrossAssetFlowEvidenceError(
                "SOURCE_DYNAMIC_CLOCK_INVALID:unavailable_shape"
            )
        return
    if kind == "error":
        if (
            set(source) != {"kind", "value"}
            or type(source.get("value")) is not str
        ):
            raise CrossAssetFlowEvidenceError(
                "SOURCE_DYNAMIC_CLOCK_INVALID:error_shape"
            )
        return
    if kind != "report" or set(source) != {"kind", "report_sha256", "report"}:
        raise CrossAssetFlowEvidenceError("SOURCE_DYNAMIC_CLOCK_INVALID:report_shape")
    report = source.get("report")
    report_sha256 = source.get("report_sha256")
    if type(report) is not dict or type(report_sha256) is not str:
        raise CrossAssetFlowEvidenceError(
            "SOURCE_DYNAMIC_CLOCK_INVALID:report_hash_type"
        )
    if SHA256_RE.fullmatch(report_sha256) is None:
        raise CrossAssetFlowEvidenceError(
            "SOURCE_DYNAMIC_CLOCK_INVALID:report_sha256"
        )
    if payload_sha256(report) != report_sha256:
        raise CrossAssetFlowEvidenceError("SOURCE_DYNAMIC_CLOCK_SHA_MISMATCH")
    if report.get("decision_date") != decision_date.isoformat():
        raise CrossAssetFlowEvidenceError("SOURCE_DYNAMIC_CLOCK_DATE_MISMATCH")


def _verify_daily_packet(packet: object, contract: dict) -> tuple[dict[str, dict], dt.date, dt.datetime]:
    if not isinstance(packet, dict):
        raise CrossAssetFlowEvidenceError("SOURCE_PACKET_NOT_OBJECT")
    if packet.get("contract_version") != contract["source_contract_version"]:
        raise CrossAssetFlowEvidenceError("SOURCE_PACKET_CONTRACT_MISMATCH")
    if packet.get("output_schema_version") != contract["source_output_schema_version"]:
        raise CrossAssetFlowEvidenceError("SOURCE_PACKET_SCHEMA_MISMATCH")
    decision_date = _date(packet.get("decision_date"), "decision_date")
    generated_at = _utc(packet.get("generated_at"), "generated_at")
    unsigned = copy.deepcopy(packet)
    digest = unsigned.pop("packet_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise CrossAssetFlowEvidenceError("SOURCE_PACKET_SHA_MISMATCH")
    _validate_dynamic_clock_frozen_source(packet, decision_date)
    components = packet.get("components")
    if not isinstance(components, list) or not all(isinstance(row, dict) for row in components):
        raise CrossAssetFlowEvidenceError("SOURCE_COMPONENTS_INVALID")
    ids = [row.get("component_id") for row in components]
    if not all(isinstance(value, str) and value for value in ids) or len(ids) != len(set(ids)):
        raise CrossAssetFlowEvidenceError("SOURCE_COMPONENT_IDS_INVALID")
    for row in components:
        if any(row.get(key) is not False for key in ("decision_eligible", "action_eligible", "order_eligible")):
            raise CrossAssetFlowEvidenceError("SOURCE_COMPONENT_AUTHORITY_INVALID")
        as_of = row.get("as_of_date")
        if as_of is not None and _date(as_of, f"{row['component_id']}.as_of_date") > decision_date:
            if not (
                row.get("status") == "DATA_BLOCKED"
                and row.get("reason") == "AS_OF_DATE_AFTER_DECISION_DATE"
            ):
                raise CrossAssetFlowEvidenceError("SOURCE_COMPONENT_FROM_FUTURE")
        available_at = row.get("available_at")
        if available_at is not None and _utc(available_at, f"{row['component_id']}.available_at") > generated_at:
            raise CrossAssetFlowEvidenceError("SOURCE_COMPONENT_AVAILABLE_AFTER_DECISION")
    authority = packet.get("authority")
    if not isinstance(authority, dict):
        raise CrossAssetFlowEvidenceError("SOURCE_AUTHORITY_INVALID")
    for key in (
        "action_generation_authorized", "order_generation_authorized",
        "production_authorized", "trading_authorized",
    ):
        if authority.get(key) is not False:
            raise CrossAssetFlowEvidenceError("SOURCE_AUTHORITY_EXPANDED")
    return dict(zip(ids, components)), decision_date, generated_at


def _base_row(component: dict | None, binding: dict, evidence_id: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_class": binding["evidence_class"],
        "market": binding["market"],
        "subject": binding["subject"],
        "observation_at": None,
        "available_at": None,
        "time_precision": "UNKNOWN",
        "status": "UNKNOWN",
        "evidence_grade": "UNKNOWN",
        "freshness_status": "UNKNOWN",
        "invalidation": {
            "status": "SOURCE_COMPONENT_NOT_READY",
            "reason": "SOURCE_COMPONENT_MISSING" if component is None else str(component.get("reason") or component.get("status")),
        },
        "values": None,
        "source": None if component is None else {
            "component_id": component["component_id"],
            "component_sha256": payload_sha256(component),
            "source_packet_path": component.get("source_packet_path"),
            "source_packet_sha256": component.get("source_packet_sha256"),
            "upstream_validated": component.get("validated") is True,
        },
        "decision_eligible": False,
        "action_eligible": False,
        "order_eligible": False,
    }


def _stablecoin(component: dict | None, binding: dict, decision_at: dt.datetime) -> list[dict]:
    row = _base_row(component, binding, "DIRECT_FLOW:CRYPTO:STABLECOIN_SUPPLY")
    if component is None or component.get("status") != "READY" or component.get("validated") is not True:
        return [row]
    packet = component.get("packet")
    if not isinstance(packet, dict) or packet.get("daily_status") != "AVAILABLE" or packet.get("weekly_status") != "AVAILABLE":
        row["invalidation"] = {"status": "SOURCE_EVIDENCE_INVALID", "reason": "STABLECOIN_VALUES_NOT_AVAILABLE"}
        return [row]
    observation_date = _date(packet.get("observation_date"), "stablecoin.observation_date")
    if observation_date.isoformat() != component.get("as_of_date"):
        raise CrossAssetFlowEvidenceError("STABLECOIN_OBSERVATION_DATE_MISMATCH")
    available_at = component.get("available_at") or component.get("generated_at")
    if _utc(available_at, "stablecoin.available_at") > decision_at:
        raise CrossAssetFlowEvidenceError("STABLECOIN_AVAILABLE_AFTER_DECISION")
    row.update({
        "observation_at": observation_date.isoformat(),
        "available_at": available_at,
        "time_precision": "DATE_ONLY",
        "status": "AVAILABLE",
        "evidence_grade": "PIPELINE_VALIDATED",
        "freshness_status": "NOT_COMPUTABLE_WINDOW_UNRATIFIED",
        "invalidation": {"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED", "reason": "NO_RATIFIED_INVALIDATION_RULE"},
        "values": {
            "daily_net_issuance_native_usd_peg": packet.get("daily_net_issuance_native_usd_peg"),
            "weekly_net_issuance_native_usd_peg": packet.get("weekly_net_issuance_native_usd_peg"),
            "unit": "USD_NATIVE_PEG_NOT_NORMALIZED_ACROSS_MARKETS",
        },
    })
    return [row]


def _krx(component: dict | None, binding: dict, decision_at: dt.datetime) -> list[dict]:
    placeholder = _base_row(component, binding, "DIRECT_FLOW:KOREA:KRX_WATCHLIST")
    if component is None or component.get("status") != "READY" or component.get("validated") is not True:
        return [placeholder]
    packet = component.get("packet")
    symbols = packet.get("symbols") if isinstance(packet, dict) else None
    if not isinstance(symbols, list) or not symbols:
        placeholder["invalidation"] = {"status": "SOURCE_EVIDENCE_INVALID", "reason": "KRX_SYMBOL_ROWS_MISSING"}
        return [placeholder]
    result = []
    for item in symbols:
        observed = item.get("observed_row") if isinstance(item, dict) else None
        if not isinstance(observed, dict):
            raise CrossAssetFlowEvidenceError("KRX_OBSERVED_ROW_INVALID")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise CrossAssetFlowEvidenceError("KRX_SYMBOL_INVALID")
        observed_at = observed.get("observed_at_kst")
        observed_utc = _utc(observed_at, f"krx.{symbol}.observed_at")
        if observed_utc > decision_at:
            raise CrossAssetFlowEvidenceError("KRX_OBSERVED_AFTER_DECISION")
        if observed.get("trading_day") != component.get("as_of_date"):
            raise CrossAssetFlowEvidenceError("KRX_TRADING_DAY_MISMATCH")
        parsed_observed = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if parsed_observed.date().isoformat() != observed["trading_day"]:
            raise CrossAssetFlowEvidenceError("KRX_OBSERVED_DATE_MISMATCH")
        row = _base_row(component, binding, f"DIRECT_FLOW:KOREA:{symbol}")
        row.update({
            "subject": f"KRX:{symbol}",
            "observation_at": observed_at,
            "available_at": observed_at,
            "time_precision": "TIMESTAMP",
            "status": "OBSERVED_UNCONFIRMED",
            "evidence_grade": "OBSERVED_UNCONFIRMED",
            "freshness_status": "NOT_COMPUTABLE_WINDOW_UNRATIFIED",
            "invalidation": {
                "status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
                "reason": "SAME_DAY_OBSERVATION_NOT_NEXT_DAY_CONFIRMED",
            },
            "values": {
                "net_value": copy.deepcopy(observed.get("net_value")),
                "net_volume": copy.deepcopy(observed.get("net_volume")),
                "unit_scope": "RAW_KRX_PARTICIPANT_BUCKETS_NOT_CROSS_MARKET_NORMALIZED",
            },
        })
        if not isinstance(row["values"]["net_value"], dict) or not isinstance(row["values"]["net_volume"], dict):
            raise CrossAssetFlowEvidenceError("KRX_NET_FLOW_INVALID")
        result.append(row)
    return result


def _vix(component: dict | None, binding: dict, decision_at: dt.datetime) -> list[dict]:
    row = _base_row(component, binding, "MACRO_CONTEXT:US:VIXCLS")
    if component is None or component.get("status") != "READY" or component.get("validated") is not True:
        return [row]
    packet = component.get("packet")
    vix = packet.get("vixcls") if isinstance(packet, dict) else None
    if not isinstance(vix, dict) or not isinstance(vix.get("value"), str):
        row["invalidation"] = {"status": "SOURCE_EVIDENCE_INVALID", "reason": "VIXCLS_MISSING"}
        return [row]
    observation_date = _date(vix.get("date"), "vix.date")
    if observation_date.isoformat() != component.get("as_of_date"):
        raise CrossAssetFlowEvidenceError("VIX_OBSERVATION_DATE_MISMATCH")
    available_at = component.get("available_at") or component.get("generated_at")
    if _utc(available_at, "vix.available_at") > decision_at:
        raise CrossAssetFlowEvidenceError("VIX_AVAILABLE_AFTER_DECISION")
    row.update({
        "observation_at": observation_date.isoformat(),
        "available_at": available_at,
        "time_precision": "DATE_ONLY",
        "status": "AVAILABLE",
        "evidence_grade": "PIPELINE_VALIDATED",
        "freshness_status": "NOT_COMPUTABLE_WINDOW_UNRATIFIED",
        "invalidation": {"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED", "reason": "NO_RATIFIED_INVALIDATION_RULE"},
        "values": {"vixcls": vix["value"], "unit": "INDEX_LEVEL_CONTEXT_ONLY"},
    })
    return [row]


def _explicit_unknown(spec: dict) -> dict:
    binding = {key: spec[key] for key in ("evidence_class", "market", "subject")}
    row = _base_row(None, binding, f"{spec['evidence_class']}:{spec['market']}:{spec['subject']}")
    row["invalidation"] = {"status": "UNKNOWN", "reason": spec["reason"]}
    return row


def build_packet(daily_packet: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    by_id, decision_date, generated_at = _verify_daily_packet(daily_packet, contract)
    bindings = contract["source_bindings"]
    rows = []
    rows.extend(_stablecoin(
        by_id.get("STABLECOIN_NET_ISSUANCE"), bindings["STABLECOIN_NET_ISSUANCE"], generated_at
    ))
    rows.extend(_krx(by_id.get("KRX_POST_CLOSE"), bindings["KRX_POST_CLOSE"], generated_at))
    rows.extend(_vix(by_id.get("FREE_MARKET_DATA"), bindings["FREE_MARKET_DATA"], generated_at))
    rows.extend(_explicit_unknown(spec) for spec in contract["explicit_unknowns"])
    if len({row["evidence_id"] for row in rows}) != len(rows):
        raise CrossAssetFlowEvidenceError("OUTPUT_EVIDENCE_ID_DUPLICATE")
    observation_dates = sorted({str(row["observation_at"])[:10] for row in rows if row["observation_at"] is not None})
    if len(observation_dates) > 1:
        comparison_reason = "SOURCE_AS_OF_MISMATCH_NO_LAG_AUTHORITY"
    else:
        comparison_reason = "CROSS_MARKET_COMPARISON_POLICY_UNRATIFIED"
    packet = {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "output_schema_version": contract["output_schema_version"],
        "decision_date": decision_date.isoformat(),
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "source_daily_packet_sha256": daily_packet["packet_sha256"],
        "evidence_rows": rows,
        "evidence_class_counts": {
            evidence_class: sum(row["evidence_class"] == evidence_class for row in rows)
            for evidence_class in contract["evidence_classes"]
        },
        "cross_market_assessment": {
            "status": "UNKNOWN",
            "reason": comparison_reason,
            "flow_direction": None,
            "from_market": None,
            "to_market": None,
            "comparison_observation_dates": observation_dates,
        },
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, daily_packet: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    if not isinstance(packet, dict):
        raise CrossAssetFlowEvidenceError("OUTPUT_NOT_OBJECT")
    unsigned = copy.deepcopy(packet)
    digest = unsigned.pop("packet_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise CrossAssetFlowEvidenceError("OUTPUT_SHA_MISMATCH")
    if packet != build_packet(daily_packet, contract):
        raise CrossAssetFlowEvidenceError("OUTPUT_DERIVATION_MISMATCH")
    return packet
