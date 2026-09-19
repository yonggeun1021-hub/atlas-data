#!/usr/bin/env python3
"""P4-07 capture: a complete, append-only Upbit REST microstructure snapshot.

Public quotation endpoints only, no API key/secret, never an order/
withdrawal/private endpoint:

* ``GET /v1/candles/minutes/{15,60,240}`` and ``GET /v1/candles/days`` --
  one call per market per timeframe (Upbit's candle endpoints are
  per-market, unlike ticker/orderbook).
* ``GET /v1/trades/ticks``                     -- recent public trade ticks,
  one call per market.
* ``GET /v1/orderbook?markets=...``            -- one batched call across
  every captured market.

Only markets in the exact-hash, effective-dated P3-12 consumer lineage are
captured here.  The consumer rejects a hash mismatch, unratified policy,
historical identity backfill, duplicate market, or partial cohort before the
first provider call.  ``IDENTITY_UNRATIFIED`` rows are never reinterpreted
with a newer registry.

Every fetch is retried with exponential backoff on transient HTTP failure
(``fetch_with_retry``, independently testable); a fetch that still fails
after the configured max attempts fails the whole capture closed -- it never
silently drops a market's evidence.

The collector writes into a temporary directory, builds and validates a
manifest, and moves the snapshot into evidence only after every candidate
market has been captured successfully -- append-only, never overwritten.
"""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Callable, Optional
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from microstructure import upbit_p3_p4_bridge as P3_P4  # noqa: E402
from universe import upbit_tradeable_universe as UNIVERSE  # noqa: E402

CONTRACT_PATH = ROOT / "config" / "upbit_market_evidence_contract.json"
UTC = dt.timezone.utc
USER_AGENT = "Project-Atlas-upbit-microstructure-capture/1.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# v2 (2026-09-14): downloaded_at_utc is the completion instant rounded UP to
# the whole second (see ceil_to_utc_second); v1 truncated it.  Layout is
# unchanged and v1 snapshots keep validating and re-deriving byte-for-byte.
# v3 (2026-09-14): the manifest records every candle fetch's own request/
# response instant (``candle_fetch_times``) so P4-07 judges finalization
# against when each candle list was requested, never against the capture's
# completion time (finalization lookahead). v1/v2 manifests carry no such
# field and keep validating and re-deriving byte-for-byte.
CAPTURE_VERSION = "upbit-microstructure-capture/v3"
LEGACY_CAPTURE_VERSIONS = ("upbit-microstructure-capture/v1", "upbit-microstructure-capture/v2")
CANDLE_FETCH_TIMES_FIELD = "candle_fetch_times"
FETCH_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
MAX_MARKETS_PER_BATCH_CALL = 400
SNAPSHOT_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-p3-[0-9a-f]{16}$")


class CaptureError(RuntimeError):
    """Fail-closed P4-07 capture or publication error."""


def fail(code: str, detail: str) -> None:
    raise CaptureError(f"{code}: {detail}")


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def ceil_to_utc_second(value: dt.datetime) -> dt.datetime:
    """Round an aware instant UP to the next whole UTC second.

    ``downloaded_at_utc`` is serialized at whole-second precision, but Upbit
    stamps orderbook rows with millisecond ``timestamp`` values. Truncating
    the completion instant (``iso_utc``'s ``timespec="seconds"``) produced a
    recorded ``downloaded_at_utc`` up to 999 ms *earlier* than orderbook rows
    that were already in hand, which P4-07's builder correctly rejects as an
    impossible ordering (``ORDERBOOK_UNKNOWN``) -- every liquid market whose
    book updated inside the final wall-clock second of the capture. Rounding
    the completion instant up keeps it a true upper bound on every response
    it covers; the start instant stays truncated (a true lower bound).
    """
    value = value.astimezone(UTC)
    if value.microsecond:
        value = value.replace(microsecond=0) + dt.timedelta(seconds=1)
    return value


