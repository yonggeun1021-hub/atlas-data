#!/usr/bin/env python3
"""KR all-stock daily price history: pure derivation for ``price_history_session/1``.

This module holds only pure functions.  It never writes a store, never
decides where bytes live, and touches the network through exactly one
injectable ``opener`` parameter, mirroring the existing split between the
thin network CLI ``collectors/krx_official_holiday_calendar.py`` and the
pure adapter ``market_data/krx_official_holiday_calendar.py``.

Nothing here is reimplemented that already exists:

* the request and the per-row provider validation come from
  ``.github/scripts/korea_breadth.py`` (``build_request`` /
  ``validate_snapshot``) against ``config/korea_breadth_contract.json``;
* open/closed for a ``bas_dd`` comes from
  ``market_data/krx_official_holiday_calendar.py`` and therefore from the
  official KRX calendar capture alone.  A session is never inferred to be
  a holiday because a response was empty, and a year with no committed
  calendar capture fails closed rather than defaulting to "open".

Fail-closed rules carried by this module:

* a collection requested before the official close plus the market settle
  offset is rejected -- there is no pre-market collection path at all;
* a closed ``bas_dd`` is rejected;
* a zero-row response is classified ``EMPTY`` and is never projected into
  session data;
* ``LIST_SHRS`` moving between two consecutive stored sessions raises
  ``CORPORATE_ACTION_SUSPECT`` for that code instead of being carried
  through as an ordinary price move.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "price_history_contract.json"
BAS_DD_RE = re.compile(r"^[0-9]{8}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PriceHistoryError(ValueError):
    """Stable failure code; never carries a credential or a raw payload."""


def _load_module(name: str, relative: str, root: Path = ROOT):
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(root) / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PriceHistoryError(f"MODULE_IMPORT_FAILED:{relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def breadth_module(root: Path = ROOT):
    """The existing KRX OpenAPI request/validation module, imported not copied."""
    return _load_module(
        "price_history_korea_breadth", ".github/scripts/korea_breadth.py", root
    )


def calendar_module(root: Path = ROOT):
    """The existing official KRX holiday-calendar adapter, imported not copied."""
    return _load_module(
        "price_history_krx_official_holiday_calendar",
        "market_data/krx_official_holiday_calendar.py",
        root,
    )


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def payload_sha256(value: object) -> str:
    return digest(canonical_bytes(value))


def utc_text(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise PriceHistoryError("INSTANT_NOT_TIMEZONE_AWARE")
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: object, code: str = "INSTANT_INVALID") -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise PriceHistoryError(code)
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )


# ---------------------------------------------------------------- contract


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PriceHistoryError("CONTRACT_READ_FAILED") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise PriceHistoryError("CONTRACT_SCHEMA_UNSUPPORTED")
    if value.get("contract_version") != "price_history_session/1":
        raise PriceHistoryError("CONTRACT_VERSION_UNSUPPORTED")
    if value.get("adjustment") != "NONE":
        raise PriceHistoryError("CONTRACT_ADJUSTMENT_INVALID")
    if value.get("pre_close_collection") != "PROHIBITED":
        raise PriceHistoryError("CONTRACT_PRE_CLOSE_POLICY_INVALID")
    if value.get("empty_is_not_a_holiday_signal") is not True:
        raise PriceHistoryError("CONTRACT_EMPTY_POLICY_INVALID")
    if any(bool(flag) for key, flag in (value.get("authority") or {}).items()
           if key != "price_history_observation_only"):
        raise PriceHistoryError("CONTRACT_AUTHORITY_PROMOTED")
    value["_contract_sha256"] = digest(raw)
    return value


def contract_sha256(contract: Mapping[str, Any]) -> str:
    sha = contract.get("_contract_sha256")
    if not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None:
        raise PriceHistoryError("CONTRACT_SHA256_UNKNOWN")
    return sha


def market_source(contract: Mapping[str, Any], market: str) -> dict:
    if market not in (contract.get("markets") or []):
        raise PriceHistoryError(f"MARKET_UNSUPPORTED:{market}")
    source = (contract.get("sources") or {}).get(market)
    if not isinstance(source, dict):
        raise PriceHistoryError(f"MARKET_SOURCE_NOT_DEFINED:{market}")
    return source


def validate_bas_dd(value: object) -> str:
    if not isinstance(value, str) or BAS_DD_RE.fullmatch(value) is None:
        raise PriceHistoryError("BAS_DD_FORMAT_INVALID")
    try:
        parsed = dt.date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError as exc:
        raise PriceHistoryError("BAS_DD_CALENDAR_INVALID") from exc
    return parsed.strftime("%Y%m%d")


def bas_dd_to_iso(bas_dd: str) -> str:
    day = validate_bas_dd(bas_dd)
    return f"{day[0:4]}-{day[4:6]}-{day[6:8]}"


# ---------------------------------------------------- official calendar only


def resolve_calendar_capture(
    bas_dd: str, contract: Mapping[str, Any], market: str = "KR", root: Path = ROOT
) -> Path:
    """The committed official calendar capture covering ``bas_dd``'s year.

    Fails closed when that year has no capture.  A missing year is never
    treated as "probably a normal weekday": the 2025 capture required by the
    260-session backfill does not exist yet and must be produced by a real
    network run of ``collectors/krx_official_holiday_calendar.py``.
    """
    source = market_source(contract, market)
    year = validate_bas_dd(bas_dd)[0:4]
    pattern = str(source["calendar_capture_glob"]).format(year=year)
    matches = sorted(Path(root).glob(pattern))
    if not matches:
        raise PriceHistoryError(f"CALENDAR_CAPTURE_MISSING:{year}")
    return matches[-1]


def session_status(
    bas_dd: str,
    calendar_capture_raw: bytes,
    *,
    source_ref: str,
    contract: Mapping[str, Any] | None = None,
    market: str = "KR",
    root: Path = ROOT,
) -> dict:
    """OPEN_REGULAR / CLOSED for ``bas_dd``, from the official calendar alone."""
    contract = contract or load_contract()
    market_source(contract, market)
    calendar = calendar_module(root)
    try:
        packet, receipt = calendar.build_calendar_packet(
            calendar_capture_raw, source_ref, bas_dd_to_iso(bas_dd)
        )
    except calendar.KrxOfficialHolidayCalendarError as exc:
        raise PriceHistoryError(f"CALENDAR_DERIVATION_FAILED:{exc}") from exc
    return {
        "bas_dd": validate_bas_dd(bas_dd),
        "status": packet["calendar"]["status"],
        "reason": receipt["reason"],
        "calendar_source_ref": source_ref,
        "calendar_source_sha256": packet["official_response_sha256"],
        "calendar_available_at": packet["calendar"]["available_at"],
    }


def earliest_collection_instant(
    bas_dd: str, contract: Mapping[str, Any], market: str = "KR"
) -> dt.datetime:
    """Provider publication plus the market settle offset, as an aware instant.

    KR: the session's daily rows are not served on the session day itself (a
    same-day 18:13 KST request came back EMPTY; the next morning it had rows),
    so the earliest collection is the next calendar day at ``publication_local``
    plus ``settle_offset_minutes`` -- 09:10 KST.
    """
    source = market_source(contract, market)
    zone = ZoneInfo(str(source["session_timezone"]))
    hour, minute = (int(part) for part in str(source["publication_local"]).split(":"))
    day = dt.date.fromisoformat(bas_dd_to_iso(bas_dd)) + dt.timedelta(
        days=int(source["publication_day_offset_calendar_days"])
    )
    published = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    return published + dt.timedelta(minutes=int(source["settle_offset_minutes"]))


def _is_open(
    bas_dd: str, contract: Mapping[str, Any], market: str, root: Path,
    cache: dict[str, tuple[bytes, str]],
) -> bool:
    year = validate_bas_dd(bas_dd)[0:4]
    if year not in cache:
        path = resolve_calendar_capture(bas_dd, contract, market, root)
        try:
            ref = path.relative_to(Path(root)).as_posix()
        except ValueError:
            ref = path.as_posix()
        cache[year] = (path.read_bytes(), ref)
    raw, ref = cache[year]
    verdict = session_status(bas_dd, raw, source_ref=ref, contract=contract,
                             market=market, root=root)
    return verdict["status"] == market_source(contract, market)["calendar_open_status"]


def forward_target_session(
    now_utc: dt.datetime,
    contract: Mapping[str, Any],
    *,
    market: str = "KR",
    root: Path = ROOT,
    max_lookback_days: int = 14,
) -> str:
    """The session a forward run collects: the latest official open session
    strictly before the run's local date (Monday -> Friday, after a holiday ->
    the last open day), decided by the official calendar alone."""
    if now_utc.tzinfo is None:
        raise PriceHistoryError("NOW_NOT_TIMEZONE_AWARE")
    source = market_source(contract, market)
    local_day = now_utc.astimezone(ZoneInfo(str(source["session_timezone"]))).date()
    cache: dict[str, tuple[bytes, str]] = {}
    for back in range(1, max_lookback_days + 1):
        bas_dd = (local_day - dt.timedelta(days=back)).strftime("%Y%m%d")
        if _is_open(bas_dd, contract, market, root, cache):
            return bas_dd
    raise PriceHistoryError("FORWARD_TARGET_SESSION_NOT_FOUND")


def open_sessions_ending(
    end_bas_dd: str,
    count: int,
    contract: Mapping[str, Any],
    *,
    market: str = "KR",
    root: Path = ROOT,
) -> list[str]:
    """The ``count`` official open sessions ending at (and including) ``end_bas_dd``,
    ascending.  ``end_bas_dd`` itself must be open.  A year without a committed
    calendar capture raises ``CALENDAR_CAPTURE_MISSING:<year>``."""
    if not isinstance(count, int) or count <= 0:
        raise PriceHistoryError("SESSION_COUNT_INVALID")
    day = dt.date.fromisoformat(bas_dd_to_iso(end_bas_dd))
    cache: dict[str, tuple[bytes, str]] = {}
    if not _is_open(end_bas_dd, contract, market, root, cache):
        raise PriceHistoryError(f"END_SESSION_NOT_OPEN:{validate_bas_dd(end_bas_dd)}")
    found: list[str] = []
    for _ in range(count * 4 + 40):
        bas_dd = day.strftime("%Y%m%d")
        if _is_open(bas_dd, contract, market, root, cache):
            found.append(bas_dd)
            if len(found) == count:
                return sorted(found)
        day -= dt.timedelta(days=1)
    raise PriceHistoryError("SESSION_WALK_EXHAUSTED")


def assert_collectable(
    bas_dd: str,
    *,
    now_utc: dt.datetime,
    calendar_capture_raw: bytes,
    calendar_source_ref: str,
    contract: Mapping[str, Any] | None = None,
    market: str = "KR",
    root: Path = ROOT,
) -> dict:
    """Fail closed unless ``bas_dd`` is an officially open, already-settled session.

    Rejects, in order: a malformed date, a year with no official calendar
    capture, a closed session (weekend or listed holiday), and any instant
    before the provider publication instant plus the settle offset (KR: next
    calendar day 08:00 + 70 min = 09:10 KST).  There is no flag, argument, or
    environment variable that permits an earlier collection.
    """
    contract = contract or load_contract()
    day = validate_bas_dd(bas_dd)
    if now_utc.tzinfo is None:
        raise PriceHistoryError("NOW_NOT_TIMEZONE_AWARE")
    verdict = session_status(
        day,
        calendar_capture_raw,
        source_ref=calendar_source_ref,
        contract=contract,
        market=market,
        root=root,
    )
    if verdict["status"] != market_source(contract, market)["calendar_open_status"]:
        raise PriceHistoryError(f"SESSION_CLOSED:{day}:{verdict['reason']}")
    earliest = earliest_collection_instant(day, contract, market)
    if now_utc.astimezone(dt.timezone.utc) < earliest.astimezone(dt.timezone.utc):
        raise PriceHistoryError(f"PRE_PUBLICATION_COLLECTION_REFUSED:{day}")
    return {
        **verdict,
        "earliest_collection_at_utc": utc_text(earliest),
        "collectable": True,
    }


# ---------------------------------------------------------------- projection


def _normalise(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    return text or None


def part_rows(raw: bytes, bas_dd: str, part: str, *, root: Path = ROOT) -> list[dict]:
    """The provider rows of one part, or ``[]`` when the provider returned none."""
    breadth = breadth_module(root)
    contract = breadth.load_contract()
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PriceHistoryError(f"RAW_NOT_JSON:{part}") from exc
    if not isinstance(payload, dict):
        raise PriceHistoryError(f"RAW_ROOT_NOT_OBJECT:{part}")
    rows = payload.get(contract["response_block"])
    if not isinstance(rows, list):
        raise PriceHistoryError(f"RESPONSE_BLOCK_MISSING:{part}")
    if not rows:
        return []
    try:
        breadth.validate_snapshot(payload, bas_dd, part, contract=contract)
    except breadth.BreadthError as exc:
        raise PriceHistoryError(f"PART_VALIDATION_FAILED:{part}:{exc}") from exc
    return rows


def classify(raw_by_part: Mapping[str, bytes], bas_dd: str, *, root: Path = ROOT) -> str:
    """``EMPTY`` only when every part returned zero rows; a partial fails closed."""
    counts = {
        part: len(part_rows(raw_by_part[part], bas_dd, part, root=root))
        for part in sorted(raw_by_part)
    }
    if not counts:
        raise PriceHistoryError("NO_PARTS_SUPPLIED")
    if all(count == 0 for count in counts.values()):
        return "EMPTY"
    empty = sorted(part for part, count in counts.items() if count == 0)
    if empty:
        raise PriceHistoryError("PART_ROW_COUNT_ZERO:" + ",".join(empty))
    return "OK"


def derive_compact_rows(
    raw_by_part: Mapping[str, bytes],
    bas_dd: str,
    contract: Mapping[str, Any] | None = None,
    *,
    market: str = "KR",
    root: Path = ROOT,
) -> list[list]:
    """``compact.jsonl`` rows as a pure function of the exact raw bytes."""
    contract = contract or load_contract()
    source = market_source(contract, market)
    mapping = source["field_mapping"]
    fields = list(contract["compact_row_fields"])
    if sorted(mapping) != sorted(fields):
        raise PriceHistoryError("FIELD_MAPPING_INCOMPLETE")
    day = validate_bas_dd(bas_dd)
    by_code: dict[str, list] = {}
    for part in sorted(raw_by_part):
        for row in part_rows(raw_by_part[part], day, part, root=root):
            code = _normalise(row.get(mapping["code"]))
            if code is None:
                raise PriceHistoryError(f"CODE_EMPTY:{part}")
            if code in by_code:
                raise PriceHistoryError(f"CODE_DUPLICATE_ACROSS_PARTS:{code}")
            by_code[code] = [_normalise(row.get(mapping[field])) for field in fields]
    return [by_code[code] for code in sorted(by_code)]


def compact_bytes(rows: Iterable[Iterable]) -> bytes:
    lines = [
        json.dumps(list(row), ensure_ascii=False, separators=(",", ":"))
        for row in rows
    ]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def parse_compact(raw: bytes, contract: Mapping[str, Any] | None = None) -> list[dict]:
    """Compact bytes back into field-named dicts, in stored order."""
    contract = contract or load_contract()
    fields = list(contract["compact_row_fields"])
    parsed = []
    for line in bytes(raw).decode("utf-8").splitlines():
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PriceHistoryError("COMPACT_LINE_NOT_JSON") from exc
        if not isinstance(row, list) or len(row) != len(fields):
            raise PriceHistoryError("COMPACT_ROW_ARITY_INVALID")
        parsed.append(dict(zip(fields, row)))
    return parsed


def rederive_compact(
    raw_by_part: Mapping[str, bytes],
    bas_dd: str,
    stored_compact: bytes,
    contract: Mapping[str, Any] | None = None,
    *,
    market: str = "KR",
    root: Path = ROOT,
) -> dict:
    """Prove stored compact bytes are exactly what raw re-derives to."""
    contract = contract or load_contract()
    rebuilt = compact_bytes(
        derive_compact_rows(raw_by_part, bas_dd, contract, market=market, root=root)
    )
    return {
        "rederived": rebuilt == bytes(stored_compact),
        "stored_compact_sha256": digest(bytes(stored_compact)),
        "rederived_compact_sha256": digest(rebuilt),
        "projection_version": contract["projection_version"],
    }


def raw_sha256(raw_by_part: Mapping[str, bytes]) -> str:
    """One session digest rolled up from each part's exact provider bytes."""
    return payload_sha256(
        {part: digest(bytes(raw_by_part[part])) for part in sorted(raw_by_part)}
    )


