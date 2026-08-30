#!/usr/bin/env python3
"""Build the fail-closed Crypto market-refresh status used by the Portal.

The current 7d/30d reference and the official five-axis decision are different
products.  This module keeps them separate while making their progress
observable and automatically refreshable.  It never turns current-reference
coverage into a final Regime, capital allocation, or order authority.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LATEST_PATH = ROOT / "data" / "latest_crypto_regime_refresh_status.json"
SCHEMA_VERSION = "crypto_regime_refresh_status/1"
AXES = ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class CryptoRegimeRefreshStatusError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise CryptoRegimeRefreshStatusError(f"{code}:{detail}" if detail else code)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail("MODULE_LOAD_FAILED", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION = _load_module(
    "atlas_crypto_refresh_decision",
    ROOT / "decision" / "crypto_paper_decision_snapshot.py",
)
BREADTH = _load_module(
    "atlas_crypto_refresh_breadth",
    ROOT / ".github" / "scripts" / "crypto_breadth.py",
)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CryptoRegimeRefreshStatusError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoRegimeRefreshStatusError(f"SOURCE_MISSING:{path}") from exc


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoRegimeRefreshStatusError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def _validate_authority_false(value: dict, true_key: str | None = None) -> None:
    if not isinstance(value, dict):
        fail("AUTHORITY_INVALID")
    for key, allowed in value.items():
        expected = key == true_key
        if type(allowed) is not bool or allowed is not expected:
            fail("AUTHORITY_INVALID", key)


def _validate_current_reference(packet: dict, path: Path) -> dict:
    if (
        packet.get("schema_version") != 2
        or packet.get("contract_version") != "crypto_recent_reference/v2"
        or packet.get("mode") != "CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY"
    ):
        fail("CURRENT_REFERENCE_CONTRACT_INVALID")
    path_date = path.parent.name
    if packet.get("decision_date") != path_date:
        fail("CURRENT_REFERENCE_PATH_DATE_MISMATCH")
    if not UTC.fullmatch(str(packet.get("generated_at_utc"))):
        fail("CURRENT_REFERENCE_TIME_INVALID")
    selection = packet.get("selection")
    if (
        not isinstance(selection, dict)
        or selection.get("selected_asset_count") != 100
        or selection.get("taxonomy_unknown_before_cutoff_count") != 0
        or selection.get("current_catalog_backfill_for_historical_replay_authorized") is not False
    ):
        fail("CURRENT_REFERENCE_SELECTION_INVALID")
    for window_id in ("7d", "30d"):
        window = packet.get("windows", {}).get(window_id)
        if (
            not isinstance(window, dict)
            or window.get("selected_asset_count") != 100
            or window.get("observed_asset_count") != 100
            or window.get("missing_asset_count") != 0
        ):
            fail("CURRENT_REFERENCE_WINDOW_INVALID", window_id)
    leadership = packet.get("leadership_reference")
    if (
        not isinstance(leadership, dict)
        or leadership.get("status") != "OBSERVED_REFERENCE_ONLY"
        or leadership.get("composite_code") not in {
            "BTC_LEADERSHIP", "ETH_LEADERSHIP", "BROAD_ALT_LEADERSHIP",
            "NARROW_ALT_LEADERSHIP", "MIXED_WINDOW_LEADERSHIP",
        }
    ):
        fail("CURRENT_REFERENCE_LEADERSHIP_INVALID")
    _validate_authority_false(packet.get("authority"), "reference_only")
    return copy.deepcopy(packet)


def _select_current_reference(root: Path) -> tuple[Path, dict]:
    paths = sorted((root / "data" / "observations" / "crypto_recent_reference").glob("*/packet.json"), reverse=True)
    for path in paths[:10]:
        try:
            return path, _validate_current_reference(read_json(path, "CURRENT_REFERENCE_INVALID"), path)
        except CryptoRegimeRefreshStatusError:
            continue
    fail("CURRENT_REFERENCE_NOT_FOUND")


def _decision_time(packet: dict) -> str:
    value = packet.get("captured_at_utc") or packet.get("generated_at")
    if not isinstance(value, str) or not UTC.fullmatch(value):
        fail("OFFICIAL_DECISION_TIME_INVALID")
    return value


def _select_official_decision(root: Path) -> tuple[Path, dict]:
    candidates = sorted(
        (root / "evidence" / "crypto_paper_decision").glob("*/*/*/packet.json"),
        reverse=True,
    )
    # The path hierarchy is date/time/generation, so reverse lexical order is
    # newest-first.  Full decision validation replays every retained source;
    # stop at the first valid packet instead of replaying dozens of older
    # half-hourly packets on every status refresh.
    for path in candidates[:10]:
        try:
            packet = read_json(path, "OFFICIAL_DECISION_INVALID")
            DECISION.validate_output(packet, allow_external_sources=True)
            return path, copy.deepcopy(packet)
        except Exception:
            continue
    fail("OFFICIAL_DECISION_NOT_FOUND")


def _eligible_streak(root: Path, latest_snapshot_date: str) -> int:
    try:
        cursor = dt.date.fromisoformat(latest_snapshot_date)
    except ValueError:
        fail("SNAPSHOT_DATE_INVALID")
    streak = 0
    for offset in range(60):
        date = cursor - dt.timedelta(days=offset)
        path = root / "evidence" / "crypto" / "breadth" / "raw" / date.isoformat()
        if not path.is_dir():
            break
        try:
            packet = BREADTH.build_transform(path)
        except Exception:
            break
        universe = packet.get("universe", {})
        if (
            packet.get("status") != "OBSERVED_UNCLASSIFIED"
            or universe.get("selected_asset_count") != 100
            or universe.get("taxonomy_unknown_before_cutoff") != []
        ):
            break
        streak += 1
    return streak


def _coverage(defined_axes: list[str]) -> dict:
    missing = [axis for axis in AXES if axis not in defined_axes]
    return {
        "defined_count": len(defined_axes),
        "required_count": 5,
        "ratio": f"{len(defined_axes)}/5",
        "defined_axes": list(defined_axes),
        "missing_axes": missing,
    }


def build_status(root: Path = ROOT) -> dict:
    reference_path, reference = _select_current_reference(root)
    decision_path, decision = _select_official_decision(root)
    official_axes = decision.get("crypto_regime_five_axis")
    if not isinstance(official_axes, dict) or set(official_axes) != set(AXES):
        fail("OFFICIAL_DECISION_AXES_INVALID")
    official_defined = [axis for axis in AXES if official_axes[axis].get("status") == "DEFINED"]
    current_defined = [axis for axis in AXES if axis in official_defined or axis == "LEADERSHIP"]
    if "LEADERSHIP" not in current_defined:
        fail("CURRENT_LEADERSHIP_NOT_DEFINED")

    snapshot_date = reference.get("source", {}).get("snapshot_date")
    if not isinstance(snapshot_date, str):
        fail("CURRENT_REFERENCE_SOURCE_INVALID")
    streak = _eligible_streak(root, snapshot_date)
    decision_date = dt.date.fromisoformat(reference["decision_date"])
    pilot_remaining = max(7 - streak, 0)
    primary_remaining = max(30 - streak, 0)
    pilot_date = (decision_date + dt.timedelta(days=pilot_remaining)).isoformat()
    primary_date = (decision_date + dt.timedelta(days=primary_remaining)).isoformat()

    official_coverage = _coverage(official_defined)
    current_coverage = _coverage(current_defined)
    if official_coverage["ratio"] == "5/5":
        state = "OFFICIAL_INPUTS_COMPLETE_POLICY_PENDING"
        headline = "공식 입력 5개 확인 · 코인 판정식 검증 중"
        detail = "필수 입력은 모두 확인됐지만 코인 전용 방향·점수 정책이 비준되기 전에는 Risk On/Off를 확정하지 않습니다."
    elif current_coverage["ratio"] == "5/5":
        state = "CURRENT_REFERENCE_COMPLETE_OFFICIAL_HISTORY_PENDING"
        headline = "오늘 참고 신호 5개 확인 · 공식 판정 이력 검증 중"
        detail = "오늘 흐름을 보는 5개 참고 신호는 확인됐습니다. 자동매매용 Leadership은 과거시점 보존 이력이 더 필요합니다."
    else:
        state = "CURRENT_REFERENCE_INCOMPLETE"
        headline = "오늘 코인 시장자료 수집 대기"
        detail = "필수 참고 신호가 모두 도착하지 않아 판정을 보류합니다."

    generated_at = max(reference["generated_at_utc"], _decision_time(decision))
    source_rows = [
        {
            "kind": "CURRENT_REFERENCE",
            "path": str(reference_path.relative_to(root)),
            "sha256": file_sha256(reference_path),
        },
        {
            "kind": "OFFICIAL_DECISION",
            "path": str(decision_path.relative_to(root)),
            "sha256": file_sha256(decision_path),
            "generation_id": decision["generation_id"],
        },
    ]
    generation_id = payload_sha256({
        "schema_version": SCHEMA_VERSION,
        "sources": source_rows,
        "current_coverage": current_coverage,
        "official_coverage": official_coverage,
    })
    packet = {
        "schema_version": SCHEMA_VERSION,
        "status": state,
        "generated_at": generated_at,
        "generation_id": generation_id,
        "current_reference": {
            "as_of_date": reference["decision_date"],
            "price_as_of_date": reference["price_as_of_date"],
            "coverage": current_coverage,
            "leadership_code": reference["leadership_reference"]["composite_code"],
            "mode": reference["mode"],
        },
        "official_decision": {
            "captured_at_utc": _decision_time(decision),
            "coverage": official_coverage,
            "runtime_regime": "UNKNOWN",
            "classification_status": (
                "WAIT_CRYPTO_NORMALIZATION_POLICY"
                if official_coverage["ratio"] == "5/5"
                else "WAIT_PIT_LEADERSHIP_HISTORY"
            ),
        },
        "natural_history_progress": {
            "eligible_consecutive_days": streak,
            "pilot_required_days": 7,
            "primary_required_days": 30,
            "pilot_remaining_days": pilot_remaining,
            "primary_remaining_days": primary_remaining,
            "earliest_pilot_capture_date_if_no_new_gap": pilot_date,
            "earliest_primary_capture_date_if_no_new_gap": primary_date,
            "new_missing_or_unknown_day_delays_dates": True,
        },
        "automatic_refresh": {
            "daily_capture_kst": "09:40",
            "official_decision_recheck_minutes": 30,
            "status_rebuild_kst": ["10:20", "10:50"],
            "portal_live_poll_minutes": 5,
            "watchdog_kst": "12:30",
        },
        "user_message": {"headline_ko": headline, "detail_ko": detail},
        "sources": source_rows,
        "authority": {
            "read_only_reference": True,
            "final_regime_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_status(packet: dict, root: Path = ROOT) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        fail("STATUS_SCHEMA_INVALID")
    unsigned = copy.deepcopy(packet)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or not SHA256.fullmatch(claimed) or payload_sha256(unsigned) != claimed:
        fail("STATUS_SHA_INVALID")
    _validate_authority_false(packet.get("authority"), "read_only_reference")
    expected = build_status(root)
    if packet != expected:
        fail("STATUS_REDERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def validate_expected_date(packet: dict, expected_date: str) -> dict:
    try:
        dt.date.fromisoformat(expected_date)
    except ValueError:
        fail("EXPECTED_DATE_INVALID")
    if packet.get("current_reference", {}).get("as_of_date") != expected_date:
        fail("CURRENT_REFERENCE_DATE_STALE")
    if packet.get("current_reference", {}).get("coverage", {}).get("ratio") != "5/5":
        fail("CURRENT_REFERENCE_NOT_COMPLETE")
    return copy.deepcopy(packet)


def write_packet(packet: dict, root: Path = ROOT) -> tuple[Path, Path]:
    date = packet["current_reference"]["as_of_date"]
    evidence = root / "evidence" / "regime" / "crypto_refresh_status" / date / packet["generation_id"] / "packet.json"
    latest = root / "data" / "latest_crypto_regime_refresh_status.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    latest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if evidence.exists() and evidence.read_text(encoding="utf-8") != text:
        fail("APPEND_ONLY_EVIDENCE_CONFLICT")
    evidence.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return evidence, latest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--expected-date")
    args = parser.parse_args()
    if args.verify:
        packet = validate_status(read_json(args.verify, "STATUS_INVALID"))
        if args.expected_date:
            validate_expected_date(packet, args.expected_date)
        print("PASS_CRYPTO_REGIME_REFRESH_STATUS_VERIFIED")
        return 0
    packet = build_status()
    if args.write:
        evidence, latest = write_packet(packet)
        print(json.dumps({
            "status": packet["status"],
            "current": packet["current_reference"]["coverage"]["ratio"],
            "official": packet["official_decision"]["coverage"]["ratio"],
            "evidence": str(evidence.relative_to(ROOT)),
            "latest": str(latest.relative_to(ROOT)),
        }, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
