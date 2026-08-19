#!/usr/bin/env python3
"""KRX investor-flow venue coverage and finality contract (P1-KR-04).

The existing KRX collector returns per-security investor flows from the KRX
Information Data System through pykrx.  Those values must not silently become
"all Korea market" flows after NXT opened a separate trading venue.

This module has two production responsibilities:

* provide immutable coverage metadata embedded by ``collectors/krx.py``; and
* qualify a saved KRX snapshot offline without granting score or trading
  authority.

It makes no network request and never writes inside the tracked repository.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "korea_investor_flow_contract.json"
CODE = re.compile(r"^[0-9]{6}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_CONTRACT = {
    "schema_version": 1,
    "contract_version": "korea_investor_flow_coverage/v1",
    "source": {
        "authority": "Korea Exchange",
        "system": "KRX Information Data System",
        "adapter": "pykrx",
        "payload_source_label": "KRX 정보데이터시스템 (pykrx)",
        "source_tier": "Official",
    },
    "coverage": {
        "observation_grain": "tracked_security",
        "market_venue_scope": "KRX_ONLY",
        "security_market_segment_status": "not_recorded_in_payload",
        "nxt_included": False,
        "whole_korea_market_claim_authorized": False,
        "covered_sources": ["net_value", "net_volume"],
        "basic_investor_categories": [
            "기관합계",
            "외국인합계",
            "개인",
            "기타법인",
        ],
    },
    "finality": {
        "same_day_confirmation": "next_day",
        "confirmed_reason": "prior_session",
        "same_day_rows_decision_eligible": False,
        "source_release_time_status": "unverified",
        "available_at_policy": "primary_source_release_evidence_required",
        "collector_observed_at_is_available_at": False,
    },
    "missing_policy": {
        "source_row_absent": "SOURCE_ROW_MISSING",
        "investor_category_absent": "INVESTOR_CATEGORY_MISSING",
        "venue_not_covered": "VENUE_NOT_INCLUDED",
        "observed_zero": "OBSERVED_ZERO",
        "stock_collection_failed": "SOURCE_FAILED",
    },
    "authority": {
        "decision_eligible": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    },
}


class InvestorFlowContractError(RuntimeError):
    """Fail-closed KRX investor-flow coverage error."""


def fail(code: str, detail: str) -> None:
    raise InvestorFlowContractError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
    except InvestorFlowContractError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", str(exc))
    if contract != EXPECTED_CONTRACT:
        fail("CONTRACT_INVALID", "schema or approved boundary changed")
    return contract


def coverage_metadata(contract: dict | None = None) -> dict:
    """Return the exact boundary embedded in every new KRX payload."""
    contract = load_contract() if contract is None else contract
    if contract != EXPECTED_CONTRACT:
        fail("CONTRACT_INVALID", "unvalidated contract")
    return {
        "contract_version": contract["contract_version"],
        "observation_grain": contract["coverage"]["observation_grain"],
        "market_venue_scope": contract["coverage"]["market_venue_scope"],
        "security_market_segment_status": contract["coverage"][
            "security_market_segment_status"
        ],
        "nxt_included": contract["coverage"]["nxt_included"],
        "whole_korea_market_claim_authorized": contract["coverage"][
            "whole_korea_market_claim_authorized"
        ],
        "covered_sources": list(contract["coverage"]["covered_sources"]),
        "basic_investor_categories": list(
            contract["coverage"]["basic_investor_categories"]
        ),
        "same_day_confirmation": contract["finality"][
            "same_day_confirmation"
        ],
        "source_release_time_status": contract["finality"][
            "source_release_time_status"
        ],
        "available_at": None,
        "missing_states": dict(contract["missing_policy"]),
        **contract["authority"],
    }


def parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code, "date must be YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code, str(value))
    if parsed.isoformat() != value:
        fail(code, value)
    return parsed


def require_number(value: object, location: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail("VALUE_TYPE_INVALID", location)
    if not math.isfinite(value):
        fail("NUMBER_INVALID", location)
    return value


def _undefined_stock(stock: dict) -> dict:
    error = stock.get("error")
    if not isinstance(error, str) or not error.strip():
        fail("FAILED_STOCK_INVALID", "missing error")
    return {
        "status": "UNDEFINED",
        "observation_date": None,
        "flows": {"net_value": None, "net_volume": None},
        "missing": {
            "source_rows": [],
            "investor_categories_by_source": {},
            "stock_collection": "SOURCE_FAILED",
        },
        "warnings": [
            "SOURCE_FAILED",
            "VENUE_NOT_INCLUDED",
            "SOURCE_RELEASE_TIME_UNVERIFIED",
        ],
        "decision_eligible": False,
    }


def qualify_stock(
    code: str,
    stock: dict,
    collected_for: dt.date,
    contract: dict,
) -> dict:
    if not CODE.fullmatch(code) or not isinstance(stock, dict):
        fail("STOCK_SCHEMA_INVALID", code)
    if stock.get("status") == "FAILED":
        return _undefined_stock(stock)
    if stock.get("status") != "ok":
        fail("STOCK_STATUS_INVALID", code)

    observation_date = parse_date(
        stock.get("latest_trading_day"), "CONFIRMED_DAY_INVALID"
    )
    if observation_date >= collected_for:
        fail("CONFIRMED_DAY_INVALID", code)
    daily = stock.get("daily")
    if not isinstance(daily, dict):
        fail("DAILY_SCHEMA_INVALID", code)
    row = daily.get(observation_date.isoformat())
    if not isinstance(row, dict):
        fail("CONFIRMED_ROW_MISSING", code)
    if (
        row.get("confirmed") is not True
        or row.get("confirm_reason") != contract["finality"]["confirmed_reason"]
    ):
        fail("CONFIRMED_ROW_INVALID", code)

    declared_absent = row.get("investor_rows_absent")
    if not isinstance(declared_absent, list) or not all(
        isinstance(item, str) for item in declared_absent
    ):
        fail("MISSING_METADATA_INVALID", code)

    categories = contract["coverage"]["basic_investor_categories"]
    category_set = set(categories)
    flows = {}
    source_rows = []
    missing_categories = {}
    for source in contract["coverage"]["covered_sources"]:
        value = row.get(source)
        if value is None:
            if source not in declared_absent:
                fail("MISSING_METADATA_INCONSISTENT", f"{code}:{source}")
            flows[source] = None
            source_rows.append(source)
            continue
        if source in declared_absent or not isinstance(value, dict):
            fail("MISSING_METADATA_INCONSISTENT", f"{code}:{source}")
        unknown = sorted(set(value) - category_set)
        if unknown:
            fail("INVESTOR_CATEGORY_UNKNOWN", f"{code}:{source}:{unknown}")
        missing = [item for item in categories if item not in value]
        if missing:
            missing_categories[source] = missing
        flows[source] = {
            item: require_number(value[item], f"{code}:{source}:{item}")
            for item in categories
            if item in value
        }

    warnings = ["VENUE_NOT_INCLUDED", "SOURCE_RELEASE_TIME_UNVERIFIED"]
    if source_rows:
        warnings.append("SOURCE_ROW_MISSING")
    if missing_categories:
        warnings.append("INVESTOR_CATEGORY_MISSING")
    status = (
        "OBSERVED_KRX_ONLY"
        if not source_rows and not missing_categories
        else "UNDEFINED_KRX_ONLY"
    )
    return {
        "status": status,
        "observation_date": observation_date.isoformat(),
        "flows": flows,
        "missing": {
            "source_rows": source_rows,
            "investor_categories_by_source": missing_categories,
            "stock_collection": None,
        },
        "warnings": warnings,
        "decision_eligible": False,
    }


def build_report(
    payload: dict,
    source_sha256: str,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    if contract != EXPECTED_CONTRACT:
        fail("CONTRACT_INVALID", "unvalidated contract")
    if not isinstance(payload, dict) or not SHA256.fullmatch(source_sha256):
        fail("SNAPSHOT_INVALID", "payload or sha256")
    if payload.get("source") != contract["source"]["payload_source_label"]:
        fail("SOURCE_IDENTITY_INVALID", str(payload.get("source")))
    if payload.get("source_tier") != contract["source"]["source_tier"]:
        fail("SOURCE_IDENTITY_INVALID", str(payload.get("source_tier")))
    if payload.get("same_day_confirmation") != "next_day":
        fail("FINALITY_POLICY_INVALID", str(payload.get("same_day_confirmation")))
    if payload.get("investor_flow_coverage") != coverage_metadata(contract):
        fail("COVERAGE_METADATA_INVALID", "payload boundary missing or changed")

    collected_for = parse_date(
        payload.get("collected_for_kst_date"), "COLLECTION_DATE_INVALID"
    )
    stocks = payload.get("stocks")
    if not isinstance(stocks, dict) or not stocks:
        fail("STOCKS_INVALID", "empty or missing")

    qualified = {
        code: qualify_stock(code, stock, collected_for, contract)
        for code, stock in sorted(stocks.items())
    }
    observed = sum(
        item["status"] == "OBSERVED_KRX_ONLY" for item in qualified.values()
    )
    undefined = len(qualified) - observed
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "coverage_status": "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
        "collected_for_kst_date": collected_for.isoformat(),
        "source_snapshot_sha256": source_sha256,
        "market_venue_scope": "KRX_ONLY",
        "security_market_segment_status": "not_recorded_in_payload",
        "nxt_included": False,
        "whole_korea_market_claim_authorized": False,
        "source_release_time_status": "unverified",
        "available_at": None,
        "missing_policy": dict(contract["missing_policy"]),
        "summary": {
            "tracked_stocks": len(qualified),
            "observed_krx_only": observed,
            "undefined": undefined,
        },
        "stocks": qualified,
        "warnings": ["VENUE_NOT_INCLUDED", "SOURCE_RELEASE_TIME_UNVERIFIED"],
        **contract["authority"],
    }


def load_snapshot(path: Path) -> tuple[dict, str]:
    try:
        raw = Path(path).read_bytes()
        payload = json.loads(raw, parse_constant=reject_json_constant)
    except InvestorFlowContractError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        fail("SNAPSHOT_INVALID", str(exc))
    return payload, hashlib.sha256(raw).hexdigest()


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def write_report(report: dict, path: Path) -> None:
    path = Path(path)
    if _inside_repo(path):
        fail("TRACKED_OUTPUT_FORBIDDEN", str(path))
    if path.exists():
        fail("OUTPUT_EXISTS", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp.{os.getpid()}"
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qualify a saved KRX investor-flow snapshot offline"
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload, sha256 = load_snapshot(args.snapshot)
    report = build_report(payload, sha256)
    write_report(report, args.out)


if __name__ == "__main__":
    main()
