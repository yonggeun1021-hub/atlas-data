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

★ CIO integration review round 1, defect 1 (past decision_date silently
  using future evidence): the old `_effective_as_of()` took
  `max(decision_date, evidence_as_of)`, which silently overwrote an
  explicit, earlier `decision_date` with LATER evidence -- a real PIT
  lookahead violation the CIO directly reproduced. Fixed structurally, not
  patched: `clock/operational_scan.py`'s scanners now filter snapshots to
  `capture_date <= decision_date` BEFORE building any series (see that
  module). This orchestrator adds two further pieces:
    - `mode="OPERATIONAL"` (default): if a caller-supplied `decision_date`
      turns out to be BEHIND some market's real, unfiltered latest
      evidence, that is anomalous for live/operational use (a real
      operational `decision_date` should always be "today", always >= all
      evidence) -- fails closed with `DECISION_DATE_PRECEDES_EVIDENCE_AS_OF`
      rather than silently reconstructing a partial/wrong-looking view.
    - `mode="HISTORICAL_REPLAY"`: the same scanner-level filtering applies
      (evidence after `decision_date` is structurally invisible), but the
      "precedes" check above is skipped -- this is the genuine, intentional
      historical-replay use case the filtering was built to support.
  `decision_date` omitted entirely (the bare CLI artifact-reproduction
  mode) is unaffected: falls back to each market's own real latest
  evidence, byte-identical and reproducible, exactly as before.

★ CIO integration review round 1, defect 2 ("new" triggers leaking stale
  raw triggers as new on every re-run): "new" is no longer `opened_at ==
  evidence_as_of` (a date-equality check that stays true forever once
  fresh evidence stops arriving). `_load_previous_committed_episode_ids()`
  reads the market's own PREVIOUSLY COMMITTED `dynamic_clock_report.json`
  (if any) and an episode is "new" only if its `episode_id` was NOT already
  present there. No prior committed state at all (first bootstrap run) is
  explicitly `newness_status="NEWNESS_NOT_COMPUTABLE"` -- never defaulted
  to "everything is new". The raw per-trigger `new_triggers_this_run` list
  stays audit-only; the briefing's `new_triggers` is subject-level
  consolidated (`new_subjects_this_run`), matching every other briefing
  list.

★ CIO integration review round 1, defect 3 (post-hoc data physically
  present in the operational object): `build_subject_review_candidate()`
  (the `review_queue` entry) no longer carries `post_hoc_audit_note` or any
  `reference_forward_metrics_*` field at all. Those are built by the
  physically SEPARATE `clock/audit_diagnostics.py` into their own
  `audit_diagnostics` collection, written to its own committed file
  (`evidence/operational/dynamic_clock/audit_diagnostics.json`) --
  `run()`'s returned report (what `build_briefing_section()` and
  `dynamic_clock_report.json` are built from) never contains it. See
  `run_with_diagnostics()` for the one function that computes both.

  ★ CIO integration review round 2, defect 1 (the round-1 fix was only
  FIELD-STRIPPING, not physical separation): `_market_result()` used to
  compute `reference_forward_metrics_*`/`build_audit_diagnostic_record()`
  for EVERY subject unconditionally and only strip the key at the very
  end -- so `run()` still READ `replay.forward_metrics`/
  `clock.audit_diagnostics`/`clock.audit_confirmed_miss` and would still
  fail if any of them broke, even though the output never showed it. Fixed
  for real this round: `compute_forward_metrics`/
  `build_audit_diagnostic_record` are no longer imported at module top
  level at all. `_market_result()` (and therefore `run()`/`_assemble()`)
  contains NO call to either, anywhere. The only place that ever imports or
  calls them is the new `_market_diagnostics()`, lazily, and ONLY
  `run_with_diagnostics()` calls `_market_diagnostics()` -- via its own
  fully independent re-scan, not shared state with `_market_result()`. If
  `_market_diagnostics()` raised for any reason, `run()`'s result would be
  completely unaffected (`test_dynamic_clock_orchestrator_defects.py::
  Defect3AuditArtifactNeverReadByOperationalPathTests::
  test_operational_run_is_unaffected_if_audit_diagnostics_computation_raises`),
  and neither `compute_forward_metrics` nor `confirmed_miss_for` is called
  even once during a `run()` execution (mock call-count proof in the same
  test class).

