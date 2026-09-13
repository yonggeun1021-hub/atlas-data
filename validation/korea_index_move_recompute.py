#!/usr/bin/env python3
"""Recompute KOSPI/KOSDAQ one-session moves from retained raw index responses.

The KRX Open API five-signal packet (``data/observations/korea_market_signals``)
keeps only response hashes (``raw_persistence: 0``), so its
``axes.TREND.measurement.benchmarks.*.one_session_return_pct`` cannot be
recomputed from anything it retains. The KRX Information Data System source
capture (``evidence/regime/kr_information_system/<date>/source-capture``) does
retain the original index response bytes, with a sha256 per response in its
manifest. This module binds the two for validators:

* every retained index response is re-hashed against its manifest entry
  before a single value is read from it;
* the benchmark row's ``CLSPRC_IDX`` and ``CMPPREVDD_IDX`` give the session
  close and the previous close (close minus change), and the one-session move
  is recomputed with the producer's own formula and decimal places;
* when the previous session's response is also retained, its close must equal
  that previous close (cross-session check);
* the recomputed move is compared with the packet's value.

A session with no retained raw response is ``NOT_VERIFIABLE_RAW_NOT_RETAINED``
and is never guessed. The comparison is cross-source (Information Data System
bytes versus the Open API value); it does not re-hash the Open API response.

Read-only: no network, no writes, no authority of any kind.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_GLOB = "evidence/regime/kr_information_system/*/source-capture/manifest.json"
CONTRACT_PATH = Path("config") / "korea_market_signals_contract.json"
MARKETS = ("KOSPI", "KOSDAQ")
RAW_SOURCE = "KRX_INFORMATION_DATA_SYSTEM_RETAINED_ORIGINAL_RESPONSE"
AUTHORITY = {
    "validation_recompute_only": True,
    "regime_authorized": False,
    "trading_authorized": False,
    "order_authorized": False,
}


class IndexRecomputeError(ValueError):
    """A retained response or packet cannot be read safely."""


def _decimal(value) -> Decimal | None:
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _format(value: Decimal, places: int) -> str:
    return format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN), "f")


def _load_contract(root: Path) -> dict:
    contract = json.loads((Path(root) / CONTRACT_PATH).read_text(encoding="utf-8"))
    names = contract.get("benchmark_names") or {}
    places = contract.get("output_decimal_places")
    if not isinstance(places, int) or set(names) != {"kospi", "kosdaq"}:
        raise IndexRecomputeError("CONTRACT_INVALID")
    return {"benchmark_names": {market: names[market.lower()] for market in MARKETS}, "places": places}


def retained_index_responses(root: Path = ROOT) -> dict[tuple[str, str], dict]:
    """Every hash-verified retained index response, keyed by (YYYYMMDD, MARKET).

    Identical bytes retained by more than one capture collapse to one entry;
    two captures retaining different bytes for the same key are a conflict.
    """
    found: dict[tuple[str, str], dict] = {}
    for manifest_path in sorted(Path(root).glob(CAPTURE_GLOB)):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("original_response_bytes_retained") is not True:
            continue
        for record in manifest.get("records") or []:
            if not isinstance(record, dict):
                continue
            parts = str(record.get("key") or "").split(":")
            if len(parts) != 3 or parts[1] not in MARKETS or parts[2] != "index":
                continue
            response = record.get("response") if isinstance(record.get("response"), dict) else {}
            relative = PurePosixPath(str(response.get("path") or ""))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                continue
            response_path = manifest_path.parent / relative
            try:
                raw = response_path.read_bytes()
            except OSError:
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if digest != response.get("sha256"):
                raise IndexRecomputeError(
                    f"RETAINED_RESPONSE_HASH_MISMATCH:{response_path.relative_to(root).as_posix()}"
                )
            key = (parts[0], parts[1])
            entry = {
                "path": response_path.relative_to(root).as_posix(),
                "sha256": digest,
                "manifest_path": manifest_path.relative_to(root).as_posix(),
                "raw": raw,
            }
            previous = found.get(key)
            if previous is not None and previous["sha256"] != digest:
                raise IndexRecomputeError(f"RETAINED_RESPONSE_CONFLICT:{key[0]}:{key[1]}")
            found.setdefault(key, entry)
    return found


def _benchmark_row(entry: dict, name: str) -> dict | None:
    try:
        payload = json.loads(entry["raw"])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    rows = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows if isinstance(row, dict) and row.get("IDX_NM") == name]
    return matches[0] if len(matches) == 1 else None


def recompute_session(
    session_date: str, market: str, *, root: Path = ROOT, retained: dict | None = None
) -> dict:
    """Recompute one market's one-session move for an ISO session date."""
    contract = _load_contract(root)
    retained = retained_index_responses(root) if retained is None else retained
    name = contract["benchmark_names"][market]
    key = (session_date.replace("-", ""), market)
    result = {
        "session_date": session_date,
        "market": market,
        "benchmark_name": name,
        "raw_source": RAW_SOURCE,
        "status": "NOT_VERIFIABLE_RAW_NOT_RETAINED",
        "response_path": None,
        "response_sha256": None,
        "close": None,
        "change": None,
        "previous_close": None,
        "one_session_return_pct": None,
        "provider_fluctuation_rate_pct": None,
        "previous_session_cross_check": "NOT_AVAILABLE",
    }
    entry = retained.get(key)
    if entry is None:
        return result
    row = _benchmark_row(entry, name)
    close = _decimal((row or {}).get("CLSPRC_IDX"))
    change = _decimal((row or {}).get("CMPPREVDD_IDX"))
    result.update(response_path=entry["path"], response_sha256=entry["sha256"])
    if row is None or close is None or change is None or close - change == 0:
        result["status"] = "NOT_VERIFIABLE_BENCHMARK_ROW_INVALID"
        return result
    previous_close = close - change
    result.update(
        status="RECOMPUTED",
        close=format(close, "f"),
        change=format(change, "f"),
        previous_close=format(previous_close, "f"),
        one_session_return_pct=_format(
            (close / previous_close - Decimal(1)) * Decimal(100), contract["places"]
        ),
        provider_fluctuation_rate_pct=(row or {}).get("FLUC_RT"),
    )
    earlier = sorted(
        date for date, retained_market in retained
        if retained_market == market and date < key[0]
    )
    if earlier:
        prior_row = _benchmark_row(retained[(earlier[-1], market)], name)
        prior_close = _decimal((prior_row or {}).get("CLSPRC_IDX"))
        result["previous_session_cross_check"] = (
            f"MATCH:{earlier[-1]}" if prior_close == previous_close
            else f"MISMATCH:{earlier[-1]}:{prior_close}"
        )
        if prior_close != previous_close:
            result["status"] = "RECOMPUTE_CROSS_CHECK_FAILED"
    return result


