#!/usr/bin/env python3
"""P7-11 Baseline Audit -- population assembly.

★ Reuse, not reimplementation: the population itself (which subjects, which
  dates, which episodes are "PIT-safe gradable-entry episodes") is taken
  VERBATIM from PR #210's already-CIO-approved `replay/` modules --
  `replay.run_pit_replay.load_all_series`/`build_signal_replay_ledger`,
  `replay.opportunity_miss_ledger.build_miss_episodes`,
  `replay.defense_ledger.build_defense_episodes`,
  `replay.coverage_gap.build_coverage_gap_report`. This module's only job
  is to (a) attach a genuinely NEW path-level measurement
  (`harvest_audit.gain_path.compute_gain_path`) to each already-approved
  episode, and (b) tag every episode with exactly one
  `diagnostic_category` -- never re-derive the underlying trigger/episode
  detection logic a second, possibly-inconsistent way.

★ Survivorship-bias boundary (B-3): the population is Miss episodes UNION
  Defense episodes, both already built by PR #210 without cherry-picking
  by outcome size beyond its own pre-defined, ratified materiality
  thresholds (`MATERIALITY_THRESHOLD_PCT`/`MATERIALITY_DRAWDOWN_THRESHOLD_PCT`,
  unmodified). This module never adds a ticker to the population because
  it is CURRENTLY rallying -- the population is fixed by PR #210's own
  historical-episode construction, before this module ever runs.

★ Market population-status boundary (B-3): reused verbatim from
  `replay.run_pit_replay.run()`'s own `by_market` logic -- BTC is the only
  market with an official-KPI-eligible population; Korea is labeled
  `CURRENT_WATCHLIST_DIAGNOSTIC_COHORT` (not a reconstructed historical PIT
  watchlist); Crypto is `NOT_COMPUTABLE` for most of the window (no
  ratified taxonomy existed yet) -- see `replay/run_pit_replay.py`'s own
  docstring for the full reasoning, unchanged here.
"""
from __future__ import annotations

from replay import coverage_gap as cg
from replay import defense_ledger as dl
from replay import opportunity_miss_ledger as oml
from replay.opportunity_trigger import payload_sha256
from replay.run_pit_replay import PRIORITY_SUBJECTS, load_all_series, market_of

from harvest_audit.gain_path import compute_gain_path

CATEGORY_HARVEST_OPPORTUNITY = "HARVEST_OPPORTUNITY_DIAGNOSTIC"
CATEGORY_DEFENSE = "DEFENSE_EPISODE"
CATEGORY_NOT_GRADABLE = "NOT_GRADABLE"
CATEGORY_NOT_COMPUTABLE = "NOT_COMPUTABLE"


def build_signal_ledger_and_episodes():
    """Reuses `replay.run_pit_replay`'s own committed, CIO-approved
    2026-07-22..2026-08-22 audit window and scan/detection logic verbatim
    -- this module never re-derives or widens the population window; doing
    so would risk silently diverging from the already-approved audit
    scope."""
    import replay.run_pit_replay as rpr

    ctx = load_all_series()
    signal_ledger = rpr.build_signal_replay_ledger(ctx)

    miss_episodes = oml.build_miss_episodes(signal_ledger)
    defense_episodes = dl.build_defense_episodes(signal_ledger)
    coverage_gap = cg.build_coverage_gap_report(signal_ledger)
    return ctx, signal_ledger, miss_episodes, defense_episodes, coverage_gap


def _series_for(ctx: dict, subject: str):
    if subject == "BTC":
        return ctx["btc_series"]
    if subject in ctx["kr_series"]:
        return ctx["kr_series"][subject]
    if subject in ctx["breadth_series"]:
        return ctx["breadth_series"][subject]
    return None


def _evaluation_date_lookup(signal_ledger: list[dict]) -> dict[tuple[str, str], str | None]:
    """(subject, decision_date) -> evaluation_date, taken verbatim from
    PR #210's own per-row computation -- never recomputed independently, so
    this can never numerically diverge from the ledger it is read from."""
    return {(e["subject"], e["decision_date"]): e.get("evaluation_date") for e in signal_ledger}


def _episode_id(episode: dict, category: str) -> str:
    return payload_sha256({
        "category": category,
        "subject": episode["subject"],
        "episode_start_date": episode["episode_start_date"],
        "episode_end_date": episode["episode_end_date"],
        "first_action_eligible_date": episode.get("first_action_eligible_date"),
        "root_cause": episode.get("root_cause"),
    })


