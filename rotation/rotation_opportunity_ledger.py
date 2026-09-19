#!/usr/bin/env python3
"""PAPER entry opportunity ledger (RULE.ENTRY.PAPER_BASELINE_B.V1, user-ratified 2026-09-15).

Entry baseline B: a new buy is allowed when (1) the market state permits new
buys under allocation v2, (2) the sector/bucket is STRONG_CONFIRMED or
STRONG_HELD under RULE.ROTATION.*, (3) T2 candidate conditions pass, within
the session budget. EMA20 position, breakout, chase and ATR distance are
record-only. The ratification explicitly does not claim an entry edge.

This module records, per market and observation date, every sector/bucket
entity that is STRONG_CONFIRMED/HELD in the rotation confirmation replay,
together with:

* the point-in-time market state (earliest committed PAPER reference
  generation for that market and as-of date) and the allocation v2 new-buy
  verdict;
* the T2 status (PENDING -- no T2 candidate output exists in this repository);
* record-only features where computable from committed evidence (US SPDR
  proxy from the earliest committed IEX bars capture observation containing
  the session, bars up to the session only -- a capture day that a later
  same-day capture replaces in place keeps the observation it was recorded
  with, see ``USBars``);
* forward-return tracking fields left null for a later scorecard job.

A day is written only once it is final (its own or a later market as-of has a
committed PAPER reference), so packets are deterministic and append-only.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal
import glob
import gzip
import importlib.util
import json
from pathlib import Path
import sys
from typing import Optional


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_RELATIVE_PATH = "config/paper_entry_opportunity_ledger_v1.json"
CONFIG_SCHEMA_VERSION = "paper_entry_opportunity_ledger_policy/1"
DAY_SCHEMA_VERSION = "paper_entry_opportunity_ledger_day/1"
EVIDENCE_RELATIVE_ROOT = "evidence/rotation/opportunity_ledger"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RC = _load("atlas_rotation_confirmation_for_opportunity_ledger", HERE / "rotation_confirmation.py")
OpportunityLedgerError = RC.RotationConfirmationError
_fail = RC._fail


def load_config(root: Path = ROOT) -> dict:
    root = Path(root)
    config = RC._read_json(root / CONFIG_RELATIVE_PATH)
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        _fail("OPPORTUNITY_CONFIG_SCHEMA_INVALID")
    entry = config["entry_rule"]
    record_path = root / entry["repo_path"]
    if not record_path.is_file() or RC.file_sha256(record_path) != entry["sha256"]:
        _fail("ENTRY_RATIFICATION_RECORD_SHA_MISMATCH")
    record = RC._read_json(record_path)
    if (
        record.get("id") != entry["record_id"]
        or record.get("rule_id") != entry["rule_id"]
        or (record.get("decision") or {}).get("status") != entry["status"]
    ):
        _fail("ENTRY_RATIFICATION_RECORD_MISMATCH")
    if config["t2"]["status"] != "PENDING":
        _fail("T2_STATUS_REQUIRES_REAL_T2_OUTPUT")
    if config["authority"].get("entry_edge_claimed") is not False:
        _fail("ENTRY_EDGE_CLAIM_FORBIDDEN")
    expected = {"RISK_ON": "PERMIT", "NEUTRAL": "PERMIT_SELECTIVE", "RISK_OFF": "DENY", "STRESS": "DENY", "UNKNOWN": "DENY"}
    if config["market_state"]["new_buys_by_market_state"] != expected:
        _fail("ALLOCATION_V2_NEW_BUY_TABLE_MISMATCH")
    return config


def entry_rule_ref(config: dict, role: str) -> dict:
    # TODO(rule-registry): switch to governance.rule_refs.make_rule_ref after the registry PR merges.
    return {
        "rule_id": config["entry_rule"]["rule_id"],
        "version": 1,
        "registry_sha256": None,
        "source_record_sha256": config["entry_rule"]["sha256"],
        "role": role,
    }


# ---------------------------------------------------------------------------
# Point-in-time market state
# ---------------------------------------------------------------------------

def market_states(config: dict, root: Path = ROOT) -> dict:
    """{market: {"by_as_of": {as_of: state}, "max_as_of": str|None}} from committed PAPER references."""
    root = Path(root)
    result = {}
    for path in sorted(glob.glob(str(root / config["market_state"]["source_root"] / "*" / "*" / "packet.json"))):
        path = Path(path)
        packet = RC._read_json(path)
        generated_at = packet.get("generated_at")
        if not isinstance(generated_at, str):
            continue
        for row in packet.get("markets") or []:
            market, as_of = row.get("market"), row.get("as_of_date")
            if market not in RC.MARKETS or not isinstance(as_of, str):
                continue
            entry = result.setdefault(market, {"by_as_of": {}, "max_as_of": None})
            candidate = (row.get("paper_reference") or {}).get("candidate_regime")
            state = {
                "candidate_regime": candidate if candidate in config["market_state"]["new_buys_by_market_state"] else "UNKNOWN",
                "runtime_regime": row.get("runtime_regime"),
                "classification_status": row.get("classification_status"),
                "source": {
                    "path": RC._relative(path, root),
                    "generated_at": generated_at,
                    "generation_id": packet.get("generation_id"),
                    "payload_sha256": packet.get("payload_sha256"),
                },
            }
            current = entry["by_as_of"].get(as_of)
            if current is None or (generated_at, state["source"]["path"]) < (current["source"]["generated_at"], current["source"]["path"]):
                entry["by_as_of"][as_of] = state
            if entry["max_as_of"] is None or as_of > entry["max_as_of"]:
                entry["max_as_of"] = as_of
    return result


# ---------------------------------------------------------------------------
# Record-only features (US SPDR proxy from committed IEX bars)
# ---------------------------------------------------------------------------

def _q(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), "f")


class USBars:
    """Committed IEX daily-bar observations, earliest capture first.

    ``evidence/free_market_data/raw/<capture day>/<file>`` is a compatibility
    address, not an append-only one: a second capture on the same UTC day
    replaces it in place. Scanning that path alone therefore silently rebinds
    an already recorded observation to newer bytes, which is what broke the
    determinism of the 2026-09-17 packet when the 2026-09-18 capture day was
    captured twice (01:41Z and 23:32Z).

    The capture collector retains every replaced response in the append-only
    content-addressed store under ``.../raw/alpaca/daily_bars/<response
    sha256>/`` and pins the pointer in each derived revision manifest, so the
    observations for a capture day are recovered from those manifests and
    ordered by their actual capture time. ``source.path`` stays the capture
    day's compatibility address -- that is where the observation was
    published -- and ``source.sha256`` pins the exact revision, which stays
    resolvable in the content-addressed store after the compatibility file is
    replaced.
    """

    DERIVED_ROOT = "evidence/free_market_data/derived"

    def __init__(self, config: dict, root: Path):
        cfg = config["record_only_features"]["US"]
        self.cfg = cfg
        self.root = Path(root)
        self.observations = self._observations()
        self._cache = {}

    def _observations(self) -> list:
        """[(capture_day, observed_at_utc, compat_relative_path, file)] in capture order."""
        cfg = self.cfg
        rows = []
        for compat in sorted(glob.glob(str(self.root / cfg["source_root"] / "*" / cfg["file_name"]))):
            compat = Path(compat)
            day = compat.parent.name
            if not day[:4].isdigit():
                continue
            relative = RC._relative(compat, self.root)
            seen, day_rows = set(), []
            for manifest in sorted(glob.glob(str(self.root / self.DERIVED_ROOT / day / "*" / "manifest.json"))) + [
                str(self.root / self.DERIVED_ROOT / day / "manifest.json")
            ]:
                pinned = self._pinned_daily_bars(Path(manifest))
                if pinned is None or pinned in seen:
                    continue
                seen.add(pinned)
                day_rows.append((day, pinned[0], relative, self.root / pinned[1]))
            # A capture day with no pinned revision was never replaced, so its
            # compatibility file still holds the bytes it was published with.
            rows.extend(sorted(day_rows) or [(day, "", relative, compat)])
        return rows

    def _pinned_daily_bars(self, manifest: Path) -> Optional[tuple]:
        """(observed_at_utc, relative raw path) a derived manifest pins, if it resolves."""
        try:
            packet = RC._read_json(manifest)
        except (OSError, ValueError):
            return None
        observed = packet.get("observed_at_utc")
        pointer = (packet.get("alpaca") or {}).get("daily_raw_evidence")
        if not isinstance(observed, str) or not isinstance(pointer, dict):
            return None
        raw_path = pointer.get("raw_path")
        if not isinstance(raw_path, str) or not (self.root / raw_path).is_file():
            return None
        return observed, raw_path

    def _load(self, path: Path) -> dict:
        if path not in self._cache:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                self._cache[path] = json.load(handle).get("responses") or {}
        return self._cache[path]

    def features(self, symbol: str, session: str) -> dict:
        for capture_day, _observed, relative, path in self.observations:
            if capture_day < session:
                continue
            bars = (self._load(path).get(symbol) or {}).get("bars") or []
            dated = [(str(bar.get("t", ""))[:10], bar) for bar in bars]
            if session not in {day for day, _ in dated}:
                continue
            upto = [bar for day, bar in dated if day <= session]
            return self._compute(upto, symbol, session, path, relative)
        return {"status": "UNKNOWN", "reason": "NO_COMMITTED_BARS_FOR_SESSION", "instrument": symbol}

    def _compute(self, bars: list, symbol: str, session: str, path: Path, relative: Optional[str] = None) -> dict:
        cfg = self.cfg
        period, lookback, atr_period = cfg["ema_period"], cfg["breakout_lookback_sessions"], cfg["atr_period"]
        base = {
            "instrument": symbol,
            "source": {
                "path": relative if relative is not None else RC._relative(path, self.root),
                "sha256": RC.file_sha256(path),
            },
            "session": session,
            "bars_used": len(bars),
        }
        if len(bars) < max(period, lookback, atr_period) + 1:
            return base | {"status": "UNKNOWN", "reason": "INSUFFICIENT_BARS"}
        closes = [Decimal(str(bar["c"])) for bar in bars]
        highs = [Decimal(str(bar["h"])) for bar in bars]
        lows = [Decimal(str(bar["l"])) for bar in bars]
        alpha = Decimal(2) / Decimal(period + 1)
        ema = sum(closes[:period]) / Decimal(period)
        for close in closes[period:]:
            ema = alpha * close + (Decimal(1) - alpha) * ema
        close = closes[-1]
        true_ranges = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(len(bars) - atr_period, len(bars))
        ]
        atr = sum(true_ranges) / Decimal(atr_period)
        prior_high = max(highs[-lookback - 1:-1])
        return base | {
            "status": "OBSERVED",
            "close": _q(close),
            "ema20": _q(ema),
            "ema20_position": "ABOVE" if close > ema else "BELOW" if close < ema else "AT",
            "close_vs_ema20_pct": _q((close / ema - 1) * 100),
            "breakout_20": close > prior_high,
            "prior_20_session_high": _q(prior_high),
            "prior_session_change_pct": _q((close / closes[-2] - 1) * 100),
            "atr14": _q(atr),
            "atr_distance_from_ema20": _q((close - ema) / atr) if atr > 0 else None,
            "rise_since_signal_pct": None,
            "feed_caveat": cfg["feed_caveat"],
        }


# ---------------------------------------------------------------------------
# Day packets
# ---------------------------------------------------------------------------

def _forward_tracking(config: dict, market: str) -> dict:
    tracking = config["forward_tracking"]
    return {
        "status": "NOT_YET_FILLED",
        "filled_by": tracking["filled_by"],
        "entry_reference_price": None,
        "horizon_unit": tracking["horizon_unit"][market],
        "forward_returns": {str(h): None for h in tracking["horizons"][market]},
        "excess_forward_returns": {str(h): None for h in tracking["horizons"][market]},
        "excess_return_baseline_status": tracking["excess_return_baseline_status"],
        "bought": None,
    }


def build_market_days(market: str, root: Path = ROOT, config: Optional[dict] = None,
                      policy: Optional[dict] = None) -> list:
    root = Path(root)
    config = load_config(root) if config is None else config
    policy = RC.load_policy(root) if policy is None else policy
    confirmation = RC.build_market(market, root, policy)
    states = market_states(config, root).get(market, {"by_as_of": {}, "max_as_of": None})
    bars = USBars(config, root) if market == "US" else None
    table = config["market_state"]["new_buys_by_market_state"]
    days = []
    for packet in confirmation:
        as_of = packet["as_of_date"]
        state = states["by_as_of"].get(as_of)
        if state is None and (states["max_as_of"] is None or states["max_as_of"] <= as_of):
            continue  # not final yet: a PAPER reference for this as-of may still be committed
        if state is None:
            market_state = {"status": "UNKNOWN", "reason": "NO_PAPER_REFERENCE_FOR_AS_OF", "candidate_regime": "UNKNOWN",
                            "runtime_regime": None, "classification_status": None, "source": None}
        else:
            market_state = {"status": "OBSERVED", "reason": None} | copy.deepcopy(state)
        verdict = table[market_state["candidate_regime"]]
        market_state["new_buy_verdict"] = verdict
        opportunities = []
        for gate in packet["entry_gate_view"]:
            if not gate["decision_eligible"]:
                continue
            blocked = verdict == "DENY"
            if market == "US":
                features = bars.features(gate["entity_id"], as_of)
            else:
                features = copy.deepcopy(config["record_only_features"][market])
            refs = gate["rule_refs"] + [entry_rule_ref(config, "BLOCKED_BY" if blocked else "APPLIED")]
            row = {
                "market": market,
                "signal_as_of_date": as_of,
                "scope_id": gate["scope_id"],
                "entity_id": gate["entity_id"],
                "label": gate["label"],
                "sector_state": gate["state"],
                "state_since_date": gate["state_since_date"],
                "observations_in_state": gate["observations_in_state"],
                "calendar_days_in_state": gate["calendar_days_in_state"],
                "market_state_new_buy": verdict,
                "t2": copy.deepcopy(config["t2"]),
                "eligibility": "BLOCKED_BY_MARKET_STATE" if blocked else "ELIGIBLE_PENDING_T2",
                "instrument_candidates_status": "PENDING_INSTRUMENT_LEVEL_T1_T2_OUTPUT",
                "record_only_features": features,
                "forward_tracking": _forward_tracking(config, market),
                "rule_refs": sorted(refs, key=lambda r: (r["rule_id"], r["role"])),
            }
            if gate.get("coverage_recalculation"):
                # RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1: scorecard rows derived
                # from recalculated observations carry the '재계산' mark.
                row["coverage_recalculation"] = copy.deepcopy(gate["coverage_recalculation"])
            row["opportunity_id"] = RC.payload_sha256({
                "market": market, "as_of": as_of, "scope_id": gate["scope_id"], "entity_id": gate["entity_id"],
                "entry_rule_sha256": config["entry_rule"]["sha256"],
                "confirmation_payload_sha256": packet["payload_sha256"],
            })
            opportunities.append(row)
        day = {
            "schema_version": DAY_SCHEMA_VERSION,
            "market": market,
            "as_of_date": as_of,
            "entry_rule": copy.deepcopy(config["entry_rule"]),
            "evidence_phase": packet["evidence_phase"],
            "confirmation": {
                "path": RC._relative(RC.evidence_path(root, market, as_of), root),
                "payload_sha256": packet["payload_sha256"],
                "observation_status": packet["observation"]["status"],
                "unknown_reason": packet["observation"]["unknown_reason"],
            },
            "market_state": market_state,
            "market_state_pending_definitions": list(config["market_state"]["pending_definitions"]),
            "opportunities": opportunities,
            "counts": {
                "opportunities": len(opportunities),
                "eligible_pending_t2": sum(1 for o in opportunities if o["eligibility"] == "ELIGIBLE_PENDING_T2"),
                "blocked_by_market_state": sum(1 for o in opportunities if o["eligibility"] == "BLOCKED_BY_MARKET_STATE"),
            },
            "authority": copy.deepcopy(config["authority"]),
        }
        if "coverage_recalculation" in packet["observation"]:
            day["confirmation"]["coverage_recalculation"] = copy.deepcopy(packet["observation"]["coverage_recalculation"])
        day["payload_sha256"] = RC.payload_sha256(day)
        days.append(day)
    return days


def day_path(root: Path, market: str, as_of: str) -> Path:
    return Path(root) / EVIDENCE_RELATIVE_ROOT / market / as_of / "packet.json"


def latest_path(root: Path, market: str) -> Path:
    return Path(root) / f"data/latest_rotation_opportunity_ledger_{market.lower()}.json"


def write_market_days(market: str, days: list, root: Path = ROOT) -> dict:
    root = Path(root)
    conflicts, new = [], []
    for day in days:
        path = day_path(root, market, day["as_of_date"])
        data = RC.render_json(day)
        if path.exists():
            if path.read_bytes() != data:
                conflicts.append(RC._relative(path, root))
        else:
            new.append((path, data))
    if conflicts:
        _fail("APPEND_ONLY_EVIDENCE_CONFLICT", ",".join(conflicts))
    for path, data in new:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if days:
        latest_path(root, market).parent.mkdir(parents=True, exist_ok=True)
        latest_path(root, market).write_bytes(RC.render_json(days[-1]))
    return {"market": market, "written": [RC._relative(p, root) for p, _ in new], "day_count": len(days)}


def verify_market_days(market: str, days: list, root: Path = ROOT) -> list:
    problems = []
    for day in days:
        path = day_path(root, market, day["as_of_date"])
        if not path.exists():
            problems.append(f"MISSING:{RC._relative(path, root)}")
        elif path.read_bytes() != RC.render_json(day):
            problems.append(f"MISMATCH:{RC._relative(path, root)}")
    return problems


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--market", action="append", choices=RC.MARKETS)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.root)
        policy = RC.load_policy(args.root)
    except OpportunityLedgerError as exc:
        print(f"Opportunity ledger failed: {exc}", file=sys.stderr)
        return 2
    # Per-market isolation: one market's failure never blocks the others.
    problems, failed = [], []
    for market in args.market or RC.MARKETS:
        try:
            days = build_market_days(market, args.root, config, policy)
            if args.command == "build":
                if args.write:
                    print(json.dumps(write_market_days(market, days, args.root), ensure_ascii=False))
                latest = days[-1] if days else None
                print(json.dumps({"market": market, "as_of_date": latest and latest["as_of_date"],
                                  "counts": latest and latest["counts"]}, ensure_ascii=False))
            else:
                problems += verify_market_days(market, days, args.root)
        except OpportunityLedgerError as exc:
            failed.append(market)
            print(f"Opportunity ledger failed for {market}: {exc}", file=sys.stderr)
    for problem in problems:
        print(problem)
    if failed:
        return 3
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(run())
