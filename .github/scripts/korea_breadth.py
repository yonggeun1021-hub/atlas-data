#!/usr/bin/env python3
"""P1-KR-05 official KRX stock PIT universe and breadth capability proof."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
from pathlib import Path
import re
import sys
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "korea_breadth_contract.json"
DATE_RE = re.compile(r"^[0-9]{8}$")
REQUIRED_FIELDS = frozenset(
    {
        "BAS_DD",
        "ISU_CD",
        "ISU_NM",
        "MKT_NM",
        "SECT_TP_NM",
        "TDD_CLSPRC",
        "CMPPREVDD_PRC",
        "FLUC_RT",
        "TDD_OPNPRC",
        "TDD_HGPRC",
        "TDD_LWPRC",
        "ACC_TRDVOL",
        "ACC_TRDVAL",
        "MKTCAP",
        "LIST_SHRS",
    }
)


class BreadthError(Exception):
    pass


def load_contract(path=CONTRACT_PATH):
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema_version") != 1:
        raise BreadthError("CONTRACT_SCHEMA_UNSUPPORTED")
    return payload


def _has_text(value):
    return value is not None and bool(str(value).strip())


def validate_date(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise BreadthError("DATE_FORMAT_INVALID")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise BreadthError("DATE_CALENDAR_INVALID") from exc
    return value


def validate_market(value, contract=None):
    contract = contract or load_contract()
    market = str(value or "").strip().lower()
    if market not in contract["market_endpoints"]:
        raise BreadthError("MARKET_UNSUPPORTED")
    return market


def build_request(auth_key, bas_dd, market, contract=None):
    contract = contract or load_contract()
    key = str(auth_key or "").strip()
    if not key:
        raise BreadthError("KRX_API_KEY_MISSING")
    day = validate_date(bas_dd)
    market = validate_market(market, contract)
    url = contract["market_endpoints"][market] + "?" + urlencode({"basDd": day})
    return Request(
        url,
        headers={
            "AUTH_KEY": key,
            "Accept": "application/json",
            "User-Agent": "Atlas-P1-KR-05/1.0",
        },
        method="GET",
    )


def inspect_request_contract(auth_key, bas_dd, market, contract=None):
    request = build_request(auth_key, bas_dd, market, contract=contract)
    parsed = urlparse(request.full_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    headers = {key.lower(): value for key, value in request.header_items()}
    return {
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "path": parsed.path,
        "query_keys": sorted(query),
        "auth_header_present": "auth_key" in headers,
        "auth_in_url": str(auth_key or "") in request.full_url,
    }


def _http_fetch(request, opener=urlopen, timeout=30):
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            body = response.read()
    except HTTPError as exc:
        raise BreadthError("KRX_HTTP_ERROR_%s" % exc.code) from exc
    except URLError as exc:
        raise BreadthError("KRX_NETWORK_ERROR") from exc
    if status != 200:
        raise BreadthError("KRX_HTTP_ERROR_%s" % status)
    return body


def _decode_payload(body):
    try:
        text = body.decode("utf-8")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise BreadthError("KRX_RESPONSE_NOT_UTF8_JSON") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BreadthError("KRX_RESPONSE_MALFORMED_JSON") from exc
    if not isinstance(payload, dict):
        raise BreadthError("KRX_RESPONSE_ROOT_NOT_OBJECT")
    return payload


def _parse_close(value):
    if not _has_text(value):
        return None
    cleaned = str(value).strip().replace(",", "")
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation as exc:
        raise BreadthError("CLOSE_VALUE_INVALID") from exc
    if not parsed.is_finite() or parsed < 0:
        raise BreadthError("CLOSE_VALUE_INVALID")
    return parsed


def validate_snapshot(payload, bas_dd, market, contract=None):
    contract = contract or load_contract()
    day = validate_date(bas_dd)
    market = validate_market(market, contract)
    rows = payload.get(contract["response_block"])
    if not isinstance(rows, list):
        raise BreadthError("RESPONSE_BLOCK_MISSING")
    if not rows:
        raise BreadthError("RESPONSE_ZERO_ROWS")

    members = {}
    unavailable_close_count = 0
    for row in rows:
        if not isinstance(row, dict):
            raise BreadthError("ROW_NOT_OBJECT")
        if REQUIRED_FIELDS.difference(row):
            raise BreadthError("REQUIRED_FIELDS_MISSING")
        if str(row["BAS_DD"]) != day:
            raise BreadthError("BAS_DD_MISMATCH")
        for field in ("ISU_CD", "ISU_NM", "MKT_NM"):
            if not _has_text(row[field]):
                raise BreadthError("IDENTITY_FIELD_EMPTY")
        identity = str(row["ISU_CD"]).strip()
        if identity in members:
            raise BreadthError("ISU_CD_DUPLICATE")
        close = _parse_close(row["TDD_CLSPRC"])
        if close is None:
            unavailable_close_count += 1
        members[identity] = close

    return {
        "date": day,
        "market": market,
        "members": members,
        "universe_count": len(members),
        "available_close_count": len(members) - unavailable_close_count,
        "unavailable_close_count": unavailable_close_count,
    }


def fetch_snapshot(auth_key, bas_dd, market, opener=urlopen, contract=None):
    contract = contract or load_contract()
    request = build_request(auth_key, bas_dd, market, contract=contract)
    body = _http_fetch(request, opener=opener)
    payload = _decode_payload(body)
    return validate_snapshot(payload, bas_dd, market, contract=contract)


def _ratio(numerator, denominator, places):
    if denominator <= 0:
        raise BreadthError("RATIO_DENOMINATOR_ZERO")
    quantum = Decimal(1).scaleb(-places)
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        quantum, rounding=ROUND_HALF_EVEN
    )
    return format(value, "f")


def build_observation(previous, current, scope, contract=None):
    contract = contract or load_contract()
    if previous["market"] != current["market"]:
        raise BreadthError("MARKET_PAIR_MISMATCH")
    if previous["date"] >= current["date"]:
        raise BreadthError("DATE_PAIR_NOT_ORDERED")

    previous_ids = set(previous["members"])
    current_ids = set(current["members"])
    shared = previous_ids & current_ids
    entered = current_ids - previous_ids
    exited = previous_ids - current_ids
    paired = [
        identity
        for identity in shared
        if previous["members"][identity] is not None
        and current["members"][identity] is not None
    ]
    if len(paired) < contract["minimum_paired_price_count"]:
        raise BreadthError("PAIRED_PRICE_COVERAGE_ZERO")

    advancing = 0
    declining = 0
    unchanged = 0
    for identity in paired:
        before = previous["members"][identity]
        after = current["members"][identity]
        if after > before:
            advancing += 1
        elif after < before:
            declining += 1
        else:
            unchanged += 1

    paired_count = len(paired)
    return {
        "schema_version": 1,
        "status": "PASS",
        "scope": scope,
        "market": current["market"].upper(),
        "previous_date": previous["date"],
        "as_of_date": current["date"],
        "universe": {
            "previous_count": previous["universe_count"],
            "current_count": current["universe_count"],
            "shared_count": len(shared),
            "entered_count": len(entered),
            "exited_count": len(exited),
            "previous_unavailable_close_count": previous[
                "unavailable_close_count"
            ],
            "current_unavailable_close_count": current[
                "unavailable_close_count"
            ],
            "paired_price_unavailable_count": len(shared) - paired_count,
            "semantics": contract["universe_semantics"],
        },
        "participation": {
            "paired_count": paired_count,
            "advancing_count": advancing,
            "declining_count": declining,
            "unchanged_count": unchanged,
            "advance_fraction": _ratio(
                advancing, paired_count, contract["output_decimal_places"]
            ),
            "decline_fraction": _ratio(
                declining, paired_count, contract["output_decimal_places"]
            ),
            "unchanged_fraction": _ratio(
                unchanged, paired_count, contract["output_decimal_places"]
            ),
            "classification": "UNDEFINED",
        },
        "raw_persistence": contract["raw_persistence"],
        "breadth_classification_authorized": contract[
            "breadth_classification_authorized"
        ],
        "threshold_authorized": contract["threshold_authorized"],
        "regime_score_authorized": contract["regime_score_authorized"],
        "production_wiring_authorized": contract[
            "production_wiring_authorized"
        ],
        "trading_action_authorized": contract["trading_action_authorized"],
    }


def probe_pair(
    auth_key,
    previous_date,
    current_date,
    market,
    scope,
    opener=urlopen,
    contract=None,
):
    contract = contract or load_contract()
    previous = fetch_snapshot(
        auth_key, previous_date, market, opener=opener, contract=contract
    )
    current = fetch_snapshot(
        auth_key, current_date, market, opener=opener, contract=contract
    )
    return build_observation(previous, current, scope, contract=contract)


def format_summary(result):
    universe = result["universe"]
    participation = result["participation"]
    fields = {
        "status": result["status"],
        "scope": result["scope"],
        "market": result["market"],
        "previous_date": result["previous_date"],
        "as_of_date": result["as_of_date"],
        "previous_universe_count": universe["previous_count"],
        "current_universe_count": universe["current_count"],
        "shared_count": universe["shared_count"],
        "entered_count": universe["entered_count"],
        "exited_count": universe["exited_count"],
        "paired_count": participation["paired_count"],
        "advancing_count": participation["advancing_count"],
        "declining_count": participation["declining_count"],
        "unchanged_count": participation["unchanged_count"],
        "paired_price_unavailable_count": universe[
            "paired_price_unavailable_count"
        ],
        "raw_persistence": result["raw_persistence"],
        "breadth_classification_authorized": result[
            "breadth_classification_authorized"
        ],
        "regime_score_authorized": result["regime_score_authorized"],
        "production_wiring_authorized": result[
            "production_wiring_authorized"
        ],
        "trading_action_authorized": result["trading_action_authorized"],
    }
    return " ".join("%s=%s" % item for item in fields.items())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", action="append", choices=("kospi", "kosdaq"))
    parser.add_argument("--historical-previous", default="20100104")
    parser.add_argument("--historical-date", default="20100105")
    parser.add_argument("--recent-previous", required=True)
    parser.add_argument("--recent-date", required=True)
    parser.add_argument("--auth-env", default="KRX_API_KEY")
    args = parser.parse_args(argv)

    import os

    key = os.getenv(args.auth_env, "")
    markets = args.market or ["kospi", "kosdaq"]
    pairs = (
        ("historical", args.historical_previous, args.historical_date),
        ("recent", args.recent_previous, args.recent_date),
    )
    try:
        for market in markets:
            for scope, previous_date, current_date in pairs:
                result = probe_pair(
                    key,
                    previous_date,
                    current_date,
                    market,
                    scope,
                )
                print(format_summary(result))
        print("P1_KR05_KOREA_BREADTH_LIVE_PROOF=PASS")
        return 0
    except BreadthError as exc:
        print("STOP: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