# ------------------------------------------------------- corporate actions


def corporate_action_suspects(
    previous_rows: Iterable[Mapping[str, Any]] | None,
    current_rows: Iterable[Mapping[str, Any]],
) -> list[dict]:
    """Codes whose ``list_shrs`` is discontinuous against the prior session.

    A listed-share change is reported as ``CORPORATE_ACTION_SUSPECT`` and is
    never folded into an ordinary return.  With no prior session there is
    nothing to compare and the list is empty -- absence of a comparison is
    never reported as "no corporate action".
    """
    if previous_rows is None:
        return []
    before = {
        row.get("code"): row.get("list_shrs")
        for row in previous_rows
        if row.get("code") is not None
    }
    suspects = []
    for row in current_rows:
        code = row.get("code")
        if code is None or code not in before:
            continue
        prior = before[code]
        latest = row.get("list_shrs")
        if prior is None or latest is None or prior == latest:
            continue
        suspects.append(
            {
                "code": code,
                "flag": "CORPORATE_ACTION_SUSPECT",
                "previous_list_shrs": prior,
                "list_shrs": latest,
            }
        )
    return sorted(suspects, key=lambda item: item["code"])


# ------------------------------------------------------------------ returns


def session_gross_factor(row: Mapping[str, Any]) -> Any:
    """``1 + FLUC_RT/100`` for one stored session, as a Decimal.

    ``adjustment`` is ``NONE``, so a multi-session return is this factor
    compounded -- never ``close_last / close_first``, which a split would
    silently corrupt.
    """
    from decimal import Decimal, InvalidOperation

    value = row.get("fluc_rt")
    if value is None:
        raise PriceHistoryError("FLUC_RT_MISSING")
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise PriceHistoryError("FLUC_RT_INVALID") from exc
    if not rate.is_finite():
        raise PriceHistoryError("FLUC_RT_INVALID")
    return Decimal(1) + rate / Decimal(100)


