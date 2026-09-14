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
as corrected by addendum ``CIO-ADDENDUM-CRYPTO-SUBSCRIPTION-FLOOR-METRIC-20260914``
(bound by file hash below) applies the ratified P3-12 universe liquidity floor
-- exactly as ``config/upbit_tradeable_universe_policy.json`` defines it: the
30-finalized-day average KRW turnover against ``min_30d_avg_krw_turnover`` --
to the realtime subscription and PAPER candidate action set.  Unknown turnover
is excluded (fail-closed).  A market with an open PAPER position is always kept
subscribed regardless of the floor so exits stay possible.

This module is pure and offline: no network, no exchange/order endpoint, no
wall-clock read, no repository write.  Every authority flag stays false.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
POLICY_RELATIVE_PATH = "config/crypto_realtime_freshness_per_market_policy_ratified.json"
POLICY_PATH = ROOT / POLICY_RELATIVE_PATH
POLICY_SHA256 = "8883e22a4d88e760a45f3cbc3df06b4a7894a6052767bc0f7dcb3b93ee3ac1c6"
POLICY_ID = "CRYPTO_REALTIME_FRESHNESS_PER_MARKET_V1"
RATIFICATION_ID = "CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914"
COMPANION_DECISION_ID = "CIO-CRYPTO-REALTIME-SUBSCRIPTION-LIQUIDITY-20260914"
RATIFICATION_RECORD_RELATIVE_PATH = (
    "evidence/authority/crypto_realtime_freshness_per_market_user_ratification_20260914.json"
)
RATIFICATION_RECORD_SHA256 = "043932a4ff13e9bd683c8ff233bad3b5c8e8e2cb62045a1e756e27c7deba5ac4"
ADDENDUM_ID = "CIO-ADDENDUM-CRYPTO-SUBSCRIPTION-FLOOR-METRIC-20260914"
ADDENDUM_RELATIVE_PATH = (
    "evidence/authority/crypto_realtime_subscription_floor_metric_cio_addendum_20260914.json"
)
ADDENDUM_SHA256 = "bc009c591cd6492c55471a499502381812d54a8f12e50e7334ec04c10a954e1f"
EFFECTIVE_FROM_UTC = "2026-09-13T23:25:00Z"
AMENDED_POLICY_RELATIVE_PATH = "config/upbit_realtime_freshness_policy_ratified.json"
AMENDED_POLICY_PACKET_SHA256 = "7caecead701b47b21f0d2b1ecfd74c6bf63d9952a8493bca5f6c06d67b397f34"
UNIVERSE_POLICY_RELATIVE_PATH = "config/upbit_tradeable_universe_policy.json"

