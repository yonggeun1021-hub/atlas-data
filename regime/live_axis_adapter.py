#!/usr/bin/env python3
"""P8-04 policy-neutral adapter from qualified briefing evidence to axes.

The adapter proves only that a Regime axis has a qualified, point-in-time
observation.  It never interprets the observation, assigns a Regime or
direction, applies a threshold, ranks a market, or authorizes an action.

Only direct semantic bindings are supported:

* CRYPTO/TREND    <- BTC_TREND
* CRYPTO/RISK_VOL <- BTC_RISK
* CRYPTO/LIQUIDITY<- STABLECOIN_NET_ISSUANCE
The Korea seven-name post-close watchlist, the three-name IEX sample, and the
US membership roster are deliberately not promoted into market-wide axes.
The current FRED/VIX pointer also remains UNDEFINED here because its raw bytes
are intentionally transient and no independent append-only provenance
validator exists yet; a self-hashed derived pointer is not sufficient evidence
for a Regime axis.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "regime_live_axis_adapter_contract.json"
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
CONTRACT_VERSION = "regime_live_axis_adapter/v1"
MARKETS = ("US", "KR", "CRYPTO")


class LiveAxisAdapterError(RuntimeError):
    """A source cannot prove the requested policy-neutral axis."""


def fail(code: str, detail: str) -> None:
    raise LiveAxisAdapterError(f"{code}:{detail}")


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "mode": "EVIDENCE_ONLY_NO_INTERPRETATION",
        "bindings": {
            "CRYPTO/TREND": {
                "source_component": "BTC_TREND",
                "source_transform_version": "btc_trend/v1",
                "axis_transform_version": "regime_live_axis_btc_trend/v1",
            },
            "CRYPTO/RISK_VOL": {
                "source_component": "BTC_RISK",
                "source_transform_version": "btc_risk/v1",
                "axis_transform_version": "regime_live_axis_btc_risk/v1",
            },
            "CRYPTO/LIQUIDITY": {
                "source_component": "STABLECOIN_NET_ISSUANCE",
                "source_transform_version": "stablecoin_net_issuance/v1",
                "axis_transform_version": "regime_live_axis_stablecoin/v1",
            },
        },
        "deferred_axes": {
            "US/RISK_VOL": "RAW_PROVENANCE_VALIDATOR_MISSING",
            "KR/TREND": "MARKET_WIDE_SOURCE_MISSING",
            "KR/BREADTH": "MARKET_WIDE_SOURCE_MISSING",
            "KR/RISK_VOL": "MARKET_WIDE_SOURCE_MISSING",
            "KR/LIQUIDITY": "SOURCE_POLICY_UNRATIFIED",
            "KR/LEADERSHIP": "SOURCE_POLICY_UNRATIFIED",
        },
        "non_promotable_evidence": [
            "IEX_THREE_SYMBOL_SAMPLE",
            "KRX_SEVEN_SYMBOL_WATCHLIST",
            "US_MEMBERSHIP_ROSTER_WITHOUT_ADVANCE_DECLINE_VALUES",
        ],
        "authority": {
            "axis_evidence_binding_only": True,
            "regime_interpretation_authorized": False,
            "direction_authorized": False,
            "confidence_authorized": False,
            "threshold_authorized": False,
            "weight_authorized": False,
            "market_ranking_authorized": False,
            "strategy_authorized": False,
            "action_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", str(exc))
    if value != _expected_contract():
        fail("CONTRACT_INVALID", "pinned semantics")
    return copy.deepcopy(value)


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail("MODULE_LOAD_FAILED", relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BTC_TREND = _load("atlas_regime_axis_btc_trend", ".github/scripts/btc_trend.py")
BTC_RISK = _load("atlas_regime_axis_btc_risk", ".github/scripts/btc_risk.py")
STABLECOIN = _load(
    "atlas_regime_axis_stablecoin", ".github/scripts/stablecoin_net_issuance.py"
)


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail("TIMESTAMP_INVALID", label)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        fail("TIMESTAMP_INVALID", label)


def _parse_date(value: object, label: str) -> dt.date:
    if not isinstance(value, str):
        fail("DATE_INVALID", label)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail("DATE_INVALID", label)
    if parsed.isoformat() != value:
        fail("DATE_INVALID", label)
    return parsed


def _authority_false(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_authorized") and item is not False:
                return False
            if not _authority_false(item):
                return False
    elif isinstance(value, list):
        return all(_authority_false(item) for item in value)
    return True


def _row(rows: dict, component_id: str, generated_at: str) -> dict:
    row = rows.get(component_id)
    if not isinstance(row, dict) or row.get("component_id") != component_id:
        fail("COMPONENT_MISSING", component_id)
    if row.get("status") != "READY" or row.get("validated") is not True:
        fail("COMPONENT_NOT_READY", component_id)
    if any(row.get(key) is not False for key in (
        "decision_eligible", "action_eligible", "order_eligible"
    )):
        fail("COMPONENT_AUTHORITY_INVALID", component_id)
    if not _authority_false(row):
        fail("COMPONENT_AUTHORITY_INVALID", component_id)
    generated = _parse_utc(generated_at, "generated_at")
    available_text = row.get("available_at") or row.get("generated_at")
    available = _parse_utc(available_text, f"{component_id}.available_at")
    if available > generated:
        fail("COMPONENT_FROM_FUTURE", component_id)
    return copy.deepcopy(row)


def _source_dir(row: dict, prefix: str) -> Path:
    relative = row.get("source_packet_path")
    if (
        not isinstance(relative, str)
        or not relative.startswith(prefix)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        fail("SOURCE_PATH_INVALID", row.get("component_id", "UNKNOWN"))
    path = (ROOT / relative).resolve()
    if ROOT.resolve() not in path.parents or not path.is_dir():
        fail("SOURCE_PATH_INVALID", row.get("component_id", "UNKNOWN"))
    return path


def _defined(
    row: dict,
    observation_date: str,
    available_at: str,
    transform_version: str,
    source_uri: str,
    source_sha256: str,
    warnings: list[str],
) -> dict:
    _parse_date(observation_date, "observation_date")
    _parse_utc(available_at, "available_at")
    if not isinstance(source_uri, str) or not source_uri:
        fail("SOURCE_EVIDENCE_INVALID", row["component_id"])
    if not isinstance(source_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", source_sha256
    ):
        fail("SOURCE_EVIDENCE_INVALID", row["component_id"])
    return {
        "status": "DEFINED",
        "observation_date": observation_date,
        "available_at": available_at,
        "transform_version": transform_version,
        "evidence": {
            "uri": source_uri,
            "sha256": source_sha256,
        },
        "warnings": sorted(warnings),
    }


def _undefined(code: str) -> dict:
    return {"status": "UNDEFINED", "warnings": [code]}


def _btc_trend(rows: dict, generated_at: str, binding: dict) -> dict:
    row = _row(rows, "BTC_TREND", generated_at)
    path = _source_dir(row, "evidence/crypto/btc/raw/")
    packet = BTC_TREND.build_transform(path)
    expected = {
        "direction": packet.get("direction"),
        "dma_200": packet.get("dma_200"),
    }
    if (
        row.get("contract_version") != binding["source_transform_version"]
        or packet.get("transform_version") != binding["source_transform_version"]
        or row.get("packet") != expected
        or row.get("generated_at") != packet.get("lineage", {}).get("available_at")
        or row.get("as_of_date") != packet.get("lineage", {}).get("vintage_date")
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "BTC_TREND")
    return _defined(
        row,
        packet["latest_finalized_day"],
        packet["lineage"]["available_at"],
        binding["axis_transform_version"],
        f"atlas-raw-response://{row['source_packet_path']}/kraken_ohlc_xbtusd.json.gz",
        packet["lineage"]["source_sha256"],
        ["REGIME_INTERPRETATION_UNAUTHORIZED"],
    )


def _btc_risk(rows: dict, generated_at: str, binding: dict) -> dict:
    row = _row(rows, "BTC_RISK", generated_at)
    path = _source_dir(row, "evidence/crypto/btc/raw/")
    packet = BTC_RISK.build_transform(path)
    expected = {"status": packet.get("status"), "risk_point": packet.get("risk_point")}
    if (
        row.get("contract_version") != binding["source_transform_version"]
        or packet.get("transform_version") != binding["source_transform_version"]
        or row.get("packet") != expected
        or row.get("generated_at") != packet.get("lineage", {}).get("available_at")
        or row.get("as_of_date") != packet.get("lineage", {}).get("vintage_date")
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "BTC_RISK")
    return _defined(
        row,
        packet["risk_point"]["as_of_date"],
        packet["lineage"]["available_at"],
        binding["axis_transform_version"],
        f"atlas-raw-response://{row['source_packet_path']}/kraken_ohlc_xbtusd.json.gz",
        packet["lineage"]["source_sha256"],
        ["STRESS_THRESHOLDS_UNCALIBRATED", "REGIME_INTERPRETATION_UNAUTHORIZED"],
    )


def _stablecoin(rows: dict, generated_at: str, binding: dict) -> dict:
    row = _row(rows, "STABLECOIN_NET_ISSUANCE", generated_at)
    path = _source_dir(row, "evidence/stablecoin/raw/")
    packet = STABLECOIN.build_transform(path)
    latest = packet["rows"][-1] if packet.get("rows") else None
    if not isinstance(latest, dict):
        fail("SOURCE_EMPTY", "STABLECOIN_NET_ISSUANCE")
    expected = {
        "observation_date": latest.get("observation_date"),
        "daily_net_issuance_native_usd_peg": latest.get(
            "daily_net_issuance_native_usd_peg"
        ),
        "daily_status": latest.get("daily_status"),
        "weekly_net_issuance_native_usd_peg": latest.get(
            "weekly_net_issuance_native_usd_peg"
        ),
        "weekly_status": latest.get("weekly_status"),
    }
    if (
        packet.get("transform_version") != binding["source_transform_version"]
        or row.get("packet") != expected
        or row.get("generated_at") != packet.get("lineage", {}).get("available_at")
        or row.get("as_of_date") != packet.get("lineage", {}).get("vintage_date")
        or latest.get("daily_status") != "AVAILABLE"
        or latest.get("weekly_status") != "AVAILABLE"
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "STABLECOIN_NET_ISSUANCE")
    return _defined(
        row,
        latest["observation_date"],
        packet["lineage"]["available_at"],
        binding["axis_transform_version"],
        f"atlas-raw-response://{row['source_packet_path']}/stablecoincharts_all.json.gz",
        packet["source"]["response_sha256"],
        ["REGIME_INTERPRETATION_UNAUTHORIZED"],
    )


def _attempt(builder, rows: dict, generated_at: str, binding: dict) -> dict:
    try:
        return builder(rows, generated_at, binding)
    except Exception:  # fail closed per axis; the Regime envelope remains available
        return _undefined("LIVE_AXIS_EVIDENCE_UNAVAILABLE")


def build_axis_factors(component_rows: dict, generated_at: str) -> dict[str, dict]:
    """Return evidence-only factor specs for the three Regime envelopes."""
    if not isinstance(component_rows, dict):
        fail("COMPONENT_ROWS_INVALID", "object required")
    _parse_utc(generated_at, "generated_at")
    contract = load_contract()
    bindings = contract["bindings"]
    result = {market: {} for market in MARKETS}
    result["US"]["RISK_VOL"] = _undefined(
        "LIVE_AXIS_PROVENANCE_VALIDATOR_MISSING"
    )
    result["CRYPTO"]["TREND"] = _attempt(
        _btc_trend, component_rows, generated_at, bindings["CRYPTO/TREND"]
    )
    result["CRYPTO"]["RISK_VOL"] = _attempt(
        _btc_risk, component_rows, generated_at, bindings["CRYPTO/RISK_VOL"]
    )
    result["CRYPTO"]["LIQUIDITY"] = _attempt(
        _stablecoin, component_rows, generated_at, bindings["CRYPTO/LIQUIDITY"]
    )
    return result
