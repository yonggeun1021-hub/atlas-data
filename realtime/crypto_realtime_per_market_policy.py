#!/usr/bin/env python3
"""Per-market Crypto realtime freshness and realtime liquidity floor.

User ratification ``CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914`` (record
bound by file hash below, exactly like the P9-06 ratified freshness policy is
bound by the realtime gate contract) amends only the *application scope* of
``P9_06_UPBIT_CRYPTO_PAPER_V1``:

* each Upbit market's realtime freshness is judged independently with the
  unchanged ratified thresholds (CRYPTO provider age 20s, transport delay 3s);
* a STALE/MISSING/UNKNOWN market caps only its own action state; FRESH markets
  are evaluated normally; the aggregate realtime status stays a
  display/telemetry value and is no longer a global blocker;
* a held position in a non-FRESH market gets no PAPER exit execution, records
  a per-market HOLD, and raises an alert once it stays stale beyond 30 minutes
  (an engineering alert budget, not a policy threshold).

CIO companion decision ``CIO-CRYPTO-REALTIME-SUBSCRIPTION-LIQUIDITY-20260914``
applies the KRW 5,000,000,000 Upbit liquidity number to the realtime
subscription and PAPER candidate set, measured as the 24h traded value in the
daily P3-12 universe raw capture.  Unknown turnover is excluded (fail-closed).

This module is pure and offline: no network, no exchange/order endpoint, no
wall-clock read, no repository write.  Every authority flag stays false.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
POLICY_RELATIVE_PATH = "config/crypto_realtime_freshness_per_market_policy_ratified.json"
POLICY_PATH = ROOT / POLICY_RELATIVE_PATH
POLICY_SHA256 = "4890b70b8a5ca70f7040d79d5ffe09c1efc7475964e134405232fc9094c8778d"
POLICY_ID = "CRYPTO_REALTIME_FRESHNESS_PER_MARKET_V1"
RATIFICATION_ID = "CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914"
COMPANION_DECISION_ID = "CIO-CRYPTO-REALTIME-SUBSCRIPTION-LIQUIDITY-20260914"
RATIFICATION_RECORD_RELATIVE_PATH = (
    "evidence/authority/crypto_realtime_freshness_per_market_user_ratification_20260914.json"
)
RATIFICATION_RECORD_SHA256 = "043932a4ff13e9bd683c8ff233bad3b5c8e8e2cb62045a1e756e27c7deba5ac4"
EFFECTIVE_FROM_UTC = "2026-09-13T23:25:00Z"
AMENDED_POLICY_RELATIVE_PATH = "config/upbit_realtime_freshness_policy_ratified.json"
AMENDED_POLICY_PACKET_SHA256 = "7caecead701b47b21f0d2b1ecfd74c6bf63d9952a8493bca5f6c06d67b397f34"
UNIVERSE_POLICY_RELATIVE_PATH = "config/upbit_tradeable_universe_policy.json"

FRESH = "FRESH"
CAPPING_STATUSES = ("STALE", "UNKNOWN", "MISSING", "MIXED_GENERATION")
INCLUDED = "INCLUDED"
EXCLUDED = "EXCLUDED"
BELOW_FLOOR = "TURNOVER_24H_BELOW_FLOOR"
UNKNOWN_PREFIX = "TURNOVER_24H_UNKNOWN"
ADMITTED_UNIVERSE_STATES = ("TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE")
RAW_TICKER_FILE = "upbit_ticker.json.gz"
RAW_MANIFEST_FILE = "_manifest.json"
RAW_ROOT_RE = re.compile(r"^evidence/crypto/upbit/raw/(?P<date>\d{4}-\d{2}-\d{2})$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{1,20}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

POLICY_FIELDS = {
    "schema_version", "policy_id", "approval_status", "ratification_id",
    "companion_decision_id", "ratified_by", "ratified_at_utc",
    "effective_from_utc", "effective_to_utc", "scope", "ratification_record",
    "amended_freshness_policy", "per_market_freshness", "liquidity_floor",
    "stale_held_position", "regime_axes_use_realtime_ticker_freshness",
    "authority", "packet_sha256",
}


class CryptoRealtimePerMarketPolicyError(ValueError):
    """Fail-closed per-market realtime policy violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _fail(code: str) -> None:
    raise CryptoRealtimePerMarketPolicyError(code)


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoRealtimePerMarketPolicyError(f"FILE_HASH_FAILED:{path}") from exc


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CryptoRealtimePerMarketPolicyError(f"JSON_READ_FAILED:{path}") from exc


