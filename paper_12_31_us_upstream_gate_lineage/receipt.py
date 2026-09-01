#!/usr/bin/env python3
"""Deterministic natural-evidence lineage for US upstream Gates 1-4.

The receipt consumes immutable, already-committed natural observations and
the existing PAPER 12-6 market-judgement implementation.  It does not collect
data, infer an exchange calendar, invent a TTL, ratify a policy, or create a
candidate/entry/exit action.  Observed-but-inadmissible evidence stays UNKNOWN
and every downstream authority remains false.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

from collectors import free_market_data
from regime import us_market_judgement


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).resolve().parent
BINDINGS_PATH = PACKAGE / "exact_bindings.v1.json"
REPORT_PATH = PACKAGE / "natural_gate_receipt.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_SCHEMA = "paper_12_31_us_upstream_gate_receipt/1"


class GateLineageError(ValueError):
    """A pinned source, derivation, or fail-closed invariant failed."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateLineageError(f"JSON_INVALID:{path}") from exc
    if not isinstance(value, dict):
        raise GateLineageError(f"JSON_NOT_OBJECT:{path}")
    return value


def load_bindings(path: Path = BINDINGS_PATH) -> dict:
    value = read_json(path)
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "paper_12_31_us_upstream_gate_lineage/1"
        or value.get("paper_12_6_commit")
        != "f4e1d955d20442326d4f42bf0be2bbbe9e263c5d"
        or value.get("natural_session_date") != "2026-08-31"
    ):
        raise GateLineageError("BINDINGS_IDENTITY_INVALID")
    files = value.get("files")
    if not isinstance(files, dict) or not files:
        raise GateLineageError("BINDINGS_FILES_INVALID")
    for binding_id, binding in files.items():
        if not isinstance(binding, dict) or set(binding) != {"ref", "sha256"}:
            raise GateLineageError(f"BINDING_INVALID:{binding_id}")
        if not isinstance(binding["ref"], str) or not binding["ref"]:
            raise GateLineageError(f"BINDING_REF_INVALID:{binding_id}")
        if not isinstance(binding["sha256"], str) or SHA256_RE.fullmatch(binding["sha256"]) is None:
            raise GateLineageError(f"BINDING_SHA_INVALID:{binding_id}")
    return copy.deepcopy(value)


def verify_bindings(bindings: dict) -> list[dict]:
    checks = []
    for binding_id, binding in bindings["files"].items():
        path = ROOT / binding["ref"]
        actual = file_sha256(path)
        if actual != binding["sha256"]:
            raise GateLineageError(f"BINDING_HASH_MISMATCH:{binding_id}:{actual}")
        checks.append({
            "binding_id": binding_id,
            "ref": binding["ref"],
            "sha256": actual,
            "verified": True,
        })
    return checks


def _verify_free_market_packet(bindings: dict) -> dict:
    path = ROOT / bindings["files"]["free_market_data"]["ref"]
    packet = read_json(path)
    claimed = packet.get("packet_sha256")
    unhashed = copy.deepcopy(packet)
    unhashed.pop("packet_sha256", None)
    if claimed != payload_sha256(unhashed):
        raise GateLineageError("FREE_MARKET_PACKET_SHA_MISMATCH")
    replay = free_market_data.validate_alpaca_daily_evidence(ROOT, packet)
    reference = replay["reference"]
    if (
        packet.get("schema_version") != "free_market_data_capture/5"
        or packet.get("contract_version") != "free_market_data/3"
        or packet.get("alpaca", {}).get("status") != "READY"
        or reference.get("status") != "READY"
        or reference.get("as_of_session_date") != bindings["natural_session_date"]
        or reference.get("coverage", {}).get("ratio") != "15/15"
        or reference.get("interpretation") != "OBSERVED_UNCLASSIFIED"
    ):
        raise GateLineageError("FREE_MARKET_PACKET_SEMANTICS_INVALID")
    if reference.get("proxy_axes", {}).get("LEADERSHIP", {}).get("status") != "OBSERVED":
        raise GateLineageError("LEADERSHIP_REFERENCE_NOT_OBSERVED")
    if reference.get("proxy_axes", {}).get("BREADTH", {}).get("status") != "OBSERVED":
        raise GateLineageError("BREADTH_REFERENCE_NOT_OBSERVED")
    return {
        "ref": bindings["files"]["free_market_data"]["ref"],
        "file_sha256": bindings["files"]["free_market_data"]["sha256"],
        "packet_sha256": claimed,
        "observed_at_utc": packet["observed_at_utc"],
        "session_date": reference["as_of_session_date"],
        "source_scope": reference["source_scope"],
        "daily_raw_sha256": replay["raw_response_sha256"],
        "daily_bar_count": len(packet["alpaca"]["daily_bars"]),
        "reference_coverage": reference["coverage"]["ratio"],
        "trend_observed_count": len(reference["trend_etfs"]),
        "leadership_observed_count": reference["proxy_axes"]["LEADERSHIP"]["measurement"]["observed_count"],
        "representative_breadth_observed_count": reference["proxy_axes"]["BREADTH"]["measurement"]["observed_count"],
        "interpretation": reference["interpretation"],
        "warnings": copy.deepcopy(reference["warnings"]),
    }


