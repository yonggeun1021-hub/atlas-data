#!/usr/bin/env python3
"""P7-11 Baseline Audit -- EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC scenario
comparison. Research-only, per B-6: never a sell threshold, never a
liquidation rule, never a real quantity, never a Trade Proposal, never
order generation, never a Harvest action. Every record here is locked
`approval_status=UNRATIFIED` / `scenario_type=ANALYTICAL_SCENARIO_ONLY` /
`action_authorized=false` / `order_authorized=false`.

This module produces exactly two things, kept structurally distinct from
each other and from `episode_ledger.json`:
  1. Per-episode scenario comparison records (EARLY-EXIT-at-horizon-N vs
     FULL-HOLD-to-endpoint), over the gradable HARVEST_OPPORTUNITY_DIAGNOSTIC
     population only.
  2. An honest sample-size gate: if the gradable population for a given
     early-exit horizon is below `MIN_SAMPLE_SIZE`, the aggregate summary
     for that horizon is `NOT_COMPUTABLE_INSUFFICIENT_SAMPLE` -- this
     module NEVER manufactures an "optimal" horizon out of too few
     episodes (B-6).
"""
from __future__ import annotations

MIN_SAMPLE_SIZE = 5
EARLY_EXIT_HORIZONS = (1, 3, 5)

SCENARIO_LOCK = {
    "approval_status": "UNRATIFIED",
    "scenario_type": "ANALYTICAL_SCENARIO_ONLY",
    "action_authorized": False,
    "order_authorized": False,
    "note": (
        "Research-only comparison for a FUTURE, separate CIO policy design decision. "
        "This is INPUT to that decision, not a policy itself. No sell threshold, "
        "liquidation rule, quantity, Trade Proposal, order, or Harvest action is "
        "produced or implied by this record."
    ),
}


def _early_exit_vs_full_hold(record: dict, early_exit_horizon: int) -> dict | None:
    gain_path = record["gain_path"]
    if gain_path["status"] != "OK":
        return None
    h = gain_path["horizons"].get(str(early_exit_horizon))
    if not h or h["status"] != "OK":
        return None
    early_exit_return_pct = h["forward_return_pct"]
    full_hold_return_pct = gain_path["terminal_return_pct"]
    return {
        **SCENARIO_LOCK,
        "episode_id": record["episode_id"],
        "subject": record["subject"],
        "market": record["market"],
        "early_exit_horizon_trading_days": early_exit_horizon,
        "early_exit_return_pct": early_exit_return_pct,
        "full_hold_evaluation_horizon_end": gain_path["evaluation_horizon_end"],
        "full_hold_return_pct": full_hold_return_pct,
        "early_exit_opportunity_cost_pct": full_hold_return_pct - early_exit_return_pct,
        "full_hold_endpoint_coverage": gain_path["endpoint_coverage"],
    }


def build_scenario_comparisons(episode_ledger: list[dict]) -> dict:
    harvest_rows = [r for r in episode_ledger if r["diagnostic_category"] == "HARVEST_OPPORTUNITY_DIAGNOSTIC"]

    by_horizon: dict[str, dict] = {}
    for horizon in EARLY_EXIT_HORIZONS:
        comparisons = [c for c in (
            _early_exit_vs_full_hold(r, horizon) for r in harvest_rows
        ) if c is not None]
        sample_size = len(comparisons)
        if sample_size < MIN_SAMPLE_SIZE:
            summary = {
                "sample_size": sample_size,
                "min_sample_size_required": MIN_SAMPLE_SIZE,
                "status": "NOT_COMPUTABLE_INSUFFICIENT_SAMPLE",
            }
        else:
            costs = [c["early_exit_opportunity_cost_pct"] for c in comparisons]
            summary = {
                "sample_size": sample_size,
                "min_sample_size_required": MIN_SAMPLE_SIZE,
                "status": "OK",
                "avg_early_exit_opportunity_cost_pct": sum(costs) / len(costs),
                "episodes_where_full_hold_outperformed": sum(1 for c in costs if c > 0),
                "episodes_where_early_exit_outperformed": sum(1 for c in costs if c < 0),
            }
        by_horizon[str(horizon)] = {
            **SCENARIO_LOCK,
            "early_exit_horizon_trading_days": horizon,
            "aggregate_summary": summary,
            "comparisons": sorted(comparisons, key=lambda c: (c["subject"], c["episode_id"])),
        }
    return {
        **SCENARIO_LOCK,
        "diagnostic_category": "EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC",
        "by_early_exit_horizon": by_horizon,
    }
