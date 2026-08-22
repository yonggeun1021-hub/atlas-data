#!/usr/bin/env python3
"""P8-12 Dynamic Clock orchestrator -- the operational
Evidence -> Trigger Event -> Dynamic Re-review -> Human-review candidate
flow.

Ties together `clock/operational_scan.py` (reused PR #210 trigger
detection, run over real committed evidence), `clock/dynamic_clock.py` (the
episode/cooldown/expiry/reactivation state machine, now driven by
`config/dynamic_clock_policy.json`), and `clock/review_candidate.py` (raw
per-trigger audit records + the CONSOLIDATED, TIERED per-subject Human
Review Candidate -- see that module's docstring for the "candidate flood"
fix from CIO review round 1 on PR #211) into one deterministic report,
split by market (BTC/KOREA/CRYPTO -- never blended, same discipline as PR
#210's `by_market`).

★ Determinism: no `datetime.now()` anywhere. Each market's "as of" date is
  that market's own latest real evidence capture_date (see
  `operational_scan.py`'s `evidence_dates`) -- re-running this script
  against the SAME committed evidence always produces byte-identical
  output; it only changes when new evidence is actually committed, which is
  exactly the "as soon as a valid trigger fires" dynamic-clock behavior the
  task asks for (no dependency on a monthly review calendar).

★ Authority: this module never sets Stage/Buy/Action/Order/Production/
  trading. Every record's `authority` block is hard-`False`/`None` (see
  `review_candidate.py`). A Trigger firing here is a re-review REQUEST
  only, and only `IMMEDIATE_REVIEW`-tier subject candidates ever carry
  `human_review_required=True`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay import asset_identity as ai
from replay.forward_metrics import compute_forward_metrics
from replay.opportunity_trigger import canonical_json

from clock import operational_scan as scan
from clock.dynamic_clock import build_episode_history, close_stale_episodes
from clock.review_candidate import (
    TIER_IMMEDIATE_REVIEW, TIER_OBSERVATION_ONLY, TIER_WATCH_REVIEW,
    build_expired_record, build_raw_trigger_record, build_subject_review_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evidence" / "operational" / "dynamic_clock"

PRIORITY_SUBJECTS = ("BTC", "005930", "000660")  # same regression priority set as PR #210


def _pit_eligibility_status(subject: str, market: str, decision_date: str, kr_universe_codes) -> str:
    return ai.asset_identity_status(subject, decision_date, kr_universe_codes)


def _market_result(market: str) -> dict:
    scanner = scan.MARKET_SCANNERS[market]
    scan_result = scanner()
    evidence_dates = scan_result["evidence_dates"]
    as_of = max(evidence_dates) if evidence_dates else None
    series_map = scan_result.get("series", {})
    kr_universe_codes = scan_result.get("kr_universe_codes")

    raw_trigger_ledger: list[dict] = []
    expired_records: list[dict] = []
    new_triggers_this_run: list[dict] = []
    active_episodes_by_subject: dict[str, list[dict]] = {}

    for subject, by_type in sorted(scan_result["subjects"].items()):
        for trigger_type, events in sorted(by_type.items()):
            if not events:
                continue
            episodes = build_episode_history(subject, market, trigger_type, events)
            if as_of is not None:
                episodes = close_stale_episodes(episodes, as_of)
            for ep in episodes:
                if ep["status"] == "ACTIVE":
                    ref_metrics_latest = None
                    ref_metrics_first = None
                    if subject in PRIORITY_SUBJECTS and subject in series_map:
                        latest = ep["evidence_trail"][-1]
                        first = ep["evidence_trail"][0]
                        ref_metrics_latest = compute_forward_metrics(
                            series_map[subject], latest["detected_at"],
                            signal_evaluation_at=latest["evidence_available_at"],
                        )
                        ref_metrics_first = compute_forward_metrics(
                            series_map[subject], first["detected_at"],
                            signal_evaluation_at=first["evidence_available_at"],
                        )
                    record = build_raw_trigger_record(
                        ep, reference_forward_metrics=ref_metrics_latest,
                        reference_forward_metrics_first_detection=ref_metrics_first,
                    )
                    raw_trigger_ledger.append(record)
                    active_episodes_by_subject.setdefault(subject, []).append(ep)
                    if ep["opened_at"] == as_of:
                        new_triggers_this_run.append(record)
                elif ep["status"] == "EXPIRED":
                    expired_records.append(build_expired_record(ep))

    review_queue: list[dict] = []
    for subject, episodes in sorted(active_episodes_by_subject.items()):
        decision_date = max(ep["last_detected_at"] for ep in episodes)
        pit_status = _pit_eligibility_status(subject, market, decision_date, kr_universe_codes)
        ref_metrics_latest = None
        ref_metrics_first = None
        if subject in PRIORITY_SUBJECTS and subject in series_map:
            latest_ep = max(episodes, key=lambda e: e["last_detected_at"])
            latest_ev = latest_ep["evidence_trail"][-1]
            first_ep = min(episodes, key=lambda e: e["opened_at"])
            first_ev = first_ep["evidence_trail"][0]
            ref_metrics_latest = compute_forward_metrics(
                series_map[subject], latest_ev["detected_at"],
                signal_evaluation_at=latest_ev["evidence_available_at"],
            )
            ref_metrics_first = compute_forward_metrics(
                series_map[subject], first_ev["detected_at"],
                signal_evaluation_at=first_ev["evidence_available_at"],
            )
        review_queue.append(build_subject_review_candidate(
            subject, market, episodes, pit_eligibility_status=pit_status,
            reference_forward_metrics_first_detection=ref_metrics_first,
            reference_forward_metrics_latest_detection=ref_metrics_latest,
        ))

    raw_trigger_ledger.sort(key=lambda r: (r["subject"], r["trigger_type"], r["candidate_id"]))
    expired_records.sort(key=lambda r: (r["subject"], r["trigger_type"], r["candidate_id"]))
    new_triggers_this_run.sort(key=lambda r: (r["subject"], r["trigger_type"], r["candidate_id"]))
    review_queue.sort(key=lambda r: (r["subject"], r["candidate_id"]))

    immediate_review = [r for r in review_queue if r["tier"] == TIER_IMMEDIATE_REVIEW]
    watch_review = [r for r in review_queue if r["tier"] == TIER_WATCH_REVIEW]
    observation_only = [r for r in review_queue if r["tier"] == TIER_OBSERVATION_ONLY]

    return {
        "market": market,
        "as_of_evidence_date": as_of,
        "population_label": scan_result["population_label"],
        "not_computable_trigger_types": scan.not_computable_report(market),
        "subject_count": len(scan_result["subjects"]),
        "raw_trigger_count": len(raw_trigger_ledger),
        "raw_trigger_ledger": raw_trigger_ledger,
        "expired_triggers": expired_records,
        "new_triggers_this_run": new_triggers_this_run,
        "review_queue": review_queue,
        "review_queue_subject_count": len(review_queue),
        "immediate_review": immediate_review,
        "watch_review": watch_review,
        "observation_only": observation_only,
    }


def run() -> dict:
    by_market = {market: _market_result(market) for market in ("BTC", "KOREA", "CRYPTO")}
    all_evidence_dates = [m["as_of_evidence_date"] for m in by_market.values() if m["as_of_evidence_date"]]
    return {
        "wbs_item": "P8-12 Opportunity Trigger + Dynamic Review Clock",
        "report_asof_evidence_date": max(all_evidence_dates) if all_evidence_dates else None,
        "repo_history_starts_at": scan.ei.REPO_HISTORY_STARTS_AT,
        "authority_note": (
            "A Trigger firing here is a re-review REQUEST only -- it is never a Buy signal and "
            "never confers Action/Order/trading authority. Only IMMEDIATE_REVIEW-tier subject "
            "candidates carry human_review_required=True; see each record's own `authority` block."
        ),
        "by_market": by_market,
    }


def build_briefing_section(report: dict) -> dict:
    """The additive artifact item 6 asks for -- new triggers, immediate/
    watch review candidates, expired triggers, and NOT_COMPUTABLE types, per
    market. Consumed by `briefing/daily_orchestrator.py`'s `DYNAMIC_CLOCK`
    component (see that file for the wiring)."""
    section = {"markets": {}}
    for market, m in report["by_market"].items():
        section["markets"][market] = {
            "as_of_evidence_date": m["as_of_evidence_date"],
            "raw_trigger_count": m["raw_trigger_count"],
            "review_queue_subject_count": m["review_queue_subject_count"],
            "new_triggers": [
                {"subject": r["subject"], "trigger_type": r["trigger_type"], "urgency": r["urgency"],
                 "next_review_at": r["next_review_at"]}
                for r in m["new_triggers_this_run"]
            ],
            "immediate_review": [
                {"subject": r["subject"], "trigger_types": r["trigger_types"],
                 "confirmation_count": r["confirmation_count"], "urgency": r["urgency"],
                 "next_review_at": r["next_review_at"], "expiry": r["expiry"],
                 "audit_confirmed_miss": r["audit_confirmed_miss"] is not None}
                for r in m["immediate_review"]
            ],
            "watch_review": [
                {"subject": r["subject"], "trigger_types": r["trigger_types"],
                 "confirmation_count": r["confirmation_count"], "next_review_at": r["next_review_at"]}
                for r in m["watch_review"]
            ],
            "observation_only_count": len(m["observation_only"]),
            "expired_triggers": [
                {"subject": r["subject"], "trigger_type": r["trigger_type"], "expiry": r["expiry"]}
                for r in m["expired_triggers"]
            ],
            "not_computable_trigger_types": [t["trigger_type"] for t in m["not_computable_trigger_types"]],
        }
    return section


def write_report(report: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "dynamic_clock_report.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
    (OUT_DIR / "briefing_section.json").write_text(
        canonical_json(build_briefing_section(report)) + "\n", encoding="utf-8")


if __name__ == "__main__":
    report = run()
    write_report(report)
    print(json.dumps(build_briefing_section(report), ensure_ascii=False, indent=2, default=str))
