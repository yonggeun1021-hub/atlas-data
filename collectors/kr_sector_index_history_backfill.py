#!/usr/bin/env python3
"""Bounded, resumable, write-once KRX sector index history backfill.

Scope: the 46 ratified Korea rotation sectors (KOSPI 24 / KOSDAQ 22) and their
own benchmarks (`config/korea_capital_rotation_policy_ratified.json`), read
from the same official KRX Open API index endpoints the Korea market-signal
and leadership lanes already use (`idx/kospi_dd_trd`, `idx/kosdaq_dd_trd`).
One request per market per `basDd` returns every index series of that market,
so one requested date costs exactly two requests.

Request dates are every Monday-Friday in the inclusive range; Saturday and
Sunday are closed by KRX market rule and are not requested. A weekday is never
assumed to be a session: a date is SESSION_CONFIRMED only when both markets
return rows with a matching BAS_DD, NON_SESSION_EMPTY_RESPONSE when both are
empty, and DISAGREEMENT otherwise or when an official KRX [01023] holiday
capture for that year says the opposite.

Fail-closed stops: HTTP 401/403/429 (and any other 4xx), KRX error response
codes, schema/date violations, request budget exhaustion and repeated
transient failures stop the run immediately with no further requests.

Data handling: KRX Open API terms restrict third-party provision and no
redistribution right is established in this repository (see
`docs/krx_execution_measurement_contract.md`). Raw response bytes are never
retained. Per-(market, date) records holding the ratified series closes and
trading values are written once, only to a directory outside this checkout,
and are never uploaded. The receipt carries counts, dates, statuses and
SHA-256 hashes only.

Default mode (`--plan-only`) makes zero network calls. This module is a tool:
nothing here schedules or dispatches a live run.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data import krx_official_holiday_calendar as CALENDAR  # noqa: E402

CONTRACT_PATH = ROOT / "config" / "kr_sector_index_history_backfill_contract.json"
ENDPOINT_CONTRACT_PATH = ROOT / "config" / "korea_market_signals_contract.json"
POLICY_PATH = ROOT / "config" / "korea_capital_rotation_policy_ratified.json"
KST = ZoneInfo("Asia/Seoul")
MARKETS = ("KOSPI", "KOSDAQ")
RECORD_SCHEMA = "kr_sector_index_history_record/1"
RECEIPT_SCHEMA = "kr_sector_index_history_backfill_receipt/1"
PLAN_SCHEMA = "kr_sector_index_history_backfill_plan/1"
USER_AGENT = "Atlas-KR-Sector-Index-History/1.0"

AUTHORITY = {
    "history_backfill_tool_only": True,
    "execution_authorized_by_this_contract": False,
    "policy_change_authorized": False,
    "candidate_authorized": False,
    "order_authorized": False,
    "trading_authorized": False,
    "real_capital_authorized": False,
}


class BackfillError(ValueError):
    """Input or local state rejected before or outside provider calls."""


class BackfillStop(Exception):
    """Fail-closed stop: no further provider request may be made."""

    def __init__(self, reason: str, *, http_status=None, bas_dd=None, market=None):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status
        self.bas_dd = bas_dd
        self.market = market


class TransientFailure(Exception):
    def __init__(self, reason: str, http_status=None):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def compact_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackfillError(code) from exc
    if not isinstance(value, dict):
        raise BackfillError(code)
    return value


def load_contract(path: Path = CONTRACT_PATH, endpoint_path: Path = ENDPOINT_CONTRACT_PATH) -> dict:
    contract = _read_json(path, "BACKFILL_CONTRACT_INVALID")
    if contract.get("schema_version") != "kr_sector_index_history_backfill_contract/1":
        raise BackfillError("BACKFILL_CONTRACT_INVALID:schema")
    endpoints = _read_json(endpoint_path, "ENDPOINT_CONTRACT_INVALID").get("index_endpoints")
    expected = {"KOSPI": endpoints.get("kospi"), "KOSDAQ": endpoints.get("kosdaq")} if isinstance(endpoints, dict) else None
    if contract.get("endpoints") != expected:
        raise BackfillError("BACKFILL_ENDPOINTS_DIVERGE_FROM_MARKET_SIGNALS_CONTRACT")
    if contract.get("requests_per_requested_date") != len(MARKETS):
        raise BackfillError("BACKFILL_CONTRACT_INVALID:requests_per_date")
    if sorted(contract.get("stop_http_statuses", [])) != [401, 403, 429]:
        raise BackfillError("BACKFILL_CONTRACT_INVALID:stop_statuses")
    boundary = contract.get("data_use_boundary", {})
    if boundary.get("derived_closes_committable") is not False or boundary.get("artifact_contains_index_values") is not False:
        raise BackfillError("BACKFILL_CONTRACT_INVALID:data_use_boundary")
    if contract.get("storage", {}).get("raw_response_bytes_retained") is not False:
        raise BackfillError("BACKFILL_CONTRACT_INVALID:storage")
    for key, value in contract.get("authority", {}).items():
        if key.endswith("_authorized") and value is not False:
            raise BackfillError(f"BACKFILL_CONTRACT_INVALID:authority.{key}")
    return copy.deepcopy(contract)


def ratified_identities(path: Path = POLICY_PATH) -> dict:
    """{market: {"benchmark": identity, "members": [identity, ...]}} exactly as ratified."""
    policy = _read_json(path, "RATIFIED_POLICY_INVALID")
    if policy.get("approval_status") != "RATIFIED":
        raise BackfillError("RATIFIED_POLICY_NOT_RATIFIED")
    result = {}
    for scope in policy.get("benchmark_scopes", []):
        benchmark = scope.get("benchmark_identity", "")
        market = benchmark.split("::", 1)[0]
        if market not in MARKETS or market in result:
            raise BackfillError("RATIFIED_POLICY_SCOPE_INVALID")
        members = [row.get("series_identity") for row in scope.get("members", [])]
        if (
            not members
            or len(set(members)) != len(members)
            or any(not isinstance(m, str) or not m.startswith(market + "::") for m in members)
            or benchmark in members
        ):
            raise BackfillError("RATIFIED_POLICY_MEMBERS_INVALID")
        result[market] = {"benchmark": benchmark, "members": sorted(members)}
    if set(result) != set(MARKETS):
        raise BackfillError("RATIFIED_POLICY_SCOPE_INVALID")
    if (len(result["KOSPI"]["members"]), len(result["KOSDAQ"]["members"])) != (24, 22):
        raise BackfillError("RATIFIED_POLICY_MEMBER_COUNT_CHANGED")
    return result


def _parse_iso(value: str, code: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise BackfillError(code) from exc
    if parsed.isoformat() != value:
        raise BackfillError(code)
    return parsed


def load_calendar_captures(paths) -> dict:
    """{year: {"closures": set(iso), "capture_sha256": str}} from official KRX [01023] captures."""
    calendars = {}
    for path in paths or ():
        raw = Path(path).read_bytes()
        try:
            checked = CALENDAR.validate_capture(raw)
        except CALENDAR.KrxOfficialHolidayCalendarError as exc:
            raise BackfillError(f"CALENDAR_CAPTURE_INVALID:{exc}") from exc
        year = checked["capture"]["year"]
        if year in calendars:
            raise BackfillError("CALENDAR_CAPTURE_DUPLICATE_YEAR")
        calendars[year] = {
            "closures": set(checked["closures"]),
            "capture_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return calendars


def build_plan(start: str, end: str, *, contract: dict, calendars: dict | None = None, today_kst: dt.date | None = None) -> dict:
    first = _parse_iso(start, "RANGE_START_INVALID")
    last = _parse_iso(end, "RANGE_END_INVALID")
    today_kst = today_kst or dt.datetime.now(KST).date()
    if first > last:
        raise BackfillError("RANGE_ORDER_INVALID")
    if first < dt.date.fromisoformat(contract["minimum_official_date"]):
        raise BackfillError("RANGE_PRECEDES_MINIMUM_OFFICIAL_DATE")
    if last >= today_kst:
        raise BackfillError("RANGE_END_NOT_BEFORE_TODAY_KST")
    span = (last - first).days + 1
    if span > contract["maximum_range_calendar_days"]:
        raise BackfillError("RANGE_EXCEEDS_MAXIMUM_CALENDAR_DAYS")
    calendars = calendars or {}
    dates, weekend_days = [], 0
    day = first
    while day <= last:
        if day.weekday() >= 5:
            weekend_days += 1
        else:
            calendar = calendars.get(day.year)
            if calendar is None:
                calendar_status = "NO_OFFICIAL_CAPTURE"
            elif day.isoformat() in calendar["closures"]:
                calendar_status = "CLOSED"
            else:
                calendar_status = "OPEN_REGULAR"
            dates.append({"bas_dd": day.strftime("%Y%m%d"), "calendar_status": calendar_status})
        day += dt.timedelta(days=1)
    planned = len(dates) * contract["requests_per_requested_date"]
    if planned > contract["hard_max_planned_requests"]:
        raise BackfillError("PLANNED_REQUESTS_EXCEED_HARD_MAXIMUM")
    years = sorted({int(row["bas_dd"][:4]) for row in dates})
    return {
        "schema_version": PLAN_SCHEMA,
        "range": {"start": start, "end": end},
        "calendar_day_count": span,
        "weekend_days_not_requested": weekend_days,
        "requested_weekday_count": len(dates),
        "planned_requests": planned,
        "hard_max_planned_requests": contract["hard_max_planned_requests"],
        "hard_max_http_attempts": contract["hard_max_http_attempts"],
        "calendar_years": {
            str(year): (
                {"source": "KRX_GLOBAL_MARKET_CLOSING_HOLIDAY_01023", "capture_sha256": calendars[year]["capture_sha256"]}
                if year in calendars
                else {"source": "NONE_RESPONSE_ONLY", "capture_sha256": None}
            )
            for year in years
        },
        "dates": dates,
    }


def estimate_wallclock_seconds(plan: dict, min_interval: float) -> float:
    return plan["planned_requests"] * min_interval


def forbid_checkout_output(path: Path) -> None:
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    raise BackfillError("OUTPUT_INSIDE_REPOSITORY_FORBIDDEN")


def build_request(auth_key: str, endpoint: str, bas_dd: str) -> Request:
    key = str(auth_key or "").strip()
    if not key:
        raise BackfillError("KRX_API_KEY_MISSING")
    return Request(
        endpoint + "?" + urlencode({"basDd": bas_dd}),
        headers={"AUTH_KEY": key, "Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )


def fetch_once(request: Request, *, opener, timeout: int) -> bytes:
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read()
    except HTTPError as exc:
        if exc.code >= 500:
            raise TransientFailure("HTTP_5XX", exc.code) from None
        raise BackfillStop("HTTP_STATUS_STOP", http_status=exc.code) from None
    except (URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise TransientFailure(type(exc).__name__) from None
    if status != 200:
        if isinstance(status, int) and status >= 500:
            raise TransientFailure("HTTP_5XX", status)
        raise BackfillStop("HTTP_STATUS_STOP", http_status=status)
    return body


def _clean_number(value) -> str | None:
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        float(text)
    except ValueError:
        return None
    return text


def parse_response(body: bytes, bas_dd: str, market: str, identities: dict, contract: dict) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BackfillStop("RESPONSE_NOT_JSON", bas_dd=bas_dd, market=market) from None
    if not isinstance(payload, dict):
        raise BackfillStop("RESPONSE_ROOT_INVALID", bas_dd=bas_dd, market=market)
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        code = str(payload.get("respCode", "")).strip()
        if code in contract["stop_response_codes"]:
            raise BackfillStop("RESPONSE_CODE_STOP", http_status=int(code), bas_dd=bas_dd, market=market)
        raise BackfillStop("RESPONSE_SCHEMA_INVALID", bas_dd=bas_dd, market=market)
    wanted = {identities[market]["benchmark"], *identities[market]["members"]}
    series, names = {}, set()
    usable = 0
    for row in rows:
        if not isinstance(row, dict):
            raise BackfillStop("RESPONSE_ROW_INVALID", bas_dd=bas_dd, market=market)
        if str(row.get("BAS_DD", "")).strip() != bas_dd:
            raise BackfillStop("RESPONSE_DATE_MISMATCH", bas_dd=bas_dd, market=market)
        name = str(row.get("IDX_NM") or "").strip()
        if not name:
            raise BackfillStop("RESPONSE_INDEX_NAME_EMPTY", bas_dd=bas_dd, market=market)
        close = _clean_number(row.get("CLSPRC_IDX"))
        if close is not None:
            usable += 1
        identity = f"{market}::{name}"
        if identity in wanted:
            if identity in series:
                raise BackfillStop("RESPONSE_RATIFIED_IDENTITY_DUPLICATE", bas_dd=bas_dd, market=market)
            series[identity] = {"close": close, "trading_value": _clean_number(row.get("ACC_TRDVAL"))}
        names.add(name)
    return {
        "schema_version": RECORD_SCHEMA,
        "market": market,
        "bas_dd": bas_dd,
        "status": "ROWS" if rows else "EMPTY",
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_bytes": len(body),
        "row_count": len(rows),
        "usable_row_count": usable,
        "index_name_catalog_sha256": compact_sha256(sorted(names)) if names else None,
        "matched_identity_count": sum(1 for v in series.values() if v["close"] is not None),
        "series": dict(sorted(series.items())),
    }


RECORD_FIELDS = {
    "schema_version", "market", "bas_dd", "status", "response_sha256", "response_bytes",
    "row_count", "usable_row_count", "index_name_catalog_sha256", "matched_identity_count", "series",
}


def record_path(records_dir: Path, market: str, bas_dd: str) -> Path:
    return Path(records_dir) / market / f"{bas_dd}.json"


def validate_record(record: dict, market: str, bas_dd: str) -> dict:
    if (
        not isinstance(record, dict)
        or set(record) != RECORD_FIELDS
        or record["schema_version"] != RECORD_SCHEMA
        or record["market"] != market
        or record["bas_dd"] != bas_dd
        or record["status"] not in ("ROWS", "EMPTY")
        or not isinstance(record["series"], dict)
        or (record["status"] == "EMPTY") != (record["row_count"] == 0)
    ):
        raise BackfillError(f"RECORD_INVALID:{market}:{bas_dd}")
    return record


def load_record(records_dir: Path, market: str, bas_dd: str) -> dict | None:
    path = record_path(records_dir, market, bas_dd)
    if not path.exists():
        return None
    return validate_record(_read_json(path, f"RECORD_INVALID:{market}:{bas_dd}"), market, bas_dd)


def write_once(path: Path, record: dict) -> None:
    """Atomic create-only write: an existing record is never replaced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_bytes(record))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise BackfillError(f"RECORD_ALREADY_EXISTS:{path.parent.name}:{path.stem}") from None
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def records_payload_sha256(records_dir: Path, plan: dict) -> str:
    """Binds public outputs to the exact private records set used."""
    entries = []
    for row in plan["dates"]:
        for market in MARKETS:
            record = load_record(records_dir, market, row["bas_dd"])
            if record is not None:
                entries.append(record)
    return compact_sha256(entries)