def parse_utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoRealtimePerMarketPolicyError(code) from exc


EFFECTIVE_FROM = parse_utc(EFFECTIVE_FROM_UTC, "EFFECTIVE_FROM_CONSTANT_INVALID")


def is_effective(generated_at: dt.datetime) -> bool:
    """Whether a decision instant falls inside the ratified per-market window."""
    if not isinstance(generated_at, dt.datetime) or generated_at.utcoffset() is None:
        _fail("GENERATED_AT_MUST_BE_TIMEZONE_AWARE")
    return generated_at.astimezone(dt.timezone.utc) >= EFFECTIVE_FROM


def load_policy(path: Path = POLICY_PATH, *, root: Path = ROOT) -> dict:
    """Load the exact-hash ratified per-market policy and its bindings."""
    value = _read_json(Path(path))
    if not isinstance(value, dict) or set(value) != POLICY_FIELDS:
        _fail("POLICY_FIELDS_MISMATCH")
    unsigned = copy.deepcopy(value)
    claimed = unsigned.pop("packet_sha256")
    if claimed != POLICY_SHA256 or payload_sha256(unsigned) != claimed:
        _fail("POLICY_EXACT_HASH_MISMATCH")
    if (
        value["schema_version"] != "crypto_realtime_freshness_per_market_policy/1"
        or value["policy_id"] != POLICY_ID
        or value["approval_status"] != "RATIFIED"
        or value["ratification_id"] != RATIFICATION_ID
        or value["companion_decision_id"] != COMPANION_DECISION_ID
        or value["scope"] != "INTERNAL_VIRTUAL_PAPER"
        or value["effective_from_utc"] != EFFECTIVE_FROM_UTC
        or value["regime_axes_use_realtime_ticker_freshness"] is not False
    ):
        _fail("POLICY_IDENTITY_INVALID")
    if parse_utc(value["ratified_at_utc"], "POLICY_RATIFIED_AT_INVALID") > EFFECTIVE_FROM:
        _fail("POLICY_EFFECTIVE_BEFORE_RATIFICATION")
    parse_utc(value["effective_to_utc"], "POLICY_EFFECTIVE_TO_INVALID")
    authority = value["authority"]
    if not isinstance(authority, dict) or not authority or any(item is not False for item in authority.values()):
        _fail("POLICY_AUTHORITY_INVALID")

    record_ref = value["ratification_record"]
    if record_ref != {"path": RATIFICATION_RECORD_RELATIVE_PATH, "file_sha256": RATIFICATION_RECORD_SHA256}:
        _fail("RATIFICATION_RECORD_BINDING_INVALID")
    record_path = Path(root) / RATIFICATION_RECORD_RELATIVE_PATH
    if _file_sha256(record_path) != RATIFICATION_RECORD_SHA256:
        _fail("RATIFICATION_RECORD_HASH_MISMATCH")
    record = _read_json(record_path)
    if (
        not isinstance(record, dict)
        or record.get("ratification_id") != RATIFICATION_ID
        or record.get("ratified_at_utc") != value["ratified_at_utc"]
        or (record.get("cio_companion_decision") or {}).get("id") != COMPANION_DECISION_ID
        or any(item is not False for item in (record.get("authority") or {"x": None}).values())
    ):
        _fail("RATIFICATION_RECORD_CONTENT_MISMATCH")

    amended = value["amended_freshness_policy"]
    amended_file = _read_json(Path(root) / AMENDED_POLICY_RELATIVE_PATH)
    if (
        amended.get("path") != AMENDED_POLICY_RELATIVE_PATH
        or amended.get("packet_sha256") != AMENDED_POLICY_PACKET_SHA256
        or amended_file.get("packet_sha256") != AMENDED_POLICY_PACKET_SHA256
        or amended.get("policy_id") != amended_file.get("policy_id")
        or amended.get("thresholds_changed") is not False
        or amended.get("crypto_max_provider_age_seconds") != 20
        or amended.get("crypto_max_transport_delay_seconds") != 3
        or amended_file.get("max_provider_age_seconds_by_market", {}).get("CRYPTO") != 20
        or amended_file.get("max_transport_delay_seconds_by_market", {}).get("CRYPTO") != 3
    ):
        _fail("AMENDED_FRESHNESS_POLICY_THRESHOLDS_MISMATCH")

    per_market = value["per_market_freshness"]
    if (
        per_market.get("judged_per_market") is not True
        or tuple(per_market.get("capping_statuses") or ()) != CAPPING_STATUSES
        or per_market.get("capped_action_states") != ["FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE"]
        or per_market.get("capped_state") != "WAIT"
    ):
        _fail("PER_MARKET_FRESHNESS_RULE_INVALID")

    floor = value["liquidity_floor"]
    universe_policy = _read_json(Path(root) / UNIVERSE_POLICY_RELATIVE_PATH)
    try:
        min_krw = Decimal(floor.get("min_krw"))
    except (InvalidOperation, TypeError) as exc:
        raise CryptoRealtimePerMarketPolicyError("LIQUIDITY_FLOOR_MIN_INVALID") from exc
    threshold_source = floor.get("threshold_source") or {}
    if (
        floor.get("metric") != "UPBIT_TICKER_ACC_TRADE_PRICE_24H_KRW"
        or floor.get("unknown_turnover_policy") != "EXCLUDE_FAIL_CLOSED"
        or min_krw != Decimal("5000000000")
        or threshold_source.get("path") != UNIVERSE_POLICY_RELATIVE_PATH
        or universe_policy.get(threshold_source.get("field")) != floor.get("min_krw")
        or universe_policy.get("approval_status") != "RATIFIED"
    ):
        _fail("LIQUIDITY_FLOOR_BINDING_INVALID")

    hold = value["stale_held_position"]
    if (
        hold.get("paper_exit_execution_while_not_fresh") is not False
        or hold.get("hold_action") != "HOLD"
        or type(hold.get("alert_after_stale_minutes")) is not int
        or hold.get("alert_after_stale_minutes") != 30
        or hold.get("alert_budget_kind") != "ENGINEERING_ALERT_BUDGET_NOT_POLICY"
    ):
        _fail("STALE_HELD_POSITION_RULE_INVALID")
    return copy.deepcopy(value)