def _verify_universe_packet(bindings: dict) -> dict:
    path = ROOT / bindings["files"]["us_global_universe"]["ref"]
    wrapper = read_json(path)
    outer = copy.deepcopy(wrapper)
    claimed_outer = outer.pop("payload_sha256", None)
    if claimed_outer != payload_sha256(outer):
        raise GateLineageError("UNIVERSE_WRAPPER_SHA_MISMATCH")
    packet = wrapper.get("packet")
    if not isinstance(packet, dict):
        raise GateLineageError("UNIVERSE_PACKET_MISSING")
    inner = copy.deepcopy(packet)
    claimed_inner = inner.pop("payload_sha256", None)
    if claimed_inner != payload_sha256(inner):
        raise GateLineageError("UNIVERSE_PACKET_SHA_MISMATCH")
    if (
        wrapper.get("source_date") != bindings["natural_session_date"]
        or packet.get("status") != "FORWARD_SOURCE_COVERAGE_UNIVERSE_VALIDATED"
        or packet.get("policy_status", {}).get("investable_universe_policy") != "UNRATIFIED"
        or packet.get("authority", {}).get("investable_universe_authorized") is not False
        or wrapper.get("authority", {}).get("investable_universe_authorized") is not False
    ):
        raise GateLineageError("UNIVERSE_PACKET_SEMANTICS_INVALID")
    return {
        "ref": bindings["files"]["us_global_universe"]["ref"],
        "file_sha256": bindings["files"]["us_global_universe"]["sha256"],
        "wrapper_payload_sha256": claimed_outer,
        "packet_payload_sha256": claimed_inner,
        "generated_at_utc": wrapper["generated_at"],
        "source_date": wrapper["source_date"],
        "source_coverage_count": packet["total_count"],
        "source_counts": copy.deepcopy(packet["source_counts"]),
        "status": packet["status"],
        "investable_universe_authorized": False,
        "policy_status": copy.deepcopy(packet["policy_status"]),
        "unresolved_boundaries": copy.deepcopy(packet["unresolved_boundaries"]),
    }


def _unknown_judgement_input(bindings: dict) -> dict:
    value = us_market_judgement.build_no_input_baseline(
        bindings["evaluation_at_utc"], bindings["natural_session_date"]
    )
    value["evidenceClass"] = "NATURAL_READ_ONLY"
    for source in value["sources"]:
        source["status"] = "UNKNOWN"
    return value


