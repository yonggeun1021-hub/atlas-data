#!/usr/bin/env python3
"""Capture a complete, append-only Kraken USD spot breadth snapshot.

The collector uses only public endpoints.  It deliberately paces per-pair OHLC
requests at no more than one request per second, writes into a temporary
directory, validates the full snapshot, and moves it into evidence only after
every candidate pair has been captured successfully.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Callable, Optional
import urllib.error
import urllib.parse
import urllib.request


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import crypto_breadth as breadth  # noqa: E402


UTC = dt.timezone.utc
USER_AGENT = "Project-Atlas-crypto-breadth/1.0"


class CaptureError(RuntimeError):
    """Fail-closed capture or publication error."""


def fail(code: str, detail: str) -> None:
    raise CaptureError(f"{code}: {detail}")


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def public_get(url: str, timeout_seconds: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                raw = response.read()
            if not raw:
                fail("EMPTY_RESPONSE", url)
            return raw
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(5 * attempt)
    fail("FETCH_FAILED", f"{url}: {last_error}")


def parse_payload(raw: bytes, label: str) -> dict:
    try:
        payload = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=breadth.reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail("SOURCE_JSON_INVALID", f"{label}: {exc}")
    if not isinstance(payload, dict):
        fail("SOURCE_JSON_INVALID", f"{label}: root")
    return payload


def write_raw(snapshot: Path, relative_gz: str, raw: bytes) -> str:
    target = snapshot / relative_gz
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
            stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def candidate_pairs(assets: dict, pairs: dict, policy: dict) -> list[str]:
    allowed_assets = set(policy["allowed_asset_statuses"])
    allowed_pairs = set(policy["allowed_pair_statuses"])
    candidates = []
    for pair_id, pair in pairs.items():
        if (
            pair["quote"] == policy["quote_currency"]
            and pair["status"] in allowed_pairs
            and assets[pair["base"]]["status"] in allowed_assets
            and assets[pair["quote"]]["status"] in allowed_assets
        ):
            candidates.append(pair_id)
    if not candidates:
        fail("CANDIDATE_UNIVERSE_EMPTY", policy["quote_currency"])
    return sorted(candidates)


def capture_snapshot(
    snapshot_root: Path,
    *,
    snapshot_date: Optional[dt.date] = None,
    request_interval_seconds: float = 1.05,
    timeout_seconds: int = 60,
    fetcher: Optional[Callable[[str, int], bytes]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], dt.datetime] = utc_now,
) -> Path:
    contract = breadth.load_contract()
    policy = breadth.load_universe_policy()
    observed_start = clock().astimezone(UTC)
    vintage = snapshot_date or observed_start.date()
    breadth.require_ratified_policy(
        policy, vintage - dt.timedelta(days=1)
    )
    if observed_start.date() != vintage:
        fail(
            "CAPTURE_DATE_MISMATCH",
            f"clock={observed_start.date()} requested={vintage}",
        )
    if request_interval_seconds < 1:
        fail("RATE_LIMIT_POLICY_INVALID", str(request_interval_seconds))
    target = Path(snapshot_root) / vintage.isoformat()
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    fetch = fetcher or public_get
    temporary_parent = Path(
        tempfile.mkdtemp(prefix="crypto-breadth-", dir=str(target.parent))
    )
    snapshot = temporary_parent / vintage.isoformat()
    snapshot.mkdir()
    try:
        (snapshot / "_downloaded_at.txt").write_text(
            observed_start.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            )
            + "\n",
            encoding="utf-8",
        )
        assets_raw = fetch(contract["assets_endpoint"], timeout_seconds)
        pairs_raw = fetch(contract["asset_pairs_endpoint"], timeout_seconds)
        assets = breadth.normalize_assets(
            parse_payload(assets_raw, "Assets")
        )
        pairs = breadth.normalize_pairs(
            parse_payload(pairs_raw, "AssetPairs"), assets
        )
        candidates = candidate_pairs(assets, pairs, policy)
        checksums = {
            breadth.raw_checksum_name(contract["assets_raw_file"]): write_raw(
                snapshot, contract["assets_raw_file"], assets_raw
            ),
            breadth.raw_checksum_name(
                contract["asset_pairs_raw_file"]
            ): write_raw(
                snapshot, contract["asset_pairs_raw_file"], pairs_raw
            ),
        }
        since_day = vintage - dt.timedelta(
            days=contract["capture_lookback_calendar_days"]
        )
        since = int(
            dt.datetime.combine(
                since_day, dt.time(), tzinfo=UTC
            ).timestamp()
        )
        ohlc_records = []
        for index, pair_id in enumerate(candidates):
            encoded_pair = urllib.parse.quote(pair_id, safe="")
            url = contract["ohlc_endpoint_template"].format(
                PAIR=encoded_pair, SINCE=since
            )
            raw = fetch(url, timeout_seconds)
            payload = parse_payload(raw, pair_id)
            result = breadth.source_result(payload, pair_id)
            pair_keys = [key for key in result if key != "last"]
            if pair_keys != [pair_id]:
                fail("OHLC_PAIR_MISMATCH", f"{pair_id}: {pair_keys}")
            ohlc_records.append(
                {
                    "pair_id": pair_id,
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "body_b64": base64.b64encode(raw).decode("ascii"),
                }
            )
            if index + 1 < len(candidates):
                sleeper(request_interval_seconds)
        bundle_raw = b"".join(
            json.dumps(
                record,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
            for record in ohlc_records
        )
        bundle_file = contract["ohlc_bundle_raw_file"]
        checksums[breadth.raw_checksum_name(bundle_file)] = write_raw(
            snapshot, bundle_file, bundle_raw
        )
        (snapshot / "_sha256.txt").write_text(
            "".join(
                f"{checksums[name]}  {name}\n"
                for name in sorted(checksums)
            ),
            encoding="utf-8",
        )
        breadth.build_manifest(
            snapshot, "crypto-breadth-capture/v2"
        )
        breadth.validate_snapshot(snapshot)
        snapshot.replace(target)
        shutil.rmtree(temporary_parent)
        return target
    except Exception:
        shutil.rmtree(temporary_parent, ignore_errors=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
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
    validated = breadth.validate_snapshot(target)
    print(json.dumps(validated, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CaptureError, breadth.BreadthError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