def compound_return(rows: Iterable[Mapping[str, Any]]) -> Any:
    """Cumulative gross factor across the supplied sessions, in stored order."""
    from decimal import Decimal, localcontext

    items = list(rows)
    if not items:
        raise PriceHistoryError("RETURN_WINDOW_EMPTY")
    with localcontext() as context:
        context.prec = 50
        factor = Decimal(1)
        for row in items:
            factor = factor * session_gross_factor(row)
    return factor


def implied_previous_close(row: Mapping[str, Any]) -> Any:
    """``close - CMPPREVDD`` -- the provider's own unadjusted prior close."""
    from decimal import Decimal, InvalidOperation

    try:
        close = Decimal(str(row["close"]))
        change = Decimal(str(row["cmpprevdd"]))
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise PriceHistoryError("CMPPREVDD_OR_CLOSE_INVALID") from exc
    return close - change


# ----------------------------------------------------------------- manifest


def build_manifest(
    *,
    market: str,
    bas_dd: str,
    status: str,
    parts: list[Mapping[str, Any]],
    compact: bytes,
    compact_row_count: int,
    pit_class: str,
    attempts: list[Mapping[str, Any]],
    public_code_commit: str,
    contract: Mapping[str, Any],
    corporate_actions: list[Mapping[str, Any]] | None = None,
    source_contract_sha256: str | None = None,
    first_available_observed_at_utc: str | None = None,
) -> dict:
    """A complete ``price_history_session/1`` manifest, every core field present."""
    day = validate_bas_dd(bas_dd)
    market_source(contract, market)
    if status not in contract["session_statuses"]:
        raise PriceHistoryError(f"STATUS_UNSUPPORTED:{status}")
    if pit_class not in contract["pit_classes"]:
        raise PriceHistoryError(f"PIT_CLASS_UNSUPPORTED:{pit_class}")
    if not attempts:
        raise PriceHistoryError("ATTEMPTS_EMPTY")
    for attempt in attempts:
        if attempt.get("kind") not in contract["attempt_kinds"]:
            raise PriceHistoryError("ATTEMPT_KIND_UNSUPPORTED")
    if not isinstance(public_code_commit, str) or len(public_code_commit) != 40:
        raise PriceHistoryError("PUBLIC_CODE_COMMIT_INVALID")
    statuses = {int(part["http_status"]) for part in parts} if parts else set()
    if len(statuses) > 1:
        raise PriceHistoryError("HTTP_STATUS_NOT_UNIFORM")
    winning = attempts[-1]
    row_count = sum(int(part["row_count"]) for part in parts)
    if status == "EMPTY" and row_count:
        raise PriceHistoryError("EMPTY_STATUS_WITH_ROWS")
    if status == "OK" and row_count != compact_row_count:
        raise PriceHistoryError("ROW_COUNT_COMPACT_MISMATCH")
    manifest = {
        "schema_version": contract["contract_version"],
        "market": market,
        "bas_dd": day,
        "status": status,
        "endpoint": sorted({str(part["endpoint"]) for part in parts}),
        "requested_at_utc": winning["requested_at_utc"],
        "retrieved_at_utc": winning["retrieved_at_utc"],
        "http_status": next(iter(statuses)) if statuses else None,
        "row_count": row_count,
        "compact_row_count": compact_row_count,
        "raw_sha256": payload_sha256(
            {str(part["part_id"]): str(part["raw_sha256"]) for part in parts}
        ),
        "compact_sha256": digest(bytes(compact)),
        "projection_version": contract["projection_version"],
        "contract_sha256": contract_sha256(contract),
        "source_contract_sha256": source_contract_sha256,
        "public_code_commit": public_code_commit,
        "pit_class": pit_class,
        "first_available_observed_at_utc": first_available_observed_at_utc,
        "parts": [copy.deepcopy(dict(part)) for part in sorted(
            parts, key=lambda item: str(item["part_id"])
        )],
        "attempts": [copy.deepcopy(dict(attempt)) for attempt in attempts],
        "corporate_action_suspects": [
            copy.deepcopy(dict(item)) for item in (corporate_actions or [])
        ],
        "authority": copy.deepcopy(contract["authority"]),
    }
    return validate_manifest(manifest, contract)