def _gate_rows(free_packet: dict, universe: dict) -> list[dict]:
    return [
        {
            "gate": 1,
            "name": "FINISHED_SESSION_SOURCE",
            "status": "UNKNOWN",
            "connected_observation": {
                "session_date": free_packet["session_date"],
                "daily_bars": free_packet["daily_bar_count"],
                "reference_coverage": free_packet["reference_coverage"],
                "source_pin": {"ref": free_packet["ref"], "sha256": free_packet["file_sha256"]},
            },
            "blockers": [
                "OFFICIAL_DATE_SPECIFIC_EXCHANGE_CALENDAR_EVIDENCE_ABSENT",
                "COMPLETED_15M_SERIES_ABSENT",
                "COMPLETED_1H_SERIES_ABSENT",
                "FINISHED_SESSION_ADMISSION_RECEIPT_ABSENT",
            ],
        },
        {
            "gate": 2,
            "name": "FRESHNESS",
            "status": "HOLD",
            "connected_observation": {
                "source_observed_at_utc": free_packet["observed_at_utc"],
                "source_hash_verified": True,
            },
            "blockers": [
                "US_FRESHNESS_REPOSITORY_DEFAULT_POLICY_ABSENT",
                "EXTERNAL_RATIFIED_US_FRESHNESS_POLICY_REQUIRED",
                "US_PROVIDER_SLA_UNRATIFIED",
                "US_TTL_NOT_RATIFIED",
            ],
        },
        {
            "gate": 3,
            "name": "FIVE_AXIS_REGIME",
            "status": "HOLD",
            "connected_observation": {
                "trend_reference_count": free_packet["trend_observed_count"],
                "representative_breadth_reference_count": free_packet["representative_breadth_observed_count"],
                "leadership_reference_count": free_packet["leadership_observed_count"],
                "interpretation": free_packet["interpretation"],
            },
            "coverage": "0/5",
            "blockers": [
                "US_REGIME_CLASSIFICATION_POLICY_UNRATIFIED",
                "US_PRICE_BREADTH_NOT_AUTHORIZED",
                "US_LEADERSHIP_POLICY_UNRATIFIED",
                "ALL_REQUIRED_AXES_5_OF_5_NOT_MET",
                "REPRESENTATIVE_ETF_REFERENCE_NOT_CANONICAL_AXIS",
            ],
        },
        {
            "gate": 4,
            "name": "ROTATION_UNIVERSE_TO_CANDIDATE_ENTRY_EXIT",
            "status": "HOLD",
            "connected_observation": {
                "source_coverage_universe_count": universe["source_coverage_count"],
                "rotation_reference_present": free_packet["leadership_observed_count"] > 0,
                "universe_pin": {"ref": universe["ref"], "sha256": universe["file_sha256"]},
            },
            "blockers": [
                "INVESTABLE_UNIVERSE_POLICY_UNRATIFIED",
                "LIQUIDITY_POLICY_ABSENT",
                "SECTOR_LEADERSHIP_CLASSIFICATION_POLICY_UNRATIFIED",
                "FINAL_CANDIDATE_POLICY_UNRATIFIED",
                "ENTRY_POLICY_UNRATIFIED",
                "HOLD_EXIT_POLICY_UNRATIFIED",
                "P8_13_EXECUTABLE_PAPER_PROPOSAL_ABSENT",
            ],
        },
    ]


