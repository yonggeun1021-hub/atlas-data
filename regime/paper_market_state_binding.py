#!/usr/bin/env python3
"""The PAPER market-state source binding (RATIFICATION_MARKET_STATE_SOURCE_BINDING).

Before this module existed the repository had two holes recorded in its own
files:

* ``regime/paper_regime_reference.py`` emitted ``"runtime_regime": "UNKNOWN"``
  as a hardcoded literal for every market, so no configuration could change it.
* No production code called
  ``portfolio/paper_allocation_envelope.allocation_envelope(market_states=...)``
  at all -- only tests did, and they passed the string ``"UNKNOWN"``.
  ``validation/paper_benchmark_nav_series.read_market_state``'s docstring and
  ``config/rule_registry_v1.json``'s accepted interpretations both name the
  missing consumer wiring and the governance decision that had to authorize it.

This module closes both.  It is the single place where a market state enters
PAPER sizing, and it is **fail closed per market**: a market that is absent
from ``config/paper_market_state_source_binding_v1.json``, or whose ``adopted``
flag is not exactly ``True``, is read as ``UNKNOWN`` no matter what the
reference producer classified.  ``UNKNOWN`` costs that market its cap (0.50 of
base from the second consecutive cycle) and forbids new buys, which is the safe
direction.

It grants a sizing **input** and nothing else.  Every capital authority flag
stays false, here and in the binding contract.

Pure and offline: reads committed bytes, calls no provider, reads no clock of
its own (callers pass ``decision_at_utc``).
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BINDING_RELATIVE = "config/paper_market_state_source_binding_v1.json"
BINDING_CONTRACT_VERSION = "paper_market_state_source_binding/v1"
BINDING_RATIFIED_STATUS = "USER_RATIFIED"
REFERENCE_RELATIVE = "data/latest_paper_regime_reference.json"
REFERENCE_SCHEMA_VERSION = "paper_regime_reference/v2"
REFERENCE_EVIDENCE_RELATIVE = "evidence/regime/paper_reference"
MARKETS = ("CRYPTO", "KR", "US")
STATES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", "UNKNOWN")
CLOSED_STATE = "UNKNOWN"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Authority flags this module refuses to see set anywhere in the binding.
FORBIDDEN_AUTHORITY = (
    "action_authorized",
    "buy_authorized",
    "capital_authorized",
    "cross_market_date_coercion_authorized",
    "exchange_order_authorized",
    "order_authorized",
    "position_size_authorized",
    "production_authorized",
    "real_capital_authorized",
    "real_trading",
    "stage_authorized",
    "strategy_authorized",
    "target_weight_authorized",
    "trading_authorized",
)
OBSERVATION_FIELDS = (
    "state", "multiplier", "observed_at", "available_at",
    "source_ref", "source_sha256", "source_schema_version",
)


class PaperMarketStateBindingError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise PaperMarketStateBindingError(f"{code}:{detail}" if detail else code)


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PaperMarketStateBindingError(f"SOURCE_MISSING:{path}") from exc


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperMarketStateBindingError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


# ---------------------------------------------------------------------------
# The binding contract
# ---------------------------------------------------------------------------

def _closed(binding: dict, reason: str) -> dict:
    """The same contract with every market closed and the reason recorded."""
    closed = copy.deepcopy(binding)
    markets = closed.get("markets")
    if isinstance(markets, dict):
        for row in markets.values():
            if isinstance(row, dict):
                row["adopted"] = False
                row["blocked_reason"] = reason
    closed["forced_closed_reason"] = reason
    return closed


def load_binding(root: Path = ROOT) -> dict:
    """Validated binding contract.  Any defect is a hard failure, not a default.

    A *missing market* is a closed market (that is the fail-closed default).
    A *malformed contract* is a failure, because silently reading a broken
    authority file as "all closed" would hide the breakage.
    """
    path = Path(root) / BINDING_RELATIVE
    binding = _read_json(path, "BINDING_INVALID")
    if binding.get("contract_version") != BINDING_CONTRACT_VERSION:
        fail("BINDING_CONTRACT_VERSION_INVALID")
    if binding.get("status") != BINDING_RATIFIED_STATUS:
        fail("BINDING_NOT_RATIFIED")
    if binding.get("closed_state") != CLOSED_STATE:
        fail("BINDING_CLOSED_STATE_INVALID")
    if list(binding.get("allowed_states") or []) != list(STATES):
        fail("BINDING_ALLOWED_STATES_INVALID")
    authority = binding.get("authority")
    if not isinstance(authority, dict):
        fail("BINDING_AUTHORITY_INVALID")
    if authority.get("paper_market_state_sizing_input_authorized") is not True:
        fail("BINDING_SIZING_INPUT_NOT_AUTHORIZED")
    for key in FORBIDDEN_AUTHORITY:
        if authority.get(key) is not False:
            fail("BINDING_AUTHORITY_ESCALATION", key)
    for key, value in authority.items():
        if key.endswith("_authorized") and value is not False and key != (
            "paper_market_state_sizing_input_authorized"
        ):
            fail("BINDING_AUTHORITY_ESCALATION", key)
    record = binding.get("authority_record")
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        fail("BINDING_AUTHORITY_RECORD_MISSING")
    if SHA256.fullmatch(str(record.get("sha256"))) is None:
        fail("BINDING_AUTHORITY_RECORD_SHA_INVALID")
    authority_path = Path(root) / record["path"]
    if not authority_path.is_file():
        # The user ratification this binding cites is not materialized in this
        # root.  Synthetic test roots and frozen replay closures carry config/
        # without evidence/, and a root that cannot show the authority cannot be
        # allowed to act on it -- so every market closes.  (A record that IS
        # present but does not hash is a tamper, handled below as a hard fail.)
        return _closed(binding, "AUTHORITY_RECORD_NOT_MATERIALIZED_IN_ROOT")
    if file_sha256(authority_path) != record["sha256"]:
        fail("BINDING_AUTHORITY_RECORD_SHA_MISMATCH")
    source = binding.get("source")
    if not isinstance(source, dict) or source.get("artifact") != REFERENCE_RELATIVE:
        fail("BINDING_SOURCE_INVALID")
    if source.get("schema_version") != REFERENCE_SCHEMA_VERSION:
        fail("BINDING_SOURCE_SCHEMA_INVALID")
    markets = binding.get("markets")
    if not isinstance(markets, dict):
        fail("BINDING_MARKETS_INVALID")
    for market, row in markets.items():
        if market not in MARKETS:
            fail("BINDING_MARKET_UNKNOWN", market)
        if not isinstance(row, dict) or row.get("adopted") not in (True, False):
            fail("BINDING_MARKET_ADOPTED_INVALID", market)
        if row["adopted"] is False and not isinstance(row.get("blocked_reason"), str):
            fail("BINDING_MARKET_BLOCK_REASON_MISSING", market)
    return copy.deepcopy(binding)


def adopted_markets(binding: dict) -> frozenset:
    """Markets whose reference judgement may be consumed.  Default: none."""
    markets = binding.get("markets")
    if not isinstance(markets, dict):
        return frozenset()
    return frozenset(
        market for market in MARKETS
        if isinstance(markets.get(market), dict)
        and markets[market].get("adopted") is True
    )


def load_adopted_markets(root: Path = ROOT) -> frozenset:
    """Convenience loader.  A missing binding file closes every market."""
    if not (Path(root) / BINDING_RELATIVE).is_file():
        return frozenset()
    return adopted_markets(load_binding(root))


# ---------------------------------------------------------------------------
# The derivation the reference producer uses instead of a hardcoded literal
# ---------------------------------------------------------------------------

def runtime_regime(market: str, candidate_regime: object, adopted: frozenset) -> str:
    """Per-market runtime state derived from the reference judgement.

    This replaces the two hardcoded ``"runtime_regime": "UNKNOWN"`` literals that
    used to sit in ``regime/paper_regime_reference.py``.  Markets are
    independent: one market being closed never closes another, and one market
    being open never opens another.
    """
    if market not in MARKETS:
        fail("MARKET_UNKNOWN", str(market))
    if market not in adopted:
        return CLOSED_STATE
    if candidate_regime not in STATES:
        return CLOSED_STATE
    return candidate_regime


# ---------------------------------------------------------------------------
# Reference artifact -> market_states
# ---------------------------------------------------------------------------

def _reference_rows(packet: dict) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != REFERENCE_SCHEMA_VERSION:
        fail("REFERENCE_SCHEMA_INVALID")
    rows = {}
    for row in packet.get("markets") or []:
        if not isinstance(row, dict) or row.get("market") not in MARKETS:
            fail("REFERENCE_MARKET_ROW_INVALID")
        rows[row["market"]] = row
    if set(rows) != set(MARKETS):
        fail("REFERENCE_MARKETS_INCOMPLETE")
    return rows


def market_states(packet: dict, adopted: frozenset) -> dict:
    """The ``market_states`` argument of ``allocation_envelope()``."""
    rows = _reference_rows(packet)
    states = {}
    for market in MARKETS:
        reference = rows[market].get("paper_reference")
        candidate = reference.get("candidate_regime") if isinstance(reference, dict) else None
        states[market] = runtime_regime(market, candidate, adopted)
    return states


def _latest_packet_per_date(root: Path) -> list:
    """Committed reference packets, one per evidence date, newest last.

    Selection rule (recorded so it is auditable rather than assumed): within an
    evidence date directory the packet with the greatest ``generated_at`` is the
    runtime input for that date.  Ties fall back to the generation id so the
    result is deterministic.
    """
    base = Path(root) / REFERENCE_EVIDENCE_RELATIVE
    if not base.is_dir():
        return []
    chosen = []
    for date_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if ISO_DATE.fullmatch(date_dir.name) is None:
            continue
        best = None
        for generation_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            candidate = generation_dir / "packet.json"
            if not candidate.is_file():
                continue
            packet = _read_json(candidate, "REFERENCE_EVIDENCE_INVALID")
            key = (str(packet.get("generated_at")), generation_dir.name)
            if best is None or key > best[0]:
                best = (key, date_dir.name, packet)
        if best is not None:
            chosen.append({"evidence_date": best[1], "packet": best[2]})
    return chosen


def state_history(root: Path = ROOT, adopted: frozenset | None = None) -> list:
    """Per-date gated market states over the committed reference evidence."""
    if adopted is None:
        adopted = load_adopted_markets(root)
    return [
        {"evidence_date": row["evidence_date"], "market_states": market_states(row["packet"], adopted)}
        for row in _latest_packet_per_date(root)
    ]


def unknown_streaks(history: list, states: dict) -> dict:
    """Trailing consecutive UNKNOWN cycles per market, including ``states``.

    ``portfolio.paper_allocation_envelope.effective_market_state`` requires the
    streak to be > 0 exactly when the state is UNKNOWN, so the streak is counted
    over the finalized cycles up to and including the cycle being sized.
    """
    streaks = {}
    for market in MARKETS:
        if states[market] != CLOSED_STATE:
            streaks[market] = 0
            continue
        streak = 1
        for row in reversed(history):
            if row["market_states"].get(market) == CLOSED_STATE:
                streak += 1
            else:
                break
        streaks[market] = streak
    return streaks


# ---------------------------------------------------------------------------
# The production wiring: market_states -> allocation_envelope()
# ---------------------------------------------------------------------------

def envelope_from_reference(
    *,
    decision_at_utc: str,
    root: Path = ROOT,
    packet: dict | None = None,
    history: list | None = None,
    drawdown_stage: str = "NONE",
    cross_market_flow_validated: bool = False,
    evidence_mode: str = "NATURAL",
) -> dict:
    """Size one PAPER decision cycle from the ratified market-state source.

    This is the production call that did not exist before: it fills
    ``market_states=`` from the reference judgement, gated per market by the
    ratified binding, and hands it to the ratified envelope.  The envelope, not
    this module, owns every number.
    """
    from portfolio import paper_allocation_envelope as ENV
    from portfolio import paper_execution_core as CORE

    adopted = load_adopted_markets(root)
    if packet is None:
        packet = _read_json(Path(root) / REFERENCE_RELATIVE, "REFERENCE_INVALID")
    states = market_states(packet, adopted)
    if history is None:
        history = state_history(root, adopted)
        # The cycle being sized must not also be counted as a prior cycle.
        rows = _reference_rows(packet)
        current_dates = {rows[m].get("as_of_date") for m in MARKETS}
        history = [
            row for row in history
            if row["evidence_date"] not in current_dates
            and row["market_states"] != states
        ]
    streaks = unknown_streaks(history, states)
    core = CORE.load_core(root=Path(root))
    record = ENV.allocation_envelope(
        core,
        decision_at_utc=decision_at_utc,
        market_states=states,
        unknown_streaks=streaks,
        drawdown_stage=drawdown_stage,
        cross_market_flow_validated=cross_market_flow_validated,
        evidence_mode=evidence_mode,
    )
    return {
        "schema_version": "paper_market_state_bound_envelope/1",
        "binding": {
            "path": BINDING_RELATIVE,
            "sha256": file_sha256(Path(root) / BINDING_RELATIVE),
            "adopted_markets": sorted(adopted),
            "closed_markets": sorted(set(MARKETS) - adopted),
        },
        "source": {
            "path": REFERENCE_RELATIVE,
            "sha256": file_sha256(Path(root) / REFERENCE_RELATIVE),
            "schema_version": REFERENCE_SCHEMA_VERSION,
            "generation_id": packet.get("generation_id"),
            "payload_sha256": packet.get("payload_sha256"),
        },
        "market_states": states,
        "unknown_streaks": streaks,
        "envelope": record,
        "authority": {
            "paper_market_state_sizing_input_authorized": True,
            "real_trading": False,
            "capital_authorized": False,
            "order_authorized": False,
            "buy_authorized": False,
            "trading_authorized": False,
            "position_size_authorized": False,
            "target_weight_authorized": False,
            "exchange_order_authorized": False,
            "real_capital_authorized": False,
        },
    }


def market_state_observations(
    *,
    root: Path = ROOT,
    packet: dict | None = None,
    observed_at: str,
    available_at: str,
) -> dict:
    """``validation.paper_benchmark_nav_series.read_market_state`` observations.

    That module deliberately opens no path of its own and asked for exactly one
    state source in the system.  This is that source, in its contract shape.
    """
    from portfolio import paper_execution_core as CORE

    adopted = load_adopted_markets(root)
    if packet is None:
        packet = _read_json(Path(root) / REFERENCE_RELATIVE, "REFERENCE_INVALID")
    states = market_states(packet, adopted)
    core = CORE.load_core(root=Path(root))
    table = core.param("state_multipliers")
    source_sha = file_sha256(Path(root) / REFERENCE_RELATIVE)
    observations = {}
    for market in MARKETS:
        state = states[market]
        observations[market] = {
            "state": state,
            "multiplier": table[state],
            "observed_at": observed_at,
            "available_at": available_at,
            "source_ref": REFERENCE_RELATIVE,
            "source_sha256": source_sha,
            "source_schema_version": REFERENCE_SCHEMA_VERSION,
        }
        if set(observations[market]) != set(OBSERVATION_FIELDS):
            fail("OBSERVATION_FIELDS_MISMATCH", market)
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-at-utc", default=None)
    parser.add_argument("--history", action="store_true",
                        help="print the per-date gated state history instead")
    args = parser.parse_args()
    if args.history:
        print(json.dumps(state_history(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    decision_at = args.decision_at_utc or dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    print(json.dumps(
        envelope_from_reference(decision_at_utc=decision_at),
        ensure_ascii=False, indent=2, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