def validate_manifest(manifest: Mapping[str, Any], contract: Mapping[str, Any]) -> dict:
    expected = set(contract["manifest_core_fields"]) | set(
        contract["manifest_additional_fields"]
    )
    if set(manifest) != expected:
        missing = sorted(expected - set(manifest))
        extra = sorted(set(manifest) - expected)
        raise PriceHistoryError(f"MANIFEST_FIELDS_INVALID:missing={missing}:extra={extra}")
    if manifest.get("schema_version") != contract["contract_version"]:
        raise PriceHistoryError("MANIFEST_SCHEMA_MISMATCH")
    if manifest.get("projection_version") != contract["projection_version"]:
        raise PriceHistoryError("MANIFEST_PROJECTION_MISMATCH")
    if manifest.get("pit_class") not in contract["pit_classes"]:
        raise PriceHistoryError("MANIFEST_PIT_CLASS_INVALID")
    if manifest.get("status") not in contract["session_statuses"]:
        raise PriceHistoryError("MANIFEST_STATUS_INVALID")
    for field in ("raw_sha256", "compact_sha256", "contract_sha256"):
        if SHA256_RE.fullmatch(str(manifest.get(field))) is None:
            raise PriceHistoryError(f"MANIFEST_SHA256_INVALID:{field}")
    if any(bool(flag) for key, flag in (manifest.get("authority") or {}).items()
           if key != "price_history_observation_only"):
        raise PriceHistoryError("MANIFEST_AUTHORITY_PROMOTED")
    if manifest.get("status") == "OK":
        if not manifest.get("first_available_observed_at_utc"):
            raise PriceHistoryError("FIRST_AVAILABLE_MISSING")
        parse_utc(manifest["first_available_observed_at_utc"])
    elif manifest.get("first_available_observed_at_utc") is not None:
        raise PriceHistoryError("EMPTY_STATUS_CLAIMS_AVAILABILITY")
    parse_utc(manifest.get("requested_at_utc"), "MANIFEST_REQUESTED_AT_INVALID")
    parse_utc(manifest.get("retrieved_at_utc"), "MANIFEST_RETRIEVED_AT_INVALID")
    return copy.deepcopy(dict(manifest))


