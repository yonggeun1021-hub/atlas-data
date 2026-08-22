#!/usr/bin/env python3
"""P11 Opportunity Capture PIT Replay -- orchestrator.

Builds every ledger (deliverables 1-3, 6-7) for 2026-07-22..2026-08-22 from
real committed repo evidence only, and writes deterministic JSON artifacts
under `evidence/audit/pit_replay/`.

★ Determinism: nothing in this module calls `datetime.now()` / `time.time()`
  / `random`. The window bounds are literal constants (the task's own audit
  window); the "as_of" stamp on the report is the latest evidence
  capture_date actually found in the repo, not wall-clock time.

★ CIO review fix (flaw 1, PR #210): the crypto Miss/Defense KPI population is
  now the FULL committed breadth catalog (all pairs the latest breadth
  snapshot tracks -- ~630+ pairs), never a top/bottom-N subset selected by
  its own full-window outcome. Selecting the audit population by the very
  outcome the audit measures is a survivorship-bias mechanism regardless of
  whether the downstream root-cause classifier itself looks at the return
  (it does not -- see root_cause.py) -- the bias was upstream of the
  classifier, in which SUBJECTS ever reached it. `top_crypto_movers()`
  still exists and is still computed, but it is now placed under
  `population.crypto_movers_descriptive_only` and explicitly excluded from
  `build_signal_replay_ledger()` -- see
  `test/test_replay_no_survivorship_bias.py`.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
DESCRIPTIVE_TOP_MOVER_N = 15  # descriptive table only -- NOT the KPI population, see module docstring


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
    kr_series = {code: build_krx_series(code, krx_snapshots) for code in kr_codes}
    btc_series = build_btc_series(btc_snapshots)

    # ★ flaw-1 fix: full committed breadth catalog, not an outcome-selected subset.
    all_pair_ids = breadth_snapshots[-1].pair_ids() if breadth_snapshots else []
    breadth_series = {pid: us.crypto_breadth_series(breadth_snapshots, pid) for pid in all_pair_ids}

    # Descriptive-only table, kept for the narrative report -- excluded from
    # the KPI population (see build_signal_replay_ledger()).
    movers_descriptive = us.top_crypto_movers(breadth_snapshots, WINDOW_START, WINDOW_END,
                                               top_n=DESCRIPTIVE_TOP_MOVER_N)

    return {
        "krx_snapshots": krx_snapshots,
        "btc_snapshots": btc_snapshots,
        "breadth_snapshots": breadth_snapshots,
        "kr_series": kr_series,
        "btc_series": btc_series,
        "breadth_series": breadth_series,
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
            entries.append(build_entry(series, date, source, evidence_sha, peers=peers))

    # ★ flaw-1 fix: replay EVERY tracked crypto pair, not a post-hoc top/bottom-N.
    for pid, series in ctx["breadth_series"].items():
        for date in dates:
            snap = ei.snapshot_at_or_before(ctx["breadth_snapshots"], date)
            source = snap.citation(pid) if snap else "NO_BREADTH_SNAPSHOT_AVAILABLE_AT_OR_BEFORE_DECISION_DATE"
            evidence_sha = snap.sha256 if snap else "0" * 64
            entries.append(build_entry(series, date, source, evidence_sha))

    entries.sort(key=lambda e: (e["subject"], e["decision_date"]))
    return entries


def run() -> dict:
    ctx = load_all_series()
    signal_ledger = build_signal_replay_ledger(ctx)

    miss_daily = oml.build_miss_records(signal_ledger)
    miss_episodes = oml.build_miss_episodes(signal_ledger)
    defense_daily = dl.build_defense_records(signal_ledger)
    defense_episodes = dl.build_defense_episodes(signal_ledger)
    ungradable = oml.build_ungradable_records(signal_ledger)

    comparison = rsc.compare(signal_ledger)
    recommendations = ra.recommend(miss_episodes, defense_episodes, comparison)
    kr_population = us.kr_population(ctx["krx_snapshots"])

    all_capture_dates = sorted(
        [s.capture_date for s in ctx["krx_snapshots"]]
        + [s.capture_date for s in ctx["btc_snapshots"]]
        + [s.capture_date for s in ctx["breadth_snapshots"]]
    )
    report_asof_evidence_date = all_capture_dates[-1] if all_capture_dates else None

    priority = [e for e in signal_ledger if e["subject"] in PRIORITY_SUBJECTS]

    return {
        "window": {"start": WINDOW_START, "end": WINDOW_END},
        "report_asof_evidence_date": report_asof_evidence_date,
        "repo_history_starts_at": ei.REPO_HISTORY_STARTS_AT,
        "population": {
            "kr_universe": kr_population,
            "priority_subjects": list(PRIORITY_SUBJECTS),
            "crypto_kpi_population_size": len(ctx["breadth_series"]),
            "crypto_movers_descriptive_only": ctx["movers_descriptive"],
        },
        "signal_replay_ledger": signal_ledger,
        "signal_replay_ledger_priority_only": priority,
        "opportunity_miss_ledger_daily": miss_daily,
        "opportunity_miss_episodes": miss_episodes,
        "defense_ledger_daily": defense_daily,
        "defense_episodes": defense_episodes,
        "ungradable_ledger": ungradable,
        "ruleset_comparison": comparison,
        "rule_attribution": recommendations,
    }


def write_report(report: dict) -> None:
    """★ The full raw `signal_replay_ledger` (20,000+ rows once the crypto
    population is the whole committed breadth catalog -- see flaw-1 fix)
    is intentionally NOT committed in full; it is >50MB and fully
    reproducible byte-for-byte via `python3 replay/run_pit_replay.py`
    (see test_pit_replay_end_to_end.py's determinism test). Only the
    priority-subject slice (BTC/005930/000660, small) is committed for
    direct inspection; the aggregated episode/daily-miss/defense tables
    carry the rest of the audit trail at a manageable size."""
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
    (OUT_DIR / "ruleset_comparison.json").write_text(
        canonical_json(report["ruleset_comparison"]) + "\n", encoding="utf-8")
    (OUT_DIR / "rule_attribution.json").write_text(
        canonical_json(report["rule_attribution"]) + "\n", encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in (
        "signal_replay_ledger", "signal_replay_ledger_priority_only",
        "opportunity_miss_ledger_daily", "opportunity_miss_episodes",
        "defense_ledger_daily", "defense_episodes", "ungradable_ledger",
    )}
    (OUT_DIR / "replay_summary.json").write_text(canonical_json(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    report = run()
    write_report(report)
    print(json.dumps(report["ruleset_comparison"], ensure_ascii=False, indent=2))
