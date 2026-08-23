#!/usr/bin/env python3
"""P7-11 Baseline Audit -- population assembly.

★ CIO methodology review round 1, defect 1 (the core fix this round):
  the original version built its population as `build_miss_episodes ∪
  build_defense_episodes` -- but Miss/Defense are THEMSELVES outcome
  classifications, built by looking at each row's FUTURE forward-return
  materiality (`>=5.0%`/`<=-5.0%`). The claim "population was fixed before
  computing outcomes" was factually wrong: a real, contemporaneous
  305930 Trigger (298040, 2026-08-13, PRICE_CONFIRMATION) was SILENTLY
  EXCLUDED purely because its future outcome never became a material
  Miss/Defense.

  Fixed for real: the OFFICIAL Harvest observation population
  (`build_pit_episodes`) is now built using ONLY facts knowable at
  `decision_date` -- a real, contemporaneous Trigger existed
  (`entry["triggers"]` non-empty) AND a gradable hypothetical entry point
  exists (`forward_metrics.status=="OK"`, itself a pure DATA-AVAILABILITY
  fact, never a return-magnitude/direction fact). Deduplication into PIT
  episodes reuses `replay.opportunity_episode.group_into_episodes`
  UNMODIFIED -- its grouping key is (subject, trigger_family, root_cause,
  window-overlap), none of which read forward-return magnitude at all.
  Post-hoc `outcome_category` labels (HARVEST_OPPORTUNITY / HOLD_BENEFIT /
  DEFENSE / FLAT_NO_MATERIAL_OUTCOME / NOT_GRADABLE) are attached ONLY
  AFTER episode membership is already fixed -- see `_classify_outcome_
  category`. `test_profit_harvest_population.py::
  PopulationMembershipIsOutcomeIndependentTests` proves the resulting
  episode-ID SET is byte-identical even when every entry's forward return
  is artificially mutated.

  The OLD Miss ∪ Defense episode set is retained as
  `build_pr210_auxiliary_cohort` -- an AUXILIARY comparison cohort against
  PR #210's own headline KPI, explicitly never the official population.

★ Reuse, not reimplementation: episode grouping, root-cause
  classification, and market population-status boundaries are all
  `replay/`'s own, unmodified.
"""
from __future__ import annotations

from replay import coverage_gap as cg
from replay import defense_ledger as dl
from replay import opportunity_miss_ledger as oml
from replay.opportunity_episode import group_into_episodes
from replay.opportunity_trigger import payload_sha256
from replay.run_pit_replay import PRIORITY_SUBJECTS, load_all_series, market_of
from replay.signal_replay_ledger import classify_gap

from harvest_audit.gain_path import compute_gain_path

CATEGORY_HARVEST_OPPORTUNITY = "HARVEST_OPPORTUNITY"
CATEGORY_HOLD_BENEFIT = "HOLD_BENEFIT"
CATEGORY_DEFENSE = "DEFENSE"
CATEGORY_FLAT = "FLAT_NO_MATERIAL_OUTCOME"
CATEGORY_NOT_GRADABLE = "NOT_GRADABLE"

# ★ Reused VERBATIM from PR #210's own already-ratified materiality bar
# (`replay.opportunity_miss_ledger.MATERIALITY_THRESHOLD_PCT` /
# `replay.defense_ledger.MATERIALITY_DRAWDOWN_THRESHOLD_PCT`) -- the SAME
# single 5.0/-5.0 magnitude is applied twice below (once to the terminal
# return, once to the peak-to-terminal giveback), never a NEW invented
# threshold.
MATERIALITY_THRESHOLD_PCT = oml.MATERIALITY_THRESHOLD_PCT
MATERIALITY_DRAWDOWN_THRESHOLD_PCT = dl.MATERIALITY_DRAWDOWN_THRESHOLD_PCT

