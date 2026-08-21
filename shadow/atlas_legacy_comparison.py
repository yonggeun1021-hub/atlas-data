#!/usr/bin/env python3
"""P10-02 exact-key Atlas versus existing-judgment evidence comparison."""
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
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "atlas_legacy_comparison_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_shadow():
    path = ROOT / "shadow" / "three_market_shadow_ledger.py"
    spec = importlib.util.spec_from_file_location("atlas_shadow_ledger", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SHADOW_LEDGER_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHADOW = _load_shadow()


class AtlasLegacyComparisonError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AtlasLegacyComparisonError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 4,
        "contract_version": "atlas_legacy_comparison/4",
        "legacy_batch_schema_version": "legacy_judgment_batch/4",
        "outcome_batch_schema_version": "comparison_outcome_batch/4",
        "output_schema_version": "atlas_legacy_comparison_packet/4",
        "shadow_ledger_schema_version": "three_market_shadow_ledger_packet/3",
        "markets": ["US", "KOREA", "CRYPTO"],
        "slots": ["morning", "evening"],
        "action_labels": [
            "BUY", "WATCH", "REDUCE", "HEDGE", "EXIT", "NO_ACTION", "UNDEFINED"
        ],
        "outcome_labels": ["POSITIVE", "NEGATIVE", "FLAT", "UNDEFINED"],
        "alignment_statuses": ["SAME", "DIFFERENT", "UNDEFINED"],
        "comparison_key": "DECISION_ID_MARKET",
        "missing_policy": "EXPLICIT_ROW_STATUS_NO_IMPUTATION",
        "effectiveness_policy": (
            "NOT_EVALUATED_UNTIL_RATIFIED_POLICY_AND_OBSERVATIONS"
        ),
        "input_authority": {
            "external_judgment_observation_only": True,
            "external_outcome_observation_only": True,
            "atlas_decision_change_authorized": False,
            "performance_interpretation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "same_period_evidence_alignment_only": True,
            "judgment_interpretation_authorized": False,
            "winner_selection_authorized": False,
            "performance_claim_authorized": False,
            "strategy_change_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise AtlasLegacyComparisonError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise AtlasLegacyComparisonError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise AtlasLegacyComparisonError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise AtlasLegacyComparisonError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise AtlasLegacyComparisonError(code)
    return parsed


def _date(value, code: str) -> str:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise AtlasLegacyComparisonError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise AtlasLegacyComparisonError(code) from exc
    if parsed.isoformat() != value:
        raise AtlasLegacyComparisonError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise AtlasLegacyComparisonError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AtlasLegacyComparisonError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AtlasLegacyComparisonError(code)
    return value


def _digest(value: dict, field: str, code: str) -> str:
    digest = _sha(value.get(field), code)
    normalized = copy.deepcopy(value)
    normalized.pop(field)
    if payload_sha256(normalized) != digest:
        raise AtlasLegacyComparisonError(f"{code}_MISMATCH")
    return digest


def _validate_legacy(value: dict, observed_at: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "judgments", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise AtlasLegacyComparisonError("LEGACY_BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["legacy_batch_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise AtlasLegacyComparisonError("LEGACY_BATCH_IDENTITY_INVALID")
    batch_time = _utc(value.get("observed_at"), "LEGACY_BATCH_TIME_INVALID")
    if batch_time > observed_at:
        raise AtlasLegacyComparisonError("LEGACY_BATCH_FROM_FUTURE")
    _token(value.get("batch_id"), "LEGACY_BATCH_ID_INVALID")
    digest = _digest(value, "packet_sha256", "LEGACY_BATCH_SHA_INVALID")
    rows = value.get("judgments")
    if not isinstance(rows, list):
        raise AtlasLegacyComparisonError("LEGACY_ROWS_NOT_LIST")
    row_fields = {
        "decision_id", "decision_date", "slot", "market", "decided_at",
        "action_label", "source_ref", "source_sha256",
    }
    checked = []
    for index, row in enumerate(rows):
        context = f"legacy:{index}"
        if not isinstance(row, dict) or set(row) != row_fields:
            raise AtlasLegacyComparisonError(f"LEGACY_ROW_FIELDS_MISMATCH:{context}")
        decided = _utc(row.get("decided_at"), f"LEGACY_ROW_TIME_INVALID:{context}")
        decision_date = _date(
            row.get("decision_date"), f"LEGACY_ROW_DATE_INVALID:{context}"
        )
        if decided > batch_time:
            raise AtlasLegacyComparisonError(f"LEGACY_ROW_FROM_FUTURE:{context}")
        if (
            row.get("market") not in contract["markets"]
            or row.get("slot") not in contract["slots"]
            or row.get("action_label") not in contract["action_labels"]
            or row.get("decision_id") != f"atlas-{decision_date}-{row.get('slot')}"
        ):
            raise AtlasLegacyComparisonError(f"LEGACY_ROW_IDENTITY_INVALID:{context}")
        _text(row.get("source_ref"), f"LEGACY_SOURCE_REF_INVALID:{context}")
        _sha(row.get("source_sha256"), f"LEGACY_SOURCE_SHA_INVALID:{context}")
        checked.append(copy.deepcopy(row))
    checked.sort(key=lambda row: (row["decision_date"], contract["slots"].index(row["slot"]), contract["markets"].index(row["market"])))
    keys = [(row["decision_id"], row["market"]) for row in checked]
    if len(keys) != len(set(keys)):
        raise AtlasLegacyComparisonError("LEGACY_ROW_KEY_DUPLICATE_OR_ORDER_INVALID")
    return {"rows": checked, "packet_sha256": digest, "batch_id": value["batch_id"]}


def _validate_outcomes(value: dict, observed_at: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "evaluation_window_id", "outcomes", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise AtlasLegacyComparisonError("OUTCOME_BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["outcome_batch_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise AtlasLegacyComparisonError("OUTCOME_BATCH_IDENTITY_INVALID")
    batch_time = _utc(value.get("observed_at"), "OUTCOME_BATCH_TIME_INVALID")
    if batch_time > observed_at:
        raise AtlasLegacyComparisonError("OUTCOME_BATCH_FROM_FUTURE")
    _token(value.get("batch_id"), "OUTCOME_BATCH_ID_INVALID")
    _token(value.get("evaluation_window_id"), "EVALUATION_WINDOW_ID_INVALID")
    digest = _digest(value, "packet_sha256", "OUTCOME_BATCH_SHA_INVALID")
    rows = value.get("outcomes")
    if not isinstance(rows, list):
        raise AtlasLegacyComparisonError("OUTCOME_ROWS_NOT_LIST")
    row_fields = {
        "decision_id", "market", "observed_at", "outcome_label",
        "source_ref", "source_sha256",
    }
    checked = []
    for index, row in enumerate(rows):
        context = f"outcome:{index}"
        if not isinstance(row, dict) or set(row) != row_fields:
            raise AtlasLegacyComparisonError(f"OUTCOME_ROW_FIELDS_MISMATCH:{context}")
        row_time = _utc(row.get("observed_at"), f"OUTCOME_ROW_TIME_INVALID:{context}")
        if row_time > batch_time:
            raise AtlasLegacyComparisonError(f"OUTCOME_ROW_FROM_FUTURE:{context}")
        if (
            row.get("market") not in contract["markets"]
            or row.get("outcome_label") not in contract["outcome_labels"]
        ):
            raise AtlasLegacyComparisonError(f"OUTCOME_ROW_IDENTITY_INVALID:{context}")
        _text(row.get("decision_id"), f"OUTCOME_DECISION_ID_INVALID:{context}")
        _text(row.get("source_ref"), f"OUTCOME_SOURCE_REF_INVALID:{context}")
        _sha(row.get("source_sha256"), f"OUTCOME_SOURCE_SHA_INVALID:{context}")
        checked.append(copy.deepcopy(row))
    checked.sort(key=lambda row: (row["decision_id"], contract["markets"].index(row["market"])))
    keys = [(row["decision_id"], row["market"]) for row in checked]
    if len(keys) != len(set(keys)):
        raise AtlasLegacyComparisonError("OUTCOME_ROW_KEY_DUPLICATE_OR_ORDER_INVALID")
    return {
        "rows": checked,
        "packet_sha256": digest,
        "batch_id": value["batch_id"],
        "evaluation_window_id": value["evaluation_window_id"],
    }


def _comparison_rows(ledger: dict, legacy: dict, outcomes: dict, contract: dict) -> list[dict]:
    legacy_by_key = {(row["decision_id"], row["market"]): row for row in legacy["rows"]}
    outcome_by_key = {(row["decision_id"], row["market"]): row for row in outcomes["rows"]}
    shadow_keys = {
        (record["decision_id"], market)
        for record in ledger["records"]
        for market in contract["markets"]
    }
    legacy_extra = sorted(set(legacy_by_key) - shadow_keys)
    outcome_extra = sorted(set(outcome_by_key) - shadow_keys)
    if legacy_extra:
        raise AtlasLegacyComparisonError(
            f"LEGACY_KEY_NOT_IN_SHADOW:{legacy_extra[0][0]}:{legacy_extra[0][1]}"
        )
    if outcome_extra:
        raise AtlasLegacyComparisonError(
            f"OUTCOME_KEY_NOT_IN_SHADOW:{outcome_extra[0][0]}:{outcome_extra[0][1]}"
        )
    rows = []
    for record in ledger["records"]:
        markets = {row["market"]: row for row in record["market_snapshots"]}
        for market in contract["markets"]:
            key = (record["decision_id"], market)
            old = legacy_by_key.get(key)
            outcome = outcome_by_key.get(key)
            atlas_action = record["action"]
            legacy_action = None if old is None else old["action_label"]
            alignment = (
                "UNDEFINED"
                if atlas_action is None or legacy_action in {None, "UNDEFINED"}
                else "SAME" if atlas_action == legacy_action else "DIFFERENT"
            )
            reasons = []
            if old is None:
                reasons.append("LEGACY_JUDGMENT_MISSING")
            if atlas_action is None:
                reasons.append("ATLAS_ACTION_UNDEFINED")
            if outcome is None:
                reasons.append("OUTCOME_MISSING")
            elif outcome["outcome_label"] == "UNDEFINED":
                reasons.append("OUTCOME_UNDEFINED")
            reasons.append("EFFECTIVENESS_POLICY_UNRATIFIED")
            rows.append({
                "decision_id": record["decision_id"],
                "decision_date": record["decision_date"],
                "slot": record["slot"],
                "market": market,
                "regime": markets[market]["regime"],
                "atlas_action": atlas_action,
                "legacy_action": legacy_action,
                "action_alignment": alignment,
                "legacy_judgment": copy.deepcopy(old),
                "outcome": copy.deepcopy(outcome),
                "effectiveness": "NOT_EVALUATED",
                "winner": None,
                "comparison_reasons": sorted(reasons),
                "shadow_record_sha256": record["record_sha256"],
            })
    return rows


def build_packet(shadow_ledger: dict, legacy_batch: dict, outcome_batch: dict, observed_at: str, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    observed = _utc(observed_at, "OBSERVED_AT_INVALID")
    try:
        ledger = SHADOW.validate_ledger(shadow_ledger)
    except (SHADOW.ThreeMarketShadowLedgerError, OSError, TypeError, ValueError) as exc:
        raise AtlasLegacyComparisonError(f"SHADOW_LEDGER_INVALID:{exc}") from exc
    if ledger["schema_version"] != contract["shadow_ledger_schema_version"]:
        raise AtlasLegacyComparisonError("SHADOW_LEDGER_SCHEMA_INVALID")
    legacy = _validate_legacy(legacy_batch, observed, contract)
    outcomes = _validate_outcomes(outcome_batch, observed, contract)
    rows = _comparison_rows(ledger, legacy, outcomes, contract)
    counts = {status: 0 for status in contract["alignment_statuses"]}
    for row in rows:
        counts[row["action_alignment"]] += 1
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "observed_at": observed_at,
        "status": "SAME_PERIOD_EVIDENCE_ALIGNED_EFFECTIVENESS_NOT_EVALUATED",
        "evaluation_window_id": outcomes["evaluation_window_id"],
        "comparisons": rows,
        "summary": {
            "shadow_record_count": len(ledger["records"]),
            "comparison_row_count": len(rows),
            "legacy_matched_count": sum(row["legacy_judgment"] is not None for row in rows),
            "outcome_matched_count": sum(row["outcome"] is not None for row in rows),
            "action_alignment_counts": counts,
            "effectiveness_evaluated_count": 0,
            "winner_count": 0,
        },
        "lineage": {
            "shadow_ledger_sha256": ledger["packet_sha256"],
            "legacy_batch_id": legacy["batch_id"],
            "legacy_batch_sha256": legacy["packet_sha256"],
            "outcome_batch_id": outcomes["batch_id"],
            "outcome_batch_sha256": outcomes["packet_sha256"],
        },
        "source_packets": {
            "SHADOW_LEDGER": copy.deepcopy(shadow_ledger),
            "LEGACY_BATCH": copy.deepcopy(legacy_batch),
            "OUTCOME_BATCH": copy.deepcopy(outcome_batch),
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "LIVE_COMPARISON_OBSERVATIONS_NOT_ESTABLISHED",
            "ATLAS_ACTION_POLICY_NOT_AUTHORIZED",
            "EFFECTIVENESS_EVALUATION_POLICY_UNRATIFIED",
            "WINNER_SELECTION_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(packet, dict) or set(packet) != {
        "schema_version", "contract_version", "observed_at", "status",
        "evaluation_window_id", "comparisons", "summary", "lineage",
        "source_packets", "authority", "unresolved_boundaries", "packet_sha256",
    }:
        raise AtlasLegacyComparisonError("PACKET_FIELDS_MISMATCH")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {
        "SHADOW_LEDGER", "LEGACY_BATCH", "OUTCOME_BATCH"
    }:
        raise AtlasLegacyComparisonError("PACKET_SOURCE_FIELDS_MISMATCH")
    shadow_ledger = sources["SHADOW_LEDGER"]
    legacy_batch = sources["LEGACY_BATCH"]
    outcome_batch = sources["OUTCOME_BATCH"]
    observed = _utc(packet.get("observed_at"), "OBSERVED_AT_INVALID")
    try:
        ledger = SHADOW.validate_ledger(shadow_ledger)
    except (SHADOW.ThreeMarketShadowLedgerError, OSError, TypeError, ValueError) as exc:
        raise AtlasLegacyComparisonError(f"SHADOW_LEDGER_INVALID:{exc}") from exc
    legacy = _validate_legacy(legacy_batch, observed, contract)
    outcomes = _validate_outcomes(outcome_batch, observed, contract)
    expected_rows = _comparison_rows(ledger, legacy, outcomes, contract)
    counts = {status: 0 for status in contract["alignment_statuses"]}
    for row in expected_rows:
        counts[row["action_alignment"]] += 1
    expected_summary = {
        "shadow_record_count": len(ledger["records"]),
        "comparison_row_count": len(expected_rows),
        "legacy_matched_count": sum(row["legacy_judgment"] is not None for row in expected_rows),
        "outcome_matched_count": sum(row["outcome"] is not None for row in expected_rows),
        "action_alignment_counts": counts,
        "effectiveness_evaluated_count": 0,
        "winner_count": 0,
    }
    expected_lineage = {
        "shadow_ledger_sha256": ledger["packet_sha256"],
        "legacy_batch_id": legacy["batch_id"],
        "legacy_batch_sha256": legacy["packet_sha256"],
        "outcome_batch_id": outcomes["batch_id"],
        "outcome_batch_sha256": outcomes["packet_sha256"],
    }
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "SAME_PERIOD_EVIDENCE_ALIGNED_EFFECTIVENESS_NOT_EVALUATED"
        or packet.get("evaluation_window_id") != outcomes["evaluation_window_id"]
        or packet.get("comparisons") != expected_rows
        or packet.get("summary") != expected_summary
        or packet.get("lineage") != expected_lineage
        or packet.get("authority") != contract["authority"]
        or packet.get("unresolved_boundaries") != [
            "LIVE_COMPARISON_OBSERVATIONS_NOT_ESTABLISHED",
            "ATLAS_ACTION_POLICY_NOT_AUTHORIZED",
            "EFFECTIVENESS_EVALUATION_POLICY_UNRATIFIED",
            "WINNER_SELECTION_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ]
    ):
        raise AtlasLegacyComparisonError("PACKET_CONTENT_MISMATCH")
    digest = _sha(packet.get("packet_sha256"), "PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise AtlasLegacyComparisonError("PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise AtlasLegacyComparisonError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(shadow_path: Path, legacy_path: Path, outcome_path: Path, observed_at: str, output_path: Path) -> int:
    try:
        packet = build_packet(
            _read_json(shadow_path), _read_json(legacy_path), _read_json(outcome_path),
            observed_at,
        )
        write_json_atomic(output_path, packet)
        return 0
    except (AtlasLegacyComparisonError, OSError, TypeError, ValueError) as exc:
        print(f"Atlas legacy comparison failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shadow", type=Path)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("outcomes", type=Path)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.shadow, args.legacy, args.outcomes, args.observed_at, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
