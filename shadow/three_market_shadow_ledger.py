#!/usr/bin/env python3
"""P10-01 append-only zero-capital three-market Shadow ledger."""
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
CONTRACT_PATH = ROOT / "config" / "three_market_shadow_ledger_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_validator(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"VALIDATOR_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UNIFIED = _load_validator(
    "atlas_unified_decision", ROOT / "decision" / "unified_decision_contract.py"
)
ENTRY_EXIT = _load_validator(
    "atlas_entry_exit_trigger_eligibility",
    ROOT / "execution" / "entry_exit_trigger_eligibility.py",
)
INTRADAY_RISK = _load_validator(
    "atlas_intraday_risk_escalation",
    ROOT / "execution" / "intraday_risk_escalation.py",
)


class ThreeMarketShadowLedgerError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThreeMarketShadowLedgerError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 4,
        "contract_version": "three_market_shadow_ledger/4",
        "ledger_schema_version": "three_market_shadow_ledger_packet/4",
        "unified_decision_schema_version": "unified_daily_decision/1",
        "unified_decision_contract_version": "unified_decision_contract/1",
        "entry_exit_schema_version": "entry_exit_trigger_eligibility_packet/1",
        "entry_exit_contract_version": "entry_exit_trigger_eligibility/1",
        "intraday_risk_schema_version": "intraday_risk_escalation_packet/3",
        "intraday_risk_contract_version": "intraday_risk_escalation/3",
        "markets": ["US", "KOREA", "CRYPTO"],
        "source_regime_markets": ["US", "KR", "CRYPTO"],
        "slots": ["morning", "evening"],
        "capital_mode": "ZERO_CAPITAL_SHADOW_ONLY",
        "append_semantics": "FORWARD_ONLY_ONE_RECORD_PER_DECISION_ID_IDEMPOTENT",
        "required_available_components": ["REGIME", "ROTATION_DISCOVERY"],
        "required_evidence_packets": [
            "UNIFIED_DECISION",
            "ENTRY_EXIT_TRIGGER_ELIGIBILITY",
            "INTRADAY_RISK_ESCALATION",
        ],
        "authority": {
            "shadow_observation_recording_only": True,
            "intraday_evidence_recording_only": True,
            "decision_interpretation_authorized": False,
            "performance_claim_authorized": False,
            "capital_allocation_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ThreeMarketShadowLedgerError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ThreeMarketShadowLedgerError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ThreeMarketShadowLedgerError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise ThreeMarketShadowLedgerError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ThreeMarketShadowLedgerError(code)
    return parsed


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ThreeMarketShadowLedgerError(code)
    return value


def _boundaries(observed: bool) -> list[str]:
    result = [
        "LIVE_DAILY_WIRING_NOT_IMPLEMENTED",
        "P9_INTRADAY_EVIDENCE_LIVE_WIRING_NOT_IMPLEMENTED",
        "SHADOW_OPERATIONAL_OBSERVATION_NOT_ESTABLISHED",
        "PERFORMANCE_ASSESSMENT_NOT_IMPLEMENTED",
        "REAL_CAPITAL_PROHIBITED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]
    if not observed:
        result.insert(0, "UNIFIED_DECISION_NOT_RECORDED")
    return result


def empty_ledger(contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    ledger = {
        "schema_version": contract["ledger_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "EMPTY",
        "capital_mode": contract["capital_mode"],
        "ledger_revision": 0,
        "records": [],
        "summary": {
            "record_count": 0,
            "decision_date_count": 0,
            "market_count": 3,
            "real_capital_deployed": "0",
            "real_order_count": 0,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": _boundaries(False),
    }
    ledger["packet_sha256"] = payload_sha256(ledger)
    return ledger


def _component(packet: dict, name: str, required: bool = True) -> dict | None:
    rows = {row["component"]: row for row in packet["components"]}
    row = rows[name]
    if row["availability"] == "UNAVAILABLE":
        if required:
            raise ThreeMarketShadowLedgerError(f"REQUIRED_COMPONENT_UNAVAILABLE:{name}")
        return None
    return row["source_packet"]


def _market_snapshots(decision: dict, contract: dict) -> list[dict]:
    regime = _component(decision, "REGIME")
    source_markets = regime.get("markets")
    if (
        not isinstance(source_markets, list)
        or [row.get("market") for row in source_markets if isinstance(row, dict)]
        != contract["source_regime_markets"]
    ):
        raise ThreeMarketShadowLedgerError("REGIME_MARKETS_INVALID")
    output = []
    market_map = {"US": "US", "KR": "KOREA", "CRYPTO": "CRYPTO"}
    required = {
        "market", "label", "market_timezone", "regime", "direction", "confidence",
        "source_generated_at", "coverage", "evidence_as_of", "available_as_of",
        "warnings", "source_sha256",
    }
    for row in source_markets:
        if set(row) != required:
            raise ThreeMarketShadowLedgerError("REGIME_MARKET_FIELDS_MISMATCH")
        output.append({
            "market": market_map[row["market"]],
            "regime": row["regime"],
            "direction": row["direction"],
            "confidence": row["confidence"],
            "coverage": copy.deepcopy(row["coverage"]),
            "warnings": copy.deepcopy(row["warnings"]),
            "source_packet_sha256": row["source_sha256"],
        })
    return output


def _validate_intraday_sources(
    decision: dict, entry_exit: dict, intraday_risk: dict, contract: dict
) -> tuple[dict, dict]:
    try:
        checked_entry_exit = ENTRY_EXIT.validate_packet(entry_exit)
    except Exception as exc:
        raise ThreeMarketShadowLedgerError(
            f"ENTRY_EXIT_TRIGGER_ELIGIBILITY_INVALID:{exc}"
        ) from exc
    try:
        checked_intraday_risk = INTRADAY_RISK.validate_packet(intraday_risk)
    except Exception as exc:
        raise ThreeMarketShadowLedgerError(
            f"INTRADAY_RISK_ESCALATION_INVALID:{exc}"
        ) from exc
    if (
        checked_entry_exit.get("schema_version")
        != contract["entry_exit_schema_version"]
        or checked_entry_exit.get("contract_version")
        != contract["entry_exit_contract_version"]
    ):
        raise ThreeMarketShadowLedgerError("ENTRY_EXIT_IDENTITY_INVALID")
    if (
        checked_intraday_risk.get("schema_version")
        != contract["intraday_risk_schema_version"]
        or checked_intraday_risk.get("contract_version")
        != contract["intraday_risk_contract_version"]
    ):
        raise ThreeMarketShadowLedgerError("INTRADAY_RISK_IDENTITY_INVALID")
    if (
        checked_entry_exit["decision_id"] != decision["decision_id"]
        or checked_entry_exit["lineage"]["unified_decision_packet_sha256"]
        != decision["packet_sha256"]
    ):
        raise ThreeMarketShadowLedgerError("ENTRY_EXIT_UNIFIED_LINEAGE_MISMATCH")
    if (
        checked_intraday_risk["lineage"][
            "entry_exit_trigger_eligibility_packet_sha256"
        ]
        != checked_entry_exit["packet_sha256"]
    ):
        raise ThreeMarketShadowLedgerError("INTRADAY_RISK_ENTRY_EXIT_LINEAGE_MISMATCH")
    eligibility_time = _utc(
        checked_entry_exit["generated_at"], "ENTRY_EXIT_GENERATED_AT_INVALID"
    )
    risk_time = _utc(
        checked_intraday_risk["observed_at"], "INTRADAY_RISK_OBSERVED_AT_INVALID"
    )
    if risk_time < eligibility_time:
        raise ThreeMarketShadowLedgerError("INTRADAY_RISK_BEFORE_ENTRY_EXIT")
    if risk_time.strftime("%Y-%m-%d") != decision["decision_date"]:
        raise ThreeMarketShadowLedgerError("INTRADAY_RISK_DECISION_DATE_MISMATCH")
    return checked_entry_exit, checked_intraday_risk


def _record(
    decision: dict,
    entry_exit: dict,
    intraday_risk: dict,
    recorded_at: str,
    revision: int,
    prior_sha: str | None,
    contract: dict,
) -> dict:
    try:
        checked = UNIFIED.validate_packet(decision)
    except (UNIFIED.UnifiedDecisionContractError, OSError, TypeError, ValueError) as exc:
        raise ThreeMarketShadowLedgerError(f"UNIFIED_DECISION_INVALID:{exc}") from exc
    if (
        checked["schema_version"] != contract["unified_decision_schema_version"]
        or checked["contract_version"] != contract["unified_decision_contract_version"]
    ):
        raise ThreeMarketShadowLedgerError("UNIFIED_DECISION_IDENTITY_INVALID")
    checked_entry_exit, checked_intraday_risk = _validate_intraday_sources(
        checked, entry_exit, intraday_risk, contract
    )
    recorded = _utc(recorded_at, "RECORDED_AT_INVALID")
    generated = _utc(checked["generated_at"], "DECISION_GENERATED_AT_INVALID")
    eligibility_time = _utc(
        checked_entry_exit["generated_at"], "ENTRY_EXIT_GENERATED_AT_INVALID"
    )
    risk_time = _utc(
        checked_intraday_risk["observed_at"], "INTRADAY_RISK_OBSERVED_AT_INVALID"
    )
    if recorded < max(generated, eligibility_time, risk_time):
        raise ThreeMarketShadowLedgerError("RECORDED_BEFORE_SOURCE_EVIDENCE")
    rotation = _component(checked, "ROTATION_DISCOVERY")
    market_snapshots = _market_snapshots(checked, contract)
    row = {
        "ledger_revision": revision,
        "decision_id": checked["decision_id"],
        "decision_date": checked["decision_date"],
        "slot": checked["slot"],
        "decision_generated_at": checked["generated_at"],
        "recorded_at": recorded_at,
        "capital_mode": contract["capital_mode"],
        "real_capital_deployed": "0",
        "real_order_count": 0,
        "market_snapshots": market_snapshots,
        "rotation_change_count": rotation["summary"]["rotation_change_count"],
        "discovery_case_count": rotation["summary"]["discovery_case_count"],
        "entry_eligible_count": checked_entry_exit["summary"]["entry_eligible_count"],
        "exit_eligible_count": checked_entry_exit["summary"]["exit_eligible_count"],
        "intraday_alert_count": checked_intraday_risk["summary"]["alert_count"],
        "decision_state": checked["decision"]["state"],
        "action": checked["decision"]["action"],
        "entry_trigger": checked["decision"]["entry_trigger"],
        "position_size": checked["decision"]["position_size"],
        "order_intent": checked["decision"]["order_intent"],
        "unified_decision_sha256": checked["packet_sha256"],
        "entry_exit_trigger_eligibility_sha256": checked_entry_exit["packet_sha256"],
        "intraday_risk_escalation_sha256": checked_intraday_risk["packet_sha256"],
        "prior_record_sha256": prior_sha,
        "unified_decision": checked,
        "entry_exit_trigger_eligibility": checked_entry_exit,
        "intraday_risk_escalation": checked_intraday_risk,
    }
    row["record_sha256"] = payload_sha256(row)
    return row


def _validate_record(row: dict, revision: int, prior: dict | None, contract: dict) -> dict:
    fields = {
        "ledger_revision", "decision_id", "decision_date", "slot",
        "decision_generated_at", "recorded_at", "capital_mode",
        "real_capital_deployed", "real_order_count", "market_snapshots",
        "rotation_change_count", "discovery_case_count", "entry_eligible_count",
        "exit_eligible_count", "intraday_alert_count", "decision_state", "action",
        "entry_trigger", "position_size", "order_intent", "unified_decision_sha256",
        "entry_exit_trigger_eligibility_sha256", "intraday_risk_escalation_sha256",
        "prior_record_sha256", "unified_decision",
        "entry_exit_trigger_eligibility", "intraday_risk_escalation", "record_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ThreeMarketShadowLedgerError("RECORD_FIELDS_MISMATCH")
    expected_prior = None if prior is None else prior["record_sha256"]
    expected = _record(
        row["unified_decision"], row["entry_exit_trigger_eligibility"],
        row["intraday_risk_escalation"], row["recorded_at"], revision,
        expected_prior, contract,
    )
    if row != expected:
        raise ThreeMarketShadowLedgerError("RECORD_MISMATCH")
    return copy.deepcopy(row)


def validate_ledger(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "capital_mode",
        "ledger_revision", "records", "summary", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ThreeMarketShadowLedgerError("LEDGER_FIELDS_MISMATCH")
    records = value.get("records")
    revision = value.get("ledger_revision")
    if (
        value.get("schema_version") != contract["ledger_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("capital_mode") != contract["capital_mode"]
        or type(revision) is not int
        or revision < 0
        or not isinstance(records, list)
        or revision != len(records)
        or value.get("authority") != contract["authority"]
    ):
        raise ThreeMarketShadowLedgerError("LEDGER_IDENTITY_INVALID")
    validated = []
    ids = []
    order_keys = []
    slot_order = {slot: index for index, slot in enumerate(contract["slots"])}
    prior = None
    for expected_revision, row in enumerate(records, 1):
        checked = _validate_record(row, expected_revision, prior, contract)
        ids.append(checked["decision_id"])
        order_keys.append((checked["decision_date"], slot_order[checked["slot"]]))
        validated.append(checked)
        prior = checked
    if ids != list(dict.fromkeys(ids)):
        raise ThreeMarketShadowLedgerError("DECISION_ID_DUPLICATE")
    if order_keys != sorted(set(order_keys)):
        raise ThreeMarketShadowLedgerError("LEDGER_NON_FORWARD_DECISION")
    observed = bool(records)
    expected_summary = {
        "record_count": len(records),
        "decision_date_count": len({row["decision_date"] for row in records}),
        "market_count": 3,
        "real_capital_deployed": "0",
        "real_order_count": 0,
    }
    if (
        value.get("status") != ("SHADOW_HISTORY_RECORDED" if observed else "EMPTY")
        or value.get("summary") != expected_summary
        or value.get("unresolved_boundaries") != _boundaries(observed)
    ):
        raise ThreeMarketShadowLedgerError("LEDGER_SUMMARY_INVALID")
    digest = _sha(value.get("packet_sha256"), "LEDGER_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise ThreeMarketShadowLedgerError("LEDGER_SHA_MISMATCH")
    return copy.deepcopy(value)


def append_decision(
    decision: dict,
    entry_exit: dict,
    intraday_risk: dict,
    recorded_at: str,
    previous_ledger: dict | None = None,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    ledger = validate_ledger(
        empty_ledger(contract) if previous_ledger is None else previous_ledger,
        contract,
    )
    candidate = _record(
        decision, entry_exit, intraday_risk, recorded_at,
        ledger["ledger_revision"] + 1,
        None if not ledger["records"] else ledger["records"][-1]["record_sha256"],
        contract,
    )
    duplicate = next(
        (row for row in ledger["records"] if row["decision_id"] == candidate["decision_id"]),
        None,
    )
    if duplicate is not None:
        evidence_keys = (
            "unified_decision_sha256",
            "entry_exit_trigger_eligibility_sha256",
            "intraday_risk_escalation_sha256",
        )
        if any(duplicate[key] != candidate[key] for key in evidence_keys):
            raise ThreeMarketShadowLedgerError("DECISION_ID_EVIDENCE_CONFLICT")
        return ledger
    if ledger["records"]:
        slot_order = {slot: index for index, slot in enumerate(contract["slots"])}
        prior = ledger["records"][-1]
        prior_key = (prior["decision_date"], slot_order[prior["slot"]])
        candidate_key = (candidate["decision_date"], slot_order[candidate["slot"]])
        if candidate_key <= prior_key:
            raise ThreeMarketShadowLedgerError("LEDGER_NON_FORWARD_DECISION")
    result = copy.deepcopy(ledger)
    result["records"].append(candidate)
    result["ledger_revision"] += 1
    result["status"] = "SHADOW_HISTORY_RECORDED"
    result["summary"] = {
        "record_count": len(result["records"]),
        "decision_date_count": len({row["decision_date"] for row in result["records"]}),
        "market_count": 3,
        "real_capital_deployed": "0",
        "real_order_count": 0,
    }
    result["unresolved_boundaries"] = _boundaries(True)
    result.pop("packet_sha256")
    result["packet_sha256"] = payload_sha256(result)
    return validate_ledger(result, contract)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ThreeMarketShadowLedgerError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(
    decision_path: Path,
    entry_exit_path: Path,
    intraday_risk_path: Path,
    recorded_at: str,
    output_path: Path,
    ledger_path: Path | None = None,
) -> int:
    try:
        previous = None if ledger_path is None else _read_json(ledger_path)
        write_json_atomic(
            output_path,
            append_decision(
                _read_json(decision_path),
                _read_json(entry_exit_path),
                _read_json(intraday_risk_path),
                recorded_at,
                previous,
            ),
        )
        return 0
    except (ThreeMarketShadowLedgerError, OSError, TypeError, ValueError) as exc:
        print(f"Three-market Shadow ledger failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decision", type=Path)
    parser.add_argument("entry_exit", type=Path)
    parser.add_argument("intraday_risk", type=Path)
    parser.add_argument("--recorded-at", required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(
        args.decision, args.entry_exit, args.intraday_risk,
        args.recorded_at, args.out, args.ledger,
    )


if __name__ == "__main__":
    raise SystemExit(main())
