#!/usr/bin/env python3
"""P8-04 policy-neutral adapter from qualified briefing evidence to axes.

The adapter proves only that a Regime axis has a qualified, point-in-time
observation.  It never interprets the observation, assigns a Regime or
direction, applies a threshold, ranks a market, or authorizes an action.

Only direct semantic bindings are supported:

* KR/TREND, BREADTH, RISK_VOL, LIQUIDITY, LEADERSHIP
                    <- one official KRX five-signal aggregate observation.
                       The five measurements are presence evidence only; no
                       threshold or Regime interpretation is applied here.
* US/TREND          <- independently replayed SPY/QQQ/IWM Alpaca IEX daily bars
* US/RISK_VOL       <- independently replayed FRED VIXCLS raw evidence
* US/LIQUIDITY      <- current FRED WRESBAL/TOTBKCR no-raw derived observation
* CRYPTO/TREND      <- BTC_TREND
* CRYPTO/RISK_VOL   <- BTC_RISK
* CRYPTO/LIQUIDITY  <- STABLECOIN_NET_ISSUANCE, and/or Upbit microstructure
                       evidence (P4-07 UPBIT_MARKET_EVIDENCE) -- either
                       qualifying input alone is sufficient for DEFINED; this
                       is still a presence check, never an interpretation of
                       which input "means" more liquidity.
* CRYPTO/BREADTH    <- CRYPTO_BREADTH (P1-CR-06)
* CRYPTO/LEADERSHIP <- CRYPTO_LEADERSHIP (P1-CR-07), derived from CR-06
                       breadth snapshots. daily_orchestrator.py's
                       build_packet() has produced this component row since
                       P1-CR-08's Breadth/Leadership axis wiring PR; the
                       binding still fails closed to UNDEFINED whenever the
                       row is absent (no capture yet for the decision date)
                       or the underlying dual-window (7d/30d) natural
                       history is incomplete -- see
                       docs/regime_live_axis_adapter_contract.md.

P1-CR-08 note: every binding above proves only evidence PRESENCE
(DEFINED/UNDEFINED). It intentionally does NOT compute or emit any of the
interpreted axis values (POSITIVE/NEUTRAL/NEGATIVE, 확산/편중/붕괴, etc.)
described by the Notion Crypto policy doc's "5축 판정" section. That gap
between Notion canon and this repository's ratified P1-COM-01 evidence-only
contract is a known, user-acknowledged scope decision (2026-08-29), not an
implementation oversight -- see docs/regime_live_axis_adapter_contract.md.

The Korea seven-name post-close watchlist, Korea Breadth lineage-only receipts,
the three-name IEX sample, and the US membership roster are deliberately not
promoted into market-wide axes. Korea axes require the combined official KRX
five-signal packet; the older Breadth receipt alone remains non-promotable.
FRED is eligible only after the append-only raw response is independently
replayed.  A self-hashed derived pointer is not sufficient evidence.
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
CONTRACT_VERSION = "regime_live_axis_adapter/v7"
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
            **{
                f"KR/{axis}": {
                    "source_component": "KOREA_MARKET_SIGNALS",
                    "source_transform_version": "korea_market_signals/1",
                    "axis_transform_version": "regime_live_axis_korea_market_signals/v1",
                }
                for axis in (
                    "TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"
                )
            },
            "US/TREND": {
                "source_component": "FREE_MARKET_DATA",
                "source_transform_version": "free_market_data/3",
                "axis_transform_version": "regime_live_axis_us_etf_trend/v1",
            },
            "US/RISK_VOL": {
                "source_component": "FREE_MARKET_DATA",
                "source_transform_version": "free_market_data/3",
                "compatible_source_transform_versions": [
                    "free_market_data/2", "free_market_data/3"
                ],
                "axis_transform_version": "regime_live_axis_fred_vix/v1",
            },
            "US/LIQUIDITY": {
                "source_component": "FREE_MARKET_DATA",
                "source_transform_version": "free_market_data/3",
                "axis_transform_version": "regime_live_axis_fred_liquidity/v1",
            },
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
                "source_components": {
                    "STABLECOIN_NET_ISSUANCE": "stablecoin_net_issuance/v1",
                    "UPBIT_MARKET_EVIDENCE": "upbit_market_evidence_packet/1",
                },
                "qualification_rule": "any_qualifying_input_present",
                "axis_transform_version": "regime_live_axis_crypto_liquidity/v2",
            },
            "CRYPTO/BREADTH": {
                "source_component": "CRYPTO_BREADTH",
                "source_transform_version": "crypto_breadth_observation/v2",
                "axis_transform_version": "regime_live_axis_crypto_breadth/v1",
            },
            "CRYPTO/LEADERSHIP": {
                "source_component": "CRYPTO_LEADERSHIP",
                "source_transform_version": "crypto_leadership_contract/v2",
                "axis_transform_version": "regime_live_axis_crypto_leadership/v1",
            },
        },
        "deferred_axes": {},
        "non_promotable_evidence": [
            "IEX_PARTIAL_EXCHANGE_SECTOR_REFERENCE_NOT_CANONICAL_LEADERSHIP",
            "KRX_SEVEN_SYMBOL_WATCHLIST",
            "KOREA_BREADTH_LINEAGE_RECEIPT_WITHOUT_PARTICIPATION_COUNTS",
            "KOREA_BREADTH_REPLAY_ATTESTATION_WITHOUT_FIVE_SIGNAL_PACKET",
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
CRYPTO_BREADTH = _load(
    "atlas_regime_axis_crypto_breadth", ".github/scripts/crypto_breadth.py"
)
CRYPTO_LEADERSHIP = _load(
    "atlas_regime_axis_crypto_leadership", ".github/scripts/crypto_leadership.py"
)
UPBIT_MARKET_EVIDENCE = _load(
    "atlas_regime_axis_upbit_liquidity",
    ".github/scripts/upbit_microstructure_populate.py",
)
FRED_VIX = _load(
    "atlas_regime_axis_fred_vix", "collectors/fred_vix_provenance.py"
)
FREE_MARKET_DATA = _load(
    "atlas_regime_axis_free_market_data", "collectors/free_market_data.py"
)
KOREA_MARKET_SIGNALS = _load(
    "atlas_korea_market_signals",
    ".github/scripts/korea_market_signals.py",
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


def _stablecoin_evidence(rows: dict, generated_at: str, source_transform_version: str):
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
        packet.get("transform_version") != source_transform_version
        or row.get("packet") != expected
        or row.get("generated_at") != packet.get("lineage", {}).get("available_at")
        or row.get("as_of_date") != packet.get("lineage", {}).get("vintage_date")
        or latest.get("daily_status") != "AVAILABLE"
        or latest.get("weekly_status") != "AVAILABLE"
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "STABLECOIN_NET_ISSUANCE")
    return (
        row,
        latest["observation_date"],
        packet["lineage"]["available_at"],
        f"atlas-raw-response://{row['source_packet_path']}/stablecoincharts_all.json.gz",
        packet["source"]["response_sha256"],
    )


def _upbit_liquidity_evidence(
    rows: dict, generated_at: str, source_transform_version: str
):
    row = _row(rows, "UPBIT_MARKET_EVIDENCE", generated_at)
    path = _source_dir(row, "evidence/crypto/upbit/microstructure/")
    record = UPBIT_MARKET_EVIDENCE.rebuild(path.name, path.parent)
    if record.get("policy_ratified") is not True:
        fail("SOURCE_POLICY_UNRATIFIED", "UPBIT_MARKET_EVIDENCE")
    summary = record.get("summary", {})
    if not summary.get("packet_count"):
        fail("SOURCE_EMPTY", "UPBIT_MARKET_EVIDENCE")
    expected = {
        "market_count": summary.get("market_count"),
        "packet_count": summary.get("packet_count"),
        "error_count": summary.get("error_count"),
    }
    if (
        record.get("builder", {}).get("output_schema_version")
        != source_transform_version
        or row.get("packet") != expected
        or row.get("generated_at") != record.get("generated_at")
        or row.get("as_of_date") != record.get("snapshot_date")
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "UPBIT_MARKET_EVIDENCE")
    return (
        row,
        record["snapshot_date"],
        record["generated_at"],
        f"atlas-raw-response://{row['source_packet_path']}/_manifest.json",
        record["payload_sha256"],
    )


def _crypto_liquidity(rows: dict, generated_at: str, binding: dict) -> dict:
    """CRYPTO/LIQUIDITY is DEFINED whenever AT LEAST ONE qualifying evidence
    input exists (stablecoin net issuance and/or Upbit microstructure) --
    this stays a presence check across two candidate inputs, never a claim
    about which input is more informative. When only one input qualifies,
    that single input's raw evidence pointer is cited and a warning records
    which sibling input was unavailable; when both qualify, the stablecoin
    pointer is cited for backward-compatible determinism and a warning
    still never appears for the sibling that IS present.
    """
    versions = binding["source_components"]
    stablecoin_result = None
    upbit_result = None
    try:
        stablecoin_result = _stablecoin_evidence(
            rows, generated_at, versions["STABLECOIN_NET_ISSUANCE"]
        )
    except LiveAxisAdapterError:
        stablecoin_result = None
    try:
        upbit_result = _upbit_liquidity_evidence(
            rows, generated_at, versions["UPBIT_MARKET_EVIDENCE"]
        )
    except LiveAxisAdapterError:
        upbit_result = None

    if stablecoin_result is None and upbit_result is None:
        fail("LIQUIDITY_NO_QUALIFYING_INPUT", "CRYPTO/LIQUIDITY")

    warnings = ["REGIME_INTERPRETATION_UNAUTHORIZED"]
    if stablecoin_result is not None:
        chosen = stablecoin_result
        if upbit_result is None:
            warnings.append("CRYPTO_LIQUIDITY_UPBIT_MICROSTRUCTURE_INPUT_UNAVAILABLE")
    else:
        chosen = upbit_result
        warnings.append("CRYPTO_LIQUIDITY_STABLECOIN_INPUT_UNAVAILABLE")

    row, observation_date, available_at, source_uri, source_sha256 = chosen
    return _defined(
        row,
        observation_date,
        available_at,
        binding["axis_transform_version"],
        source_uri,
        source_sha256,
        warnings,
    )


def _crypto_breadth_coverage_diagnostics(packet: dict) -> dict:
    """Mirrors briefing/daily_orchestrator.py's own helper of the same
    name exactly -- duplicated, not imported, because daily_orchestrator.py
    already imports this module and a reverse import would cycle. Both
    copies only ever pass through crypto_breadth.py's own already-computed
    ``universe`` diagnostics; neither invents a number. Kept in lock-step
    intentionally: this function's output is exactly what
    ``_classify_crypto_breadth`` in daily_orchestrator.py wrote into the
    row it is independently re-deriving here, so the two must match or
    COMPONENT_REDERIVATION_MISMATCH correctly fails closed."""
    universe = packet.get("universe")
    if not isinstance(universe, dict):
        return {
            "selected_asset_count": None,
            "target_asset_count": None,
            "known_eligible_count_so_far": None,
            "resolved_cutoff_slot_count": None,
            "taxonomy_unknown_before_cutoff_count": None,
            "taxonomy_unknown_before_cutoff_assets": None,
            "coverage_ratio_bps": None,
        }
    target = universe.get("target_asset_count")
    known_so_far = universe.get("known_eligible_count_so_far")
    selected = universe.get("selected_asset_count")
    unknown_before_cutoff = universe.get("taxonomy_unknown_before_cutoff")
    unknown_assets = None
    if isinstance(unknown_before_cutoff, list):
        unknown_assets = sorted(
            item["canonical_asset_id"]
            for item in unknown_before_cutoff
            if isinstance(item, dict) and isinstance(item.get("canonical_asset_id"), str)
        )
    resolved_slots = None
    coverage_ratio_bps = None
    if isinstance(target, int) and target > 0:
        if isinstance(unknown_before_cutoff, list):
            resolved_slots = max(target - len(unknown_before_cutoff), 0)
        elif isinstance(selected, int):
            resolved_slots = min(max(selected, 0), target)
        if resolved_slots is not None:
            coverage_ratio_bps = (resolved_slots * 10000) // target
    return {
        "selected_asset_count": selected,
        "target_asset_count": target,
        "known_eligible_count_so_far": known_so_far,
        "resolved_cutoff_slot_count": resolved_slots,
        "taxonomy_unknown_before_cutoff_count": (
            len(unknown_before_cutoff)
            if isinstance(unknown_before_cutoff, list)
            else None
        ),
        "taxonomy_unknown_before_cutoff_assets": unknown_assets,
        "coverage_ratio_bps": coverage_ratio_bps,
    }


def _crypto_breadth(rows: dict, generated_at: str, binding: dict) -> dict:
    row = _row(rows, "CRYPTO_BREADTH", generated_at)
    path = _source_dir(row, "evidence/crypto/breadth/raw/")
    packet = CRYPTO_BREADTH.build_transform(path)
    if packet.get("status") != "OBSERVED_UNCLASSIFIED":
        fail("COMPONENT_NOT_OBSERVED", "CRYPTO_BREADTH")
    expected = {
        "status": packet.get("status"),
    } | _crypto_breadth_coverage_diagnostics(packet)
    if (
        packet.get("transform_version") != binding["source_transform_version"]
        or row.get("packet") != expected
        or row.get("generated_at") != packet.get("lineage", {}).get("available_at")
        or row.get("as_of_date") != packet.get("lineage", {}).get("vintage_date")
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "CRYPTO_BREADTH")
    return _defined(
        row,
        packet["as_of_date"],
        packet["lineage"]["available_at"],
        binding["axis_transform_version"],
        f"atlas-raw-response://{row['source_packet_path']}/_manifest.json",
        packet["lineage"]["manifest_sha256"],
        ["REGIME_INTERPRETATION_UNAUTHORIZED"],
    )


def _crypto_leadership(rows: dict, generated_at: str, binding: dict) -> dict:
    """Derived, not directly captured: CRYPTO_LEADERSHIP re-derives from the
    same evidence/crypto/breadth/raw CR-06 snapshots CRYPTO_BREADTH reads,
    independently, for the row's own as_of_date end-of-window date.
    daily_orchestrator.py's build_packet() now produces this component row
    (see briefing/daily_orchestrator.py's build_crypto_leadership()) -- a
    row absent from ``rows`` (e.g. no capture yet for the decision date)
    still fails closed to UNDEFINED (COMPONENT_MISSING), and a present row
    whose own underlying dual-window (pilot_7d/primary_30d) natural history
    is not yet ``OBSERVED_UNCLASSIFIED`` on both windows fails closed the
    same way (COMPONENT_NOT_OBSERVED) -- see
    docs/regime_live_axis_adapter_contract.md.
    """
    row = _row(rows, "CRYPTO_LEADERSHIP", generated_at)
    path = _source_dir(row, "evidence/crypto/breadth/raw")
    # Mirrors CRYPTO_BREADTH's own row.as_of_date convention: the capture
    # VINTAGE directory name, one calendar day after the trading day the
    # evidence actually reports on -- see crypto_breadth.py's
    # ``as_of = core["vintage"] - timedelta(days=1)``. crypto_leadership's
    # own ``end_date`` argument is the trading day itself.
    vintage = row.get("as_of_date")
    if not isinstance(vintage, str):
        fail("SOURCE_EVIDENCE_INVALID", "CRYPTO_LEADERSHIP")
    try:
        end_date = (
            dt.date.fromisoformat(vintage) - dt.timedelta(days=1)
        ).isoformat()
    except ValueError:
        fail("SOURCE_EVIDENCE_INVALID", "CRYPTO_LEADERSHIP")
    packet = CRYPTO_LEADERSHIP.build_transform(path, end_date=end_date)
    if packet.get("status") != "OBSERVED_UNCLASSIFIED":
        fail("COMPONENT_NOT_OBSERVED", "CRYPTO_LEADERSHIP")
    expected = {"status": packet.get("status")}
    if (
        packet.get("contract_version") != binding["source_transform_version"]
        or row.get("packet") != expected
        or packet.get("as_of_date") != end_date
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "CRYPTO_LEADERSHIP")
    manifest_entries = packet.get("lineage", {}).get("manifest_sha256_by_date", [])
    matching = [
        entry
        for entry in manifest_entries
        if entry.get("as_of_date") == packet["as_of_date"]
    ]
    if len(matching) != 1:
        fail("SOURCE_EVIDENCE_INVALID", "CRYPTO_LEADERSHIP")
    available_candidates = [
        point.get("lineage", {}).get("available_at")
        for window in packet.get("windows", [])
        for point in window.get("daily_points", [])
    ]
    if not available_candidates or any(
        value is None for value in available_candidates
    ):
        fail("SOURCE_EVIDENCE_INVALID", "CRYPTO_LEADERSHIP")
    available_at = max(available_candidates)
    return _defined(
        row,
        packet["as_of_date"],
        available_at,
        binding["axis_transform_version"],
        (
            "atlas-raw-response://evidence/crypto/breadth/raw/"
            f"{vintage}/_manifest.json"
        ),
        matching[0]["manifest_sha256"],
        ["REGIME_INTERPRETATION_UNAUTHORIZED"],
    )


def _fred_vix(rows: dict, generated_at: str, binding: dict) -> dict:
    component_id = "FREE_MARKET_DATA"
    row = rows.get(component_id)
    if not isinstance(row, dict) or row.get("component_id") != component_id:
        fail("COMPONENT_MISSING", component_id)
    if row.get("status") not in {"READY", "DEGRADED"} or row.get("validated") is not True:
        fail("COMPONENT_NOT_READY", component_id)
    if any(row.get(key) is not False for key in (
        "decision_eligible", "action_eligible", "order_eligible"
    )) or not _authority_false(row):
        fail("COMPONENT_AUTHORITY_INVALID", component_id)
    generated = _parse_utc(generated_at, "generated_at")
    available = _parse_utc(
        row.get("available_at") or row.get("generated_at"),
        f"{component_id}.available_at",
    )
    if available > generated:
        fail("COMPONENT_FROM_FUTURE", component_id)
    packet = row.get("packet")
    if not isinstance(packet, dict):
        fail("COMPONENT_PACKET_INVALID", component_id)
    replay = FRED_VIX.validate_evidence(
        ROOT, packet.get("fred_evidence"), decision_at=generated_at
    )
    observation = replay["observation"]
    if (
        row.get("contract_version") not in binding.get(
            "compatible_source_transform_versions",
            [binding["source_transform_version"]],
        )
        or packet.get("vixcls") != {
            "date": observation.get("observation_date"),
            "value": observation.get("value"),
        }
        or row.get("as_of_date") != observation.get("observation_date")
        or row.get("generated_at") != replay.get("captured_at_utc")
        or row.get("available_at") != replay.get("captured_at_utc")
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", component_id)
    return _defined(
        row,
        observation["observation_date"],
        replay["captured_at_utc"],
        binding["axis_transform_version"],
        f"atlas-raw-response://{replay['pointer']['raw_path']}",
        replay["pointer"]["raw_response_sha256"],
        ["REGIME_INTERPRETATION_UNAUTHORIZED"],
    )


def _free_market_capture(rows: dict, generated_at: str, binding: dict) -> tuple[dict, dict]:
    component_id = "FREE_MARKET_DATA"
    row = rows.get(component_id)
    if not isinstance(row, dict) or row.get("component_id") != component_id:
        fail("COMPONENT_MISSING", component_id)
    if row.get("status") not in {"READY", "DEGRADED"} or row.get("validated") is not True:
        fail("COMPONENT_NOT_READY", component_id)
    if any(row.get(key) is not False for key in (
        "decision_eligible", "action_eligible", "order_eligible"
    )) or not _authority_false(row):
        fail("COMPONENT_AUTHORITY_INVALID", component_id)
    if row.get("contract_version") != binding["source_transform_version"]:
        fail("COMPONENT_CONTRACT_MISMATCH", component_id)
    generated = _parse_utc(generated_at, "generated_at")
    available_at = row.get("available_at") or row.get("generated_at")
    if _parse_utc(available_at, f"{component_id}.available_at") > generated:
        fail("COMPONENT_FROM_FUTURE", component_id)
    if row.get("source_packet_path") != "data/latest_free_market_data.json":
        fail("SOURCE_PATH_INVALID", component_id)
    path = (ROOT / row["source_packet_path"]).resolve()
    try:
        path.relative_to(ROOT.resolve())
        capture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LiveAxisAdapterError(
            f"SOURCE_EVIDENCE_INVALID:{component_id}"
        ) from exc
    unsigned = copy.deepcopy(capture)
    claimed = unsigned.pop("packet_sha256", None)
    actual = FREE_MARKET_DATA.sha256_bytes(FREE_MARKET_DATA.canonical_bytes(unsigned))
    if (
        capture.get("schema_version") != "free_market_data_capture/5"
        or capture.get("contract_version") != binding["source_transform_version"]
        or claimed != actual
        or row.get("source_packet_sha256") != claimed
        or row.get("generated_at") != capture.get("observed_at_utc")
        or row.get("available_at") != capture.get("observed_at_utc")
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", component_id)
    return row, capture


def _us_trend(rows: dict, generated_at: str, binding: dict) -> dict:
    row, capture = _free_market_capture(rows, generated_at, binding)
    try:
        replay = FREE_MARKET_DATA.validate_alpaca_daily_evidence(ROOT, capture)
    except Exception as exc:
        raise LiveAxisAdapterError(
            "SOURCE_EVIDENCE_INVALID:US/TREND"
        ) from exc
    reference = replay["reference"]
    if (
        reference.get("status") != "READY"
        or row.get("packet", {}).get("us_market_reference") != reference
        or row.get("packet", {}).get("alpaca_daily_evidence") != {
            "raw_path": replay["raw_path"],
            "raw_response_sha256": replay["raw_response_sha256"],
        }
        or [item.get("symbol") for item in reference.get("trend_etfs", [])]
        != ["SPY", "QQQ", "IWM"]
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "US/TREND")
    return _defined(
        row,
        reference["as_of_session_date"],
        capture["observed_at_utc"],
        binding["axis_transform_version"],
        f"atlas-raw-response://{replay['raw_path']}",
        replay["raw_response_sha256"],
        [
            "IEX_PARTIAL_EXCHANGE_REFERENCE",
            "REGIME_INTERPRETATION_UNAUTHORIZED",
        ],
    )


def _us_liquidity(rows: dict, generated_at: str, binding: dict) -> dict:
    row, capture = _free_market_capture(rows, generated_at, binding)
    liquidity = capture.get("fred_liquidity")
    row_liquidity = row.get("packet", {}).get("fred_liquidity")
    series = liquidity.get("series") if isinstance(liquidity, dict) else None
    if (
        not isinstance(liquidity, dict)
        or liquidity.get("status") != "READY"
        or liquidity.get("derivation_version") != "fred_liquidity_current/v1"
        or liquidity.get("raw_retention")
        != "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED"
        or not isinstance(series, list)
        or {item.get("series_id") for item in series if isinstance(item, dict)}
        != {"WRESBAL", "TOTBKCR"}
        or liquidity.get("derived_payload_sha256")
        != FREE_MARKET_DATA.sha256_bytes(FREE_MARKET_DATA.canonical_bytes(series))
        or row_liquidity != liquidity
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", "US/LIQUIDITY")
    observation_date = max(item["observation_date"] for item in series)
    return _defined(
        row,
        observation_date,
        capture["observed_at_utc"],
        binding["axis_transform_version"],
        "atlas-derived://data/latest_free_market_data.json#fred_liquidity",
        liquidity["derived_payload_sha256"],
        [
            "CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY",
            "TRANSIENT_HASH_ATTESTED_NO_RAW_REPLAY",
            "REGIME_INTERPRETATION_UNAUTHORIZED",
        ],
    )


def _attempt(builder, rows: dict, generated_at: str, binding: dict) -> dict:
    try:
        return builder(rows, generated_at, binding)
    except Exception:  # fail closed per axis; the Regime envelope remains available
        return _undefined("LIVE_AXIS_EVIDENCE_UNAVAILABLE")


def _korea_market_signal(
    rows: dict, generated_at: str, binding: dict, axis: str
) -> dict:
    component_id = "KOREA_MARKET_SIGNALS"
    row = _row(rows, component_id, generated_at)
    if row.get("contract_version") != binding["source_transform_version"]:
        fail("COMPONENT_CONTRACT_MISMATCH", component_id)
    source_dir = _source_dir(
        row, "data/observations/korea_market_signals/"
    )
    packet_path = source_dir / "packet.json"
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        packet = KOREA_MARKET_SIGNALS.validate_packet(packet)
    except Exception as exc:
        raise LiveAxisAdapterError(
            f"SOURCE_EVIDENCE_INVALID:{component_id}"
        ) from exc
    expected_packet = row.get("packet")
    if (
        not isinstance(expected_packet, dict)
        or expected_packet != packet
        or row.get("as_of_date") != packet.get("as_of_date")
        or row.get("generated_at") != packet.get("generated_at")
        or row.get("available_at") != packet.get("available_at")
        or row.get("source_packet_sha256") != packet.get("payload_sha256")
        or packet.get("status") != "OBSERVED_UNCLASSIFIED"
        or packet.get("axes", {}).get(axis, {}).get("status") != "OBSERVED"
    ):
        fail("COMPONENT_REDERIVATION_MISMATCH", component_id)
    return _defined(
        row,
        packet["as_of_date"],
        packet["available_at"],
        binding["axis_transform_version"],
        f"atlas-observation://{packet_path.relative_to(ROOT.resolve()).as_posix()}",
        packet["payload_sha256"],
        ["REGIME_INTERPRETATION_UNAUTHORIZED"],
    )


def build_axis_factors(component_rows: dict, generated_at: str) -> dict[str, dict]:
    """Return evidence-only factor specs for the three Regime envelopes."""
    if not isinstance(component_rows, dict):
        fail("COMPONENT_ROWS_INVALID", "object required")
    _parse_utc(generated_at, "generated_at")
    contract = load_contract()
    bindings = contract["bindings"]
    result = {market: {} for market in MARKETS}
    for axis in ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"):
        result["KR"][axis] = _attempt(
            lambda rows, generated_at, binding, axis=axis: _korea_market_signal(
                rows, generated_at, binding, axis
            ),
            component_rows,
            generated_at,
            bindings[f"KR/{axis}"],
        )
    result["US"]["TREND"] = _attempt(
        _us_trend, component_rows, generated_at, bindings["US/TREND"]
    )
    result["US"]["RISK_VOL"] = _attempt(
        _fred_vix, component_rows, generated_at, bindings["US/RISK_VOL"]
    )
    result["US"]["LIQUIDITY"] = _attempt(
        _us_liquidity, component_rows, generated_at, bindings["US/LIQUIDITY"]
    )
    result["CRYPTO"]["TREND"] = _attempt(
        _btc_trend, component_rows, generated_at, bindings["CRYPTO/TREND"]
    )
    result["CRYPTO"]["RISK_VOL"] = _attempt(
        _btc_risk, component_rows, generated_at, bindings["CRYPTO/RISK_VOL"]
    )
    result["CRYPTO"]["LIQUIDITY"] = _attempt(
        _crypto_liquidity, component_rows, generated_at, bindings["CRYPTO/LIQUIDITY"]
    )
    result["CRYPTO"]["BREADTH"] = _attempt(
        _crypto_breadth, component_rows, generated_at, bindings["CRYPTO/BREADTH"]
    )
    result["CRYPTO"]["LEADERSHIP"] = _attempt(
        _crypto_leadership,
        component_rows,
        generated_at,
        bindings["CRYPTO/LEADERSHIP"],
    )
    for qualified_axis, reason in sorted(contract["deferred_axes"].items()):
        market, axis = qualified_axis.split("/", 1)
        if market not in result or axis in result[market]:
            fail(
                "CONTRACT_INVALID",
                f"deferred axis conflicts with binding: {qualified_axis}",
            )
        result[market][axis] = _undefined(reason)
    return result