def run_backfill(
    auth_key: str,
    plan: dict,
    records_dir: Path,
    *,
    contract: dict,
    identities: dict,
    opener=urlopen,
    sleep=time.sleep,
    monotonic=time.monotonic,
    min_interval: float | None = None,
) -> dict:
    pacing = contract["pacing"]
    min_interval = pacing["default_min_interval_seconds"] if min_interval is None else float(min_interval)
    if min_interval < pacing["min_interval_floor_seconds"]:
        raise BackfillError("MIN_INTERVAL_BELOW_FLOOR")
    if not str(auth_key or "").strip():
        raise BackfillError("KRX_API_KEY_MISSING")
    forbid_checkout_output(records_dir)
    backoffs = list(pacing["transient_retry_backoff_seconds"])
    accounting = {
        "planned_requests": plan["planned_requests"],
        "http_attempts": 0,
        "transient_retries": 0,
        "records_written": 0,
        "records_reused": 0,
        "requests_blocked_after_retries": 0,
    }
    stop = None
    blocked = []
    consecutive_transient = 0
    last_request = [None]

    def paced_fetch(request):
        if accounting["http_attempts"] >= contract["hard_max_http_attempts"]:
            raise BackfillStop("HTTP_ATTEMPT_BUDGET_EXHAUSTED")
        if last_request[0] is not None:
            wait = min_interval - (monotonic() - last_request[0])
            if wait > 0:
                sleep(wait)
        accounting["http_attempts"] += 1
        try:
            return fetch_once(request, opener=opener, timeout=pacing["timeout_seconds"])
        finally:
            last_request[0] = monotonic()

    try:
        for row in plan["dates"]:
            bas_dd = row["bas_dd"]
            for market in MARKETS:
                if load_record(records_dir, market, bas_dd) is not None:
                    accounting["records_reused"] += 1
                    continue
                request = build_request(auth_key, contract["endpoints"][market], bas_dd)
                body = None
                for attempt in range(len(backoffs) + 1):
                    try:
                        body = paced_fetch(request)
                        break
                    except BackfillStop as exc:
                        exc.bas_dd, exc.market = bas_dd, market
                        raise
                    except TransientFailure:
                        if attempt < len(backoffs):
                            accounting["transient_retries"] += 1
                            sleep(backoffs[attempt])
                if body is None:
                    accounting["requests_blocked_after_retries"] += 1
                    blocked.append({"bas_dd": bas_dd, "market": market})
                    consecutive_transient += 1
                    if consecutive_transient >= pacing["max_consecutive_transient_failures"]:
                        raise BackfillStop("CONSECUTIVE_TRANSIENT_FAILURES", bas_dd=bas_dd, market=market)
                    continue
                consecutive_transient = 0
                record = parse_response(body, bas_dd, market, identities, contract)
                write_once(record_path(records_dir, market, bas_dd), record)
                accounting["records_written"] += 1
    except BackfillStop as exc:
        stop = {
            "reason": exc.reason,
            "http_status": exc.http_status,
            "bas_dd": exc.bas_dd,
            "market": exc.market,
        }
    return build_receipt(
        plan,
        records_dir,
        contract=contract,
        identities=identities,
        accounting=accounting,
        stop=stop,
        blocked=blocked,
        min_interval=min_interval,
    )