★ CIO integration review round 1, defect 4 (missing PIT timing fields):
  `build_subject_review_candidate()` now requires `decision_at` and emits
  the full `evidence_as_of`/`trigger_observed_at`/`decision_at`/
  `price_as_of`/`candidate_created_at`/`candidate_updated_at`/
  `time_precision` set, independently validated (see
  `clock/review_candidate.py::_validate_candidate_timing`).

★ Authority: this module never sets Stage/Buy/Action/Order/Production/
  trading. Every record's `authority` block is hard-`False`/`None` (see
  `review_candidate.py`). A Trigger firing here is a re-review REQUEST
  only, and only `IMMEDIATE_REVIEW`-tier subject candidates ever carry
  `human_review_required=True` -- and `IMMEDIATE_REVIEW` itself can NEVER
  be reached today (no RATIFIED-basis thesis/price linkage exists yet).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay import asset_identity as ai
from replay.opportunity_trigger import canonical_json

from clock import dynamic_clock as dc
from clock import operational_scan as scan
from clock.candidate_validity_observation import (
    TRIGGER_LOCAL_REPRODUCTION,
    VALID_TRIGGER_KINDS,
    write_observation as write_validity_observation,
)
from clock.dynamic_clock import build_episode_history, close_stale_episodes
# ★ CIO integration review round 2, defect 1: `replay.forward_metrics.
#   compute_forward_metrics` and `clock.audit_diagnostics.
#   build_audit_diagnostic_record` are DELIBERATELY NOT imported at module
#   top level -- the operational `run()`/`_market_result()` path must never
#   read either, even transitively via an import. Only `_market_diagnostics()`
#   (called exclusively from `run_with_diagnostics()`) lazily imports them.
from clock.price_reflection_link import link_price_reflection, to_price_reflection_status
from clock.review_candidate import (
    TIER_IMMEDIATE_REVIEW, TIER_OBSERVATION_ONLY, TIER_WATCH_REVIEW,
    build_expired_record, build_raw_trigger_record, build_subject_review_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evidence" / "operational" / "dynamic_clock"
REPORT_PATH = OUT_DIR / "dynamic_clock_report.json"
AUDIT_DIAGNOSTICS_PATH = OUT_DIR / "audit_diagnostics.json"
VALIDITY_OBSERVATIONS_DIR = OUT_DIR / "candidate_validity_observations"
VALIDITY_SOURCE_REPORTS_DIR = OUT_DIR / "candidate_validity_source_reports"

PRIORITY_SUBJECTS = ("BTC", "005930", "000660")  # same regression priority set as PR #210

DATE_FMT = "%Y-%m-%d"

MODE_OPERATIONAL = "OPERATIONAL"
MODE_HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
VALID_MODES = (MODE_OPERATIONAL, MODE_HISTORICAL_REPLAY)


class DynamicClockOrchestratorError(ValueError):
    pass


def _validate_decision_date(decision_date: str | None) -> None:
    """Fails closed only on a malformed date string."""
    if decision_date is None:
        return
    try:
        dt.datetime.strptime(decision_date, DATE_FMT)
    except (ValueError, TypeError) as exc:
        raise DynamicClockOrchestratorError(f"DECISION_DATE_INVALID:{decision_date!r}") from exc


def _check_decision_date_not_behind_evidence(market: str, decision_date: str | None, mode: str) -> None:
    """Defect 1's fail-closed half: in OPERATIONAL mode, a `decision_date`
    behind the market's REAL, UNFILTERED latest evidence is anomalous (a
    genuine operational decision_date -- "today" -- should never be behind
    evidence that is already committed). `HISTORICAL_REPLAY` mode skips
    this deliberately -- that is its whole purpose."""
    if decision_date is None or mode == MODE_HISTORICAL_REPLAY:
        return
    raw_dates = scan.all_evidence_dates(market)
    raw_latest = max(raw_dates) if raw_dates else None
    if raw_latest is not None and raw_latest > decision_date:
        raise DynamicClockOrchestratorError(
            f"DECISION_DATE_PRECEDES_EVIDENCE_AS_OF:{market}:decision_date={decision_date} "
            f"< raw_latest_evidence={raw_latest} (pass mode=\"{MODE_HISTORICAL_REPLAY}\" for genuine "
            "historical replay)"
        )


def _pit_eligibility_status(subject: str, decision_date: str, kr_universe_codes) -> str:
    return ai.asset_identity_status(subject, decision_date, kr_universe_codes)


def _load_previous_committed_episode_ids(market: str) -> set[str] | None:
    """Defect 2: the market's set of `episode_id`s (== raw records'
    `candidate_id`) that were ACTIVE in the PREVIOUSLY COMMITTED
    `dynamic_clock_report.json`, or `None` if no prior committed state
    exists at all, or it is unreadable/schema-incompatible (fails closed to
    "not computable", never crashes and never defaults to "everything is
    new")."""
    if not REPORT_PATH.is_file():
        return None
    try:
        doc = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        market_result = doc["by_market"][market]
        return {r["candidate_id"] for r in market_result["raw_trigger_ledger"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _market_result(market: str, decision_date: str | None, mode: str) -> dict:
    """The OPERATIONAL result for one market. ★ CIO integration review
    round 2, defect 1: this function performs NO audit/forward-return
    computation of any kind, and imports neither
    `replay.forward_metrics.compute_forward_metrics` nor
    `clock.audit_diagnostics.build_audit_diagnostic_record` -- not even
    transitively. See `_market_diagnostics()` for the genuinely separate
    function that does."""
    _check_decision_date_not_behind_evidence(market, decision_date, mode)
    scanner = scan.MARKET_SCANNERS[market]
    # ★ defect 1: decision_date is passed straight into the scanner, which
    #   filters evidence at its own boundary -- never patched after the
    #   fact here.
    scan_result = scanner(decision_date)
    evidence_dates = scan_result["evidence_dates"]
    evidence_as_of = max(evidence_dates) if evidence_dates else None
    # decision_at: the operational "as of" date this market's candidates
    # are evaluated FOR. Structurally >= evidence_as_of now (the scanner
    # already guarantees evidence_as_of <= decision_date when one was
    # given), so no max()/correction is needed here at all.
    decision_at = decision_date if decision_date is not None else evidence_as_of
    kr_universe_codes = scan_result.get("kr_universe_codes")

    previous_episode_ids = _load_previous_committed_episode_ids(market)
    newness_status = "COMPUTABLE" if previous_episode_ids is not None else "NEWNESS_NOT_COMPUTABLE"

    raw_trigger_ledger: list[dict] = []
    expired_records: list[dict] = []
    new_triggers_this_run: list[dict] = []  # raw, audit-only -- never rendered directly in the briefing
    active_episodes_by_subject: dict[str, list[dict]] = {}

    for subject, by_type in sorted(scan_result["subjects"].items()):
        for trigger_type, events in sorted(by_type.items()):
            if not events:
                continue
            episodes = build_episode_history(subject, market, trigger_type, events)
            if decision_at is not None:
                episodes = close_stale_episodes(episodes, decision_at)
            for ep in episodes:
                if ep["status"] == "ACTIVE":
                    record = build_raw_trigger_record(ep)
                    raw_trigger_ledger.append(record)
                    active_episodes_by_subject.setdefault(subject, []).append(ep)
                    # ★ defect 2: "new" means "not in the previously
                    #   committed state" -- never a date-equality check,
                    #   and never defaulted to True when there is no prior
                    #   state to compare against.
                    if previous_episode_ids is not None and record["candidate_id"] not in previous_episode_ids:
                        new_triggers_this_run.append(record)
                elif ep["status"] == "EXPIRED":
                    expired_records.append(build_expired_record(ep))

    review_queue: list[dict] = []
    for subject, episodes in sorted(active_episodes_by_subject.items()):
        # PIT eligibility is checked as of the real operational "now"
        # (decision_at) -- this reveals nothing about the future (a
        # ratified taxonomy's effective_from is already a known, committed
        # fact as of any date <= today), unlike using a forward-looking
        # price outcome would.
        pit_status = _pit_eligibility_status(subject, decision_at or evidence_as_of, kr_universe_codes)

        # ★ P8-10 integration: real, independently re-validated
        #   price_reflection link (BTC/KOREA only -- see
        #   clock/price_reflection_link.py's docstring for CRYPTO's honest
        #   NOT_SUPPORTED_FOR_SUBJECT boundary). link_price_reflection()
        #   itself never raises (fail-closed per candidate).
        link_result = link_price_reflection(subject, market, decision_at)
        price_reflection_status = to_price_reflection_status(link_result)

        review_queue.append(build_subject_review_candidate(
            subject, market, episodes, pit_eligibility_status=pit_status,
            decision_at=decision_at,
            price_reflection_status=price_reflection_status,
        ))

    raw_trigger_ledger.sort(key=lambda r: (r["subject"], r["trigger_type"], r["candidate_id"]))
    expired_records.sort(key=lambda r: (r["subject"], r["trigger_type"], r["candidate_id"]))
    new_triggers_this_run.sort(key=lambda r: (r["subject"], r["trigger_type"], r["candidate_id"]))
    review_queue.sort(key=lambda r: (r["subject"], r["candidate_id"]))

    # ★ defect 2: subject-level consolidation of "new" -- the raw 95 stays
    #   in new_triggers_this_run (audit only); this is what the briefing
    #   is allowed to read from.
    new_subjects_this_run = sorted({r["subject"] for r in new_triggers_this_run})

    immediate_review = [r for r in review_queue if r["tier"] == TIER_IMMEDIATE_REVIEW]
    watch_review = [r for r in review_queue if r["tier"] == TIER_WATCH_REVIEW]
    observation_only = [r for r in review_queue if r["tier"] == TIER_OBSERVATION_ONLY]

    return {
        "market": market,
        "evidence_as_of": evidence_as_of,
        "decision_date": decision_at,
        "population_label": scan_result["population_label"],
        "not_computable_trigger_types": scan.not_computable_report(market),
        "subject_count": len(scan_result["subjects"]),
        "raw_trigger_count": len(raw_trigger_ledger),
        "raw_trigger_ledger": raw_trigger_ledger,
        "expired_triggers": expired_records,
        "newness_status": newness_status,
        "new_triggers_this_run": new_triggers_this_run,
        "new_subjects_this_run": new_subjects_this_run,
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


def _market_diagnostics(market: str, decision_date: str | None, mode: str) -> list[dict]:
    """★ CIO integration review round 2, defect 1: a GENUINELY separate
    computation path from `_market_result()` -- lazily imports
    `replay.forward_metrics.compute_forward_metrics` and
    `clock.audit_diagnostics.build_audit_diagnostic_record` (the only
    importer of `clock.audit_confirmed_miss` anywhere in this package)
    ONLY inside this function's own body, and independently re-scans and
    re-builds episode history rather than sharing any state with
    `_market_result()`. Called ONLY from `run_with_diagnostics()` -- never
    from `run()`/`_assemble()`. If this function raises for any reason (a
    broken audit file, a forward-metrics bug), `run()`'s own result is
    completely unaffected -- see
    `test_dynamic_clock_orchestrator_defects.py::
    Defect3AuditArtifactNeverReadByOperationalPathTests`."""
    from clock.audit_diagnostics import build_audit_diagnostic_record
    from replay.forward_metrics import compute_forward_metrics

    _check_decision_date_not_behind_evidence(market, decision_date, mode)
    scanner = scan.MARKET_SCANNERS[market]
    scan_result = scanner(decision_date)
    evidence_dates = scan_result["evidence_dates"]
    evidence_as_of = max(evidence_dates) if evidence_dates else None
    decision_at = decision_date if decision_date is not None else evidence_as_of
    # Audit diagnostics are intentionally post-hoc.  Episode membership is
    # reconstructed strictly at ``decision_date`` above, but forward-return
    # grading needs later bars that were not (and must not have been) visible
    # to the operational decision.  Read those bars from a separate unfiltered
    # scan used only inside this audit-only function.  They never flow back to
    # ``run()``/tiering/briefing.
    series_map = scanner(None).get("series", {})

    active_episodes_by_subject: dict[str, list[dict]] = {}
    for subject, by_type in sorted(scan_result["subjects"].items()):
        for trigger_type, events in sorted(by_type.items()):
            if not events:
                continue
            episodes = build_episode_history(subject, market, trigger_type, events)
            if decision_at is not None:
                episodes = close_stale_episodes(episodes, decision_at)
            for ep in episodes:
                if ep["status"] == "ACTIVE":
                    active_episodes_by_subject.setdefault(subject, []).append(ep)

    diagnostics: list[dict] = []
    for subject, episodes in sorted(active_episodes_by_subject.items()):
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
        diagnostics.append(build_audit_diagnostic_record(
            subject, market, episodes,
            reference_forward_metrics_first_detection=ref_metrics_first,
            reference_forward_metrics_latest_detection=ref_metrics_latest,
        ))
    diagnostics.sort(key=lambda r: r["subject"])
    return diagnostics


def _assemble(decision_date: str | None, mode: str) -> dict:
    if mode not in VALID_MODES:
        raise DynamicClockOrchestratorError(f"INVALID_MODE:{mode!r} (must be one of {VALID_MODES})")
    policy = dc.load_policy()
    _validate_decision_date(decision_date)

    by_market = {market: _market_result(market, decision_date, mode) for market in ("BTC", "KOREA", "CRYPTO")}
    all_evidence_dates = [m["evidence_as_of"] for m in by_market.values() if m["evidence_as_of"]]
    return {
        "wbs_item": "P8-12 Opportunity Trigger + Dynamic Review Clock",
        "report_asof_evidence_date": max(all_evidence_dates) if all_evidence_dates else None,
        "decision_date": decision_date,
        "mode": mode,
        "repo_history_starts_at": scan.ei.REPO_HISTORY_STARTS_AT,
        "policy_approval_status": policy["approval_status"],
        "policy_version": policy["policy_version"],
        "authority_note": (
            "A Trigger firing here is a re-review REQUEST only -- it is never a Buy signal and "
            "never confers Action/Order/trading authority. Only IMMEDIATE_REVIEW-tier subject "
            "candidates carry human_review_required=True; see each record's own `authority` block. "
            "IMMEDIATE_REVIEW cannot be reached today: no RATIFIED-basis thesis/price linkage exists yet."
        ),
        "by_market": by_market,
    }


def run(decision_date: str | None = None, mode: str = MODE_OPERATIONAL) -> dict:
    """The OPERATIONAL report: what `build_briefing_section()` and
    `dynamic_clock_report.json` are built from. ★ CIO integration review
    round 2, defect 1: this call path performs NO audit/forward-return
    computation at all -- `_market_result()` never imports or calls
    `compute_forward_metrics`/`build_audit_diagnostic_record`, so there is
    nothing to strip any more (round 1's fix only stripped the FIELD after
    computing it anyway; round 2 removes the computation itself). Use
    `run_with_diagnostics()` to also get the diagnostics artifact.

    `decision_date`, when supplied, is the real operational "today" this
    run is FOR. Never sourced from `datetime.now()` inside this function --
    the caller (a workflow's own shell `date` command, or
    `briefing/daily_orchestrator.py`'s own decision_date parameter) is
    responsible for supplying it."""
    return _assemble(decision_date, mode)


def run_with_diagnostics(decision_date: str | None = None, mode: str = MODE_OPERATIONAL) -> tuple[dict, dict]:
    """Returns `(operational_report, audit_diagnostics_report)`. ★ CIO
    integration review round 2, defect 1: the two are now computed via
    GENUINELY SEPARATE scans/code paths (`run()` -> `_market_result()` vs.
    `_market_diagnostics()`), not split out of one shared computation --
    this is the only function that calls `_market_diagnostics()` at all.
    `write_report()` uses this to persist the two as genuinely separate
    committed files."""
    operational_report = run(decision_date, mode)
    diagnostics_by_market = {
        market: _market_diagnostics(market, decision_date, mode)
        for market in ("BTC", "KOREA", "CRYPTO")
    }
    diagnostics_report = {
        "wbs_item": "P8-12 audit diagnostics (post-hoc, NEVER read by the operational path)",
        "decision_date": operational_report["decision_date"],
        "mode": operational_report["mode"],
        "report_asof_evidence_date": operational_report["report_asof_evidence_date"],
        "by_market": diagnostics_by_market,
    }
    return operational_report, diagnostics_report


def _briefing_candidate_summary(r: dict) -> dict:
    """The EXACT per-subject shape the integration spec's section 7
    requires -- subject, tier, trigger_types+confirmation_count,
    price_state, reflection_status (always "UNKNOWN" whenever present),
    data_state, threshold_basis, a data-as-of timestamp, a templated
    `reason`, and authority=REVIEW_ONLY/money_action=NONE. Deliberately
    excludes everything section 8 forbids: no forward return, no MFE/MAE,
    no post-hoc Miss/Defense result, no invented expected-return figure, no
    Buy/Entry/Order-style field or language."""
    pr = r["price_reflection_status"]
    linked = pr.get("status") == "LINKED"
    return {
        "subject": r["subject"],
        "tier": r["tier"],
        "trigger_types": r["trigger_types"],
        "confirmation_count": r["confirmation_count"],
        "price_state": pr.get("price_state") if linked else "UNKNOWN",
        "reflection_status": pr.get("reflection_status") if linked else "UNKNOWN",
        "data_state": pr.get("data_state") if linked else "NOT_LINKED",
        "threshold_basis": pr.get("threshold_basis") if linked else "N/A",
        "price_as_of": pr.get("price_as_of") if linked else "UNKNOWN",
        "next_review_at": r["next_review_at"],
        "reason": r["reason"],
        "authority": "REVIEW_ONLY",
        "money_action": "NONE",
    }


def build_briefing_section(report: dict) -> dict:
    """The user-facing artifact item 6/7/8 asks for -- ONLY the
    consolidated, tiered subject-level queue (never the raw per-trigger
    ledger, which stays in the full committed report for audit only), with
    per-tier counts and a plain-language, non-outcome-based `reason` per
    candidate. NO forward-return/MFE/post-hoc-audit figure, invented
    expected-return, or Buy/Entry/Order-style language appears anywhere in
    this section -- those don't even exist in `report` any more (defect 3).
    `new_triggers` is subject-level consolidated (defect 2), looked up from
    `review_queue` by `new_subjects_this_run` -- the raw
    `new_triggers_this_run` list is never read here."""
    section = {
        "policy_approval_status": report["policy_approval_status"],
        "policy_version": report["policy_version"],
        "decision_date": report["decision_date"],
        "mode": report["mode"],
        "markets": {},
    }
    for market, m in report["by_market"].items():
        candidates_by_subject = {r["subject"]: r for r in m["review_queue"]}
        new_subjects = m.get("new_subjects_this_run", [])
        section["markets"][market] = {
            "evidence_as_of": m["evidence_as_of"],
            "decision_date": m["decision_date"],
            "raw_trigger_count_audit_only": m["raw_trigger_count"],
            "tier_counts": m["tier_counts"],
            "newness_status": m["newness_status"],
            "new_triggers": [
                _briefing_candidate_summary(candidates_by_subject[s])
                for s in new_subjects if s in candidates_by_subject
            ],
            "immediate_review": [_briefing_candidate_summary(r) for r in m["immediate_review"]],
            "watch_review": [_briefing_candidate_summary(r) for r in m["watch_review"]],
            "observation_only_count": len(m["observation_only"]),
            "expired_triggers": [
                {"subject": r["subject"], "trigger_type": r["trigger_type"], "expiry": r["expiry"]}
                for r in m["expired_triggers"]
            ],
            "not_computable_trigger_types": [t["trigger_type"] for t in m["not_computable_trigger_types"]],
        }
        section["markets"][market]["calendar_confidence"] = dc.calendar_confidence_for(market)
    return section


def write_report(
    decision_date: str | None = None,
    mode: str = MODE_OPERATIONAL,
    *,
    observation_trigger_kind: str = TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    """Computes and persists BOTH committed artifacts -- the operational
    report/briefing section, and the physically separate audit_diagnostics
    file -- from a single scan. Returns the operational report (for
    `__main__`'s own printing)."""
    operational_report, diagnostics_report = run_with_diagnostics(decision_date, mode)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(canonical_json(operational_report) + "\n", encoding="utf-8")
    (OUT_DIR / "briefing_section.json").write_text(
        canonical_json(build_briefing_section(operational_report)) + "\n", encoding="utf-8")
    AUDIT_DIAGNOSTICS_PATH.write_text(canonical_json(diagnostics_report) + "\n", encoding="utf-8")
    # P8-12 validity-window v2: collect a separate append-only SHADOW
    # observation of real candidate timing.  This cannot open Risk
    # Capacity or P8-13; candidate_validity_observation.py hard-locks every
    # candidate to NOT_COMPUTABLE while the validity policy is unratified.
    write_validity_observation(
        operational_report,
        output_root=VALIDITY_OBSERVATIONS_DIR,
        source_output_root=VALIDITY_SOURCE_REPORTS_DIR,
        trigger_kind=observation_trigger_kind,
    )
    return operational_report


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
    parser.add_argument(
        "--mode", default=MODE_OPERATIONAL, choices=VALID_MODES,
        help=(
            "OPERATIONAL (default): fails closed if --decision-date is behind real evidence. "
            "HISTORICAL_REPLAY: genuine past reconstruction -- evidence after --decision-date is "
            "still structurally invisible, but the fail-closed anomaly check is skipped."
        ),
    )
    parser.add_argument(
        "--observation-trigger-kind",
        default=TRIGGER_LOCAL_REPRODUCTION,
        choices=VALID_TRIGGER_KINDS,
        help=(
            "Provenance of this run for the append-only Candidate Validity Shadow observation. "
            "This labels natural workflow_run samples separately from manual dispatches and "
            "local reproductions; it never changes candidate tier or authority."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    report = write_report(
        decision_date=args.decision_date,
        mode=args.mode,
        observation_trigger_kind=args.observation_trigger_kind,
    )
    print(json.dumps(build_briefing_section(report), ensure_ascii=False, indent=2, default=str))
