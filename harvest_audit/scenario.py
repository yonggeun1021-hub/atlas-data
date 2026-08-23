#!/usr/bin/env python3
"""P7-11 Baseline Audit -- EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC scenario
comparison. Research-only, per B-6: never a sell threshold, never a
liquidation rule, never a real quantity, never a Trade Proposal, never
order generation, never a Harvest action.

★ CIO methodology review round 1, defect 3: the original version used
  `MIN_SAMPLE_SIZE=5` as if it were an already-ratified computability
  criterion, attaching a `NOT_COMPUTABLE_INSUFFICIENT_SAMPLE` verdict
  below it and an implicit "OK" verdict (with an averaged "opportunity
  cost" figure) above it. But neither `MIN_SAMPLE_SIZE` nor the
  1/3/5-trading-day early-exit grid is a ratified policy parameter --
  choosing them IS itself an unratified policy decision, so no aggregate
  comparison built on top of them can ever be presented as a computability
  verdict or an answer.

  Fixed: the 1/3/5-day grid is labeled `ANALYTICAL_GRID_UNRATIFIED`
  everywhere. The real sample count is still printed (a plain fact), but
  the aggregate summary's `status` is ALWAYS
  `NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED`, regardless of sample
  size -- no averaged "opportunity cost", no "N episodes where X
  outperformed" count, no aggregate figure of any kind is ever computed.
  Only the RAW per-episode `comparisons` (individual, real, already-
  happened numbers) are provided, for a future, separate, ratified
  analysis to aggregate however that future policy design decides.
"""
from __future__ import annotations

EARLY_EXIT_HORIZONS = (1, 3, 5)
ANALYTICAL_GRID_STATUS = "ANALYTICAL_GRID_UNRATIFIED"
AGGREGATE_STATUS = "NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED"

SCENARIO_LOCK = {
    "approval_status": "UNRATIFIED",
    "scenario_type": "ANALYTICAL_SCENARIO_ONLY",
    "action_authorized": False,
    "order_authorized": False,
    "note": (
        "Research-only comparison for a FUTURE, separate CIO policy design decision. "
        "This is INPUT to that decision, not a policy itself. No sell threshold, "
        "liquidation rule, quantity, Trade Proposal, order, or Harvest action is "
        "produced or implied by this record. The early-exit horizon grid itself is "
        "an unratified analytical choice, not a ratified policy parameter."
    ),
}

HARVEST_LIKE_CATEGORIES = ("HARVEST_OPPORTUNITY", "HOLD_BENEFIT")


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
        "early_exit_vs_full_hold_diff_pct": full_hold_return_pct - early_exit_return_pct,
        "full_hold_endpoint_coverage": gain_path["endpoint_coverage"],
    }


def build_scenario_comparisons(episode_ledger: list[dict]) -> dict:
    harvest_like_rows = [r for r in episode_ledger if r["outcome_category"] in HARVEST_LIKE_CATEGORIES]

    by_horizon: dict[str, dict] = {}
    for horizon in EARLY_EXIT_HORIZONS:
        comparisons = [c for c in (
            _early_exit_vs_full_hold(r, horizon) for r in harvest_like_rows
        ) if c is not None]
        sample_size = len(comparisons)
        # ★ defect 3 fix: the real count is a plain fact; the STATUS is
        # ALWAYS unratified-policy-parameters, never a computability
        # verdict, regardless of sample_size. No averaged/aggregate
        # figure of any kind is computed here.
        aggregate_summary = {
            "sample_size": sample_size,
            "status": AGGREGATE_STATUS,
            "grid_status": ANALYTICAL_GRID_STATUS,
            "reason": (
                "MIN_SAMPLE_SIZE and the early-exit horizon grid are themselves "
                "unratified analytical choices, not ratified policy parameters -- no "
                "aggregate verdict (e.g. an average opportunity cost, or a count of "
                "episodes where one path 'outperformed') is ever produced from this "
                "comparison, regardless of sample size. See `comparisons` below for "
                "the real, per-episode facts."
            ),
        }
        by_horizon[str(horizon)] = {
            **SCENARIO_LOCK,
            "grid_status": ANALYTICAL_GRID_STATUS,
            "early_exit_horizon_trading_days": horizon,
            "aggregate_summary": aggregate_summary,
            "comparisons": sorted(comparisons, key=lambda c: (c["subject"], c["episode_id"])),
        }
    return {
        **SCENARIO_LOCK,
        "diagnostic_category": "EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC",
        "grid_status": ANALYTICAL_GRID_STATUS,
        "by_early_exit_horizon": by_horizon,
    }
