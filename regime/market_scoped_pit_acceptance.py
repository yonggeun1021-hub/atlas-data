#!/usr/bin/env python3
"""Market-scoped runtime PIT acceptance — G8, evidence NOT ACCEPTED.

CIO decision identity ``CIO-P1-COM-05-NORMALIZATION-FRESHNESS-PIT-FINAL-V1-
2026-09-12`` (see ``docs/p1_com_05_cio_final_verdict_20260912.md`` and
``config/market_scoped_pit_acceptance_contract_v1.json``) ratified the
*acceptance rule* below, independently per market (US/KR/CRYPTO). It ratified
no evidence: every market's PIT acceptance status starts, and as of this
module's committed snapshot remains, ``NOT_ACCEPTED``.

This is a **separate, additional** contract. It does not replace, weaken, or
bypass the existing three-market ``regime_replay_harness/v1``
(``config/regime_replay_harness_contract.json``,
``regime/replay_harness.py``), which stays exactly as-is and is never
imported or altered here.

Six conditions gate ``PIT_ACCEPTED`` for a market, exactly as ratified:

1. Real historical evidence only. A caller-supplied sequence receives zero
   credit unless it was built by the real
   ``regime.us_historical_replay_population`` /
   ``regime.kr_historical_replay_population`` population modules — checked by
   requiring an exact ``schema_version``/``mode``/per-record ``evidence_class``
   match against those modules' own constants, never by trusting a caller's
   claim.
2. Required 5-of-5 axes per evaluated date; an incomplete date is excluded,
   never substituted or carried forward.
3. No-lookahead/PIT validation pass — this module does not re-derive the
   lookahead arithmetic itself (that is already independently enforced inside
   the population modules before a record is ever emitted); it requires the
   record's own ``no_lookahead_attestation`` to be structurally present,
   which only a real population-module record carries.
4. Deterministic rerun byte-identical.
5. Ratified classification and hysteresis come exclusively from an
   unmodified call to ``regime.decision_authority.replay_common_v1`` — no
   new threshold, weight, or hysteresis rule is authored here.
6. Across the full accepted real sequence (never a caller-selected
   sub-range), RISK_ON, NEUTRAL, RISK_OFF, and STRESS must each occur at
   least once among the confirmed regimes ``replay_common_v1`` actually
   produced. No episode date is picked in advance, and no output is
   cherry-picked after the fact.

CRYPTO is always, structurally, ``NOT_ACCEPTED`` here: its signed-axis
normalization stays unratified per this same CIO decision, so this module
never attempts to build or evaluate a Crypto sequence. Each market's status is
computed and reported independently; Crypto's incompleteness never blocks or
changes US or KR's status, and US/KR reaching a future ``PIT_ACCEPTED`` would
never change Crypto's.

``runtime_decision_available`` is never touched or referenced as available by
this module. Reaching ``PIT_ACCEPTED`` for a market is necessary evidence,
never itself an authority: strategy, Stage, Buy, Action, Order, capital,
Production, and trading authority all stay false everywhere in this module,
unconditionally.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import decision_authority as AUTHORITY  # noqa: E402

CONTRACT_PATH = ROOT / "config" / "market_scoped_pit_acceptance_contract_v1.json"
CONTRACT_VERSION = "market_scoped_pit_acceptance_contract/v1"

MARKETS = ("US", "KR", "CRYPTO")
REQUIRED_AXES = ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP")
REQUIRED_REGIMES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS")

US_SCHEMA_VERSION = "regime_us_historical_replay_population/v1"
KR_SCHEMA_VERSION = "regime_kr_historical_replay_population/v1"
POPULATION_MODE = "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL"
POPULATION_EVIDENCE_CLASS = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"
POPULATION_SCHEMA_VERSION = {"US": US_SCHEMA_VERSION, "KR": KR_SCHEMA_VERSION}

STATUS_NOT_ACCEPTED = "NOT_ACCEPTED"
STATUS_PIT_ACCEPTED = "PIT_ACCEPTED"

REASON_PREFIX = "PIT_ACCEPTANCE_CONDITION_FAILED:"
REASON_NO_BUNDLE = "NO_EVIDENCE_BUNDLE_SUPPLIED"
REASON_CRYPTO = "CRYPTO_NORMALIZATION_UNRATIFIED"


class MarketScopedPitAcceptanceError(ValueError):
    """Fail-closed market-scoped PIT acceptance violation."""


def fail(code: str, detail: str) -> None:
    raise MarketScopedPitAcceptanceError(f"{code}:{detail}")


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_READ_FAILED", f"{path}:{exc}")


def canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = _read_json(path)
    if not isinstance(contract, dict):
        fail("CONTRACT_INVALID", "object required")
    if (
        contract.get("contract_version") != CONTRACT_VERSION
        or contract.get("policy_status") != "RATIFIED"
        or contract.get("markets") != list(MARKETS)
        or contract.get("episode_selection_forbidden") is not True
        or contract.get("cherry_pick_forbidden") is not True
    ):
        fail("CONTRACT_INVALID", "pinned fields")
    return contract


def authority() -> dict:
    return {
        "acceptance_rule_ratified": True,
        "evidence_accepted": False,
        "runtime_decision_available": False,
        "runtime_classification_authorized": False,
        "runtime_binding_authorized": False,
        "regime_result_ratification_authorized": False,
        "strategy_eligibility_authorized": False,
        "stage_authorized": False,
        "buy_authorized": False,
        "action_authorized": False,
        "order_authorized": False,
        "capital_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }


def _condition_failed(condition_id: int) -> str:
    return f"{REASON_PREFIX}{condition_id}"


def _real_evidence_bundle(market: str, bundle: object) -> list:
    """Condition 1: only a byte-matching real population bundle is usable.

    Returns the bundle's ``records`` list, or fails the whole evaluation
    closed to an empty list (never partial trust) if the bundle's own
    provenance markers do not match the real population module exactly.
    """
    if not isinstance(bundle, dict):
        return []
    if (
        bundle.get("schema_version") != POPULATION_SCHEMA_VERSION.get(market)
        or bundle.get("mode") != POPULATION_MODE
        or bundle.get("wbs") != "P1-COM-05"
    ):
        return []
    records = bundle.get("records")
    if not isinstance(records, list) or not records:
        return []
    for record in records:
        if (
            not isinstance(record, dict)
            or record.get("evidence_class") != POPULATION_EVIDENCE_CLASS
        ):
            return []
    return records


def _axis_directions(record: dict) -> Optional[dict]:
    """Extract {axis: direction} from a real population record, or None.

    ``candidate_normalized_result.axes`` is the exact, already-classified
    output of the unmodified ``regime.paper_regime_reference`` per-axis
    helpers (see the population modules' own parity tests), shaped as a list
    of ``{"axis": ..., "direction": ...}`` rows. An axis absent from that list
    (structurally always true for US BREADTH/LEADERSHIP, which those
    functions never compute) is simply not represented here — condition 2
    then excludes the date rather than inventing a value for it.
    """
    if record.get("status") != "OBSERVED":
        return None
    if "no_lookahead_attestation" not in record or not record["no_lookahead_attestation"]:
        # Condition 3: only a real population record carries this field; its
        # own arithmetic is validated by the population module before this
        # record is ever emitted, not re-derived here.
        return None
    normalized = record.get("candidate_normalized_result")
    if not isinstance(normalized, dict):
        return None
    rows = normalized.get("axes")
    if not isinstance(rows, list):
        return None
    directions = {}
    for row in rows:
        if not isinstance(row, dict) or "axis" not in row or "direction" not in row:
            continue
        if row["axis"] in REQUIRED_AXES:
            directions[row["axis"]] = row["direction"]
    return directions


def _as_of_date(record: dict) -> Optional[str]:
    return record.get("effective_session_date") or record.get("effective_trading_date")


def _build_sequence(market: str, records: list) -> Optional[dict]:
    """Condition 2: keep only dates with real, complete 5-of-5 axes."""
    steps = []
    for record in records:
        directions = _axis_directions(record)
        as_of_date = _as_of_date(record)
        if directions is None or as_of_date is None:
            continue
        if set(directions) != set(REQUIRED_AXES):
            continue  # incomplete date excluded, never substituted
        steps.append((as_of_date, directions))
    if not steps:
        return None
    steps.sort(key=lambda item: item[0])
    return {
        "schema_version": 1,
        "market": market,
        "case_id": f"pit-acceptance-{market.lower()}",
        "steps": [
            {
                "packet_id": f"pit-{market.lower()}-{as_of_date}",
                "as_of_date": as_of_date,
                "axes": {
                    axis: {"status": "DEFINED", "direction": directions[axis]}
                    for axis in REQUIRED_AXES
                },
            }
            for as_of_date, directions in steps
        ],
    }


def evaluate_market_pit_acceptance(market: str, bundle: object = None) -> dict:
    """Evaluate one market's PIT acceptance from a caller-supplied bundle.

    ``bundle`` must be the real, unmodified output of
    ``regime.us_historical_replay_population.build_population`` (for
    ``market == "US"``) or
    ``regime.kr_historical_replay_population.build_population`` (for
    ``market == "KR"``). Passing ``None`` (no bundle available) or anything
    that does not byte-match that provenance yields ``NOT_ACCEPTED`` with
    ``NO_EVIDENCE_BUNDLE_SUPPLIED`` -- there is no partial credit.
    """
    if market not in MARKETS:
        fail("MARKET_INVALID", str(market))
    contract = load_contract()

    if market == "CRYPTO":
        return {
            "market": market,
            "status": STATUS_NOT_ACCEPTED,
            "reasons": [REASON_CRYPTO],
            "evaluated_date_count": 0,
            "regimes_observed": [],
            "missing_regimes": list(REQUIRED_REGIMES),
            "replay_report_sha256": None,
            "contract_version": contract["contract_version"],
            "authority": authority(),
        }

    records = _real_evidence_bundle(market, bundle)
    if not records:
        return {
            "market": market,
            "status": STATUS_NOT_ACCEPTED,
            "reasons": [REASON_NO_BUNDLE],
            "evaluated_date_count": 0,
            "regimes_observed": [],
            "missing_regimes": list(REQUIRED_REGIMES),
            "replay_report_sha256": None,
            "contract_version": contract["contract_version"],
            "authority": authority(),
        }

    sequence = _build_sequence(market, records)
    if sequence is None:
        return {
            "market": market,
            "status": STATUS_NOT_ACCEPTED,
            "reasons": [_condition_failed(2)],
            "evaluated_date_count": 0,
            "regimes_observed": [],
            "missing_regimes": list(REQUIRED_REGIMES),
            "replay_report_sha256": None,
            "contract_version": contract["contract_version"],
            "authority": authority(),
        }

    # Condition 5 (exact reuse) + condition 4 (deterministic rerun): call the
    # unmodified common-v1 replay twice and require byte-identical output.
    try:
        first = AUTHORITY.replay_common_v1(sequence)
        rerun = AUTHORITY.replay_common_v1(copy.deepcopy(sequence))
    except AUTHORITY.DecisionAuthorityError:
        return {
            "market": market,
            "status": STATUS_NOT_ACCEPTED,
            "reasons": [_condition_failed(3)],
            "evaluated_date_count": 0,
            "regimes_observed": [],
            "missing_regimes": list(REQUIRED_REGIMES),
            "replay_report_sha256": None,
            "contract_version": contract["contract_version"],
            "authority": authority(),
        }
    if canonical_json(first) != canonical_json(rerun):
        return {
            "market": market,
            "status": STATUS_NOT_ACCEPTED,
            "reasons": [_condition_failed(4)],
            "evaluated_date_count": len(sequence["steps"]),
            "regimes_observed": [],
            "missing_regimes": list(REQUIRED_REGIMES),
            "replay_report_sha256": None,
            "contract_version": contract["contract_version"],
            "authority": authority(),
        }

    observed = sorted({row["confirmed_regime"] for row in first["steps"]} & set(REQUIRED_REGIMES))
    missing = sorted(set(REQUIRED_REGIMES) - set(observed))
    status = STATUS_PIT_ACCEPTED if not missing else STATUS_NOT_ACCEPTED
    reasons = [] if not missing else [_condition_failed(6)]

    return {
        "market": market,
        "status": status,
        "reasons": reasons,
        "evaluated_date_count": len(sequence["steps"]),
        "regimes_observed": observed,
        "missing_regimes": missing,
        "replay_report_sha256": payload_sha256(first),
        "contract_version": contract["contract_version"],
        "authority": authority(),
    }


def build_status(bundles: Optional[dict] = None) -> dict:
    """Independent, per-market PIT acceptance status for all three markets.

    ``bundles`` maps a market name to its real population bundle (or is
    omitted/partial -- a missing market simply evaluates with no bundle,
    exactly as if none exists yet). Markets never affect each other's
    status.
    """
    bundles = {} if bundles is None else bundles
    contract = load_contract()
    markets = [
        evaluate_market_pit_acceptance(market, bundles.get(market))
        for market in MARKETS
    ]
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "contract_path": "config/market_scoped_pit_acceptance_contract_v1.json",
        "regime_replay_harness_v1_unaffected": True,
        "markets": markets,
        "authority": authority(),
    }


NORMALIZATION_IDENTITY_PATH = ROOT / "config" / "paper_runtime_normalization_v1.json"
FRESHNESS_IDENTITY_PATH = ROOT / "config" / "regime_semantic_freshness_policy_v1.json"

READINESS_POLICY_READY_PIT_PENDING = "POLICY_READY_PIT_EVIDENCE_PENDING"
READINESS_NORMALIZATION_UNRATIFIED = "SIGNED_NORMALIZATION_POLICY_UNRATIFIED"
READINESS_PIT_ACCEPTED_RUNTIME_STILL_CLOSED = "PIT_ACCEPTED_RUNTIME_DECISION_STILL_CLOSED"


def normalization_and_freshness_ratified(market: str) -> bool:
    """True only for a market this session's CIO decision actually ratified.

    Reads the two identity contracts fresh every call rather than caching a
    module-level constant, so a future edit to either file is reflected
    immediately and fails closed (returns False) on any structural mismatch
    instead of raising past this boundary.
    """
    if market not in ("US", "KR"):
        return False
    try:
        norm = _read_json(NORMALIZATION_IDENTITY_PATH)
        fresh = _read_json(FRESHNESS_IDENTITY_PATH)
    except MarketScopedPitAcceptanceError:
        return False
    if not isinstance(norm, dict) or not isinstance(fresh, dict):
        return False
    return (
        norm.get("policy_status") == "RATIFIED"
        and market in norm.get("ratified_markets", [])
        and fresh.get("policy_status") == "RATIFIED"
        and market in fresh.get("markets", {})
    )


def readiness_overlay_status(market: str, pit_status: str) -> dict:
    """One market's additive readiness label -- never a runtime decision.

    This never asserts ``runtime_decision_available``; it is pinned ``False``
    unconditionally, exactly matching
    ``regime.runtime_regime_readiness``'s own structural invariant.
    """
    if market not in MARKETS:
        fail("MARKET_INVALID", str(market))
    if not normalization_and_freshness_ratified(market):
        label = READINESS_NORMALIZATION_UNRATIFIED
    elif pit_status == STATUS_PIT_ACCEPTED:
        label = READINESS_PIT_ACCEPTED_RUNTIME_STILL_CLOSED
    else:
        label = READINESS_POLICY_READY_PIT_PENDING
    return {
        "market": market,
        "policy_readiness_status": label,
        "runtime_decision_available": False,
    }


def build_readiness_overlay(status: Optional[dict] = None) -> dict:
    """Compose PIT acceptance status into a readiness-shaped overlay.

    This is genuinely additive: it consumes ``build_status()`` (this module)
    and, separately, whatever ``regime.runtime_regime_readiness.build_readiness``
    already reports, without importing, modifying, or re-deriving that
    module's own closed-schema packet or its
    ``p1_regime_decision_unavailable_reasons``/P6-06 contract in any way.
    """
    status = build_status() if status is None else status
    rows = [
        readiness_overlay_status(row["market"], row["status"])
        for row in status["markets"]
    ]
    return {
        "schema_version": 1,
        "contract_version": status["contract_version"],
        "source": "regime.market_scoped_pit_acceptance",
        "note": (
            "Additive overlay only. Composes with, and does not modify, "
            "regime.runtime_regime_readiness's existing build_readiness()/"
            "validate_readiness() contract, schema, or "
            "p1_regime_decision_unavailable_reasons."
        ),
        "markets": rows,
        "runtime_decision_available": False,
        "authority": authority(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Market-scoped PIT acceptance")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--us-bundle", type=Path)
    status.add_argument("--kr-bundle", type=Path)
    status.add_argument("--out", type=Path)

    overlay = sub.add_parser("readiness-overlay")
    overlay.add_argument("--us-bundle", type=Path)
    overlay.add_argument("--kr-bundle", type=Path)
    overlay.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    if args.command in ("status", "readiness-overlay"):
        bundles = {}
        if args.us_bundle:
            bundles["US"] = _read_json(args.us_bundle)
        if args.kr_bundle:
            bundles["KR"] = _read_json(args.kr_bundle)
        status_result = build_status(bundles)
        result = (
            status_result
            if args.command == "status"
            else build_readiness_overlay(status_result)
        )
        payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(payload + "\n", encoding="utf-8")
            print(str(args.out))
        else:
            print(payload)
        return 0
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MarketScopedPitAcceptanceError, AUTHORITY.DecisionAuthorityError) as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
