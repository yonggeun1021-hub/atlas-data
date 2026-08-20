#!/usr/bin/env python3
"""P2-05 external-policy-gated append-only rotation state ledger."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "rotation_state_ledger_contract.json"
POLICY_SCHEMA_VERSION = "rotation_state_policy/1"
LEDGER_SCHEMA_VERSION = "rotation_state_ledger_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
BUCKETS = {"TOP", "MIDDLE", "BOTTOM"}


class RotationStateLedgerError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationStateLedgerError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "rotation_state_ledger/1",
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "state_vocabulary": ["EMERGING", "STRONG", "WEAKENING"],
        "structural_bucket_transitions": [
            "BOTTOM_TO_BOTTOM", "BOTTOM_TO_MIDDLE", "BOTTOM_TO_TOP",
            "MIDDLE_TO_BOTTOM", "MIDDLE_TO_MIDDLE", "MIDDLE_TO_TOP",
            "TOP_TO_BOTTOM", "TOP_TO_MIDDLE", "TOP_TO_TOP",
        ],
        "supported_rotation_packets": {
            "US": {
                "schema_version": "us_capital_rotation_packet/1",
                "contract_version": "us_capital_rotation/1",
                "measurement": "us_theme_relative_rotation_observation",
            },
            "KOREA": {
                "schema_version": "korea_capital_rotation_packet/1",
                "contract_version": "korea_capital_rotation/1",
                "measurement": "korea_theme_relative_rotation_observation",
            },
            "CRYPTO": {
                "schema_version": "crypto_rotation_packet/1",
                "contract_version": "crypto_rotation/1",
                "measurement": "crypto_bucket_relative_rotation_observation",
            },
        },
        "repository_default_policy": "ABSENT",
        "update_semantics": "APPEND_ONLY_IDEMPOTENT_SOURCE_PACKET",
        "policy_timing": "RATIFIED_BEFORE_OBSERVATION_DATE_UTC_BOUNDARY",
        "missing_entity_policy": "NO_STATE_INFERENCE_NO_TOMBSTONE",
        "authority": {
            "external_ratified_state_policy_only": True,
            "regime_input_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "briefing_wiring_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RotationStateLedgerError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RotationStateLedgerError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str):
        raise RotationStateLedgerError(code)
    try:
        result = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise RotationStateLedgerError(code) from exc
    if result.isoformat() != value:
        raise RotationStateLedgerError(code)
    return result


def _timestamp(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise RotationStateLedgerError(code)
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RotationStateLedgerError(code) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise RotationStateLedgerError(code)
    return result.astimezone(dt.timezone.utc)


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RotationStateLedgerError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise RotationStateLedgerError(code)
    return value


def _identity(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise RotationStateLedgerError(code)
    return value


def _positive_int(value, code: str) -> int:
    if type(value) is not int or value < 1:
        raise RotationStateLedgerError(code)
    return value


def _packet_digest(value: dict) -> str:
    digest = _sha(value.get("payload_sha256"), "ROTATION_PACKET_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("payload_sha256")
    if payload_sha256(payload) != digest:
        raise RotationStateLedgerError("ROTATION_PACKET_SHA_MISMATCH")
    return digest


def _validate_observation(row: dict, market: str, scope_id: str) -> dict:
    if not isinstance(row, dict):
        raise RotationStateLedgerError(
            f"ROTATION_OBSERVATION_FIELDS_MISMATCH:{market}"
        )
    common = {
        "prior_bucket", "current_bucket", "bucket_transition", "p2_state",
    }
    if market == "US":
        expected = common | {
            "theme_id", "prior_relative_strength_vs_benchmark",
            "current_relative_strength_vs_benchmark", "relative_strength_change",
            "prior_rank", "current_rank", "rank_change",
        }
        entity_id = source_identity = _token(
            row.get("theme_id"), "ROTATION_ENTITY_INVALID:US"
        )
        current_rank = row.get("current_rank")
    elif market == "KOREA":
        expected = common | {
            "series_identity", "theme_id", "role",
            "prior_relative_strength_vs_benchmark",
            "current_relative_strength_vs_benchmark", "relative_strength_change",
            "prior_rank_within_benchmark", "current_rank_within_benchmark",
            "rank_change_within_benchmark",
        }
        entity_id = _token(row.get("theme_id"), "ROTATION_ENTITY_INVALID:KOREA")
        source_identity = _identity(
            row.get("series_identity"), "ROTATION_SOURCE_IDENTITY_INVALID:KOREA"
        )
        current_rank = row.get("current_rank_within_benchmark")
    else:
        expected = common | {
            "bucket_id", "prior_relative_strength_vs_btc",
            "current_relative_strength_vs_btc", "relative_strength_change",
            "prior_rank", "current_rank", "rank_change",
        }
        entity_id = source_identity = _token(
            row.get("bucket_id"), "ROTATION_ENTITY_INVALID:CRYPTO"
        )
        current_rank = row.get("current_rank")
    if set(row) != expected:
        raise RotationStateLedgerError(f"ROTATION_OBSERVATION_FIELDS_MISMATCH:{market}")
    prior_bucket = row.get("prior_bucket")
    current_bucket = row.get("current_bucket")
    transition = row.get("bucket_transition")
    if (
        prior_bucket not in BUCKETS
        or current_bucket not in BUCKETS
        or transition != f"{prior_bucket}_TO_{current_bucket}"
        or row.get("p2_state") != "UNDEFINED_PENDING_P2_05"
        or _positive_int(current_rank, f"ROTATION_RANK_INVALID:{market}") < 1
    ):
        raise RotationStateLedgerError(f"ROTATION_OBSERVATION_INVALID:{market}")
    return {
        "market": market,
        "scope_id": scope_id,
        "entity_id": entity_id,
        "source_identity": source_identity,
        "structural_bucket_transition": transition,
    }


def _validate_rotation_packet(value: dict, contract: dict) -> dict:
    if not isinstance(value, dict):
        raise RotationStateLedgerError("ROTATION_PACKET_INVALID")
    market = value.get("market")
    if market not in contract["supported_rotation_packets"]:
        raise RotationStateLedgerError("ROTATION_MARKET_UNSUPPORTED")
    expected_identity = contract["supported_rotation_packets"][market]
    expected_fields = {
        "US": {
            "schema_version", "contract_version", "measurement", "market",
            "as_of_date", "status", "benchmark_asset", "observation_pair",
            "taxonomy_binding", "rotation_policy", "rotation_policy_effective",
            "ranking_method", "top_themes", "bottom_themes",
            "theme_observations", "retention", "lineage", "authority",
            "unresolved_boundaries", "payload_sha256",
        },
        "KOREA": {
            "schema_version", "contract_version", "measurement", "market",
            "as_of_date", "status", "observation_pair", "taxonomy_binding",
            "coverage_context", "rotation_policy", "rotation_policy_effective",
            "ranking_method", "benchmark_scopes", "retention", "lineage",
            "authority", "unresolved_boundaries", "payload_sha256",
        },
        "CRYPTO": {
            "schema_version", "contract_version", "measurement", "market",
            "as_of_date", "status", "window_id", "lookback_calendar_days",
            "rotation_policy", "rotation_policy_effective", "ranking_method",
            "top_groups", "bottom_groups", "bucket_observations",
            "sector_chain_layer", "lineage", "authority",
            "unresolved_boundaries", "payload_sha256",
        },
    }
    if set(value) != expected_fields[market]:
        raise RotationStateLedgerError("ROTATION_PACKET_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != expected_identity["schema_version"]
        or value.get("contract_version") != expected_identity["contract_version"]
        or value.get("measurement") != expected_identity["measurement"]
        or value.get("status") != "ROTATION_BUCKETS_OBSERVED"
        or value.get("rotation_policy_effective") is not True
    ):
        raise RotationStateLedgerError("ROTATION_PACKET_IDENTITY_INVALID")
    digest = _packet_digest(value)
    as_of_date = _date(value.get("as_of_date"), "ROTATION_AS_OF_DATE_INVALID")
    rotation_policy = value.get("rotation_policy")
    lineage = value.get("lineage")
    authority = value.get("authority")
    if (
        not isinstance(rotation_policy, dict)
        or not isinstance(lineage, dict)
        or not isinstance(authority, dict)
        or lineage.get("rotation_policy_sha256") != payload_sha256(rotation_policy)
        or authority.get("bucket_transition_authorized") is not True
        or authority.get("p2_state_vocabulary_authorized") is not False
        or authority.get("state_ledger_authorized") is not False
    ):
        raise RotationStateLedgerError("ROTATION_PACKET_AUTHORITY_INVALID")
    rotation_policy_sha = _sha(
        lineage.get("rotation_policy_sha256"), "ROTATION_POLICY_SHA_INVALID"
    )
    rows = []
    scope_ids = []
    if market == "US":
        scope_id = _identity(
            value.get("benchmark_asset"), "ROTATION_SCOPE_INVALID:US"
        )
        observations = value.get("theme_observations")
        scope_ids.append(scope_id)
        if not isinstance(observations, list) or not observations:
            raise RotationStateLedgerError("ROTATION_OBSERVATIONS_INVALID:US")
        rows.extend(
            _validate_observation(item, market, scope_id) for item in observations
        )
    elif market == "KOREA":
        scopes = value.get("benchmark_scopes")
        if not isinstance(scopes, list) or not scopes:
            raise RotationStateLedgerError("ROTATION_SCOPES_INVALID:KOREA")
        for scope in scopes:
            if not isinstance(scope, dict) or set(scope) != {
                "benchmark_identity", "top_themes", "bottom_themes",
                "theme_observations",
            }:
                raise RotationStateLedgerError("ROTATION_SCOPE_FIELDS_INVALID:KOREA")
            scope_id = _identity(
                scope.get("benchmark_identity"), "ROTATION_SCOPE_INVALID:KOREA"
            )
            observations = scope.get("theme_observations")
            if not isinstance(observations, list) or not observations:
                raise RotationStateLedgerError("ROTATION_OBSERVATIONS_INVALID:KOREA")
            scope_ids.append(scope_id)
            rows.extend(
                _validate_observation(item, market, scope_id)
                for item in observations
            )
    else:
        scope_id = "BTC_RELATIVE_BUCKETS"
        observations = value.get("bucket_observations")
        scope_ids.append(scope_id)
        if not isinstance(observations, list) or not observations:
            raise RotationStateLedgerError("ROTATION_OBSERVATIONS_INVALID:CRYPTO")
        rows.extend(
            _validate_observation(item, market, scope_id) for item in observations
        )
    keys = [(item["scope_id"], item["entity_id"]) for item in rows]
    if len(keys) != len(set(keys)) or scope_ids != sorted(set(scope_ids)):
        raise RotationStateLedgerError("ROTATION_ENTITY_OR_SCOPE_DUPLICATE")
    return {
        "market": market,
        "contract_version": value["contract_version"],
        "as_of_date": as_of_date,
        "packet_sha256": digest,
        "rotation_policy_sha256": rotation_policy_sha,
        "scope_ids": scope_ids,
        "observations": sorted(
            rows, key=lambda item: (item["scope_id"], item["entity_id"])
        ),
    }


def _validate_policy(value: dict, rotation: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to", "market",
        "input_rotation_contract_version", "input_rotation_policy_sha256",
        "state_vocabulary", "state_by_bucket_transition",
        "maximum_ledger_gap_days",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RotationStateLedgerError("STATE_POLICY_FIELDS_MISMATCH")
    if value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise RotationStateLedgerError("STATE_POLICY_SCHEMA_MISMATCH")
    _token(value.get("policy_id"), "STATE_POLICY_ID_INVALID")
    if value.get("approval_status") != "RATIFIED":
        raise RotationStateLedgerError("STATE_POLICY_NOT_RATIFIED")
    if not isinstance(value.get("ratified_by"), str) or not value["ratified_by"].strip():
        raise RotationStateLedgerError("STATE_POLICY_RATIFICATION_PROOF_INVALID")
    ratified_at = _timestamp(
        value.get("ratified_at_utc"), "STATE_POLICY_RATIFICATION_PROOF_INVALID"
    )
    if not value["ratified_at_utc"].endswith("Z"):
        raise RotationStateLedgerError("STATE_POLICY_RATIFICATION_PROOF_INVALID")
    as_of_boundary = dt.datetime.combine(
        rotation["as_of_date"], dt.time.min, tzinfo=dt.timezone.utc
    )
    if ratified_at >= as_of_boundary:
        raise RotationStateLedgerError("STATE_POLICY_RATIFIED_TOO_LATE")
    start = _date(value.get("effective_from"), "STATE_POLICY_EFFECTIVE_FROM_INVALID")
    end = (
        None
        if value.get("effective_to") is None
        else _date(value["effective_to"], "STATE_POLICY_EFFECTIVE_TO_INVALID")
    )
    if end is not None and end <= start:
        raise RotationStateLedgerError("STATE_POLICY_EFFECTIVE_TO_INVALID")
    if not (start <= rotation["as_of_date"] and (end is None or rotation["as_of_date"] < end)):
        raise RotationStateLedgerError("STATE_POLICY_NOT_EFFECTIVE")
    if (
        value.get("market") != rotation["market"]
        or value.get("input_rotation_contract_version") != rotation["contract_version"]
        or value.get("input_rotation_policy_sha256") != rotation["rotation_policy_sha256"]
    ):
        raise RotationStateLedgerError("STATE_POLICY_INPUT_BINDING_MISMATCH")
    if value.get("state_vocabulary") != contract["state_vocabulary"]:
        raise RotationStateLedgerError("STATE_POLICY_VOCABULARY_MISMATCH")
    mapping = value.get("state_by_bucket_transition")
    if (
        not isinstance(mapping, dict)
        or set(mapping) != set(contract["structural_bucket_transitions"])
        or any(state not in contract["state_vocabulary"] for state in mapping.values())
        or set(mapping.values()) != set(contract["state_vocabulary"])
    ):
        raise RotationStateLedgerError("STATE_POLICY_MAPPING_INVALID")
    _positive_int(
        value.get("maximum_ledger_gap_days"), "STATE_POLICY_LEDGER_GAP_INVALID"
    )
    return copy.deepcopy(value)


def _authority(contract: dict, observed: bool) -> dict:
    return copy.deepcopy(contract["authority"]) | {
        "p2_state_vocabulary_authorized": observed,
        "state_ledger_authorized": observed,
    }


def empty_ledger(contract: Optional[dict] = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    ledger = {
        "schema_version": contract["ledger_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "EMPTY",
        "ledger_revision": 0,
        "source_packets": [],
        "records": [],
        "authority": _authority(contract, False),
        "unresolved_boundaries": [
            "EXTERNAL_STATE_POLICY_NOT_PROVIDED",
            "LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
            "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    ledger["payload_sha256"] = payload_sha256(ledger)
    return ledger


def _validate_ledger(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "status", "ledger_revision",
        "source_packets", "records", "authority", "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RotationStateLedgerError("LEDGER_FIELDS_MISMATCH")
    digest = _sha(value.get("payload_sha256"), "LEDGER_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("payload_sha256")
    if payload_sha256(payload) != digest:
        raise RotationStateLedgerError("LEDGER_SHA_MISMATCH")
    sources = value.get("source_packets")
    records = value.get("records")
    revision = value.get("ledger_revision")
    if (
        value.get("schema_version") != contract["ledger_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or type(revision) is not int
        or revision < 0
        or not isinstance(sources, list)
        or not isinstance(records, list)
        or revision != len(sources)
    ):
        raise RotationStateLedgerError("LEDGER_IDENTITY_INVALID")
    observed = bool(records)
    if (
        value.get("status") != ("STATE_HISTORY_OBSERVED" if observed else "EMPTY")
        or value.get("authority") != _authority(contract, observed)
    ):
        raise RotationStateLedgerError("LEDGER_AUTHORITY_INVALID")
    expected_boundaries = (
        [
            "LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
            "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
            "PRODUCTION_NOT_AUTHORIZED",
        ]
        if observed
        else [
            "EXTERNAL_STATE_POLICY_NOT_PROVIDED",
            "LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
            "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
            "PRODUCTION_NOT_AUTHORIZED",
        ]
    )
    if value.get("unresolved_boundaries") != expected_boundaries:
        raise RotationStateLedgerError("LEDGER_BOUNDARIES_INVALID")
    source_fields = {
        "ledger_revision", "market", "scope_ids", "as_of_date",
        "input_packet_sha256", "input_rotation_contract_version",
        "input_rotation_policy_sha256", "state_policy",
        "state_policy_sha256", "record_count",
    }
    source_by_sha = {}
    for expected_revision, item in enumerate(sources, 1):
        if not isinstance(item, dict) or set(item) != source_fields:
            raise RotationStateLedgerError("LEDGER_SOURCE_FIELDS_MISMATCH")
        if (
            item.get("ledger_revision") != expected_revision
            or item.get("market") not in contract["supported_rotation_packets"]
            or not isinstance(item.get("scope_ids"), list)
            or not item["scope_ids"]
            or any(
                not isinstance(scope_id, str) or not scope_id.strip()
                for scope_id in item["scope_ids"]
            )
            or item["scope_ids"] != sorted(set(item["scope_ids"]))
            or _positive_int(item.get("record_count"), "LEDGER_SOURCE_INVALID") < 1
        ):
            raise RotationStateLedgerError("LEDGER_SOURCE_INVALID")
        source_date = _date(item.get("as_of_date"), "LEDGER_SOURCE_DATE_INVALID")
        packet_sha = _sha(item.get("input_packet_sha256"), "LEDGER_SOURCE_SHA_INVALID")
        rotation_policy_sha = _sha(
            item.get("input_rotation_policy_sha256"), "LEDGER_SOURCE_SHA_INVALID"
        )
        state_policy_sha = _sha(
            item.get("state_policy_sha256"), "LEDGER_SOURCE_SHA_INVALID"
        )
        stored_policy = item.get("state_policy")
        stored_rotation = {
            "market": item["market"],
            "contract_version": item.get("input_rotation_contract_version"),
            "rotation_policy_sha256": rotation_policy_sha,
            "as_of_date": source_date,
        }
        if (
            _validate_policy(stored_policy, stored_rotation, contract)
            != stored_policy
            or payload_sha256(stored_policy) != state_policy_sha
        ):
            raise RotationStateLedgerError("LEDGER_STORED_POLICY_INVALID")
        if packet_sha in source_by_sha:
            raise RotationStateLedgerError("LEDGER_SOURCE_DUPLICATE")
        source_by_sha[packet_sha] = item
    record_fields = {
        "ledger_revision", "market", "scope_id", "entity_id", "source_identity",
        "as_of_date", "structural_bucket_transition", "prior_p2_state",
        "current_p2_state", "state_transition", "input_packet_sha256",
        "state_policy_sha256", "prior_record_sha256", "record_sha256",
    }
    last_by_key = {}
    previous_revision = 0
    record_keys_by_revision = {}
    for item in records:
        if not isinstance(item, dict) or set(item) != record_fields:
            raise RotationStateLedgerError("LEDGER_RECORD_FIELDS_MISMATCH")
        item_revision = _positive_int(item.get("ledger_revision"), "LEDGER_RECORD_INVALID")
        if item_revision < previous_revision or item_revision > revision:
            raise RotationStateLedgerError("LEDGER_RECORD_ORDER_INVALID")
        previous_revision = item_revision
        market = item.get("market")
        scope_id = _identity(item.get("scope_id"), "LEDGER_RECORD_SCOPE_INVALID")
        entity_id = _token(item.get("entity_id"), "LEDGER_RECORD_ENTITY_INVALID")
        _identity(item.get("source_identity"), "LEDGER_RECORD_SOURCE_IDENTITY_INVALID")
        day = _date(item.get("as_of_date"), "LEDGER_RECORD_DATE_INVALID")
        source_sha = _sha(item.get("input_packet_sha256"), "LEDGER_RECORD_SHA_INVALID")
        policy_sha = _sha(item.get("state_policy_sha256"), "LEDGER_RECORD_SHA_INVALID")
        source = source_by_sha.get(source_sha)
        if (
            source is None
            or source["ledger_revision"] != item_revision
            or source["market"] != market
            or scope_id not in source["scope_ids"]
            or source["as_of_date"] != day.isoformat()
            or source["state_policy_sha256"] != policy_sha
            or item.get("structural_bucket_transition") not in contract["structural_bucket_transitions"]
            or item.get("current_p2_state") not in contract["state_vocabulary"]
        ):
            raise RotationStateLedgerError("LEDGER_RECORD_SOURCE_MISMATCH")
        key = (market, scope_id, entity_id)
        prior = last_by_key.get(key)
        expected_prior_state = None if prior is None else prior["current_p2_state"]
        expected_prior_sha = None if prior is None else prior["record_sha256"]
        expected_transition = (
            f"UNINITIALIZED_TO_{item['current_p2_state']}"
            if prior is None
            else f"{prior['current_p2_state']}_TO_{item['current_p2_state']}"
        )
        if (
            item.get("prior_p2_state") != expected_prior_state
            or item.get("prior_record_sha256") != expected_prior_sha
            or item.get("state_transition") != expected_transition
            or (
                prior is not None
                and item.get("source_identity") != prior["source_identity"]
            )
        ):
            raise RotationStateLedgerError("LEDGER_RECORD_CHAIN_INVALID")
        record_sha = _sha(item.get("record_sha256"), "LEDGER_RECORD_SHA_INVALID")
        record_payload = copy.deepcopy(item)
        record_payload.pop("record_sha256")
        if payload_sha256(record_payload) != record_sha:
            raise RotationStateLedgerError("LEDGER_RECORD_SHA_MISMATCH")
        last_by_key[key] = item
        record_keys_by_revision.setdefault(item_revision, []).append(
            (scope_id, entity_id)
        )
    for source in sources:
        revision_keys = record_keys_by_revision.get(source["ledger_revision"], [])
        if (
            len(revision_keys) != source["record_count"]
            or revision_keys != sorted(set(revision_keys))
            or sorted({item[0] for item in revision_keys}) != source["scope_ids"]
        ):
            raise RotationStateLedgerError("LEDGER_SOURCE_RECORD_COVERAGE_MISMATCH")
    return copy.deepcopy(value)


def validate_ledger(value: dict, contract: Optional[dict] = None) -> dict:
    """Public read-only validator for downstream audit/read-model consumers."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    return _validate_ledger(value, contract)


