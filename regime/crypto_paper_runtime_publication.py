#!/usr/bin/env python3
"""Rebuild the Crypto PAPER runtime decision from retained, committed raw bytes.

This mirrors the KR PAPER publication pattern (PR #696): every axis is
rederived from the append-only raw captures by the existing owner transforms,
the unchanged common-v1 replay runs over the ratified runtime chain, and the
crypto-only PROVISIONAL_FORWARD_ACCEPTANCE is re-evaluated on every build.  A
missing capture, a failed transform, a validator failure or an unaccepted
history publishes UNKNOWN; nothing is carried forward.

Reads committed evidence only.  It calls no provider, downloads nothing, and
opens no strategy, capital, order, production, trading or REAL authority.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from regime import crypto_paper_runtime as RUNTIME


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "latest_crypto_paper_runtime_decision.json"
BTC_RAW = Path("evidence/crypto/btc/raw")
BREADTH_RAW = Path("evidence/crypto/breadth/raw")
STABLECOIN_RAW = Path("evidence/stablecoin/raw")
EVIDENCE_ROOTS = (BTC_RAW, BREADTH_RAW, STABLECOIN_RAW)


class CryptoPaperRuntimePublicationError(ValueError):
    """The display-only decision could not be reproduced or would escalate authority."""


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TREND = _load("atlas_crypto_runtime_btc_trend", ".github/scripts/btc_trend.py")
RISK = _load("atlas_crypto_runtime_btc_risk", ".github/scripts/btc_risk.py")
BREADTH = _load("atlas_crypto_runtime_breadth", ".github/scripts/crypto_breadth.py")
LEADERSHIP = _load("atlas_crypto_runtime_leadership", ".github/scripts/crypto_leadership.py")
STABLECOIN = _load("atlas_crypto_runtime_stablecoin", ".github/scripts/stablecoin_net_issuance.py")


def _code(exc: Exception) -> str:
    text = str(exc).split(":", 1)[0].strip()
    return text if text and text.replace("_", "").isalnum() and text.isupper() else type(exc).__name__


def _btc(root: Path, decision_date: dt.date) -> dict:
    path = root / BTC_RAW / decision_date.isoformat()
    if not path.is_dir():
        return {"error": "SNAPSHOT_MISSING"}
    try:
        trend = TREND.build_transform(path)
        risk = RISK.build_transform(path)
    except Exception as exc:  # owner transforms fail closed with their own codes
        return {"error": _code(exc)}
    point = risk["risk_point"]
    return {
        "vintage_date": trend["lineage"]["vintage_date"],
        "available_at": trend["lineage"]["available_at"],
        "latest_finalized_day": trend["latest_finalized_day"],
        "trend_latest_finalized_day": trend["latest_finalized_day"],
        "risk_latest_finalized_day": risk["latest_finalized_day"],
        "trend_category": trend["direction"],
        "trend_source_sha256": trend["lineage"]["source_sha256"],
        "risk_source_sha256": risk["lineage"]["source_sha256"],
        "risk_transform_version": risk["transform_version"],
        "realized_vol_annualized_fraction": point["realized_volatility"]["annualized_fraction"],
        "current_drawdown_fraction": point["drawdown"]["current_fraction"],
    }


def _breadth(root: Path, decision_date: dt.date) -> dict:
    path = root / BREADTH_RAW / decision_date.isoformat()
    if not path.is_dir():
        return {"error": "SNAPSHOT_MISSING"}
    try:
        packet = BREADTH.build_transform(path)
    except Exception as exc:
        return {"error": _code(exc)}
    participation = packet.get("alt_participation") or {}
    return {
        "vintage_date": packet["lineage"]["vintage_date"],
        "available_at": packet["lineage"]["available_at"],
        "as_of_date": packet["as_of_date"],
        "status": packet["status"],
        "unknown_reason": packet["unknown_reason"],
        "advance_fraction": participation.get("advance_fraction"),
        "manifest_sha256": packet["lineage"]["manifest_sha256"],
    }


def _stablecoin(root: Path, decision_date: dt.date) -> dict:
    path = root / STABLECOIN_RAW / decision_date.isoformat()
    if not path.is_dir():
        return {"error": "SNAPSHOT_MISSING"}
    try:
        packet = STABLECOIN.build_transform(path)
    except Exception as exc:
        return {"error": _code(exc)}
    rows = packet.get("rows") or []
    if not rows:
        return {"error": "ROWS_EMPTY"}
    row = max(rows, key=lambda item: item["observation_date"])
    return {
        "vintage_date": packet["lineage"]["vintage_date"],
        "available_at": packet["lineage"]["available_at"],
        "observation_date": row["observation_date"],
        "daily_status": row["daily_status"],
        "weekly_status": row["weekly_status"],
        "daily_net_issuance": row["daily_net_issuance_native_usd_peg"],
        "weekly_net_issuance": row["weekly_net_issuance_native_usd_peg"],
        "response_sha256": packet["source"]["response_sha256"],
    }


def _window(window: dict) -> dict:
    summary = {
        "window_id": window["window_id"],
        "status": window["status"],
        "unknown_reason": window["unknown_reason"],
        "start_date": window["window"]["start_date"],
        "end_date": window["window"]["end_date"],
        "source_unknown_reasons": sorted({p["unknown_reason"] for p in window["source_unknown_points"]}),
        "sector_chain_unknown_reason": None,
        "point_available_at": [p["lineage"]["available_at"] for p in window["daily_points"]],
        "last_manifest_sha256": (
            window["daily_points"][-1]["lineage"]["manifest_sha256"] if window["daily_points"] else None
        ),
        "bucket_gross_returns": None,
        "alt_asset_gross_returns": None,
    }
    if window["status"] == "OBSERVED_UNCLASSIFIED":
        groups = window["group_relative_strength"]
        summary["sector_chain_unknown_reason"] = groups["sector_chain"]["unknown_reason"]
        buckets = {row["group_id"]: row for row in groups["bucket"]}
        if all(row["status"] == "OBSERVED_UNCLASSIFIED" for row in buckets.values()):
            summary["bucket_gross_returns"] = {
                key: row["cumulative_gross_return"] for key, row in sorted(buckets.items())
            }
        summary["alt_asset_gross_returns"] = [
            row["cumulative_gross_return"] for row in window["asset_relative_strength"]
            if row["canonical_asset_id"] not in {"BTC", "ETH"}
        ]
    return summary


def _leadership(root: Path, decision_date: dt.date) -> dict:
    try:
        packet = LEADERSHIP.build_transform(
            root / BREADTH_RAW, end_date=(decision_date - dt.timedelta(days=1)).isoformat())
    except Exception as exc:
        return {"error": _code(exc)}
    windows = {row["window_id"]: _window(row) for row in packet["windows"]}
    if set(windows) != {RUNTIME.PILOT, RUNTIME.PRIMARY}:
        return {"error": "LEADERSHIP_WINDOWS_INVALID"}
    return {"windows": windows}


def collect_day_record(root: Path, decision_date: dt.date) -> dict:
    return {
        "decision_date": decision_date.isoformat(),
        "btc": _btc(root, decision_date),
        "breadth": _breadth(root, decision_date),
        "stablecoin": _stablecoin(root, decision_date),
        "leadership": _leadership(root, decision_date),
    }


def collect_day_records(root: Path, start: dt.date, end: dt.date) -> dict:
    records, cursor = {}, start
    while cursor <= end:
        records[cursor.isoformat()] = collect_day_record(root, cursor)
        cursor += dt.timedelta(days=1)
    return records


def evidence_class(root: Path) -> str:
    """LIVE_NATURAL only for this repository's committed, unmodified raw captures."""
    if Path(root).resolve() != ROOT.resolve():
        return "SYNTHETIC_OFFLINE_FIXTURE"
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *map(str, EVIDENCE_ROOTS)],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=120,
        ).stdout
        tracked = subprocess.run(
            ["git", "ls-files", "--", *map(str, EVIDENCE_ROOTS)],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "SYNTHETIC_OFFLINE_FIXTURE"
    if status.strip() or not tracked.strip():
        return "SYNTHETIC_OFFLINE_FIXTURE"
    return RUNTIME.LIVE_NATURAL


def kraken_receipt_raw(root: Path) -> bytes | None:
    try:
        policy = RUNTIME.load_policy(root)
    except (RUNTIME.CryptoPaperRuntimeError, OSError, KeyError, ValueError):
        return None
    path = root / policy["acceptance"]["replaced_condition_6"]["receipt_path"]
    return path.read_bytes() if path.is_file() else None


def build_decision(*, evaluation_at: str, code_revision: str, root: Path = ROOT) -> dict:
    now = RUNTIME.instant(evaluation_at, "EVALUATION_TIME_INVALID")
    current = RUNTIME.current_decision_date(now)
    try:
        start = RUNTIME.day(RUNTIME.load_policy(root)["finalized_packet"]["runtime_chain_start_date"],
                            "CHAIN_START_INVALID")
    except (RUNTIME.CryptoPaperRuntimeError, OSError, KeyError, ValueError):
        start = current
    records = collect_day_records(root, start, current) if start <= current else {}
    rerun = collect_day_records(root, start, current) if start <= current else {}
    result = RUNTIME.evaluate_crypto_paper_runtime(
        evaluation_at=evaluation_at, code_revision=code_revision,
        day_records=records, rerun_day_records=rerun,
        evidence_class=evidence_class(root), kraken_receipt_raw=kraken_receipt_raw(root), root=root,
    )
    authority = result.get("authority")
    if not isinstance(authority, dict) or any(
        value is not False for key, value in authority.items() if key != "paper_runtime_display_authorized"
    ):
        raise CryptoPaperRuntimePublicationError("AUTHORITY_ESCALATION")
    if authority.get("paper_runtime_display_authorized") is not result.get("runtime_decision_available"):
        raise CryptoPaperRuntimePublicationError("DISPLAY_AUTHORITY_MISMATCH")
    if result["runtime_decision_available"] is not (result["runtime_regime"] != "UNKNOWN"):
        raise CryptoPaperRuntimePublicationError("RUNTIME_AVAILABILITY_MISMATCH")
    return copy.deepcopy(result)


def published_for_current_date(output: Path, evaluation_at: str) -> bool:
    """True when ``output`` already holds this UTC day's verified decision.

    A retry slot must not republish the same finalized packet with a new
    evaluation time.  The retained packet counts only if its decision date is
    the date being evaluated and it still rederives byte-for-byte from its own
    evaluation_at and code_revision; otherwise the caller rebuilds.
    """
    try:
        raw = Path(output).read_bytes()
        packet = json.loads(raw)
        current = RUNTIME.current_decision_date(RUNTIME.instant(evaluation_at, "EVALUATION_TIME_INVALID"))
        if not isinstance(packet, dict) or packet.get("current_decision_date") != current.isoformat():
            return False
        rebuilt = build_decision(evaluation_at=packet["evaluation_at"], code_revision=packet["code_revision"])
    except (OSError, ValueError, KeyError, TypeError, RUNTIME.CryptoPaperRuntimeError):
        return False
    return RUNTIME.pretty_bytes(rebuilt) == raw


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-at", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--published-for-current-date", action="store_true",
                        help="exit 0 if --output already holds this UTC day's verified decision, else 3")
    args = parser.parse_args(argv)
    if args.published_for_current_date:
        published = published_for_current_date(args.output, args.evaluation_at)
        print(json.dumps({"output": str(args.output), "published_for_current_date": published}, sort_keys=True))
        return 0 if published else 3
    expected = RUNTIME.pretty_bytes(
        build_decision(evaluation_at=args.evaluation_at, code_revision=args.code_revision)
    )
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != expected:
            raise CryptoPaperRuntimePublicationError("PUBLISHED_DECISION_BYTES_MISMATCH")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(expected)
    temporary.replace(args.output)
    packet = json.loads(expected)
    print(json.dumps({
        "output": str(args.output), "sha256": hashlib.sha256(expected).hexdigest(),
        "runtime_regime": packet["runtime_regime"], "decision_status": packet["decision_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
