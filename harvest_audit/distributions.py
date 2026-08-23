#!/usr/bin/env python3
"""P7-11 Baseline Audit -- deterministic distribution summaries.
Pure descriptive statistics only (min/max/median/mean/count) -- never a
recommended/optimal value, never a policy threshold."""
from __future__ import annotations

import statistics


def _summary(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"count": 0, "status": "NOT_COMPUTABLE_NO_DATA"}
    vals_sorted = sorted(vals)
    return {
        "count": len(vals_sorted),
        "status": "OK",
        "min": vals_sorted[0],
        "max": vals_sorted[-1],
        "median": statistics.median(vals_sorted),
        "mean": statistics.fmean(vals_sorted),
    }


def build_gain_path_distribution(episode_ledger: list[dict]) -> dict:
    harvest = [r for r in episode_ledger
               if r["diagnostic_category"] == "HARVEST_OPPORTUNITY_DIAGNOSTIC" and r["gain_path"]["status"] == "OK"]
    by_market: dict[str, dict] = {}
    for market in ("BTC", "KOREA", "CRYPTO"):
        rows = [r for r in harvest if r["market"] == market]
        gp = [r["gain_path"] for r in rows]
        by_market[market] = {
            "episode_count": len(rows),
            "mfe_pct": _summary([g["mfe_pct"] for g in gp]),
            "mae_pct": _summary([g["mae_pct"] for g in gp]),
            "time_to_mfe_days": _summary([g["time_to_mfe_days"] for g in gp]),
            "time_to_first_positive_return_days": _summary(
                [g["time_to_first_positive_return_days"] for g in gp]),
            "terminal_return_pct": _summary([g["terminal_return_pct"] for g in gp]),
            "positive_return_duration_days": _summary([g["positive_return_duration_days"] for g in gp]),
            "underwater_duration_days": _summary([g["underwater_duration_days"] for g in gp]),
        }
        by_market[market]["episodes"] = sorted(
            [{"episode_id": r["episode_id"], "subject": r["subject"],
              "mfe_pct": r["gain_path"]["mfe_pct"],
              "time_to_mfe_days": r["gain_path"]["time_to_mfe_days"],
              "terminal_return_pct": r["gain_path"]["terminal_return_pct"]}
             for r in rows],
            key=lambda e: (e["subject"], e["episode_id"]),
        )
    return by_market


def build_giveback_distribution(episode_ledger: list[dict]) -> dict:
    gradable = [r for r in episode_ledger if r["gain_path"]["status"] == "OK"]
    by_category: dict[str, dict] = {}
    for category in ("HARVEST_OPPORTUNITY_DIAGNOSTIC", "DEFENSE_EPISODE"):
        rows = [r for r in gradable if r["diagnostic_category"] == category]
        gp = [r["gain_path"] for r in rows]
        by_category[category] = {
            "episode_count": len(rows),
            "max_giveback_after_mfe_pct": _summary([g["max_giveback_after_mfe_pct"] for g in gp]),
            "peak_to_terminal_giveback_pct": _summary([g["peak_to_terminal_giveback_pct"] for g in gp]),
            "episodes_with_giveback_below_breakeven": sum(
                1 for g in gp if g["breakeven_after_positive_mfe_status"] != "NO_GIVEBACK_BELOW_BREAKEVEN"),
            "episodes_recovered_after_giveback": sum(
                1 for g in gp if g["breakeven_after_positive_mfe_status"] == "RECOVERED"),
            "episodes_not_recovered_in_window": sum(
                1 for g in gp if g["breakeven_after_positive_mfe_status"] == "NOT_RECOVERED_IN_WINDOW"),
        }
    return by_category
