"""Fail-closed source-byte capture for the KRX information-system candidate."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

ENDPOINT = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
BUILDERS = {
    "dbms/MDC/STAT/standard/MDCSTAT01501": ("stock", "mktId", {"STK": "KOSPI", "KSQ": "KOSDAQ"}),
    "dbms/MDC/STAT/standard/MDCSTAT00101": ("index", "idxIndMidclssCd", {"02": "KOSPI", "03": "KOSDAQ"}),
}
PUBLIC_FIELDS = {"bld", "trdDd", "mktId", "idxIndMidclssCd"}
STOCK_FIELDS = {
    "ISU_CD", "ISU_SRT_CD", "ISU_ABBRV", "MKT_NM", "SECT_TP_NM", "TDD_CLSPRC", "FLUC_TP_CD",
    "CMPPREVDD_PRC", "FLUC_RT", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL",
    "ACC_TRDVAL", "MKTCAP", "LIST_SHRS", "MKT_ID", "lateInfoMsgCd",
}
STOCK_REQUIRED = {"ISU_SRT_CD", "TDD_CLSPRC", "FLUC_RT", "ACC_TRDVAL", "MKTCAP"}
INDEX_FIELDS = {
    "IDX_NM", "CLSPRC_IDX", "FLUC_TP_CD", "CMPPREVDD_IDX", "FLUC_RT", "OPNPRC_IDX",
    "HGPRC_IDX", "LWPRC_IDX", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP",
}
INDEX_REQUIRED = {"IDX_NM", "CLSPRC_IDX"}
UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SECRET_KEY_PATTERN = re.compile(
    r"(?:access[_-]?token|refresh[_-]?token|(?:^|[_-])token(?:$|[_-])|password|passwd|secret|authorization|cookie|session|api[_-]?key)", re.I
)
PYKRX_FILTER = re.compile(r"[^-\w\.]")
SEOUL = ZoneInfo("Asia/Seoul")


class CaptureError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: object, code: str = "UTC_TIME_INVALID") -> dt.datetime:
    if not isinstance(value, str) or UTC_PATTERN.fullmatch(value) is None:
        raise CaptureError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CaptureError(code) from exc


def parse_evidence_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise CaptureError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise CaptureError(code)
    return parsed.astimezone(dt.timezone.utc)


def format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise CaptureError("RESPONSE_JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _json_object(raw: bytes, code: str) -> dict:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(CaptureError("RESPONSE_JSON_NONFINITE")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError(code) from exc
    if not isinstance(value, dict):
        raise CaptureError(code)
    return value


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(SECRET_KEY_PATTERN.search(str(key)) or _contains_secret_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _body_params(body: object) -> dict[str, str]:
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    if not isinstance(body, str):
        raise CaptureError("REQUEST_BODY_INVALID")
    parsed = parse_qs(body, keep_blank_values=True, strict_parsing=True)
    if any(len(values) != 1 for values in parsed.values()):
        raise CaptureError("REQUEST_FIELD_DUPLICATE")
    result = {key: values[0] for key, values in parsed.items()}
    if set(result) - PUBLIC_FIELDS:
        raise CaptureError("REQUEST_SECRET_OR_UNKNOWN_FIELD")
    return result


def classify_public_request(method: str, url: str, body: object) -> tuple[str, dict]:
    if method != "POST" or url != ENDPOINT:
        raise CaptureError("REQUEST_ENDPOINT_INVALID")
    params = _body_params(body)
    builder = params.get("bld")
    if builder not in BUILDERS:
        raise CaptureError("REQUEST_BUILDER_NOT_ALLOWED")
    family, market_field, markets = BUILDERS[builder]
    if set(params) != {"bld", "trdDd", market_field}:
        raise CaptureError("REQUEST_PUBLIC_FIELDS_INVALID")
    date = params["trdDd"]
    market = markets.get(params[market_field])
    try:
        parsed_date = dt.datetime.strptime(date, "%Y%m%d").date()
    except ValueError as exc:
        raise CaptureError("REQUEST_IDENTITY_INVALID") from exc
    if parsed_date.strftime("%Y%m%d") != date or market is None:
        raise CaptureError("REQUEST_IDENTITY_INVALID")
    key = f"{date}:{market}:{family}"
    return key, {name: params[name] for name in sorted(params)}


def _normalize_pykrx(value: object) -> str:
    rendered = PYKRX_FILTER.sub("", str(value))
    return "0" if rendered in {"", "-"} else rendered


def _decimal(value: object, code: str) -> str:
    try:
        number = Decimal(_normalize_pykrx(value))
    except InvalidOperation as exc:
        raise CaptureError(code) from exc
    if not number.is_finite():
        raise CaptureError(code)
    normalized = number.normalize()
    return format(normalized, "f") if normalized != 0 else "0"


def _validate_provider_payload(raw: bytes, family: str) -> tuple[dict, list[dict]]:
    payload = _json_object(raw, "RESPONSE_JSON_INVALID")
    if _contains_secret_key(payload):
        raise CaptureError("RESPONSE_SECRET_FIELD_REJECTED")
    block = "OutBlock_1" if family == "stock" else "output"
    allowed_top = {block, "CURRENT_DATETIME"}
    if (
        block not in payload
        or not set(payload) <= allowed_top
        or not isinstance(payload[block], list)
        or not payload[block]
        or (
            "CURRENT_DATETIME" in payload
            and not isinstance(payload["CURRENT_DATETIME"], str)
        )
    ):
        fields = ",".join(sorted(str(key) for key in payload))
        raise CaptureError(f"RESPONSE_SCHEMA_INVALID:{family}:FIELDS={fields}")
    allowed = STOCK_FIELDS if family == "stock" else INDEX_FIELDS
    required = STOCK_REQUIRED if family == "stock" else INDEX_REQUIRED
    for row in payload[block]:
        if not isinstance(row, dict) or not required <= set(row) <= allowed:
            fields = (
                ",".join(sorted(str(key) for key in row))
                if isinstance(row, dict)
                else type(row).__name__
            )
            raise CaptureError(
                f"RESPONSE_ROW_SCHEMA_INVALID:{family}:FIELDS={fields}"
            )
        if any(not isinstance(value, str) for value in row.values()):
            raise CaptureError(f"RESPONSE_ROW_VALUE_INVALID:{family}")
    return payload, payload[block]


def _raw_projection(raw: bytes, family: str) -> dict:
    _, rows = _validate_provider_payload(raw, family)
    result = {}
    for row in rows:
        if family == "stock":
            identity = _normalize_pykrx(row["ISU_SRT_CD"]).zfill(6)
            values = {
                "close": _decimal(row["TDD_CLSPRC"], "RAW_NUMBER_INVALID"),
                "market_cap": _decimal(row["MKTCAP"], "RAW_NUMBER_INVALID"),
                "return_pct": _decimal(row["FLUC_RT"], "RAW_NUMBER_INVALID"),
                "trading_value": _decimal(row["ACC_TRDVAL"], "RAW_NUMBER_INVALID"),
            }
        else:
            identity = _normalize_pykrx(row["IDX_NM"])
            values = {"close": _decimal(row["CLSPRC_IDX"], "RAW_NUMBER_INVALID")}
        if not identity or identity == "0" or identity in result:
            raise CaptureError(f"RAW_IDENTITY_INVALID:{family}")
        result[identity] = values
    return result


def normalize_projection(value: dict) -> dict:
    if not isinstance(value, dict) or not value:
        raise CaptureError("NORMALIZED_PROJECTION_INVALID")
    result = {}
    for identity, fields in value.items():
        if not isinstance(identity, str) or not identity or not isinstance(fields, dict):
            raise CaptureError("NORMALIZED_PROJECTION_INVALID")
        result[identity] = {name: _decimal(item, "NORMALIZED_NUMBER_INVALID") for name, item in sorted(fields.items())}
    return result


def unknown_status(reason: str) -> dict:
    return {
        "status": "UNKNOWN_NO_OVERWRITE", "reason": reason,
        "authority": {
            "paper_reference_display_authorized": False, "runtime_regime_authorized": False,
            "trading_authorized": False, "order_authorized": False,
            "capital_authorized": False, "real_authorized": False,
        },
    }


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise CaptureError("NO_OVERWRITE") from exc


def require_claimed_start(value: str, actual_start: str) -> None:
    claimed = parse_utc(value, "CLAIMED_START_INVALID")
    actual = parse_utc(actual_start, "CAPTURE_START_INVALID")
    if claimed > actual or actual - claimed > dt.timedelta(seconds=30):
        raise CaptureError("CLAIMED_START_ORDER_INVALID")


def require_completed_session_pair(previous_date: str, current_date: str, contract_path: Path, decision_at: str) -> dict:
    """Resolve the last two completed sessions from pinned official KRX holiday bytes."""
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    calendar = contract.get("session_calendar", {})
    evidence_path = Path(contract_path).resolve().parents[1] / calendar.get("path", "")
    raw = evidence_path.read_bytes()
    if sha256_bytes(raw) != calendar.get("sha256"):
        raise CaptureError("CALENDAR_EVIDENCE_HASH_INVALID")
    captured = _json_object(raw, "CALENDAR_EVIDENCE_JSON_INVALID")
    if captured.get("schema_version") != "krx_official_holiday_capture/1" or captured.get("provider_id") != "KRX_GLOBAL_MARKET_CLOSING_HOLIDAY_01023":
        raise CaptureError("CALENDAR_EVIDENCE_IDENTITY_INVALID")
    response = captured.get("response")
    if not isinstance(response, dict):
        raise CaptureError("CALENDAR_EVIDENCE_RESPONSE_INVALID")
    try:
        provider_raw = base64.b64decode(response.get("raw_base64", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise CaptureError("CALENDAR_EVIDENCE_RESPONSE_INVALID") from exc
    if sha256_bytes(provider_raw) != response.get("raw_sha256"):
        raise CaptureError("CALENDAR_PROVIDER_HASH_INVALID")
    decision_utc = parse_utc(decision_at, "CALENDAR_DECISION_TIME_INVALID")
    started = parse_evidence_utc(
        captured.get("capture_started_at"), "CALENDAR_EVIDENCE_TIME_INVALID"
    )
    received = parse_evidence_utc(
        captured.get("response_received_at"), "CALENDAR_EVIDENCE_TIME_INVALID"
    )
    if started > received or received > decision_utc:
        raise CaptureError("CALENDAR_EVIDENCE_TIME_ORDER_INVALID")
    year = captured.get("year")
    if type(year) is not int or year != decision_utc.astimezone(SEOUL).year:
        raise CaptureError("CALENDAR_EVIDENCE_YEAR_INVALID")
    provider = _json_object(provider_raw, "CALENDAR_PROVIDER_JSON_INVALID")
    if set(provider) != {"block1"} or not isinstance(provider["block1"], list):
        raise CaptureError("CALENDAR_PROVIDER_SCHEMA_INVALID")
    closed = set()
    for row in provider["block1"]:
        if not isinstance(row, dict) or not isinstance(row.get("calnd_dd"), str):
            raise CaptureError("CALENDAR_PROVIDER_ROW_INVALID")
        day = dt.date.fromisoformat(row["calnd_dd"])
        if day.year != year:
            raise CaptureError("CALENDAR_PROVIDER_YEAR_INVALID")
        closed.add(day)
    decision = decision_utc.astimezone(SEOUL)

    completed = completed_session_pair(decision, year, closed)
    expected = tuple(day.strftime("%Y%m%d") for day in reversed(completed))
    if (previous_date, current_date) != expected:
        raise CaptureError(f"STALE_SESSION:expected={expected[0]},{expected[1]}:observed={previous_date},{current_date}")
    return {
        "provider_id": captured["provider_id"], "evidence_path": calendar["path"],
        "evidence_sha256": calendar["sha256"], "previous_completed_session": previous_date,
        "latest_completed_session": current_date, "decision_at_utc": format_utc(decision),
    }


def completed_session_pair(
    decision: dt.datetime, evidence_year: int, closed: set[dt.date]
) -> list[dt.date]:
    """Find two sessions without inferring any date outside evidence_year."""
    completed = []
    cursor = decision.date()
    while cursor.year == evidence_year:
        if cursor.weekday() < 5 and cursor not in closed:
            close = dt.datetime.combine(cursor, dt.time(15, 30), SEOUL)
            if decision >= close:
                completed.append(cursor)
                if len(completed) == 2:
                    return completed
        cursor -= dt.timedelta(days=1)
    raise CaptureError("CALENDAR_EVIDENCE_RANGE_INSUFFICIENT")


class SourceCapture:
    def __init__(self, root: Path, dates: tuple[str, str], *, clock=None):
        self.root = Path(root)
        self.dates = dates
        self.clock = clock or utc_now
        self.records: dict[str, dict] = {}
        self.reserved: set[str] = set()
        self.capture_started_at_utc = format_utc(parse_utc(self.clock(), "CAPTURE_START_INVALID"))
        self._last_received = parse_utc(self.capture_started_at_utc)

    @property
    def expected_keys(self) -> set[str]:
        return {f"{date}:{market}:{family}" for date in self.dates for market in ("KOSPI", "KOSDAQ") for family in ("stock", "index")}

    def reserve_request(self, request) -> tuple[str, dict]:
        key, public_params = classify_public_request(request.method, request.url, request.body)
        if key not in self.expected_keys:
            raise CaptureError(f"UNEXPECTED_SESSION:{key}")
        if key in self.reserved or key in self.records:
            raise CaptureError(f"DUPLICATE_RESPONSE:{key}")
        if len(self.reserved) >= len(self.expected_keys):
            raise CaptureError("REQUEST_BUDGET_EXCEEDED")
        self.reserved.add(key)
        return key, public_params

    def capture(self, request, response, *, received_at: str | None = None, reserved: tuple[str, dict] | None = None) -> tuple[str, bytes]:
        key, public_params = reserved or self.reserve_request(request)
        now = parse_utc(self.clock(), "CAPTURE_CLOCK_INVALID")
        received = parse_utc(received_at, "RECEIVED_AT_INVALID") if received_at is not None else now
        request_day = dt.datetime.strptime(key.split(":", 1)[0], "%Y%m%d").date()
        if received > now or received < self._last_received:
            raise CaptureError(f"RECEIVED_AT_ORDER_INVALID:{key}")
        if received.astimezone(SEOUL).date() < request_day:
            raise CaptureError(f"RECEIVED_BEFORE_SESSION:{key}")
        if getattr(response, "status_code", 200) != 200:
            raise CaptureError(f"RESPONSE_HTTP_INVALID:{key}")
        content_type = str(response.headers.get("Content-Type", ""))
        if content_type.split(";", 1)[0].strip().lower() not in {
            "application/json",
            "text/html",
        }:
            raise CaptureError(f"RESPONSE_CONTENT_TYPE_INVALID:{key}")
        raw = bytes(response.content)
        if not raw:
            raise CaptureError(f"EMPTY_RESPONSE:{key}")
        family = key.rsplit(":", 1)[1]
        _validate_provider_payload(raw, family)
        name = key.replace(":", "-") + ".json"
        path = self.root / "responses" / name
        write_new(path, raw)
        stored = path.read_bytes()
        if stored != raw or sha256_bytes(stored) != sha256_bytes(raw):
            raise CaptureError(f"STORED_RESPONSE_WRITE_INVALID:{key}")
        _validate_provider_payload(stored, family)
        self._last_received = received
        self.records[key] = {
            "key": key,
            "request": {"method": "POST", "url": ENDPOINT, "public_params": public_params},
            "response": {
                "path": f"responses/{name}", "bytes": len(stored), "sha256": sha256_bytes(stored),
                "content_type": content_type, "received_at_utc": format_utc(received),
                "provider_published_at": None,
            },
        }
        return key, stored

    def bind_parser_input(self, key: str, raw: bytes) -> None:
        record = self.records.get(key)
        if record is None:
            raise CaptureError(f"PARSER_INPUT_WITHOUT_RESPONSE:{key}")
        stored = (self.root / record["response"]["path"]).read_bytes()
        if raw != stored:
            raise CaptureError(f"PARSER_INPUT_NOT_STORED_BYTES:{key}")
        record["response"]["parser_input_sha256"] = sha256_bytes(raw)

    def bind_normalized_frame(self, key: str, frame_sha256: str, projection: dict) -> None:
        record = self.records.get(key)
        if record is None or record["response"].get("parser_input_sha256") != record["response"]["sha256"]:
            raise CaptureError(f"NORMALIZED_FRAME_WITHOUT_PARSER_INPUT:{key}")
        if SHA256_PATTERN.fullmatch(frame_sha256) is None:
            raise CaptureError(f"NORMALIZED_FRAME_HASH_INVALID:{key}")
        family = key.rsplit(":", 1)[1]
        stored = (self.root / record["response"]["path"]).read_bytes()
        raw_projection = _raw_projection(stored, family)
        calculated = normalize_projection(projection)
        if raw_projection != calculated:
            raise CaptureError(f"RAW_FRAME_MISMATCH:{key}")
        record["normalized_frame"] = {
            "schema": f"pykrx_1_2_8_{family}_required_projection/1", "sha256": frame_sha256,
            "required_projection_sha256": sha256_bytes(canonical_bytes(calculated)),
            "raw_to_frame_equivalent": True,
        }

    def finalize(self) -> dict:
        missing = sorted(self.expected_keys - set(self.records))
        unexpected = sorted(set(self.records) - self.expected_keys)
        if missing:
            raise CaptureError("MISSING_RESPONSES:" + ",".join(missing))
        if unexpected:
            raise CaptureError("UNEXPECTED_RESPONSES:" + ",".join(unexpected))
        for key, record in sorted(self.records.items()):
            raw = (self.root / record["response"]["path"]).read_bytes()
            response = record["response"]
            if len(raw) != response["bytes"] or sha256_bytes(raw) != response["sha256"]:
                raise CaptureError(f"STORED_RESPONSE_HASH_INVALID:{key}")
            _validate_provider_payload(raw, key.rsplit(":", 1)[1])
            if response.get("parser_input_sha256") != response["sha256"]:
                raise CaptureError(f"PARSER_INPUT_BINDING_MISSING:{key}")
            binding = record.get("normalized_frame")
            if not isinstance(binding, dict) or binding.get("raw_to_frame_equivalent") is not True:
                raise CaptureError(f"NORMALIZED_FRAME_BINDING_MISSING:{key}")
        completed = parse_utc(self.clock(), "CAPTURE_COMPLETE_INVALID")
        if completed < self._last_received:
            raise CaptureError("CAPTURE_COMPLETE_ORDER_INVALID")
        manifest = {
            "schema": "krx_information_system_source_capture/2", "status": "COMPLETE",
            "dates": list(self.dates), "capture_started_at_utc": self.capture_started_at_utc,
            "capture_completed_at_utc": format_utc(completed), "original_response_bytes_retained": True,
            "response_body_schema_allowlisted_before_retention": True,
            "request_headers_retained": False, "cookies_retained": False,
            "credentials_retained": False, "provider_published_at_is_received_at": False,
            "records": [self.records[key] for key in sorted(self.records)],
        }
        manifest["payload_sha256"] = sha256_bytes(canonical_bytes(manifest))
        write_new(self.root / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n")
        return manifest


@contextmanager
def capture_requests(capture: SourceCapture):
    """Reserve every exact public KRX request before dispatch and parse stored bytes."""
    import requests
    original = requests.Session.send

    def send(session, request, **kwargs):
        reserved = capture.reserve_request(request)
        response = original(session, request, **kwargs)
        key, stored = capture.capture(request, response, reserved=reserved)
        response._content = stored
        response._content_consumed = True
        capture.bind_parser_input(key, bytes(response.content))
        return response

    requests.Session.send = send
    try:
        yield capture
    finally:
        requests.Session.send = original