def policy_reference(policy: dict) -> dict:
    return {
        "policy_id": policy["policy_id"],
        "path": POLICY_RELATIVE_PATH,
        "packet_sha256": policy["packet_sha256"],
        "ratification_id": policy["ratification_id"],
        "ratification_record_path": policy["ratification_record"]["path"],
        "ratification_record_sha256": policy["ratification_record"]["file_sha256"],
        "companion_decision_id": policy["companion_decision_id"],
        "effective_from_utc": policy["effective_from_utc"],
        "amended_freshness_policy_packet_sha256": policy["amended_freshness_policy"]["packet_sha256"],
    }


# ---------------------------------------------------------------------------
# Liquidity floor over the daily universe raw capture
# ---------------------------------------------------------------------------

def admitted_markets(universe_packet: dict) -> list[str]:
    markets = universe_packet.get("markets") if isinstance(universe_packet, dict) else None
    if not isinstance(markets, list):
        _fail("UNIVERSE_MARKETS_INVALID")
    return sorted(
        row["market"] for row in markets
        if isinstance(row, dict) and row.get("state") in ADMITTED_UNIVERSE_STATES
        and isinstance(row.get("market"), str)
    )


def _unknown_all(markets: list[str], detail: str) -> dict:
    return {
        market: {
            "status": EXCLUDED,
            "reason": f"{UNKNOWN_PREFIX}:{detail}",
            "krw_24h_traded_value": None,
        }
        for market in markets
    }