# Same horizon-preference order PR #210's own oml/dl modules use -- purely
# about DATA AVAILABILITY (a horizon's status=="OK"), never about return
# magnitude or direction.
PREFERRED_HORIZONS_FOR_GROUPING = ("5", "3", "1", "10")


def build_signal_ledger_and_episodes():
    """Reuses `replay.run_pit_replay`'s own committed, CIO-approved
    2026-07-22..2026-08-22 audit window and scan/detection logic verbatim
    -- this module never re-derives or widens the population window."""
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
    PR #210's own per-row computation -- never recomputed independently."""
    return {(e["subject"], e["decision_date"]): e.get("evaluation_date") for e in signal_ledger}


def _best_available_horizon_for_grouping(entry: dict) -> tuple[str, dict] | None:
    """PIT-safe: chooses a horizon based ONLY on whether real forward data
    exists for it (`status=="OK"`) -- never on the return's magnitude or
    direction. Identical selection rule to `replay.opportunity_miss_ledger.
    _best_available_horizon`/`replay.defense_ledger._best_available_horizon`,
    re-derived here rather than imported since those are private helpers."""
    if entry["forward_metrics"].get("status") != "OK":
        return None
    for h in PREFERRED_HORIZONS_FOR_GROUPING:
        data = entry["forward_metrics"]["horizons"].get(h, {})
        if data.get("status") == "OK":
            return h, data
    return None


def build_trigger_population_records(signal_ledger: list[dict]) -> list[dict]:
    """The raw, one-row-per-day rows underlying the OFFICIAL population:
    a real contemporaneous Trigger existed AND a gradable entry point
    exists. Nothing about future return magnitude/direction is read here
    at all."""
    records = []
    for entry in signal_ledger:
        if not entry["triggers"]:
            continue
        best = _best_available_horizon_for_grouping(entry)
        if best is None:
            continue
        _horizon_used, data = best
        root_cause = classify_gap(entry)  # PIT-safe: no outcome input
        records.append({
            "decision_date": entry["decision_date"],
            "subject": entry["subject"],
            "root_cause": root_cause,
            "entry_date": entry["forward_metrics"]["hypothetical_entry_at"],
            "outcome_window_end": data["end_date"],
            "triggers_detected": [t["trigger_type"] for t in entry["triggers"]],
            "evidence_sha256": entry["evidence_sha256"],
            "source": entry["source"],
        })
    return records


def build_pit_episodes(signal_ledger: list[dict]) -> list[dict]:
    """The OFFICIAL Harvest observation population: real-trigger +
    gradable rows, deduplicated into PIT episodes via PR #210's own
    unmodified `group_into_episodes` (grouping key: subject + trigger
    family + root_cause + real forward-window overlap -- none of which is
    a function of future return magnitude). `representative_forward_
    return_pct` is dropped from the result entirely -- that field belonged
    to the OLD Miss/Defense framing and has no meaning for this
    trigger-based population; the real per-episode outcome is attached
    later, AFTER episode membership is already fixed, via `compute_gain_
    path` + `_classify_outcome_category`."""
    records = build_trigger_population_records(signal_ledger)
    episodes = group_into_episodes(records, outcome_field="root_cause")
    for ep in episodes:
        ep.pop("representative_forward_return_pct", None)
        ep.pop("representative_horizon_used", None)
    return episodes


def _episode_id(episode: dict, category: str) -> str:
    return payload_sha256({
        "category": category,
        "subject": episode["subject"],
        "trigger_family": episode.get("trigger_family"),
        "episode_start_date": episode["episode_start_date"],
        "episode_end_date": episode["episode_end_date"],
        "root_cause": episode.get("root_cause"),
    })


