#!/usr/bin/env python3
"""P3-12 capture: a complete, append-only Upbit KRW public-market snapshot.

Public endpoints only, no API key/secret, never an order/withdrawal/private
endpoint:

* ``GET /v1/market/all?is_details=true``      -- full market list, incl.
  ``market_event.warning``/``market_event.caution``.
* ``GET /v1/ticker?markets=...``               -- one batched call across
  every captured KRW market.
* ``GET /v1/orderbook?markets=...``            -- one batched call across
  every captured KRW market (best bid/ask + depth for spread/slippage).
* ``GET /v1/candles/days?market=...&count=...`` -- one call per KRW market,
  paced at no more than one request per second (listing-history length and
  30-finalized-day KRW turnover).

The collector writes into a temporary directory, builds and validates a
manifest, and moves the snapshot into evidence only after every candidate
market has been captured successfully -- append-only, never overwritten.
"""
from __future__ import annotations

import argparse
import base64
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
CONTRACT_PATH = ROOT / "config" / "upbit_market_capture_contract.json"
UTC = dt.timezone.utc
USER_AGENT = "Project-Atlas-upbit-universe-capture/1.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CAPTURE_VERSION = "upbit-market-capture/v1"
# Batched multi-market query strings are comfortably below any practical URL
# limit for the largest observed Upbit KRW market count; capped defensively.
MAX_MARKETS_PER_BATCH_CALL = 400


class CaptureError(RuntimeError):
    """Fail-closed capture or publication error."""


def fail(code: str, detail: str) -> None:
    raise CaptureError(f"{code}: {detail}")


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_READ_FAILED", f"{path}: {exc}")
    if contract.get("auth_required") is not False or contract.get("order_or_withdrawal_endpoints_called") is not False:
        fail("CONTRACT_SAFETY_INVARIANT_VIOLATED", "auth_required/order_or_withdrawal_endpoints_called must both be false")
    for key in (
        "market_all_endpoint", "ticker_endpoint_template", "orderbook_endpoint_template",
        "candles_days_endpoint_template", "market_prefix", "candle_lookback_count",
    ):
        if key not in contract:
            fail("CONTRACT_FIELD_MISSING", key)
    return contract


