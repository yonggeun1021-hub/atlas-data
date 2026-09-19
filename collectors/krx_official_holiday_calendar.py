#!/usr/bin/env python3
"""Capture the official KRX Global [01023] annual holiday response."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_data import krx_official_holiday_calendar as CAL


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _read(opener, request: urllib.request.Request) -> tuple[bytes, int, str, str]:
    with opener.open(request, timeout=20) as response:
        return (
            response.read(),
            response.status,
            response.headers.get("Content-Type", ""),
            response.geturl(),
        )


def capture_year(year: int, *, opener=None) -> dict:
    if type(year) is not int or not 2009 <= year <= 2100:
        raise CAL.KrxOfficialHolidayCalendarError("YEAR_INVALID")
    if opener is None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            NoRedirect(),
        )
    started = _utc_now()
    headers = {"User-Agent": "Atlas-KRX-Calendar/1", "Accept": "*/*"}
    page_raw, page_status, _, page_final = _read(
        opener, urllib.request.Request(CAL.PAGE_URL, headers=headers, method="GET")
    )
    if page_status != 200 or page_final != CAL.PAGE_URL or CAL.BLD.encode() not in page_raw:
        raise CAL.KrxOfficialHolidayCalendarError("PAGE_RESPONSE_INVALID")
    otp_query = urllib.parse.urlencode({"bld": CAL.BLD, "name": "form"})
    otp_raw, otp_status, _, otp_final = _read(
        opener,
        urllib.request.Request(
            CAL.OTP_URL + "?" + otp_query,
            headers={**headers, "Referer": CAL.PAGE_URL},
            method="GET",
        ),
    )
    if otp_status != 200 or otp_final.split("?", 1)[0] != CAL.OTP_URL or not otp_raw.strip():
        raise CAL.KrxOfficialHolidayCalendarError("OTP_RESPONSE_INVALID")
    body = urllib.parse.urlencode({
        "search_bas_yy": str(year),
        "gridTp": "KRX",
        "pagePath": "",
        "code": otp_raw.decode("ascii").strip(),
    }).encode("ascii")
    raw, status, content_type, final_url = _read(
        opener,
        urllib.request.Request(
            CAL.DATA_URL,
            data=body,
            headers={
                **headers,
                "Referer": CAL.PAGE_URL,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        ),
    )
    received = _utc_now()
    bundle = {
        "schema_version": CAL.CAPTURE_SCHEMA,
        "provider_id": CAL.PROVIDER_ID,
        "market": "KOREA",
        "venue_scope": "KRX_ONLY",
        "year": year,
        "page_url": CAL.PAGE_URL,
        "otp_url": CAL.OTP_URL,
        "data_url": CAL.DATA_URL,
        "bld": CAL.BLD,
        "market_rule_url": CAL.MARKET_RULE_URL,
        "request": {
            "search_bas_yy": str(year),
            "gridTp": "KRX",
            "pagePath": "",
            "network_operations": ["PAGE_GET", "OTP_GET", "HOLIDAY_POST"],
            "redirects_allowed": False,
        },
        "capture_started_at": started,
        "response_received_at": received,
        "page_raw_sha256": hashlib.sha256(page_raw).hexdigest(),
        "otp_retained": False,
        "response": {
            "http_status": status,
            "content_type": content_type,
            "final_url": final_url,
            "redirect_count": 0,
            "raw_base64": base64.b64encode(raw).decode("ascii"),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        },
        "authority": {
            "market_calendar_observation_only": True,
            "candidate_authorized": False,
            "entry_authorized": False,
            "order_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }
    CAL.validate_capture(CAL.canonical_bytes(bundle))
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("year", type=int)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    bundle = capture_year(args.year)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(CAL.canonical_bytes(bundle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