def iso_utc_ms_floor(value: dt.datetime) -> str:
    """Millisecond ISO-8601 UTC, rounded DOWN (a lower bound)."""
    value = value.astimezone(UTC)
    value = value.replace(microsecond=value.microsecond // 1000 * 1000)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_utc_ms_ceil(value: dt.datetime) -> str:
    """Millisecond ISO-8601 UTC, rounded UP (an upper bound)."""
    value = value.astimezone(UTC)
    if value.microsecond % 1000:
        value = value.replace(microsecond=value.microsecond // 1000 * 1000) + dt.timedelta(milliseconds=1)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_fetch_time(value) -> dt.datetime:
    if not isinstance(value, str) or FETCH_TIME_RE.fullmatch(value) is None:
        fail("MANIFEST_CANDLE_FETCH_TIME_INVALID", repr(value))
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _parse_manifest_second(value, label: str) -> dt.datetime:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        fail("MANIFEST_TIMESTAMP_INVALID", f"{label}={value!r}")


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_READ_FAILED", f"{path}: {exc}")
    if contract.get("auth_required") is not False or contract.get("order_or_withdrawal_endpoints_called") is not False:
        fail("CONTRACT_SAFETY_INVARIANT_VIOLATED", "auth_required/order_or_withdrawal_endpoints_called must both be false")
    for key in (
        "timeframes", "candles_minutes_endpoint_template", "candles_days_endpoint_template",
        "trades_endpoint_template", "orderbook_endpoint_template", "candle_lookback_count_by_timeframe",
    ):
        if key not in contract:
            fail("CONTRACT_FIELD_MISSING", key)
    return contract


def public_get(url: str, timeout_seconds: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read()
    if not raw:
        fail("EMPTY_RESPONSE", url)
    return raw


def fetch_with_retry(
    url: str,
    *,
    fetcher: Callable[[str, int], bytes],
    timeout_seconds: int = 60,
    max_attempts: int = 4,
    backoff_base_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> bytes:
    """Reconnect/retry-with-backoff on a transient HTTP failure. REST has no
    persistent connection to "reconnect", so this is the REST analogue --
    exponential backoff (``backoff_base_seconds * 2**(attempt-1)``) up to
    ``max_attempts``. Either eventually returns the fetched bytes, or fails
    closed (raises) after the last attempt -- never silently drops the
    market/timeframe's evidence and never returns partial/empty bytes as if
    they were a real response.
    """
    if max_attempts < 1:
        fail("RETRY_POLICY_INVALID", str(max_attempts))
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fetcher(url, timeout_seconds)
        except (urllib.error.URLError, TimeoutError, OSError, CaptureError) as exc:
            last_error = exc
            if attempt < max_attempts:
                sleeper(backoff_base_seconds * (2 ** (attempt - 1)))
    fail("FETCH_FAILED_MAX_RETRIES", f"{url}: attempts={max_attempts} last_error={last_error}")


def parse_json_array(raw: bytes, label: str) -> list:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail("SOURCE_JSON_INVALID", f"{label}: {exc}")
    if not isinstance(payload, list):
        fail("SOURCE_JSON_INVALID", f"{label}: root not a list")
    return payload


def load_target_markets(universe_packet_path: Optional[Path]) -> list[str]:
    """Markets at TRADEABLE_UNIVERSE or PAPER_ELIGIBLE in the given P3-12
    classification packet. Returns an empty list (not an error) if no
    packet path is given, the file does not exist, or no market currently
    qualifies -- an empty capture is a normal, expected outcome while P3-12's
    policy/taxonomy/identity remain unratified.
    """
    if universe_packet_path is None or not Path(universe_packet_path).exists():
        return []
    try:
        record = json.loads(Path(universe_packet_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("UNIVERSE_PACKET_UNREADABLE", f"{universe_packet_path}: {exc}")
    packet = record.get("packet", record)
    markets = packet.get("markets", [])
    eligible = {"TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE"}
    return sorted(row["market"] for row in markets if row.get("state") in eligible)


def load_universe_lineage(
    universe_packet_path: Path,
    *,
    expected_record_sha256: str | None = None,
    transition_manifest_path: Path | None = None,
) -> dict:
    """Production P3->P4 bridge: exact hash + effective-time validation."""
    try:
        lineage = P3_P4.consume_universe_record(
            universe_packet_path,
            expected_record_sha256=expected_record_sha256,
        )
    except P3_P4.BridgeError as exc:
        fail("UNIVERSE_CONSUMER_REJECTED", str(exc))
    if transition_manifest_path is not None:
        try:
            transition = UNIVERSE.validate_same_vintage_transition(transition_manifest_path)
        except UNIVERSE.UpbitUniverseError as exc:
            fail("UNIVERSE_TRANSITION_REJECTED", str(exc))
        if (
            transition["path"].resolve() != Path(universe_packet_path).resolve()
            or transition["record"]["payload_sha256"] != lineage["record_payload_sha256"]
        ):
            fail("UNIVERSE_TRANSITION_SUCCESSOR_MISMATCH", str(universe_packet_path))
        manifest = transition["transition_manifest"]
        lineage["transition"] = {
            "manifest_path": str(transition_manifest_path),
            "manifest_file_sha256": transition["transition_manifest_file_sha256"],
            "manifest_payload_sha256": transition["transition_manifest_payload_sha256"],
            "canonical_source": copy.deepcopy(manifest["source_record"]),
            "successor": copy.deepcopy(manifest["successor_record"]),
        }
    elif "/transitions/" in Path(universe_packet_path).as_posix():
        fail("UNIVERSE_TRANSITION_MANIFEST_REQUIRED", str(universe_packet_path))
    return lineage


def write_raw(snapshot: Path, relative_gz: str, raw: bytes) -> str:
    target = snapshot / relative_gz
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
            stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def capture_snapshot(
    snapshot_root: Path,
    *,
    markets: list[str],
    snapshot_date: Optional[dt.date] = None,
    request_interval_seconds: float = 1.05,
    timeout_seconds: int = 60,
    contract: Optional[dict] = None,
    fetcher: Optional[Callable[[str, int], bytes]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], dt.datetime] = utc_now,
    snapshot_key: str | None = None,
    universe_lineage: dict | None = None,
) -> Path:
    contract = contract or load_contract()
    observed_start = clock().astimezone(UTC)
    vintage = snapshot_date or observed_start.date()
    if observed_start.date() != vintage:
        fail("CAPTURE_DATE_MISMATCH", f"clock={observed_start.date()} requested={vintage}")
    if request_interval_seconds < 1:
        fail("RATE_LIMIT_POLICY_INVALID", str(request_interval_seconds))
    key = snapshot_key or vintage.isoformat()
    if snapshot_key is not None and SNAPSHOT_KEY_RE.fullmatch(snapshot_key) is None:
        fail("SNAPSHOT_KEY_INVALID", snapshot_key)
    if universe_lineage is not None:
        expected_key = P3_P4.snapshot_key(universe_lineage)
        if key != expected_key:
            fail("SNAPSHOT_KEY_LINEAGE_MISMATCH", f"expected={expected_key} actual={key}")
        if sorted(markets) != universe_lineage.get("markets"):
            fail("PARTIAL_UNIVERSE_REJECTED", "capture markets differ from exact P3 cohort")
    target = Path(snapshot_root) / key
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    fetch = fetcher or public_get
    max_attempts = contract.get("retry_max_attempts", 4)
    backoff_base = contract.get("retry_backoff_base_seconds", 2.0)

    def robust_fetch(url: str) -> bytes:
        return fetch_with_retry(
            url, fetcher=fetch, timeout_seconds=timeout_seconds,
            max_attempts=max_attempts, backoff_base_seconds=backoff_base, sleeper=sleeper,
        )

    def timed_robust_fetch(url: str) -> tuple[bytes, dict]:
        """Fetch with retry and record the successful attempt's own request
        instant (a lower bound on when the provider served the rows) and the
        instant the response was in hand (an upper bound on what it can
        describe). P4-07 judges candle finalization against the former."""
        attempt_started: list[dt.datetime] = []

        def timed(url_: str, timeout_: int) -> bytes:
            attempt_started.append(clock().astimezone(UTC))
            return fetch(url_, timeout_)

        raw = fetch_with_retry(
            url, fetcher=timed, timeout_seconds=timeout_seconds,
            max_attempts=max_attempts, backoff_base_seconds=backoff_base, sleeper=sleeper,
        )
        received = clock().astimezone(UTC)
        requested = attempt_started[-1]
        if requested < observed_start or received < requested:
            fail("CAPTURE_CLOCK_REVERSED", f"start={observed_start} requested={requested} received={received}")
        return raw, {
            "request_started_at_utc": iso_utc_ms_floor(requested),
            "response_received_at_utc": iso_utc_ms_ceil(received),
        }

    markets = sorted(set(markets))
    temporary_parent = Path(tempfile.mkdtemp(prefix="upbit-microstructure-", dir=str(target.parent)))
    snapshot = temporary_parent / key
    snapshot.mkdir()
    try:
        checksums: dict[str, str] = {}
        candle_fetch_times: dict[str, dict[str, dict]] = {}

        for timeframe in contract["timeframes"]:
            count = contract["candle_lookback_count_by_timeframe"][timeframe]
            unit = contract.get("candle_upbit_unit_by_timeframe", {}).get(timeframe)
            records = []
            fetch_times_for_timeframe = candle_fetch_times.setdefault(timeframe, {})
            for index, market in enumerate(markets):
                if unit is not None:
                    url = contract["candles_minutes_endpoint_template"].format(UNIT=unit, MARKET=market, COUNT=count)
                else:
                    url = contract["candles_days_endpoint_template"].format(MARKET=market, COUNT=count)
                raw, fetch_times_for_timeframe[market] = timed_robust_fetch(url)
                parse_json_array(raw, f"{market}:{timeframe}")
                records.append({
                    "market": market,
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "body_b64": base64.b64encode(raw).decode("ascii"),
                })
                if index + 1 < len(markets):
                    sleeper(request_interval_seconds)
            bundle_raw = b"".join(
                json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
                for record in sorted(records, key=lambda row: row["market"])
            )
            file_name = contract["candles_raw_file_template"].format(TIMEFRAME=timeframe)
            checksums[file_name] = write_raw(snapshot, file_name, bundle_raw)

        trade_records = []
        for index, market in enumerate(markets):
            url = contract["trades_endpoint_template"].format(MARKET=market, COUNT=contract["trades_lookback_count"])
            raw = robust_fetch(url)
            parse_json_array(raw, f"{market}:trades")
            trade_records.append({
                "market": market,
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "body_b64": base64.b64encode(raw).decode("ascii"),
            })
            if index + 1 < len(markets):
                sleeper(request_interval_seconds)
        trades_bundle_raw = b"".join(
            json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            for record in sorted(trade_records, key=lambda row: row["market"])
        )
        checksums[contract["trades_raw_file"]] = write_raw(snapshot, contract["trades_raw_file"], trades_bundle_raw)

        orderbook_payload: list = []
        if markets:
            orderbook_chunks = []
            for chunk_start in range(0, len(markets), MAX_MARKETS_PER_BATCH_CALL):
                chunk = markets[chunk_start:chunk_start + MAX_MARKETS_PER_BATCH_CALL]
                encoded = urllib.parse.quote(",".join(chunk), safe=",")
                url = contract["orderbook_endpoint_template"].format(MARKETS=encoded)
                raw = robust_fetch(url)
                orderbook_chunks.append(parse_json_array(raw, "orderbook"))
            orderbook_payload = [row for chunk in orderbook_chunks for row in chunk]
        orderbook_raw = json.dumps(orderbook_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        checksums[contract["orderbook_raw_file"]] = write_raw(snapshot, contract["orderbook_raw_file"], orderbook_raw)

        completed_at = clock().astimezone(UTC)
        if completed_at < observed_start:
            fail("CAPTURE_CLOCK_REVERSED", f"start={observed_start} completed={completed_at}")
        # Whole-second upper bound on the instant every response above was in
        # hand -- see ceil_to_utc_second.
        completed_at = ceil_to_utc_second(completed_at)
        (snapshot / "_downloaded_at.txt").write_text(iso_utc(completed_at) + "\n", encoding="utf-8")
        (snapshot / "_sha256.txt").write_text(
            "".join(f"{checksums[name]}  {name}\n" for name in sorted(checksums)), encoding="utf-8",
        )
        manifest = {
            "schema_version": 1,
            "capture_version": CAPTURE_VERSION,
            "transform_version": contract["transform_version"],
            "source_name": contract["source_name"],
            "vintage_date": vintage.isoformat(),
            "capture_started_at_utc": iso_utc(observed_start),
            "downloaded_at_utc": iso_utc(completed_at),
            "market_count": len(markets),
            "markets": markets,
            "universe_lineage": universe_lineage,
            "timeframes": list(contract["timeframes"]),
            "checksums": checksums,
            CANDLE_FETCH_TIMES_FIELD: candle_fetch_times,
            "auth_required": False,
            "order_or_withdrawal_endpoints_called": False,
        }
        (snapshot / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        validate_snapshot(snapshot)
        snapshot.replace(target)
        shutil.rmtree(temporary_parent, ignore_errors=True)
        return target
    except Exception:
        shutil.rmtree(temporary_parent, ignore_errors=True)
        raise


def validate_snapshot(snapshot_dir: Path) -> dict:
    """Structural, hash-bound validation only -- semantic derivation into
    finalized-candle/spread/depth/slippage/freshness evidence is
    ``microstructure/upbit_market_evidence.py``'s job.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "_manifest.json"
    if not manifest_path.exists():
        fail("MANIFEST_MISSING", str(snapshot_dir))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("MANIFEST_UNREADABLE", str(exc))
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict) or not checksums:
        fail("MANIFEST_CHECKSUMS_INVALID", str(snapshot_dir))
    for relative_gz, expected_sha in checksums.items():
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            fail("MANIFEST_CHECKSUM_SHAPE_INVALID", relative_gz)
        target = snapshot_dir / relative_gz
        if not target.exists():
            fail("RAW_FILE_MISSING", relative_gz)
        with gzip.open(target, "rb") as handle:
            raw = handle.read()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha:
            fail("RAW_FILE_HASH_MISMATCH", f"{relative_gz}: expected={expected_sha} actual={actual}")
    markets = manifest.get("markets")
    if not isinstance(markets, list) or sorted(markets) != markets or len(set(markets)) != len(markets):
        fail("MANIFEST_MARKET_LIST_INVALID", str(snapshot_dir))
    if manifest.get("market_count") != len(markets):
        fail("MANIFEST_MARKET_COUNT_MISMATCH", str(snapshot_dir))
    lineage = manifest.get("universe_lineage")
    if lineage is not None:
        if not isinstance(lineage, dict):
            fail("MANIFEST_UNIVERSE_LINEAGE_INVALID", str(snapshot_dir))
        if lineage.get("markets") != markets or lineage.get("market_count") != len(markets):
            fail("MANIFEST_PARTIAL_UNIVERSE", str(snapshot_dir))
        if snapshot_dir.name != P3_P4.snapshot_key(lineage):
            fail("MANIFEST_SNAPSHOT_KEY_MISMATCH", str(snapshot_dir))
        authority = lineage.get("authority") or {}
        if not authority or any(value is True for key, value in authority.items() if key != "evidence_derivation_only"):
            fail("MANIFEST_UNIVERSE_AUTHORITY_VIOLATED", str(snapshot_dir))
        transition = lineage.get("transition")
        transition_selected = "/transitions/" in str(lineage.get("record_path", "")).replace("\\", "/")
        if transition_selected and not isinstance(transition, dict):
            fail("MANIFEST_UNIVERSE_TRANSITION_PROVENANCE_MISSING", str(snapshot_dir))
        if transition is not None:
            if not isinstance(transition, dict) or set(transition) != {
                "manifest_path", "manifest_file_sha256", "manifest_payload_sha256",
                "canonical_source", "successor",
            }:
                fail("MANIFEST_UNIVERSE_TRANSITION_PROVENANCE_INVALID", str(snapshot_dir))
            if any(
                SHA256_RE.fullmatch(str(transition.get(field, ""))) is None
                for field in ("manifest_file_sha256", "manifest_payload_sha256")
            ):
                fail("MANIFEST_UNIVERSE_TRANSITION_HASH_INVALID", str(snapshot_dir))
            for pin_label in ("canonical_source", "successor"):
                pin = transition.get(pin_label)
                if not isinstance(pin, dict) or set(pin) != {"path", "file_sha256", "payload_sha256"}:
                    fail("MANIFEST_UNIVERSE_TRANSITION_PIN_INVALID", pin_label)
            if transition["successor"]["payload_sha256"] != lineage.get("record_payload_sha256"):
                fail("MANIFEST_UNIVERSE_TRANSITION_SUCCESSOR_HASH_MISMATCH", str(snapshot_dir))
    _validate_candle_fetch_times(manifest, snapshot_dir)
    if manifest.get("auth_required") is not False or manifest.get("order_or_withdrawal_endpoints_called") is not False:
        fail("MANIFEST_SAFETY_INVARIANT_VIOLATED", str(snapshot_dir))
    return manifest


def _validate_candle_fetch_times(manifest: dict, snapshot_dir: Path) -> None:
    """v3 manifests must carry one request/response instant per captured
    (timeframe, market) candle fetch, inside the capture window; legacy v1/v2
    manifests must carry none (they are revalidated exactly as issued)."""
    version = manifest.get("capture_version")
    has_field = CANDLE_FETCH_TIMES_FIELD in manifest
    if version != CAPTURE_VERSION:
        if has_field:
            fail("MANIFEST_CANDLE_FETCH_TIMES_UNEXPECTED", f"{snapshot_dir}: capture_version={version!r}")
        return
    fetch_times = manifest.get(CANDLE_FETCH_TIMES_FIELD)
    if not isinstance(fetch_times, dict):
        fail("MANIFEST_CANDLE_FETCH_TIMES_MISSING", str(snapshot_dir))
    timeframes = manifest.get("timeframes")
    if not isinstance(timeframes, list) or set(fetch_times) != set(timeframes):
        fail("MANIFEST_CANDLE_FETCH_TIMES_TIMEFRAMES_MISMATCH", str(snapshot_dir))
    window_start = _parse_manifest_second(manifest.get("capture_started_at_utc"), "capture_started_at_utc")
    window_end = _parse_manifest_second(manifest.get("downloaded_at_utc"), "downloaded_at_utc")
    for timeframe in timeframes:
        by_market = fetch_times[timeframe]
        if not isinstance(by_market, dict) or sorted(by_market) != manifest["markets"]:
            fail("MANIFEST_CANDLE_FETCH_TIMES_MARKETS_MISMATCH", f"{snapshot_dir}:{timeframe}")
        for market, window in by_market.items():
            if not isinstance(window, dict) or set(window) != {"request_started_at_utc", "response_received_at_utc"}:
                fail("MANIFEST_CANDLE_FETCH_TIMES_SHAPE_INVALID", f"{timeframe}:{market}")
            requested = parse_fetch_time(window["request_started_at_utc"])
            received = parse_fetch_time(window["response_received_at_utc"])
            if not (window_start <= requested <= received <= window_end):
                fail("MANIFEST_CANDLE_FETCH_TIME_OUTSIDE_CAPTURE", f"{timeframe}:{market}")


def candle_fetch_window(manifest: dict, timeframe: str, market: str) -> dict | None:
    """The recorded (request_started, response_received) instants for one
    candle fetch of an already-validated manifest, or ``None`` for a legacy
    v1/v2 capture that never recorded them."""
    fetch_times = manifest.get(CANDLE_FETCH_TIMES_FIELD)
    if fetch_times is None:
        return None
    window = fetch_times[timeframe][market]
    return {
        "request_started_at": parse_fetch_time(window["request_started_at_utc"]),
        "response_received_at": parse_fetch_time(window["response_received_at_utc"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--snapshot-date", type=dt.date.fromisoformat)
    parser.add_argument("--universe-packet", type=Path, required=True, help="exact P3-12 population record")
    parser.add_argument("--expected-universe-record-sha256", required=True)
    parser.add_argument("--transition-manifest", type=Path)
    parser.add_argument("--snapshot-key")
    parser.add_argument("--request-interval-seconds", type=float, default=1.05)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    lineage = load_universe_lineage(
        args.universe_packet,
        expected_record_sha256=args.expected_universe_record_sha256,
        transition_manifest_path=args.transition_manifest,
    )
    markets = lineage["markets"]
    key = args.snapshot_key or P3_P4.snapshot_key(lineage)
    target = capture_snapshot(
        args.snapshot_root, markets=markets, snapshot_date=args.snapshot_date,
        request_interval_seconds=args.request_interval_seconds, timeout_seconds=args.timeout_seconds,
        snapshot_key=key, universe_lineage=lineage,
    )
    validated = validate_snapshot(target)
    print(json.dumps({
        "path": str(target), "snapshot_key": key,
        "market_count": validated["market_count"],
        "universe_record_sha256": lineage["record_payload_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CaptureError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
