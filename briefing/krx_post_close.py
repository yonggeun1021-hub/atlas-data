#!/usr/bin/env python3
"""Build the fail-closed P0-04 evening KRX observation packet.

This is a read-only consumer of the immutable post-close bundle. It exposes
same-day close and investor flow as ``Observed / Unconfirmed`` and never turns
them into a confirmed row, rule input, score, action, or order.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
CONTRACT_PATH = ROOT / "config" / "krx_post_close_briefing_contract.json"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KRX_CODE_RE = re.compile(r"^\d{6}$")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COLLECTOR = _load_module(
    "atlas_krx_post_close_collector",
    ROOT / "collectors" / "krx_post_close.py",
)


class PostCloseBriefingError(ValueError):
    """Fail-closed evening briefing input or output violation."""


def canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostCloseBriefingError(f"JSON_READ_FAILED:{path}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "krx_post_close_briefing/1",
        "output_schema_version": "krx_post_close_briefing_packet/1",
        "source_bundle_schema_version": 1,
        "slot": "evening",
        "earliest_consumer_time_kst": "18:00:00",
        "ready_status": "READY_OBSERVED_UNCONFIRMED",
        "unknown_status": "UNKNOWN",
        "observation_status": "observed_unconfirmed",
        "display_status": "Observed / Unconfirmed",
        "confirm_reason": "deferred_to_next_day",
        "authority": {
            "briefing_observation_only": True,
            "same_day_confirmation_authorized": False,
            "rule_input_authorized": False,
            "regime_input_authorized": False,
            "production_decision_authorized": False,
            "trading_action_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PostCloseBriefingError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise PostCloseBriefingError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise PostCloseBriefingError("EXPECTED_KST_DATE_INVALID")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PostCloseBriefingError("EXPECTED_KST_DATE_INVALID") from exc
    if parsed.isoformat() != value:
        raise PostCloseBriefingError("EXPECTED_KST_DATE_INVALID")
    return parsed


def _generated_at(value: str, expected_date: str, contract: dict) -> dt.datetime:
    if not isinstance(value, str):
        raise PostCloseBriefingError("GENERATED_AT_KST_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise PostCloseBriefingError("GENERATED_AT_KST_INVALID") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != dt.timedelta(hours=9)
        or parsed.isoformat(timespec="seconds") != value
        or parsed.date().isoformat() != expected_date
    ):
        raise PostCloseBriefingError("GENERATED_AT_KST_INVALID")
    earliest = dt.time.fromisoformat(contract["earliest_consumer_time_kst"])
    if parsed.timetz().replace(tzinfo=None) < earliest:
        raise PostCloseBriefingError("EVENING_CONSUMER_TOO_EARLY")
    return parsed


def _bundle_paths(data_root: Path, expected_date: str) -> tuple[Path, str]:
    target = Path(data_root) / "observations" / "krx_post_close" / expected_date
    logical_root = f"data/observations/krx_post_close/{expected_date}"
    return target, logical_root


def _file_inventory(target: Path, logical_root: str) -> list[dict]:
    rows = []
    for path in sorted(item for item in target.rglob("*") if item.is_file()):
        relative = path.relative_to(target).as_posix()
        rows.append(
            {
                "path": f"{logical_root}/{relative}",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return rows


def _empty_bundle(logical_root: str) -> dict:
    return {
        "root_path": logical_root,
        "index_path": f"{logical_root}/index.json",
        "source_path": f"{logical_root}/source.json",
        "file_count": None,
        "files": [],
        "bundle_sha256": None,
    }


def _unknown_packet(
    expected_date: str,
    generated_at_kst: str,
    contract: dict,
    logical_root: str,
    reason: str,
) -> dict:
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "slot": contract["slot"],
        "expected_kst_date": expected_date,
        "generated_at_kst": generated_at_kst,
        "status": contract["unknown_status"],
        "observation_status": "unknown",
        "display_status": "Unknown",
        "decision_eligible": False,
        "reason_codes": [reason],
        "bundle": _empty_bundle(logical_root),
        "symbols": [],
        "summary": {
            "observed_symbol_count": None,
            "decision_eligible_symbol_count": None,
            "confirmed_same_day_count": None,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "POST_CLOSE_OBSERVATION_MISSING_OR_INVALID",
            "NO_VALUE_OR_NEUTRAL_SUBSTITUTION_AUTHORIZED",
            "SAME_DAY_CONFIRMATION_DEFERRED_TO_NEXT_DAY",
        ],
    }


def _ready_packet(
    expected_date: str,
    generated_at_kst: str,
    contract: dict,
    target: Path,
    logical_root: str,
) -> dict:
    index = _read_json(target / "index.json")
    files = _file_inventory(target, logical_root)
    rows = []
    for symbol in index["symbols"]:
        view = _read_json(target / "symbols" / f"{symbol}.json")
        observed = view["observed_row"]
        rows.append(
            {
                "symbol": symbol,
                "name": view.get("name"),
                "latest_observed_day": view["latest_observed_day"],
                "latest_trading_day": view["latest_trading_day"],
                "display_status": contract["display_status"],
                "observation_status": observed["observation_status"],
                "decision_eligible": observed["decision_eligible"],
                "confirmed": observed["confirmed"],
                "confirm_reason": observed["confirm_reason"],
                "observed_at_kst": observed["observed_at_kst"],
                "source_snapshot_sha256": observed[
                    "source_snapshot_sha256"
                ],
                "observed_row": copy.deepcopy(observed),
                "decision_boundary": copy.deepcopy(
                    view["decision_boundary"]
                ),
            }
        )
    bundle = {
        "root_path": logical_root,
        "index_path": f"{logical_root}/index.json",
        "source_path": f"{logical_root}/source.json",
        "file_count": len(files),
        "files": files,
        "bundle_sha256": payload_sha256(files),
    }
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "slot": contract["slot"],
        "expected_kst_date": expected_date,
        "generated_at_kst": generated_at_kst,
        "status": contract["ready_status"],
        "observation_status": contract["observation_status"],
        "display_status": contract["display_status"],
        "decision_eligible": False,
        "reason_codes": [],
        "bundle": bundle,
        "symbols": rows,
        "summary": {
            "observed_symbol_count": len(rows),
            "decision_eligible_symbol_count": 0,
            "confirmed_same_day_count": 0,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "SAME_DAY_CONFIRMATION_DEFERRED_TO_NEXT_DAY",
            "RULE_REGIME_AND_ACTION_INPUT_NOT_AUTHORIZED",
            "LIVE_18_00_BRIEFING_DELIVERY_NOT_YET_PROVEN",
        ],
    }


def build_packet(
    data_root: Path,
    expected_date: str,
    generated_at_kst: str,
    contract: dict | None = None,
) -> dict:
    contract = (
        _validate_contract(contract) if contract is not None else load_contract()
    )
    _date(expected_date)
    _generated_at(generated_at_kst, expected_date, contract)
    target, logical_root = _bundle_paths(data_root, expected_date)
    if not COLLECTOR.check_bundle(expected_date, data_root=Path(data_root)):
        packet = _unknown_packet(
            expected_date,
            generated_at_kst,
            contract,
            logical_root,
            "POST_CLOSE_BUNDLE_MISSING_OR_INVALID",
        )
    else:
        try:
            packet = _ready_packet(
                expected_date,
                generated_at_kst,
                contract,
                target,
                logical_root,
            )
            packet["packet_sha256"] = payload_sha256(packet)
            return validate_packet(packet, contract)
        except (
            KeyError,
            OSError,
            TypeError,
            json.JSONDecodeError,
            PostCloseBriefingError,
        ):
            packet = _unknown_packet(
                expected_date,
                generated_at_kst,
                contract,
                logical_root,
                "POST_CLOSE_BRIEFING_ADAPTER_VALIDATION_FAILED",
            )
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def _valid_file_inventory(bundle: dict, expected_date: str) -> bool:
    files = bundle.get("files")
    if not isinstance(files, list) or not files:
        return False
    prefix = f"data/observations/krx_post_close/{expected_date}/"
    paths = []
    for row in files:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "sha256"}
            or not isinstance(row.get("path"), str)
            or not row["path"].startswith(prefix)
            or SHA256_RE.fullmatch(str(row.get("sha256"))) is None
        ):
            return False
        paths.append(row["path"])
    return paths == sorted(set(paths))


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = (
        _validate_contract(contract) if contract is not None else load_contract()
    )
    fields = {
        "schema_version",
        "contract_version",
        "slot",
        "expected_kst_date",
        "generated_at_kst",
        "status",
        "observation_status",
        "display_status",
        "decision_eligible",
        "reason_codes",
        "bundle",
        "symbols",
        "summary",
        "authority",
        "unresolved_boundaries",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise PostCloseBriefingError("PACKET_FIELDS_MISMATCH")
    expected_date = packet.get("expected_kst_date")
    _date(expected_date)
    _generated_at(packet.get("generated_at_kst"), expected_date, contract)
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("slot") != contract["slot"]
        or type(packet.get("decision_eligible")) is not bool
        or packet["decision_eligible"] is not False
        or packet.get("authority") != contract["authority"]
        or not isinstance(packet.get("reason_codes"), list)
        or not isinstance(packet.get("unresolved_boundaries"), list)
    ):
        raise PostCloseBriefingError("PACKET_IDENTITY_INVALID")
    bundle = packet.get("bundle")
    symbols = packet.get("symbols")
    summary = packet.get("summary")
    bundle_fields = {
        "root_path",
        "index_path",
        "source_path",
        "file_count",
        "files",
        "bundle_sha256",
    }
    summary_fields = {
        "observed_symbol_count",
        "decision_eligible_symbol_count",
        "confirmed_same_day_count",
    }
    if (
        not isinstance(bundle, dict)
        or set(bundle) != bundle_fields
        or not isinstance(symbols, list)
        or not isinstance(summary, dict)
        or set(summary) != summary_fields
    ):
        raise PostCloseBriefingError("PACKET_SECTIONS_INVALID")
    logical_root = f"data/observations/krx_post_close/{expected_date}"
    if (
        bundle.get("root_path") != logical_root
        or bundle.get("index_path") != f"{logical_root}/index.json"
        or bundle.get("source_path") != f"{logical_root}/source.json"
    ):
        raise PostCloseBriefingError("PACKET_BUNDLE_PATH_INVALID")

    if packet.get("status") == contract["unknown_status"]:
        if (
            packet.get("observation_status") != "unknown"
            or packet.get("display_status") != "Unknown"
            or not packet["reason_codes"]
            or symbols != []
            or any(value is not None for value in summary.values())
            or bundle["file_count"] is not None
            or bundle["files"] != []
            or bundle["bundle_sha256"] is not None
        ):
            raise PostCloseBriefingError("UNKNOWN_PACKET_INVALID")
    elif packet.get("status") == contract["ready_status"]:
        if (
            packet.get("observation_status")
            != contract["observation_status"]
            or packet.get("display_status") != contract["display_status"]
            or packet["reason_codes"] != []
            or not symbols
            or not _valid_file_inventory(bundle, expected_date)
            or bundle.get("file_count") != len(bundle["files"])
            or bundle.get("bundle_sha256") != payload_sha256(bundle["files"])
        ):
            raise PostCloseBriefingError("READY_PACKET_INVALID")
        symbol_fields = {
            "symbol",
            "name",
            "latest_observed_day",
            "latest_trading_day",
            "display_status",
            "observation_status",
            "decision_eligible",
            "confirmed",
            "confirm_reason",
            "observed_at_kst",
            "source_snapshot_sha256",
            "observed_row",
            "decision_boundary",
        }
        observed_fields = {
            "observation_status",
            "decision_eligible",
            "confirmed",
            "confirm_reason",
            "trading_day",
            "observed_at_kst",
            "source_snapshot_sha256",
            "close",
            "open",
            "high",
            "low",
            "volume",
            "change_pct",
            "net_value",
            "net_volume",
        }
        boundary_fields = {
            "same_day_confirmation",
            "history_basis",
            "sma20",
            "sma20_basis",
            "sma20_through",
            "sma20_status",
        }
        source_path = f"{logical_root}/source.json"
        source_file = next(
            (row for row in bundle["files"] if row["path"] == source_path),
            None,
        )
        if source_file is None:
            raise PostCloseBriefingError("READY_SOURCE_FILE_MISSING")
        seen = []
        for row in symbols:
            observed = row.get("observed_row") if isinstance(row, dict) else None
            boundary = (
                row.get("decision_boundary") if isinstance(row, dict) else None
            )
            observed_at = None
            latest_confirmed = None
            try:
                observed_at = dt.datetime.fromisoformat(
                    row.get("observed_at_kst", "")
                )
                latest_confirmed = _date(row.get("latest_trading_day"))
            except (TypeError, ValueError, PostCloseBriefingError):
                pass
            if (
                not isinstance(row, dict)
                or set(row) != symbol_fields
                or not isinstance(observed, dict)
                or set(observed) != observed_fields
                or not isinstance(boundary, dict)
                or set(boundary) != boundary_fields
                or KRX_CODE_RE.fullmatch(str(row.get("symbol"))) is None
                or row.get("latest_observed_day") != expected_date
                or latest_confirmed is None
                or latest_confirmed >= _date(expected_date)
                or row.get("display_status") != contract["display_status"]
                or row.get("observation_status")
                != contract["observation_status"]
                or row.get("decision_eligible") is not False
                or row.get("confirmed") is not False
                or row.get("confirm_reason") != contract["confirm_reason"]
                or observed.get("decision_eligible") is not False
                or observed.get("confirmed") is not False
                or observed.get("trading_day") != expected_date
                or observed.get("observation_status")
                != contract["observation_status"]
                or observed.get("confirm_reason") != contract["confirm_reason"]
                or observed.get("observed_at_kst")
                != row.get("observed_at_kst")
                or observed_at is None
                or observed_at.utcoffset() != dt.timedelta(hours=9)
                or observed_at.date().isoformat() != expected_date
                or observed.get("source_snapshot_sha256")
                != row.get("source_snapshot_sha256")
                or row.get("source_snapshot_sha256")
                != source_file["sha256"]
                or SHA256_RE.fullmatch(
                    str(row.get("source_snapshot_sha256"))
                )
                is None
                or boundary.get("history_basis") != "confirmed_only"
                or boundary.get("same_day_confirmation") != "next_day"
                or boundary.get("sma20_through")
                != row.get("latest_trading_day")
            ):
                raise PostCloseBriefingError("READY_SYMBOL_INVALID")
            seen.append(row.get("symbol"))
        if seen != sorted(set(seen)):
            raise PostCloseBriefingError("READY_SYMBOL_ORDER_INVALID")
        expected_paths = {
            f"{logical_root}/index.json",
            source_path,
            *(
                f"{logical_root}/symbols/{symbol}.json"
                for symbol in seen
            ),
        }
        if {row["path"] for row in bundle["files"]} != expected_paths:
            raise PostCloseBriefingError("READY_FILE_INVENTORY_INVALID")
        expected_summary = {
            "observed_symbol_count": len(symbols),
            "decision_eligible_symbol_count": 0,
            "confirmed_same_day_count": 0,
        }
        if summary != expected_summary:
            raise PostCloseBriefingError("READY_SUMMARY_INVALID")
    else:
        raise PostCloseBriefingError("PACKET_STATUS_INVALID")

    unsigned = copy.deepcopy(packet)
    digest = unsigned.pop("packet_sha256")
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise PostCloseBriefingError("PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def _inside_repository(path: Path) -> bool:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def write_packet(packet: dict, output: Path) -> Path:
    output = Path(output)
    if _inside_repository(output):
        raise PostCloseBriefingError("TRACKED_OUTPUT_FORBIDDEN")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def run(
    data_root: Path,
    expected_date: str,
    generated_at_kst: str,
    output: Path,
) -> int:
    try:
        packet = build_packet(data_root, expected_date, generated_at_kst)
        target = write_packet(packet, output)
    except PostCloseBriefingError as exc:
        print(f"P0-04 evening briefing failed: {exc}")
        return 1
    print(f"P0-04 evening briefing status={packet['status']} path={target}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--generated-at-kst", required=True)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(
        args.data_root,
        args.date,
        args.generated_at_kst,
        args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
