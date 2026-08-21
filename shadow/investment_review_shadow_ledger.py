#!/usr/bin/env python3
"""P10-06 append-only zero-capital ledger for P8-07 review packets."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "investment_review_shadow_ledger_contract.json"
P8_PATH = ROOT / "decision" / "investment_decision_review.py"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_p8():
    spec = importlib.util.spec_from_file_location("p10_05_p8_validator", P8_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P8 = _load_p8()


class InvestmentReviewShadowLedgerError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvestmentReviewShadowLedgerError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract():
    return {
        "schema_version": 1,
        "contract_version": "investment_review_shadow_ledger/1",
        "output_schema_version": "investment_review_shadow_record/1",
        "authority": {
            "append_only_review_observation": True,
            "stage_change_authorized": False,
            "shadow_eligibility_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value):
    if not isinstance(value, dict) or value != _expected_contract():
        raise InvestmentReviewShadowLedgerError("CONTRACT_IDENTITY_INVALID")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH):
    return _validate_contract(_read(path))


def _sha(value, code):
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise InvestmentReviewShadowLedgerError(code)
    return value


def build_record(review_packet: dict, recorded_at: str, sequence: int,
                 previous_record_sha256: str | None = None,
                 contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = P8.validate_packet(review_packet)
    if not isinstance(recorded_at, str) or UTC_RE.fullmatch(recorded_at) is None:
        raise InvestmentReviewShadowLedgerError("RECORDED_AT_INVALID")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise InvestmentReviewShadowLedgerError("SEQUENCE_INVALID")
    if sequence == 1:
        if previous_record_sha256 is not None:
            raise InvestmentReviewShadowLedgerError("GENESIS_PREVIOUS_SHA_MUST_BE_NULL")
    else:
        _sha(previous_record_sha256, "PREVIOUS_RECORD_SHA_INVALID")
    outcome = checked["buy_review"]["outcome"]
    record = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "sequence": sequence,
        "recorded_at": recorded_at,
        "subject": checked["subject"],
        "review_outcome": outcome,
        "proposal_observed": checked["trade_proposal"] is not None,
        "review_packet": checked,
        "lineage": {
            "review_packet_sha256": checked["packet_sha256"],
            "previous_record_sha256": previous_record_sha256,
        },
        "capital": {"authorized": False, "amount": 0},
        "action": None,
        "order": None,
        "stage_change": None,
        "authority": copy.deepcopy(contract["authority"]),
    }
    record["record_sha256"] = payload_sha256(record)
    return validate_record(record, contract)


def validate_record(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "sequence", "recorded_at", "subject",
        "review_outcome", "proposal_observed", "review_packet", "lineage", "capital",
        "action", "order", "stage_change", "authority", "record_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise InvestmentReviewShadowLedgerError("RECORD_FIELDS_MISMATCH")
    checked = P8.validate_packet(value.get("review_packet"))
    if (
        value.get("schema_version") != contract["output_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["authority"]
        or value.get("subject") != checked["subject"]
        or value.get("review_outcome") != checked["buy_review"]["outcome"]
        or value.get("proposal_observed") is not (checked["trade_proposal"] is not None)
        or value.get("capital") != {"authorized": False, "amount": 0}
        or value.get("action") is not None
        or value.get("order") is not None
        or value.get("stage_change") is not None
    ):
        raise InvestmentReviewShadowLedgerError("RECORD_BOUNDARY_INVALID")
    sequence = value.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise InvestmentReviewShadowLedgerError("RECORD_SEQUENCE_INVALID")
    lineage = value.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {
        "review_packet_sha256", "previous_record_sha256"
    }:
        raise InvestmentReviewShadowLedgerError("RECORD_LINEAGE_INVALID")
    if lineage["review_packet_sha256"] != checked["packet_sha256"]:
        raise InvestmentReviewShadowLedgerError("REVIEW_PACKET_SHA_MISMATCH")
    if sequence == 1 and lineage["previous_record_sha256"] is not None:
        raise InvestmentReviewShadowLedgerError("GENESIS_PREVIOUS_SHA_MUST_BE_NULL")
    if sequence > 1:
        _sha(lineage["previous_record_sha256"], "PREVIOUS_RECORD_SHA_INVALID")
    digest = _sha(value.get("record_sha256"), "RECORD_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("record_sha256")
    if payload_sha256(payload) != digest:
        raise InvestmentReviewShadowLedgerError("RECORD_SHA_MISMATCH")
    return copy.deepcopy(value)
