#!/usr/bin/env python3
"""Build a fail-closed, point-in-time Korea universe registry.

The builder joins KIS's official KOSPI/KOSDAQ master files to the existing
exact-date official KRX stock source-coverage packet.  Full records are private
evidence.  The public projection contains only hashes, lineage, aggregate
counts, policy state, and authority flags.

This module does not fetch providers, invent liquidity thresholds, approve an
investable universe, or authorize PAPER/REAL orders.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universe import global_asset_master as GAM  # noqa: E402
from universe import krx_global_universe as KRU  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "krx_investable_registry_contract.json"
COMMON_SAFETY_GATE_CONTRACT_PATH = (
    ROOT / "config" / "krx_paper_common_safety_gate_contract.json"
)
KRX_MARKET_GATE_CONTRACT_PATH = ROOT / "config" / "krx_paper_market_gate_contract.json"
INPUT_SCHEMA_VERSION = "krx_investable_registry_input/1"
OUTPUT_SCHEMA_VERSION = "krx_investable_registry/1"
PUBLIC_SUMMARY_SCHEMA_VERSION = "krx_investable_registry_public_summary/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STANDARD_CODE_RE = re.compile(r"^[A-Z0-9]{12}$")
SHORT_CODE_RE = re.compile(r"^[A-Z0-9]{1,9}$")


KOSPI_WIDTHS = [
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1,
    1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1,
    1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
]
KOSPI_FIELDS = [
    "security_group", "market_cap_scale", "sector_large", "sector_medium",
    "sector_small", "manufacturing", "low_liquidity", "governance_index",
    "kospi200_sector", "kospi100", "kospi50", "krx_issue", "etp_code",
    "elw_issuer", "krx100", "krx_auto", "krx_semiconductor", "krx_bio",
    "krx_bank", "spac", "krx_energy_chemical", "krx_steel", "short_heat",
    "krx_media", "krx_construction", "deleted_field", "krx_securities",
    "krx_ship", "krx_insurance", "krx_transport", "sri", "base_price",
    "regular_lot", "after_hours_lot", "trading_halt", "liquidation_trading",
    "managed_issue", "market_warning", "warning_advance", "unfaithful_disclosure",
    "backdoor_listing", "lock_code", "face_value_change", "capital_change",
    "margin_rate", "credit_available", "credit_days", "previous_volume",
    "face_value", "listing_date", "listed_shares", "capital", "closing_month",
    "offer_price", "preferred_code", "short_sale_hot", "abnormal_surge",
    "krx300", "kospi_issue", "sales", "operating_profit", "ordinary_profit",
    "net_income", "roe", "base_year_month", "market_cap", "group_code",
    "credit_limit_exceeded", "collateral_loan", "stock_lending",
]
KOSDAQ_WIDTHS = [
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3,
    1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9,
    8, 9, 3, 1, 1, 1,
]
KOSDAQ_FIELDS = [
    "security_group", "market_cap_scale", "sector_large", "sector_medium",
    "sector_small", "venture", "low_liquidity", "krx_issue", "etp_code",
    "krx100", "krx_auto", "krx_semiconductor", "krx_bio", "krx_bank",
    "spac", "krx_energy_chemical", "krx_steel", "short_heat", "krx_media",
    "krx_construction", "investment_attention", "krx_securities", "krx_ship",
    "krx_insurance", "krx_transport", "kosdaq150", "base_price", "regular_lot",
    "after_hours_lot", "trading_halt", "liquidation_trading", "managed_issue",
    "market_warning", "warning_advance", "unfaithful_disclosure",
    "backdoor_listing", "lock_code", "face_value_change", "capital_change",
    "margin_rate", "credit_available", "credit_days", "previous_volume",
    "face_value", "listing_date", "listed_shares", "capital", "closing_month",
    "offer_price", "preferred_code", "short_sale_hot", "abnormal_surge",
    "krx300", "sales", "operating_profit", "ordinary_profit", "net_income",
    "roe", "base_year_month", "market_cap", "group_code",
    "credit_limit_exceeded", "collateral_loan", "stock_lending",
]
MASTER_LAYOUT = {
    "KOSPI": (KOSPI_WIDTHS, KOSPI_FIELDS),
    "KOSDAQ": (KOSDAQ_WIDTHS, KOSDAQ_FIELDS),
}


class RegistryError(ValueError):
    """A registry cannot be built without violating its evidence contract."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"JSON_READ_FAILED:{path}:{type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise RegistryError(f"JSON_ROOT_NOT_OBJECT:{path}")
    return value


def _parse_time(value: object, code: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RegistryError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RegistryError(code)
    return parsed.astimezone(dt.timezone.utc)


def _valid_date(value: object) -> bool:
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if value.get("schema_version") != 1:
        raise RegistryError("CONTRACT_SCHEMA_MISMATCH")
    if value.get("contract_version") != "krx_investable_registry/1":
        raise RegistryError("CONTRACT_VERSION_MISMATCH")
    if value.get("output_schema_version") != OUTPUT_SCHEMA_VERSION:
        raise RegistryError("CONTRACT_OUTPUT_SCHEMA_MISMATCH")
    if value.get("public_summary_schema_version") != PUBLIC_SUMMARY_SCHEMA_VERSION:
        raise RegistryError("CONTRACT_PUBLIC_SCHEMA_MISMATCH")
    if value.get("markets") != ["KOSDAQ", "KOSPI"]:
        raise RegistryError("CONTRACT_MARKETS_MISMATCH")
    authority = value.get("authority")
    expected_false = {
        "investable_universe_authorized", "strategy_entry_authorized",
        "paper_order_authorized", "real_order_authorized", "production_authorized",
        "trading_authorized",
    }
    if not isinstance(authority, dict) or authority.get("registry_evidence_only") is not True:
        raise RegistryError("CONTRACT_AUTHORITY_INVALID")
    if any(authority.get(key) is not False for key in expected_false):
        raise RegistryError("CONTRACT_AUTHORITY_PROMOTED")
    measurements = value.get("measurement_policy")
    if not isinstance(measurements, dict) or set(measurements) != {
        "turnover", "order_book_depth", "spread", "slippage"
    }:
        raise RegistryError("CONTRACT_MEASUREMENT_POLICY_INVALID")
    for policy in measurements.values():
        if policy != {"status": "UNRATIFIED", "proposed_threshold": None}:
            raise RegistryError("CONTRACT_MEASUREMENT_THRESHOLD_RATIFIED")
    _gate_compatibility_projection(value)
    return copy.deepcopy(value)


def _gate_compatibility_projection(contract: dict) -> dict:
    compatibility = contract.get("krx_paper_gate_compatibility")
    expected = {
        "common_safety_contract_version": "krx_paper_common_safety_gate/1",
        "market_gate_contract_version": "krx_paper_market_gate/1",
        "evidence_targets": [
            "COMMON_PIT_AND_IMMUTABLE_LINEAGE",
            "KRX_FINAL_CANDIDATE_POLICY_RATIFIED",
        ],
        "evidence_role": "NON_AUTHORITY_EVIDENCE_CANDIDATE",
        "gate_result_authorized": False,
        "state_transition_authorized": False,
    }
    if compatibility != expected:
        raise RegistryError("CONTRACT_KRX_PAPER_GATE_COMPATIBILITY_INVALID")

    common = _read_json(COMMON_SAFETY_GATE_CONTRACT_PATH)
    market = _read_json(KRX_MARKET_GATE_CONTRACT_PATH)
    if common.get("contract_version") != compatibility["common_safety_contract_version"]:
        raise RegistryError("COMMON_SAFETY_GATE_CONTRACT_VERSION_MISMATCH")
    if market.get("contract_version") != compatibility["market_gate_contract_version"]:
        raise RegistryError("KRX_MARKET_GATE_CONTRACT_VERSION_MISMATCH")
    if market.get("market") != "KOREA":
        raise RegistryError("KRX_MARKET_GATE_MARKET_MISMATCH")

    common_ids = {row.get("id") for row in common.get("checks", []) if isinstance(row, dict)}
    market_ids = {
        row.get("id") for row in market.get("check_definitions", [])
        if isinstance(row, dict)
    }
    if compatibility["evidence_targets"][0] not in common_ids:
        raise RegistryError("COMMON_SAFETY_GATE_EVIDENCE_TARGET_MISSING")
    if compatibility["evidence_targets"][1] not in market_ids:
        raise RegistryError("KRX_MARKET_GATE_EVIDENCE_TARGET_MISSING")

    permanent = market.get("permanent_authority_boundary")
    required_permanent_false = {
        "real_capital_authorized",
        "live_account_order_authorized",
        "production_authorized",
        "trading_authorized",
    }
    if not isinstance(permanent, dict) or any(
        permanent.get(key) is not False for key in required_permanent_false
    ):
        raise RegistryError("KRX_MARKET_GATE_PERMANENT_AUTHORITY_PROMOTED")
    locked = market.get("authority_by_state", {}).get("LOCKED")
    if locked != {
        "internal_virtual_ledger_paper_authorized": False,
        "kis_mock_account_auto_order_authorized": False,
    }:
        raise RegistryError("KRX_MARKET_GATE_LOCKED_AUTHORITY_INVALID")
    common_invariants = common.get("invariants")
    if not isinstance(common_invariants, dict) or any(
        common_invariants.get(key) is not False
        for key in (
            "real_capital_authorized",
            "live_order_submission_authorized",
            "secret_values_permitted_in_evidence",
        )
    ):
        raise RegistryError("COMMON_SAFETY_GATE_INVARIANT_INVALID")

    return {
        "market": "KOREA",
        "contract_versions": {
            "common_safety": common["contract_version"],
            "krx_market": market["contract_version"],
        },
        "contract_sha256": {
            "common_safety": payload_sha256(common),
            "krx_market": payload_sha256(market),
        },
        "evidence_targets": copy.deepcopy(compatibility["evidence_targets"]),
        "evidence_role": compatibility["evidence_role"],
        "evidence_state": "INSUFFICIENT",
        "evidence_reason_codes": [
            "COMMON_PIT_OR_LINEAGE_NOT_PROVEN",
            "KRX_FINAL_CANDIDATE_AUTHORITY_UNRATIFIED",
        ],
        "current_state_claim": None,
        "gate_result_authorized": False,
        "state_transition_authorized": False,
        "authority": {
            "internal_virtual_ledger_paper_authorized": False,
            "kis_mock_account_auto_order_authorized": False,
            **copy.deepcopy(permanent),
        },
    }


def _validate_input(value: dict, contract: dict) -> dict:
    if value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise RegistryError("INPUT_SCHEMA_MISMATCH")
    captured = _parse_time(value.get("captured_at_utc"), "CAPTURED_AT_INVALID")
    if captured > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise RegistryError("CAPTURED_AT_IN_FUTURE")
    expected_date = value.get("latest_completed_session_date")
    if not _valid_date(expected_date):
        raise RegistryError("LATEST_COMPLETED_SESSION_DATE_INVALID")
    session_evidence = value.get("latest_session_evidence")
    if not isinstance(session_evidence, dict):
        raise RegistryError("LATEST_SESSION_EVIDENCE_MISSING")
    if session_evidence.get("as_of_date") != expected_date:
        raise RegistryError("LATEST_SESSION_EVIDENCE_DATE_MISMATCH")
    if not SHA256_RE.fullmatch(str(session_evidence.get("source_sha256") or "")):
        raise RegistryError("LATEST_SESSION_EVIDENCE_SHA_INVALID")
    evidence_path = session_evidence.get("path")
    if not isinstance(evidence_path, str) or not evidence_path:
        raise RegistryError("LATEST_SESSION_EVIDENCE_PATH_INVALID")
    try:
        evidence_bytes = Path(evidence_path).read_bytes()
        evidence_packet = json.loads(evidence_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"LATEST_SESSION_EVIDENCE_READ_FAILED:{type(exc).__name__}") from exc
    if hashlib.sha256(evidence_bytes).hexdigest() != session_evidence["source_sha256"]:
        raise RegistryError("LATEST_SESSION_EVIDENCE_FILE_SHA_MISMATCH")
    if not isinstance(evidence_packet, dict):
        raise RegistryError("LATEST_SESSION_EVIDENCE_ROOT_INVALID")
    if evidence_packet.get("schema_version") != contract["krx_primary_source"]["required_latest_session_evidence_schema"]:
        raise RegistryError("LATEST_SESSION_EVIDENCE_SCHEMA_MISMATCH")
    if evidence_packet.get("as_of_date") != expected_date:
        raise RegistryError("LATEST_SESSION_EVIDENCE_PACKET_DATE_MISMATCH")
    embedded_digest = evidence_packet.get("payload_sha256")
    unsigned_evidence = copy.deepcopy(evidence_packet)
    unsigned_evidence.pop("payload_sha256", None)
    if embedded_digest != payload_sha256(unsigned_evidence):
        raise RegistryError("LATEST_SESSION_EVIDENCE_PAYLOAD_SHA_MISMATCH")
    evidence_authority = evidence_packet.get("authority")
    if not isinstance(evidence_authority, dict) or evidence_authority.get("observation_only") is not True:
        raise RegistryError("LATEST_SESSION_EVIDENCE_AUTHORITY_INVALID")
    for key in ("action_authorized", "order_authorized", "production_authorized", "strategy_authorized", "trading_authorized"):
        if evidence_authority.get(key) is not False:
            raise RegistryError("LATEST_SESSION_EVIDENCE_AUTHORITY_PROMOTED")
    masters = value.get("masters")
    if not isinstance(masters, dict) or set(masters) != set(contract["markets"]):
        raise RegistryError("MASTER_INPUTS_MISMATCH")
    for market in contract["markets"]:
        source = masters[market]
        if not isinstance(source, dict):
            raise RegistryError(f"MASTER_SOURCE_INVALID:{market}")
        if source.get("source_url") != contract["kis_primary_source"]["master_urls"][market]:
            raise RegistryError(f"MASTER_URL_MISMATCH:{market}")
        if not isinstance(source.get("path"), str) or not source["path"]:
            raise RegistryError(f"MASTER_PATH_INVALID:{market}")
        _parse_time(source.get("retrieved_at_utc"), f"MASTER_RETRIEVED_AT_INVALID:{market}")
        if source.get("http_last_modified") is not None:
            _parse_time(source["http_last_modified"], f"MASTER_LAST_MODIFIED_INVALID:{market}")
    if value.get("kis_parser_commit") != contract["kis_primary_source"]["parser_commit"]:
        raise RegistryError("KIS_PARSER_COMMIT_MISMATCH")
    if not isinstance(value.get("krx_packet_path"), str) or not value["krx_packet_path"]:
        raise RegistryError("KRX_PACKET_PATH_INVALID")
    previous = value.get("previous_registry_path")
    if previous is not None and (not isinstance(previous, str) or not previous):
        raise RegistryError("PREVIOUS_REGISTRY_PATH_INVALID")
    cleaned = copy.deepcopy(value)
    cleaned["latest_session_evidence"] = {
        "source_name": session_evidence.get("source_name"),
        "schema_version": evidence_packet["schema_version"],
        "as_of_date": expected_date,
        "source_sha256": session_evidence["source_sha256"],
        "payload_sha256": embedded_digest,
    }
    return cleaned


def _read_master(source: dict, market: str, contract: dict) -> tuple[list[dict], dict]:
    path = Path(source["path"])
    try:
        archive_bytes = path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"MASTER_READ_FAILED:{market}:{type(exc).__name__}") from exc
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            expected = contract["kis_primary_source"]["archive_members"][market]
            if names != [expected]:
                raise RegistryError(f"MASTER_ARCHIVE_MEMBERS_MISMATCH:{market}")
            raw = archive.read(expected)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise RegistryError(f"MASTER_ARCHIVE_INVALID:{market}:{type(exc).__name__}") from exc
    raw_sha = hashlib.sha256(raw).hexdigest()
    widths, fields = MASTER_LAYOUT[market]
    if len(widths) != len(fields):
        raise RegistryError(f"INTERNAL_LAYOUT_MISMATCH:{market}")
    tail_length = sum(widths)
    rows = []
    for index, line in enumerate(raw.splitlines(), start=1):
        if len(line) <= 21 + tail_length:
            raise RegistryError(f"MASTER_ROW_TOO_SHORT:{market}:{index}")
        head, tail = line[:-tail_length], line[-tail_length:]
        short_code = head[:9].decode("ascii").strip()
        standard_code = head[9:21].decode("ascii").strip()
        try:
            display_name = head[21:].decode(contract["kis_primary_source"]["encoding"]).strip()
            tail_text = tail.decode("ascii")
        except (UnicodeDecodeError, LookupError) as exc:
            raise RegistryError(f"MASTER_ROW_DECODE_FAILED:{market}:{index}") from exc
        if not SHORT_CODE_RE.fullmatch(short_code):
            raise RegistryError(f"SHORT_CODE_INVALID:{market}:{index}")
        if not STANDARD_CODE_RE.fullmatch(standard_code):
            raise RegistryError(f"STANDARD_CODE_INVALID:{market}:{index}")
        if not display_name:
            raise RegistryError(f"DISPLAY_NAME_EMPTY:{market}:{index}")
        offset = 0
        parsed = {}
        for field, width in zip(fields, widths):
            parsed[field] = tail_text[offset:offset + width].strip()
            offset += width
        parsed.update({
            "market": market,
            "short_code": short_code,
            "standard_code": standard_code,
            "display_name": display_name,
            "row_sha256": hashlib.sha256(line).hexdigest(),
        })
        rows.append(parsed)
    if not rows:
        raise RegistryError(f"MASTER_EMPTY:{market}")
    return rows, {
        "market": market,
        "source_url": source["source_url"],
        "retrieved_at_utc": _parse_time(source["retrieved_at_utc"], "INVALID").isoformat().replace("+00:00", "Z"),
        "http_last_modified": (
            _parse_time(source["http_last_modified"], "INVALID").isoformat().replace("+00:00", "Z")
            if source.get("http_last_modified") is not None else None
        ),
        "archive_sha256": archive_sha,
        "archive_byte_length": len(archive_bytes),
        "master_sha256": raw_sha,
        "master_byte_length": len(raw),
        "row_count": len(rows),
    }


def _validate_krx_packet(path: Path, contract: dict) -> tuple[dict, dict[str, str]]:
    packet = _read_json(path)
    digest = packet.get("payload_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != KRU.payload_sha256(unsigned):
        raise RegistryError("KRX_PACKET_SHA256_MISMATCH")
    if packet.get("schema_version") != contract["krx_primary_source"]["packet_schema_version"]:
        raise RegistryError("KRX_PACKET_SCHEMA_MISMATCH")
    if packet.get("membership_semantics") != contract["krx_primary_source"]["membership_semantics"]:
        raise RegistryError("KRX_PACKET_MEMBERSHIP_SEMANTICS_MISMATCH")
    krx_contract = KRU.load_contract()
    if packet.get("contract_version") != krx_contract["contract_version"]:
        raise RegistryError("KRX_PACKET_CONTRACT_MISMATCH")
    if packet.get("status") != "SOURCE_COVERAGE_UNIVERSE_VALIDATED":
        raise RegistryError("KRX_PACKET_STATUS_MISMATCH")
    if packet.get("policy_status") != krx_contract["policy_status"]:
        raise RegistryError("KRX_PACKET_POLICY_STATUS_MISMATCH")
    if packet.get("authority") != krx_contract["authority"]:
        raise RegistryError("KRX_PACKET_AUTHORITY_MISMATCH")
    try:
        master = GAM.validate_packet(packet["asset_master"])
    except (KeyError, GAM.AssetMasterError) as exc:
        raise RegistryError(f"KRX_ASSET_MASTER_INVALID:{exc}") from exc
    if master["as_of_date"] != packet.get("as_of_date"):
        raise RegistryError("KRX_PACKET_AS_OF_MISMATCH")
    if master["record_count"] != packet.get("total_count"):
        raise RegistryError("KRX_PACKET_COUNT_MISMATCH")
    mapping = {}
    for record in master["records"]:
        krx_short_code = record["primary_symbol"]
        memberships = [
            item["membership_id"] for item in record["active_memberships"]
            if item["membership_type"] == "UNIVERSE"
        ]
        if len(memberships) != 1 or memberships[0] not in contract["markets"]:
            raise RegistryError("KRX_RECORD_MARKET_INVALID")
        if record["source_identity"].get("source_id") != contract["krx_primary_source"]["required_source_id"]:
            raise RegistryError("KRX_RECORD_SOURCE_ID_MISMATCH")
        if not SHORT_CODE_RE.fullmatch(krx_short_code):
            raise RegistryError("KRX_SHORT_CODE_INVALID")
        if krx_short_code in mapping:
            raise RegistryError("KRX_SHORT_CODE_DUPLICATE")
        mapping[krx_short_code] = memberships[0]
    if len(mapping) != packet["total_count"]:
        raise RegistryError("KRX_MAPPING_COUNT_MISMATCH")
    for source in packet.get("source_snapshots", []):
        if source.get("source_sha256") is None:
            raise RegistryError("KRX_SOURCE_SHA_MISSING")
    return packet, mapping


def _load_previous(path: str | None) -> tuple[dict[str, str], str]:
    if path is None:
        return {}, "NOT_COMPUTABLE_NO_PRIOR_KIS_REGISTRY"
    value = _read_json(Path(path))
    digest = value.get("payload_sha256")
    unsigned = copy.deepcopy(value)
    unsigned.pop("payload_sha256", None)
    if value.get("schema_version") != OUTPUT_SCHEMA_VERSION or digest != payload_sha256(unsigned):
        raise RegistryError("PREVIOUS_REGISTRY_INVALID")
    mapping = {}
    for record in value.get("records", []):
        short = record.get("short_code")
        standard = record.get("standard_code")
        if short in mapping and mapping[short] != standard:
            raise RegistryError("PREVIOUS_SHORT_CODE_AMBIGUOUS")
        mapping[short] = standard
    return mapping, "CHECKED_AGAINST_PRIOR_REGISTRY"


def _product(row: dict, contract: dict) -> tuple[str, str, list[str]]:
    policy = contract["product_policy"]
    group = row["security_group"]
    preferred = row["preferred_code"]
    spac = row["spac"]
    etp = row["etp_code"]
    if group == policy["common_stock"]["security_group"]:
        if preferred in policy["preferred_stock"]["preferred_codes"]:
            return "PREFERRED_STOCK", "EXCLUDED", ["PRODUCT_PREFERRED_STOCK"]
        if preferred not in policy["common_stock"]["preferred_codes"]:
            return "UNKNOWN", "UNKNOWN", [f"KIS_PREFERRED_CODE_UNDOCUMENTED:{preferred or 'BLANK'}"]
        if spac == policy["spac"]["spac_flag"]:
            return "SPAC", "EXCLUDED", ["PRODUCT_SPAC"]
        if spac != policy["common_stock"]["spac_flag"]:
            return "UNKNOWN", "UNKNOWN", [f"KIS_SPAC_FLAG_UNDOCUMENTED:{spac or 'BLANK'}"]
        return "COMMON_STOCK", "CATEGORICAL_CANDIDATE", []
    if group == policy["etf"]["security_group"]:
        if etp not in policy["etf"]["documented_etp_codes"]:
            return "ETF", "UNKNOWN", [f"KIS_ETF_ETP_CODE_UNDOCUMENTED:{etp or 'BLANK'}"]
        return "ETF", "CATEGORICAL_CANDIDATE", []
    if etp in policy["etn"]["documented_etp_codes"]:
        return "ETN", "EXCLUDED", ["PRODUCT_ETN"]
    if group in policy["other_security_groups"]:
        return policy["other_security_groups"][group], "EXCLUDED", ["PRODUCT_OUTSIDE_COMMON_STOCK_ETF_SCOPE"]
    return "UNKNOWN", "UNKNOWN", [f"KIS_SECURITY_GROUP_UNDOCUMENTED:{group or 'BLANK'}"]


def _apply_status(row: dict, state: str, reasons: list[str]) -> tuple[str, list[str]]:
    known_yn = {
        "trading_halt": "KIS_TRADING_HALT",
        "liquidation_trading": "KIS_LIQUIDATION_TRADING",
        "managed_issue": "KIS_MANAGED_ISSUE",
        "warning_advance": "KIS_MARKET_WARNING_ADVANCE_NOTICE",
    }
    for field, reason in known_yn.items():
        value = row.get(field)
        if value == "Y":
            state = "EXCLUDED"
            reasons.append(reason)
        elif value != "N":
            state = "UNKNOWN" if state != "EXCLUDED" else state
            reasons.append(f"KIS_STATUS_FLAG_UNDOCUMENTED:{field}:{value or 'BLANK'}")
    warning = row.get("market_warning")
    if warning in {"01", "02", "03"}:
        state = "EXCLUDED"
        reasons.append(f"KIS_MARKET_WARNING:{warning}")
    elif warning != "00":
        state = "UNKNOWN" if state != "EXCLUDED" else state
        reasons.append(f"KIS_MARKET_WARNING_UNDOCUMENTED:{warning or 'BLANK'}")
    if row["market"] == "KOSDAQ":
        attention = row.get("investment_attention")
        if attention == "Y":
            state = "EXCLUDED"
            reasons.append("KIS_KOSDAQ_INVESTMENT_ATTENTION")
        elif attention != "N":
            state = "UNKNOWN" if state != "EXCLUDED" else state
            reasons.append(f"KIS_INVESTMENT_ATTENTION_UNDOCUMENTED:{attention or 'BLANK'}")
    if row.get("low_liquidity") == "Y":
        reasons.append("KIS_LOW_LIQUIDITY_FLAG_MEASURED_NOT_THRESHOLD")
    elif row.get("low_liquidity") != "N":
        state = "UNKNOWN" if state != "EXCLUDED" else state
        reasons.append(f"KIS_LOW_LIQUIDITY_FLAG_UNDOCUMENTED:{row.get('low_liquidity') or 'BLANK'}")
    return state, reasons


def build_registry(value: dict, contract: dict | None = None) -> tuple[dict, dict]:
    expected_contract = load_contract(CONTRACT_PATH)
    contract = expected_contract if contract is None else copy.deepcopy(contract)
    if contract != expected_contract:
        raise RegistryError("CONTRACT_CONTENT_MISMATCH")
    gate_compatibility = _gate_compatibility_projection(contract)
    value = _validate_input(value, contract)
    all_rows = []
    master_sources = []
    for market in contract["markets"]:
        rows, source = _read_master(value["masters"][market], market, contract)
        all_rows.extend(rows)
        master_sources.append(source)
    standards = [row["standard_code"] for row in all_rows]
    shorts = [row["short_code"] for row in all_rows]
    if len(standards) != len(set(standards)):
        raise RegistryError("CURRENT_STANDARD_CODE_DUPLICATE")
    if len(shorts) != len(set(shorts)):
        raise RegistryError("CURRENT_SHORT_CODE_DUPLICATE")

    krx_packet, krx_mapping = _validate_krx_packet(Path(value["krx_packet_path"]), contract)
    latest_date = value["latest_completed_session_date"]
    krx_stale = krx_packet["as_of_date"] != latest_date
    previous, history_status = _load_previous(value.get("previous_registry_path"))
    current_stock_scope = {
        row["short_code"] for row in all_rows
        if row["security_group"] not in {"EF", "EN", "PF", "BC", "SR", "SW"}
    }

    records = []
    for row in all_rows:
        product_type, screening_state, reasons = _product(row, contract)
        screening_state, reasons = _apply_status(row, screening_state, reasons)
        krx_market = krx_mapping.get(row["short_code"])
        if product_type in {"COMMON_STOCK", "PREFERRED_STOCK", "SPAC", "DEPOSITARY_RECEIPT", "FOREIGN_STOCK", "INFRASTRUCTURE_FUND", "REIT", "SECURITIES_INVESTMENT_COMPANY"}:
            if krx_market is None:
                screening_state = "UNKNOWN" if screening_state != "EXCLUDED" else screening_state
                reasons.append("STANDARD_CODE_MISSING_FROM_KRX_STOCK_SNAPSHOT")
                cross_source_status = "MISSING_FROM_KRX_STOCK_SNAPSHOT"
            elif krx_market != row["market"]:
                screening_state = "UNKNOWN" if screening_state != "EXCLUDED" else screening_state
                reasons.append("KIS_KRX_MARKET_MISMATCH")
                cross_source_status = "MARKET_MISMATCH"
            else:
                cross_source_status = "MATCHED"
        else:
            cross_source_status = "NOT_APPLICABLE_TO_KRX_STOCK_ENDPOINT"

        prior_standard = previous.get(row["short_code"])
        if prior_standard is not None and prior_standard != row["standard_code"]:
            screening_state = "UNKNOWN" if screening_state != "EXCLUDED" else screening_state
            reasons.append("SHORT_CODE_REUSED_WITH_DIFFERENT_STANDARD_CODE")
            code_reuse_status = "REUSED"
        elif history_status.startswith("NOT_COMPUTABLE"):
            code_reuse_status = history_status
        else:
            code_reuse_status = "UNCHANGED_OR_NEW"

        decision_blockers = []
        if screening_state == "CATEGORICAL_CANDIDATE":
            if krx_stale:
                decision_blockers.append("KRX_SNAPSHOT_NOT_LATEST_COMPLETED_SESSION")
            if history_status.startswith("NOT_COMPUTABLE"):
                decision_blockers.append(history_status)
            decision_blockers.extend([
                "KRX_DELISTING_SCHEDULE_EVIDENCE_MISSING",
                "TURNOVER_MEASUREMENT_MISSING",
                "ORDER_BOOK_DEPTH_MEASUREMENT_MISSING",
                "SPREAD_MEASUREMENT_MISSING",
                "SLIPPAGE_MEASUREMENT_MISSING",
                "LIQUIDITY_AND_EXECUTION_THRESHOLDS_UNRATIFIED",
            ])
        if screening_state == "EXCLUDED":
            decision_state = "EXCLUDED"
        else:
            decision_state = "UNKNOWN"
            if screening_state == "UNKNOWN":
                decision_blockers.append("CATEGORICAL_SCREENING_UNKNOWN")

        evidence_material = {
            "market": row["market"],
            "standard_code": row["standard_code"],
            "short_code": row["short_code"],
            "kis_row_sha256": row["row_sha256"],
            "krx_packet_sha256": krx_packet["payload_sha256"],
            "krx_cross_source_status": cross_source_status,
        }
        records.append({
            "security_id": f"KR:XKRX:{row['standard_code']}",
            "standard_code": row["standard_code"],
            "short_code": row["short_code"],
            "display_name": row["display_name"],
            "market": row["market"],
            "product_type": product_type,
            "screening_state": screening_state,
            "decision_eligibility": decision_state,
            "eligibility_reason_codes": sorted(set(reasons)),
            "decision_blocker_codes": sorted(set(decision_blockers)),
            "krx_cross_source_status": cross_source_status,
            "code_reuse_status": code_reuse_status,
            "evidence_sha256": payload_sha256(evidence_material),
            "as_of": value["captured_at_utc"],
        })

    records.sort(key=lambda item: (item["market"], item["standard_code"]))
    screening_counts = {key: 0 for key in ("CATEGORICAL_CANDIDATE", "EXCLUDED", "UNKNOWN")}
    decision_counts = {key: 0 for key in ("ELIGIBLE", "EXCLUDED", "UNKNOWN")}
    product_counts: dict[str, int] = {}
    market_counts = {market: 0 for market in contract["markets"]}
    for record in records:
        screening_counts[record["screening_state"]] += 1
        decision_counts[record["decision_eligibility"]] += 1
        product_counts[record["product_type"]] = product_counts.get(record["product_type"], 0) + 1
        market_counts[record["market"]] += 1

    registry = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "snapshot_captured_at_utc": value["captured_at_utc"],
        "effective_available_at_utc": value["captured_at_utc"],
        "latest_completed_session_date": latest_date,
        "latest_session_evidence": copy.deepcopy(value["latest_session_evidence"]),
        "krx_snapshot_as_of_date": krx_packet["as_of_date"],
        "krx_snapshot_freshness": "CURRENT" if not krx_stale else "STALE",
        "history_status": history_status,
        "source_lineage": {
            "kis_parser_commit": value["kis_parser_commit"],
            "kis_masters": sorted(master_sources, key=lambda item: item["market"]),
            "krx_packet_sha256": krx_packet["payload_sha256"],
            "krx_source_snapshots": copy.deepcopy(krx_packet["source_snapshots"]),
        },
        "summary": {
            "total_count": len(records),
            "market_counts": market_counts,
            "product_counts": dict(sorted(product_counts.items())),
            "screening_counts": screening_counts,
            "decision_counts": decision_counts,
            "krx_orphan_standard_code_count": len(set(krx_mapping) - current_stock_scope),
            "kis_stock_scope_missing_from_krx_count": len(current_stock_scope - set(krx_mapping)),
            "duplicate_standard_code_count": 0,
            "duplicate_short_code_count": 0,
            "code_reuse_count": sum(record["code_reuse_status"] == "REUSED" for record in records),
            "measurement_coverage": {
                "turnover": 0,
                "order_book_depth": 0,
                "spread": 0,
                "slippage": 0,
            },
        },
        "measurement_policy": copy.deepcopy(contract["measurement_policy"]),
        "krx_paper_gate_compatibility": copy.deepcopy(gate_compatibility),
        "distribution_boundary": copy.deepcopy(contract["distribution_boundary"]),
        "authority": copy.deepcopy(contract["authority"]),
        "records": records,
    }
    registry["payload_sha256"] = payload_sha256(registry)
    public = {
        "schema_version": PUBLIC_SUMMARY_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "snapshot_captured_at_utc": registry["snapshot_captured_at_utc"],
        "effective_available_at_utc": registry["effective_available_at_utc"],
        "latest_completed_session_date": latest_date,
        "latest_session_evidence": copy.deepcopy(registry["latest_session_evidence"]),
        "krx_snapshot_as_of_date": registry["krx_snapshot_as_of_date"],
        "krx_snapshot_freshness": registry["krx_snapshot_freshness"],
        "history_status": history_status,
        "source_lineage": copy.deepcopy(registry["source_lineage"]),
        "summary": copy.deepcopy(registry["summary"]),
        "measurement_policy": copy.deepcopy(registry["measurement_policy"]),
        "krx_paper_gate_compatibility": copy.deepcopy(
            registry["krx_paper_gate_compatibility"]
        ),
        "distribution_boundary": copy.deepcopy(registry["distribution_boundary"]),
        "authority": copy.deepcopy(registry["authority"]),
        "private_registry_payload_sha256": registry["payload_sha256"],
    }
    public["payload_sha256"] = payload_sha256(public)
    return registry, public


def write_json_atomic(path: Path, value: dict) -> None:
    GAM.write_json_atomic(path, value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--private-out", required=True, type=Path)
    parser.add_argument("--public-summary-out", required=True, type=Path)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)
    try:
        contract = load_contract(args.contract)
        registry, public = build_registry(_read_json(args.input), contract)
        write_json_atomic(args.private_out, registry)
        write_json_atomic(args.public_summary_out, public)
    except RegistryError as exc:
        print(f"KRX investable registry failed reason={exc}")
        return 1
    print(
        "KRX investable registry "
        f"screening={public['summary']['screening_counts']} "
        f"decision={public['summary']['decision_counts']} "
        f"freshness={public['krx_snapshot_freshness']} "
        f"sha256={public['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
