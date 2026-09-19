#!/usr/bin/env python3
"""PAPER mechanical validation checklist v1 (P1-P18 + operational safety thresholds).

Ratified input: RULE.VALIDATION.MECHANICAL_ONLY.V1 (D10, record 10de02bf...)
-- PAPER_VALIDATED means only "기계 검증 완료": the path checklist and the
operational safety thresholds passed; KRX 9-8 performance criteria are
display only.  The path list, evidence kinds and thresholds are canon 8-2 /
8-3 (source document of that ratification); build plan section 10 G6 keeps P1
natural-only with no fixture substitution.

Evidence kinds: N natural, F fixed-input replay (production code, synthetic
input), N/F natural or -- only after the observation window closed without a
natural occurrence -- F marked '자연 미관찰', N+F / F+N both kinds required.

Performance never changes this verdict; the performance label stays
'투자 성과 표본 부족' until the scorecard contract reports enough sample.

Pure and offline.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio import paper_execution_core as CORE  # noqa: E402


CHECKLIST_SCHEMA_VERSION = "paper_mechanical_checklist/1"
RULE_D10 = "RULE.VALIDATION.MECHANICAL_ONLY.V1"
EVIDENCE_FIELDS = {"path_id", "kind", "passed", "evidence_sha256"}
SAFETY_FIELDS = {
    "observed_sessions", "scheduled_runs_expected", "scheduled_runs_executed", "duplicate_ledger_mutations",
    "reconciliation_mismatches", "silent_errors", "restart_recovery_attempts", "restart_recovery_successes",
    "stale_data_orders",
}


def _path_status(spec: dict, items: list, window_closed: bool) -> dict:
    kinds = {i["kind"] for i in items if i["passed"] is True}
    failed = [i["evidence_sha256"] for i in items if i["passed"] is not True]
    required = spec["evidence"]
    natural_not_observed = False
    if failed:
        status = "FAIL"
    elif required == "N":
        status = "PASS" if "N" in kinds else "PENDING_NATURAL"
    elif required == "F":
        status = "PASS" if "F" in kinds else "PENDING_FIXTURE"
    elif required in ("N+F", "F+N"):
        status = "PASS" if {"N", "F"} <= kinds else "PENDING_BOTH_KINDS"
    elif "N" in kinds:
        status = "PASS"
    elif "F" in kinds and window_closed:
        status, natural_not_observed = "PASS", True
    else:
        status = "PENDING_NATURAL_WINDOW_OPEN" if "F" in kinds else "PENDING"
    return {"status": status, "required_evidence": required, "passed_kinds": sorted(kinds),
            "natural_not_observed": natural_not_observed, "failed_evidence": sorted(failed)}


def operational_safety(core, metrics: dict) -> dict:
    thresholds = core.interpretations["operational_safety"]
    if not isinstance(metrics, dict) or set(metrics) != SAFETY_FIELDS:
        CORE.fail("SAFETY_METRICS_FIELDS_INVALID")
    for key, value in metrics.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            CORE.fail("SAFETY_METRIC_INVALID", key)
    if metrics["scheduled_runs_executed"] > metrics["scheduled_runs_expected"] \
            or metrics["restart_recovery_successes"] > metrics["restart_recovery_attempts"]:
        CORE.fail("SAFETY_METRICS_INCONSISTENT")
    coverage = None if metrics["scheduled_runs_expected"] == 0 else \
        Fraction(metrics["scheduled_runs_executed"], metrics["scheduled_runs_expected"])
    recovery = None if metrics["restart_recovery_attempts"] == 0 else \
        Fraction(metrics["restart_recovery_successes"], metrics["restart_recovery_attempts"])
    checks = {
        "observed_sessions": metrics["observed_sessions"] >= thresholds["min_observed_sessions"],
        "scheduled_run_coverage": coverage is not None and coverage >= CORE.frac(thresholds["min_scheduled_run_coverage"]),
        "duplicate_ledger_mutations": metrics["duplicate_ledger_mutations"] <= thresholds["max_duplicate_ledger_mutations"],
        "reconciliation_mismatches": metrics["reconciliation_mismatches"] <= thresholds["max_reconciliation_mismatches"],
        "silent_errors": metrics["silent_errors"] <= thresholds["max_silent_errors"],
        "restart_recovery": metrics["restart_recovery_attempts"] >= thresholds["min_restart_recovery_attempts"]
        and recovery is not None and recovery >= CORE.frac(thresholds["min_restart_recovery_success_rate"]),
        "stale_data_orders": metrics["stale_data_orders"] <= thresholds["max_stale_data_orders"],
    }
    # A breach (something that went wrong) is a failure; a not-yet-met count is pending.
    breaches = sorted(k for k in ("duplicate_ledger_mutations", "reconciliation_mismatches", "silent_errors",
                                  "stale_data_orders") if not checks[k])
    if recovery is not None and recovery < CORE.frac(thresholds["min_restart_recovery_success_rate"]):
        breaches.append("restart_recovery")
    if coverage is not None and coverage < CORE.frac(thresholds["min_scheduled_run_coverage"]) \
            and metrics["observed_sessions"] >= thresholds["min_observed_sessions"]:
        breaches.append("scheduled_run_coverage")
    status = "FAIL" if breaches else "PASS" if all(checks.values()) else "PENDING"
    return {"status": status, "checks": checks, "breaches": sorted(breaches),
            "scheduled_run_coverage": CORE.opt_fstr(coverage), "restart_recovery_rate": CORE.opt_fstr(recovery)}


def evaluate_checklist(core, *, market: str, as_of_utc: str, cohort: str, evidence: list,
                       observation_window_closed: bool, safety_metrics: dict,
                       performance_sample_state: str = "INSUFFICIENT", display_only_performance: dict | None = None) -> dict:
    """Mechanical validation state for one market.

    ``cohort``: INVESTMENT_PAPER or SYSTEM_CANARY (canary is never counted as
    performance sample; its mechanical checks are still reported).
    """
    CORE.require_market(market)
    if cohort not in ("INVESTMENT_PAPER", "SYSTEM_CANARY"):
        CORE.fail("COHORT_INVALID")
    if observation_window_closed not in (True, False):
        CORE.fail("WINDOW_FLAG_INVALID")
    if core.param("paper_validated_meaning") != "MECHANICAL_VALIDATION_ONLY" \
            or core.param("krx_9_8_performance_criteria") != "DISPLAY_ONLY":
        CORE.fail("D10_RULE_UNEXPECTED")
    spec = core.interpretations["mechanical_checklist"]
    performance_labels = spec["performance_state_display_ko"]
    if performance_sample_state not in performance_labels:
        CORE.fail("PERFORMANCE_SAMPLE_STATE_INVALID", str(performance_sample_state))
    by_path = {path_id: [] for path_id in spec["paths"]}
    for item in evidence:
        if not isinstance(item, dict) or set(item) != EVIDENCE_FIELDS:
            CORE.fail("EVIDENCE_FIELDS_INVALID")
        if item["path_id"] not in by_path or item["kind"] not in ("N", "F") or item["passed"] not in (True, False):
            CORE.fail("EVIDENCE_ITEM_INVALID", str(item.get("path_id")))
        if CORE.SHA256_RE.fullmatch(str(item["evidence_sha256"])) is None:
            CORE.fail("EVIDENCE_SHA_INVALID", item["path_id"])
        by_path[item["path_id"]].append(item)
    paths = {pid: _path_status(spec["paths"][pid], items, observation_window_closed) for pid, items in by_path.items()}
    safety = operational_safety(core, safety_metrics)
    labels = spec["status_display_ko"]
    if safety["status"] == "FAIL" or any(p["status"] == "FAIL" for p in paths.values()):
        verdict = "FAILED"  # operational safety failure overrides any sample status (canon 8-3)
    elif safety["status"] == "PASS" and all(p["status"] == "PASS" for p in paths.values()):
        verdict = "MECHANICAL_VALIDATED"
    else:
        verdict = "IN_PROGRESS"
    display = labels["validated"] if verdict == "MECHANICAL_VALIDATED" else labels["in_progress"]
    record = {
        "schema_version": CHECKLIST_SCHEMA_VERSION,
        "market": market,
        "cohort": cohort,
        "as_of_utc": as_of_utc,
        "paths": paths,
        "natural_not_observed_paths": sorted(pid for pid, p in paths.items() if p["natural_not_observed"]),
        "natural_not_observed_ko": labels["natural_not_observed"],
        "operational_safety": safety,
        "verdict": verdict,
        "paper_validated_means": core.param("paper_validated_meaning"),
        "display_ko": f"{display} / {performance_labels[performance_sample_state]}",
        "performance_sample_state": performance_sample_state,
        "performance_counted": cohort == "INVESTMENT_PAPER",
        # KRX 9-8 performance numbers are carried for display only and never read.
        "display_only_performance": display_only_performance,
        "config_sha256": core.config_sha256,
        "rule_refs": core.rule_refs([(RULE_D10, "APPLIED")], as_of_utc),
    }
    return CORE.sign(record, "record_sha256")
