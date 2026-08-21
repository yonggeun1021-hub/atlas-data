#!/usr/bin/env python3
"""Evaluate the P0-03 06:55 briefing readiness contract.

The gate deliberately re-reads the current collector files.  Cached
``freshness`` or ``overall`` fields in the published read model are never the
first authority for today's readiness decision.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SOURCE_NAMES = ("krx", "dart", "sec")

EXIT_CODES = {
    "data_ready_read_model_ready": 0,
    "data_ready_read_model_degraded": 2,
    "data_not_ready": 3,
    "unknown_manual_inspection_required": 4,
}


class ContractError(RuntimeError):
    pass


def load_json(path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"missing_or_unreadable:{path}") from exc

    try:
        obj = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"incomplete_or_invalid_json:{path}") from exc

    if not isinstance(obj, dict):
        raise ContractError(f"top_level_not_object:{path}")

    return obj, hashlib.sha256(raw).hexdigest()


def require_summary(name, obj):
    summary = obj.get("summary")
    if not isinstance(summary, dict):
        raise ContractError(f"{name}:summary_missing")

    ok = summary.get("ok")
    failed = summary.get("failed")
    if type(ok) is not int or type(failed) is not int:
        raise ContractError(f"{name}:summary_invalid")

    return {"ok": ok, "failed": failed}


def result_payload(expected_date):
    return {
        "schema_version": 1,
        "expected_kst_date": expected_date,
        "classification": None,
        "data_ready": False,
        "read_model_ready": False,
        "manual_inspection_required": False,
        "recovery_action": None,
        "sources": {},
        "read_model": {
            "status_path": "data/briefing/step0_status.json",
            "health_path": "data/briefing_status.json",
            "compact_views": {},
        },
        "reasons": [],
    }


def finish(payload, classification, recovery_action, reasons):
    payload["classification"] = classification
    payload["data_ready"] = classification.startswith("data_ready_")
    payload["read_model_ready"] = (
        classification == "data_ready_read_model_ready"
    )
    payload["manual_inspection_required"] = (
        classification == "unknown_manual_inspection_required"
    )
    payload["recovery_action"] = recovery_action
    payload["reasons"] = sorted(set(reasons))
    return payload


def persist_health(payload, data_root=DATA):
    """Atomically persist the final gate result, not the builder's guess."""
    path = data_root / "briefing_status.json"
    temporary = data_root / f".briefing_status.tmp.{os.getpid()}"
    status = (
        "ready"
        if payload["classification"] == "data_ready_read_model_ready"
        else "read_model_degraded"
        if payload["classification"] == "data_ready_read_model_degraded"
        else "data_not_ready"
        if payload["classification"] == "data_not_ready"
        else "unknown_manual_inspection_required"
    )
    artifact = {
        "schema_version": 1,
        "expected_kst_date": payload["expected_kst_date"],
        "data_ready": payload["data_ready"],
        "read_model_ready": payload["read_model_ready"],
        "status": status,
        "error": (
            None
            if not payload["reasons"]
            else ";".join(payload["reasons"])
        ),
        "classification": payload["classification"],
        "manual_inspection_required": payload[
            "manual_inspection_required"
        ],
        "recovery_action": payload["recovery_action"],
        "reasons": payload["reasons"],
        "sources": payload["sources"],
        "read_model": payload["read_model"],
    }
    data_root.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def validate_compact_views(data_root, name, source, source_sha, reasons):
    expected = {
        str(symbol)
        for symbol, stock in source.get("stocks", {}).items()
        if isinstance(stock, dict)
    }
    directory = data_root / "briefing" / name

    if not directory.is_dir():
        reasons.append(f"{name}:compact_directory_missing")
        return {"expected": len(expected), "valid": 0}

    actual = {path.stem for path in directory.glob("*.json")}
    if actual != expected:
        reasons.append(f"{name}:compact_inventory_mismatch")

    valid = 0
    for symbol in sorted(expected):
        path = directory / f"{symbol}.json"
        try:
            compact, _ = load_json(path)
        except ContractError as exc:
            reasons.append(str(exc))
            continue

        compact_source = compact.get("source")
        if not isinstance(compact_source, dict):
            reasons.append(f"{name}:{symbol}:source_missing")
            continue

        if compact.get("symbol") != symbol:
            reasons.append(f"{name}:{symbol}:symbol_mismatch")
            continue

        expected_schema = 2
        if compact.get("schema_version") != expected_schema:
            reasons.append(f"{name}:{symbol}:schema_version_mismatch")
            continue

        if compact_source.get("source_sha256") != source_sha:
            reasons.append(f"{name}:{symbol}:source_sha_mismatch")
            continue

        if (
            compact_source.get("collected_for_kst_date")
            != source.get("collected_for_kst_date")
        ):
            reasons.append(f"{name}:{symbol}:source_date_mismatch")
            continue

        valid += 1

    return {"expected": len(expected), "valid": valid}