def build_receipt(plan, records_dir, *, contract, identities, accounting, stop, blocked, min_interval) -> dict:
    manifest, sessions, non_sessions, disagreements = [], [], [], []
    catalogs = {market: {} for market in MARKETS}
    coverage = {}
    for market in MARKETS:
        for identity in [identities[market]["benchmark"], *identities[market]["members"]]:
            coverage[identity] = {"present_session_count": 0, "first_present": None, "last_present": None}
    missing_dates = 0
    for row in plan["dates"]:
        bas_dd = row["bas_dd"]
        iso = f"{bas_dd[:4]}-{bas_dd[4:6]}-{bas_dd[6:]}"
        records = {market: load_record(records_dir, market, bas_dd) for market in MARKETS}
        for market, record in records.items():
            if record is None:
                continue
            manifest.append({
                "bas_dd": bas_dd,
                "market": market,
                "status": record["status"],
                "response_sha256": record["response_sha256"],
                "response_bytes": record["response_bytes"],
                "row_count": record["row_count"],
                "usable_row_count": record["usable_row_count"],
                "matched_identity_count": record["matched_identity_count"],
                "index_name_catalog_sha256": record["index_name_catalog_sha256"],
            })
            if record["index_name_catalog_sha256"]:
                seen = catalogs[market].setdefault(
                    record["index_name_catalog_sha256"], {"first_seen": iso, "last_seen": iso, "record_count": 0}
                )
                seen["last_seen"] = iso
                seen["record_count"] += 1
        if any(record is None for record in records.values()):
            missing_dates += 1
            continue
        statuses = {records[m]["status"] for m in MARKETS}
        calendar_status = row["calendar_status"]
        if statuses == {"ROWS"}:
            if calendar_status == "CLOSED":
                disagreements.append({"date": iso, "kind": "CALENDAR_CLOSED_BUT_ROWS"})
                continue
            sessions.append(iso)
            for market in MARKETS:
                for identity, value in records[market]["series"].items():
                    if value["close"] is not None and float(value["close"]) > 0:
                        cell = coverage[identity]
                        cell["present_session_count"] += 1
                        cell["first_present"] = cell["first_present"] or iso
                        cell["last_present"] = iso
        elif statuses == {"EMPTY"}:
            if calendar_status == "OPEN_REGULAR":
                disagreements.append({"date": iso, "kind": "CALENDAR_OPEN_BUT_EMPTY"})
                continue
            non_sessions.append(iso)
        else:
            disagreements.append({"date": iso, "kind": "MARKETS_DISAGREE"})
    for identity, cell in coverage.items():
        cell["missing_session_count"] = len(sessions) - cell["present_session_count"]
    if stop is not None:
        status = "STOPPED"
    elif blocked or missing_dates:
        status = "INCOMPLETE_BLOCKED"
    elif disagreements:
        status = "DISAGREEMENT"
    else:
        status = "COMPLETE"
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "status": status,
        "stop": stop,
        "range": copy.deepcopy(plan["range"]),
        "source": {
            "name": contract["source_name"],
            "endpoints": copy.deepcopy(contract["endpoints"]),
            "contract_sha256": file_sha256(CONTRACT_PATH),
            "ratified_policy_sha256": file_sha256(POLICY_PATH),
            "module_sha256": file_sha256(Path(__file__)),
        },
        "request_accounting": {
            **accounting,
            "requested_weekday_count": plan["requested_weekday_count"],
            "weekend_days_not_requested": plan["weekend_days_not_requested"],
            "hard_max_planned_requests": plan["hard_max_planned_requests"],
            "hard_max_http_attempts": plan["hard_max_http_attempts"],
            "min_interval_seconds": min_interval,
            "blocked_requests": blocked,
            "dates_without_both_records": missing_dates,
        },
        "calendar_years": copy.deepcopy(plan["calendar_years"]),
        "sessions": {
            "session_confirmed_count": len(sessions),
            "first_session": sessions[0] if sessions else None,
            "last_session": sessions[-1] if sessions else None,
            "non_session_empty_response_count": len(non_sessions),
            "non_session_empty_response_dates": non_sessions,
            "disagreement_count": len(disagreements),
            "disagreements": disagreements,
            "weekday_inference_used_for_sessions": False,
        },
        "identity_coverage": coverage,
        "index_name_catalogs": {
            market: [{"catalog_sha256": key, **value} for key, value in sorted(catalogs[market].items(), key=lambda kv: kv[1]["first_seen"])]
            for market in MARKETS
        },
        "manifest_sha256": compact_sha256(manifest),
        "records_payload_sha256": records_payload_sha256(records_dir, plan),
        "manifest": manifest,
        "data_handling": {
            "provider_bytes_retained": False,
            "private_records_location": "runner temp outside checkout; never uploaded",
            "public_contains_index_values": False,
            "terms_basis": contract["data_use_boundary"]["basis"],
        },
        "authority": copy.deepcopy(AUTHORITY),
    }
    return receipt