def apply_rotation(
    rotation_packet: dict,
    state_policy: dict,
    previous_ledger: Optional[dict] = None,
    contract: Optional[dict] = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    rotation = _validate_rotation_packet(rotation_packet, contract)
    policy = _validate_policy(state_policy, rotation, contract)
    ledger = _validate_ledger(
        empty_ledger(contract) if previous_ledger is None else previous_ledger,
        contract,
    )
    policy_sha = payload_sha256(policy)
    duplicate = next(
        (
            item
            for item in ledger["source_packets"]
            if item["input_packet_sha256"] == rotation["packet_sha256"]
        ),
        None,
    )
    if duplicate is not None:
        if duplicate["state_policy_sha256"] != policy_sha:
            raise RotationStateLedgerError("SOURCE_PACKET_POLICY_CONFLICT")
        return ledger
    latest_source_by_scope = {}
    for source in ledger["source_packets"]:
        if source["market"] != rotation["market"]:
            continue
        for scope_id in source["scope_ids"]:
            latest_source_by_scope[scope_id] = _date(
                source["as_of_date"], "LEDGER_SOURCE_DATE_INVALID"
            )
    for scope_id in rotation["scope_ids"]:
        previous_date = latest_source_by_scope.get(scope_id)
        if previous_date is not None and rotation["as_of_date"] <= previous_date:
            raise RotationStateLedgerError("LEDGER_NON_FORWARD_OBSERVATION")
        if (
            previous_date is not None
            and (rotation["as_of_date"] - previous_date).days
            > policy["maximum_ledger_gap_days"]
        ):
            raise RotationStateLedgerError("LEDGER_GAP_EXCEEDS_POLICY")
    result = copy.deepcopy(ledger)
    revision = result["ledger_revision"] + 1
    result["source_packets"].append({
        "ledger_revision": revision,
        "market": rotation["market"],
        "scope_ids": rotation["scope_ids"],
        "as_of_date": rotation["as_of_date"].isoformat(),
        "input_packet_sha256": rotation["packet_sha256"],
        "input_rotation_contract_version": rotation["contract_version"],
        "input_rotation_policy_sha256": rotation["rotation_policy_sha256"],
        "state_policy": policy,
        "state_policy_sha256": policy_sha,
        "record_count": len(rotation["observations"]),
    })
    last_by_key = {
        (item["market"], item["scope_id"], item["entity_id"]): item
        for item in result["records"]
    }
    for observation in rotation["observations"]:
        key = (
            observation["market"], observation["scope_id"], observation["entity_id"]
        )
        prior = last_by_key.get(key)
        if (
            prior is not None
            and prior["source_identity"] != observation["source_identity"]
        ):
            raise RotationStateLedgerError("LEDGER_SOURCE_IDENTITY_DRIFT")
        current_state = policy["state_by_bucket_transition"][
            observation["structural_bucket_transition"]
        ]
        record = {
            "ledger_revision": revision,
            "market": observation["market"],
            "scope_id": observation["scope_id"],
            "entity_id": observation["entity_id"],
            "source_identity": observation["source_identity"],
            "as_of_date": rotation["as_of_date"].isoformat(),
            "structural_bucket_transition": observation[
                "structural_bucket_transition"
            ],
            "prior_p2_state": None if prior is None else prior["current_p2_state"],
            "current_p2_state": current_state,
            "state_transition": (
                f"UNINITIALIZED_TO_{current_state}"
                if prior is None
                else f"{prior['current_p2_state']}_TO_{current_state}"
            ),
            "input_packet_sha256": rotation["packet_sha256"],
            "state_policy_sha256": policy_sha,
            "prior_record_sha256": None if prior is None else prior["record_sha256"],
        }
        record["record_sha256"] = payload_sha256(record)
        result["records"].append(record)
        last_by_key[key] = record
    result["status"] = "STATE_HISTORY_OBSERVED"
    result["ledger_revision"] = revision
    result["authority"] = _authority(contract, True)
    result["unresolved_boundaries"] = [
        "LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
        "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]
    result.pop("payload_sha256")
    result["payload_sha256"] = payload_sha256(result)
    return _validate_ledger(result, contract)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise RotationStateLedgerError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
    rotation_path: Path,
    policy_path: Path,
    output_path: Path,
    ledger_path: Optional[Path] = None,
) -> int:
    try:
        previous = None if ledger_path is None else _read_json(ledger_path)
        result = apply_rotation(
            _read_json(rotation_path), _read_json(policy_path), previous
        )
        write_json_atomic(output_path, result)
        return 0
    except (RotationStateLedgerError, OSError, TypeError, ValueError) as exc:
        print(f"Rotation state ledger failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply a ratified state policy to one rotation packet"
    )
    parser.add_argument("rotation_packet", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.rotation_packet, args.policy, args.out, args.ledger)


if __name__ == "__main__":
    raise SystemExit(main())
