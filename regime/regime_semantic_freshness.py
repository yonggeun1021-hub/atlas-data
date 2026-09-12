#!/usr/bin/env python3
"""Evaluate the ratified G4 source-frequency semantic freshness policy.

CIO decision identity ``CIO-P1-COM-05-NORMALIZATION-FRESHNESS-PIT-FINAL-V1-
2026-09-12`` (see ``docs/p1_com_05_cio_final_verdict_20260912.md`` and
``config/regime_semantic_freshness_policy_v1.json``) ratified a
source-frequency *semantic* freshness rule for US and KR, explicitly instead
of a numeric-seconds TTL:

* Session-based axes (US TREND/BREADTH/LEADERSHIP; all five KR axes) must
  exactly match the latest officially completed session. An observation from
  an earlier session while a newer completed session exists is UNKNOWN with
  reason ``SOURCE_NOT_ADVANCED_EXPECTED_SESSION``. No carry-forward or
  substitution is permitted.
* Release-based axes (US ``RISK_VOL``=VIXCLS daily, US ``LIQUIDITY``=
  WRESBAL/TOTBKCR weekly) require the latest successfully fetched,
  hash-retained publication. An unchanged value versus the prior observation
  is a normal, fresh outcome for that frequency. The evidence/hash
  requirement is already enforced upstream by
  ``regime/output_contract.py``'s ``DEFINED`` factor shape (``evidence.uri``/
  ``evidence.sha256`` are mandatory there), so a ``DEFINED`` release-based
  factor already satisfies "successful current fetch + retained hash".
* Missing, failed, calendar-unknown, or not-advanced evidence is immediate
  UNKNOWN. There is no numeric TTL anywhere in this module.

This module does not fetch or compute any market calendar itself. Determining
"the latest officially completed session" for a market is a separate,
already-existing capability (``regime/us_market_judgement.py``,
``market_judgement/krx_market_judgement.py``, the ``official_calendar`` block
in ``config/regime_source_owner_registry_v2.json``). Callers must supply it;
this module only compares. Likewise, the KR same-session 18:00 KST usability
gate is *reused*, not re-declared: this module reads the live
``earliest_usable_time`` value out of ``config/korea_leadership_policy.json``
itself (failing closed if that file drifts from its pinned hash) rather than
copying the value. The authoritative enforcement of that gate remains
``market_judgement/krx_market_judgement.py``; the check here is an additional,
optional, defensive convenience for direct callers of this contract.

Authorizes nothing beyond freshness evaluation. No action, capital, order,
trading, Stage, Buy, or Production authority is granted here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from typing import Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POLICY_PATH = ROOT / "config" / "regime_semantic_freshness_policy_v1.json"
KOREA_LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"

CONTRACT_VERSION = "regime_semantic_freshness_policy/v1"
SESSION_EXACT_MATCH = "SESSION_EXACT_MATCH"
RELEASE_CYCLE_LATEST_FETCH = "RELEASE_CYCLE_LATEST_FETCH"
FRESH = "FRESH"
UNKNOWN = "UNKNOWN"

REASON_AXIS_UNDEFINED = "AXIS_EVIDENCE_UNDEFINED"
REASON_NOT_ADVANCED = "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
REASON_CALENDAR_UNKNOWN = "EXPECTED_COMPLETED_SESSION_CALENDAR_UNKNOWN"
REASON_OBSERVATION_INVALID = "OBSERVATION_DATE_INVALID"
REASON_KR_BEFORE_USABLE_TIME = "KR_SESSION_BEFORE_EARLIEST_USABLE_TIME"

REQUIRED_AXES = ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP")


class RegimeSemanticFreshnessError(ValueError):
    """Fail-closed semantic-freshness policy violation."""


def fail(code: str, detail: str) -> None:
    raise RegimeSemanticFreshnessError(f"{code}:{detail}")


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_READ_FAILED", f"{path}:{exc}")


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("SOURCE_FILE_MISSING", f"{path}:{exc}")


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = _read_json(path)
    if not isinstance(policy, dict):
        fail("POLICY_INVALID", "object required")
    if (
        policy.get("contract_version") != CONTRACT_VERSION
        or policy.get("policy_status") != "RATIFIED"
    ):
        fail("POLICY_INVALID", "contract_version or policy_status")
    markets = policy.get("markets")
    if not isinstance(markets, dict) or set(markets) != {"US", "KR"}:
        fail("POLICY_INVALID", "markets")
    gate = markets["KR"].get("same_session_usability_gate")
    if not isinstance(gate, dict):
        fail("POLICY_INVALID", "KR same_session_usability_gate")
    live_sha256 = _file_sha256(KOREA_LEADERSHIP_POLICY_PATH)
    if gate.get("policy_sha256") != live_sha256:
        fail("KR_USABILITY_GATE_HASH_MISMATCH", KOREA_LEADERSHIP_POLICY_PATH.name)
    return policy


def _axis_rule(policy: dict, market: str, axis: str) -> tuple[str, dict]:
    block = policy["markets"].get(market)
    if not isinstance(block, dict):
        fail("MARKET_INVALID", str(market))
    session_axes = block.get("session_based_axes", {})
    release_axes = block.get("release_based_axes", {})
    if axis in session_axes:
        return SESSION_EXACT_MATCH, session_axes[axis]
    if axis in release_axes:
        return RELEASE_CYCLE_LATEST_FETCH, release_axes[axis]
    fail("AXIS_NOT_COVERED_BY_FRESHNESS_POLICY", f"{market}:{axis}")


def _kr_earliest_usable_time(policy: dict) -> tuple[dt.time, str]:
    gate = policy["markets"]["KR"]["same_session_usability_gate"]
    live = _read_json(KOREA_LEADERSHIP_POLICY_PATH)
    if not isinstance(live, dict) or live.get("approval_status") != "RATIFIED":
        fail("KR_USABILITY_GATE_INVALID", "approval_status")
    value = live.get(gate["earliest_usable_time_field"])
    if value != gate["earliest_usable_time_value"]:
        fail("KR_USABILITY_GATE_DRIFTED", str(value))
    return dt.time.fromisoformat(value), gate["earliest_usable_time_zone"]


def evaluate_axis_freshness(
    market: str,
    axis: str,
    factor: dict,
    *,
    expected_completed_session_date: Optional[str] = None,
    decision_at: Optional[str] = None,
    policy: Optional[dict] = None,
) -> dict:
    """Evaluate one axis's factor against the ratified semantic rule.

    ``factor`` is a ``regime_output/v1`` ``factor_results[axis]`` entry
    (``status``/``observation_date``/``available_at``). This function performs
    no I/O beyond loading the freshness policy itself; the caller owns
    producing ``expected_completed_session_date`` from the market's own
    calendar authority.
    """
    policy = load_policy() if policy is None else policy
    if axis not in REQUIRED_AXES:
        fail("AXIS_UNKNOWN", str(axis))
    freshness_form, rule = _axis_rule(policy, market, axis)

    if not isinstance(factor, dict) or factor.get("status") not in ("DEFINED", "UNDEFINED"):
        fail("FACTOR_INVALID", f"{market}:{axis}")
    if factor["status"] != "DEFINED":
        return {
            "market": market,
            "axis": axis,
            "freshness_form": freshness_form,
            "freshness_status": UNKNOWN,
            "reason": REASON_AXIS_UNDEFINED,
        }

    if freshness_form == RELEASE_CYCLE_LATEST_FETCH:
        # A DEFINED factor already carries a validated evidence.uri/sha256
        # pair (regime/output_contract.py::defined_factor), which is exactly
        # "successful current fetch + retained source/hash validation". An
        # unchanged value versus the prior observation is not evaluated here
        # (and is not a staleness signal) because this module never compares
        # values across dates.
        return {
            "market": market,
            "axis": axis,
            "freshness_form": freshness_form,
            "freshness_status": FRESH,
            "reason": None,
        }

    # SESSION_EXACT_MATCH
    if expected_completed_session_date is None:
        return {
            "market": market,
            "axis": axis,
            "freshness_form": freshness_form,
            "freshness_status": UNKNOWN,
            "reason": REASON_CALENDAR_UNKNOWN,
        }
    try:
        expected = dt.date.fromisoformat(expected_completed_session_date)
        observed = dt.date.fromisoformat(factor["observation_date"])
    except (TypeError, ValueError):
        fail("OBSERVATION_DATE_UNPARSEABLE", f"{market}:{axis}")
    if observed == expected:
        status, reason = FRESH, None
    elif observed < expected:
        status, reason = UNKNOWN, REASON_NOT_ADVANCED
    else:
        status, reason = UNKNOWN, REASON_OBSERVATION_INVALID

    if (
        status == FRESH
        and market == "KR"
        and decision_at is not None
    ):
        earliest_time, zone = _kr_earliest_usable_time(policy)
        try:
            parsed_decision_at = dt.datetime.strptime(
                decision_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            fail("DECISION_AT_INVALID", str(decision_at))
        local = parsed_decision_at.astimezone(ZoneInfo(zone))
        if local.date() == expected and local.time() < earliest_time:
            status, reason = UNKNOWN, REASON_KR_BEFORE_USABLE_TIME

    return {
        "market": market,
        "axis": axis,
        "freshness_form": freshness_form,
        "freshness_status": status,
        "reason": reason,
    }


def evaluate_market_freshness(
    market: str,
    factor_results: dict,
    *,
    expected_completed_session_date: Optional[str] = None,
    decision_at: Optional[str] = None,
    policy: Optional[dict] = None,
) -> dict:
    policy = load_policy() if policy is None else policy
    if not isinstance(factor_results, dict) or set(factor_results) != set(REQUIRED_AXES):
        fail("FACTOR_RESULTS_INVALID", str(market))
    axes = {
        axis: evaluate_axis_freshness(
            market,
            axis,
            factor_results[axis],
            expected_completed_session_date=expected_completed_session_date,
            decision_at=decision_at,
            policy=policy,
        )
        for axis in REQUIRED_AXES
    }
    all_fresh = all(axes[axis]["freshness_status"] == FRESH for axis in REQUIRED_AXES)
    return {
        "market": market,
        "axes": axes,
        "all_fresh": all_fresh,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Regime semantic freshness")
    parser.add_argument("--market", required=True, choices=["US", "KR"])
    parser.add_argument("--regime-output", type=Path, required=True)
    parser.add_argument("--expected-completed-session-date")
    parser.add_argument("--decision-at")
    args = parser.parse_args(argv)
    envelope = _read_json(args.regime_output)
    if not isinstance(envelope, dict) or "factor_results" not in envelope:
        fail("REGIME_OUTPUT_INVALID", "factor_results")
    result = evaluate_market_freshness(
        args.market,
        envelope["factor_results"],
        expected_completed_session_date=args.expected_completed_session_date,
        decision_at=args.decision_at,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RegimeSemanticFreshnessError as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