def public_get(url: str, timeout_seconds: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
            if not raw:
                fail("EMPTY_RESPONSE", url)
            return raw
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(5 * attempt)
    fail("FETCH_FAILED", f"{url}: {last_error}")


def parse_json_array(raw: bytes, label: str) -> list:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail("SOURCE_JSON_INVALID", f"{label}: {exc}")
    if not isinstance(payload, list):
        fail("SOURCE_JSON_INVALID", f"{label}: root not a list")
    return payload


def krw_markets(market_all_payload: list, market_prefix: str) -> list:
    # Deduplicated deterministically (first occurrence in the raw response
    # order wins) -- the classifier's load_snapshot_core independently
    # dedupes and records any duplicate for diagnostics, but the manifest's
    # own market list must already be a set (append-only, hash-bound).
    seen: dict[str, None] = {}
    for row in market_all_payload:
        if not isinstance(row, dict):
            continue
        code = row.get("market")
        if isinstance(code, str) and code.startswith(market_prefix) and code not in seen:
            seen[code] = None
    if not seen:
        fail("CANDIDATE_UNIVERSE_EMPTY", market_prefix)
    return sorted(seen)


def _batched(markets: list, size: int) -> list:
    return [markets[i:i + size] for i in range(0, len(markets), size)]


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
    snapshot_date: Optional[dt.date] = None,
    request_interval_seconds: float = 1.05,
    timeout_seconds: int = 60,
    contract: Optional[dict] = None,
    fetcher: Optional[Callable[[str, int], bytes]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], dt.datetime] = utc_now,
) -> Path:
    contract = contract or load_contract()
    observed_start = clock().astimezone(UTC)
    vintage = snapshot_date or observed_start.date()
    if observed_start.date() != vintage:
        fail("CAPTURE_DATE_MISMATCH", f"clock={observed_start.date()} requested={vintage}")
    if request_interval_seconds < 1:
        fail("RATE_LIMIT_POLICY_INVALID", str(request_interval_seconds))
    target = Path(snapshot_root) / vintage.isoformat()
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    fetch = fetcher or public_get
    temporary_parent = Path(tempfile.mkdtemp(prefix="upbit-universe-", dir=str(target.parent)))
    snapshot = temporary_parent / vintage.isoformat()
    snapshot.mkdir()
    try:
        (snapshot / "_downloaded_at.txt").write_text(iso_utc(observed_start) + "\n", encoding="utf-8")

        market_all_raw = fetch(contract["market_all_endpoint"], timeout_seconds)
        market_all = parse_json_array(market_all_raw, "market/all")
        markets = krw_markets(market_all, contract["market_prefix"])
        checksums = {
            contract["market_all_raw_file"]: write_raw(snapshot, contract["market_all_raw_file"], market_all_raw),
        }

        ticker_chunks = []
        for chunk in _batched(markets, MAX_MARKETS_PER_BATCH_CALL):
            encoded = urllib.parse.quote(",".join(chunk), safe=",")
            url = contract["ticker_endpoint_template"].format(MARKETS=encoded)
            raw = fetch(url, timeout_seconds)
            ticker_chunks.append(parse_json_array(raw, "ticker"))
        ticker_payload = [row for chunk in ticker_chunks for row in chunk]
        ticker_raw = json.dumps(ticker_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        checksums[contract["ticker_raw_file"]] = write_raw(snapshot, contract["ticker_raw_file"], ticker_raw)

        orderbook_chunks = []
        for chunk in _batched(markets, MAX_MARKETS_PER_BATCH_CALL):
            encoded = urllib.parse.quote(",".join(chunk), safe=",")
            url = contract["orderbook_endpoint_template"].format(MARKETS=encoded)
            raw = fetch(url, timeout_seconds)
            orderbook_chunks.append(parse_json_array(raw, "orderbook"))
        orderbook_payload = [row for chunk in orderbook_chunks for row in chunk]
        orderbook_raw = json.dumps(orderbook_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        checksums[contract["orderbook_raw_file"]] = write_raw(snapshot, contract["orderbook_raw_file"], orderbook_raw)

        count = contract["candle_lookback_count"]
        candle_records = []
        for index, market in enumerate(markets):
            url = contract["candles_days_endpoint_template"].format(MARKET=market, COUNT=count)
            raw = fetch(url, timeout_seconds)
            parse_json_array(raw, market)  # structural check only; parsed again downstream
            candle_records.append({
                "market": market,
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "body_b64": base64.b64encode(raw).decode("ascii"),
            })
            if index + 1 < len(markets):
                sleeper(request_interval_seconds)
        bundle_raw = b"".join(
            json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            for record in sorted(candle_records, key=lambda row: row["market"])
        )
        checksums[contract["candles_bundle_raw_file"]] = write_raw(snapshot, contract["candles_bundle_raw_file"], bundle_raw)

        (snapshot / "_sha256.txt").write_text(
            "".join(f"{checksums[name]}  {name}\n" for name in sorted(checksums)),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 1,
            "capture_version": CAPTURE_VERSION,
            "transform_version": contract["transform_version"],
            "source_name": contract["source_name"],
            "vintage_date": vintage.isoformat(),
            "downloaded_at_utc": iso_utc(observed_start),
            "market_count": len(markets),
            "markets": markets,
            "checksums": checksums,
            "auth_required": False,
            "order_or_withdrawal_endpoints_called": False,
        }
        (snapshot / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_snapshot(snapshot)
        snapshot.replace(target)
        shutil.rmtree(temporary_parent, ignore_errors=True)
        return target
    except Exception:
        shutil.rmtree(temporary_parent, ignore_errors=True)
        raise


def validate_snapshot(snapshot_dir: Path) -> dict:
    """Structural, hash-bound validation only -- semantic parsing of the raw
    payloads into per-market metrics is the classifier's job
    (``universe/upbit_tradeable_universe.py``'s ``load_snapshot_core``).
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
    if not isinstance(markets, list) or not markets or sorted(markets) != markets or len(set(markets)) != len(markets):
        fail("MANIFEST_MARKET_LIST_INVALID", str(snapshot_dir))
    if manifest.get("market_count") != len(markets):
        fail("MANIFEST_MARKET_COUNT_MISMATCH", str(snapshot_dir))
    if manifest.get("auth_required") is not False or manifest.get("order_or_withdrawal_endpoints_called") is not False:
        fail("MANIFEST_SAFETY_INVARIANT_VIOLATED", str(snapshot_dir))
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--snapshot-date", type=dt.date.fromisoformat)
    parser.add_argument("--request-interval-seconds", type=float, default=1.05)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    target = capture_snapshot(
        args.snapshot_root,
        snapshot_date=args.snapshot_date,
        request_interval_seconds=args.request_interval_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    validated = validate_snapshot(target)
    print(json.dumps({"path": str(target), "market_count": validated["market_count"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CaptureError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
