#!/usr/bin/env python3
"""P2-03 wiring: commit the non-reconstructive per-market lineage summary
that P1-KR-05's own "recent" scope Korea Breadth observation packets
already carry (payload_sha256, as_of_date, source_available_at,
captured_at, first_seen_at) -- no raw response body, no per-symbol
identity or price, no re-fetch.

Reads the two already-built "recent" scope breadth packets
(korea-breadth-recent-kospi.json / korea-breadth-recent-kosdaq.json,
produced in-memory by .github/scripts/korea_breadth_derived_outputs.py
and uploaded as a workflow artifact -- never re-derived from a second
KRX request here) and commits only their identity/timing facts to
data/observations/korea_breadth_context/{date}/packet.json. This is the
sole tracked anchor rotation/korea_capital_rotation_ledger_wire.py reads
to build coverage_context.breadth's per-market lineage -- if this file
is absent for a date, the wiring layer must see UNKNOWN, never a
default/AVAILABLE guess.

capture_mode is a required, explicitly-declared fact (never inferred
from timestamps alone): "forward_live" for a genuine live capture
through this real fetch mechanism (the only mode this repository's own
workflow ever produces), "historical_backfill" for evidence deliberately
re-derived long after the fact for testing/regression purposes only.
rotation/korea_capital_rotation_ledger_wire.py's confirmed-history gate
treats historical_backfill as permanently ineligible regardless of how
the real timestamps compare -- date math alone cannot distinguish a
genuine next-trading-day capture from a convenient later catch-up, so
this is a declared fact, not derived.

Idempotent: byte-compares against any already-committed packet for the
same date and fails closed on source drift (EXISTING_PACKET_DRIFT_OR_TAMPER),
exactly like the P3-02/P3-04 population scripts.  A later workflow dispatch
may carry a different workflow_run_id while reusing the exact same two source
packets.  In that case the first committed packet remains immutable: the
function rebuilds the packet using the *committed* run id and current source
packets, verifies exact equality, and reports verified_existing without a
rewrite.  The current run id is never backfilled into old evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "korea_breadth_context_lineage/2"
REQUIRED_MARKETS = ("KOSPI", "KOSDAQ")
CAPTURE_MODES = ("forward_live", "historical_backfill")
MARKET_LINEAGE_FIELDS = ("payload_sha256", "as_of_date", "source_available_at", "captured_at", "first_seen_at")


class ContextPopulateError(ValueError):
    """Fail-closed P2-03 breadth-context lineage population violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _iso_date(compact_or_iso: str) -> str:
    if len(compact_or_iso) == 8 and compact_or_iso.isdigit():
        return f"{compact_or_iso[0:4]}-{compact_or_iso[4:6]}-{compact_or_iso[6:8]}"
    return compact_or_iso


def load_recent_market_packet(derived_dir: Path, market: str) -> dict:
    path = derived_dir / f"korea-breadth-recent-{market.lower()}.json"
    if not path.is_file():
        raise ContextPopulateError(f"RECENT_PACKET_MISSING:{market}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextPopulateError(f"RECENT_PACKET_READ_FAILED:{market}:{exc}") from exc
    if value.get("scope") != "recent" or value.get("market") != market:
        raise ContextPopulateError(f"RECENT_PACKET_IDENTITY_MISMATCH:{market}")
    for field in MARKET_LINEAGE_FIELDS:
        if field not in value:
            raise ContextPopulateError(f"RECENT_PACKET_FIELD_MISSING:{market}:{field}")
    return value


def build_context_summary(
    market_packets: dict[str, dict], *, workflow_run_id: str | None, capture_mode: str
) -> dict:
    """market_packets maps 'KOSPI'/'KOSDAQ' to a loaded "recent" scope
    breadth packet (unchanged, from load_recent_market_packet). Extracts
    only lineage_sha256 (the packet's own payload_sha256)/as_of_date/
    source_available_at/captured_at/first_seen_at per market -- never a
    raw price, symbol, or count."""
    if capture_mode not in CAPTURE_MODES:
        raise ContextPopulateError(f"CAPTURE_MODE_INVALID:{capture_mode}")
    if set(market_packets) != set(REQUIRED_MARKETS):
        raise ContextPopulateError("MARKETS_INCOMPLETE")
    as_of_dates = set()
    markets = {}
    generated_ats = []
    for market in REQUIRED_MARKETS:
        packet = market_packets[market]
        as_of = _iso_date(packet["as_of_date"])
        as_of_dates.add(as_of)
        markets[market] = {
            "lineage_sha256": packet["payload_sha256"],
            "as_of_date": as_of,
            "source_available_at": packet["source_available_at"],
            "captured_at": packet["captured_at"],
            "first_seen_at": packet["first_seen_at"],
        }
        current_fetch = packet.get("fetched_at_utc", {})
        if isinstance(current_fetch, dict) and current_fetch.get("current"):
            generated_ats.append(current_fetch["current"])
    if len(as_of_dates) != 1:
        raise ContextPopulateError("MARKETS_AS_OF_DATE_MISMATCH")
    (as_of_date,) = as_of_dates
    # generated_at is derived from the underlying packets' own recorded
    # fetch timestamps (never wall-clock), for byte-identical
    # reproducibility on every re-run against the same source artifact.
    generated_at = max(generated_ats) if generated_ats else None
    if generated_at is None:
        raise ContextPopulateError("GENERATED_AT_UNRESOLVED")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": as_of_date,
        "capture_mode": capture_mode,
        "markets": markets,
        "source": {
            "producer": "korea_breadth_derived_outputs.py",
            "scope": "recent",
            "workflow_run_id": workflow_run_id,
        },
        "generated_at": generated_at,
    }
    summary["payload_sha256"] = payload_sha256(summary)
    return summary


