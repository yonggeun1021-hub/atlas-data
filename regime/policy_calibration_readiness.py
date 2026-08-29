#!/usr/bin/env python3
"""Inventory evidence readiness for the ratified P1-COM-05 B+C process.

This command replays retained source evidence for every currently qualified
live-axis binding. It reports which markets have all five required axes and
how much point-in-time source history is retained. It does not create policy
values, classification candidates, replay cases, recommendations, or trading
authority.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "regime_policy_calibration_readiness_contract.json"
SCHEMA_VERSION = "regime_policy_calibration_readiness/v2"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIVE_AXIS = _load_module(
    "atlas_live_axis_for_calibration_readiness", "regime/live_axis_adapter.py"
)
MINIMUM_COVERAGE = _load_module(
    "atlas_minimum_coverage_for_calibration_readiness", "regime/minimum_coverage.py"
)
POPULATION = _load_module(
    "atlas_policy_population_for_calibration_readiness",
    "regime/policy_candidate_population.py",
)


class PolicyCalibrationReadinessError(ValueError):
    """Canonical calibration-readiness evidence is invalid."""


def fail(code: str, detail: str = "") -> None:
    suffix = f":{detail}" if detail else ""
    raise PolicyCalibrationReadinessError(f"{code}{suffix}")


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_bytes(path: Path, code: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise PolicyCalibrationReadinessError(code) from exc


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(_read_bytes(path, code).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyCalibrationReadinessError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def _safe(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        fail("CONTRACT_PATH_INVALID", relative)
    root = Path(root).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PolicyCalibrationReadinessError("CONTRACT_PATH_INVALID") from exc
    return path


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": SCHEMA_VERSION,
        "contract_mode": "SHADOW_CALIBRATION_READINESS_ONLY",
        "methodology_status": "RATIFIED_PROCESS_B_MARKET_ROLLOUT_C",
        "bound_contracts": {
            "live_axis_adapter": {
                "path": "config/regime_live_axis_adapter_contract.json",
                "sha256": "2028a4db0c218b6cac20e1825c0ba13870ad28fff0d2d22a4acb6487e5954e63",
                "contract_version": "regime_live_axis_adapter/v6",
            },
            "minimum_coverage": {
                "path": "config/regime_minimum_coverage_policy.json",
                "sha256": "92bfc5704e97d65a4c9a28043db49a2e09a75eea08e88c157be88db6dcd81e7b",
                "contract_version": "regime_minimum_coverage/v1",
            },
            "policy_candidate_population": {
                "path": "config/regime_policy_candidate_population_contract.json",
                "sha256": "ddcba56291409fc5b8ac9e0a9324d6c6e779240ac8138468b310feb0b512db76",
                "contract_version": "regime_policy_candidate_population/v5",
            },
        },
        "required_markets": ["US", "KR", "CRYPTO"],
        "required_axes": [
            "TREND",
            "BREADTH",
            "RISK_VOL",
            "LIQUIDITY",
            "LEADERSHIP",
        ],
        "minimum_defined_axes": 5,
        "coverage_policy": "ALL_REQUIRED_AXES_5_OF_5",
        "history_requirement": "UNRATIFIED_NO_MINIMUM_INVENTED",
        "market_rollout_policy": "FIRST_MARKET_WITH_VALIDATED_5_OF_5_PIT_HISTORY",
        "candidate_mode": (
            "SHADOW_ONLY_AFTER_COVERAGE_AND_SEPARATE_VALUE_RATIFICATION"
        ),
        "not_ready_status": "NOT_READY_AXIS_COVERAGE",
        "authority": {
            "readiness_inventory_only": True,
            "policy_value_generation_authorized": False,
            "shadow_candidate_generation_authorized": False,
            "replay_population_authorized": False,
            "candidate_selection_authorized": False,
            "policy_recommendation_authorized": False,
            "policy_ratification_authorized": False,
            "regime_classification_authorized": False,
            "direction_authorized": False,
            "confidence_authorized": False,
            "threshold_authorized": False,
            "weight_authorized": False,
            "market_ranking_authorized": False,
            "strategy_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "proposal_authorized": False,
            "order_authorized": False,
            "capital_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def load_contract(path: Path = CONTRACT_PATH, root: Path = ROOT) -> dict:
    value = _read_json(path, "READINESS_CONTRACT_INVALID")
    if value != _expected_contract():
        fail("READINESS_CONTRACT_MISMATCH")
    for name, reference in value["bound_contracts"].items():
        raw = _read_bytes(_safe(root, reference["path"]), "BOUND_CONTRACT_MISSING")
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            fail("BOUND_CONTRACT_SHA_MISMATCH", name)
    return copy.deepcopy(value)


def _parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str) or DATE.fullmatch(value) is None:
        fail(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code)
    if parsed.isoformat() != value:
        fail(code)
    return parsed


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        fail(code)


def _all_authorized_false(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_authorized") and item is not False:
                return False
            if not _all_authorized_false(item):
                return False
    elif isinstance(value, list):
        return all(_all_authorized_false(item) for item in value)
    return True


def _record(
    *, observation_date: str, available_at: str, uri: str,
    evidence_sha256: str, source_revision_id: str,
) -> dict:
    observed = _parse_date(observation_date, "SOURCE_OBSERVATION_DATE_INVALID")
    available = _parse_utc(available_at, "SOURCE_AVAILABLE_AT_INVALID")
    if observed > available.date():
        fail("SOURCE_OBSERVATION_FROM_FUTURE", uri)
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
        fail("SOURCE_SHA_INVALID", uri)
    if not isinstance(source_revision_id, str) or not source_revision_id:
        fail("SOURCE_REVISION_INVALID", uri)
    return {
        "observation_date": observation_date,
        "available_at": available_at,
        "evidence_uri": uri,
        "evidence_sha256": evidence_sha256,
        "source_revision_id": source_revision_id,
    }


def _date_directories(root: Path, relative: str) -> list[Path]:
    base = _safe(root, relative)
    if not base.is_dir():
        return []
    result = []
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            fail("SOURCE_LAYOUT_INVALID", str(path.relative_to(root)))
        _parse_date(path.name, "SOURCE_DIRECTORY_DATE_INVALID")
        result.append(path)
    return result


def _scan_btc(root: Path, axis: str) -> list[dict]:
    records = []
    builder = LIVE_AXIS.BTC_TREND if axis == "TREND" else LIVE_AXIS.BTC_RISK
    for path in _date_directories(root, "evidence/crypto/btc/raw"):
        try:
            packet = builder.build_transform(path)
        except Exception as exc:  # noqa: BLE001
            raise PolicyCalibrationReadinessError(
                f"SOURCE_EVIDENCE_INVALID:CRYPTO/{axis}:{path.name}"
            ) from exc
        if not _all_authorized_false(packet):
            fail("SOURCE_AUTHORITY_INVALID", f"CRYPTO/{axis}:{path.name}")
        observation_date = (
            packet["latest_finalized_day"]
            if axis == "TREND"
            else packet["risk_point"]["as_of_date"]
        )
        lineage = packet["lineage"]
        records.append(_record(
            observation_date=observation_date,
            available_at=lineage["available_at"],
            uri=f"atlas-raw-response://{path.relative_to(root).as_posix()}/kraken_ohlc_xbtusd.json.gz",
            evidence_sha256=lineage["source_sha256"],
            source_revision_id=f"{path.name}:{lineage['source_sha256']}",
        ))
    return records


def _scan_stablecoin(root: Path) -> list[dict]:
    records = []
    for path in _date_directories(root, "evidence/stablecoin/raw"):
        try:
            packet = LIVE_AXIS.STABLECOIN.build_transform(path)
        except Exception as exc:  # noqa: BLE001
            raise PolicyCalibrationReadinessError(
                f"SOURCE_EVIDENCE_INVALID:CRYPTO/LIQUIDITY:{path.name}"
            ) from exc
        if not _all_authorized_false(packet):
            fail("SOURCE_AUTHORITY_INVALID", f"CRYPTO/LIQUIDITY:{path.name}")
        rows = packet.get("rows")
        latest = rows[-1] if isinstance(rows, list) and rows else None
        if (
            not isinstance(latest, dict)
            or latest.get("daily_status") != "AVAILABLE"
            or latest.get("weekly_status") != "AVAILABLE"
        ):
            fail("SOURCE_EVIDENCE_INVALID", f"CRYPTO/LIQUIDITY:{path.name}")
        records.append(_record(
            observation_date=latest["observation_date"],
            available_at=packet["lineage"]["available_at"],
            uri=f"atlas-raw-response://{path.relative_to(root).as_posix()}/stablecoincharts_all.json.gz",
            evidence_sha256=packet["source"]["response_sha256"],
            source_revision_id=f"{path.name}:{packet['source']['response_sha256']}",
        ))
    return records


def _scan_fred(root: Path) -> list[dict]:
    base = _safe(root, "evidence/free_market_data/fred/raw")
    if not base.is_dir():
        return []
    records = []
    for day in sorted(base.iterdir()):
        if not day.is_dir():
            fail("SOURCE_LAYOUT_INVALID", str(day.relative_to(root)))
        _parse_date(day.name, "SOURCE_DIRECTORY_DATE_INVALID")
        for revision in sorted(day.iterdir()):
            if not revision.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", revision.name):
                fail("SOURCE_REVISION_INVALID", str(revision.relative_to(root)))
            manifest_path = revision / "manifest.json"
            raw_path = revision / "fred_vixcls.json.gz"
            manifest_bytes = _read_bytes(manifest_path, "SOURCE_EVIDENCE_INVALID")
            raw_bytes = _read_bytes(raw_path, "SOURCE_EVIDENCE_INVALID")
            manifest = _read_json(manifest_path, "SOURCE_EVIDENCE_INVALID")
            pointer = {
                "evidence_revision_id": revision.name,
                "manifest_path": manifest_path.relative_to(root).as_posix(),
                "manifest_file_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "raw_path": raw_path.relative_to(root).as_posix(),
                "raw_file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "raw_response_sha256": manifest.get("raw_response_sha256"),
            }
            try:
                replay = LIVE_AXIS.FRED_VIX.validate_evidence(root, pointer)
            except Exception as exc:  # noqa: BLE001
                raise PolicyCalibrationReadinessError(
                    f"SOURCE_EVIDENCE_INVALID:US/RISK_VOL:{revision.name}"
                ) from exc
            if not _all_authorized_false(replay):
                fail("SOURCE_AUTHORITY_INVALID", f"US/RISK_VOL:{revision.name}")
            records.append(_record(
                observation_date=replay["observation"]["observation_date"],
                available_at=replay["captured_at_utc"],
                uri=f"atlas-raw-response://{pointer['raw_path']}",
                evidence_sha256=pointer["raw_response_sha256"],
                source_revision_id=revision.name,
            ))
    return records


def _scan_crypto_breadth(root: Path) -> list[dict]:
    records = []
    for path in _date_directories(root, "evidence/crypto/breadth/raw"):
        try:
            packet = LIVE_AXIS.CRYPTO_BREADTH.build_transform(path)
        except Exception as exc:  # noqa: BLE001
            raise PolicyCalibrationReadinessError(
                f"SOURCE_EVIDENCE_INVALID:CRYPTO/BREADTH:{path.name}"
            ) from exc
        if not _all_authorized_false(packet):
            fail("SOURCE_AUTHORITY_INVALID", f"CRYPTO/BREADTH:{path.name}")
        if packet.get("status") != "OBSERVED_UNCLASSIFIED":
            continue
        lineage = packet["lineage"]
        records.append(_record(
            observation_date=packet["as_of_date"],
            available_at=lineage["available_at"],
            uri=f"atlas-raw-response://{path.relative_to(root).as_posix()}/_manifest.json",
            evidence_sha256=lineage["manifest_sha256"],
            source_revision_id=f"{path.name}:{lineage['manifest_sha256']}",
        ))
    return records


def _scan_crypto_leadership(root: Path) -> list[dict]:
    breadth_root = _safe(root, "evidence/crypto/breadth/raw")
    records = []
    for path in _date_directories(root, "evidence/crypto/breadth/raw"):
        vintage = _parse_date(path.name, "SOURCE_DIRECTORY_DATE_INVALID")
        end_date = (vintage - dt.timedelta(days=1)).isoformat()
        try:
            packet = LIVE_AXIS.CRYPTO_LEADERSHIP.build_transform(
                breadth_root, end_date=end_date
            )
        except Exception as exc:  # noqa: BLE001
            raise PolicyCalibrationReadinessError(
                f"SOURCE_EVIDENCE_INVALID:CRYPTO/LEADERSHIP:{path.name}"
            ) from exc
        if not _all_authorized_false(packet):
            fail("SOURCE_AUTHORITY_INVALID", f"CRYPTO/LEADERSHIP:{path.name}")
        if packet.get("status") != "OBSERVED_UNCLASSIFIED":
            continue
        manifest_entries = packet["lineage"]["manifest_sha256_by_date"]
        matching = [
            entry for entry in manifest_entries
            if entry["as_of_date"] == packet["as_of_date"]
        ]
        if len(matching) != 1:
            continue
        available_candidates = [
            point["lineage"]["available_at"]
            for window in packet["windows"]
            for point in window.get("daily_points", [])
        ]
        if not available_candidates:
            continue
        records.append(_record(
            observation_date=packet["as_of_date"],
            available_at=max(available_candidates),
            uri=(
                "atlas-raw-response://evidence/crypto/breadth/raw/"
                f"{path.name}/_manifest.json"
            ),
            evidence_sha256=matching[0]["manifest_sha256"],
            source_revision_id=f"{path.name}:{matching[0]['manifest_sha256']}",
        ))
    return records


def _scan_korea_market_signals(root: Path, axis: str) -> list[dict]:
    records = []
    for path in _date_directories(root, "data/observations/korea_market_signals"):
        packet_path = path / "packet.json"
        try:
            packet = LIVE_AXIS.KOREA_MARKET_SIGNALS.validate_packet(
                _read_json(packet_path, "SOURCE_EVIDENCE_INVALID")
            )
        except Exception as exc:  # noqa: BLE001
            raise PolicyCalibrationReadinessError(
                f"SOURCE_EVIDENCE_INVALID:KR/{axis}:{path.name}"
            ) from exc
        if not _all_authorized_false(packet):
            fail("SOURCE_AUTHORITY_INVALID", f"KR/{axis}:{path.name}")
        if packet.get("axes", {}).get(axis, {}).get("status") != "OBSERVED":
            continue
        records.append(_record(
            observation_date=packet["as_of_date"],
            available_at=packet["available_at"],
            uri=(
                "atlas-observation://"
                f"{packet_path.relative_to(root.resolve()).as_posix()}"
            ),
            evidence_sha256=packet["payload_sha256"],
            source_revision_id=f"{path.name}:{packet['payload_sha256']}",
        ))
    return records


def _scan_source_history(root: Path, qualified_axis: str) -> list[dict]:
    if qualified_axis.startswith("KR/"):
        return _scan_korea_market_signals(root, qualified_axis.split("/", 1)[1])
    if qualified_axis == "US/RISK_VOL":
        return _scan_fred(root)
    if qualified_axis == "CRYPTO/TREND":
        return _scan_btc(root, "TREND")
    if qualified_axis == "CRYPTO/RISK_VOL":
        return _scan_btc(root, "RISK_VOL")
    if qualified_axis == "CRYPTO/LIQUIDITY":
        return _scan_stablecoin(root)
    if qualified_axis == "CRYPTO/BREADTH":
        return _scan_crypto_breadth(root)
    if qualified_axis == "CRYPTO/LEADERSHIP":
        return _scan_crypto_leadership(root)
    fail("SOURCE_SCANNER_UNDEFINED", qualified_axis)


def _history_summary(records: list[dict]) -> dict:
    ordered = sorted(
        records,
        key=lambda row: (
            row["available_at"], row["observation_date"], row["source_revision_id"]
        ),
    )
    observations = sorted({row["observation_date"] for row in ordered})
    available = sorted({row["available_at"] for row in ordered})
    first = observations[0] if observations else None
    last = observations[-1] if observations else None
    span = (
        (
            _parse_date(last, "SOURCE_OBSERVATION_DATE_INVALID")
            - _parse_date(first, "SOURCE_OBSERVATION_DATE_INVALID")
        ).days + 1
        if first is not None and last is not None
        else 0
    )
    return {
        "status": "VALIDATED_RETAINED" if ordered else "NO_VALIDATED_EVIDENCE",
        "retained_revision_count": len(ordered),
        "distinct_observation_count": len(observations),
        "first_observation_date": first,
        "last_observation_date": last,
        "history_span_calendar_days": span,
        "first_available_at": available[0] if available else None,
        "last_available_at": available[-1] if available else None,
        "records": ordered,
    }


def _overall_status(coverage_ready: list[str], candidate_status: str) -> str:
    if not coverage_ready:
        return "NOT_READY_AXIS_COVERAGE"
    if candidate_status != "CANDIDATE_READY":
        return "NOT_READY_POLICY_CANDIDATE"
    return "READY_FOR_SEPARATE_SHADOW_CASE_DESIGN"


def build_readiness(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    contract = load_contract(root / "config" / CONTRACT_PATH.name, root)
    try:
        live_contract = LIVE_AXIS.load_contract(
            _safe(root, contract["bound_contracts"]["live_axis_adapter"]["path"])
        )
        minimum = MINIMUM_COVERAGE.load_contract(
            _safe(root, contract["bound_contracts"]["minimum_coverage"]["path"])
        )
        population = POPULATION.validate_population(root, root)
    except Exception as exc:  # noqa: BLE001
        raise PolicyCalibrationReadinessError(
            "BOUND_SOURCE_VALIDATION_FAILED"
        ) from exc
    if list(minimum["required_axes"]) != contract["required_axes"]:
        fail("REQUIRED_AXES_MISMATCH")
    if minimum["minimum_defined_axes"] != contract["minimum_defined_axes"]:
        fail("MINIMUM_COVERAGE_MISMATCH")
    if live_contract["contract_version"] != contract["bound_contracts"][
        "live_axis_adapter"
    ]["contract_version"]:
        fail("BOUND_CONTRACT_VERSION_MISMATCH", "live_axis_adapter")
    if minimum["contract_version"] != contract["bound_contracts"][
        "minimum_coverage"
    ]["contract_version"]:
        fail("BOUND_CONTRACT_VERSION_MISMATCH", "minimum_coverage")
    if population["contract_version"] != contract["bound_contracts"][
        "policy_candidate_population"
    ]["contract_version"]:
        fail("BOUND_CONTRACT_VERSION_MISMATCH", "policy_candidate_population")
    if list(live_contract["bindings"]) != [
        "KR/TREND", "KR/BREADTH", "KR/RISK_VOL", "KR/LIQUIDITY",
        "KR/LEADERSHIP", "US/RISK_VOL", "CRYPTO/TREND",
        "CRYPTO/RISK_VOL", "CRYPTO/LIQUIDITY", "CRYPTO/BREADTH",
        "CRYPTO/LEADERSHIP",
    ]:
        fail("LIVE_BINDING_SET_MISMATCH")

    histories = {
        qualified_axis: _history_summary(_scan_source_history(root, qualified_axis))
        for qualified_axis in live_contract["bindings"]
    }
    market_rows = []
    for market in contract["required_markets"]:
        axes = []
        defined = []
        missing = []
        for axis in contract["required_axes"]:
            qualified = f"{market}/{axis}"
            history = histories.get(qualified)
            if history is not None and history["status"] == "VALIDATED_RETAINED":
                status = "RETAINED_EVIDENCE_AVAILABLE"
                blocker = None
                defined.append(axis)
            else:
                status = "NOT_READY"
                blocker = live_contract["deferred_axes"].get(
                    qualified, "NO_RATIFIED_LIVE_AXIS_BINDING"
                )
                if history is not None:
                    blocker = "VALIDATED_RETAINED_EVIDENCE_MISSING"
                missing.append(axis)
            axes.append({
                "axis": axis,
                "qualified_axis": qualified,
                "status": status,
                "blocker": blocker,
                "binding": copy.deepcopy(live_contract["bindings"].get(qualified)),
                "history": copy.deepcopy(history) if history is not None else None,
            })
        coverage_met = len(defined) == contract["minimum_defined_axes"] and not missing
        blockers = []
        if not coverage_met:
            blockers.append("AXIS_COVERAGE_INCOMPLETE")
            blockers.extend(
                f"AXIS_NOT_READY:{row['axis']}:{row['blocker']}"
                for row in axes if row["status"] != "RETAINED_EVIDENCE_AVAILABLE"
            )
        blockers.extend([
            "CALIBRATION_HISTORY_REQUIREMENT_UNRATIFIED",
            "POLICY_VALUES_REQUIRE_SEPARATE_FINAL_RATIFICATION",
        ])
        market_rows.append({
            "market": market,
            "status": (
                "NOT_READY_HISTORY_AND_POLICY_RATIFICATION"
                if coverage_met else contract["not_ready_status"]
            ),
            "coverage": {
                "policy": contract["coverage_policy"],
                "required_axes": list(contract["required_axes"]),
                "defined_axes": defined,
                "missing_axes": missing,
                "defined_count": len(defined),
                "required_count": len(contract["required_axes"]),
                "ratio": f"{len(defined)}/{len(contract['required_axes'])}",
                "minimum_coverage_met": coverage_met,
            },
            "axes": axes,
            "shadow_candidate_eligible": False,
            "replay_population_eligible": False,
            "blockers": blockers,
        })

    readiness_order = [
        row["market"]
        for row in sorted(
            market_rows,
            key=lambda row: (
                -row["coverage"]["defined_count"],
                contract["required_markets"].index(row["market"]),
            ),
        )
    ]
    coverage_ready = [
        row["market"] for row in market_rows
        if row["coverage"]["minimum_coverage_met"]
    ]
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "contract_mode": contract["contract_mode"],
        "methodology": {
            "status": contract["methodology_status"],
            "history_requirement": contract["history_requirement"],
            "market_rollout_policy": contract["market_rollout_policy"],
            "candidate_mode": contract["candidate_mode"],
        },
        "status": _overall_status(coverage_ready, population["candidate_status"]),
        "source_contracts": {
            "live_axis_adapter": live_contract["contract_version"],
            "minimum_coverage": minimum["contract_version"],
            "policy_candidate_population": population["contract_version"],
        },
        "policy_candidate": {
            "candidate_id": population["candidate_id"],
            "candidate_status": population["candidate_status"],
            "supported_components": list(population["supported_components"]),
            "blocked_components": list(population["blocked_components"]),
            "generated_policy_value_count": 0,
            "selected_candidate_count": 0,
            "recommended_candidate_count": 0,
            "ratified_candidate_count": 0,
        },
        "markets": market_rows,
        "summary": {
            "market_count": len(market_rows),
            "coverage_ready_market_count": len(coverage_ready),
            "coverage_ready_markets": coverage_ready,
            "shadow_candidate_eligible_market_count": 0,
            "shadow_candidate_count": 0,
            "replay_population_eligible_market_count": 0,
            "replay_case_count": 0,
            "historical_outcome_evaluated": False,
            "current_readiness_order_not_market_ranking": readiness_order,
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_readiness(value: dict, root: Path = ROOT) -> dict:
    expected = build_readiness(root)
    if value != expected:
        fail("READINESS_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


def write_json_atomic(path: Path, value: dict, root: Path = ROOT) -> None:
    path = Path(path).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError:
        pass
    else:
        fail("TRACKED_OUTPUT_FORBIDDEN")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    packet = build_readiness()
    validate_readiness(packet)
    write_json_atomic(args.out, packet)
    ratios = ",".join(
        f"{row['market']}={row['coverage']['ratio']}" for row in packet["markets"]
    )
    print(f"regime policy calibration readiness: {packet['status']} {ratios}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