FRESH = "FRESH"
CAPPING_STATUSES = ("STALE", "UNKNOWN", "MISSING", "MIXED_GENERATION")
INCLUDED = "INCLUDED"
EXCLUDED = "EXCLUDED"
BELOW_FLOOR = "TURNOVER_30D_AVG_BELOW_FLOOR"
UNKNOWN_PREFIX = "TURNOVER_30D_AVG_UNKNOWN"
HELD_KEEP_SUBSCRIBED = "HELD_POSITION_KEPT_SUBSCRIBED_REGARDLESS_OF_FLOOR"
ADMITTED_UNIVERSE_STATES = ("TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{1,20}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

POLICY_FIELDS = {
    "schema_version", "policy_id", "approval_status", "ratification_id",
    "companion_decision_id", "ratified_by", "ratified_at_utc",
    "effective_from_utc", "effective_to_utc", "scope", "ratification_record",
    "companion_decision_correction",
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

    correction = value["companion_decision_correction"]
    if (
        not isinstance(correction, dict)
        or correction.get("addendum_id") != ADDENDUM_ID
        or correction.get("path") != ADDENDUM_RELATIVE_PATH
        or correction.get("file_sha256") != ADDENDUM_SHA256
    ):
        _fail("ADDENDUM_BINDING_INVALID")
    addendum_path = Path(root) / ADDENDUM_RELATIVE_PATH
    if _file_sha256(addendum_path) != ADDENDUM_SHA256:
        _fail("ADDENDUM_HASH_MISMATCH")
    addendum = _read_json(addendum_path)
    if (
        not isinstance(addendum, dict)
        or addendum.get("addendum_id") != ADDENDUM_ID
        or (addendum.get("ratification") or {}).get("sha256") != RATIFICATION_RECORD_SHA256
        or any(item is not False for item in (addendum.get("authority") or {"x": None}).values())
    ):
        _fail("ADDENDUM_CONTENT_MISMATCH")

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
    source = floor.get("definition_source") or {}
    if (
        floor.get("metric") != "RATIFIED_UNIVERSE_POLICY_30D_AVG_FINALIZED_DAILY_KRW_TURNOVER"
        or floor.get("unknown_turnover_policy") != "EXCLUDE_FAIL_CLOSED"
        or floor.get("held_position_markets") != "ALWAYS_SUBSCRIBED_REGARDLESS_OF_FLOOR"
        or source != {
            "path": UNIVERSE_POLICY_RELATIVE_PATH,
            "threshold_field": "min_30d_avg_krw_turnover",
            "lookback_field": "turnover_lookback_finalized_days",
            "input_row_field": "trailing_30d_krw_turnover",
        }
        or "min_krw" in floor
    ):
        _fail("LIQUIDITY_FLOOR_BINDING_INVALID")
    load_universe_floor_definition(root=root, policy=value)

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


def load_universe_floor_definition(*, root: Path = ROOT, policy: dict | None = None) -> dict:
    """The floor exactly as the RATIFIED universe policy defines it (no copy)."""
    floor = (policy or {}).get("liquidity_floor") if policy is not None else None
    source = (floor or {}).get("definition_source") or {
        "path": UNIVERSE_POLICY_RELATIVE_PATH,
        "threshold_field": "min_30d_avg_krw_turnover",
        "lookback_field": "turnover_lookback_finalized_days",
    }
    path = Path(root) / source["path"]
    universe_policy = _read_json(path)
    if not isinstance(universe_policy, dict) or universe_policy.get("approval_status") != "RATIFIED":
        _fail("UNIVERSE_POLICY_NOT_RATIFIED")
    try:
        minimum = Decimal(str(universe_policy[source["threshold_field"]]))
        lookback = universe_policy[source["lookback_field"]]
    except (KeyError, InvalidOperation) as exc:
        raise CryptoRealtimePerMarketPolicyError("UNIVERSE_POLICY_FLOOR_FIELDS_INVALID") from exc
    if not minimum.is_finite() or minimum <= 0 or type(lookback) is not int or lookback < 1:
        _fail("UNIVERSE_POLICY_FLOOR_FIELDS_INVALID")
    return {
        "policy_path": source["path"],
        "policy_file_sha256": _file_sha256(path),
        "policy_version": universe_policy.get("policy_version"),
        "threshold_field": source["threshold_field"],
        "lookback_field": source["lookback_field"],
        "min_30d_avg_krw_turnover": minimum,
        "turnover_lookback_finalized_days": lookback,
    }


def policy_reference(policy: dict) -> dict:
    return {
        "policy_id": policy["policy_id"],
        "path": POLICY_RELATIVE_PATH,
        "packet_sha256": policy["packet_sha256"],
        "ratification_id": policy["ratification_id"],
        "ratification_record_path": policy["ratification_record"]["path"],
        "ratification_record_sha256": policy["ratification_record"]["file_sha256"],
        "companion_decision_id": policy["companion_decision_id"],
        "companion_decision_addendum_id": policy["companion_decision_correction"]["addendum_id"],
        "companion_decision_addendum_sha256": policy["companion_decision_correction"]["file_sha256"],
        "effective_from_utc": policy["effective_from_utc"],
        "amended_freshness_policy_packet_sha256": policy["amended_freshness_policy"]["packet_sha256"],
    }


# ---------------------------------------------------------------------------
# Liquidity floor: the ratified P3-12 30-day average, per admitted market
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


def evaluate_liquidity_floor(universe_record: dict | None, *, policy: dict | None = None) -> dict:
    """Include/exclude every admitted market by the ratified 30-day average.

    Reads the hash-pinned P3-12 packet's own ``trailing_30d_krw_turnover``
    aggregate and divides by the ratified lookback, exactly the comparison the
    universe classifier performs.  A packet built under a different universe
    policy version, or a missing/invalid aggregate, is UNKNOWN and excluded.
    """
    policy = load_policy() if policy is None else policy
    definition = load_universe_floor_definition(root=ROOT, policy=policy)
    minimum = definition["min_30d_avg_krw_turnover"]
    lookback = definition["turnover_lookback_finalized_days"]
    block = {
        "metric": policy["liquidity_floor"]["metric"],
        "definition_source": {
            key: definition[key]
            for key in ("policy_path", "policy_file_sha256", "policy_version", "threshold_field", "lookback_field")
        },
        "unknown_turnover_policy": policy["liquidity_floor"]["unknown_turnover_policy"],
        "universe_snapshot_date": None,
        "markets": {},
    }
    if universe_record is None:
        return block
    packet = universe_record.get("packet") if isinstance(universe_record, dict) else None
    if not isinstance(packet, dict):
        _fail("UNIVERSE_PACKET_INVALID")
    block["universe_snapshot_date"] = universe_record.get("snapshot_date")
    rows = {
        row["market"]: row for row in packet.get("markets") or []
        if isinstance(row, dict) and isinstance(row.get("market"), str)
    }
    version_matches = packet.get("policy_version") == definition["policy_version"]
    result = {}
    for market in admitted_markets(packet):
        if not version_matches:
            result[market] = {"status": EXCLUDED, "reason": f"{UNKNOWN_PREFIX}:UNIVERSE_POLICY_VERSION_MISMATCH", "krw_30d_avg_turnover": None}
            continue
        raw = rows[market].get("trailing_30d_krw_turnover")
        try:
            aggregate = Decimal(raw) if isinstance(raw, str) else None
        except InvalidOperation:
            aggregate = None
        if aggregate is None or not aggregate.is_finite() or aggregate < 0:
            result[market] = {"status": EXCLUDED, "reason": f"{UNKNOWN_PREFIX}:AGGREGATE_INVALID", "krw_30d_avg_turnover": None}
            continue
        average = aggregate / Decimal(lookback)
        rendered = format(average.quantize(Decimal("0.00000001")), "f")
        if average >= minimum:
            result[market] = {"status": INCLUDED, "reason": None, "krw_30d_avg_turnover": rendered}
        else:
            result[market] = {"status": EXCLUDED, "reason": BELOW_FLOOR, "krw_30d_avg_turnover": rendered}
    block["markets"] = result
    return block


def _checked_held_markets(held_markets) -> list[str]:
    if held_markets is None:
        return []
    if not isinstance(held_markets, (list, tuple)) or any(
        not isinstance(market, str) or MARKET_RE.fullmatch(market) is None for market in held_markets
    ):
        _fail("HELD_MARKETS_INVALID")
    if len(set(held_markets)) != len(held_markets):
        _fail("HELD_MARKETS_DUPLICATE")
    return sorted(held_markets)


def subscription_markets(universe_packet_path, *, held_markets=None) -> dict:
    """Realtime subscription set.

    Admitted P3-12 markets passing the ratified floor, plus every market with
    an open PAPER position -- those are always kept subscribed regardless of
    the floor (addendum decision) so exits remain possible.
    """
    held = _checked_held_markets(held_markets)
    floor = None
    included: list[str] = []
    excluded: list[dict] = []
    if universe_packet_path is not None and Path(universe_packet_path).is_file():
        record = _read_json(Path(universe_packet_path))
        if not isinstance(record, dict) or not isinstance(record.get("packet"), dict):
            _fail("UNIVERSE_RECORD_INVALID")
        floor = evaluate_liquidity_floor(record)
        included = sorted(market for market, row in floor["markets"].items() if row["status"] == INCLUDED)
        excluded = [
            {"market": market, "reason": row["reason"], "krw_30d_avg_turnover": row["krw_30d_avg_turnover"]}
            for market, row in sorted(floor["markets"].items()) if row["status"] != INCLUDED
        ]
    kept = sorted(set(held) - set(included))
    return {
        "markets": sorted(set(included) | set(held)),
        "floor_included": included,
        "floor_excluded": excluded,
        "held_kept_subscribed": [{"market": market, "reason": HELD_KEEP_SUBSCRIBED} for market in kept],
        "floor": floor,
    }


def parse_held_markets_document(value) -> list[str]:
    """``{"schema_version": "crypto_paper_held_markets/1", "markets": [...]}``.

    Market codes only; quantities, prices and account values never enter.
    """
    if value is None or value == "":
        return []
    document = json.loads(value) if isinstance(value, str) else value
    if not isinstance(document, dict) or set(document) != {"schema_version", "markets"} or document.get("schema_version") != "crypto_paper_held_markets/1":
        _fail("HELD_MARKETS_DOCUMENT_INVALID")
    return _checked_held_markets(document["markets"])


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
