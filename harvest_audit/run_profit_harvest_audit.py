#!/usr/bin/env python3
"""P7-11 Profit Harvesting Baseline Audit -- orchestrator.

★ Authority: this module produces DIAGNOSTIC MEASUREMENT ONLY. It contains
  no sell threshold, no liquidation rule, no real quantity, no Trade
  Proposal, no order generation, and no Harvest action of any kind.
  `authority` is hard-coded on every record this module builds:

    {"review_only": True, "action_authorized": False, "order_authorized": False,
     "stage_authorized": False, "buy_authorized": False, "production_authorized": False,
     "trading_authorized": False}

  There is no code path anywhere in this module that ever sets any of
  those to True (see `test/test_profit_harvest_end_to_end.py::
  AuthorityInvariantTests`).

★ Reuse boundary: every episode's underlying detection/gate/materiality
  logic is `replay/`'s own, unmodified (see `harvest_audit/population.py`'s
  docstring). This module is additive-only -- it does not modify
  `decision/`, `clock/`, `shadow/`, `briefing/`, or any existing P5/P7/P8
  policy contract, and Dynamic Clock's operational output is untouched.

★ Determinism: no `datetime.now()`/`time.time()`/`random` anywhere in this
  package. The report's own `report_asof_evidence_date` is the latest real
  evidence capture_date found in the repo, exactly like `replay/
  run_pit_replay.py`'s own convention.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay.opportunity_trigger import canonical_json
from replay import evidence_index as ei

from harvest_audit.distributions import build_gain_path_distribution, build_giveback_distribution
from harvest_audit.population import (
    build_episode_ledger, build_market_summary, build_signal_ledger_and_episodes,
    priority_subject_rows,
)
from harvest_audit.scenario import build_scenario_comparisons

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evidence" / "audit" / "profit_harvest_baseline"

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "action_authorized": False,
    "order_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


def run() -> dict:
    ctx, signal_ledger, miss_episodes, defense_episodes, coverage_gap = build_signal_ledger_and_episodes()
    episode_ledger = build_episode_ledger(ctx, signal_ledger, miss_episodes, defense_episodes)
    market_summary = build_market_summary(episode_ledger)
    gain_path_distribution = build_gain_path_distribution(episode_ledger)
    giveback_distribution = build_giveback_distribution(episode_ledger)
    policy_input_packet = build_scenario_comparisons(episode_ledger)

    all_capture_dates = sorted(
        [s.capture_date for s in ctx["krx_snapshots"]]
        + [s.capture_date for s in ctx["btc_snapshots"]]
        + [s.capture_date for s in ctx["breadth_snapshots"]]
    )
    report_asof_evidence_date = all_capture_dates[-1] if all_capture_dates else None

    return {
        "wbs_item": "P7-11 Profit Harvesting / Rapid Gain Realization Engine -- BASELINE AUDIT ONLY",
        "authority": AUTHORITY_ALL_FALSE,
        "report_asof_evidence_date": report_asof_evidence_date,
        "repo_history_starts_at": ei.REPO_HISTORY_STARTS_AT,
        "not_an_operational_harvest_engine": (
            "This artifact measures gain-path dynamics of already-approved PR #210 "
            "episodes only. It never produces a sell threshold, a liquidation rule, a "
            "real quantity, a Trade Proposal, an order, or any Harvest action. See "
            "docs/profit_harvest_baseline_audit.md for the full authority boundary."
        ),
        "episode_ledger": episode_ledger,
        "market_summary": market_summary,
        "coverage_gap": coverage_gap,
        "gain_path_distribution": gain_path_distribution,
        "giveback_distribution": giveback_distribution,
        "policy_input_packet": policy_input_packet,
        "priority_subject_episodes": priority_subject_rows(episode_ledger),
    }


def _render_priority_row(r: dict) -> str:
    gp = r["gain_path"]
    if gp["status"] != "OK":
        return (f"| {r['subject']} | {r['diagnostic_category']} | {r['episode_start_date']} | "
                f"NOT_GRADABLE | - | - | - | - | - |")
    h5 = gp["horizons"].get("5", {})
    h5_str = f"{h5['forward_return_pct']:.2f}%" if h5.get("status") == "OK" else "N/A"
    return (f"| {r['subject']} | {r['diagnostic_category']} | {r['episode_start_date']} | "
            f"{gp['mfe_pct']:.2f}% | {gp['mae_pct']:.2f}% | {gp['time_to_mfe_days']}d | "
            f"{h5_str} | {gp['terminal_return_pct']:.2f}% | {gp['max_giveback_after_mfe_pct']:.2f}% |")


def render_report_markdown(report: dict) -> str:
    lines = [
        "# P7-11 Profit Harvesting Baseline Audit",
        "",
        "**BASELINE AUDIT ONLY -- not an operational Harvest Engine, not a sell-policy "
        "ratification.** Every record's `authority` block is hard-`False`/`review_only`. "
        "This report measures how already-approved PR #210 Miss/Defense episodes' price "
        "paths actually unfolded after a hypothetical PIT-safe entry -- it produces no "
        "sell threshold, liquidation rule, quantity, Trade Proposal, or order.",
        "",
        f"- report_asof_evidence_date: `{report['report_asof_evidence_date']}`",
        f"- repo_history_starts_at: `{report['repo_history_starts_at']}`",
        "",
        "## Market population boundary",
        "",
        "| Market | population_label | kpi_population_status | episodes | harvest_opp | defense | not_gradable |",
        "|---|---|---|---|---|---|---|",
    ]
    for market, m in report["market_summary"].items():
        lines.append(
            f"| {market} | {m['population_label']} | {m['kpi_population_status']} | "
            f"{m['episode_count']} | {m['harvest_opportunity_diagnostic_count']} | "
            f"{m['defense_episode_count']} | {m['not_gradable_count']} |"
        )
    lines += [
        "",
        "## Priority subjects (BTC / 005930 / 000660)",
        "",
        "| Subject | Category | Episode start | MFE | MAE | Time-to-MFE | 5d fwd return | "
        "Terminal return | Max giveback after MFE |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["priority_subject_episodes"]:
        lines.append(_render_priority_row(r))
    lines += [
        "",
        "## Coverage gap (DATA_FAILURE, excluded from every Miss/Defense KPI)",
        "",
        "See `coverage_gap.json` -- reused verbatim from `replay.coverage_gap` "
        f"(auditable_coverage_pct={report['coverage_gap'].get('auditable_coverage_pct')}, "
        f"a blended cross-market operational metric only -- never a performance KPI).",
        "",
        "## Policy input packet (research-only, UNRATIFIED)",
        "",
        "See `policy_input_packet.json`. Every scenario comparison record carries "
        "`approval_status=UNRATIFIED`, `scenario_type=ANALYTICAL_SCENARIO_ONLY`, "
        "`action_authorized=false`, `order_authorized=false`. This is INPUT for a "
        "future, separate CIO policy design decision on P7-11 -- not a policy itself.",
        "",
    ]
    for horizon, block in report["policy_input_packet"]["by_early_exit_horizon"].items():
        s = block["aggregate_summary"]
        if s["status"] == "OK":
            lines.append(
                f"- Early exit at {horizon}d vs full hold: n={s['sample_size']}, "
                f"avg opportunity cost={s['avg_early_exit_opportunity_cost_pct']:.2f}pp, "
                f"full-hold-better={s['episodes_where_full_hold_outperformed']}, "
                f"early-exit-better={s['episodes_where_early_exit_outperformed']}"
            )
        else:
            lines.append(f"- Early exit at {horizon}d vs full hold: {s['status']} (n={s['sample_size']})")
    lines += [
        "",
        "## Authority",
        "",
        "Every record in this artifact carries `authority.action_authorized=false`, "
        "`order_authorized=false`, `stage_authorized=false`, `buy_authorized=false`, "
        "`production_authorized=false`, `trading_authorized=false`. No code path in "
        "`harvest_audit/` ever sets any of these to `true`.",
    ]
    return "\n".join(lines) + "\n"


def write_report(report: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "episode_ledger.json").write_text(canonical_json(report["episode_ledger"]) + "\n", encoding="utf-8")
    (OUT_DIR / "market_summary.json").write_text(canonical_json(report["market_summary"]) + "\n", encoding="utf-8")
    (OUT_DIR / "coverage_gap.json").write_text(canonical_json(report["coverage_gap"]) + "\n", encoding="utf-8")
    (OUT_DIR / "gain_path_distribution.json").write_text(
        canonical_json(report["gain_path_distribution"]) + "\n", encoding="utf-8")
    (OUT_DIR / "giveback_distribution.json").write_text(
        canonical_json(report["giveback_distribution"]) + "\n", encoding="utf-8")
    (OUT_DIR / "policy_input_packet.json").write_text(
        canonical_json(report["policy_input_packet"]) + "\n", encoding="utf-8")
    (OUT_DIR / "audit_report.md").write_text(render_report_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    report = run()
    write_report(report)
    print(json.dumps(report["market_summary"], ensure_ascii=False, indent=2, default=str))
