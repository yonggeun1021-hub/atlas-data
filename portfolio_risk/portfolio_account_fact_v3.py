#!/usr/bin/env python3
"""KIS PAPER ``portfolio_account_fact/3`` readiness contract.

This module is intentionally one gate short of an account-fact producer.
It validates a closed normalized valuation bundle supplied by the private
trust boundary and independently resolves provider, instrument-identity,
valuation-semantic, and freshness authority from the current git-backed
public registries.  It does not claim to reproduce private source bytes: the
private caller must validate those records and receipts before constructing
the bundle.  Even when every public prerequisite clears, ``accountFact``
remains ``None`` because no account-fact consumption authority exists yet.

The v3 vocabulary deliberately has no account-wide ``buyingPower`` field.
KIS ``inquire-psbl-order`` is instrument/query specific, so its approved
values remain under ``instrumentBuyCapacity`` and retain the exact 071050
identity binding.  This module performs no network, broker, persistence,
order, sizing, Stage, Buy, Action, Production, Trading, or REAL operation.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

from identity import canonical_identity as canonical_identity
from portfolio_risk import kis_valuation_authority as valuation_authority


SOURCE_BUNDLE_VERSION = "kis_portfolio_account_fact_v3_source_bundle/1"
READINESS_VERSION = "kis_portfolio_account_fact_v3_readiness/1"
TARGET_CONTRACT_VERSION = "portfolio_account_fact/3"

NOT_COMPUTABLE_POSITION_IDENTITY_INCOMPLETE = (
    "NOT_COMPUTABLE_POSITION_IDENTITY_INCOMPLETE"
)
NOT_COMPUTABLE_VALUATION_SEMANTIC_AUTHORITY = (
    "NOT_COMPUTABLE_VALUATION_SEMANTIC_AUTHORITY"
)
NOT_COMPUTABLE_FRESHNESS_AUTHORITY = "NOT_COMPUTABLE_FRESHNESS_AUTHORITY"
NOT_COMPUTABLE_SOURCE_STALE_OR_FUTURE = (
    "NOT_COMPUTABLE_SOURCE_STALE_OR_FUTURE"
)
NOT_COMPUTABLE_SOURCE_PAIR_GAP_EXCEEDED = (
    "NOT_COMPUTABLE_SOURCE_PAIR_GAP_EXCEEDED"
)
NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED = (
    "NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED"
)

PROVIDER_TUPLE = dict(valuation_authority.PROVIDER_TUPLE)
EXACT_CAPACITY_SOURCE_ASSET_ID = "071050"
EXACT_CAPACITY_CANONICAL_INSTRUMENT_ID = "KRX:071050:COMMON"
EXACT_CAPACITY_LISTING_ID = "XKRX:071050"

CONSUMPTION_AUTHORITY_ALL_FALSE = {
    "accountFactAuthorized": False,
    "riskInputAuthorized": False,
    "stageAuthorized": False,
    "buyAuthorized": False,
    "actionAuthorized": False,
    "orderAuthorized": False,
    "productionAuthorized": False,
    "tradingAuthorized": False,
    "realCapitalAuthorized": False,
}

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ASSET_ID_RE = re.compile(r"^[0-9]{6}$")
_ENTRY_FIELDS = {"rawKisField", "value"}
_BUNDLE_FIELDS = {
    "contractVersion", "providerTuple", "balanceObservation",
    "instrumentBuyCapacityObservation", "sourceBindings", "bundleSha256",
}
_BALANCE_FIELDS = {
    "sourceContractVersion", "sourceRecordSha256", "accountIdentityHash",
    "capturedAt", "availableAt", "account", "positions",
}
_CAPACITY_FIELDS = {
    "sourceContractVersion", "sourceRecordSha256", "accountIdentityHash",
    "capturedAt", "availableAt", "instrument", "capacity",
}
_SOURCE_BINDING_FIELDS = {
    "fullAccountRecordSha256", "buyCapacityRecordSha256",
    "pairBindingRecordSha256", "lockedRuntimeReceiptSha256",
}
_ACCOUNT_FIELDS = {
    "netAssetKrw", "cashDepositTotalKrw", "securitiesValuationKrw",
    "totalValuationKrw", "valuationSumKrw", "unrealizedPlSumKrw",
}
_ACCOUNT_KIS_FIELDS = {
    "netAssetKrw": "nass_amt",
    "cashDepositTotalKrw": "dnca_tot_amt",
    "securitiesValuationKrw": "scts_evlu_amt",
    "totalValuationKrw": "tot_evlu_amt",
    "valuationSumKrw": "evlu_amt_smtl_amt",
    "unrealizedPlSumKrw": "evlu_pfls_smtl_amt",
}
_POSITION_FIELDS = {
    "sourceName", "sourceAssetId", "holdingQuantity", "orderableQuantity",
    "marketValueKrw", "unrealizedPlKrw",
}
_POSITION_KIS_FIELDS = {
    "marketValueKrw": "evlu_amt",
    "unrealizedPlKrw": "evlu_pfls_amt",
}
_INSTRUMENT_FIELDS = {"sourceName", "sourceAssetId"}
_INSTRUMENT_CAPACITY_FIELDS = {
    "noReceivableBuyAmountKrw", "noReceivableBuyQuantity",
    "quantityCalculationPriceKrw",
}
_INSTRUMENT_CAPACITY_KIS_FIELDS = {
    "noReceivableBuyAmountKrw": "nrcvb_buy_amt",
    "noReceivableBuyQuantity": "nrcvb_buy_qty",
    "quantityCalculationPriceKrw": "psbl_qty_calc_unpr",
}


class PortfolioAccountFactV3Error(ValueError):
    pass


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise PortfolioAccountFactV3Error(code)
    try:
        return dt.datetime.strptime(value, _TIMESTAMP_FORMAT).replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        raise PortfolioAccountFactV3Error(code) from None


def _strict_int(value: object, code: str, *, nonnegative: bool = False) -> int:
    if type(value) is not int or (nonnegative and value < 0):
        raise PortfolioAccountFactV3Error(code)
    return value


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PortfolioAccountFactV3Error(code)
    return value


def _entry(
    value: object, *, expected_field: str, code: str, nonnegative: bool,
) -> int:
    if not isinstance(value, dict) or set(value) != _ENTRY_FIELDS:
        raise PortfolioAccountFactV3Error(f"{code}_FIELDS_INVALID")
    if value.get("rawKisField") != expected_field:
        raise PortfolioAccountFactV3Error(f"{code}_KIS_FIELD_MISMATCH")
    return _strict_int(
        value.get("value"), f"{code}_VALUE_INVALID", nonnegative=nonnegative
    )


def _validate_provider_tuple(value: object) -> None:
    if not isinstance(value, dict) or value != PROVIDER_TUPLE:
        raise PortfolioAccountFactV3Error("SOURCE_BUNDLE_PROVIDER_TUPLE_INVALID")


def _validate_balance(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _BALANCE_FIELDS:
        raise PortfolioAccountFactV3Error("BALANCE_OBSERVATION_FIELDS_INVALID")
    if value.get("sourceContractVersion") != "kis_paper_full_account_snapshot/3":
        raise PortfolioAccountFactV3Error("BALANCE_SOURCE_CONTRACT_INVALID")
    _sha256(value.get("sourceRecordSha256"), "BALANCE_SOURCE_RECORD_SHA_INVALID")
    _sha256(value.get("accountIdentityHash"), "BALANCE_ACCOUNT_BINDING_INVALID")
    captured = _parse_utc(value.get("capturedAt"), "BALANCE_CAPTURED_AT_INVALID")
    available = _parse_utc(value.get("availableAt"), "BALANCE_AVAILABLE_AT_INVALID")
    if available < captured:
        raise PortfolioAccountFactV3Error("BALANCE_AVAILABLE_BEFORE_CAPTURED")

    account = value.get("account")
    if not isinstance(account, dict) or set(account) != _ACCOUNT_FIELDS:
        raise PortfolioAccountFactV3Error("BALANCE_ACCOUNT_FIELDS_INVALID")
    for name, raw_field in _ACCOUNT_KIS_FIELDS.items():
        _entry(
            account[name], expected_field=raw_field,
            code=f"BALANCE_ACCOUNT_{name.upper()}",
            nonnegative=name != "unrealizedPlSumKrw",
        )

    positions = value.get("positions")
    if not isinstance(positions, list):
        raise PortfolioAccountFactV3Error("BALANCE_POSITIONS_INVALID")
    seen: set[tuple[str, str]] = set()
    for position in positions:
        if not isinstance(position, dict) or set(position) != _POSITION_FIELDS:
            raise PortfolioAccountFactV3Error("BALANCE_POSITION_FIELDS_INVALID")
        source_name = position.get("sourceName")
        source_id = position.get("sourceAssetId")
        if source_name != PROVIDER_TUPLE["positionSourceName"]:
            raise PortfolioAccountFactV3Error("BALANCE_POSITION_SOURCE_NAME_INVALID")
        if not isinstance(source_id, str) or _SOURCE_ASSET_ID_RE.fullmatch(source_id) is None:
            raise PortfolioAccountFactV3Error("BALANCE_POSITION_SOURCE_ASSET_ID_INVALID")
        pair = (source_name, source_id)
        if pair in seen:
            raise PortfolioAccountFactV3Error("BALANCE_POSITION_DUPLICATE")
        seen.add(pair)
        holding = _strict_int(
            position.get("holdingQuantity"),
            "BALANCE_HOLDING_QUANTITY_INVALID", nonnegative=True,
        )
        orderable = _strict_int(
            position.get("orderableQuantity"),
            "BALANCE_ORDERABLE_QUANTITY_INVALID", nonnegative=True,
        )
        if orderable > holding:
            raise PortfolioAccountFactV3Error("BALANCE_ORDERABLE_ABOVE_HOLDING")
        for name, raw_field in _POSITION_KIS_FIELDS.items():
            _entry(
                position[name], expected_field=raw_field,
                code=f"BALANCE_POSITION_{name.upper()}",
                nonnegative=name == "marketValueKrw",
            )


def _validate_capacity(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _CAPACITY_FIELDS:
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_OBSERVATION_FIELDS_INVALID")
    if value.get("sourceContractVersion") != "kis_paper_buy_capacity_snapshot/1":
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_SOURCE_CONTRACT_INVALID")
    _sha256(value.get("sourceRecordSha256"), "BUY_CAPACITY_SOURCE_RECORD_SHA_INVALID")
    _sha256(value.get("accountIdentityHash"), "BUY_CAPACITY_ACCOUNT_BINDING_INVALID")
    captured = _parse_utc(value.get("capturedAt"), "BUY_CAPACITY_CAPTURED_AT_INVALID")
    available = _parse_utc(value.get("availableAt"), "BUY_CAPACITY_AVAILABLE_AT_INVALID")
    if available < captured:
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_AVAILABLE_BEFORE_CAPTURED")
    instrument = value.get("instrument")
    if not isinstance(instrument, dict) or set(instrument) != _INSTRUMENT_FIELDS:
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_INSTRUMENT_FIELDS_INVALID")
    if instrument != {
        "sourceName": PROVIDER_TUPLE["positionSourceName"],
        "sourceAssetId": EXACT_CAPACITY_SOURCE_ASSET_ID,
    }:
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_INSTRUMENT_NOT_EXACT_071050")
    capacity = value.get("capacity")
    if not isinstance(capacity, dict) or set(capacity) != _INSTRUMENT_CAPACITY_FIELDS:
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_FIELDS_INVALID")
    for name, raw_field in _INSTRUMENT_CAPACITY_KIS_FIELDS.items():
        parsed = _entry(
            capacity[name], expected_field=raw_field,
            code=f"BUY_CAPACITY_{name.upper()}", nonnegative=True,
        )
        if name == "quantityCalculationPriceKrw" and parsed <= 0:
            raise PortfolioAccountFactV3Error(
                "BUY_CAPACITY_QUANTITY_CALCULATION_PRICE_NOT_POSITIVE"
            )


def _validate_relationships(bundle: dict) -> None:
    balance = bundle["balanceObservation"]
    capacity = bundle["instrumentBuyCapacityObservation"]
    if balance["accountIdentityHash"] != capacity["accountIdentityHash"]:
        raise PortfolioAccountFactV3Error("SOURCE_ACCOUNT_BINDING_MISMATCH")
    account = balance["account"]
    positions = balance["positions"]
    market_sum = sum(position["marketValueKrw"]["value"] for position in positions)
    pl_sum = sum(position["unrealizedPlKrw"]["value"] for position in positions)
    cash = account["cashDepositTotalKrw"]["value"]
    if market_sum != account["valuationSumKrw"]["value"]:
        raise PortfolioAccountFactV3Error("POSITION_MARKET_VALUE_SUM_MISMATCH")
    if market_sum != account["securitiesValuationKrw"]["value"]:
        raise PortfolioAccountFactV3Error("SECURITIES_VALUATION_SUM_MISMATCH")
    if pl_sum != account["unrealizedPlSumKrw"]["value"]:
        raise PortfolioAccountFactV3Error("POSITION_UNREALIZED_PL_SUM_MISMATCH")
    if cash + market_sum != account["totalValuationKrw"]["value"]:
        raise PortfolioAccountFactV3Error("TOTAL_VALUATION_RELATIONSHIP_MISMATCH")
    if cash + market_sum != account["netAssetKrw"]["value"]:
        raise PortfolioAccountFactV3Error("NET_ASSET_RELATIONSHIP_MISMATCH")


def validate_source_bundle(bundle: object) -> dict:
    if not isinstance(bundle, dict) or set(bundle) != _BUNDLE_FIELDS:
        raise PortfolioAccountFactV3Error("SOURCE_BUNDLE_FIELDS_INVALID")
    if bundle.get("contractVersion") != SOURCE_BUNDLE_VERSION:
        raise PortfolioAccountFactV3Error("SOURCE_BUNDLE_CONTRACT_INVALID")
    _validate_provider_tuple(bundle.get("providerTuple"))
    _validate_balance(bundle.get("balanceObservation"))
    _validate_capacity(bundle.get("instrumentBuyCapacityObservation"))
    balance = bundle["balanceObservation"]
    capacity = bundle["instrumentBuyCapacityObservation"]
    if not (
        _parse_utc(balance["capturedAt"], "BALANCE_CAPTURED_AT_INVALID")
        <= _parse_utc(balance["availableAt"], "BALANCE_AVAILABLE_AT_INVALID")
        <= _parse_utc(capacity["capturedAt"], "BUY_CAPACITY_CAPTURED_AT_INVALID")
        <= _parse_utc(capacity["availableAt"], "BUY_CAPACITY_AVAILABLE_AT_INVALID")
    ):
        raise PortfolioAccountFactV3Error("SOURCE_CAPTURE_SEQUENCE_INVALID")
    bindings = bundle.get("sourceBindings")
    if not isinstance(bindings, dict) or set(bindings) != _SOURCE_BINDING_FIELDS:
        raise PortfolioAccountFactV3Error("SOURCE_BINDINGS_FIELDS_INVALID")
    for field in _SOURCE_BINDING_FIELDS:
        _sha256(bindings.get(field), f"SOURCE_BINDING_SHA_INVALID:{field}")
    if bindings["fullAccountRecordSha256"] != balance["sourceRecordSha256"]:
        raise PortfolioAccountFactV3Error("FULL_ACCOUNT_SOURCE_BINDING_MISMATCH")
    if bindings["buyCapacityRecordSha256"] != capacity["sourceRecordSha256"]:
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_SOURCE_BINDING_MISMATCH")
    _validate_relationships(bundle)
    unsigned = {key: value for key, value in bundle.items() if key != "bundleSha256"}
    if bundle.get("bundleSha256") != payload_sha256(unsigned):
        raise PortfolioAccountFactV3Error("SOURCE_BUNDLE_SHA_MISMATCH")
    return dict(bundle)


def _blocked(status: str, bundle_sha: str, **diagnostics: object) -> dict:
    result = {
        "contractVersion": READINESS_VERSION,
        "targetContractVersion": TARGET_CONTRACT_VERSION,
        "status": status,
        "accountFact": None,
        "sourceBundleSha256": bundle_sha,
        "privateSourceValidationBoundary": (
            "STRUCTURAL_HASH_BINDINGS_ONLY_PRIVATE_SOURCE_BYTES_MUST_BE_VALIDATED_BY_CALLER"
        ),
        "authority": dict(CONSUMPTION_AUTHORITY_ALL_FALSE),
    }
    result.update(diagnostics)
    return result


def evaluate_kis_portfolio_account_fact_v3_readiness(
    *, bundle: object, decision_at: str, provider_authority: dict,
    security_identity: dict, valuation_authority_document: dict,
    trusted_commit: str | None = None,
) -> dict:
    """Evaluate every prerequisite while keeping account-fact authority shut.

    ``trusted_commit`` is an externally pinned immutable commit for all three
    git-backed authority documents.  It is never read from the source bundle.
    """
    bundle = validate_source_bundle(bundle)
    decision = _parse_utc(decision_at, "DECISION_AT_INVALID")
    bundle_sha = bundle["bundleSha256"]

    provider = canonical_identity.resolve_provider_authority(
        provider=PROVIDER_TUPLE["provider"],
        account_scope=PROVIDER_TUPLE["accountScope"],
        currency=PROVIDER_TUPLE["currency"],
        position_source_name=PROVIDER_TUPLE["positionSourceName"],
        decision_date=decision_at,
        authority=provider_authority,
        trusted_commit=trusted_commit,
    )
    if provider.get("status") != canonical_identity.RESOLVED:
        return _blocked(provider.get("status", "PROVIDER_AUTHORITY_INVALID"), bundle_sha)
    if provider.get("provider") != PROVIDER_TUPLE["provider"]:
        raise PortfolioAccountFactV3Error("PROVIDER_AUTHORITY_RESULT_TUPLE_MISMATCH")

    source_pairs = [
        (position["sourceName"], position["sourceAssetId"])
        for position in bundle["balanceObservation"]["positions"]
    ]
    capacity_instrument = bundle["instrumentBuyCapacityObservation"]["instrument"]
    source_pairs.append(
        (capacity_instrument["sourceName"], capacity_instrument["sourceAssetId"])
    )
    resolved_positions: list[dict] = []
    capacity_identity: dict | None = None
    for index, (source_name, source_asset_id) in enumerate(source_pairs):
        identity = canonical_identity.resolve_instrument_identity(
            source_name, source_asset_id, PROVIDER_TUPLE["accountScope"],
            decision_at, security_identity, trusted_commit=trusted_commit,
        )
        if identity.get("status") != canonical_identity.RESOLVED:
            return _blocked(
                NOT_COMPUTABLE_POSITION_IDENTITY_INCOMPLETE, bundle_sha,
                unresolvedPositionCount=1,
            )
        if index == len(source_pairs) - 1:
            capacity_identity = identity
        else:
            resolved_positions.append(identity)
    canonical_ids = [row["canonical_instrument_id"] for row in resolved_positions]
    if len(canonical_ids) != len(set(canonical_ids)):
        raise PortfolioAccountFactV3Error(
            "DUPLICATE_CANONICAL_INSTRUMENT_ACROSS_POSITIONS"
        )
    if capacity_identity is None or (
        capacity_identity.get("canonical_instrument_id")
        != EXACT_CAPACITY_CANONICAL_INSTRUMENT_ID
        or capacity_identity.get("listing_id") != EXACT_CAPACITY_LISTING_ID
    ):
        raise PortfolioAccountFactV3Error("BUY_CAPACITY_CANONICAL_IDENTITY_MISMATCH")

    semantic = valuation_authority.resolve_semantic_authority(
        decision_at=decision_at, authority=valuation_authority_document,
        trusted_commit=trusted_commit,
    )
    if (
        semantic.get("status") != valuation_authority.RESOLVED
        or semantic.get("authority", {}).get("valuationSemanticAuthorized") is not True
    ):
        return _blocked(
            NOT_COMPUTABLE_VALUATION_SEMANTIC_AUTHORITY, bundle_sha,
            semanticAuthorityStatus=semantic.get("status"),
        )

    freshness = valuation_authority.resolve_freshness_authority(
        decision_at=decision_at, authority=valuation_authority_document,
        trusted_commit=trusted_commit,
    )
    if (
        freshness.get("status") != valuation_authority.RESOLVED
        or freshness.get("authority", {}).get("freshnessPolicyAuthorized") is not True
    ):
        return _blocked(
            NOT_COMPUTABLE_FRESHNESS_AUTHORITY, bundle_sha,
            freshnessAuthorityStatus=freshness.get("status"),
        )
    policy = freshness["policy"]
    if (
        policy.get("clockField") != "availableAt"
        or type(policy.get("maxSourceAgeSeconds")) is not int
        or type(policy.get("maxPairGapSeconds")) is not int
        or policy.get("bothSourcesRequired") is not True
        or policy.get("callerOverridePermitted") is not False
    ):
        raise PortfolioAccountFactV3Error("FRESHNESS_POLICY_CONTRACT_INVALID")

    balance = bundle["balanceObservation"]
    capacity = bundle["instrumentBuyCapacityObservation"]
    balance_available = _parse_utc(
        balance["availableAt"], "BALANCE_AVAILABLE_AT_INVALID"
    )
    capacity_available = _parse_utc(
        capacity["availableAt"], "BUY_CAPACITY_AVAILABLE_AT_INVALID"
    )
    ages = [
        int((decision - balance_available).total_seconds()),
        int((decision - capacity_available).total_seconds()),
    ]
    if any(age < 0 or age > policy["maxSourceAgeSeconds"] for age in ages):
        return _blocked(
            NOT_COMPUTABLE_SOURCE_STALE_OR_FUTURE, bundle_sha,
            semanticAuthorityStatus=semantic["status"],
            freshnessAuthorityStatus=freshness["status"],
        )
    pair_gap = int(abs((capacity_available - balance_available).total_seconds()))
    if pair_gap > policy["maxPairGapSeconds"]:
        return _blocked(
            NOT_COMPUTABLE_SOURCE_PAIR_GAP_EXCEEDED, bundle_sha,
            semanticAuthorityStatus=semantic["status"],
            freshnessAuthorityStatus=freshness["status"],
        )

    return _blocked(
        NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED, bundle_sha,
        providerAuthorityStatus=provider["status"],
        resolvedPositionCount=len(resolved_positions),
        capacityIdentityStatus=capacity_identity["status"],
        semanticAuthorityStatus=semantic["status"],
        freshnessAuthorityStatus=freshness["status"],
        sourceAgeSeconds=max(ages),
        sourcePairGapSeconds=pair_gap,
    )
