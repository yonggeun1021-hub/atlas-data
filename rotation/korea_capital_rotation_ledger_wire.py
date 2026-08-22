#!/usr/bin/env python3
"""P2-03 -> rotation_state_ledger -> daily briefing wiring.

Completes the flow: P3-03 Korea Breadth lineage (committed by
.github/scripts/korea_breadth_context_populate.py) ->
korea_capital_rotation.py's coverage_context.breadth -> the existing,
UNCHANGED rotation_state_ledger.apply_rotation() -> a small committed
briefing rolling-pointer (data/latest_korea_rotation.json) that
briefing/daily_orchestrator.py reads.

This module does not modify rotation/rotation_state_ledger.py or
rotation/korea_capital_rotation.py at all -- it only builds valid inputs
for them and persists a briefing-facing summary of their output. The
AVAILABLE/BLOCKED/UNKNOWN/STALE status is never invented here: it is
independently re-derived from the same raw per-market facts
korea_capital_rotation.py itself validates against
(_derive_breadth_market_status in that module), so a caller-side bug
here that disagreed with the module's own derivation would be caught by
build_packet()'s own BREADTH_CONTEXT_AUTHORITY_INVALID check -- this is
defense-in-depth, not the only line of defense.

P1-KR-05 first-seen lineage (2026-08-22): coverage_context.breadth's own
real-time AVAILABLE/BLOCKED/STALE/UNKNOWN status is UNCHANGED --
source_available_at (verified official publication timing) remains
permanently null today, an honest, unchanged gap; korea_capital_
rotation.py's own available_at invariant (same-day-or-earlier) is not
compatible with a genuinely T+1-confirmed first_seen_at, and is not
touched here. Separately, build_confirmed_history_context()/the
briefing pointer's new "confirmed_history" block track first_seen_at/
capture_mode and become CONFIRMED once a genuine forward_live capture's
first_seen_at falls strictly after its own observation date -- usable
only for retrospective/narrative evidence, never wired into
ranking_input_authorized or any decision-input path. historical_backfill
evidence stays permanently NOT confirmed regardless of timestamps.

rotation_state_ledger.py's own write_json_atomic() deliberately forbids
writing inside this repository (the P2 rotation ledger is not yet
authorized to persist as tracked, production state) -- this module does
not change that boundary. The full replayable ledger stays an
external/outside-repo artifact, built via rotation_state_ledger.apply_
rotation()/run() exactly as before. Only a small, non-authoritative
"latest known observation" pointer -- the same rolling-pointer pattern
already used by data/latest_krx.json/data/latest_dart_content.json/
data/latest_sec_content.json -- is committed here, purely so the daily
briefing has something to read; it carries no more authority than those
existing pointer files (all Stage/Buy/Action/Order/Production/trading
authority stays false).
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BREADTH_CONTEXT_ROOT = ROOT / "data" / "observations" / "korea_breadth_context"
BRIEFING_POINTER_PATH = ROOT / "data" / "latest_korea_rotation.json"
BRIEFING_POINTER_SCHEMA_VERSION = "korea_rotation_briefing_pointer/2"
REQUIRED_MARKETS = ("KOSDAQ", "KOSPI")
_BREADTH_STATUS_SEVERITY = {"UNKNOWN": 0, "BLOCKED": 1, "STALE": 2, "AVAILABLE": 3}

# Fixed, non-derived boundary -- identical to every existing test fixture
# and to korea_capital_rotation.py's own required shape. Investor flow is
# not wired here (no committed source exists for it yet); it stays this
# exact closed, non-ranking, unavailable shape.
INVESTOR_FLOW_CONTEXT = {
    "status": "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
    "market_venue_scope": "KRX_ONLY",
    "nxt_included": False,
    "whole_korea_market_claim_authorized": False,
    "source_release_time_status": "unverified",
    "available_at": None,
    "decision_eligible": False,
    "ranking_input_authorized": False,
}


class KoreaRotationWireError(ValueError):
    """Fail-closed P2-03 ledger/briefing wiring violation."""


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_date(value: str, code: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise KoreaRotationWireError(code) from exc
    if parsed.isoformat() != value:
        raise KoreaRotationWireError(code)
    return parsed


def _parse_timestamp(value: str, code: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise KoreaRotationWireError(code) from exc
    if parsed.tzinfo is None:
        raise KoreaRotationWireError(code)
    return parsed.astimezone(dt.timezone.utc)


def context_source_path(as_of_date: str) -> Path:
    return BREADTH_CONTEXT_ROOT / as_of_date / "packet.json"


def load_breadth_context_source(as_of_date: str) -> dict | None:
    """The committed non-reconstructive lineage summary for as_of_date,
    or None if it was never populated for this exact date -- never a
    fallback to a different date's file. Absence here is precisely what
    must fail closed to UNKNOWN downstream, not silently substitute
    stale or future evidence."""
    path = context_source_path(as_of_date)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KoreaRotationWireError(f"BREADTH_CONTEXT_SOURCE_READ_FAILED:{exc}") from exc
    digest = value.get("payload_sha256")
    payload = copy.deepcopy(value)
    payload.pop("payload_sha256", None)
    if not isinstance(digest, str) or payload_sha256(payload) != digest:
        raise KoreaRotationWireError("BREADTH_CONTEXT_SOURCE_SHA_MISMATCH")
    if value.get("as_of_date") != as_of_date:
        raise KoreaRotationWireError("BREADTH_CONTEXT_SOURCE_DATE_MISMATCH")
    return value


def _classify_market(
    market_fact: dict, as_of_date: dt.date, freshness_limit_days: int, market: str
) -> str:
    """Independent re-derivation of one market's AVAILABLE/BLOCKED/UNKNOWN/
    STALE status from raw facts alone -- deliberately re-implemented here
    (not a call into korea_capital_rotation.py's private function) so a
    divergence between this module's own understanding and the producer
    module's is a real, catchable defect rather than the same code path
    trivially agreeing with itself.

    Deliberately unchanged real-time decision-input semantics: this is
    korea_capital_rotation.py's own AVAILABLE/BLOCKED/STALE/UNKNOWN
    vocabulary, which assumes a same-day-or-earlier `available_at` (its
    own age_days check raises if available_at is ever *after* as_of_date
    -- the opposite direction of a T+1-confirmed first_seen_at). Only
    `source_available_at` (an honest, still-permanently-null gap -- KRX
    gives no verified official publication timing) feeds this slot; a
    real first-seen-based confirmation is a genuinely different temporal
    model (see build_confirmed_history_context below) and is
    deliberately NOT plugged in here, since doing so would either break
    this real, already-ratified invariant or silently reinterpret it.
    This is a real, load-bearing architectural boundary -- see this
    module's docstring and the PR body, not a placeholder to quietly
    paper over."""
    lineage_sha256 = market_fact.get("lineage_sha256")
    as_of = market_fact.get("as_of_date")
    available_at = market_fact.get("source_available_at")
    if lineage_sha256 is None and as_of is None and available_at is None:
        return "UNKNOWN"
    if lineage_sha256 is None or as_of is None:
        raise KoreaRotationWireError(f"BREADTH_MARKET_PARTIAL_IDENTITY:{market}")
    if available_at is None:
        return "BLOCKED"
    available_at_dt = _parse_timestamp(
        available_at, f"BREADTH_MARKET_AVAILABLE_AT_INVALID:{market}"
    )
    age_days = (as_of_date - available_at_dt.date()).days
    if age_days < 0:
        raise KoreaRotationWireError(f"BREADTH_MARKET_AVAILABLE_AT_AFTER_AS_OF:{market}")
    if age_days > freshness_limit_days:
        return "STALE"
    return "AVAILABLE"


_CONFIRMED_HISTORY_STATUS_SEVERITY = {
    "NO_LINEAGE": 0, "HISTORICAL_BACKFILL_NEVER_ELIGIBLE": 1,
    "NO_FIRST_SEEN": 1, "SAME_DAY_NOT_YET_CONFIRMED": 2, "CONFIRMED": 3,
}


def _confirmed_history_market(
    market_fact: dict, capture_mode: str | None, as_of_date: dt.date, market: str
) -> tuple[str, dt.datetime | None]:
    """P1-KR-05 first-seen policy (CIO minimum determination, separate
    from _classify_market's real-time decision-input status above):

    - source_available_at (verified official publication timing) would
      make an observation confirmed immediately, were it ever non-null
      -- it never is yet, so this path is honest but currently inert.
    - Otherwise, a genuine forward_live capture becomes CONFIRMED once
      its own first_seen_at falls on a calendar date strictly AFTER the
      observation's own as_of_date ("the next trading day's briefing
      saw a first-seen later than the observation date") -- same-day
      capture is explicitly SAME_DAY_NOT_YET_CONFIRMED, never used for a
      same-day real decision.
    - historical_backfill evidence is permanently ineligible regardless
      of how the real timestamps compare -- date math alone cannot tell
      a genuine next-day capture from a convenient later catch-up.
    - A first_seen_at claiming to be BEFORE the observation's own
      as_of_date is structurally impossible (data cannot be seen before
      the day it describes) and fails closed as a real tamper/defect,
      not a silently-ignored edge case.
    """
    lineage_sha256 = market_fact.get("lineage_sha256")
    if lineage_sha256 is None:
        return "NO_LINEAGE", None
    source_available_at = market_fact.get("source_available_at")
    if source_available_at is not None:
        confirmed_at = _parse_timestamp(
            source_available_at, f"BREADTH_MARKET_SOURCE_AVAILABLE_AT_INVALID:{market}"
        )
        return "CONFIRMED", confirmed_at
    first_seen_at = market_fact.get("first_seen_at")
    if first_seen_at is None:
        return "NO_FIRST_SEEN", None
    first_seen_dt = _parse_timestamp(
        first_seen_at, f"BREADTH_MARKET_FIRST_SEEN_AT_INVALID:{market}"
    )
    if first_seen_dt.date() < as_of_date:
        raise KoreaRotationWireError(f"BREADTH_MARKET_FIRST_SEEN_BEFORE_AS_OF:{market}")
    if capture_mode != "forward_live":
        return "HISTORICAL_BACKFILL_NEVER_ELIGIBLE", None
    if first_seen_dt.date() <= as_of_date:
        return "SAME_DAY_NOT_YET_CONFIRMED", None
    return "CONFIRMED", first_seen_dt


def build_confirmed_history_context(as_of_date: str, source: dict | None) -> dict:
    """Per-market confirmed-history evidence, entirely separate from
    coverage_context.breadth's real-time decision-input status: usable
    only for retrospective/narrative purposes (e.g. a later briefing
    honestly describing what already happened), never as a same-day
    ranking or trading input -- that boundary is enforced by never
    wiring this into korea_capital_rotation.py's ranking_input_authorized
    at all, not by a flag here."""
    as_of = _parse_date(as_of_date, "AS_OF_DATE_INVALID")
    capture_mode = source.get("capture_mode") if source else None
    markets = {}
    for market in REQUIRED_MARKETS:
        fact = (
            source.get("markets", {}).get(market, {}) if source else {}
        )
        if source is not None and not isinstance(fact, dict):
            raise KoreaRotationWireError(f"BREADTH_CONTEXT_SOURCE_MARKET_MISSING:{market}")
        status, confirmed_at = _confirmed_history_market(fact, capture_mode, as_of, market)
        markets[market] = {
            "status": status,
            "confirmed_history_eligible": status == "CONFIRMED",
            "first_seen_at": fact.get("first_seen_at"),
            "captured_at": fact.get("captured_at"),
            "confirmed_at": confirmed_at.isoformat() if confirmed_at else None,
            "capture_mode": capture_mode,
        }
    worst = min(
        markets, key=lambda m: _CONFIRMED_HISTORY_STATUS_SEVERITY[markets[m]["status"]]
    )
    return {
        "markets": markets,
        "all_markets_confirmed": all(m["confirmed_history_eligible"] for m in markets.values()),
        "worst_status": markets[worst]["status"],
    }


def _reason_for(market: str, status: str, fact: dict) -> str:
    if status == "UNKNOWN":
        return f"{market}_NO_LINEAGE_SUPPLIED"
    if status == "BLOCKED":
        return f"{market}_AVAILABLE_AT_NULL"
    if status == "STALE":
        return f"{market}_AVAILABLE_AT_STALE"
    return f"{market}_AVAILABLE"


def build_coverage_context_breadth(
    as_of_date: str, freshness_limit_days: int, source: dict | None
) -> tuple[dict, str]:
    """Builds the exact coverage_context.breadth shape korea_capital_
    rotation.py requires, plus a human-readable reason string, from the
    committed lineage source (or None -> both markets UNKNOWN, i.e. the
    fail-closed "downstream data missing" path -- no code branch here
    invents an AVAILABLE/BLOCKED guess when the file is simply absent)."""
    as_of = _parse_date(as_of_date, "AS_OF_DATE_INVALID")
    facts = {}
    output_markets = {}
    for market in REQUIRED_MARKETS:
        if source is None:
            entry = None
        else:
            entry = source.get("markets", {}).get(market)
            if not isinstance(entry, dict):
                raise KoreaRotationWireError(f"BREADTH_CONTEXT_SOURCE_MARKET_MISSING:{market}")
        fact = {
            "lineage_sha256": entry.get("lineage_sha256") if entry else None,
            "as_of_date": entry.get("as_of_date") if entry else None,
            "source_available_at": entry.get("source_available_at") if entry else None,
        }
        facts[market] = fact
        # korea_capital_rotation.py's own contract requires exactly
        # {"lineage_sha256","as_of_date","available_at"} here -- its
        # real-time decision-input semantics are deliberately UNCHANGED
        # (see _classify_market's docstring): only source_available_at
        # (still permanently null today) ever fills this slot, never a
        # T+1-confirmed first_seen_at.
        output_markets[market] = {
            "lineage_sha256": fact["lineage_sha256"],
            "as_of_date": fact["as_of_date"],
            "available_at": fact["source_available_at"],
        }
    per_market_status = {
        market: _classify_market(facts[market], as_of, freshness_limit_days, market)
        for market in REQUIRED_MARKETS
    }
    worst_market = min(
        per_market_status, key=lambda market: _BREADTH_STATUS_SEVERITY[per_market_status[market]]
    )
    derived_status = per_market_status[worst_market]
    reason = ",".join(
        _reason_for(market, per_market_status[market], facts[market])
        for market in REQUIRED_MARKETS
        if _BREADTH_STATUS_SEVERITY[per_market_status[market]]
        == _BREADTH_STATUS_SEVERITY[derived_status]
    )
    breadth = {
        "status": derived_status,
        "markets": output_markets,
        "freshness_limit_days": freshness_limit_days,
        "ranking_input_authorized": False,
        "decision_eligible": derived_status == "AVAILABLE",
    }
    return breadth, reason


def build_coverage_context(
    as_of_date: str, freshness_limit_days: int, source: dict | None
) -> tuple[dict, str]:
    breadth, reason = build_coverage_context_breadth(as_of_date, freshness_limit_days, source)
    return {
        "breadth": breadth,
        "investor_flow": copy.deepcopy(INVESTOR_FLOW_CONTEXT),
    }, reason


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def build_briefing_pointer(
    rotation_packet: dict,
    breadth_reason: str,
    context_source: dict | None,
    context_source_rel_path: str | None,
    generated_at: str,
) -> dict:
    """The small, non-authoritative briefing rolling-pointer -- same
    class of file as data/latest_krx.json, overwritten each refresh, no
    hash-chain of its own (the tamper-evident history lives in the
    external rotation_state_ledger, not here).

    generated_at is caller-supplied, never wall-clock: the rotation
    packet itself carries no generated_at (output_retention_policy keeps
    it non-reconstructive, so prior/current observation timestamps are
    not retained either -- only their sha256, in lineage), so the caller
    must derive it from real evidence -- ordinarily the breadth context
    source's own generated_at when one was found, since that is the
    freshest real timestamp this refresh actually depends on."""
    breadth = rotation_packet["coverage_context"]["breadth"]
    context_sha256 = context_source.get("payload_sha256") if context_source else None
    _parse_timestamp(generated_at, "GENERATED_AT_INVALID")
    # Entirely separate from breadth.status/decision_eligible above --
    # retrospective-only evidence, never a ranking or trading input (see
    # build_confirmed_history_context's docstring).
    confirmed_history = build_confirmed_history_context(
        rotation_packet["as_of_date"], context_source
    )
    pointer = {
        "schema_version": BRIEFING_POINTER_SCHEMA_VERSION,
        "contract_version": rotation_packet["contract_version"],
        "as_of_date": rotation_packet["as_of_date"],
        "generated_at": generated_at,
        "run_status": "OK",
        "rotation": {
            "status": rotation_packet["status"],
            "rotation_policy_effective": rotation_packet["rotation_policy_effective"],
            "packet_sha256": rotation_packet["payload_sha256"],
        },
        "breadth": {
            "status": breadth["status"],
            "reason": breadth_reason,
            "decision_eligible": breadth["decision_eligible"],
            "ranking_input_authorized": breadth["ranking_input_authorized"],
            "markets": breadth["markets"],
            "source_context_path": context_source_rel_path,
            "source_context_sha256": context_sha256,
        },
        "confirmed_history": confirmed_history,
        "authority": {
            "ranking_input_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    pointer["payload_sha256"] = payload_sha256(pointer)
    return pointer


def refresh_briefing_pointer(
    rotation_packet: dict,
    breadth_reason: str,
    context_source: dict | None,
    context_source_rel_path: str | None,
    generated_at: str,
    *,
    out_path: Path = BRIEFING_POINTER_PATH,
) -> dict:
    pointer = build_briefing_pointer(
        rotation_packet, breadth_reason, context_source, context_source_rel_path, generated_at
    )
    write_json_atomic(out_path, pointer)
    return pointer
