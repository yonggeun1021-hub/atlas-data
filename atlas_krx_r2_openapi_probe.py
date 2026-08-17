#!/usr/bin/env python3
"""
Atlas R2-ENG-001 — KRX OPEN API direct capability probe.

Scope
-----
- Direct KRX KOSPI index endpoint capability proof.
- AUTH_KEY is accepted from the environment/argument and sent only as a request header.
- Response bytes and market rows remain in memory and are never written by this module.
- Console output is limited to non-reconstructive diagnostics.
- This does not authorize Production, redistribution, Regime scoring, thresholds,
  weights, evaluator wiring, or trading.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen

TOOL_VERSION = "0.1"
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd"
MIN_OFFICIAL_DATE = "20100104"
REQUIRED_FIELDS = frozenset({
    "BAS_DD",
    "IDX_NM",
    "CLSPRC_IDX",
})
DATE_RE = re.compile(r"^[0-9]{8}$")


class Stop(Exception):
    pass


def validate_date(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise Stop("basDd must be YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise Stop("basDd is not a valid calendar date") from exc
    return value


def build_request(auth_key, bas_dd):
    key = (auth_key or "").strip()
    if not key:
        raise Stop("KRX_API_KEY is missing")

    day = validate_date(bas_dd)
    url = BASE_URL + "?" + urlencode({"basDd": day})
    request = Request(
        url,
        headers={
            "AUTH_KEY": key,
            "Accept": "application/json",
            "User-Agent": "Atlas-R2-ENG-001/0.1",
        },
        method="GET",
    )
    return request


def _http_fetch(request, opener=urlopen, timeout=30):
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            body = response.read()
    except HTTPError as exc:
        raise Stop("KRX HTTP error status=%s" % exc.code) from exc
    except URLError as exc:
        raise Stop("KRX network error") from exc

    if status != 200:
        raise Stop("KRX HTTP error status=%s" % status)

    return body


def _decode_payload(body):
    try:
        text = body.decode("utf-8")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise Stop("KRX response is not UTF-8 JSON") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Stop("KRX response is malformed JSON") from exc

    if not isinstance(payload, dict):
        raise Stop("KRX response root must be an object")

    return payload


def validate_payload(payload, bas_dd):
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        raise Stop("KRX response missing OutBlock_1 list")
    if not rows:
        raise Stop("KRX response returned zero rows")

    for row in rows:
        if not isinstance(row, dict):
            raise Stop("KRX row is not an object")

        missing = sorted(REQUIRED_FIELDS.difference(row))
        if missing:
            raise Stop("KRX response missing required fields")

        if str(row["BAS_DD"]) != bas_dd:
            raise Stop("KRX response BAS_DD mismatch")

        if not str(row["IDX_NM"]).strip():
            raise Stop("KRX response IDX_NM is empty")

        if not str(row["CLSPRC_IDX"]).strip():
            raise Stop("KRX response CLSPRC_IDX is empty")

    return len(rows)


def probe_date(auth_key, bas_dd, opener=urlopen):
    request = build_request(auth_key, bas_dd)
    body = _http_fetch(request, opener=opener)
    payload = _decode_payload(body)
    row_count = validate_payload(payload, bas_dd)

    # Deliberately do not return payload/body/market values.
    return {
        "status": "PASS",
        "date": bas_dd,
        "row_count": row_count,
        "schema": "PASS",
        "source": "KRX_OPEN_API_KOSPI",
        "source_capability_proof_only": True,
        "raw_persistence": 0,
        "production_authorized": False,
        "redistribution_authorized": False,
        "regime_score_authorized": False,
        "trading_authorized": False,
    }


def format_summary(result):
    keys = (
        "status",
        "date",
        "row_count",
        "schema",
        "source",
        "source_capability_proof_only",
        "raw_persistence",
        "production_authorized",
        "redistribution_authorized",
        "regime_score_authorized",
        "trading_authorized",
    )
    return " ".join("%s=%s" % (key, result[key]) for key in keys)


def inspect_request_contract(auth_key, bas_dd):
    request = build_request(auth_key, bas_dd)
    parsed = urlparse(request.full_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    headers = {k.lower(): v for k, v in request.header_items()}

    return {
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "path": parsed.path,
        "query_keys": sorted(query),
        "auth_header_present": "auth_key" in headers,
        "auth_in_url": (auth_key or "") in request.full_url,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        action="append",
        dest="dates",
        required=True,
        help="KRX basis date YYYYMMDD; may be supplied more than once",
    )
    parser.add_argument(
        "--auth-env",
        default="KRX_API_KEY",
        help="environment variable containing the KRX AUTH_KEY",
    )
    args = parser.parse_args(argv)

    import os
    key = os.getenv(args.auth_env, "")

    try:
        for day in args.dates:
            result = probe_date(key, day)
            print(format_summary(result))
        print("R2_KRX_LIVE_PROOF=PASS")
        return 0
    except Stop as exc:
        print("STOP: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
