#!/usr/bin/env python3
"""P8-12 Dynamic Clock orchestrator -- the operational
Evidence -> Trigger Event -> Dynamic Re-review -> Human-review candidate
flow.

Ties together `clock/operational_scan.py` (reused PR #210 trigger
detection, run over real committed evidence), `clock/dynamic_clock.py` (the
episode/cooldown/expiry/reactivation state machine, driven by
`config/dynamic_clock_policy.json`), and `clock/review_candidate.py` (raw
per-trigger audit records + the CONSOLIDATED, TIERED, PIT-safe per-subject
Human Review Candidate) into one deterministic report, split by market
(BTC/KOREA/CRYPTO -- never blended).

★ Two distinct dates, kept separate (CIO review round 2, item 5):
    - `evidence_as_of` (per market) -- the latest real evidence capture_date
      this repo actually has. Used to bound trigger DETECTION (never look
      past it -- that would be a signal-side lookahead) and to report "what
      data did we see".
    - `decision_date` (optional argument to `run()`/`main()`) -- the real
      calendar date this operational run/briefing is FOR, supplied
      externally (e.g. by `briefing/daily_orchestrator.py`'s own
      decision_date, itself derived from a real `TZ=Asia/Seoul date`
      command in the workflow, never `datetime.now()` inside this module).
      Used ONLY to decide whether an episode has gone STALE (past its
      `expiry`) even when no new evidence has arrived at all -- round 1's
      version incorrectly gated staleness purely off `evidence_as_of`, so
      an episode could stay "ACTIVE" indefinitely just because no new
      collector run happened, which understates staleness. When
      `decision_date` is omitted (e.g. the bare `python3
      clock/run_dynamic_clock.py` artifact-reproduction CLI), this module
      falls back to `evidence_as_of` -- byte-identical, reproducible
      output, unaffected by wall-clock time, exactly as before.
    Each market's effective staleness date is `max(decision_date,
    evidence_as_of)` when both are known -- never earlier than the
    evidence itself (a caller-supplied `decision_date` behind a market's
    real evidence must not make an episode look artificially fresher than
    the evidence already implies), and never earlier than the real
    operational "today" when that is later than the evidence (the actual
    round-2 fix). `decision_date` still fails closed on a malformed date
    string.

★ Authority: this module never sets Stage/Buy/Action/Order/Production/
  trading. Every record's `authority` block is hard-`False`/`None` (see
  `review_candidate.py`). A Trigger firing here is a re-review REQUEST
  only, and only `IMMEDIATE_REVIEW`-tier subject candidates ever carry
  `human_review_required=True` -- and `IMMEDIATE_REVIEW` itself can NEVER
  be reached today (no real thesis/price linkage exists yet -- see
  `review_candidate.py`'s module docstring on the round-2 PIT fix).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay import asset_identity as ai
from replay.forward_metrics import compute_forward_metrics
from replay.opportunity_trigger import canonical_json

from clock import dynamic_clock as dc
from clock import operational_scan as scan
from clock.dynamic_clock import build_episode_history, close_stale_episodes
from clock.review_candidate import (
    TIER_IMMEDIATE_REVIEW, TIER_OBSERVATION_ONLY, TIER_WATCH_REVIEW,
    build_expired_record, build_raw_trigger_record, build_subject_review_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evidence" / "operational" / "dynamic_clock"

PRIORITY_SUBJECTS = ("BTC", "005930", "000660")  # same regression priority set as PR #210

DATE_FMT = "%Y-%m-%d"


class DynamicClockOrchestratorError(ValueError):
    pass


def _validate_decision_date(decision_date: str | None) -> None:
    """Fails closed only on a malformed date string. A `decision_date`
    earlier than some market's `evidence_as_of` is NOT an error (see
    module docstring) -- `_effective_as_of` below takes the max of the
    two, so it simply has no effect for that market rather than needing to
    be rejected here."""
    if decision_date is None:
        return
    try:
        dt.datetime.strptime(decision_date, DATE_FMT)
    except (ValueError, TypeError) as exc:
        raise DynamicClockOrchestratorError(f"DECISION_DATE_INVALID:{decision_date!r}") from exc


def _effective_as_of(decision_date: str | None, evidence_as_of: str | None) -> str | None:
    if decision_date is None:
        return evidence_as_of
    if evidence_as_of is None:
        return decision_date
    return max(decision_date, evidence_as_of)


def _pit_eligibility_status(subject: str, decision_date: str, kr_universe_codes) -> str:
    return ai.asset_identity_status(subject, decision_date, kr_universe_codes)


def _market_result(market: str, decision_date: str | None) -> dict:
    scanner = scan.MARKET_SCANNERS[market]
    scan_result = scanner()
    evidence_dates = scan_result["evidence_dates"]
    evidence_as_of = max(evidence_dates) if evidence_dates else None
    # ★ round 2, item 5: staleness is evaluated as of the real operational
    #   decision_date when one is supplied, never capped at evidence_as_of
    #   -- but also never earlier than evidence_as_of itself (see
    #   _effective_as_of's docstring / module docstring).
    episode_as_of = _effective_as_of(decision_date, evidence_as_of)
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
            if episode_as_of is not None:
                episodes = close_stale_episodes(episodes, episode_as_of)
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
                    if ep["opened_at"] == evidence_as_of:
                        new_triggers_this_run.append(record)
                elif ep["status"] == "EXPIRED":
                    expired_records.append(build_expired_record(ep))

    review_queue: list[dict] = []
    for subject, episodes in sorted(active_episodes_by_subject.items()):
        # PIT eligibility is checked as of the real operational "now"
        # (episode_as_of) -- this reveals nothing about the future (a
        # ratified taxonomy's effective_from is already a known, committed
        # fact as of any date <= today), unlike using a forward-looking
        # price outcome would.
        pit_status = _pit_eligibility_status(subject, episode_as_of or evidence_as_of, kr_universe_codes)
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
        "evidence_as_of": evidence_as_of,
        "decision_date": episode_as_of,
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
        "tier_counts": {
            "IMMEDIATE_REVIEW": len(immediate_review),
            "WATCH_REVIEW": len(watch_review),
            "OBSERVATION_ONLY": len(observation_only),
        },
    }


def run(decision_date: str | None = None) -> dict:
    """`decision_date`, when supplied, is the real operational "today" this
    run is FOR (see module docstring). Never sourced from `datetime.now()`
    inside this function -- the caller (a workflow's own shell `date`
    command, or `briefing/daily_orchestrator.py`'s own decision_date
    parameter) is responsible for supplying it."""
    policy = dc.load_policy()
    # Format-only validation -- does not need each market's evidence_as_of
    # (a decision_date behind some market's evidence is not an error, see
    # _effective_as_of), so no need to pre-scan every market twice.
    _validate_decision_date(decision_date)

    by_market = {market: _market_result(market, decision_date) for market in ("BTC", "KOREA", "CRYPTO")}
    all_evidence_dates = [m["evidence_as_of"] for m in by_market.values() if m["evidence_as_of"]]
    return {
        "wbs_item": "P8-12 Opportunity Trigger + Dynamic Review Clock",
        "report_asof_evidence_date": max(all_evidence_dates) if all_evidence_dates else None,
        "decision_date": decision_date,
        "repo_history_starts_at": scan.ei.REPO_HISTORY_STARTS_AT,
        # ★ round 2, item 7: policy status surfaced at the top level, so a
        #   component's own READY status is never mistaken for "the
        #   cadence/tiering policy itself is finally ratified".
        "policy_approval_status": policy["approval_status"],
        "policy_version": policy["policy_version"],
        "authority_note": (
            "A Trigger firing here is a re-review REQUEST only -- it is never a Buy signal and "
            "never confers Action/Order/trading authority. Only IMMEDIATE_REVIEW-tier subject "
            "candidates carry human_review_required=True; see each record's own `authority` block. "
            "IMMEDIATE_REVIEW cannot be reached today: no real thesis/price linkage exists yet."
        ),
        "by_market": by_market,
    }


def build_briefing_section(report: dict) -> dict:
    """The user-facing artifact item 6/8 asks for -- ONLY the consolidated,
    tiered subject-level queue (never the raw per-trigger ledger, which
    stays in the full committed report for audit only), with per-tier
    counts and a plain-language, non-outcome-based `reason` per candidate.
    NO forward-return/MFE/post-hoc-audit figure appears anywhere in this
    section (CIO review round 2, item 8) -- those exist only in the full
    report's `review_queue`/`raw_trigger_ledger` for audit purposes.
    Consumed by `briefing/daily_orchestrator.py`'s `DYNAMIC_CLOCK`
    component."""
    section = {
        "policy_approval_status": report["policy_approval_status"],
        "policy_version": report["policy_version"],
        "decision_date": report["decision_date"],
        "markets": {},
    }
    for market, m in report["by_market"].items():
        section["markets"][market] = {
            "evidence_as_of": m["evidence_as_of"],
            "decision_date": m["decision_date"],
            "raw_trigger_count_audit_only": m["raw_trigger_count"],
            "tier_counts": m["tier_counts"],
            "new_triggers": [
                {"subject": r["subject"], "trigger_type": r["trigger_type"], "urgency": r["urgency"],
                 "next_review_at": r["next_review_at"]}
                for r in m["new_triggers_this_run"]
            ],
            "immediate_review": [
                {"subject": r["subject"], "trigger_types": r["trigger_types"],
                 "confirmation_count": r["confirmation_count"], "urgency": r["urgency"],
                 "next_review_at": r["next_review_at"], "expiry": r["expiry"], "reason": r["reason"]}
                for r in m["immediate_review"]
            ],
            "watch_review": [
                {"subject": r["subject"], "trigger_types": r["trigger_types"],
                 "confirmation_count": r["confirmation_count"], "next_review_at": r["next_review_at"],
                 "reason": r["reason"]}
                for r in m["watch_review"]
            ],
            "observation_only_count": len(m["observation_only"]),
            "expired_triggers": [
                {"subject": r["subject"], "trigger_type": r["trigger_type"], "expiry": r["expiry"]}
                for r in m["expired_triggers"]
            ],
            "not_computable_trigger_types": [t["trigger_type"] for t in m["not_computable_trigger_types"]],
        }
        if market == "KOREA":
            # ★ round 2, item 7: the KOREA holiday-calendar-unverified flag
            #   must be visible in the briefing itself, not only buried
            #   inside each candidate record.
            section["markets"][market]["calendar_confidence"] = dc.calendar_confidence_for("KOREA")
        else:
            section["markets"][market]["calendar_confidence"] = dc.calendar_confidence_for(market)
    return section


def write_report(report: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "dynamic_clock_report.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
    (OUT_DIR / "briefing_section.json").write_text(
        canonical_json(build_briefing_section(report)) + "\n", encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P8-12 Dynamic Clock orchestrator")
    parser.add_argument(
        "--decision-date", default=None,
        help=(
            "Real operational 'today' this run is FOR (YYYY-MM-DD), supplied by the caller "
            "(e.g. a workflow's own `date` command). Omit for artifact-reproduction mode, which "
            "falls back to each market's own evidence_as_of -- byte-identical, reproducible output."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    report = run(decision_date=args.decision_date)
    write_report(report)
    print(json.dumps(build_briefing_section(report), ensure_ascii=False, indent=2, default=str))
