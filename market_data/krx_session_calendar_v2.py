#!/usr/bin/env python3
"""Validate either retained KIS holiday or exact KRX observation calendars.

The established completed-bar validator remains byte-for-byte unchanged.
This narrow adapter admits one additional OPEN_REGULAR source identity and
then delegates all date, time, market-rule, and session-bound checks to that
validator.  It cannot derive CLOSED dates from missing observations.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from market_data import krx_session_bars as BASE


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/krx_session_calendar_sources_v2.json"
KIS_PROVIDER = "KIS_OPEN_API_DOMESTIC_HOLIDAY_CTCA0903R"
KRX_PROVIDER = "KRX_INFORMATION_DATA_SYSTEM_EXACT_DATE_OHLCV"


class KrxSessionCalendarV2Error(ValueError):
    """Fail-closed source-profile validation error."""


# Existing consumers catch this public name from the selected validator.
KrxMarketDataError = KrxSessionCalendarV2Error


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KrxSessionCalendarV2Error("CONTRACT_READ_FAILED") from exc
    if not isinstance(value, dict):
        raise KrxSessionCalendarV2Error("CONTRACT_NOT_OBJECT")
    if (
        value.get("schema_version") != "krx_session_calendar_sources/2"
        or value.get("market") != "KOREA"
        or value.get("venue_scope") != "KRX_ONLY"
        or value.get("timezone") != "Asia/Seoul"
        or value.get("allowed_open_source_identities") != [KIS_PROVIDER, KRX_PROVIDER]
        or value.get("market_rule_identity") != "KRX_EQUITY_MARKET_OPERATION_RULES"
    ):
        raise KrxSessionCalendarV2Error("CONTRACT_IDENTITY_INVALID")
    base_validator = value.get("base_validator", {})
    base_contract = value.get("base_contract", {})
    decision_evidence = value.get("decision_evidence", {})
    if (
        base_validator.get("path") != "market_data/krx_session_bars.py"
        or base_contract.get("path") != "config/krx_market_data_contract.json"
        or _sha(ROOT / base_validator["path"]) != base_validator.get("sha256")
        or _sha(ROOT / base_contract["path"]) != base_contract.get("sha256")
    ):
        raise KrxSessionCalendarV2Error("BASE_PIN_MISMATCH")
    if value.get("regular_session") != BASE.load_contract().get("regular_session"):
        raise KrxSessionCalendarV2Error("REGULAR_SESSION_MISMATCH")
    if (
        decision_evidence.get("path")
        != "evidence/authority/krx_direct_exact_date_calendar_source_adoption_20260908.json"
        or _sha(ROOT / decision_evidence["path"]) != decision_evidence.get("sha256")
    ):
        raise KrxSessionCalendarV2Error("DECISION_EVIDENCE_PIN_MISMATCH")
    profile = value.get("krx_observation_profile")
    if profile != {
        "adapter_path": "market_data/krx_post_close_session_calendar.py",
        "profile": "krx_post_close_open_session_evidence/1",
        "source_label": "KRX 정보데이터시스템 (pykrx)",
        "source_tier": "Official",
        "required_symbols": ["000660", "005930"],
        "exact_date_positive_ohlcv_required": True,
        "price_or_flow_finality_promoted": False,
        "closed_session_derivation_authorized": False,
    }:
        raise KrxSessionCalendarV2Error("KRX_PROFILE_INVALID")
    if value.get("authority") != {
        "market_calendar_observation_only": True,
        "candidate_authorized": False,
        "entry_authorized": False,
        "order_authorized": False,
        "trading_authorized": False,
        "real_capital_authorized": False,
    }:
        raise KrxSessionCalendarV2Error("AUTHORITY_INVALID")
    return copy.deepcopy(value)


def validate_calendar(value: dict, decision_at, contract: dict | None = None) -> dict:
    checked_contract = load_contract() if contract is None else contract
    if checked_contract != load_contract():
        raise KrxSessionCalendarV2Error("CONTRACT_MISMATCH")
    if not isinstance(value, dict):
        raise KrxSessionCalendarV2Error("CALENDAR_NOT_OBJECT")
    provider = value.get("provider_id")
    if provider not in checked_contract["allowed_open_source_identities"]:
        raise KrxSessionCalendarV2Error("CALENDAR_PROVIDER_INVALID")
    if value.get("market_rule_source") != checked_contract["market_rule_identity"]:
        raise KrxSessionCalendarV2Error("CALENDAR_RULE_SOURCE_INVALID")
    if provider == KRX_PROVIDER and value.get("status") != "OPEN_REGULAR":
        raise KrxSessionCalendarV2Error("KRX_OBSERVATION_CLOSED_DERIVATION_FORBIDDEN")

    delegated = copy.deepcopy(value)
    delegated["provider_id"] = KIS_PROVIDER
    try:
        result = BASE.validate_calendar(delegated, decision_at, BASE.load_contract())
    except BASE.KrxMarketDataError as exc:
        raise KrxSessionCalendarV2Error(str(exc)) from exc
    result["provider_id"] = provider
    return result
