#!/usr/bin/env python3
"""Signal Replay Ledger (deliverable 1): date-by-date, per-subject entry of
what was detectable at decision_date, what the existing and proposed
rulesets would have done with it, and forward return/MFE/MAE.

Every entry's `entry_hash` is a deterministic sha256 of its own canonical
JSON -- same inputs always produce the same ledger, byte for byte, with no
wall-clock or random component (satisfies the "deterministic output" hard
constraint).

★ CIO review round 4 fix (confirmed lookahead bug): `forward_metrics` is no
  longer anchored to `evaluation_date` (the trigger's own signal-evaluation
  date, which is often the PRIOR trading day relative to when the signal
  was actually knowable). It is now anchored purely to `decision_date`
  (== `action_eligible_at`); `evaluation_date` is passed through only as
  diagnostic `signal_evaluation_at` metadata, never as the pricing date --
  see `forward_metrics.py`'s docstring for the concrete bug this fixes.
★ CIO review fix (flaw 2/3, PR #210): `action_conversion_gate.evaluate()` is
  now called with the real `series`/`evaluation_date`/`lookback_dates`
  context it needs to actually compute conditions 5 and 6, and
  `classify_gap()` now passes `conditions_1_to_6_all_pass` (not a raw
  trigger-type count) into `root_cause.classify()`, and defaults
  `no_position_rule_active=False` (see `root_cause.py`'s docstring for why).
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

INVALIDATION_LOOKBACK = 10


def _invalidation_price(series: PriceSeries, live_dates: list) -> float | None:
    if len(live_dates) < INVALIDATION_LOOKBACK:
        return None
    lows = [series.row_on(d)["low"] for d in live_dates[-INVALIDATION_LOOKBACK:]]
    return min(lows)


def build_entry(series: PriceSeries, decision_date: str, source: str, evidence_sha: str,
                 peers: dict[str, PriceSeries] | None = None,
                 kr_universe_codes: set | None = None) -> dict:
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
    invalidation_price = _invalidation_price(series, live_dates)
    lookback_dates = live_dates[-INVALIDATION_LOOKBACK:] if live_dates else []

    gate_result = acg.evaluate(
        series.subject, decision_date, triggers, entry_price, invalidation_price,
        series=series, evaluation_date=evaluation_date, lookback_dates=lookback_dates,
        kr_universe_codes=kr_universe_codes,
    )
    existing = erb.existing_ruleset_action_for(bool(triggers))

    # ★ round-4 fix: entry timing is derived purely from decision_date
    #   (action_eligible_at) inside compute_forward_metrics -- never from
    #   evaluation_date, which can be an already-past, no-longer-executable
    #   trading day. evaluation_date is passed through only as diagnostic
    #   signal_evaluation_at metadata.
    forward = compute_forward_metrics(series, decision_date, signal_evaluation_at=evaluation_date)

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


def classify_gap(entry: dict, no_position_rule_active: bool = False,
                  decision_latency_days: int | None = "USE_ENTRY", in_universe: bool = True) -> str | None:
    """Returns a root-cause code if this entry represents a miss/action-gap
    under the PROPOSED ruleset, else None (no gap: either action was taken,
    which never happens anywhere in this system since capital is always 0,
    or there genuinely was nothing to act on).

    `decision_latency_days` defaults to the entry's own real
    `evaluation_lag_days` (how many days the collector's own finalization
    lag put between decision_date and the freshest live-known data) --
    pass an explicit value to override for a scenario test.

    `no_position_rule_active` defaults to False: this replay has no
    committed-evidence source for a genuinely-observed, explicit
    portfolio-level "no position" constraint for any subject (see
    root_cause.py's docstring) -- it must never be used as a stand-in for
    the generic "capital is always 0" fact, which GATE_BLOCK already
    represents correctly."""
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
        conditions_1_to_6_all_pass=entry["proposed_ruleset"]["conditions_1_to_6_all_pass"],
        gate_available=entry["proposed_ruleset"]["condition_7_gate_ratified"] == "PASS",
        action_taken=action_taken,
        decision_latency_days=decision_latency_days,
        no_position_rule_active=no_position_rule_active,
    )