def build_receipt(bindings_path: Path = BINDINGS_PATH) -> dict:
    bindings = load_bindings(bindings_path)
    binding_checks = verify_bindings(bindings)
    free_packet = _verify_free_market_packet(bindings)
    universe = _verify_universe_packet(bindings)
    judgement_input = _unknown_judgement_input(bindings)
    judgement = us_market_judgement.build_receipt(judgement_input)
    gates = _gate_rows(free_packet, universe)
    blockers = list(dict.fromkeys(
        blocker for gate in gates for blocker in gate["blockers"]
    ))
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "contract_version": bindings["contract_version"],
        "market": "US",
        "session_date": bindings["natural_session_date"],
        "evaluation_at_utc": bindings["evaluation_at_utc"],
        "evidence_class": "NATURAL_READ_ONLY_COMMITTED_SOURCE_OBSERVATIONS",
        "main_audit_commit": bindings["main_audit_commit"],
        "paper_12_6_commit": bindings["paper_12_6_commit"],
        "binding_manifest_sha256": file_sha256(bindings_path),
        "binding_checks": binding_checks,
        "natural_observations": {
            "finished_session_candidate": free_packet,
            "source_coverage_universe": universe,
        },
        "gates": gates,
        "regime_coverage": "0/5",
        "status": "HOLD",
        "judgement": "UNKNOWN",
        "recommendation": "WAIT",
        "action": None,
        "paper_12_6_input": judgement_input,
        "paper_12_6_receipt": judgement,
        "downstream": {
            "paper_12_4": copy.deepcopy(judgement["consumerPins"]["paper_12_4"]),
            "paper_12_1": copy.deepcopy(judgement["consumerPins"]["paper_12_1"]),
            "candidate_receipt": {"status": "NOT_ELIGIBLE", "action": None},
            "entry_receipt": {"status": "NOT_ELIGIBLE", "action": None},
            "exit_receipt": {"status": "NOT_ELIGIBLE", "action": None},
        },
        "audited_local_commits": copy.deepcopy(bindings["audited_local_commits"]),
        "blockers": blockers,
        "next_natural_session": {
            "status": "WAIT_FOR_NEXT_OFFICIAL_FINISHED_US_SESSION",
            "requirements": [
                "OFFICIAL_DATE_SPECIFIC_EXCHANGE_CALENDAR_EVIDENCE",
                "COMPLETED_15M_1H_1D_SOURCE_RECEIPT",
                "EXTERNAL_RATIFIED_US_FRESHNESS_POLICY_AND_TTL",
            ],
        },
        "side_effects": {
            "network": 0,
            "broker": 0,
            "credential": 0,
            "oauth": 0,
            "post": 0,
            "order": 0,
            "ledger_mutation": 0,
            "portal_mutation": 0,
        },
        "authority": {
            "observation_only": True,
            "candidate_authorized": False,
            "entry_authorized": False,
            "exit_action_authorized": False,
            "real": False,
            "live": False,
            "real_capital": False,
            "production": False,
            "trading": False,
        },
    }
    receipt["receipt_sha256"] = payload_sha256(receipt)
    return validate_receipt(receipt, bindings_path, rederive=False)


def validate_receipt(
    receipt: object,
    bindings_path: Path = BINDINGS_PATH,
    *,
    rederive: bool = True,
) -> dict:
    if not isinstance(receipt, dict):
        raise GateLineageError("RECEIPT_NOT_OBJECT")
    claimed = receipt.get("receipt_sha256")
    unhashed = copy.deepcopy(receipt)
    unhashed.pop("receipt_sha256", None)
    if claimed != payload_sha256(unhashed):
        raise GateLineageError("RECEIPT_SHA_MISMATCH")
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("market") != "US"
        or receipt.get("status") != "HOLD"
        or receipt.get("judgement") != "UNKNOWN"
        or receipt.get("recommendation") != "WAIT"
        or receipt.get("action") is not None
        or receipt.get("regime_coverage") != "0/5"
        or [gate.get("status") for gate in receipt.get("gates", [])]
        != ["UNKNOWN", "HOLD", "HOLD", "HOLD"]
    ):
        raise GateLineageError("RECEIPT_FAIL_CLOSED_INVARIANT")
    authority = receipt.get("authority", {})
    if authority.get("observation_only") is not True or any(
        value is not False for key, value in authority.items() if key != "observation_only"
    ):
        raise GateLineageError("RECEIPT_AUTHORITY_INVALID")
    if any(value != 0 for value in receipt.get("side_effects", {}).values()):
        raise GateLineageError("RECEIPT_SIDE_EFFECT_INVALID")
    judgement = receipt.get("paper_12_6_receipt", {})
    if (
        judgement.get("status") != "HOLD"
        or judgement.get("judgement") != "UNKNOWN"
        or judgement.get("regimeOutput", {}).get("coverage", {}).get("ratio") != "0/5"
        or receipt.get("downstream", {}).get("paper_12_4")
        != judgement.get("consumerPins", {}).get("paper_12_4")
        or receipt.get("downstream", {}).get("paper_12_1")
        != judgement.get("consumerPins", {}).get("paper_12_1")
    ):
        raise GateLineageError("PAPER_12_6_PIN_INVALID")
    if rederive and canonical_bytes(receipt) != canonical_bytes(build_receipt(bindings_path)):
        raise GateLineageError("RECEIPT_DERIVATION_MISMATCH")
    return copy.deepcopy(receipt)


if __name__ == "__main__":
    print(json.dumps(build_receipt(), ensure_ascii=False, sort_keys=True, indent=2))