def _classify_outcome_category(gain_path: dict) -> str:
    """Post-hoc label, applied ONLY AFTER episode membership is already
    fixed by `build_pit_episodes`. Reuses the SAME single already-ratified
    5.0/-5.0 materiality magnitude twice (never a new invented number):
    once against the terminal (endpoint-truncated) return, and once
    against the peak-to-terminal giveback, to distinguish a "should have
    harvested near the peak" case (HARVEST_OPPORTUNITY -- a material
    pullback from peak actually occurred) from a "holding kept paying off"
    case (HOLD_BENEFIT -- no material pullback from peak)."""
    if gain_path["status"] != "OK":
        return CATEGORY_NOT_GRADABLE
    terminal = gain_path["terminal_return_pct"]
    giveback = gain_path["peak_to_terminal_giveback_pct"]
    if terminal <= MATERIALITY_DRAWDOWN_THRESHOLD_PCT:
        return CATEGORY_DEFENSE
    if terminal >= MATERIALITY_THRESHOLD_PCT:
        if giveback <= MATERIALITY_DRAWDOWN_THRESHOLD_PCT:
            return CATEGORY_HARVEST_OPPORTUNITY
        return CATEGORY_HOLD_BENEFIT
    return CATEGORY_FLAT


def _build_ledger_record(ctx: dict, episode: dict, eval_date_lookup: dict) -> dict:
    subject = episode["subject"]
    market = market_of(subject)
    action_eligible_at = episode.get("first_action_eligible_date")
    record = {
        "episode_id": _episode_id(episode, "PIT_TRIGGER_EPISODE"),
        "subject": subject,
        "market": market,
        "trigger_family": episode.get("trigger_family"),
        "root_cause": episode.get("root_cause"),
        "episode_start_date": episode["episode_start_date"],
        "episode_end_date": episode["episode_end_date"],
        "first_signal_date": episode.get("first_signal_date"),
        "first_action_eligible_date": action_eligible_at,
        "daily_rows_deduped": episode.get("daily_rows_deduped"),
        "evidence_sha256": episode.get("evidence_sha256"),
        "source": episode.get("source"),
    }
    if action_eligible_at is None:
        record["gain_path"] = {
            "status": "NOT_GRADABLE",
            "not_gradable_reason": "episode with no real action-eligible date",
        }
        record["outcome_category"] = CATEGORY_NOT_GRADABLE
        return record

    series = _series_for(ctx, subject)
    if series is None:
        record["gain_path"] = {
            "status": "NOT_GRADABLE",
            "not_gradable_reason": f"no committed price series available for subject={subject}",
        }
        record["outcome_category"] = CATEGORY_NOT_GRADABLE
        return record

    signal_evaluation_at = eval_date_lookup.get((subject, action_eligible_at))
    gain_path = compute_gain_path(series, action_eligible_at, market,
                                   signal_evaluation_at=signal_evaluation_at)
    record["gain_path"] = gain_path
    record["outcome_category"] = _classify_outcome_category(gain_path)
    return record


def build_episode_ledger(ctx: dict, signal_ledger: list[dict]) -> list[dict]:
    """The OFFICIAL episode ledger: real-trigger+gradable population,
    deduplicated PIT-safely, THEN labeled with a post-hoc outcome
    category."""
    eval_date_lookup = _evaluation_date_lookup(signal_ledger)
    pit_episodes = build_pit_episodes(signal_ledger)
    ledger = [_build_ledger_record(ctx, ep, eval_date_lookup) for ep in pit_episodes]
    ledger.sort(key=lambda r: (r["subject"], r["episode_start_date"], r["episode_id"]))
    return ledger


