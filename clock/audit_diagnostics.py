#!/usr/bin/env python3
"""P8-12 audit diagnostics -- POST-HOC information, PHYSICALLY SEPARATED
from the operational candidate/briefing path (CIO integration review round
1, defect 3).

The original `clock/review_candidate.py::build_subject_review_candidate`
embedded `post_hoc_audit_note` (PR #210's real Miss-episode registry) and
`reference_forward_metrics_*` (forward return/MFE/MAE, reused from PR
#210's `replay.forward_metrics`) directly on the operational candidate
object. Not being an INPUT to `compute_tier()` was not enough: any other
downstream consumer of that same object could still read a forward-return
figure sitting right next to `tier`. This module builds a genuinely
SEPARATE artifact instead.

★ `clock.audit_confirmed_miss` is imported ONLY here -- never by
  `clock/review_candidate.py` or `briefing/daily_orchestrator.py`'s
  Dynamic Clock rendering path. `test/test_price_reflection_link.py::
  test_operational_path_never_imports_audit_module` asserts this
  structurally by scanning source, not just by convention.

★ `clock/run_dynamic_clock.py` calls `build_audit_diagnostic_record()`
  SEPARATELY from `build_subject_review_candidate()` and writes the result
  to its own file (`evidence/operational/dynamic_clock/audit_diagnostics.json`)
  -- never merged into `dynamic_clock_report.json`'s `review_queue`.
"""
from __future__ import annotations

from clock.audit_confirmed_miss import confirmed_miss_for


def _post_hoc_audit_note(subject: str, active_episodes: list[dict]) -> dict | None:
    """PR #210's real, committed Miss-episode registry, read for
    REGRESSION-EXPLANATION/EVALUATION PURPOSES ONLY -- never fed into
    `compute_tier()`, and (as of this module) never even reachable from the
    operational candidate-generation import graph."""
    for ep in active_episodes:
        for ev in ep["evidence_trail"]:
            match = confirmed_miss_for(subject, ev["detected_at"])
            if match is not None:
                return {
                    "authoritative_for_tier": False,
                    "purpose": "post_hoc_regression_explanation_only",
                    "matched_trigger_type": ep["trigger_type"],
                    "matched_detected_at": ev["detected_at"],
                    "pr210_episode_id": match.get("episode_id"),
                    "pr210_root_cause": match.get("root_cause"),
                    "pr210_representative_forward_return_pct": match.get("representative_forward_return_pct"),
                    "pr210_episode_window": [match.get("episode_start_date"), match.get("episode_end_date")],
                }
    return None


def build_audit_diagnostic_record(
    subject: str, market: str, active_episodes: list[dict], *,
    reference_forward_metrics_first_detection: dict | None = None,
    reference_forward_metrics_latest_detection: dict | None = None,
) -> dict:
    """The physically-separate audit artifact for one subject: PR #210's
    post-hoc Miss note plus PR #210's forward-return/MFE/MAE diagnostics
    (`replay.forward_metrics.compute_forward_metrics`, reused verbatim).
    Never consumed by `compute_tier()`, `build_subject_review_candidate()`,
    or the briefing -- audit/regression-explanation only."""
    return {
        "subject": subject,
        "market": market,
        "post_hoc_audit_note": _post_hoc_audit_note(subject, active_episodes),
        "reference_forward_metrics_first_detection": reference_forward_metrics_first_detection,
        "reference_forward_metrics_latest_detection": reference_forward_metrics_latest_detection,
        "note": (
            "post-hoc diagnostic only -- reused verbatim from replay.forward_metrics."
            "compute_forward_metrics (PR #210's anti-backdated-entry invariant) and "
            "clock.audit_confirmed_miss (PR #210's real Miss-episode registry). NEVER an "
            "input to tier or an entry authorization; physically separate from the "
            "operational review_queue candidate on purpose (CIO integration review round "
            "1, defect 3)."
        ),
    }