def write_json(path: Path, value: dict) -> None:
    path = Path(path)
    forbid_checkout_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="inclusive YYYY-MM-DD, before today KST")
    parser.add_argument("--calendar-capture", action="append", default=[], type=Path)
    parser.add_argument("--records-dir", type=Path)
    parser.add_argument("--receipt-out", type=Path)
    parser.add_argument("--min-interval-seconds", type=float)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    contract = load_contract()
    identities = ratified_identities()
    calendars = load_calendar_captures(args.calendar_capture)
    plan = build_plan(args.start, args.end, contract=contract, calendars=calendars)
    interval = args.min_interval_seconds or contract["pacing"]["default_min_interval_seconds"]
    summary = {key: value for key, value in plan.items() if key != "dates"}
    summary["estimated_pacing_seconds"] = estimate_wallclock_seconds(plan, interval)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if args.plan_only:
        return 0
    if args.records_dir is None or args.receipt_out is None:
        raise BackfillError("RECORDS_DIR_AND_RECEIPT_OUT_REQUIRED")
    forbid_checkout_output(args.records_dir)
    forbid_checkout_output(args.receipt_out)
    receipt = run_backfill(
        os.environ.get("KRX_API_KEY", ""),
        plan,
        args.records_dir,
        contract=contract,
        identities=identities,
        min_interval=args.min_interval_seconds,
    )
    write_json(args.receipt_out, receipt)
    print(json.dumps({
        "status": receipt["status"],
        "stop": receipt["stop"],
        "request_accounting": {k: v for k, v in receipt["request_accounting"].items() if k != "blocked_requests"},
        "session_confirmed_count": receipt["sessions"]["session_confirmed_count"],
        "disagreement_count": receipt["sessions"]["disagreement_count"],
    }, ensure_ascii=False, sort_keys=True))
    return {"COMPLETE": 0, "STOPPED": 3, "INCOMPLETE_BLOCKED": 4, "DISAGREEMENT": 5}[receipt["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
