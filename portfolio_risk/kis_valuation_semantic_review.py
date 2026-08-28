#!/usr/bin/env python3
"""Fail-closed review of the KIS valuation-semantic proposal.

Review readiness requires the exact canonical proposal, independently parsed
official KIS git bytes, and two money-free private operational attestations
bound to their exact canonical private source records.  Because no numeric
freshness policy is ratified yet, the current contract deliberately remains
``REVIEW_INCOMPLETE`` even when every other layer reproduces exactly.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path
import re
import subprocess

from identity import canonical_identity

from identity.kis_official_evidence_resolver import (
    KisOfficialEvidenceResolutionError,
    _resolve_git_evidence,
)
from portfolio_risk.kis_valuation_semantic_proposal import (
    AUTHORITY_ALL_FALSE,
    KIS_OFFICIAL_COMMIT,
    KIS_OFFICIAL_REPO,
    KIS_VALUATION_EVIDENCE_MANIFEST,
    PROPOSAL_STATUS,
    SCHEMA_VERSION,
    TARGET_CONTRACT_VERSION,
    canonical_json,
    payload_sha256,
    valuation_semantic_mapping_proposal,
)


RELATIONSHIP_ATTESTATION_VERSION = "kis_paper_valuation_relationship_attestation/1"
BUY_CAPACITY_ATTESTATION_VERSION = "kis_paper_buy_capacity_attestation/1"

_PROPOSAL_FIELDS = set(valuation_semantic_mapping_proposal())
_FORBIDDEN_AUTHORITY_KEYS = {
    "approval_status", "approvalStatus", "ratified_at", "ratifiedAt",
    "broker_verified", "brokerVerified", "tradingAuthority", "orderAuthority",
}
_RELATIONSHIP_TRUE_FIELDS = {
    "positionValuationComplete",
    "accountValuationPresent",
    "positionMarketValueSumMatchesValuationSum",
    "positionUnrealizedPlSumMatchesUnrealizedPlSum",
    "securitiesValuationMatchesPositionMarketValueSum",
    "totalValuationEqualsCashDepositPlusSecuritiesValuation",
    "netAssetEqualsCashDepositPlusPositionMarketValue",
}
_BUY_CAPACITY_TRUE_FIELDS = {
    "orderableCashPresent",
    "noReceivableBuyAmountPresent",
    "noReceivableBuyQuantityPresent",
    "noReceivableBuyAmountNotAboveOrderableCash",
    "quantityCalculationPriceMatchesQuote",
}
_COMMON_ATTESTATION_FIELDS = {
    "contractVersion", "status", "snapshotSchemaVersion",
    "semanticMappingRatified", "orderSubmissionAttempted", "authority",
    "sourceRecordSha256", "capturedAt", "availableAt", "accountBindingHash",
    "attestationSha256",
}
_RELATIONSHIP_ATTESTATION_FIELDS = _COMMON_ATTESTATION_FIELDS | _RELATIONSHIP_TRUE_FIELDS
_BUY_CAPACITY_ATTESTATION_FIELDS = (
    _COMMON_ATTESTATION_FIELDS | _BUY_CAPACITY_TRUE_FIELDS
    | {"capacityKisFields", "instrumentBindingHash"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL_RE = re.compile(r"^[0-9]{6}$")
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MAX_PRIVATE_RECORD_BYTES = 20 * 1024 * 1024
_MAX_OFFICIAL_BLOB_BYTES = 2 * 1024 * 1024
_PROVIDER = "KIS_PAPER_ACCOUNT"
_ACCOUNT_SCOPE = "KOREA"
_VERIFICATION_STATUS = "BROKER_VERIFIED"
_SOURCE_NAME = "kis_paper_domestic_balance"
_FULL_RECORD_FIELDS = {
    "schemaVersion", "provider", "accountScope", "verificationStatus",
    "accountIdentityHash", "capturedAt", "availableAt", "balanceCash",
    "positions", "observedAccountValuation", "rawResponseSha256", "recordSha256",
}
_FULL_POSITION_FIELDS = {
    "sourceName", "sourceAssetId", "holdingQuantity", "orderableQuantity",
    "observedValuation",
}
_POSITION_VALUATION_FIELDS = {
    "marketValueKrw": "evlu_amt",
    "unrealizedPlKrw": "evlu_pfls_amt",
}
_ACCOUNT_VALUATION_FIELDS = {
    "cashDepositTotalKrw": "dnca_tot_amt",
    "securitiesValuationKrw": "scts_evlu_amt",
    "totalValuationKrw": "tot_evlu_amt",
    "netAssetKrw": "nass_amt",
    "valuationSumKrw": "evlu_amt_smtl_amt",
    "unrealizedPlSumKrw": "evlu_pfls_smtl_amt",
}
_BUY_RECORD_FIELDS = {
    "schemaVersion", "provider", "accountScope", "verificationStatus",
    "accountIdentityHash", "capturedAt", "availableAt", "instrument",
    "referenceQuote", "buyCapacity", "query", "quoteRawResponseSha256",
    "capacityRawResponseSha256", "recordSha256",
}
_BUY_CAPACITY_FIELDS = {
    "orderableCashKrw": "ord_psbl_cash",
    "noReceivableBuyAmountKrw": "nrcvb_buy_amt",
    "noReceivableBuyQuantity": "nrcvb_buy_qty",
    "quantityCalculationPriceKrw": "psbl_qty_calc_unpr",
}
_EXPECTED_OFFICIAL_MEANINGS = {
    "nass_amt": "순자산금액",
    "dnca_tot_amt": "예수금총금액",
    "evlu_amt": "평가금액",
    "evlu_pfls_amt": "평가손익금액",
    "nrcvb_buy_amt": "미수없는매수금액",
    "nrcvb_buy_qty": "미수없는매수수량",
    "psbl_qty_calc_unpr": "가능수량계산단가",
    "ord_psbl_cash": "주문가능현금",
    "tot_evlu_amt": "총평가금액",
}


class KisValuationSemanticReviewError(ValueError):
    pass


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.strptime(value, _TIMESTAMP_FORMAT).replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None


def _binding_hash(kind: str, **values: str) -> str:
    return payload_sha256({"bindingKind": kind, **values})


def _account_binding_hash(account_identity_hash: str) -> str:
    return _binding_hash(
        "KIS_PAPER_ACCOUNT_REVIEW_ONLY",
        accountIdentityHash=account_identity_hash,
    )


def _instrument_binding_hash(source_name: str, source_asset_id: str) -> str:
    return _binding_hash(
        "KIS_PAPER_INSTRUMENT_REVIEW_ONLY",
        sourceName=source_name,
        sourceAssetId=source_asset_id,
    )


def _canonical_private_record_bytes(record: object) -> bytes:
    return json.dumps(record, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _load_private_record(
    source: bytes | Path | None,
    label: str,
) -> tuple[dict | None, list[str]]:
    if source is None:
        return None, [f"PRIVATE_SOURCE_RECORD_REQUIRED:{label}"]
    if isinstance(source, Path):
        if not source.is_absolute():
            return None, [f"PRIVATE_SOURCE_PATH_NOT_ABSOLUTE:{label}"]
        if source.is_symlink() or not source.is_file():
            return None, [f"PRIVATE_SOURCE_PATH_INVALID:{label}"]
        try:
            size = source.stat().st_size
            if size > _MAX_PRIVATE_RECORD_BYTES:
                return None, [f"PRIVATE_SOURCE_BYTES_TOO_LARGE:{label}"]
            raw = source.read_bytes()
        except OSError:
            return None, [f"PRIVATE_SOURCE_READ_FAILED:{label}"]
    elif isinstance(source, bytes):
        raw = source
    else:
        return None, [f"PRIVATE_SOURCE_TYPE_INVALID:{label}"]
    if len(raw) > _MAX_PRIVATE_RECORD_BYTES:
        return None, [f"PRIVATE_SOURCE_BYTES_TOO_LARGE:{label}"]
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, [f"PRIVATE_SOURCE_JSON_INVALID:{label}"]
    if not isinstance(value, dict):
        return None, [f"PRIVATE_SOURCE_RECORD_INVALID:{label}"]
    reasons: list[str] = []
    if raw != _canonical_private_record_bytes(value):
        reasons.append(f"PRIVATE_SOURCE_CANONICAL_BYTES_INVALID:{label}")
    return value, reasons


def _record_common_reasons(
    record: dict,
    *,
    label: str,
    expected_fields: set[str],
    expected_schema: str,
    review_as_of: dt.datetime | None,
) -> tuple[list[str], dt.datetime | None, dt.datetime | None]:
    reasons: list[str] = []
    if set(record) != expected_fields:
        reasons.append(f"PRIVATE_SOURCE_FIELDS_INVALID:{label}")
    if record.get("schemaVersion") != expected_schema:
        reasons.append(f"PRIVATE_SOURCE_SCHEMA_INVALID:{label}")
    if (
        record.get("provider") != _PROVIDER
        or record.get("accountScope") != _ACCOUNT_SCOPE
        or record.get("verificationStatus") != _VERIFICATION_STATUS
    ):
        reasons.append(f"PRIVATE_SOURCE_PROVIDER_SCOPE_INVALID:{label}")
    if _SHA256_RE.fullmatch(str(record.get("accountIdentityHash", ""))) is None:
        reasons.append(f"PRIVATE_SOURCE_ACCOUNT_HASH_INVALID:{label}")
    captured = _parse_timestamp(record.get("capturedAt"))
    available = _parse_timestamp(record.get("availableAt"))
    if captured is None or available is None:
        reasons.append(f"PRIVATE_SOURCE_TIMESTAMP_INVALID:{label}")
    elif available < captured:
        reasons.append(f"PRIVATE_SOURCE_AVAILABLE_BEFORE_CAPTURED:{label}")
    elif review_as_of is not None and available > review_as_of:
        reasons.append(f"PRIVATE_SOURCE_AFTER_REVIEW_AS_OF:{label}")
    for field in (
        "rawResponseSha256", "quoteRawResponseSha256", "capacityRawResponseSha256",
    ):
        if field in record and _SHA256_RE.fullmatch(str(record.get(field, ""))) is None:
            reasons.append(f"PRIVATE_SOURCE_SHA256_INVALID:{label}:{field}")
    unsigned = {key: value for key, value in record.items() if key != "recordSha256"}
    if record.get("recordSha256") != payload_sha256(unsigned):
        reasons.append(f"PRIVATE_SOURCE_RECORD_HASH_MISMATCH:{label}")
    return reasons, captured, available


def _valuation_entry(value: object, expected_field: str) -> int | None:
    if (
        not isinstance(value, dict)
        or set(value) != {"kisField", "valueKrw"}
        or value.get("kisField") != expected_field
        or not _is_int(value.get("valueKrw"))
    ):
        return None
    return value["valueKrw"]


def _review_relationship_source(
    source: bytes | Path | None,
    attestation: object,
    review_as_of: dt.datetime | None,
) -> list[str]:
    label = RELATIONSHIP_ATTESTATION_VERSION
    record, reasons = _load_private_record(source, label)
    if record is None:
        return reasons
    common, _, available = _record_common_reasons(
        record,
        label=label,
        expected_fields=_FULL_RECORD_FIELDS,
        expected_schema="kis_paper_full_account_snapshot/3",
        review_as_of=review_as_of,
    )
    reasons.extend(common)
    positions = record.get("positions")
    complete_positions = isinstance(positions, list)
    market_values: list[int] = []
    unrealized_values: list[int] = []
    seen: set[str] = set()
    if isinstance(positions, list):
        for position in positions:
            if not isinstance(position, dict) or set(position) != _FULL_POSITION_FIELDS:
                complete_positions = False
                continue
            symbol = position.get("sourceAssetId")
            holding = position.get("holdingQuantity")
            orderable = position.get("orderableQuantity")
            if (
                position.get("sourceName") != _SOURCE_NAME
                or _SYMBOL_RE.fullmatch(str(symbol)) is None
                or symbol in seen
                or not _is_int(holding)
                or not _is_int(orderable)
                or holding < 0
                or orderable < 0
                or orderable > holding
            ):
                complete_positions = False
            seen.add(str(symbol))
            observed = position.get("observedValuation")
            if not isinstance(observed, dict) or set(observed) != set(
                _POSITION_VALUATION_FIELDS
            ):
                complete_positions = False
                continue
            market = _valuation_entry(observed.get("marketValueKrw"), "evlu_amt")
            unrealized = _valuation_entry(
                observed.get("unrealizedPlKrw"), "evlu_pfls_amt"
            )
            if market is None or unrealized is None:
                complete_positions = False
                continue
            market_values.append(market)
            unrealized_values.append(unrealized)
    else:
        reasons.append(f"PRIVATE_SOURCE_POSITIONS_INVALID:{label}")

    account = record.get("observedAccountValuation")
    account_values: dict[str, int] = {}
    account_present = isinstance(account, dict) and set(account) == set(
        _ACCOUNT_VALUATION_FIELDS
    )
    if account_present:
        for normalized, kis_field in _ACCOUNT_VALUATION_FIELDS.items():
            parsed = _valuation_entry(account.get(normalized), kis_field)
            if parsed is None:
                account_present = False
                break
            account_values[normalized] = parsed
    cash = record.get("balanceCash")
    if (
        not isinstance(cash, dict)
        or set(cash) != {"kisField", "valueKrw"}
        or cash.get("kisField") not in {"ord_psbl_cash", "dnca_tot_amt"}
        or not _is_int(cash.get("valueKrw"))
        or cash.get("valueKrw") < 0
    ):
        reasons.append(f"PRIVATE_SOURCE_BALANCE_CASH_INVALID:{label}")

    comparable = account_present and complete_positions
    market_sum = sum(market_values)
    unrealized_sum = sum(unrealized_values)
    derived = {
        "positionValuationComplete": complete_positions,
        "accountValuationPresent": account_present,
        "positionMarketValueSumMatchesValuationSum": bool(
            comparable and market_sum == account_values.get("valuationSumKrw")
        ),
        "positionUnrealizedPlSumMatchesUnrealizedPlSum": bool(
            comparable and unrealized_sum == account_values.get("unrealizedPlSumKrw")
        ),
        "securitiesValuationMatchesPositionMarketValueSum": bool(
            comparable and account_values.get("securitiesValuationKrw") == market_sum
        ),
        "totalValuationEqualsCashDepositPlusSecuritiesValuation": bool(
            account_present
            and account_values.get("totalValuationKrw")
            == account_values.get("cashDepositTotalKrw")
            + account_values.get("securitiesValuationKrw")
        ),
        "netAssetEqualsCashDepositPlusPositionMarketValue": bool(
            comparable
            and account_values.get("netAssetKrw")
            == account_values.get("cashDepositTotalKrw") + market_sum
        ),
    }
    if isinstance(attestation, dict):
        if attestation.get("sourceRecordSha256") != record.get("recordSha256"):
            reasons.append(f"PRIVATE_ATTESTATION_SOURCE_RECORD_MISMATCH:{label}")
        if attestation.get("capturedAt") != record.get("capturedAt"):
            reasons.append(f"PRIVATE_ATTESTATION_CAPTURED_AT_MISMATCH:{label}")
        if attestation.get("availableAt") != record.get("availableAt"):
            reasons.append(f"PRIVATE_ATTESTATION_AVAILABLE_AT_MISMATCH:{label}")
        expected_binding = _account_binding_hash(str(record.get("accountIdentityHash")))
        if attestation.get("accountBindingHash") != expected_binding:
            reasons.append(f"PRIVATE_ATTESTATION_ACCOUNT_BINDING_NOT_DERIVED:{label}")
        for field, expected in derived.items():
            if attestation.get(field) is not expected:
                reasons.append(f"PRIVATE_ATTESTATION_RELATIONSHIP_NOT_DERIVED:{label}:{field}")
        expected_status = (
            "COMPLETE_RELATIONSHIP_OBSERVATION"
            if comparable else "INCOMPLETE_RELATIONSHIP_OBSERVATION"
        )
        if attestation.get("status") != expected_status:
            reasons.append(f"PRIVATE_ATTESTATION_STATUS_NOT_DERIVED:{label}")
    if available is None:
        reasons.append(f"PRIVATE_SOURCE_AVAILABILITY_NOT_REPRODUCED:{label}")
    return reasons


def _capacity_entry(value: object, expected_field: str) -> int | None:
    if (
        not isinstance(value, dict)
        or set(value) != {"kisField", "value"}
        or value.get("kisField") != expected_field
        or not _is_int(value.get("value"))
        or value.get("value") < 0
    ):
        return None
    return value["value"]


def _review_buy_capacity_source(
    source: bytes | Path | None,
    attestation: object,
    review_as_of: dt.datetime | None,
) -> list[str]:
    label = BUY_CAPACITY_ATTESTATION_VERSION
    record, reasons = _load_private_record(source, label)
    if record is None:
        return reasons
    common, _, available = _record_common_reasons(
        record,
        label=label,
        expected_fields=_BUY_RECORD_FIELDS,
        expected_schema="kis_paper_buy_capacity_snapshot/1",
        review_as_of=review_as_of,
    )
    reasons.extend(common)
    instrument = record.get("instrument")
    instrument_valid = (
        isinstance(instrument, dict)
        and set(instrument) == {"sourceName", "sourceAssetId"}
        and instrument.get("sourceName") == _SOURCE_NAME
        and _SYMBOL_RE.fullmatch(str(instrument.get("sourceAssetId", ""))) is not None
    )
    if not instrument_valid:
        reasons.append(f"PRIVATE_SOURCE_INSTRUMENT_INVALID:{label}")
    quote = record.get("referenceQuote")
    quote_value = _capacity_entry(quote, "stck_prpr")
    if quote_value is None or quote_value <= 0:
        reasons.append(f"PRIVATE_SOURCE_REFERENCE_QUOTE_INVALID:{label}")
    capacity = record.get("buyCapacity")
    values: dict[str, int] = {}
    capacity_valid = isinstance(capacity, dict) and set(capacity) == set(
        _BUY_CAPACITY_FIELDS
    )
    if capacity_valid:
        for normalized, kis_field in _BUY_CAPACITY_FIELDS.items():
            parsed = _capacity_entry(capacity.get(normalized), kis_field)
            if parsed is None:
                capacity_valid = False
                break
            values[normalized] = parsed
    if not capacity_valid:
        reasons.append(f"PRIVATE_SOURCE_BUY_CAPACITY_INVALID:{label}")
    if record.get("query") != {
        "trId": "VTTC8908R", "orderDivision": "01", "cmaIncluded": False,
        "overseasIncluded": False,
    }:
        reasons.append(f"PRIVATE_SOURCE_QUERY_CONTRACT_INVALID:{label}")
    derived = {
        "orderableCashPresent": capacity_valid and values.get("orderableCashKrw", -1) >= 0,
        "noReceivableBuyAmountPresent": capacity_valid and values.get(
            "noReceivableBuyAmountKrw", -1
        ) >= 0,
        "noReceivableBuyQuantityPresent": capacity_valid and values.get(
            "noReceivableBuyQuantity", -1
        ) >= 0,
        "noReceivableBuyAmountNotAboveOrderableCash": bool(
            capacity_valid
            and values.get("noReceivableBuyAmountKrw", 0)
            <= values.get("orderableCashKrw", -1)
        ),
        "quantityCalculationPriceMatchesQuote": bool(
            capacity_valid
            and quote_value is not None
            and values.get("quantityCalculationPriceKrw") == quote_value
        ),
    }
    if isinstance(attestation, dict):
        if attestation.get("sourceRecordSha256") != record.get("recordSha256"):
            reasons.append(f"PRIVATE_ATTESTATION_SOURCE_RECORD_MISMATCH:{label}")
        if attestation.get("capturedAt") != record.get("capturedAt"):
            reasons.append(f"PRIVATE_ATTESTATION_CAPTURED_AT_MISMATCH:{label}")
        if attestation.get("availableAt") != record.get("availableAt"):
            reasons.append(f"PRIVATE_ATTESTATION_AVAILABLE_AT_MISMATCH:{label}")
        expected_account = _account_binding_hash(str(record.get("accountIdentityHash")))
        if attestation.get("accountBindingHash") != expected_account:
            reasons.append(f"PRIVATE_ATTESTATION_ACCOUNT_BINDING_NOT_DERIVED:{label}")
        if instrument_valid:
            expected_instrument = _instrument_binding_hash(
                str(instrument["sourceName"]), str(instrument["sourceAssetId"])
            )
            if attestation.get("instrumentBindingHash") != expected_instrument:
                reasons.append(f"PRIVATE_ATTESTATION_INSTRUMENT_BINDING_NOT_DERIVED:{label}")
        expected_fields = sorted(_BUY_CAPACITY_FIELDS.values())
        if attestation.get("capacityKisFields") != expected_fields:
            reasons.append(f"PRIVATE_ATTESTATION_KIS_FIELDS_NOT_DERIVED:{label}")
        for field, expected in derived.items():
            if attestation.get(field) is not expected:
                reasons.append(f"PRIVATE_ATTESTATION_RELATIONSHIP_NOT_DERIVED:{label}:{field}")
    if instrument_valid and available is not None:
        decision_date = available.astimezone(
            dt.timezone(dt.timedelta(hours=9))
        ).date().isoformat()
        try:
            identity = canonical_identity.resolve_instrument_identity(
                str(instrument["sourceName"]),
                str(instrument["sourceAssetId"]),
                _ACCOUNT_SCOPE,
                decision_date,
                canonical_identity.load_authority(),
            )
        except Exception as error:  # noqa: BLE001
            reasons.append(f"PRIVATE_SOURCE_IDENTITY_REPRODUCTION_FAILED:{type(error).__name__}")
        else:
            if identity.get("status") != canonical_identity.RESOLVED:
                reasons.append("PRIVATE_SOURCE_INSTRUMENT_ALIAS_NOT_RATIFIED_AT_AVAILABLE_AT")
            if any(identity.get("authority", {}).values()):
                reasons.append("PRIVATE_SOURCE_IDENTITY_AUTHORITY_ESCALATION")
    return reasons


def _walk_forbidden(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in _FORBIDDEN_AUTHORITY_KEYS:
                raise KisValuationSemanticReviewError(
                    f"EMBEDDED_AUTHORITY_FIELD_FORBIDDEN:{child_path}"
                )
            _walk_forbidden(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden(child, f"{path}[{index}]")
    elif isinstance(value, str) and value in {"RATIFIED", "BROKER_VERIFIED"}:
        raise KisValuationSemanticReviewError(
            f"EMBEDDED_AUTHORITY_VALUE_FORBIDDEN:{path}:{value}"
        )


def _review_proposal(proposal: object) -> list[str]:
    reasons: list[str] = []
    if not isinstance(proposal, dict) or set(proposal) != _PROPOSAL_FIELDS:
        return ["PROPOSAL_FIELDS_INVALID"]
    if proposal.get("schemaVersion") != SCHEMA_VERSION:
        reasons.append("SCHEMA_VERSION_INVALID")
    if proposal.get("proposalStatus") != PROPOSAL_STATUS:
        reasons.append("PROPOSAL_STATUS_NOT_UNRATIFIED")
    if proposal.get("targetContractVersion") != TARGET_CONTRACT_VERSION:
        reasons.append("TARGET_CONTRACT_VERSION_INVALID")
    authority = proposal.get("authority")
    if (
        authority != AUTHORITY_ALL_FALSE
        or not isinstance(authority, dict)
        or any(type(authority.get(key)) is not bool for key in AUTHORITY_ALL_FALSE)
    ):
        reasons.append("AUTHORITY_NOT_ALL_FALSE")
    if proposal.get("canonicalAuthorityConfigMutated") is not False:
        reasons.append("CANONICAL_AUTHORITY_CONFIG_MUTATION_CLAIMED")
    if proposal.get("existingPortfolioAccountFactV2Mutated") is not False:
        reasons.append("PORTFOLIO_ACCOUNT_FACT_V2_MUTATION_CLAIMED")
    expected_hash = payload_sha256({
        key: value for key, value in proposal.items() if key != "proposalSha256"
    })
    if proposal.get("proposalSha256") != expected_hash:
        reasons.append("PROPOSAL_HASH_MISMATCH")
    if canonical_json(proposal) != canonical_json(valuation_semantic_mapping_proposal()):
        reasons.append("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT")
    return reasons


def _read_git_blob(checkout: Path, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{KIS_OFFICIAL_COMMIT}:{path}"],
        cwd=checkout,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise KisValuationSemanticReviewError(f"OFFICIAL_GIT_BLOB_READ_FAILED:{path}")
    if len(completed.stdout) > _MAX_OFFICIAL_BLOB_BYTES:
        raise KisValuationSemanticReviewError(f"OFFICIAL_GIT_BLOB_TOO_LARGE:{path}")
    return completed.stdout


def _literal_assignment(tree: ast.AST, name: str) -> object:
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise KisValuationSemanticReviewError(f"OFFICIAL_ASSIGNMENT_MISSING:{name}")


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise KisValuationSemanticReviewError(f"OFFICIAL_FUNCTION_MISSING:{name}")


def _dict_name_bindings(function: ast.FunctionDef, variable: str) -> dict[str, str]:
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == variable for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        bindings: dict[str, str] = {}
        for key, value in zip(node.value.keys, node.value.values):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Name)
            ):
                bindings[key.value] = value.id
        return bindings
    raise KisValuationSemanticReviewError(
        f"OFFICIAL_FUNCTION_DICT_ASSIGNMENT_MISSING:{function.name}:{variable}"
    )


def _review_official_semantics(blobs: dict[str, bytes], proposal: dict) -> list[str]:
    reasons: list[str] = []
    balance_check_path = (
        "examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py"
    )
    balance_impl_path = (
        "examples_llm/domestic_stock/inquire_balance/inquire_balance.py"
    )
    capacity_check_path = (
        "examples_llm/domestic_stock/inquire_psbl_order/chk_inquire_psbl_order.py"
    )
    capacity_impl_path = (
        "examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py"
    )
    try:
        trees = {
            path: ast.parse(raw.decode("utf-8"), filename=path)
            for path, raw in blobs.items()
        }
        balance_mapping = _literal_assignment(trees[balance_check_path], "COLUMN_MAPPING")
        capacity_mapping = _literal_assignment(trees[capacity_check_path], "COLUMN_MAPPING")
    except (UnicodeDecodeError, SyntaxError, ValueError, KisValuationSemanticReviewError) as error:
        return [f"OFFICIAL_SEMANTIC_PARSE_FAILED:{error}"]
    if not isinstance(balance_mapping, dict) or not isinstance(capacity_mapping, dict):
        return ["OFFICIAL_COLUMN_MAPPING_INVALID"]

    proposal_mappings = {
        row.get("rawKisField"): row
        for row in proposal.get("mappings", [])
        if isinstance(row, dict)
    }
    balance_fields = {
        "nass_amt", "dnca_tot_amt", "evlu_amt", "evlu_pfls_amt", "tot_evlu_amt",
    }
    capacity_fields = {
        "nrcvb_buy_amt", "nrcvb_buy_qty", "psbl_qty_calc_unpr", "ord_psbl_cash",
    }
    for field in sorted(balance_fields | capacity_fields):
        mapping = balance_mapping if field in balance_fields else capacity_mapping
        if mapping.get(field) != _EXPECTED_OFFICIAL_MEANINGS[field]:
            reasons.append(f"OFFICIAL_FIELD_MEANING_MISMATCH:{field}")
        proposal_row = proposal_mappings.get(field)
        if proposal_row is not None and proposal_row.get(
            "officialKoreanMeaning"
        ) != mapping.get(field):
            reasons.append(f"PROPOSAL_OFFICIAL_MEANING_NOT_REPRODUCED:{field}")

    expected_endpoints = {
        "nass_amt": "inquire_balance.output2",
        "dnca_tot_amt": "inquire_balance.output2",
        "evlu_amt": "inquire_balance.output1",
        "evlu_pfls_amt": "inquire_balance.output1",
        "nrcvb_buy_amt": "inquire_psbl_order.output",
        "nrcvb_buy_qty": "inquire_psbl_order.output",
        "psbl_qty_calc_unpr": "inquire_psbl_order.output",
    }
    for field, endpoint in expected_endpoints.items():
        if proposal_mappings.get(field, {}).get("sourceEndpoint") != endpoint:
            reasons.append(f"PROPOSAL_SOURCE_ENDPOINT_INVALID:{field}")

    try:
        balance_tree = trees[balance_impl_path]
        capacity_tree = trees[capacity_impl_path]
        if _literal_assignment(balance_tree, "API_URL") != (
            "/uapi/domestic-stock/v1/trading/inquire-balance"
        ):
            reasons.append("OFFICIAL_BALANCE_API_URL_INVALID")
        if _literal_assignment(capacity_tree, "API_URL") != (
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        ):
            reasons.append("OFFICIAL_BUY_CAPACITY_API_URL_INVALID")
        balance_function = _function(balance_tree, "inquire_balance")
        capacity_function = _function(capacity_tree, "inquire_psbl_order")
        capacity_args = {argument.arg for argument in capacity_function.args.args}
        if not {"pdno", "ord_unpr"}.issubset(capacity_args):
            reasons.append("OFFICIAL_BUY_CAPACITY_INSTRUMENT_QUERY_ARGS_MISSING")
        query_bindings = _dict_name_bindings(capacity_function, "params")
        if query_bindings.get("PDNO") != "pdno" or query_bindings.get(
            "ORD_UNPR"
        ) != "ord_unpr":
            reasons.append("OFFICIAL_BUY_CAPACITY_INSTRUMENT_QUERY_BINDING_INVALID")
        balance_attrs = {
            node.attr for node in ast.walk(balance_function) if isinstance(node, ast.Attribute)
        }
        capacity_attrs = {
            node.attr for node in ast.walk(capacity_function) if isinstance(node, ast.Attribute)
        }
        if not {"output1", "output2"}.issubset(balance_attrs):
            reasons.append("OFFICIAL_BALANCE_OUTPUT_SPLIT_NOT_REPRODUCED")
        if "output" not in capacity_attrs:
            reasons.append("OFFICIAL_BUY_CAPACITY_OUTPUT_NOT_REPRODUCED")
        balance_strings = {
            node.value for node in ast.walk(balance_function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        capacity_strings = {
            node.value for node in ast.walk(capacity_function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if "VTTC8434R" not in balance_strings:
            reasons.append("OFFICIAL_BALANCE_PAPER_TR_ID_NOT_REPRODUCED")
        if "VTTC8908R" not in capacity_strings:
            reasons.append("OFFICIAL_BUY_CAPACITY_PAPER_TR_ID_NOT_REPRODUCED")
    except (ValueError, KisValuationSemanticReviewError) as error:
        reasons.append(f"OFFICIAL_IMPLEMENTATION_SEMANTIC_PARSE_FAILED:{error}")
    return reasons


def _review_official_bytes(checkout: Path | None, proposal: dict) -> list[str]:
    if checkout is None:
        return ["EXTERNAL_SOURCE_BYTES_REPRODUCTION_REQUIRED"]
    try:
        resolution = _resolve_git_evidence(
            Path(checkout),
            repo=KIS_OFFICIAL_REPO,
            commit_sha=KIS_OFFICIAL_COMMIT,
            manifest=KIS_VALUATION_EVIDENCE_MANIFEST,
        )
    except KisOfficialEvidenceResolutionError as error:
        return [f"EXTERNAL_SOURCE_REPRODUCTION_FAILED:{error}"]
    if (
        resolution.get("resolutionStatus") != "EXACT_GIT_BYTES_REPRODUCED"
        or resolution.get("repo") != KIS_OFFICIAL_REPO
        or resolution.get("commitSha") != KIS_OFFICIAL_COMMIT
    ):
        return ["EXTERNAL_SOURCE_REPRODUCTION_RESULT_INVALID"]
    try:
        blobs = {
            path: _read_git_blob(Path(checkout), path)
            for path in KIS_VALUATION_EVIDENCE_MANIFEST
        }
    except KisValuationSemanticReviewError as error:
        return [str(error)]
    return _review_official_semantics(blobs, proposal)


def _review_common_attestation(attestation: object, version: str) -> list[str]:
    if attestation is None:
        return [f"PRIVATE_ATTESTATION_REQUIRED:{version}"]
    if not isinstance(attestation, dict):
        return [f"PRIVATE_ATTESTATION_INVALID:{version}"]
    reasons: list[str] = []
    if attestation.get("contractVersion") != version:
        reasons.append(f"PRIVATE_ATTESTATION_VERSION_INVALID:{version}")
    if attestation.get("semanticMappingRatified") is not False:
        reasons.append(f"PRIVATE_ATTESTATION_SELF_RATIFICATION_REJECTED:{version}")
    if attestation.get("orderSubmissionAttempted") is not False:
        reasons.append(f"PRIVATE_ATTESTATION_ORDER_ATTEMPT_REJECTED:{version}")
    authority = attestation.get("authority")
    if (
        authority != AUTHORITY_ALL_FALSE
        or not isinstance(authority, dict)
        or any(type(authority.get(key)) is not bool for key in AUTHORITY_ALL_FALSE)
    ):
        reasons.append(f"PRIVATE_ATTESTATION_AUTHORITY_INVALID:{version}")
    if any(key in attestation for key in (
        "accountIdentityHash", "evidencePath", "moneyValues", "positions", "sourceAssetId",
    )):
        reasons.append(f"PRIVATE_ATTESTATION_SENSITIVE_FIELD_FORBIDDEN:{version}")
    for field in ("sourceRecordSha256", "accountBindingHash"):
        if _SHA256_RE.fullmatch(str(attestation.get(field, ""))) is None:
            reasons.append(f"PRIVATE_ATTESTATION_SHA256_INVALID:{version}:{field}")
    try:
        captured = dt.datetime.strptime(
            str(attestation.get("capturedAt")), _TIMESTAMP_FORMAT
        ).replace(tzinfo=dt.timezone.utc)
        available = dt.datetime.strptime(
            str(attestation.get("availableAt")), _TIMESTAMP_FORMAT
        ).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        reasons.append(f"PRIVATE_ATTESTATION_TIMESTAMP_INVALID:{version}")
    else:
        if available < captured:
            reasons.append(f"PRIVATE_ATTESTATION_AVAILABLE_BEFORE_CAPTURED:{version}")
    claimed_hash = attestation.get("attestationSha256")
    computed_hash = payload_sha256({
        key: value for key, value in attestation.items() if key != "attestationSha256"
    })
    if claimed_hash != computed_hash:
        reasons.append(f"PRIVATE_ATTESTATION_HASH_MISMATCH:{version}")
    return reasons


def _review_relationship_attestation(attestation: object) -> list[str]:
    reasons = _review_common_attestation(attestation, RELATIONSHIP_ATTESTATION_VERSION)
    if not isinstance(attestation, dict):
        return reasons
    if set(attestation) != _RELATIONSHIP_ATTESTATION_FIELDS:
        reasons.append("VALUATION_RELATIONSHIP_ATTESTATION_FIELDS_INVALID")
    if attestation.get("status") != "COMPLETE_RELATIONSHIP_OBSERVATION":
        reasons.append("VALUATION_RELATIONSHIP_OBSERVATION_INCOMPLETE")
    if attestation.get("snapshotSchemaVersion") != "kis_paper_full_account_snapshot/3":
        reasons.append("VALUATION_SNAPSHOT_SCHEMA_INVALID")
    for field in sorted(_RELATIONSHIP_TRUE_FIELDS):
        if attestation.get(field) is not True:
            reasons.append(f"VALUATION_RELATIONSHIP_NOT_PROVEN:{field}")
    return reasons


def _review_buy_capacity_attestation(attestation: object) -> list[str]:
    reasons = _review_common_attestation(attestation, BUY_CAPACITY_ATTESTATION_VERSION)
    if not isinstance(attestation, dict):
        return reasons
    if set(attestation) != _BUY_CAPACITY_ATTESTATION_FIELDS:
        reasons.append("BUY_CAPACITY_ATTESTATION_FIELDS_INVALID")
    if attestation.get("status") != "CAPTURED_BUY_CAPACITY_COMPLETE":
        reasons.append("BUY_CAPACITY_OBSERVATION_INCOMPLETE")
    if attestation.get("snapshotSchemaVersion") != "kis_paper_buy_capacity_snapshot/1":
        reasons.append("BUY_CAPACITY_SNAPSHOT_SCHEMA_INVALID")
    expected_fields = {
        "nrcvb_buy_amt", "nrcvb_buy_qty", "ord_psbl_cash", "psbl_qty_calc_unpr",
    }
    if set(attestation.get("capacityKisFields", [])) != expected_fields:
        reasons.append("BUY_CAPACITY_KIS_FIELDS_INCOMPLETE")
    if _SHA256_RE.fullmatch(str(attestation.get("instrumentBindingHash", ""))) is None:
        reasons.append("BUY_CAPACITY_INSTRUMENT_BINDING_HASH_INVALID")
    for field in sorted(_BUY_CAPACITY_TRUE_FIELDS):
        if attestation.get(field) is not True:
            reasons.append(f"BUY_CAPACITY_RELATIONSHIP_NOT_PROVEN:{field}")
    return reasons


def review_valuation_semantic_mapping_proposal(
    proposal: dict,
    *,
    official_checkout: Path | None = None,
    relationship_attestation: dict | None = None,
    buy_capacity_attestation: dict | None = None,
    relationship_source_record: bytes | Path | None = None,
    buy_capacity_source_record: bytes | Path | None = None,
) -> dict:
    """Return mechanical review readiness without creating authority."""
    _walk_forbidden(proposal)
    reasons = _review_proposal(proposal)
    reasons.extend(_review_official_bytes(official_checkout, proposal))
    reasons.extend(_review_relationship_attestation(relationship_attestation))
    reasons.extend(_review_buy_capacity_attestation(buy_capacity_attestation))
    review_as_of = _parse_timestamp(proposal.get("reviewAsOf"))
    if review_as_of is None:
        reasons.append("PROPOSAL_REVIEW_AS_OF_INVALID")
    reasons.extend(_review_relationship_source(
        relationship_source_record, relationship_attestation, review_as_of
    ))
    reasons.extend(_review_buy_capacity_source(
        buy_capacity_source_record, buy_capacity_attestation, review_as_of
    ))
    # The proposal requires both private source records to be fresh, but no
    # CIO-ratified maximum source age or maximum pair gap exists yet.  PIT
    # upper bounds alone do not prove freshness.  Never invent numeric policy
    # here and never let otherwise self-consistent stale records become READY.
    reasons.append("PRIVATE_SOURCE_FRESHNESS_POLICY_UNRATIFIED")
    if isinstance(relationship_attestation, dict) and isinstance(buy_capacity_attestation, dict):
        if relationship_attestation.get("accountBindingHash") != buy_capacity_attestation.get(
            "accountBindingHash"
        ):
            reasons.append("PRIVATE_ATTESTATION_ACCOUNT_BINDING_MISMATCH")
    unique = sorted(set(reasons))
    return {
        "reviewStatus": "REVIEW_INCOMPLETE" if unique else "REVIEW_READY_FOR_CIO",
        "reasons": unique,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
