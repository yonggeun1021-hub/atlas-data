#!/usr/bin/env python3
"""P10-02/P10-03 Opportunity Capture PIT Replay -- orchestrator.

★ CIO review round 3 (flaw 5/10): this work is Atlas WBS P10-02/P10-03
  (Shadow Audit), NOT P11 (Real Capital, a different, unrelated WBS phase).
  Earlier commits on this branch used a "P11" label before this was
  clarified -- history is not rewritten, but every doc/output-artifact
  label going forward says P10-02/P10-03 / "P10 Audit". This module has
  NO relationship to Real Capital authority: every capital field anywhere
  in `replay/` is hard-coded 0, and no Stage/Buy/Action/Order/Production/
  trading boolean is ever set True.

Builds every ledger (deliverables 1-3, 6-7) for 2026-07-22..2026-08-22 from
real committed repo evidence only, and writes deterministic JSON artifacts
under `evidence/audit/pit_replay/`.

★ Determinism: nothing in this module calls `datetime.now()` / `time.time()`
  / `random`. The window bounds are literal constants (the task's own audit
  window); the "as_of" stamp on the report is the latest evidence
  capture_date actually found in the repo, not wall-clock time.

★ CIO review round 3 fix (flaw 1): the crypto Opportunity KPI population is
  no longer the full 632-pair source catalog (round 2's fix eliminated
  survivorship bias but replaced it with a DIFFERENT problem -- a raw
  Kraken listing catalog is not a confirmed, PIT-eligible investable
  universe). The catalog now feeds only a `source_coverage_population`
  metric (data-coverage only, see `universe_scan.crypto_source_coverage`).
  The actual Opportunity KPI population is
  `asset_identity.crypto_pit_eligible_pair_ids(decision_date, ...)` -- the
  real, RATIFIED `eligible_crypto` taxonomy, evaluated per-date against its
  own real `effective_from` field. Per that taxonomy's real ratification
  history, this is an EMPTY set for essentially the entire window before
  2026-08-19 -- reported honestly as NOT_COMPUTABLE for those dates, never
  silently substituted with the full catalog.

★ CIO review round 3 fix (flaw 11): results are reported split by market
  (BTC / Korea / Crypto) via `market_of()`, never blended into one number.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay import asset_identity as ai
from replay import coverage_gap as cg
from replay import evidence_index as ei
from replay import opportunity_miss_ledger as oml
from replay import defense_ledger as dl
from replay import rule_attribution as ra
from replay import ruleset_comparison as rsc
from replay import universe_scan as us
from replay.opportunity_trigger import canonical_json
from replay.price_series import build_krx_series, build_btc_series
from replay.signal_replay_ledger import build_entry

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evidence" / "audit" / "pit_replay"

WINDOW_START = "2026-07-22"
WINDOW_END = "2026-08-22"
PRIORITY_SUBJECTS = ("BTC", "005930", "000660")
DESCRIPTIVE_TOP_MOVER_N = 15  # descriptive table only -- NOT the KPI population


def market_of(subject: str) -> str:
    if subject == "BTC":
        return "BTC"
    if subject.isdigit() and len(subject) == 6:
        return "KOREA"
    if "/" in subject:
        return "CRYPTO"
    return "OTHER"


def _dates(start: str, end: str) -> list[str]:
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    out = []
    d = d0
    while d <= d1:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def load_all_series():
    krx_snapshots = ei.find_krx_snapshots()
    btc_snapshots = ei.find_btc_snapshots()
    breadth_snapshots = ei.find_breadth_snapshots()

    kr_codes = ei.all_krx_codes(krx_snapshots)
    kr_universe_codes = {row["code"] for row in ei.load_universe().get("kr", [])} | set(kr_codes)
    kr_series = {code: build_krx_series(code, krx_snapshots) for code in kr_codes}
    btc_series = build_btc_series(btc_snapshots)

    # ★ flaw-1 fix: build series only for pairs that are EVER PIT-eligible
    # anywhere in the window (union across all decision dates -- eligibility
    # is monotonic via effective_from, so this equals eligibility as of
    # WINDOW_END), not the full 632-pair catalog.
    known_pair_ids = set(breadth_snapshots[-1].pair_ids()) if breadth_snapshots else set()
    ever_eligible_pairs = ai.crypto_pit_eligible_pair_ids(WINDOW_END, known_pair_ids) if breadth_snapshots else set()
    breadth_series = {pid: us.crypto_breadth_series(breadth_snapshots, pid) for pid in ever_eligible_pairs}

    # Pure data-coverage metric -- NOT the KPI population (flaw 1).
    source_coverage = us.crypto_source_coverage(breadth_snapshots)
    # Descriptive-only outcome-ranked table -- NOT the KPI population either.
    movers_descriptive = us.top_crypto_movers(breadth_snapshots, WINDOW_START, WINDOW_END,
                                               top_n=DESCRIPTIVE_TOP_MOVER_N)

    return {
        "krx_snapshots": krx_snapshots,
        "btc_snapshots": btc_snapshots,
        "breadth_snapshots": breadth_snapshots,
        "kr_series": kr_series,
        "kr_universe_codes": kr_universe_codes,
        "btc_series": btc_series,
        "breadth_series": breadth_series,
        "source_coverage": source_coverage,
        "movers_descriptive": movers_descriptive,
    }


def build_signal_replay_ledger(ctx: dict) -> list[dict]:
    entries = []
    dates = _dates(WINDOW_START, WINDOW_END)

    for date in dates:
        snap = ei.snapshot_at_or_before(ctx["btc_snapshots"], date)
        source = snap.citation() if snap else "NO_BTC_SNAPSHOT_AVAILABLE_AT_OR_BEFORE_DECISION_DATE"
        evidence_sha = snap.sha256 if snap else "0" * 64
        entries.append(build_entry(ctx["btc_series"], date, source, evidence_sha))

    for code, series in ctx["kr_series"].items():
        for date in dates:
            snap = ei.snapshot_at_or_before(ctx["krx_snapshots"], date)
            source = snap.citation(code) if snap else "NO_KRX_SNAPSHOT_AVAILABLE_AT_OR_BEFORE_DECISION_DATE"
            evidence_sha = snap.sha256 if snap else "0" * 64
            peers = {c: s for c, s in ctx["kr_series"].items()}
            entries.append(build_entry(series, date, source, evidence_sha, peers=peers,
                                        kr_universe_codes=ctx["kr_universe_codes"]))

    # ★ flaw-1 fix: only (pair, date) combinations where the pair is REALLY,
    #   RATIFIED-ly PIT-eligible AS OF that specific date. For most of the
    #   window this real-evidence eligibility set is empty (see module
    #   docstring) -- those dates simply produce no crypto-breadth entries
    #   at all, rather than a fabricated substitute population.
    known_pair_ids = set(ctx["breadth_series"])
    for date in dates:
        eligible_today = ai.crypto_pit_eligible_pair_ids(date, known_pair_ids)
        for pid in sorted(eligible_today):
            series = ctx["breadth_series"][pid]
            snap = ei.snapshot_at_or_before(ctx["breadth_snapshots"], date)
            source = snap.citation(pid) if snap else "NO_BREADTH_SNAPSHOT_AVAILABLE_AT_OR_BEFORE_DECISION_DATE"
            evidence_sha = snap.sha256 if snap else "0" * 64
            entries.append(build_entry(series, date, source, evidence_sha))

    entries.sort(key=lambda e: (e["subject"], e["decision_date"]))
    return entries


def _by_market(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"BTC": [], "KOREA": [], "CRYPTO": [], "OTHER": []}
    for e in entries:
        out[market_of(e["subject"])].append(e)
    return out


def run() -> dict:
    ctx = load_all_series()
    signal_ledger = build_signal_replay_ledger(ctx)

    miss_daily = oml.build_miss_records(signal_ledger)
    miss_episodes = oml.build_miss_episodes(signal_ledger)
    defense_daily = dl.build_defense_records(signal_ledger)
    defense_episodes = dl.build_defense_episodes(signal_ledger)
    ungradable = oml.build_ungradable_records(signal_ledger)
    coverage_gap = cg.build_coverage_gap_report(signal_ledger)

    comparison = rsc.compare(signal_ledger)
    recommendations = ra.recommend(miss_episodes, defense_episodes, comparison, coverage_gap=coverage_gap)
    kr_population = us.kr_population(ctx["krx_snapshots"])

    all_capture_dates = sorted(
        [s.capture_date for s in ctx["krx_snapshots"]]
        + [s.capture_date for s in ctx["btc_snapshots"]]
        + [s.capture_date for s in ctx["breadth_snapshots"]]
    )
    report_asof_evidence_date = all_capture_dates[-1] if all_capture_dates else None

    priority = [e for e in signal_ledger if e["subject"] in PRIORITY_SUBJECTS]

    # ★ flaw-11 fix: per-market breakdown, never blended.
    market_ledgers = _by_market(signal_ledger)
    by_market = {}
    for market, market_entries in market_ledgers.items():
        if not market_entries:
            continue
        m_miss = oml.build_miss_episodes(market_entries)
        m_defense = dl.build_defense_episodes(market_entries)
        m_coverage = cg.build_coverage_gap_report(market_entries)
        # ★ CIO review round 4, item 5: config/universe.json is the CURRENT
        #   watchlist, not evidence of what the PIT-investable KR population
        #   actually was on 2026-07-22. No committed evidence in this repo
        #   reconstructs an as-of-date historical KR watchlist/Discovery-
        #   Candidate-Ready population -- so the official KR Opportunity KPI
        #   is NOT_COMPUTABLE. The 6-ticker results are still reported, but
        #   explicitly labeled a diagnostic cohort over the CURRENT
        #   watchlist, never a PIT-eligible-universe result.
        if market == "CRYPTO":
            kpi_status = "NOT_COMPUTABLE_MOSTLY_PRE_2026_08_19"
            population_label = "PIT_RATIFIED_ELIGIBLE_UNIVERSE"
        elif market == "KOREA":
            kpi_status = "NOT_COMPUTABLE_NO_HISTORICAL_PIT_WATCHLIST_EVIDENCE"
            population_label = "CURRENT_WATCHLIST_DIAGNOSTIC_COHORT"
        else:
            kpi_status = "OK"
            population_label = "DEDICATED_COLLECTOR"
        by_market[market] = {
            "entry_count": len(market_entries),
            "miss_episode_count": len(m_miss),
            "defense_episode_count": len(m_defense),
            "coverage_gap": m_coverage,
            "kpi_population_status": kpi_status,
            "population_label": population_label,
        }

    return {
        "window": {"start": WINDOW_START, "end": WINDOW_END},
        "report_asof_evidence_date": report_asof_evidence_date,
        "repo_history_starts_at": ei.REPO_HISTORY_STARTS_AT,
        "wbs_phase": "P10-02/P10-03 (Shadow Audit) -- NOT P11 (Real Capital)",
        "population": {
            "kr_universe": kr_population,
            "priority_subjects": list(PRIORITY_SUBJECTS),
            "crypto_source_coverage_population": ctx["source_coverage"],
            "crypto_pit_eligible_population_size_at_window_end": len(ctx["breadth_series"]),
            "crypto_movers_descriptive_only": ctx["movers_descriptive"],
        },
        "by_market": by_market,
        "signal_replay_ledger": signal_ledger,
        "signal_replay_ledger_priority_only": priority,
        "opportunity_miss_ledger_daily": miss_daily,
        "opportunity_miss_episodes": miss_episodes,
        "defense_ledger_daily": defense_daily,
        "defense_episodes": defense_episodes,
        "ungradable_ledger": ungradable,
        "coverage_gap": coverage_gap,
        "ruleset_comparison": comparison,
        "rule_attribution": recommendations,
    }


def write_report(report: dict) -> None:
    """★ The full raw `signal_replay_ledger` is intentionally NOT committed
    in full -- it is fully reproducible byte-for-byte via
    `python3 replay/run_pit_replay.py` (see test_pit_replay_end_to_end.py's
    determinism test). Only the priority-subject slice (BTC/005930/000660)
    is committed for direct inspection; the aggregated episode/daily/
    coverage-gap/by-market tables carry the rest of the audit trail."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "signal_replay_ledger_priority_only.json").write_text(
        canonical_json(report["signal_replay_ledger_priority_only"]) + "\n", encoding="utf-8")
    (OUT_DIR / "opportunity_miss_ledger_daily.json").write_text(
        canonical_json(report["opportunity_miss_ledger_daily"]) + "\n", encoding="utf-8")
    (OUT_DIR / "opportunity_miss_episodes.json").write_text(
        canonical_json(report["opportunity_miss_episodes"]) + "\n", encoding="utf-8")
    (OUT_DIR / "defense_ledger_daily.json").write_text(
        canonical_json(report["defense_ledger_daily"]) + "\n", encoding="utf-8")
    (OUT_DIR / "defense_episodes.json").write_text(
        canonical_json(report["defense_episodes"]) + "\n", encoding="utf-8")
    (OUT_DIR / "ungradable_ledger.json").write_text(
        canonical_json(report["ungradable_ledger"]) + "\n", encoding="utf-8")
    (OUT_DIR / "coverage_gap.json").write_text(
        canonical_json(report["coverage_gap"]) + "\n", encoding="utf-8")
    (OUT_DIR / "by_market.json").write_text(
        canonical_json(report["by_market"]) + "\n", encoding="utf-8")
    (OUT_DIR / "ruleset_comparison.json").write_text(
        canonical_json(report["ruleset_comparison"]) + "\n", encoding="utf-8")
    (OUT_DIR / "rule_attribution.json").write_text(
        canonical_json(report["rule_attribution"]) + "\n", encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in (
        "signal_replay_ledger", "signal_replay_ledger_priority_only",
        "opportunity_miss_ledger_daily", "opportunity_miss_episodes",
        "defense_ledger_daily", "defense_episodes", "ungradable_ledger",
        "coverage_gap", "by_market",
    )}
    (OUT_DIR / "replay_summary.json").write_text(canonical_json(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    report = run()
    write_report(report)
    print(json.dumps(report["by_market"], ensure_ascii=False, indent=2, default=str))