# ------------------------------------------------------------------ network


def build_part_request(auth_key: str, bas_dd: str, part: str, *, root: Path = ROOT):
    """Delegates entirely to ``korea_breadth.build_request``."""
    breadth = breadth_module(root)
    try:
        return breadth.build_request(auth_key, validate_bas_dd(bas_dd), part)
    except breadth.BreadthError as exc:
        raise PriceHistoryError(f"REQUEST_BUILD_FAILED:{exc}") from exc


def fetch_part(
    auth_key: str,
    bas_dd: str,
    part: str,
    *,
    opener: Callable[..., Any],
    timeout_seconds: float = 30.0,
    root: Path = ROOT,
) -> dict:
    """One GET for one part.  ``opener`` is the only network surface here.

    There is no default opener on purpose: a caller -- including every test --
    must hand in the transport explicitly, so no code path can reach the
    provider by accident.
    """
    if opener is None:
        raise PriceHistoryError("OPENER_REQUIRED")
    request = build_part_request(auth_key, bas_dd, part, root=root)
    if getattr(request, "method", "GET") != "GET":
        raise PriceHistoryError("NETWORK_METHOD_NOT_GET")
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            raw = response.read()
    except Exception as exc:  # noqa: BLE001 - never leak a provider message
        raise PriceHistoryError(f"GET_TRANSPORT_{type(exc).__name__.upper()}") from None
    if status != 200:
        raise PriceHistoryError(f"GET_HTTP_STATUS_{status}")
    return {
        "part_id": part,
        "endpoint": request.full_url.split("?", 1)[0],
        "http_status": status,
        "raw": bytes(raw),
        "raw_sha256": digest(bytes(raw)),
        "raw_byte_count": len(raw),
    }
