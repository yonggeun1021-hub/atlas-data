#!/usr/bin/env python3
"""PAPER scorecard single contract v1: episode rows, clusters, checkpoints, cooling-off.

Ratified inputs (via ``config/paper_execution_core_v1.json``):

* RULE.SCORECARD.SINGLE_CONTRACT.V1 (D11, record 10de02bf...): one scorecard
  contract; grouped by strength episode; judged only at scheduled checkpoints;
  the minimum sample starts review (never a pass).
* RULE.GOVERNANCE.COOLING_OFF.V1 (P6, record 2a94be2b...): performance-reason
  re-adjustment proposals wait until the later of KR/US 20 trading days or
  crypto 30 days from the new rule version's effective date and the day the
  rule's minimum sample is refilled with post-effective data; bug fixes,
  clarifications, new evidence and conflict resolution are not blocked.

Canon 9-2 (source document of D11): unit = position episode; clusters =
strength episode and entry date, the wider cluster-robust interval is used;
n_eff = number of clusters; checkpoints n_eff 30, 80, 160, then every 80;
cumulative figures shown as '참고, 판정 아님'; below 30 '표본 부족 n/30'.
Badge thresholds (t levels, risk-policy premium / CVaR10) are not computed
here (config not_defined SCORECARD_BADGE_THRESHOLDS).

Exact arithmetic only: squared standard errors are compared, no floats.
Pure and offline.
"""
from __future__ import annotations

import datetime as dt
from fractions import Fraction
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio import paper_execution_core as CORE  # noqa: E402


SUMMARY_SCHEMA_VERSION = "paper_scorecard_summary/1"
COOLING_OFF_SCHEMA_VERSION = "paper_readjustment_cooling_off/1"
ROW_FIELDS = {"position_episode_id", "strength_episode_id", "market", "entry_date", "rule_id",
              "metric_net_of_cost", "uses_recalculated_rotation_days", "nav_verification"}
RULE_D11 = "RULE.SCORECARD.SINGLE_CONTRACT.V1"
RULE_COOL = "RULE.GOVERNANCE.COOLING_OFF.V1"
RULE_RECALC = "RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1"


def _clustered_se_squared(values: list, clusters: list):
    groups = {}
    for value, cluster in zip(values, clusters):
        groups.setdefault(cluster, []).append(value)
    count_g, count_n = len(groups), len(values)
    if count_g <= 1:
        return count_g, None
    mean = sum(values, Fraction(0)) / count_n
    deviations = [sum((v - mean for v in members), Fraction(0)) for members in groups.values()]
    score = sum((d * d for d in deviations), Fraction(0))
    return count_g, Fraction(count_g, count_g - 1) * score / (count_n * count_n)


def checkpoints_up_to(core, n_eff: int) -> list:
    spec = core.interpretations["scorecard"]
    points = list(spec["checkpoints_n_eff"])
    while points[-1] + spec["checkpoint_step_after_last"] <= n_eff:
        points.append(points[-1] + spec["checkpoint_step_after_last"])
    return [p for p in points if p <= n_eff]


def scorecard_summary(core, *, rule_id: str, rows: list, as_of_utc: str, scheduled_check: bool,
                      last_judged_checkpoint) -> dict:
    """Sample state of one rule's scorecard; judgement allowed only at a new checkpoint."""
    if core.param("scorecard_single_contract") is not True or core.param("scorecard_evaluation_unit") != "STRENGTH_EPISODE" \
            or core.param("scorecard_judgement_timing") != "SCHEDULED_CHECKPOINTS_ONLY":
        CORE.fail("D11_RULE_UNEXPECTED")
    if rule_id not in core.context.rules:
        CORE.fail("SCORECARD_RULE_NOT_REGISTERED", str(rule_id))
    if scheduled_check not in (True, False):
        CORE.fail("SCHEDULED_CHECK_FLAG_INVALID")
    values, by_episode, by_date, seen, recalculated, unverified_nav = [], [], [], set(), 0, 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_FIELDS:
            CORE.fail("SCORECARD_ROW_FIELDS_INVALID")
        if row["rule_id"] != rule_id:
            CORE.fail("SCORECARD_ROW_OTHER_RULE")
        CORE.require_market(row["market"])
        CORE.require_token(row["position_episode_id"], "position_episode_id")
        CORE.require_token(row["strength_episode_id"], "strength_episode_id")
        dt.date.fromisoformat(row["entry_date"])
        if row["position_episode_id"] in seen:
            CORE.fail("SCORECARD_ROW_DUPLICATE_EPISODE", row["position_episode_id"])
        if row["uses_recalculated_rotation_days"] not in (True, False):
            CORE.fail("RECALC_FLAG_INVALID")
        if row["nav_verification"] not in ("VERIFIED", "UNVERIFIED"):
            CORE.fail("NAV_VERIFICATION_INVALID")
        unverified_nav += row["nav_verification"] == "UNVERIFIED"
        seen.add(row["position_episode_id"])
        recalculated += row["uses_recalculated_rotation_days"]
        values.append(CORE.frac(row["metric_net_of_cost"], "metric_net_of_cost"))
        by_episode.append(row["strength_episode_id"])
        by_date.append(row["entry_date"])
    g_episode, se_episode = _clustered_se_squared(values, by_episode)
    g_date, se_date = _clustered_se_squared(values, by_date)
    if se_episode is None or se_date is None:
        chosen, n_eff = "INSUFFICIENT_CLUSTERS", min(g_episode, g_date)
    elif se_episode >= se_date:
        chosen, n_eff = "STRENGTH_EPISODE", g_episode
    else:
        chosen, n_eff = "ENTRY_DATE", g_date
    spec = core.interpretations["scorecard"]
    reached = checkpoints_up_to(core, n_eff)
    if last_judged_checkpoint is not None and last_judged_checkpoint not in reached:
        CORE.fail("LAST_JUDGED_CHECKPOINT_INVALID")
    new_checkpoint = reached[-1] if reached and reached[-1] != last_judged_checkpoint else None
    judgement_allowed = bool(scheduled_check and new_checkpoint is not None)
    minimum = spec["observation_minimum_n_eff"]
    record = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "rule_id": rule_id,
        "as_of_utc": as_of_utc,
        "unit": spec["unit"],
        "position_episodes": len(values),
        "clusters": {"STRENGTH_EPISODE": g_episode, "ENTRY_DATE": g_date},
        "clustered_se_squared": {"STRENGTH_EPISODE": CORE.opt_fstr(se_episode), "ENTRY_DATE": CORE.opt_fstr(se_date)},
        "cluster_used": chosen,
        "n_eff": n_eff,
        "mean_metric_reference": CORE.fstr(sum(values, Fraction(0)) / len(values)) if values else None,
        "checkpoints_reached": reached,
        "new_checkpoint": new_checkpoint,
        "judgement_allowed": judgement_allowed,
        "minimum_sample_role": core.param("scorecard_minimum_sample_role"),
        "sample_label_ko": f"{spec['display_insufficient_ko']} {n_eff}/{minimum}" if n_eff < minimum else None,
        "cumulative_label_ko": spec["display_reference_ko"],
        "recalculated_rotation_rows": recalculated,
        "recalculated_mark_ko": core.param("recalculated_mark") if recalculated else None,
        "nav_unverified_rows": unverified_nav,
        "nav_status_ko": core.param("fx_staleness")["display"] if unverified_nav else None,
        "badges": "NOT_DEFINED:SCORECARD_BADGE_THRESHOLDS",
        "rule_refs": core.rule_refs([(RULE_D11, "APPLIED")] + ([(RULE_RECALC, "APPLIED")] if recalculated else []),
                                    as_of_utc),
    }
    return CORE.sign(record, "record_sha256")


