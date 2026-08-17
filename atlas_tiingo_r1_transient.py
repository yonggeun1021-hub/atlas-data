#!/usr/bin/env python3
"""
Atlas — R-1 Tiingo transient source-contract probe (v0.1)

Purpose
-------
Verify that Tiingo EOD can satisfy the US long-price source contract while
respecting the Starter-plan transient-only retention boundary.

TRANSIENT-ONLY contract
-----------------------
- TIINGO_API_KEY is read only from the environment.
- The token is sent in the Authorization header, never in the URL.
- Response bytes and parsed Tiingo Data are held in memory only.
- No Tiingo response body, row, price, metadata value, or reconstructible
  timeseries is written to disk, logs, stdout, or stderr.
- Durable output is limited to non-reconstructive PASS/FAIL and aggregate
  contract diagnostics.
- Present-day adjusted history is NOT qualified as historical point-in-time.

Not authorized here: Regime score/threshold/weight, Production wiring, or
trading behavior.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

BASE = "https://api.tiingo.com"
TIMEOUT = 30
TOOL_VERSION = "0.1-transient"
DEFAULT_TICKERS = ("SPY", "QQQ")
DEFAULT_START = "2008-09-01"
DEFAULT_END = "2008-10-31"

REQUIRED_FIELDS = frozenset({
    "date", "open", "high", "low", "close", "volume",
    "adjOpen", "adjHigh", "adjLow", "adjClose", "adjVolume",
    "divCash", "splitFactor",
})
TICKER_RE = re.compile(r"^[A-Za-z0-9._-]{1,20}$")


class Stop(Exception):
    pass


def api_key():
    key = os.environ.get("TIINGO_API_KEY", "").strip()
    if not key:
        raise Stop("TIINGO_API_KEY 환경변수가 없습니다. 키를 파일·인자·URL에 넣지 마십시오.")
    return key


def validate_date(value, label):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Stop("%s 는 YYYY-MM-DD 형식이어야 합니다." % label)


def validate_inputs(tickers, start_date, end_date):
    start = validate_date(start_date, "start_date")
    end = validate_date(end_date, "end_date")
    if start > end:
        raise Stop("start_date 가 end_date 보다 늦습니다.")
    if not tickers:
        raise Stop("ticker 가 없습니다.")

    clean = []
    for ticker in tickers:
        t = ticker.strip().upper()
        if not TICKER_RE.fullmatch(t):
            raise Stop("허용하지 않는 ticker 형식입니다.")
        clean.append(t)

    return tuple(clean), start, end


def call(path, params, key):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Token %s" % key,
            "Content-Type": "application/json",
            "User-Agent": "Atlas-R1-Tiingo-Transient/%s" % TOOL_VERSION,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return raw, int(resp.status)
    except urllib.error.HTTPError as e:
        raise Stop("Tiingo HTTP %d on %s" % (e.code, path))
    except (urllib.error.URLError, TimeoutError):
        raise Stop("Tiingo network failure on %s" % path)


def load_json(raw, context):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise Stop("%s JSON 응답을 해석할 수 없습니다." % context)


def require_http_200(status, context):
    if status != 200:
        raise Stop("%s HTTP status=%s" % (context, status))


def probe_ticker(ticker, start_date, end_date, key, call_fn=call):
    raw_meta, meta_status = call_fn(
        "/tiingo/daily/%s" % ticker,
        None,
        key,
    )
    require_http_200(meta_status, "metadata")

    meta = load_json(raw_meta, "metadata")
    if not isinstance(meta, dict):
        raise Stop("metadata 응답 형식이 object 가 아닙니다.")

    start_raw = str(meta.get("startDate") or "")[:10]
    try:
        coverage_start = date.fromisoformat(start_raw)
    except ValueError:
        raise Stop("metadata startDate 가 없거나 잘못되었습니다.")

    requested_start = date.fromisoformat(start_date)
    if coverage_start > requested_start:
        raise Stop("요청 시작일을 source coverage 가 포함하지 않습니다.")

    raw_px, px_status = call_fn(
        "/tiingo/daily/%s/prices" % ticker,
        {
            "startDate": start_date,
            "endDate": end_date,
        },
        key,
    )
    require_http_200(px_status, "prices")

    rows = load_json(raw_px, "prices")
    if not isinstance(rows, list) or not rows:
        raise Stop("요청 구간의 price rows 가 비어 있습니다.")

    for row in rows:
        if not isinstance(row, dict):
            raise Stop("price row 형식이 object 가 아닙니다.")

        missing = REQUIRED_FIELDS.difference(row)
        if missing:
            raise Stop("필수 EOD field 누락이 있습니다.")

    adjusted_diff = any(
        row.get("close") != row.get("adjClose")
        or row.get("volume") != row.get("adjVolume")
        for row in rows
    )

    corporate_action_field_seen = all(
        "divCash" in row and "splitFactor" in row
        for row in rows
    )

    # Explicitly discard references before returning sanitized results.
    del rows, meta, raw_px, raw_meta

    return {
        "schema_ok": True,
        "coverage_ok": True,
        "nonempty_window": True,
        "adjusted_diff_observed": bool(adjusted_diff),
        "corporate_action_fields_present": bool(
            corporate_action_field_seen
        ),
        "historical_adjusted_pit_qualified": False,
    }


def run_probe(
    key,
    tickers=DEFAULT_TICKERS,
    start_date=DEFAULT_START,
    end_date=DEFAULT_END,
    call_fn=call,
):
    clean_tickers, _start, _end = validate_inputs(
        tickers,
        start_date,
        end_date,
    )

    raw_auth, auth_status = call_fn("/api/test/", None, key)
    require_http_200(auth_status, "auth")

    auth = load_json(raw_auth, "auth")
    auth_ok = "success" in json.dumps(
        auth,
        ensure_ascii=False,
    ).lower()

    del raw_auth, auth

    if not auth_ok:
        raise Stop("Tiingo auth response did not confirm success.")

    passed = 0
    adjusted_diff_count = 0

    for ticker in clean_tickers:
        result = probe_ticker(
            ticker,
            start_date,
            end_date,
            key,
            call_fn=call_fn,
        )

        if not all((
            result["schema_ok"],
            result["coverage_ok"],
            result["nonempty_window"],
            result["corporate_action_fields_present"],
        )):
            raise Stop("source capability contract failed.")

        passed += 1
        adjusted_diff_count += int(
            result["adjusted_diff_observed"]
        )

    return {
        "auth_ok": True,
        "ticker_count": len(clean_tickers),
        "ticker_pass_count": passed,
        "required_schema_ok": passed == len(clean_tickers),
        "historical_window_nonempty": passed == len(clean_tickers),
        "adjusted_difference_ticker_count": adjusted_diff_count,
        "historical_adjusted_pit_qualified": False,
        "starter_retention_contract": "TRANSIENT_ONLY",
        "persistent_tiingo_data_written": 0,
        "regime_score_authorized": False,
        "probe_pass": passed == len(clean_tickers),
    }


def format_summary(result):
    lines = [
        "Atlas R-1 Tiingo transient contract probe",
        "auth=%s" % (
            "PASS" if result["auth_ok"] else "FAIL"
        ),
        "tickers_requested=%d" % result["ticker_count"],
        "tickers_passed=%d" % result["ticker_pass_count"],
        "required_schema=%s" % (
            "PASS"
            if result["required_schema_ok"]
            else "FAIL"
        ),
        "historical_window_data=%s" % (
            "PASS"
            if result["historical_window_nonempty"]
            else "FAIL"
        ),
        "adjusted_difference_detected=%s" % (
            "YES"
            if result["adjusted_difference_ticker_count"]
            else "NO"
        ),
        "historical_adjusted_pit=NOT_QUALIFIED",
        "starter_retention_contract=TRANSIENT_ONLY",
        "persistent_tiingo_data_written=0",
        "regime_score_authorized=NO",
        "PROBE=%s" % (
            "PASS" if result["probe_pass"] else "FAIL"
        ),
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START,
    )
    parser.add_argument(
        "--end-date",
        default=DEFAULT_END,
    )
    args = parser.parse_args(argv)

    tickers = (
        tuple(args.tickers)
        if args.tickers
        else DEFAULT_TICKERS
    )

    try:
        result = run_probe(
            api_key(),
            tickers=tickers,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        print(format_summary(result))
        return 0
    except Stop as e:
        print("STOP: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