def verify_packet(packet: dict, *, root: Path = ROOT) -> dict:
    """Compare a five-signal packet's KOSPI/KOSDAQ moves with retained raw bytes."""
    as_of = packet.get("as_of_date") if isinstance(packet, dict) else None
    try:
        dt.date.fromisoformat(str(as_of))
    except ValueError as exc:
        raise IndexRecomputeError("PACKET_AS_OF_DATE_INVALID") from exc
    benchmarks = (
        ((packet.get("axes") or {}).get("TREND") or {}).get("measurement") or {}
    ).get("benchmarks") or {}
    retained = retained_index_responses(root)
    markets = {}
    for market in MARKETS:
        recomputed = recompute_session(as_of, market, root=root, retained=retained)
        reported = (benchmarks.get(market) or {}).get("one_session_return_pct")
        if recomputed["status"] != "RECOMPUTED":
            verdict = recomputed["status"]
        elif reported == recomputed["one_session_return_pct"]:
            verdict = "MATCH"
        else:
            verdict = "MISMATCH"
        markets[market] = recomputed | {"packet_value": reported, "verdict": verdict}
    return {
        "schema_version": "korea_index_move_recompute/1",
        "packet_as_of_date": as_of,
        "packet_payload_sha256": packet.get("payload_sha256"),
        "markets": markets,
        "authority": dict(AUTHORITY),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("packet", nargs="?", type=Path, help="korea_market_signals packet.json")
    parser.add_argument("--session-date", help="recompute one ISO session date without a packet")
    args = parser.parse_args(argv)
    if bool(args.packet) == bool(args.session_date):
        parser.error("give exactly one of packet or --session-date")
    if args.packet:
        report = verify_packet(json.loads(args.packet.read_text(encoding="utf-8")))
        failed = any(row["verdict"] == "MISMATCH" or row["verdict"] == "RECOMPUTE_CROSS_CHECK_FAILED"
                     for row in report["markets"].values())
    else:
        retained = retained_index_responses(ROOT)
        report = {
            "schema_version": "korea_index_move_recompute/1",
            "markets": {
                market: recompute_session(args.session_date, market, retained=retained)
                for market in MARKETS
            },
            "authority": dict(AUTHORITY),
        }
        failed = any(row["status"] == "RECOMPUTE_CROSS_CHECK_FAILED" for row in report["markets"].values())
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