def output_path_for(as_of_date: str) -> Path:
    return ROOT / "data" / "observations" / "korea_breadth_context" / as_of_date / "packet.json"


def verify_existing_context(as_of_date: str, *, capture_mode: str = "forward_live") -> dict:
    """Validate the immutable committed context before a workflow reuses it.

    Existence alone is never enough to suppress a provider call: the packet
    must retain the exact schema, date, market set, lineage fields, capture
    mode, source identity, and self-hash expected by this producer.
    """
    path = output_path_for(as_of_date)
    if not path.is_file():
        raise ContextPopulateError(f"EXISTING_PACKET_MISSING:{as_of_date}")
    try:
        packet = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextPopulateError(f"EXISTING_PACKET_READ_FAILED:{exc}") from exc
    if not isinstance(packet, dict):
        raise ContextPopulateError("EXISTING_PACKET_SCHEMA_INVALID")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise ContextPopulateError("EXISTING_PACKET_SCHEMA_VERSION_INVALID")
    if packet.get("as_of_date") != as_of_date:
        raise ContextPopulateError("EXISTING_PACKET_DATE_MISMATCH")
    if packet.get("capture_mode") != capture_mode:
        raise ContextPopulateError("EXISTING_PACKET_CAPTURE_MODE_MISMATCH")
    if set(packet.get("markets", {})) != set(REQUIRED_MARKETS):
        raise ContextPopulateError("EXISTING_PACKET_MARKETS_INCOMPLETE")
    for market in REQUIRED_MARKETS:
        row = packet["markets"].get(market)
        if not isinstance(row, dict) or set(row) != {
            "lineage_sha256", *MARKET_LINEAGE_FIELDS[1:]
        } or row.get("as_of_date") != as_of_date:
            raise ContextPopulateError(f"EXISTING_PACKET_MARKET_LINEAGE_INVALID:{market}")
    source = packet.get("source")
    if not isinstance(source, dict) or source.get("producer") != (
        "korea_breadth_derived_outputs.py"
    ) or source.get("scope") != "recent":
        raise ContextPopulateError("EXISTING_PACKET_SOURCE_INVALID")
    unsigned = {key: value for key, value in packet.items() if key != "payload_sha256"}
    if packet.get("payload_sha256") != payload_sha256(unsigned):
        raise ContextPopulateError("EXISTING_PACKET_HASH_MISMATCH")
    return packet


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def populate(
    derived_dir: Path, *, workflow_run_id: str | None = None, capture_mode: str
) -> dict:
    market_packets = {
        market: load_recent_market_packet(derived_dir, market) for market in REQUIRED_MARKETS
    }
    summary = build_context_summary(
        market_packets, workflow_run_id=workflow_run_id, capture_mode=capture_mode
    )
    path = output_path_for(summary["as_of_date"])
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContextPopulateError(f"EXISTING_PACKET_READ_FAILED:{exc}") from exc
        if not isinstance(existing, dict):
            raise ContextPopulateError("EXISTING_PACKET_SCHEMA_INVALID")
        if existing == summary:
            return {
                "outcome": "verified_existing", "path": str(path),
                "payload_sha256": existing["payload_sha256"],
                "committed_workflow_run_id": existing.get("source", {}).get("workflow_run_id"),
                "current_workflow_run_id": workflow_run_id,
            }
        # The workflow run id identifies the first producer, not the
        # economic/source contents.  Rebuild from today's exact source
        # packets while retaining that immutable first-producer id.  Exact
        # equality proves that run-id drift is the *only* difference.
        committed_run_id = existing.get("source", {}).get("workflow_run_id")
        expected_existing = build_context_summary(
            market_packets,
            workflow_run_id=committed_run_id,
            capture_mode=capture_mode,
        )
        if existing == expected_existing:
            return {
                "outcome": "verified_existing", "path": str(path),
                "payload_sha256": existing["payload_sha256"],
                "committed_workflow_run_id": committed_run_id,
                "current_workflow_run_id": workflow_run_id,
            }
        raise ContextPopulateError("EXISTING_PACKET_DRIFT_OR_TAMPER")
    write_json_atomic(path, summary)
    return {"outcome": "populated", "path": str(path), "payload_sha256": summary["payload_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived-dir", type=Path)
    parser.add_argument("--workflow-run-id", default=None)
    parser.add_argument("--capture-mode", choices=CAPTURE_MODES)
    parser.add_argument("--verify-existing-date")
    args = parser.parse_args()
    try:
        if args.verify_existing_date:
            if args.derived_dir is not None or args.workflow_run_id is not None:
                raise ContextPopulateError("VERIFY_EXISTING_ARGUMENTS_CONFLICT")
            packet = verify_existing_context(
                args.verify_existing_date,
                capture_mode=args.capture_mode or "forward_live",
            )
            result = {
                "outcome": "verified_existing", "path": str(output_path_for(args.verify_existing_date)),
                "payload_sha256": packet["payload_sha256"],
            }
        else:
            if args.derived_dir is None or args.capture_mode is None:
                raise ContextPopulateError("POPULATE_ARGUMENTS_INCOMPLETE")
            result = populate(
                args.derived_dir,
                workflow_run_id=args.workflow_run_id,
                capture_mode=args.capture_mode,
            )
    except ContextPopulateError as exc:
        print(f"korea breadth context population failed reason={exc}")
        return 1
    print(f"korea breadth context population outcome={result['outcome']} path={result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