def build_reconciliation_table(signal_ledger: list[dict], episode_ledger: list[dict]) -> list[dict]:
    """Required reconciliation (CIO methodology review round 1): shows how
    each of the real-trigger+gradable daily rows maps into a PIT episode.
    Any row that does NOT map into an episode indicates a genuine gap in
    the episode-membership logic -- structurally tested to be empty."""
    # subject -> sorted list of (episode_id, start, end) for cheap lookup.
    by_subject: dict[str, list[tuple]] = {}
    for r in episode_ledger:
        by_subject.setdefault(r["subject"], []).append(
            (r["episode_id"], r["episode_start_date"], r["episode_end_date"]))

    rows = []
    for entry in signal_ledger:
        if not entry["triggers"]:
            continue
        best = _best_available_horizon_for_grouping(entry)
        if best is None:
            continue
        subject, decision_date = entry["subject"], entry["decision_date"]
        matched = [eid for eid, start, end in by_subject.get(subject, [])
                   if start <= decision_date <= end]
        rows.append({
            "subject": subject,
            "decision_date": decision_date,
            "triggers_detected": [t["trigger_type"] for t in entry["triggers"]],
            "matched_episode_ids": matched,
            "reconciled": len(matched) > 0,
        })
    rows.sort(key=lambda r: (r["subject"], r["decision_date"]))
    return rows


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
        harvest = [r for r in rows if r["outcome_category"] == CATEGORY_HARVEST_OPPORTUNITY]
        hold = [r for r in rows if r["outcome_category"] == CATEGORY_HOLD_BENEFIT]
        defense = [r for r in rows if r["outcome_category"] == CATEGORY_DEFENSE]
        flat = [r for r in rows if r["outcome_category"] == CATEGORY_FLAT]
        not_gradable = [r for r in rows if r["outcome_category"] == CATEGORY_NOT_GRADABLE]

        def _avg(vals):
            vals = [v for v in vals if v is not None]
            return sum(vals) / len(vals) if vals else None

        gradable = [r for r in rows if r["gain_path"]["status"] == "OK"]
        by_market[market] = {
            **MARKET_KPI_STATUS[market],
            "episode_count": len(rows),
            "harvest_opportunity_count": len(harvest),
            "hold_benefit_count": len(hold),
            "defense_count": len(defense),
            "flat_no_material_outcome_count": len(flat),
            "not_gradable_count": len(not_gradable),
            "avg_mfe_pct_gradable": _avg([r["gain_path"]["mfe_pct"] for r in gradable]),
            "avg_terminal_return_pct_gradable": _avg([r["gain_path"]["terminal_return_pct"] for r in gradable]),
        }
    return by_market


def build_pr210_auxiliary_cohort(ctx: dict, signal_ledger: list[dict],
                                  miss_episodes: list[dict], defense_episodes: list[dict]) -> list[dict]:
    """AUXILIARY comparison cohort ONLY -- PR #210's own headline Miss ∪
    Defense episodes (each already an outcome classification by
    construction), kept here purely so this audit's real-trigger-based
    population can be compared against PR #210's own KPI framing. NEVER
    the official Harvest observation population (see module docstring)."""
    eval_date_lookup = _evaluation_date_lookup(signal_ledger)
    cohort = []
    for kind, episodes in (("MISS", miss_episodes), ("DEFENSE", defense_episodes)):
        for ep in episodes:
            subject = ep["subject"]
            market = market_of(subject)
            action_eligible_at = ep.get("first_action_eligible_date")
            record = {
                "pr210_category": kind,
                "subject": subject,
                "market": market,
                "episode_start_date": ep["episode_start_date"],
                "episode_end_date": ep["episode_end_date"],
                "root_cause": ep.get("root_cause"),
                "pr210_representative_forward_return_pct": ep.get("representative_forward_return_pct"),
            }
            series = _series_for(ctx, subject)
            if action_eligible_at is None or series is None:
                record["gain_path"] = {"status": "NOT_GRADABLE"}
            else:
                signal_evaluation_at = eval_date_lookup.get((subject, action_eligible_at))
                record["gain_path"] = compute_gain_path(series, action_eligible_at, market,
                                                          signal_evaluation_at=signal_evaluation_at)
            cohort.append(record)
    cohort.sort(key=lambda r: (r["subject"], r["episode_start_date"], r["pr210_category"]))
    return cohort


def priority_subject_rows(episode_ledger: list[dict]) -> list[dict]:
    return [r for r in episode_ledger if r["subject"] in PRIORITY_SUBJECTS]