def _build_ledger_record(ctx: dict, episode: dict, category: str,
                          eval_date_lookup: dict) -> dict:
    subject = episode["subject"]
    market = market_of(subject)
    action_eligible_at = episode.get("first_action_eligible_date")
    record = {
        "episode_id": _episode_id(episode, category),
        "subject": subject,
        "market": market,
        "diagnostic_category": category,
        "trigger_family": episode.get("trigger_family"),
        "root_cause": episode.get("root_cause"),
        "episode_start_date": episode["episode_start_date"],
        "episode_end_date": episode["episode_end_date"],
        "daily_rows_deduped": episode.get("daily_rows_deduped"),
        "pr210_representative_forward_return_pct": episode.get("representative_forward_return_pct"),
        "evidence_sha256": episode.get("evidence_sha256"),
        "source": episode.get("source"),
    }
    if action_eligible_at is None:
        record["gain_path"] = {
            "status": "NOT_GRADABLE",
            "not_gradable_reason": "DATA_FAILURE/SIGNAL_MISS episode with no real action-eligible date",
        }
        record["diagnostic_category"] = CATEGORY_NOT_GRADABLE
        return record

    series = _series_for(ctx, subject)
    if series is None:
        record["gain_path"] = {
            "status": "NOT_GRADABLE",
            "not_gradable_reason": f"no committed price series available for subject={subject}",
        }
        record["diagnostic_category"] = CATEGORY_NOT_GRADABLE
        return record

    signal_evaluation_at = eval_date_lookup.get((subject, action_eligible_at))
    gain_path = compute_gain_path(series, action_eligible_at, market,
                                   signal_evaluation_at=signal_evaluation_at)
    record["gain_path"] = gain_path
    if gain_path["status"] != "OK":
        record["diagnostic_category"] = CATEGORY_NOT_GRADABLE
    return record


def build_episode_ledger(ctx: dict, signal_ledger: list[dict],
                          miss_episodes: list[dict], defense_episodes: list[dict]) -> list[dict]:
    eval_date_lookup = _evaluation_date_lookup(signal_ledger)
    ledger: list[dict] = []
    for ep in miss_episodes:
        ledger.append(_build_ledger_record(ctx, ep, CATEGORY_HARVEST_OPPORTUNITY, eval_date_lookup))
    for ep in defense_episodes:
        ledger.append(_build_ledger_record(ctx, ep, CATEGORY_DEFENSE, eval_date_lookup))
    ledger.sort(key=lambda r: (r["subject"], r["episode_start_date"], r["episode_id"]))
    return ledger


# ★ Market population-status boundary -- reused verbatim (B-3): BTC is the
# only market whose population is a real, reconstructable, official-KPI
# population; Korea and Crypto are honestly labeled diagnostic-only /
# not-computable, exactly as PR #210's own `run_pit_replay.run()` already
# established -- never re-litigated or loosened here.
MARKET_KPI_STATUS = {
    "BTC": {"kpi_population_status": "OK", "population_label": "DEDICATED_COLLECTOR"},
    "KOREA": {
        "kpi_population_status": "NOT_COMPUTABLE_NO_HISTORICAL_PIT_WATCHLIST_EVIDENCE",
        "population_label": "CURRENT_WATCHLIST_DIAGNOSTIC_COHORT",
    },
    "CRYPTO": {
        "kpi_population_status": "NOT_COMPUTABLE_MOSTLY_PRE_2026_08_19",
        "population_label": "PIT_RATIFIED_ELIGIBLE_UNIVERSE",
    },
}


def build_market_summary(episode_ledger: list[dict]) -> dict:
    by_market: dict[str, dict] = {}
    for market in ("BTC", "KOREA", "CRYPTO"):
        rows = [r for r in episode_ledger if r["market"] == market]
        harvest = [r for r in rows if r["diagnostic_category"] == CATEGORY_HARVEST_OPPORTUNITY]
        defense = [r for r in rows if r["diagnostic_category"] == CATEGORY_DEFENSE]
        not_gradable = [r for r in rows if r["diagnostic_category"] == CATEGORY_NOT_GRADABLE]
        gradable_harvest = [r for r in harvest if r["gain_path"]["status"] == "OK"]
        gradable_defense = [r for r in defense if r["gain_path"]["status"] == "OK"]

        def _avg(vals):
            vals = [v for v in vals if v is not None]
            return sum(vals) / len(vals) if vals else None

        by_market[market] = {
            **MARKET_KPI_STATUS[market],
            "episode_count": len(rows),
            "harvest_opportunity_diagnostic_count": len(harvest),
            "defense_episode_count": len(defense),
            "not_gradable_count": len(not_gradable),
            "gradable_harvest_opportunity_count": len(gradable_harvest),
            "avg_mfe_pct_gradable_harvest_opportunities": _avg(
                [r["gain_path"]["mfe_pct"] for r in gradable_harvest]),
            "avg_terminal_return_pct_gradable_harvest_opportunities": _avg(
                [r["gain_path"]["terminal_return_pct"] for r in gradable_harvest]),
            "avg_max_giveback_after_mfe_pct_gradable_harvest_opportunities": _avg(
                [r["gain_path"]["max_giveback_after_mfe_pct"] for r in gradable_harvest]),
            "avg_avoided_mae_pct_gradable_defense_episodes": _avg(
                [r["gain_path"]["mae_pct"] for r in gradable_defense]),
        }
    return by_market


def priority_subject_rows(episode_ledger: list[dict]) -> list[dict]:
    return [r for r in episode_ledger if r["subject"] in PRIORITY_SUBJECTS]