def validate_read_model(data_root, expected_date, sources, hashes):
    reasons = []
    step0_path = data_root / "briefing" / "step0_status.json"
    health_path = data_root / "briefing_status.json"

    try:
        step0, _ = load_json(step0_path)
    except ContractError as exc:
        reasons.append(str(exc))
        step0 = None

    if step0 is not None:
        if step0.get("schema_version") != 1:
            reasons.append("read_model:schema_version_mismatch")
        if step0.get("expected_kst_date") != expected_date:
            reasons.append("read_model:expected_kst_date_mismatch")

        expected_inventory = {
            "date_basis": "KST",
            "authority_path": "data/briefing/step0_status.json",
            "health_path": "data/briefing_status.json",
            "compact_path_templates": [
                "data/briefing/krx/{SYMBOL}.json",
                "data/briefing/dart/{SYMBOL}.json",
                "data/briefing/sec/{SYMBOL}.json",
            ],
            "optional_evidence_sources": [
                "data/latest_dart_content.json",
                "data/latest_sec_content.json",
            ],
        }
        actual_inventory = step0.get("read_model_inventory")
        if (
            not isinstance(actual_inventory, dict)
            or set(actual_inventory) - {"operations_telemetry_sources"}
            != set(expected_inventory)
            or any(
                actual_inventory.get(key) != expected_value
                for key, expected_value in expected_inventory.items()
            )
        ):
            reasons.append("read_model:inventory_mismatch")

        collectors = step0.get("collectors")
        if not isinstance(collectors, dict):
            reasons.append("read_model:collectors_missing")
        else:
            for name in SOURCE_NAMES:
                entry = collectors.get(name)
                if not isinstance(entry, dict):
                    reasons.append(f"read_model:{name}:collector_missing")
                    continue
                if entry.get("collected_for_kst_date") != expected_date:
                    reasons.append(f"read_model:{name}:date_mismatch")
                if entry.get("failed") != 0:
                    reasons.append(f"read_model:{name}:failed_not_zero")
                if entry.get("source_sha256") != hashes[name]:
                    reasons.append(f"read_model:{name}:source_sha_mismatch")
                if entry.get("freshness") != "fresh":
                    reasons.append(f"read_model:{name}:freshness_not_fresh")

        if step0.get("overall") != "pass":
            reasons.append("read_model:overall_not_pass")

    try:
        health, _ = load_json(health_path)
    except ContractError as exc:
        reasons.append(str(exc))
        health = None

    if health is not None:
        if health.get("schema_version") != 1:
            reasons.append("health:schema_version_mismatch")
        if health.get("expected_kst_date") != expected_date:
            reasons.append("health:expected_kst_date_mismatch")
        if health.get("data_ready") is not True:
            reasons.append("health:data_ready_not_true")
        if health.get("read_model_ready") is not True:
            reasons.append("health:read_model_ready_not_true")
        if health.get("status") != "ready":
            reasons.append("health:status_not_ready")
        if health.get("error") is not None:
            reasons.append("health:error_not_null")

    compact = {}
    for name in ("krx", "dart", "sec"):
        stocks = sources[name].get("stocks")
        if not isinstance(stocks, dict):
            reasons.append(f"{name}:stocks_missing")
            compact[name] = {"expected": 0, "valid": 0}
            continue
        compact[name] = validate_compact_views(
            data_root,
            name,
            sources[name],
            hashes[name],
            reasons,
        )

    return reasons, compact


def evaluate(expected_date, data_root=DATA):
    payload = result_payload(expected_date)
    if not expected_date:
        return finish(
            payload,
            "unknown_manual_inspection_required",
            "manual_inspection",
            ["expected_kst_date_missing"],
        )
    try:
        dt.date.fromisoformat(expected_date)
    except (TypeError, ValueError):
        return finish(
            payload,
            "unknown_manual_inspection_required",
            "manual_inspection",
            ["expected_kst_date_invalid"],
        )

    sources = {}
    hashes = {}
    summaries = {}

    try:
        for name in SOURCE_NAMES:
            obj, sha = load_json(data_root / f"latest_{name}.json")
            summary = require_summary(name, obj)
            sources[name] = obj
            hashes[name] = sha
            summaries[name] = summary
            payload["sources"][name] = {
                "path": f"data/latest_{name}.json",
                "collected_for_kst_date": obj.get(
                    "collected_for_kst_date"
                ),
                "ok": summary["ok"],
                "failed": summary["failed"],
                "source_sha256": sha,
            }
    except ContractError as exc:
        return finish(
            payload,
            "unknown_manual_inspection_required",
            "manual_inspection",
            [str(exc)],
        )

    data_reasons = []
    for name in SOURCE_NAMES:
        if sources[name].get("collected_for_kst_date") != expected_date:
            data_reasons.append(f"{name}:collector_date_mismatch")
        if summaries[name]["failed"] != 0:
            data_reasons.append(f"{name}:collector_failed_not_zero")

    if data_reasons:
        return finish(
            payload,
            "data_not_ready",
            "workflow_dispatch",
            data_reasons,
        )

    read_model_reasons, compact = validate_read_model(
        data_root,
        expected_date,
        sources,
        hashes,
    )
    payload["read_model"]["compact_views"] = compact

    if read_model_reasons:
        return finish(
            payload,
            "data_ready_read_model_degraded",
            "repair_read_model_only",
            read_model_reasons,
        )

    return finish(
        payload,
        "data_ready_read_model_ready",
        "none",
        [],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", required=True)
    parser.add_argument("--data-root", type=Path, default=DATA)
    parser.add_argument("--write-health", action="store_true")
    args = parser.parse_args()

    payload = evaluate(args.today, args.data_root)
    if args.write_health:
        persist_health(payload, args.data_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_CODES[payload["classification"]]


if __name__ == "__main__":
    raise SystemExit(main())
