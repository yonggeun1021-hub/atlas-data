"""Fail-closed source-byte capture for the KRX information-system candidate."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs


ENDPOINT = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
BUILDERS = {
    "dbms/MDC/STAT/standard/MDCSTAT01501": ("stock", "mktId", {"STK": "KOSPI", "KSQ": "KOSDAQ"}),
    "dbms/MDC/STAT/standard/MDCSTAT00101": ("index", "idxIndMidclssCd", {"02": "KOSPI", "03": "KOSDAQ"}),
}
PUBLIC_FIELDS = {"bld", "trdDd", "mktId", "idxIndMidclssCd"}


class CaptureError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


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
    if method != "POST" or url.split("?", 1)[0] != ENDPOINT:
        raise CaptureError("REQUEST_ENDPOINT_INVALID")
    params = _body_params(body)
    builder = params.get("bld")
    if builder not in BUILDERS:
        raise CaptureError("REQUEST_BUILDER_NOT_ALLOWED")
    family, market_field, markets = BUILDERS[builder]
    expected_fields = {"bld", "trdDd", market_field}
    if set(params) != expected_fields:
        raise CaptureError("REQUEST_PUBLIC_FIELDS_INVALID")
    date = params["trdDd"]
    market = markets.get(params[market_field])
    if len(date) != 8 or not date.isdigit() or market is None:
        raise CaptureError("REQUEST_IDENTITY_INVALID")
    key = f"{date}:{market}:{family}"
    return key, {key: params[key] for key in sorted(params)}


def unknown_status(reason: str) -> dict:
    return {
        "status": "UNKNOWN_NO_OVERWRITE",
        "reason": reason,
        "authority": {
            "paper_reference_display_authorized": False,
            "runtime_regime_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
            "capital_authorized": False,
            "real_authorized": False,
        },
    }


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise CaptureError("NO_OVERWRITE") from exc


class SourceCapture:
    def __init__(self, root: Path, dates: tuple[str, str]):
        self.root = Path(root)
        self.dates = dates
        self.records: dict[str, dict] = {}

    @property
    def expected_keys(self) -> set[str]:
        return {
            f"{date}:{market}:{family}"
            for date in self.dates
            for market in ("KOSPI", "KOSDAQ")
            for family in ("stock", "index")
        }

    def capture(self, request, response, *, received_at: str | None = None) -> None:
        key, public_params = classify_public_request(request.method, request.url, request.body)
        if key not in self.expected_keys:
            raise CaptureError(f"UNEXPECTED_SESSION:{key}")
        if key in self.records:
            raise CaptureError(f"DUPLICATE_RESPONSE:{key}")
        raw = bytes(response.content)
        if not raw:
            raise CaptureError(f"EMPTY_RESPONSE:{key}")
        try:
            json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CaptureError(f"RESPONSE_JSON_INVALID:{key}") from exc
        name = key.replace(":", "-") + ".json"
        write_new(self.root / "responses" / name, raw)
        self.records[key] = {
            "key": key,
            "request": {"method": "POST", "url": ENDPOINT, "public_params": public_params},
            "response": {
                "path": f"responses/{name}",
                "bytes": len(raw),
                "sha256": sha256_bytes(raw),
                "content_type": str(response.headers.get("Content-Type", "")),
                "received_at_utc": received_at or utc_now(),
                "provider_published_at": None,
            },
        }

    def finalize(self) -> dict:
        missing = sorted(self.expected_keys - set(self.records))
        unexpected = sorted(set(self.records) - self.expected_keys)
        if missing:
            raise CaptureError("MISSING_RESPONSES:" + ",".join(missing))
        if unexpected:
            raise CaptureError("UNEXPECTED_RESPONSES:" + ",".join(unexpected))
        manifest = {
            "schema": "krx_information_system_source_capture/1",
            "status": "COMPLETE",
            "dates": list(self.dates),
            "original_response_bytes_retained": True,
            "request_headers_retained": False,
            "cookies_retained": False,
            "credentials_retained": False,
            "provider_published_at_is_received_at": False,
            "records": [self.records[key] for key in sorted(self.records)],
        }
        manifest["payload_sha256"] = sha256_bytes(canonical_bytes(manifest))
        write_new(self.root / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n")
        return manifest


def require_current_session(current_date: str, expected_current_date: str) -> None:
    if current_date != expected_current_date:
        raise CaptureError(f"STALE_SESSION:expected={expected_current_date}:observed={current_date}")


def _is_candidate_request(request) -> bool:
    if request.method != "POST" or request.url.split("?", 1)[0] != ENDPOINT:
        return False
    try:
        params = _body_params(request.body)
    except CaptureError:
        return False
    return params.get("bld") in BUILDERS


@contextmanager
def capture_requests(capture: SourceCapture):
    """Intercept only the two allowlisted public KRX response families."""
    import requests

    original = requests.Session.send

    def send(session, request, **kwargs):
        response = original(session, request, **kwargs)
        if _is_candidate_request(request):
            capture.capture(request, response)
        return response

    requests.Session.send = send
    try:
        yield capture
    finally:
        requests.Session.send = original