def readjustment_proposal_gate(core, *, rule_id: str, market: str, reason: str, as_of_date: str,
                               kr_us_trading_days_since_effective, post_effective_sample_n: int) -> dict:
    """RULE.GOVERNANCE.COOLING_OFF.V1: may a re-adjustment proposal be raised today?

    ``reason``: PERFORMANCE or one of the not-blocked reasons.  KR/US trading
    days come from the official calendar (caller input); crypto counts days.
    The rule's minimum sample is the registry row's ``minimum_sample``; when
    the row has none, the sample condition is NOT_DEFINED and a performance
    proposal stays blocked.
    """
    CORE.require_market(market)
    row = core.context.rules.get(rule_id)
    if row is None or row["effective_from"] is None:
        CORE.fail("COOLING_OFF_RULE_NOT_DECIDED", str(rule_id))
    not_blocked = core.param("cooling_off_not_blocked")
    calendar = core.param("cooling_off_calendar_minimum")
    if core.param("cooling_off_combine") != "LATER_OF":
        CORE.fail("COOLING_OFF_RULE_UNEXPECTED")
    if reason != "PERFORMANCE" and reason not in not_blocked:
        CORE.fail("READJUSTMENT_REASON_INVALID", str(reason))
    effective_date = dt.date.fromisoformat(row["effective_from"]["utc"].split("T")[0])
    today = dt.date.fromisoformat(as_of_date)
    if today < effective_date:
        CORE.fail("AS_OF_BEFORE_EFFECTIVE_DATE")
    need = calendar[market]
    if need["unit"] == "DAYS":
        elapsed = (today - effective_date).days
    else:
        if not isinstance(kr_us_trading_days_since_effective, int) or kr_us_trading_days_since_effective < 0:
            CORE.fail("TRADING_DAYS_INPUT_REQUIRED")
        elapsed = kr_us_trading_days_since_effective
    calendar_met = elapsed >= need["value"]
    minimum = row["minimum_sample"]
    sample_met = None if minimum is None else post_effective_sample_n >= minimum["value"]
    if reason != "PERFORMANCE":
        allowed, status = True, "NOT_BLOCKED_REASON"
    elif sample_met is None:
        # The D11 record states no numeric minimum sample to fall back on.
        allowed, status = False, "MIN_SAMPLE_NOT_DEFINED"
    else:
        allowed = calendar_met and sample_met
        status = "ALLOWED" if allowed else "BLOCKED:COOLING_OFF"
    record = {
        "schema_version": COOLING_OFF_SCHEMA_VERSION,
        "rule_id": rule_id,
        "rule_version": row["version"],
        "market": market,
        "reason": reason,
        "as_of_date": as_of_date,
        "effective_date": effective_date.isoformat(),
        "calendar_requirement": need,
        "calendar_elapsed": elapsed,
        "calendar_met": calendar_met,
        "minimum_sample": None if minimum is None else {"value": minimum["value"], "unit": minimum["unit"]},
        "post_effective_sample_n": post_effective_sample_n,
        "sample_met": sample_met,
        "allowed": allowed,
        "status": status,
        "rule_refs": core.rule_refs([(RULE_COOL, "APPLIED" if allowed else "BLOCKED_BY")], f"{as_of_date}T23:59:59Z"),
    }
    return CORE.sign(record, "record_sha256")
