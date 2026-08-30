#!/usr/bin/env python3
"""P4-07 Upbit market-evidence scheduled population wiring.

Reads the exact, already-committed P4-07 raw microstructure snapshot for one
snapshot_date and publishes (or verifies) the corresponding derived evidence
packet (finalized candles per timeframe, trades, orderbook spread/depth/
slippage/freshness) built by ``microstructure/upbit_market_evidence.py``.

This module never calls a network provider -- semantic derivation only, from
raw bytes already hash-validated by
``upbit_microstructure_capture.py::validate_snapshot``. Idempotent: rerunning
against the same committed raw snapshot re-derives and verifies a
byte-identical packet, matching an existing published one, or fails closed on
drift/tamper -- exactly ``upbit_universe_populate.py``'s discipline.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "microstructure"
DATA_ROOT = ROOT / "data" / "observations" / "upbit_market_evidence"
RECORD_SCHEMA_VERSION = "upbit_microstructure_population/2"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CAP = _load_module("upbit_microstructure_capture_for_population", ".github/scripts/upbit_microstructure_capture.py")
EV = _load_module("upbit_market_evidence_for_population", "microstructure/upbit_market_evidence.py")


class PopulationError(ValueError):
    """Fail-closed P4-07 population wiring violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def snapshot_dir(snapshot_date: str, raw_root: Path = RAW_ROOT) -> Path:
    return Path(raw_root) / snapshot_date


def output_path(snapshot_date: str, data_root: Path = DATA_ROOT) -> Path:
    return Path(data_root) / snapshot_date / "packet.json"


def _read_ndjson_bundle_by_market(snapshot: Path, relative_gz: str) -> dict:
    raw_bundle = gzip.open(snapshot / relative_gz, "rb").read()
    by_market: dict = {}
    for line in raw_bundle.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        market = record["market"]
        if market in by_market:
            raise PopulationError(f"DUPLICATE_MARKET_IN_BUNDLE:{relative_gz}:{market}")
        body = base64.b64decode(record["body_b64"])
        if hashlib.sha256(body).hexdigest() != record["response_sha256"]:
            raise PopulationError(f"BUNDLE_BODY_HASH_MISMATCH:{relative_gz}:{market}")
        by_market[market] = {
            "body": json.loads(body),
            "response_sha256": record["response_sha256"],
        }
    return by_market


def build_packets(snapshot_date: str, raw_root: Path = RAW_ROOT) -> dict:
    directory = snapshot_dir(snapshot_date, raw_root)
    if not directory.is_dir():
        raise PopulationError(f"RAW_SNAPSHOT_MISSING:{snapshot_date}")
    contract = CAP.load_contract()
    try:
        manifest = CAP.validate_snapshot(directory)
    except CAP.CaptureError as exc:
        raise PopulationError(f"RAW_SNAPSHOT_INVALID:{snapshot_date}:{exc}") from exc

    lineage = manifest.get("universe_lineage")
    if not isinstance(lineage, dict) or not lineage.get("record_payload_sha256"):
        raise PopulationError("UNIVERSE_LINEAGE_MISSING")
    if lineage.get("markets") != manifest.get("markets"):
        raise PopulationError("PARTIAL_UNIVERSE_MANIFEST")
    policy = EV.load_policy()
    if (lineage.get("p4_policy") or {}).get("packet_sha256") != policy.get("packet_sha256"):
        raise PopulationError("P4_POLICY_LINEAGE_HASH_MISMATCH")
    as_of = _parse_utc(manifest["downloaded_at_utc"])
    captured_at = as_of
    generated_at = as_of
    effective_from = _parse_utc(policy["effective_from_utc"])
    effective_to = _parse_utc(policy["effective_to_utc"])
    if not (effective_from <= as_of < effective_to):
        raise PopulationError("P4_POLICY_NOT_EFFECTIVE_AT_CAPTURE")

    candles_by_timeframe_by_market: dict = {}
    for timeframe in contract["timeframes"]:
        file_name = contract["candles_raw_file_template"].format(TIMEFRAME=timeframe)
        candles_by_timeframe_by_market[timeframe] = _read_ndjson_bundle_by_market(directory, file_name)

    trades_by_market = _read_ndjson_bundle_by_market(directory, contract["trades_raw_file"])

    orderbook_raw = json.loads(gzip.open(directory / contract["orderbook_raw_file"], "rb").read())
    orderbook_by_market = {}
    for row in orderbook_raw:
        if not isinstance(row, dict) or not row.get("market"):
            continue
        if row["market"] in orderbook_by_market:
            raise PopulationError(f"DUPLICATE_ORDERBOOK_MARKET:{row['market']}")
        orderbook_by_market[row["market"]] = row

    packets = {}
    errors = {}
    market_results = {}
    for market in manifest["markets"]:
        try:
            candles_by_timeframe = {
                timeframe: (
                    candles_by_timeframe_by_market[timeframe].get(market) or {}
                ).get("body")
                for timeframe in contract["timeframes"]
            }
            candle_hashes = {
                timeframe: (
                    candles_by_timeframe_by_market[timeframe].get(market) or {}
                ).get("response_sha256")
                for timeframe in contract["timeframes"]
            }
            trade_record = trades_by_market.get(market) or {}
            orderbook_record = orderbook_by_market.get(market)
            source_identity = {
                "source_id": "upbit_public_api",
                "source_name": manifest.get("source_name"),
                "venue": "UPBIT",
                "quote_currency": "KRW",
                "raw_snapshot_key": snapshot_date,
                "raw_manifest_sha256": hashlib.sha256(
                    (directory / "_manifest.json").read_bytes()
                ).hexdigest(),
                "candle_response_sha256_by_timeframe": candle_hashes,
                "trades_response_sha256": trade_record.get("response_sha256"),
                "orderbook_market_payload_sha256": (
                    payload_sha256(orderbook_record) if orderbook_record is not None else None
                ),
            }
            packets[market] = EV.build_market_evidence_packet(
                market,
                candles_by_timeframe=candles_by_timeframe,
                trades=trade_record.get("body"),
                orderbook_row=orderbook_record,
                as_of=as_of, captured_at=captured_at, generated_at=generated_at,
                policy=policy, source_identity=source_identity,
            )
            market_results[market] = {
                "status": packets[market]["status"],
                "reasons": packets[market]["fail_closed_reasons"],
                "packet_sha256": packets[market]["payload_sha256"],
            }
        except EV.MarketEvidenceError as exc:
            # A gap in ONE market's evidence (e.g. missing orderbook) fails
            # only that market's packet -- every other market is unaffected.
            errors[market] = str(exc)
            market_results[market] = {
                "status": EV.UNKNOWN,
                "reasons": [f"MALFORMED_OR_MISSING:{exc}"],
                "packet_sha256": None,
            }

    if set(market_results) != set(manifest["markets"]):
        raise PopulationError("PARTIAL_UNIVERSE_RESULT")

    return {
        "manifest": manifest, "packets": packets, "errors": errors,
        "policy_version": policy.get("policy_version"),
        "market_results": market_results,
        "universe_lineage": lineage,
        "policy_id": policy.get("policy_id"),
        "policy_packet_sha256": policy.get("packet_sha256"),
        "policy_ratified": policy.get("approval_status") == "RATIFIED",
    }


