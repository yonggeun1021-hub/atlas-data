#!/usr/bin/env python3
"""KR T2 condition C3 (liquidity) -- pure evaluator over the TKT-2 price history.

Rule source: user ratification ``PAPER-LIQUIDITY-KR-US-V1-20260914``,
committed byte-identically at
``evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json``.
Every threshold is read from that file at load time, after its sha256 has
been checked against ``RATIFICATION_SHA256`` below. This module holds no
threshold number of its own: if the file is missing, altered, or its KR
block does not have the exact shape this code was written against,
``load_ratified_kr_rule`` returns ``None`` and every evaluation is
``UNKNOWN`` (the record's own ``fail_closed`` clause).

What the record says for KR, and how each clause is applied:

* ``avg_traded_value_20_sessions_krw_min`` -- mean of the provider's own
  ``ACC_TRDVAL`` (compact field ``value``) over exactly the 20 most recent
  completed sessions available at the evaluation instant. Never averaged over
  fewer sessions.
* ``last_close_krw_min`` -- ``TDD_CLSPRC`` (compact ``close``) of the required
  session.
* ``exclude`` (trading halted, 관리종목) -- supplied by the caller as the
  private ``kr_c3_status_exclusion_result/1`` produced from the KIS masters
  (atlas-private-evidence ``private_evidence/kis_master_status_flags.py``).
  ``CLEAR`` -> PASS, ``EXCLUDED`` -> FAIL, anything else or absent -> UNKNOWN.
* ``window`` -- the official KRX calendar's last 20 open sessions ending at
  ``required_session`` (the last completed session). Never the last 20
  *stored* sessions: an EMPTY or missing session is not skipped over.
  "fewer than 20 sessions of history -> NOT_EVALUATED" applies only to the
  symbol's own listing history -- every window session is stored and the
  symbol's rows start inside the window and run to its end.
* ``fail_closed`` -- missing price history, a missing flag, or stale data ->
  ``UNKNOWN``. Any window session the store does not hold as OK (never stored,
  EMPTY, not yet available at the instant, calendar year missing), a row gap
  inside or at the end of the window, or a flag result for another
  session/symbol are all UNKNOWN. No age threshold is invented: through
  ``evaluate_from_store`` the required session must be exactly the latest
  session whose earliest collection instant (collector contract) is at or
  before the evaluation instant; anything else is stale -> UNKNOWN.

Status combination (same order as ``universe/us_liquidity_sip_source.py``):
``FAIL`` beats ``UNKNOWN`` beats ``PASS``.

Distribution boundary: the per-symbol result carries a value derived from
KRX OpenAPI rows (the 20-session average). It belongs with the private store
and is never committed to this public repository. ``public_summary`` returns
status counts only.

No network call, no credential, no clock read.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]

RULE_ID = "RULE.LIQUIDITY.KR_T2_C3.V1"
RESULT_SCHEMA_VERSION = "kr_liquidity_c3_result/1"
SUMMARY_SCHEMA_VERSION = "kr_liquidity_c3_public_summary/1"
MARKET = "KR"

RATIFICATION_ID = "PAPER-LIQUIDITY-KR-US-V1-20260914"
RATIFICATION_RELPATH = "evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json"
RATIFICATION_SHA256 = "1e068439c4e43050072e38c7450dea38baccf701dab23e784467c9ce48ef40d6"

# The exact KR block this code was written against. A different key set or a
# different exclusion list means the record changed meaning -> fail closed.
_KR_KEYS = {"avg_traded_value_20_sessions_krw_min", "last_close_krw_min", "exclude", "window"}
_KR_EXCLUDE = ["trading halted", "administrative issue (관리종목)"]
_KR_WINDOW_PREFIX = "20 completed KRX sessions;"
# The "20" is the rule's own window (named in the ratified key and window text),
# not a threshold.
WINDOW_SESSIONS = 20

STATUS_RESULT_SCHEMA_VERSION = "kr_c3_status_exclusion_result/1"
ALLOWED_STATUSES = ("PASS", "FAIL", "UNKNOWN", "NOT_EVALUATED")

_BAS_DD_RE = re.compile(r"^\d{8}$")
_SHORT_CODE_RE = re.compile(r"^[A-Z0-9]{1,9}$")
_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


class KrLiquidityC3Error(ValueError):
    """A caller passed a malformed argument -- a code defect, not a data fact."""


# ---------------------------------------------------------------- the rule


def load_ratified_kr_rule(root: Path = ROOT) -> Optional[dict]:
    """The KR rule read from the sha-verified record, or ``None`` (fail closed)."""
    return describe_ratified_kr_rule(root)["rule"]


def describe_ratified_kr_rule(root: Path = ROOT) -> dict:
    """Non-raising diagnostic: ``{"status", "problems", "rule"}``."""
    path = Path(root) / RATIFICATION_RELPATH
    try:
        raw = path.read_bytes()
    except OSError:
        return {"status": "RATIFICATION_MISSING", "problems": [RATIFICATION_RELPATH], "rule": None}
    actual = hashlib.sha256(raw).hexdigest()
    if actual != RATIFICATION_SHA256:
        return {"status": "RATIFICATION_HASH_MISMATCH", "problems": [actual], "rule": None}
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return {"status": "RATIFICATION_MALFORMED", "problems": ["JSON"], "rule": None}
    problems = []
    if not isinstance(record, dict) or record.get("ratification_id") != RATIFICATION_ID:
        problems.append("RATIFICATION_ID")
        record = record if isinstance(record, dict) else {}
    kr = record.get("KR")
    if not isinstance(kr, dict) or set(kr) != _KR_KEYS:
        problems.append("KR_BLOCK_SHAPE")
        kr = {}
    avg_min = kr.get("avg_traded_value_20_sessions_krw_min")
    close_min = kr.get("last_close_krw_min")
    for name, value in (("avg_traded_value_20_sessions_krw_min", avg_min), ("last_close_krw_min", close_min)):
        if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value):
            problems.append(f"KR_{name.upper()}_INVALID")
    if kr and kr.get("exclude") != _KR_EXCLUDE:
        problems.append("KR_EXCLUDE_CHANGED")
    window = kr.get("window")
    if kr and (not isinstance(window, str) or not window.startswith(_KR_WINDOW_PREFIX)
               or "NOT_EVALUATED" not in window):
        problems.append("KR_WINDOW_CHANGED")
    fail_closed = record.get("fail_closed")
    if not isinstance(fail_closed, str) or "UNKNOWN" not in fail_closed:
        problems.append("FAIL_CLOSED_CLAUSE_CHANGED")
    authority = record.get("authority")
    if not isinstance(authority, dict) or any(v is not False for v in authority.values()):
        problems.append("AUTHORITY_NOT_ALL_FALSE")
    if problems:
        return {"status": "RATIFICATION_SHAPE_CHANGED", "problems": problems, "rule": None}
    return {
        "status": "RATIFIED",
        "problems": [],
        "rule": {
            "rule_id": RULE_ID,
            "ratification_id": RATIFICATION_ID,
            "ratification_path": RATIFICATION_RELPATH,
            "ratification_sha256": RATIFICATION_SHA256,
            "avg_traded_value_krw_min": avg_min,
            "last_close_krw_min": close_min,
            "window_sessions": WINDOW_SESSIONS,
            "exclude": list(_KR_EXCLUDE),
        },
    }


# ------------------------------------------------------------- evaluation


def _combine(*statuses: str) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    return "PASS"


def _bas_dd(value: object, code: str) -> str:
    if not isinstance(value, str) or not _BAS_DD_RE.fullmatch(value):
        raise KrLiquidityC3Error(code)
    return value


def _amount(value: object) -> Optional[Decimal]:
    if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:  # pragma: no cover - regex already guards
        return None


def _iso(bas_dd: str) -> str:
    return f"{bas_dd[0:4]}-{bas_dd[4:6]}-{bas_dd[6:8]}"


def _status_exclusion(value: object, short_code: str, required_session: str) -> tuple[str, list[str]]:
    if value is None:
        return "UNKNOWN", ["STATUS_FLAGS_MISSING"]
    if not isinstance(value, Mapping) or value.get("schema_version") != STATUS_RESULT_SCHEMA_VERSION:
        return "UNKNOWN", ["STATUS_FLAGS_SCHEMA_INVALID"]
    if value.get("ratification_id") != RATIFICATION_ID:
        return "UNKNOWN", ["STATUS_FLAGS_RATIFICATION_MISMATCH"]
    if value.get("short_code") != short_code:
        return "UNKNOWN", ["STATUS_FLAGS_SYMBOL_MISMATCH"]
    if value.get("required_session") != _iso(required_session):
        return "UNKNOWN", ["STATUS_FLAGS_SESSION_MISMATCH"]
    result = value.get("result")
    if result == "CLEAR":
        return "PASS", []
    if result == "EXCLUDED":
        return "FAIL", ["STATUS_EXCLUDED:" + ",".join(str(r) for r in value.get("reasons") or [])]
    return "UNKNOWN", ["STATUS_FLAGS_UNKNOWN:" + ",".join(str(r) for r in value.get("reasons") or [])]


def evaluate_symbol(
    short_code: str,
    *,
    calendar_sessions: Iterable[str],
    available_sessions: Iterable[str],
    rows: Iterable[Mapping[str, Any]],
    required_session: str,
    status_exclusion: Optional[Mapping[str, Any]],
    rule_root: Path = ROOT,
) -> dict:
    """Apply KR T2 C3 to one symbol.

    ``calendar_sessions`` are the official calendar's last 20 open sessions
    ending at ``required_session`` (``YYYYMMDD``, the last completed session at
    evaluation time); ``available_sessions`` are the sessions the store holds
    as OK at the evaluation instant; ``rows`` are the symbol's stored rows on
    those sessions; ``status_exclusion`` is the private KIS-master result for
    the same symbol and session.  The rule is always re-read from the
    sha-verified record under ``rule_root`` -- a caller cannot pass thresholds.
    """
    if not isinstance(short_code, str) or not _SHORT_CODE_RE.fullmatch(short_code):
        raise KrLiquidityC3Error("SHORT_CODE_INVALID")
    required_session = _bas_dd(required_session, "REQUIRED_SESSION_INVALID")
    window = [_bas_dd(day, "WINDOW_SESSION_INVALID") for day in calendar_sessions]
    if window != sorted(set(window)):
        raise KrLiquidityC3Error("WINDOW_NOT_ASCENDING_UNIQUE")
    if len(window) != WINDOW_SESSIONS or window[-1] != required_session:
        raise KrLiquidityC3Error("CALENDAR_WINDOW_NOT_LAST_20_ENDING_AT_REQUIRED_SESSION")
    available = {_bas_dd(day, "AVAILABLE_SESSION_INVALID") for day in available_sessions}
    by_day: dict[str, dict] = {}
    for row in rows:
        row = dict(row)
        day = _bas_dd(row.get("bas_dd"), "ROW_BAS_DD_INVALID")
        if row.get("code") != short_code:
            raise KrLiquidityC3Error("ROW_CODE_MISMATCH")
        if day in by_day or day not in window or day not in available:
            raise KrLiquidityC3Error("ROW_OUTSIDE_WINDOW_OR_DUPLICATE")
        by_day[day] = row

    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "rule_id": RULE_ID,
        "market": MARKET,
        "short_code": short_code,
        "required_session": required_session,
        "window_first_session": window[0],
        "window_last_session": window[-1],
        "window_session_count": len(window),
        "missing_sessions": [],
        "symbol_session_count": len(by_day),
        "avg_traded_value_krw": None,
        "last_close_krw": None,
        "traded_value_status": None,
        "last_close_status": None,
        "status_exclusion_status": None,
        "status": "UNKNOWN",
        "reasons": [],
        "ratification_id": RATIFICATION_ID,
        "ratification_sha256": RATIFICATION_SHA256,
        "distribution": "PRIVATE_DERIVED_FROM_KRX_OPENAPI_ROWS",
        "authority": {"candidate_authorized": False, "order_authorized": False, "real_capital_authorized": False},
    }

    rule = load_ratified_kr_rule(rule_root)
    if rule is None:
        result["reasons"] = ["RATIFIED_RULE_UNAVAILABLE"]
        return result

    # Store-level completeness: every calendar session of the window must be
    # held as OK.  Missing, EMPTY or not-yet-available sessions are missing
    # history -> UNKNOWN; the window is never stretched to older sessions.
    missing = [day for day in window if day not in available]
    if missing:
        result["missing_sessions"] = missing
        result["reasons"] = ["PRICE_HISTORY_SESSION_MISSING:" + ",".join(missing)]
        return result

    present = [day for day in window if day in by_day]
    if not present:
        result["reasons"] = ["SYMBOL_NOT_IN_PRICE_HISTORY"]
        return result
    if len(present) < WINDOW_SESSIONS:
        first = window.index(present[0])
        if present == window[first:]:
            # Every session is stored and the symbol's rows start inside the
            # window and run to its end: its own listing history is shorter
            # than 20 sessions.
            result["status"] = "NOT_EVALUATED"
            result["reasons"] = ["SYMBOL_LISTING_HISTORY_SHORTER_THAN_WINDOW"]
            return result
        result["reasons"] = ["SYMBOL_SESSION_ROW_MISSING"]
        return result

    values = [_amount(by_day[day].get("value")) for day in window]
    close = _amount(by_day[required_session].get("close"))
    reasons: list[str] = []
    if any(v is None for v in values):
        traded_value_status = "UNKNOWN"
        reasons.append("TRADED_VALUE_MISSING")
    else:
        avg = sum(values, Decimal(0)) / Decimal(WINDOW_SESSIONS)
        result["avg_traded_value_krw"] = format(avg, "f")
        traded_value_status = "PASS" if avg >= Decimal(rule["avg_traded_value_krw_min"]) else "FAIL"
        if traded_value_status == "FAIL":
            reasons.append("AVG_TRADED_VALUE_BELOW_MIN")
    if close is None:
        last_close_status = "UNKNOWN"
        reasons.append("LAST_CLOSE_MISSING")
    else:
        result["last_close_krw"] = format(close, "f")
        last_close_status = "PASS" if close >= Decimal(rule["last_close_krw_min"]) else "FAIL"
        if last_close_status == "FAIL":
            reasons.append("LAST_CLOSE_BELOW_MIN")
    status_status, status_reasons = _status_exclusion(status_exclusion, short_code, required_session)
    reasons.extend(status_reasons)

    result.update(
        traded_value_status=traded_value_status,
        last_close_status=last_close_status,
        status_exclusion_status=status_status,
        status=_combine(traded_value_status, last_close_status, status_status),
        reasons=reasons,
    )
    return result


def _unknown_without_window(short_code: str, required_session: str, reason: str) -> dict:
    return {
        "schema_version": RESULT_SCHEMA_VERSION, "rule_id": RULE_ID, "market": MARKET,
        "short_code": short_code, "required_session": required_session,
        "status": "UNKNOWN", "reasons": [reason],
        "ratification_id": RATIFICATION_ID, "ratification_sha256": RATIFICATION_SHA256,
        "distribution": "PRIVATE_DERIVED_FROM_KRX_OPENAPI_ROWS",
        "authority": {"candidate_authorized": False, "order_authorized": False, "real_capital_authorized": False},
    }


def latest_collectable_session(store: Any, as_of_utc: str) -> str:
    """The latest official open session whose earliest collection instant is at
    or before ``as_of_utc`` -- the only ``required_session`` that is not stale.

    Uses the pinned collector's own helpers: ``forward_target_session`` gives
    the latest open session before the as-of local date (the as-of day's own
    session is never collectable yet); if that session's
    ``earliest_collection_instant`` is still in the future, the open session
    before it is the latest collectable one.
    """
    collector, contract, root = store.collector, store.contract, store.code_root
    instant = collector.parse_utc(as_of_utc, "AS_OF_INVALID")
    previous = collector.forward_target_session(instant, contract, market=MARKET, root=root)
    if collector.earliest_collection_instant(previous, contract, MARKET) <= instant:
        return previous
    return collector.open_sessions_ending(previous, 2, contract, market=MARKET, root=root)[0]


def evaluate_from_store(
    store: Any,
    short_code: str,
    *,
    as_of_utc: str,
    required_session: str,
    status_exclusion: Optional[Mapping[str, Any]],
    rule_root: Path = ROOT,
) -> dict:
    """``evaluate_symbol`` fed from a ``universe.price_history_store.PriceHistoryStore``.

    ``required_session`` must be the latest session collectable at
    ``as_of_utc`` (see :func:`latest_collectable_session`); an older one is
    stale data and a newer one is not yet published -- both UNKNOWN, before
    any row is read.  A calendar that cannot resolve the sessions (no
    committed capture for a year, ``required_session`` not an open session)
    is missing history -> UNKNOWN, not an exception.
    """
    try:
        latest = latest_collectable_session(store, as_of_utc)
    except ValueError as exc:  # PriceHistoryError / PriceHistoryStoreError are ValueErrors
        return _unknown_without_window(short_code, required_session, f"CALENDAR_WINDOW_UNAVAILABLE:{exc}")
    if required_session != latest:
        return _unknown_without_window(
            short_code, required_session,
            f"REQUIRED_SESSION_NOT_LATEST_COLLECTABLE:required={required_session}:latest={latest}",
        )
    try:
        window = store.calendar_window(MARKET, WINDOW_SESSIONS, required_session, as_of_utc)
    except ValueError as exc:
        return _unknown_without_window(short_code, required_session, f"CALENDAR_WINDOW_UNAVAILABLE:{exc}")
    sessions = window["sessions"]
    available = [day for day in sessions if day not in set(window["missing"])]
    return evaluate_symbol(
        short_code,
        calendar_sessions=sessions,
        available_sessions=available,
        rows=store.rows_on_sessions(MARKET, short_code, available, as_of_utc),
        required_session=required_session,
        status_exclusion=status_exclusion,
        rule_root=rule_root,
    )


def public_summary(results: Iterable[Mapping[str, Any]]) -> dict:
    """Counts only -- no code, price, traded value or per-symbol reason."""
    counts = {status: 0 for status in ALLOWED_STATUSES}
    sessions = set()
    for item in results:
        if item.get("schema_version") != RESULT_SCHEMA_VERSION or item.get("status") not in counts:
            raise KrLiquidityC3Error("SUMMARY_INPUT_INVALID")
        counts[item["status"]] += 1
        sessions.add(item["required_session"])
    if len(sessions) > 1:
        raise KrLiquidityC3Error("SUMMARY_MIXED_SESSIONS")
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "rule_id": RULE_ID,
        "market": MARKET,
        "required_session": next(iter(sessions)) if sessions else None,
        "symbol_count": sum(counts.values()),
        "status_counts": counts,
        "ratification_id": RATIFICATION_ID,
        "ratification_sha256": RATIFICATION_SHA256,
    }