def evaluate_liquidity_floor(universe_record: dict | None, *, root: Path = ROOT, policy: dict | None = None) -> dict:
    """Include/exclude every admitted universe market by 24h KRW traded value.

    The raw ticker bytes are bound transitively: the (hash-pinned) universe
    record names its raw snapshot directory and manifest hash, and the
    manifest names the ticker file hash.  A missing raw file is UNKNOWN
    turnover (excluded); a hash mismatch is tamper and fails closed.
    """
    # The policy is code-checkout configuration; ``root`` only locates the
    # (possibly separately checked-out) raw observation evidence.
    policy = load_policy() if policy is None else policy
    min_krw = Decimal(policy["liquidity_floor"]["min_krw"])
    block = {
        "metric": policy["liquidity_floor"]["metric"],
        "min_krw": policy["liquidity_floor"]["min_krw"],
        "unknown_turnover_policy": policy["liquidity_floor"]["unknown_turnover_policy"],
        "universe_snapshot_date": None,
        "raw_manifest_sha256": None,
        "raw_ticker_sha256": None,
        "markets": {},
    }
    if universe_record is None:
        return block
    packet = universe_record.get("packet") if isinstance(universe_record, dict) else None
    markets = admitted_markets(packet)
    snapshot_date = universe_record.get("snapshot_date")
    block["universe_snapshot_date"] = snapshot_date
    raw = universe_record.get("raw_snapshot")
    if not isinstance(raw, dict) or set(raw) != {"path", "manifest_sha256"}:
        block["markets"] = _unknown_all(markets, "RAW_SNAPSHOT_REFERENCE_MISSING")
        return block
    match = RAW_ROOT_RE.fullmatch(raw["path"]) if isinstance(raw.get("path"), str) else None
    if match is None or match.group("date") != snapshot_date or not isinstance(raw.get("manifest_sha256"), str) or not SHA256_RE.fullmatch(raw["manifest_sha256"]):
        _fail("UNIVERSE_RAW_SNAPSHOT_REFERENCE_INVALID")
    raw_dir = Path(root) / raw["path"]
    manifest_path = raw_dir / RAW_MANIFEST_FILE
    ticker_path = raw_dir / RAW_TICKER_FILE
    if not manifest_path.is_file():
        block["markets"] = _unknown_all(markets, "RAW_MANIFEST_MISSING")
        return block
    if _file_sha256(manifest_path) != raw["manifest_sha256"]:
        _fail("UNIVERSE_RAW_MANIFEST_HASH_MISMATCH")
    manifest = _read_json(manifest_path)
    checksums = manifest.get("checksums") if isinstance(manifest, dict) else None
    if (
        not isinstance(checksums, dict)
        or manifest.get("vintage_date") != snapshot_date
        or manifest.get("auth_required") is not False
        or manifest.get("downloaded_at_utc") != (packet or {}).get("available_at")
    ):
        _fail("UNIVERSE_RAW_MANIFEST_INVALID")
    block["raw_manifest_sha256"] = raw["manifest_sha256"]
    expected_ticker_sha = checksums.get(RAW_TICKER_FILE)
    if not isinstance(expected_ticker_sha, str) or not SHA256_RE.fullmatch(expected_ticker_sha):
        block["markets"] = _unknown_all(markets, "RAW_TICKER_CHECKSUM_MISSING")
        return block
    if not ticker_path.is_file():
        block["markets"] = _unknown_all(markets, "RAW_TICKER_MISSING")
        return block
    # The capture manifest checksums the decompressed response bytes.
    try:
        ticker_bytes = gzip.decompress(ticker_path.read_bytes())
    except (OSError, EOFError) as exc:
        raise CryptoRealtimePerMarketPolicyError("UNIVERSE_RAW_TICKER_GZIP_INVALID") from exc
    if hashlib.sha256(ticker_bytes).hexdigest() != expected_ticker_sha:
        _fail("UNIVERSE_RAW_TICKER_HASH_MISMATCH")
    block["raw_ticker_sha256"] = expected_ticker_sha
    try:
        rows = json.loads(ticker_bytes, parse_float=Decimal, parse_int=Decimal)
    except ValueError:
        block["markets"] = _unknown_all(markets, "RAW_TICKER_UNPARSEABLE")
        return block
    if not isinstance(rows, list):
        block["markets"] = _unknown_all(markets, "RAW_TICKER_NOT_A_LIST")
        return block
    by_market: dict[str, list] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("market"), str):
            by_market.setdefault(row["market"], []).append(row)
    result = {}
    for market in markets:
        matches = by_market.get(market, [])
        if len(matches) != 1:
            detail = "TICKER_ROW_MISSING" if not matches else "TICKER_ROW_DUPLICATE"
            result[market] = {"status": EXCLUDED, "reason": f"{UNKNOWN_PREFIX}:{detail}", "krw_24h_traded_value": None}
            continue
        value = matches[0].get("acc_trade_price_24h")
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            result[market] = {"status": EXCLUDED, "reason": f"{UNKNOWN_PREFIX}:VALUE_INVALID", "krw_24h_traded_value": None}
            continue
        rendered = format(value, "f")
        if value >= min_krw:
            result[market] = {"status": INCLUDED, "reason": None, "krw_24h_traded_value": rendered}
        else:
            result[market] = {"status": EXCLUDED, "reason": BELOW_FLOOR, "krw_24h_traded_value": rendered}
    block["markets"] = result
    return block