def _parse_utc(value: str):
    import datetime as dt
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def rebuild(snapshot_date: str, raw_root: Path = RAW_ROOT) -> dict:
    built = build_packets(snapshot_date, raw_root)
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "snapshot_key": snapshot_date,
        "snapshot_date": built["manifest"]["vintage_date"],
        "generated_at": built["manifest"]["downloaded_at_utc"],
        "raw_snapshot": {
            "path": f"evidence/crypto/upbit/microstructure/{snapshot_date}",
            "manifest_sha256": hashlib.sha256(
                (snapshot_dir(snapshot_date, raw_root) / "_manifest.json").read_bytes()
            ).hexdigest(),
            "market_count": built["manifest"]["market_count"],
        },
        "universe_lineage": built["universe_lineage"],
        "builder": {
            "module": "microstructure/upbit_market_evidence.py",
            "output_schema_version": EV.OUTPUT_SCHEMA_VERSION,
        },
        "policy_id": built["policy_id"],
        "policy_version": built["policy_version"],
        "policy_packet_sha256": built["policy_packet_sha256"],
        "policy_ratified": built["policy_ratified"],
        "summary": {
            "market_count": len(built["manifest"]["markets"]),
            "packet_count": len(built["packets"]),
            "error_count": len(built["errors"]),
            "pass_count": sum(
                result["status"] == "PASS" for result in built["market_results"].values()
            ),
            "unknown_count": sum(
                result["status"] == "UNKNOWN" for result in built["market_results"].values()
            ),
        },
        "errors": built["errors"],
        "market_results": built["market_results"],
        "authority": {
            "evidence_derivation_only": True,
            "decision_eligible": False,
            "entry_eligibility_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
        },
        "packets": built["packets"],
    }
    record["payload_sha256"] = payload_sha256(record)
    return record


def populate(snapshot_date: str, raw_root: Path = RAW_ROOT, data_root: Path = DATA_ROOT) -> dict:
    record = rebuild(snapshot_date, raw_root)
    target = output_path(snapshot_date, data_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationError(f"EXISTING_PACKET_UNREADABLE:{snapshot_date}:{exc}") from exc
        if existing != record:
            raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}")
        return {
            "outcome": "verified_existing", "reason": None,
            "path": str(target), "payload_sha256": record["payload_sha256"],
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "outcome": "populated", "reason": None,
        "path": str(target), "payload_sha256": record["payload_sha256"],
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    single_line = lambda value: (value or "").replace("\n", " ").replace("\r", " ")
    lines = [
        f"outcome={single_line(result.get('outcome'))}",
        f"reason={single_line(result.get('reason'))}",
        f"path={single_line(result.get('path'))}",
        f"payload_sha256={single_line(result.get('payload_sha256'))}",
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_date")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(args.snapshot_date, args.raw_root, args.data_root)
    except PopulationError as exc:
        _write_github_output({"outcome": "failed", "reason": str(exc), "path": None, "payload_sha256": None})
        print(f"P4-07 Upbit microstructure population failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"P4-07 Upbit microstructure population {result['outcome']}"
        f" date={args.snapshot_date} path={result['path']} sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
