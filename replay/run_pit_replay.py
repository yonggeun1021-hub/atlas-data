#!/usr/bin/env python3
"""P11 Opportunity Capture PIT Replay -- orchestrator.

Builds every ledger (deliverables 1-3, 6-7) for 2026-07-22..2026-08-22 from
real committed repo evidence only, and writes deterministic JSON artifacts
under `evidence/audit/pit_replay/`.

★ Determinism: nothing in this module calls `datetime.now()` / `time.time()`
  / `random`. The window bounds are literal constants (the task's own audit
  window); the "as_of" stamp on the report is the latest evidence
  capture_date actually found in the repo, not wall-clock time.
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
TOP_MOVER_N = 15


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

    movers = us.top_crypto_movers(breadth_snapshots, WINDOW_START, WINDOW_END, top_n=TOP_MOVER_N)
    mover_pair_ids = []
    if movers["status"] == "OK":
        mover_pair_ids = [r["pair_id"] for r in movers["gainers"]] + [r["pair_id"] for r in movers["losers"]]
    mover_series = {pid: us.crypto_breadth_series(breadth_snapshots, pid) for pid in mover_pair_ids}

    return {
        "krx_snapshots": krx_snapshots,
        "btc_snapshots": btc_snapshots,
        "breadth_snapshots": breadth_snapshots,
        "kr_series": kr_series,
        "btc_series": btc_series,
        "movers": movers,
        "mover_series": mover_series,
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

    for pid, series in ctx["mover_series"].items():
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
    miss_ledger = oml.build_miss_records(signal_ledger)
    defense_records = dl.build_defense_records(signal_ledger)
    comparison = rsc.compare(signal_ledger)
    recommendations = ra.recommend(miss_ledger, defense_records, comparison)
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
            "crypto_movers": ctx["movers"],
        },
        "signal_replay_ledger": signal_ledger,
        "signal_replay_ledger_priority_only": priority,
        "opportunity_miss_ledger": miss_ledger,
        "defense_ledger": defense_records,
        "ruleset_comparison": comparison,
        "rule_attribution": recommendations,
    }


def write_report(report: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "signal_replay_ledger.json").write_text(
        canonical_json(report["signal_replay_ledger"]) + "\n", encoding="utf-8")
    (OUT_DIR / "opportunity_miss_ledger.json").write_text(
        canonical_json(report["opportunity_miss_ledger"]) + "\n", encoding="utf-8")
    (OUT_DIR / "defense_ledger.json").write_text(
        canonical_json(report["defense_ledger"]) + "\n", encoding="utf-8")
    (OUT_DIR / "ruleset_comparison.json").write_text(
        canonical_json(report["ruleset_comparison"]) + "\n", encoding="utf-8")
    (OUT_DIR / "rule_attribution.json").write_text(
        canonical_json(report["rule_attribution"]) + "\n", encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in (
        "signal_replay_ledger", "signal_replay_ledger_priority_only",
        "opportunity_miss_ledger", "defense_ledger",
    )}
    (OUT_DIR / "replay_summary.json").write_text(canonical_json(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    report = run()
    write_report(report)
    print(json.dumps(report["ruleset_comparison"], ensure_ascii=False, indent=2))
