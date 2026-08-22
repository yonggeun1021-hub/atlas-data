#!/usr/bin/env python3
"""Signal Replay Ledger (deliverable 1): date-by-date, per-subject entry of
what was detectable at decision_date, what the existing and proposed
rulesets would have done with it, and forward return/MFE/MAE.

Every entry's `entry_hash` is a deterministic sha256 of its own canonical
JSON -- same inputs always produce the same ledger, byte for byte, with no
wall-clock or random component (satisfies the "deterministic output" hard
constraint).
"""
from __future__ import annotations

import dataclasses

from replay import action_conversion_gate as acg
from replay import existing_ruleset_baseline as erb
from replay import root_cause as rc
from replay.forward_metrics import compute_forward_metrics
from replay.opportunity_trigger import canonical_json, payload_sha256
from replay.price_series import PriceSeries
from replay.trigger_engine import detect_all


def _invalidation_price(series: PriceSeries, decision_date: str) -> float | None:
    live_dates = series.live_trading_dates_at_or_before(decision_date)
    if len(live_dates) < 10:
        return None
    lows = [series.row_on(d)["low"] for d in live_dates[-10:]]
    return min(lows)


def build_entry(series: PriceSeries, decision_date: str, source: str, evidence_sha: str,
                 peers: dict[str, PriceSeries] | None = None) -> dict:
    live_dates = series.live_trading_dates_at_or_before(decision_date)
    data_available = bool(live_dates)
    evaluation_date = live_dates[-1] if live_dates else None
    evaluation_lag_days = None
    if evaluation_date is not None:
        import datetime as _dt
        evaluation_lag_days = (
            _dt.date.fromisoformat(decision_date) - _dt.date.fromisoformat(evaluation_date)
        ).days

    triggers = detect_all(series, decision_date, source, evidence_sha, peers=peers) if data_available else []
    entry_price = series.close_on(evaluation_date) if data_available else None
    invalidation_price = _invalidation_price(series, decision_date) if data_available else None

    gate_result = acg.evaluate(series.subject, decision_date, triggers, entry_price, invalidation_price)
    existing = erb.existing_ruleset_action_for(bool(triggers))

    forward = compute_forward_metrics(series, decision_date)

    entry = {
        "decision_date": decision_date,
        "subject": series.subject,
        "data_available": data_available,
        "evaluation_date": evaluation_date,
        "evaluation_lag_days": evaluation_lag_days,
        "source": source,
        "evidence_sha256": evidence_sha,
        "triggers": [t.to_dict() for t in triggers],
        "existing_ruleset": existing,
        "proposed_ruleset": dataclasses.asdict(gate_result),
        "forward_metrics": forward,
    }
    entry["entry_hash"] = payload_sha256(entry)
    return entry


def classify_gap(entry: dict, no_position_rule_active: bool = True,
                  decision_latency_days: int | None = "USE_ENTRY", in_universe: bool = True) -> str | None:
    """Returns a root-cause code if this entry represents a miss/action-gap
    under the PROPOSED ruleset, else None (no gap: either action was taken,
    which never happens anywhere in this system since capital is always 0,
    or there genuinely was nothing to act on).

    `decision_latency_days` defaults to the entry's own real
    `evaluation_lag_days` (how many days the collector's own finalization
    lag put between decision_date and the freshest live-known data) --
    pass an explicit value to override for a scenario test."""
    if decision_latency_days == "USE_ENTRY":
        decision_latency_days = entry.get("evaluation_lag_days")
    action_taken = False  # ⛔ structural constant: no ratified Buy/Action/Order authority exists anywhere in this repo

    # ★ No "nothing to miss" shortcut here: callers (e.g. opportunity_miss_ledger.py)
    #   are expected to have already filtered to entries worth classifying (a
    #   materially positive forward move). Given such an entry, the absence of a
    #   detected trigger despite live data being available IS a real SIGNAL_MISS,
    #   not a non-event -- rc.classify()'s own priority chain already encodes
    #   exactly that (data_available -> in_universe -> had_valid_trigger -> ...).
    return rc.classify(
        data_available=entry["data_available"],
        in_universe=in_universe,
        had_valid_trigger=bool(entry["triggers"]),
        had_independent_confirmation=len(entry["proposed_ruleset"]["trigger_types_present"]) >= 2,
        gate_available=entry["proposed_ruleset"]["condition_7_gate_ratified"],
        action_taken=action_taken,
        decision_latency_days=decision_latency_days,
        no_position_rule_active=no_position_rule_active,
    )
