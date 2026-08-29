#!/usr/bin/env python3
"""Crypto PAPER decision-packet composition (wires P1-CR-08 + P5-08 + P5-09
onto the tail of the existing P9-06 30-minute Upbit realtime capture run).

This module is a **composition** layer only. It invents no new criterion,
threshold, candle interval, cooldown, or severity vocabulary -- it reads
already-committed evidence produced by four independently-ratified-or-
honestly-unratified upstream modules and calls their own pure functions
verbatim:

* ``universe/upbit_tradeable_universe.py``      (P3-12) -- the committed
  Upbit KRW tradeable-universe classification packet, read from
  ``data/observations/upbit_tradeable_universe/<date>/packet.json``.
* ``microstructure/upbit_market_evidence.py`` / ``upbit_candle_finalization.py``
  (P4-07) -- the committed finalized-candle / spread / depth / slippage
  evidence packet, read from
  ``data/observations/upbit_market_evidence/<date>/packet.json``.
* the just-captured P9-06 bounded WebSocket realtime evidence, read from
  ``evidence/crypto/upbit/realtime/<date>/run_NNN.json`` -- used ONLY for
  freshness/quote-state reporting; it is never an argument to P5-08/P5-09's
  own derivation math.
* ``regime/live_axis_adapter.py`` + ``regime/output_contract.py`` (P1-CR-08)
  -- DEFINED/UNDEFINED axis evidence only, never an interpreted RISK_ON/
  NEUTRAL/RISK_OFF/STRESS value (the runtime contract authorizes only
  ``UNKNOWN`` for the aggregate today).
* ``universe/crypto_candidate_promotion.py`` (P5-08) and
  ``universe/crypto_paper_buy_eligibility.py`` (P5-09) -- run verbatim,
  unmodified, over the packets above.

Explicitly OUT OF SCOPE (per the CIO's 2026-08-29 Crypto Continuous Briefing
v1 addendum, Notion ``3c79f2d73c848160a51de6931256dee4``, and the Crypto
policy canon, Notion ``3ca9f2d73c84810a9ee7c7125e1dabd0`` -- both re-read at
the start of this work): ``CRYPTO_CONTINUOUS_EVENT`` / Continuous Briefing /
alerting, any trigger threshold, candle interval, severity vocabulary,
cooldown, or notification SLO. Producing this PAPER decision snapshot is not
a declaration that Continuous Briefing has been built
(CIO's own words: "PAPER decision snapshot을 만들었다고 CRYPTO_CONTINUOUS_
EVENT까지 완성했다고 선언하지 않는다").

Freshness discipline: if the P3-12 packet is missing, stale beyond
``config/upbit_tradeable_universe_policy.json``'s own
``max_capture_age_hours`` bound (the same bound P3-12 itself already uses
for its own capture-freshness gate -- reused here, not invented), or the
P4-07 evidence used does not share P3-12's own snapshot date ("mixed
generation"), this module never silently proceeds as if fresh: the funnel
either does not run at all (P3-12 missing) or P5-08 itself fails closed
(regime/P3-12 date mismatch -- P5-08's own existing invariant, not a new
one), and any candidate state that would otherwise be actionable
(``FOCUSED_REVIEW`` un-reviewed / ``PAPER_BUY_ELIGIBLE``) is capped to
``WAIT`` with an explicit ``freshness_capped`` reason -- this composition
module's own belt-and-suspenders safety net on top of the upstream modules'
own gates, never a relaxation of them.

Regime authority today: ``regime/output_contract.py``'s runtime contract
authorizes only ``regime: "UNKNOWN"`` for every market
(``runtime_authorized_regimes == ["UNKNOWN"]``). P5-08's own ``evaluate_regime``
therefore can never return anything but criterion status ``UNKNOWN`` for
every candidate, which caps every candidate at ``WATCH`` at best -- Every
market landing at WATCH/WAIT with ``orderDraft=null`` and
``paper_ready_count == 0`` today is the correct, honest output, not a bug to
route around.

Determinism: ``generation_id`` and every derivation field are pure functions
of already-committed input bytes (source commit, upstream payload hashes) --
never of wall-clock. ``generated_at``/``capture_date``/``capture_hhmm`` DO
carry this run's observed wall-clock capture instant, but only as *recorded
evidence of what inputs happened to exist at build time* -- they are never
folded into ``generation_id``. Duplicate-packet guard: mirrors
``.github/scripts/upbit_universe_populate.py::populate``'s exact
"verified_existing vs populated" idempotency discipline -- the on-disk path
is itself content-addressed by ``generation_id``, so a second build of the
same slot with the exact same input bytes verifies-not-duplicates; a second
build of the same slot with genuinely different input bytes lands under a
different ``generation_id`` subdirectory, never silently overwriting.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UNIVERSE_DATA_ROOT = ROOT / "data" / "observations" / "upbit_tradeable_universe"
MARKET_EVIDENCE_DATA_ROOT = ROOT / "data" / "observations" / "upbit_market_evidence"
REALTIME_EVIDENCE_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "realtime"
OUTPUT_ROOT = ROOT / "evidence" / "crypto_paper_decision"

OUTPUT_SCHEMA_VERSION = "crypto_paper_decision_snapshot_packet/1"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HHMM_RE = re.compile(r"^\d{4}$")
# Same idempotency-key token shape as decision/action_order_idempotency.py
# (P9-04) and universe/crypto_paper_buy_eligibility.py::compute_duplicate_guard_key
# (P5-09) -- reused verbatim, not reinvented.
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoPaperDecisionSnapshotError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CryptoPaperDecisionSnapshotError(ValueError):
    """Fail-closed crypto PAPER decision-packet composition violation."""


UNIVERSE = _load("crypto_paper_decision_snapshot_universe", "universe/upbit_tradeable_universe.py")
PROMOTION = _load("crypto_paper_decision_snapshot_promotion", "universe/crypto_candidate_promotion.py")
ELIGIBILITY = _load("crypto_paper_decision_snapshot_eligibility", "universe/crypto_paper_buy_eligibility.py")
MARKET_EVIDENCE = _load("crypto_paper_decision_snapshot_market_evidence", "microstructure/upbit_market_evidence.py")
CANDLE_FINALIZATION = _load(
    "crypto_paper_decision_snapshot_candle_finalization", "microstructure/upbit_candle_finalization.py"
)
REGIME_OUTPUT = _load("crypto_paper_decision_snapshot_regime_output", "regime/output_contract.py")
LIVE_AXIS = _load("crypto_paper_decision_snapshot_live_axis", "regime/live_axis_adapter.py")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoPaperDecisionSnapshotError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperDecisionSnapshotError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _relpath(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise CryptoPaperDecisionSnapshotError(f"UTC_INVALID:{label}")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# Freshness vocabulary -- worst-of aggregation, same discipline as
# universe/crypto_paper_buy_eligibility.py::_worst_of.
# ---------------------------------------------------------------------------

FRESH = "FRESH"
STALE = "STALE"
MISSING = "MISSING"
MIXED_GENERATION = "MIXED_GENERATION"
FRESHNESS_STATUSES = (FRESH, STALE, MISSING, MIXED_GENERATION)
_FRESHNESS_SEVERITY = {FRESH: 0, STALE: 1, MIXED_GENERATION: 2, MISSING: 3}


def _worst_freshness(statuses) -> str:
    return max(statuses, key=lambda status: _FRESHNESS_SEVERITY[status])


# Actionable states this composition module will never report while
# evidence freshness is degraded -- a belt-and-suspenders cap on top of
# (never a relaxation of) P5-08's/P5-09's own gates. Isolated as a small
# pure function so the "this can never be bypassed" invariant is directly
# unit-testable against every state/freshness combination, independent of
# whether today's Regime-UNKNOWN state can actually reach these states yet.
_ACTIONABLE_STATES = ("FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE")


def cap_state_for_freshness(state: str, reason: str, overall_freshness: str) -> dict:
    if overall_freshness != FRESH and state in _ACTIONABLE_STATES:
        cap_reason = f"OVERALL_FRESHNESS_NOT_FRESH:{overall_freshness}"
        return {"state": "WAIT", "reason": cap_reason, "capped": True, "cap_reason": cap_reason}
    return {"state": state, "reason": reason, "capped": False, "cap_reason": None}


# ---------------------------------------------------------------------------
# Upstream evidence discovery -- read-only, zero network calls.
# ---------------------------------------------------------------------------

def find_latest_universe_packet(data_root: Path = UNIVERSE_DATA_ROOT):
    """Latest committed P3-12 record under ``data_root``, or ``None``.

    Mirrors ``upbit-realtime-capture.yml``'s own
    "Find latest committed P3-12 tradeable-universe classification" step
    (``ls -1 ... | sort | tail -n 1``): the lexicographically-last
    ``YYYY-MM-DD`` directory that actually contains a ``packet.json``.
    """
    data_root = Path(data_root)
    if not data_root.is_dir():
        return None
    dates = sorted(p.name for p in data_root.iterdir() if p.is_dir() and DATE_RE.fullmatch(p.name))
    for date in reversed(dates):
        candidate = data_root / date / "packet.json"
        if candidate.is_file():
            record = _read_json(candidate)
            return {
                "date": date, "path": candidate, "record": record,
                "packet": record.get("packet") if isinstance(record, dict) else None,
            }
    return None


def find_latest_market_evidence_packet(data_root: Path = MARKET_EVIDENCE_DATA_ROOT):
    """Latest committed P4-07 record under ``data_root``, or ``None``."""
    data_root = Path(data_root)
    if not data_root.is_dir():
        return None
    dates = sorted(p.name for p in data_root.iterdir() if p.is_dir() and DATE_RE.fullmatch(p.name))
    for date in reversed(dates):
        candidate = data_root / date / "packet.json"
        if candidate.is_file():
            record = _read_json(candidate)
            return {"date": date, "path": candidate, "record": record}
    return None


def find_latest_realtime_run(evidence_root: Path = REALTIME_EVIDENCE_ROOT):
    """The most recently written P9-06 ``run_NNN.json`` across every
    committed date directory -- "the run that just happened in this same
    workflow invocation" when called immediately after the capture step, or
    the most recent prior run for a standalone manual/functional check.
    """
    evidence_root = Path(evidence_root)
    if not evidence_root.is_dir():
        return None
    dates = sorted(p.name for p in evidence_root.iterdir() if p.is_dir() and DATE_RE.fullmatch(p.name))
    for date in reversed(dates):
        runs = sorted((evidence_root / date).glob("run_*.json"))
        if runs:
            latest = runs[-1]
            return {"date": date, "path": latest, "record": _read_json(latest)}
    return None


def find_previous_packet(output_root: Path, before_date: str, before_hhmm: str):
    """The immediately-prior committed packet under ``output_root`` whose
    ``(capture_date, capture_hhmm)`` sorts strictly before
    ``(before_date, before_hhmm)`` -- an honest audit-trail pointer, never a
    threshold/severity/alert judgment (see module docstring).
    """
    output_root = Path(output_root)
    if not output_root.is_dir():
        return None
    candidates = []
    for date_dir in output_root.iterdir():
        if not date_dir.is_dir() or not DATE_RE.fullmatch(date_dir.name):
            continue
        for hhmm_dir in date_dir.iterdir():
            if not hhmm_dir.is_dir() or not HHMM_RE.fullmatch(hhmm_dir.name):
                continue
            if (date_dir.name, hhmm_dir.name) >= (before_date, before_hhmm):
                continue
            for gen_dir in sorted(hhmm_dir.iterdir()):
                packet_path = gen_dir / "packet.json"
                if packet_path.is_file():
                    candidates.append((date_dir.name, hhmm_dir.name, gen_dir.name, packet_path))
    if not candidates:
        return None
    candidates.sort()
    _, _, _, packet_path = candidates[-1]
    packet = _read_json(packet_path)
    return {
        "generation_id": packet.get("generation_id"),
        "payload_sha256": packet.get("payload_sha256"),
        "funnel_counts": packet.get("funnel_counts"),
    }


def resolve_source_commit(explicit: str | None = None) -> str:
    if explicit is not None:
        if not FULL_SHA_RE.fullmatch(explicit):
            raise CryptoPaperDecisionSnapshotError(f"SOURCE_COMMIT_INVALID:{explicit}")
        return explicit
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not FULL_SHA_RE.fullmatch(value):
        raise CryptoPaperDecisionSnapshotError("SOURCE_COMMIT_UNRESOLVABLE")
    return value


# ---------------------------------------------------------------------------
# Authority -- hardcoded all-false. Unions P5-08's and P5-09's own exact
# authority-block vocabulary (reused, not reinvented) with two additional
# explicit fields for concepts neither upstream module names on its own.
# ---------------------------------------------------------------------------

def _authority_block() -> dict:
    block = dict(PROMOTION._ROW_AUTHORITY)
    block.update(ELIGIBILITY._ROW_AUTHORITY)
    block.update({
        # No concept in P3-12/P5-08/P5-09's own authority vocabulary names
        # "a real (non-PAPER) authority" or "permission to write an order"
        # directly -- these two are additive, not a duplicate of
        # order_authorized/trading_authorized/production_authorized above.
        "real_authority": False,
        "order_write_authorized": False,
    })
    return block


def _require_all_false(block: dict) -> None:
    if any(value is not False for value in block.values()):
        raise CryptoPaperDecisionSnapshotError("AUTHORITY_NOT_ALL_FALSE")


# ---------------------------------------------------------------------------
# Regime (P1-CR-08) -- DEFINED/UNDEFINED axis evidence only.
# ---------------------------------------------------------------------------

def build_regime_snapshot(generated_at: str, component_rows: dict | None = None) -> dict:
    """Build the market="CRYPTO" Regime envelope via the real P1-CR-08
    adapter. ``component_rows`` defaults to ``{}`` -- this composition
    script has no wired component registry of its own (that wiring is
    ``briefing/daily_orchestrator.py``'s job, a separate module) -- so every
    bound axis (TREND/RISK_VOL/LIQUIDITY/BREADTH/LEADERSHIP) fails closed to
    UNDEFINED via ``live_axis_adapter.py``'s own ``_attempt`` wrapper. This
    is an honest reflection of today's real wiring state, never a shortcut.
    """
    component_rows = {} if component_rows is None else component_rows
    factors = LIVE_AXIS.build_axis_factors(component_rows, generated_at)
    return REGIME_OUTPUT.build_unknown_output("CRYPTO", generated_at, factors=factors.get("CRYPTO", {}))


def crypto_regime_five_axis(regime_payload: dict) -> dict:
    """The five DEFINED/UNDEFINED axis facts only -- never the aggregate
    ``regime``/``direction`` fields (those stay internal derivation inputs
    for P5-08, never surfaced here as if they were an interpreted value).
    """
    factor_results = regime_payload.get("factor_results", {})
    return {
        axis: {
            "status": factor["status"],
            "warnings": factor.get("warnings", []),
            "observation_date": factor.get("observation_date"),
            "available_at": factor.get("available_at"),
        }
        for axis, factor in sorted(factor_results.items())
    }


# ---------------------------------------------------------------------------
# Finalized-candle attestation -- reuses P4-07's own already-computed
# classify_candles()/is_candle_finalized() results verbatim (they are
# already baked into the committed evidence packet); never re-derives.
# ---------------------------------------------------------------------------

def finalized_candle_attestation(market_evidence_entry: dict | None, *, used_in_promotion: bool) -> dict:
    if market_evidence_entry is None:
        return {"market_evidence_snapshot_date": None, "used_in_promotion": False, "markets": {}}
    packets = (market_evidence_entry["record"] or {}).get("packets", {})
    markets = {}
    for market, packet in sorted(packets.items()):
        candles = packet.get("candles", {})
        markets[market] = {
            timeframe: {
                "finalized_candle_count": (candles.get(timeframe) or {}).get("finalized_candle_count"),
                "in_progress_candle_count": (candles.get(timeframe) or {}).get("in_progress_candle_count"),
                "latest_finalized_close_time": (candles.get(timeframe) or {}).get("latest_finalized_close_time"),
                "freshness": (candles.get(timeframe) or {}).get("freshness"),
            }
            for timeframe in CANDLE_FINALIZATION.TIMEFRAMES
        }
    return {
        "market_evidence_snapshot_date": market_evidence_entry["date"],
        "used_in_promotion": used_in_promotion,
        "markets": markets,
    }


# ---------------------------------------------------------------------------
# Pure derivation -- given already-loaded upstream entries, build the full
# packet. No filesystem access below this point except the two config loads
# (part of the committed source tree, same discipline
# universe/crypto_paper_buy_eligibility.py::build_eligibility_packet already
# uses for its own default policy load).
# ---------------------------------------------------------------------------

def build_snapshot(
    *,
    generated_at: str,
    source_commit: str,
    universe_entry: dict | None,
    market_evidence_entry: dict | None,
    realtime_entry: dict | None,
    previous_entry: dict | None = None,
    component_rows: dict | None = None,
) -> dict:
    generated_dt = _parse_utc(generated_at, "generated_at")
    if not FULL_SHA_RE.fullmatch(source_commit):
        raise CryptoPaperDecisionSnapshotError(f"SOURCE_COMMIT_INVALID:{source_commit}")
    capture_date = generated_at[:10]
    capture_hhmm = generated_at[11:13] + generated_at[14:16]

    notes: list[str] = []
    source_refs: list[dict] = []

    # -- P3-12 universe freshness -------------------------------------
    universe_policy = UNIVERSE.load_policy()
    max_age_hours = Decimal(str(universe_policy["max_capture_age_hours"]))
    universe_packet = universe_entry["packet"] if universe_entry else None
    universe_date = universe_entry["date"] if universe_entry else None
    if universe_entry is not None:
        source_refs.append({
            "role": "upbit_tradeable_universe_packet", "path": _relpath(universe_entry["path"]),
            "sha256": _file_sha256(universe_entry["path"]),
        })
    if universe_packet is None:
        universe_status = MISSING
        notes.append("UPBIT_UNIVERSE_PACKET_MISSING")
    else:
        available_at = _parse_utc(universe_packet["available_at"], "universe.available_at")
        if available_at > generated_dt:
            raise CryptoPaperDecisionSnapshotError("UNIVERSE_AVAILABLE_AT_FUTURE_DATED")
        age_hours = Decimal(str((generated_dt - available_at).total_seconds())) / Decimal("3600")
        if age_hours > max_age_hours:
            universe_status = STALE
            notes.append(f"UPBIT_UNIVERSE_PACKET_STALE:age_hours={age_hours}:max={max_age_hours}")
        else:
            universe_status = FRESH

    # -- P4-07 market evidence freshness -------------------------------
    if market_evidence_entry is not None:
        source_refs.append({
            "role": "upbit_market_evidence_packet", "path": _relpath(market_evidence_entry["path"]),
            "sha256": _file_sha256(market_evidence_entry["path"]),
        })
    if market_evidence_entry is None:
        market_evidence_status = MISSING
        notes.append("UPBIT_MARKET_EVIDENCE_PACKET_MISSING")
    elif universe_date is not None and market_evidence_entry["date"] != universe_date:
        market_evidence_status = MIXED_GENERATION
        notes.append(
            f"UPBIT_MARKET_EVIDENCE_DATE_MISMATCH:universe={universe_date}:"
            f"market_evidence={market_evidence_entry['date']}"
        )
    else:
        market_evidence_status = FRESH

    # -- P9-06 realtime evidence freshness (metadata only -- never an
    #    argument to P5-08/P5-09's own derivation) ----------------------
    if realtime_entry is not None:
        source_refs.append({
            "role": "upbit_realtime_capture_run", "path": _relpath(realtime_entry["path"]),
            "sha256": _file_sha256(realtime_entry["path"]),
        })
    if realtime_entry is None:
        realtime_status = MISSING
        notes.append("UPBIT_REALTIME_RUN_MISSING")
    elif universe_date is not None and realtime_entry["date"] != universe_date:
        realtime_status = MIXED_GENERATION
        notes.append(
            f"UPBIT_REALTIME_RUN_DATE_MISMATCH:universe={universe_date}:realtime={realtime_entry['date']}"
        )
    else:
        gate_status = ((realtime_entry["record"].get("run") or {}).get("status") or {}).get("overall_status")
        realtime_status = FRESH if gate_status == "FRESH" else STALE
        if realtime_status == STALE:
            notes.append(f"UPBIT_REALTIME_RUN_GATE_STATUS_NOT_FRESH:{gate_status}")

    overall_freshness = _worst_freshness([universe_status, market_evidence_status, realtime_status])

    # -- Regime (P1-CR-08) -- independent of universe/market-evidence
    #    freshness; always computed honestly. ---------------------------
    regime_payload = build_regime_snapshot(generated_at, component_rows)

    # -- Funnel: P5-08 then P5-09, reused verbatim ----------------------
    promotion_packet = None
    promotion_error = None
    eligibility_packet = None
    eligibility_error = None
    # "Used" requires both a consistent-generation market-evidence packet
    # AND an actual universe packet to run the funnel against -- reporting
    # true here when the funnel never even attempted to run (universe
    # missing) would misrepresent what this build actually did.
    market_evidence_used = market_evidence_status == FRESH and universe_packet is not None
    market_evidence_by_market = (
        market_evidence_entry["record"].get("packets", {}) if (market_evidence_entry and market_evidence_used) else {}
    )

    if universe_packet is not None:
        try:
            promotion_packet = PROMOTION.build_promotion_packet(
                universe_packet, regime_payload, market_evidence_by_market, None,
                evaluation_as_of=universe_packet["evaluation_as_of"],
            )
        except PROMOTION.CryptoCandidatePromotionError as exc:
            promotion_error = str(exc)
            notes.append(f"P5_08_PROMOTION_FUNNEL_UNAVAILABLE:{promotion_error}")
        if promotion_packet is not None:
            try:
                eligibility_packet = ELIGIBILITY.build_eligibility_packet(
                    promotion_packet, evaluation_as_of=universe_packet["evaluation_as_of"],
                )
            except ELIGIBILITY.CryptoPaperBuyEligibilityError as exc:
                eligibility_error = str(exc)
                notes.append(f"P5_09_ELIGIBILITY_FUNNEL_UNAVAILABLE:{eligibility_error}")
    else:
        notes.append("PROMOTION_FUNNEL_NOT_ATTEMPTED:UPBIT_UNIVERSE_PACKET_MISSING")

    eligibility_by_market = (
        {row["market"]: row for row in eligibility_packet["candidates"]} if eligibility_packet else {}
    )

    candidates = []
    if promotion_packet is not None:
        for row in promotion_packet["candidates"]:
            market = row["market"]
            elig_row = eligibility_by_market.get(market)
            if elig_row is not None:
                effective_state = elig_row["eligibility_state"]
                effective_reason = elig_row["eligibility_reason"]
            else:
                effective_state = row["promotion_state"]
                effective_reason = row["promotion_reason"]
            capped = cap_state_for_freshness(effective_state, effective_reason, overall_freshness)
            effective_state = capped["state"]
            effective_reason = capped["reason"]
            freshness_capped = capped["capped"]
            freshness_cap_reason = capped["cap_reason"]
            candidates.append({
                "market": market,
                "canonical_asset_id": row.get("canonical_asset_id"),
                "p3_12_state": row.get("p3_12_state"),
                "state": effective_state,
                "reason": effective_reason,
                "freshness_capped": freshness_capped,
                "freshness_cap_reason": freshness_cap_reason,
                "p5_08": {
                    "promotion_state": row["promotion_state"],
                    "promotion_reason": row["promotion_reason"],
                    "criteria": row["criteria"],
                },
                "p5_09": (
                    {
                        "eligibility_state": elig_row["eligibility_state"],
                        "eligibility_reason": elig_row["eligibility_reason"],
                        "criteria": elig_row["criteria"],
                        "order_draft": elig_row["order_draft"],
                    }
                    if elig_row is not None else None
                ),
                "authority": _authority_block(),
            })

    observation_pool_count = universe_packet["summary"]["observation_pool_count"] if universe_packet else 0
    tradeable_universe_count = (
        universe_packet["summary"]["tradeable_universe_count"] + universe_packet["summary"]["paper_eligible_count"]
        if universe_packet else 0
    )
    focused_review_count = promotion_packet["summary"]["focused_review_count"] if promotion_packet else 0
    paper_ready_count = sum(1 for row in candidates if row["state"] == "PAPER_BUY_ELIGIBLE")

    funnel_counts = {
        "observation_pool_count": observation_pool_count,
        "tradeable_universe_count": tradeable_universe_count,
        "focused_review_count": focused_review_count,
        "paper_ready_count": paper_ready_count,
    }

    # -- generation_id: deterministic identity of "this exact combination
    #    of input evidence" -- never wall-clock. -----------------------
    generation_basis = {
        "source_commit": source_commit,
        "universe": (
            {"date": universe_entry["date"], "payload_sha256": universe_packet.get("payload_sha256")}
            if universe_entry else None
        ),
        "market_evidence": (
            {"date": market_evidence_entry["date"], "payload_sha256": market_evidence_entry["record"].get("payload_sha256")}
            if market_evidence_entry else None
        ),
        "realtime": (
            {
                "date": realtime_entry["date"],
                "source_sha256": realtime_entry["record"].get("source_sha256"),
            }
            if realtime_entry else None
        ),
        "regime_axis_snapshot_sha256": payload_sha256(regime_payload),
    }
    generation_id = payload_sha256(generation_basis)
    if not SHA256_RE.fullmatch(generation_id):
        raise CryptoPaperDecisionSnapshotError("GENERATION_ID_INVALID")

    duplicate_guard_key = f"CRYPTO-PAPER-DECISION-{capture_date.replace('-', '')}-{capture_hhmm}-{generation_id[:24].upper()}"
    if not TOKEN_RE.fullmatch(duplicate_guard_key):
        raise CryptoPaperDecisionSnapshotError("DUPLICATE_GUARD_KEY_FORMAT_INVALID")

    authority = _authority_block()
    _require_all_false(authority)

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "capture_date": capture_date,
        "capture_hhmm": capture_hhmm,
        "source_commit": source_commit,
        "generation_id": generation_id,
        "duplicate_guard_key": duplicate_guard_key,
        "source_refs": source_refs,
        "upbit_universe_snapshot_identity": {
            "date": universe_date,
            "payload_sha256": universe_packet.get("payload_sha256") if universe_packet else None,
        },
        "finalized_candle_attestation": finalized_candle_attestation(
            market_evidence_entry, used_in_promotion=market_evidence_used,
        ),
        "crypto_regime_five_axis": crypto_regime_five_axis(regime_payload),
        "funnel_counts": funnel_counts,
        "candidates": candidates,
        "freshness_status": {
            "upbit_universe": universe_status,
            "market_evidence": market_evidence_status,
            "realtime": realtime_status,
            "overall": overall_freshness,
        },
        "authority": authority,
        "previous_state_reference": previous_entry,
        "derivation_notes": notes,
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


# ---------------------------------------------------------------------------
# I/O layer -- discovery, idempotent atomic write, GITHUB_OUTPUT, CLI.
# Mirrors .github/scripts/upbit_universe_populate.py::populate exactly.
# ---------------------------------------------------------------------------

class PopulationError(CryptoPaperDecisionSnapshotError):
    pass


def output_path(capture_date: str, capture_hhmm: str, generation_id: str, output_root: Path = OUTPUT_ROOT) -> Path:
    return Path(output_root) / capture_date / capture_hhmm / generation_id / "packet.json"


def populate(
    *,
    generated_at: str,
    source_commit: str | None = None,
    universe_data_root: Path = UNIVERSE_DATA_ROOT,
    market_evidence_data_root: Path = MARKET_EVIDENCE_DATA_ROOT,
    realtime_evidence_root: Path = REALTIME_EVIDENCE_ROOT,
    realtime_run_path: Path | None = None,
    output_root: Path = OUTPUT_ROOT,
) -> dict:
    resolved_source_commit = resolve_source_commit(source_commit)
    universe_entry = find_latest_universe_packet(universe_data_root)
    market_evidence_entry = find_latest_market_evidence_packet(market_evidence_data_root)
    if realtime_run_path is not None:
        realtime_run_path = Path(realtime_run_path)
        realtime_entry = (
            {"date": realtime_run_path.parent.name, "path": realtime_run_path, "record": _read_json(realtime_run_path)}
            if realtime_run_path.is_file() else None
        )
    else:
        realtime_entry = find_latest_realtime_run(realtime_evidence_root)

    capture_date = generated_at[:10]
    capture_hhmm = generated_at[11:13] + generated_at[14:16]
    previous_entry = find_previous_packet(output_root, capture_date, capture_hhmm)

    record = build_snapshot(
        generated_at=generated_at,
        source_commit=resolved_source_commit,
        universe_entry=universe_entry,
        market_evidence_entry=market_evidence_entry,
        realtime_entry=realtime_entry,
        previous_entry=previous_entry,
    )

    target = output_path(record["capture_date"], record["capture_hhmm"], record["generation_id"], output_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationError(f"EXISTING_PACKET_UNREADABLE:{target}:{exc}") from exc
        if existing != record:
            raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{target}")
        return {
            "outcome": "verified_existing", "reason": None, "path": str(target),
            "payload_sha256": record["payload_sha256"], "generation_id": record["generation_id"],
            "record": record,
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "outcome": "populated", "reason": None, "path": str(target),
        "payload_sha256": record["payload_sha256"], "generation_id": record["generation_id"],
        "record": record,
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    single_line = lambda value: (value or "").replace("\n", " ").replace("\r", " ")
    lines = [
        f"outcome={single_line(result.get('outcome'))}",
        f"reason={single_line(result.get('reason'))}",
        f"path={single_line(result.get('path'))}",
        f"payload_sha256={single_line(result.get('payload_sha256'))}",
        f"generation_id={single_line(result.get('generation_id'))}",
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-at", required=True, help="This workflow run's observed UTC capture instant")
    parser.add_argument("--source-commit", default=None, help="Full 40-char git SHA (default: git rev-parse HEAD)")
    parser.add_argument("--universe-data-root", type=Path, default=UNIVERSE_DATA_ROOT)
    parser.add_argument("--market-evidence-data-root", type=Path, default=MARKET_EVIDENCE_DATA_ROOT)
    parser.add_argument("--realtime-evidence-root", type=Path, default=REALTIME_EVIDENCE_ROOT)
    parser.add_argument("--realtime-run-path", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(
            generated_at=args.generated_at,
            source_commit=args.source_commit,
            universe_data_root=args.universe_data_root,
            market_evidence_data_root=args.market_evidence_data_root,
            realtime_evidence_root=args.realtime_evidence_root,
            realtime_run_path=args.realtime_run_path,
            output_root=args.output_root,
        )
    except CryptoPaperDecisionSnapshotError as exc:
        _write_github_output({"outcome": "failed", "reason": str(exc), "path": None, "payload_sha256": None, "generation_id": None})
        print(f"Crypto PAPER decision snapshot failed: {exc}")
        return 1
    _write_github_output(result)
    record = result["record"]
    print(json.dumps({
        "outcome": result["outcome"],
        "path": result["path"],
        "payload_sha256": result["payload_sha256"],
        "generation_id": result["generation_id"],
        "freshness_status": record["freshness_status"],
        "funnel_counts": record["funnel_counts"],
        "derivation_notes": record["derivation_notes"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