def subscription_markets(universe_packet_path, *, root: Path = ROOT) -> dict:
    """Realtime subscription set: admitted P3-12 markets passing the floor."""
    if universe_packet_path is None or not Path(universe_packet_path).is_file():
        return {"markets": [], "excluded": [], "floor": None}
    record = _read_json(Path(universe_packet_path))
    if not isinstance(record, dict) or not isinstance(record.get("packet"), dict):
        _fail("UNIVERSE_RECORD_INVALID")
    floor = evaluate_liquidity_floor(record, root=root)
    included = sorted(market for market, row in floor["markets"].items() if row["status"] == INCLUDED)
    excluded = [
        {"market": market, "reason": row["reason"], "krw_24h_traded_value": row["krw_24h_traded_value"]}
        for market, row in sorted(floor["markets"].items()) if row["status"] != INCLUDED
    ]
    return {"markets": included, "excluded": excluded, "floor": floor}


# ---------------------------------------------------------------------------
# Per-market action cap
# ---------------------------------------------------------------------------

def cap_state_for_market(
    state: str, reason: str, *, market: str, non_realtime_freshness: str,
    market_realtime_freshness: str, liquidity_floor_status: str,
    liquidity_floor_reason: str | None, actionable_states=("FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE"),
) -> dict:
    """Cap only this market's actionable state; never another market's."""
    cap_reason = None
    if liquidity_floor_status != INCLUDED:
        cap_reason = f"REALTIME_LIQUIDITY_FLOOR_EXCLUDED:{market}:{liquidity_floor_reason}"
    elif non_realtime_freshness != FRESH:
        cap_reason = f"NON_REALTIME_FRESHNESS_NOT_FRESH:{non_realtime_freshness}"
    elif market_realtime_freshness != FRESH:
        cap_reason = f"MARKET_REALTIME_FRESHNESS_NOT_FRESH:{market}:{market_realtime_freshness}"
    if cap_reason is not None and state in actionable_states:
        return {
            "state": "WAIT", "reason": cap_reason, "capped": True,
            "cap_reason": cap_reason, "market_action_cap_reason": cap_reason,
        }
    return {
        "state": state, "reason": reason, "capped": False, "cap_reason": None,
        "market_action_cap_reason": cap_reason,
    }
