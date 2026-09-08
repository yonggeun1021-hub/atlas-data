#!/usr/bin/env python3
"""``portfolio_account_fact/3`` producer and original-source-bound validator.

This module is a gate-9 extension placed strictly AFTER the existing
``portfolio_risk.portfolio_account_fact_v3`` readiness evaluator.  That
evaluator is imported unchanged and keeps emitting its byte-identical
``kis_portfolio_account_fact_v3_readiness/1`` envelope; nothing here edits
it, relaxes it, or re-implements any prerequisite.  Every public result of
this module is a ``/2`` envelope, including every blocked path, so a
consumer pinned to ``/1`` can never be handed a populated fact.

The one prerequisite the evaluator structurally cannot clear -- an
account-fact consumption authority -- is resolved here from a separate,
committed-empty registry.  With zero records the producer returns exactly
today's terminal ``NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED`` with
``accountFact=None``.

The validator deliberately does NOT trust the fact.  It revalidates the
caller-supplied ORIGINAL private source bundle, binds it by exact hash,
pins every authority document to one immutable commit, re-derives the
whole fact from that validated bundle plus the pinned documents, and
requires exact equality.  A coherently tampered fact whose internal
arithmetic still balances and whose ``factSha256`` has been recomputed is
therefore still rejected, because it no longer matches the original
source.  Authenticity of the private source BYTES remains the private
caller's boundary, exactly as before.

Producing a fact is a read-only data capability.  It grants no Portfolio
Risk Input, sizing, Stage, Buy, Action, Order, Production, Trading or REAL
authority, and this module performs no network, broker, persistence or
order operation.
"""
from __future__ import annotations

import datetime as dt

from identity import canonical_identity as canonical_identity
from portfolio_risk import kis_valuation_authority as valuation_authority
from portfolio_risk import (
    portfolio_account_fact_consumption_authority as consumption_authority,
)
from portfolio_risk import portfolio_account_fact_v3 as fact_v3


READINESS_VERSION = "kis_portfolio_account_fact_v3_readiness/2"
TARGET_CONTRACT_VERSION = fact_v3.TARGET_CONTRACT_VERSION
SOURCE_BUNDLE_VERSION = fact_v3.SOURCE_BUNDLE_VERSION
PROVIDER_TUPLE = dict(fact_v3.PROVIDER_TUPLE)

RESOLVED_ACCOUNT_FACT_PRODUCED = "RESOLVED_ACCOUNT_FACT_PRODUCED"
ACCOUNT_FACT_AUTHORITY_FILE_PROVENANCE_REQUIRED = (
    "ACCOUNT_FACT_AUTHORITY_FILE_PROVENANCE_REQUIRED"
)
ACCOUNT_FACT_AUTHORITY_SEMANTIC_BINDING_MISMATCH = (
    "ACCOUNT_FACT_AUTHORITY_SEMANTIC_BINDING_MISMATCH"
)
ACCOUNT_FACT_AUTHORITY_FRESHNESS_BINDING_MISMATCH = (
    "ACCOUNT_FACT_AUTHORITY_FRESHNESS_BINDING_MISMATCH"
)
ACCOUNT_FACT_AUTHORITY_CAPACITY_BINDING_MISMATCH = (
    consumption_authority.ACCOUNT_FACT_AUTHORITY_CAPACITY_BINDING_MISMATCH
)
ACCOUNT_FACT_CONSUMER_NOT_PERMITTED = (
    consumption_authority.ACCOUNT_FACT_CONSUMER_NOT_PERMITTED
)
ACCOUNT_FACT_FIELDS_INVALID = "ACCOUNT_FACT_FIELDS_INVALID"
ACCOUNT_FACT_CONTRACT_OR_TUPLE_INVALID = "ACCOUNT_FACT_CONTRACT_OR_TUPLE_INVALID"

# The exact disclaimer the held evaluator already emits. Asserted equal to
# the evaluator's own output in the focused suite so it cannot drift.
PRIVATE_SOURCE_VALIDATION_BOUNDARY = (
    "STRUCTURAL_HASH_BINDINGS_ONLY_PRIVATE_SOURCE_BYTES_MUST_BE_VALIDATED_BY_CALLER"
)

ACCOUNT_FACT_AUTHORITY = dict(
    consumption_authority.ACCOUNT_FACT_CONSUMPTION_AUTHORITY
)
ACCOUNT_FACT_AUTHORITY_ALL_FALSE = dict(
    consumption_authority.ACCOUNT_FACT_CONSUMPTION_AUTHORITY_ALL_FALSE
)

# A produced fact carries lineage and the seven ratified valuation
# mappings -- never an account-wide spendable aggregate, a risk/sizing
# figure, or a human label standing in for a canonical id.
FORBIDDEN_FACT_VOCABULARY = frozenset({
    "buyingPower", "orderableCash", "orderableCashKrw", "equity", "cash",
    "riskCapacity", "riskBudget", "budgetPct", "positionSize", "entrySize",
    "exposureWeight", "weight", "stage", "candidateValidity",
    "orderQuantity", "ticker", "issuerName", "accountNumber", "accountNo",
})

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_FACT_FIELDS = {
    "contractVersion", "providerTuple", "accountIdentityHash", "consumerId",
    "decisionAt", "sourceObservations", "account", "rawReconciliation",
    "positions", "instrumentBuyCapacity", "freshness", "authorityBasis",
    "sourceBundleSha256", "sourceBindings", "privateSourceValidationBoundary",
    "authority", "factSha256",
}
_SOURCE_OBSERVATION_FIELDS = {"balance", "buyCapacity"}
_OBSERVATION_FIELDS = {
    "sourceContractVersion", "sourceRecordSha256", "capturedAt", "availableAt",
}
_FACT_IDENTITY_FIELDS = (
    "canonicalIssuerId", "canonicalInstrumentId", "listingId",
)
_FACT_POSITION_FIELDS = {
    "sourceName", "sourceAssetId", "canonicalIssuerId",
    "canonicalInstrumentId", "listingId", "holdingQuantity",
    "orderableQuantity", "marketValueKrw", "unrealizedPlKrw",
}
_FACT_CAPACITY_FIELDS = {
    "sourceName", "sourceAssetId", "canonicalIssuerId",
    "canonicalInstrumentId", "listingId", "noReceivableBuyAmountKrw",
    "noReceivableBuyQuantity", "quantityCalculationPriceKrw",
}
_FRESHNESS_FIELDS = {"clockField", "sourceAgeSeconds", "sourcePairGapSeconds"}
_AUTHORITY_BASIS_FIELDS = {
    "trustedCommit", "providerAuthorityStatus", "consumption", "semantic",
    "freshness",
}
_CONSUMPTION_BASIS_FIELDS = {
    "ruleId", "ruleVersion", "businessPayloadSha256", "approvalEvidenceSha256",
    "realUsableFrom",
}
_ROW_BASIS_FIELDS = {"businessPayloadSha256", "realUsableFrom"}


class PortfolioAccountFactV3ProducerError(ValueError):
    pass


def canonical_json(value: object) -> str:
    return fact_v3.canonical_json(value)


def payload_sha256(value: object) -> str:
    return fact_v3.payload_sha256(value)


def _fail(code: str) -> None:
    raise PortfolioAccountFactV3ProducerError(code)


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = dt.datetime.strptime(value, _TIMESTAMP_FORMAT).replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        raise PortfolioAccountFactV3ProducerError(code) from None
    if parsed.strftime(_TIMESTAMP_FORMAT) != value:
        _fail(code)
    return parsed


def _strict_int(value: object, code: str, *, nonnegative: bool = False) -> int:
    if type(value) is not int or (nonnegative and value < 0):
        _fail(code)
    return value


def _sha256_field(value: object, code: str) -> str:
    if not isinstance(value, str) or fact_v3._SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonempty_str(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value


def _entry(value: object, expected_field: str, code: str, *, nonnegative: bool) -> int:
    if not isinstance(value, dict) or set(value) != fact_v3._ENTRY_FIELDS:
        _fail(f"{code}_FIELDS_INVALID")
    if value.get("rawKisField") != expected_field:
        _fail(f"{code}_KIS_FIELD_MISMATCH")
    return _strict_int(
        value.get("value"), f"{code}_VALUE_INVALID", nonnegative=nonnegative
    )


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

def _to_v2(envelope: dict, *, authority: dict | None = None, **extra: object) -> dict:
    """Re-label a ``/1`` readiness envelope as ``/2`` without losing a
    single diagnostic. The held evaluator's own output object is never
    mutated -- this always works on a copy."""
    result = dict(envelope)
    result["contractVersion"] = READINESS_VERSION
    result["authority"] = dict(
        authority if authority is not None else ACCOUNT_FACT_AUTHORITY_ALL_FALSE
    )
    if "accountFact" not in result:
        result["accountFact"] = None
    result.update(extra)
    return result


_CONSUMPTION_STATUS_PASSTHROUGH = frozenset({
    ACCOUNT_FACT_CONSUMER_NOT_PERMITTED,
    ACCOUNT_FACT_AUTHORITY_CAPACITY_BINDING_MISMATCH,
})


def _consumption_blocked_status(status: str) -> str:
    if status in _CONSUMPTION_STATUS_PASSTHROUGH:
        return status
    # Absent, unratified, not-yet-usable and expired records all keep the
    # exact terminal meaning the held evaluator has today.
    return fact_v3.NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED


def _document_source_paths(
    provider_authority: object, security_identity: object,
    valuation_authority_document: object, account_fact_authority_document: object,
) -> list[str]:
    if not isinstance(provider_authority, dict) or not provider_authority.get(
        "_source_path"
    ):
        _fail("PROVIDER_AUTHORITY_FILE_PROVENANCE_REQUIRED")
    if not isinstance(security_identity, dict) or not security_identity.get(
        "_source_path"
    ):
        _fail("SECURITY_IDENTITY_FILE_PROVENANCE_REQUIRED")
    if not isinstance(valuation_authority_document, dict) or not (
        valuation_authority_document.get("_sourcePath")
    ):
        _fail("VALUATION_AUTHORITY_FILE_PROVENANCE_REQUIRED")
    if not isinstance(account_fact_authority_document, dict) or not (
        account_fact_authority_document.get("_sourcePath")
    ):
        _fail(ACCOUNT_FACT_AUTHORITY_FILE_PROVENANCE_REQUIRED)
    return [
        provider_authority["_source_path"],
        security_identity["_source_path"],
        valuation_authority_document["_sourcePath"],
        account_fact_authority_document["_sourcePath"],
    ]


def _identity_entry(identity: dict) -> dict:
    return {
        "canonicalIssuerId": identity["canonical_issuer_id"],
        "canonicalInstrumentId": identity["canonical_instrument_id"],
        "listingId": identity["listing_id"],
    }


def _observation(observation: dict) -> dict:
    return {
        field: observation[field] for field in sorted(_OBSERVATION_FIELDS)
    }


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------

def build_kis_portfolio_account_fact_v3(
    *, bundle: object, decision_at: str, provider_authority: dict,
    security_identity: dict, valuation_authority_document: dict,
    account_fact_authority_document: dict, consumer_id: str,
    trusted_commit: str | None = None,
) -> dict:
    """Produce a ``portfolio_account_fact/3`` for one explicit consumer.

    Every prerequisite is delegated to the unchanged readiness evaluator.
    Only its exact terminal ``NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED``
    status opens the new gate; any other status is returned verbatim (as a
    ``/2`` envelope) with ``accountFact=None``.
    """
    consumer_id = consumption_authority.require_consumer_id(consumer_id)
    _parse_utc(decision_at, "DECISION_AT_INVALID")
    source_paths = _document_source_paths(
        provider_authority, security_identity, valuation_authority_document,
        account_fact_authority_document,
    )
    repo, commit = consumption_authority.resolve_trusted_commit(
        source_paths=source_paths, trusted_commit=trusted_commit,
    )

    readiness = fact_v3.evaluate_kis_portfolio_account_fact_v3_readiness(
        bundle=bundle, decision_at=decision_at,
        provider_authority=provider_authority,
        security_identity=security_identity,
        valuation_authority_document=valuation_authority_document,
        trusted_commit=commit,
    )
    if readiness["status"] != fact_v3.NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED:
        return _to_v2(readiness, trustedCommit=commit)

    consumption = consumption_authority.resolve_account_fact_consumption_authority(
        decision_at=decision_at, authority=account_fact_authority_document,
        consumer_id=consumer_id, repo=repo, trusted_commit=commit,
    )
    if (
        consumption["status"] != consumption_authority.RESOLVED
        or consumption["authority"].get("accountFactAuthorized") is not True
    ):
        return _to_v2(
            readiness, trustedCommit=commit,
            status=_consumption_blocked_status(consumption["status"]),
            accountFactAuthorityStatus=consumption["status"],
        )

    semantic = valuation_authority.resolve_semantic_authority(
        decision_at=decision_at, authority=valuation_authority_document,
        trusted_commit=commit,
    )
    freshness = valuation_authority.resolve_freshness_authority(
        decision_at=decision_at, authority=valuation_authority_document,
        trusted_commit=commit,
    )
    if semantic.get("status") != valuation_authority.RESOLVED:
        return _to_v2(
            readiness, trustedCommit=commit,
            status=fact_v3.NOT_COMPUTABLE_VALUATION_SEMANTIC_AUTHORITY,
        )
    if freshness.get("status") != valuation_authority.RESOLVED:
        return _to_v2(
            readiness, trustedCommit=commit,
            status=fact_v3.NOT_COMPUTABLE_FRESHNESS_AUTHORITY,
        )
    if (
        semantic["businessPayloadSha256"]
        != consumption["boundSemanticAuthorityBusinessPayloadSha256"]
    ):
        return _to_v2(
            readiness, trustedCommit=commit,
            status=ACCOUNT_FACT_AUTHORITY_SEMANTIC_BINDING_MISMATCH,
        )
    if (
        freshness["businessPayloadSha256"]
        != consumption["boundFreshnessAuthorityBusinessPayloadSha256"]
    ):
        return _to_v2(
            readiness, trustedCommit=commit,
            status=ACCOUNT_FACT_AUTHORITY_FRESHNESS_BINDING_MISMATCH,
        )

    validated = fact_v3.validate_source_bundle(bundle)
    balance = validated["balanceObservation"]
    capacity = validated["instrumentBuyCapacityObservation"]

    positions: list[dict] = []
    for position in balance["positions"]:
        identity = canonical_identity.resolve_instrument_identity(
            position["sourceName"], position["sourceAssetId"],
            PROVIDER_TUPLE["accountScope"], decision_at, security_identity,
            trusted_commit=commit,
        )
        if identity.get("status") != canonical_identity.RESOLVED:
            return _to_v2(
                readiness, trustedCommit=commit,
                status=fact_v3.NOT_COMPUTABLE_POSITION_IDENTITY_INCOMPLETE,
            )
        entry = {
            "sourceName": position["sourceName"],
            "sourceAssetId": position["sourceAssetId"],
            "holdingQuantity": position["holdingQuantity"],
            "orderableQuantity": position["orderableQuantity"],
        }
        entry.update(_identity_entry(identity))
        for name in fact_v3._POSITION_KIS_FIELDS:
            entry[name] = dict(position[name])
        positions.append(entry)

    capacity_instrument = capacity["instrument"]
    capacity_identity = canonical_identity.resolve_instrument_identity(
        capacity_instrument["sourceName"], capacity_instrument["sourceAssetId"],
        PROVIDER_TUPLE["accountScope"], decision_at, security_identity,
        trusted_commit=commit,
    )
    if capacity_identity.get("status") != canonical_identity.RESOLVED:
        return _to_v2(
            readiness, trustedCommit=commit,
            status=fact_v3.NOT_COMPUTABLE_POSITION_IDENTITY_INCOMPLETE,
        )
    if (
        capacity_identity["canonical_instrument_id"]
        != fact_v3.EXACT_CAPACITY_CANONICAL_INSTRUMENT_ID
        or capacity_identity["listing_id"] != fact_v3.EXACT_CAPACITY_LISTING_ID
    ):
        _fail("BUY_CAPACITY_CANONICAL_IDENTITY_MISMATCH")
    capacity_entry = {
        "sourceName": capacity_instrument["sourceName"],
        "sourceAssetId": capacity_instrument["sourceAssetId"],
    }
    capacity_entry.update(_identity_entry(capacity_identity))
    for name in fact_v3._INSTRUMENT_CAPACITY_KIS_FIELDS:
        capacity_entry[name] = dict(capacity["capacity"][name])

    fact = {
        "contractVersion": TARGET_CONTRACT_VERSION,
        "providerTuple": dict(PROVIDER_TUPLE),
        "accountIdentityHash": balance["accountIdentityHash"],
        "consumerId": consumer_id,
        "decisionAt": decision_at,
        "sourceObservations": {
            "balance": _observation(balance),
            "buyCapacity": _observation(capacity),
        },
        "account": {
            name: dict(balance["account"][name])
            for name in fact_v3._ACCOUNT_MAPPED_KIS_FIELDS
        },
        "rawReconciliation": dict(balance["rawReconciliation"]),
        "positions": positions,
        "instrumentBuyCapacity": [capacity_entry],
        "freshness": {
            "clockField": freshness["policy"]["clockField"],
            "sourceAgeSeconds": readiness["sourceAgeSeconds"],
            "sourcePairGapSeconds": readiness["sourcePairGapSeconds"],
        },
        "authorityBasis": {
            "trustedCommit": commit,
            "providerAuthorityStatus": readiness["providerAuthorityStatus"],
            "consumption": {
                "ruleId": consumption["ruleId"],
                "ruleVersion": consumption["ruleVersion"],
                "businessPayloadSha256": consumption["businessPayloadSha256"],
                "approvalEvidenceSha256": consumption["approvalEvidenceSha256"],
                "realUsableFrom": consumption["realUsableFrom"],
            },
            "semantic": {
                "businessPayloadSha256": semantic["businessPayloadSha256"],
                "realUsableFrom": semantic["realUsableFrom"],
            },
            "freshness": {
                "businessPayloadSha256": freshness["businessPayloadSha256"],
                "realUsableFrom": freshness["realUsableFrom"],
            },
        },
        "sourceBundleSha256": validated["bundleSha256"],
        "sourceBindings": dict(validated["sourceBindings"]),
        "privateSourceValidationBoundary": PRIVATE_SOURCE_VALIDATION_BOUNDARY,
        "authority": dict(ACCOUNT_FACT_AUTHORITY),
    }
    fact["factSha256"] = payload_sha256(fact)

    return _to_v2(
        readiness, authority=ACCOUNT_FACT_AUTHORITY,
        trustedCommit=commit,
        status=RESOLVED_ACCOUNT_FACT_PRODUCED,
        accountFact=fact,
        accountFactAuthorityStatus=consumption["status"],
        consumerId=consumer_id,
    )


# ---------------------------------------------------------------------------
# Independent, original-source-bound validator
# ---------------------------------------------------------------------------

def _validate_observations(fact: dict, decision: dt.datetime) -> None:
    observations = fact["sourceObservations"]
    if (
        not isinstance(observations, dict)
        or set(observations) != _SOURCE_OBSERVATION_FIELDS
    ):
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    expected_contract = {
        "balance": "kis_paper_full_account_snapshot/3",
        "buyCapacity": "kis_paper_buy_capacity_snapshot/1",
    }
    parsed: dict[str, tuple[dt.datetime, dt.datetime]] = {}
    for name, observation in observations.items():
        if not isinstance(observation, dict) or set(observation) != _OBSERVATION_FIELDS:
            _fail(ACCOUNT_FACT_FIELDS_INVALID)
        if observation["sourceContractVersion"] != expected_contract[name]:
            _fail("ACCOUNT_FACT_SOURCE_CONTRACT_INVALID")
        _sha256_field(
            observation["sourceRecordSha256"],
            "ACCOUNT_FACT_SOURCE_RECORD_SHA_INVALID",
        )
        captured = _parse_utc(
            observation["capturedAt"], "ACCOUNT_FACT_CAPTURED_AT_INVALID"
        )
        available = _parse_utc(
            observation["availableAt"], "ACCOUNT_FACT_AVAILABLE_AT_INVALID"
        )
        if available < captured:
            _fail("ACCOUNT_FACT_AVAILABLE_BEFORE_CAPTURED")
        if decision < available:
            _fail("ACCOUNT_FACT_SOURCE_IS_IN_THE_FUTURE")
        parsed[name] = (captured, available)
    if parsed["balance"][1] > parsed["buyCapacity"][0]:
        _fail("ACCOUNT_FACT_SOURCE_CAPTURE_SEQUENCE_INVALID")


def _validate_positions(fact: dict) -> tuple[int, int]:
    positions = fact["positions"]
    if not isinstance(positions, list):
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    seen_pairs: set[tuple[str, str]] = set()
    seen_instruments: set[str] = set()
    market_sum = 0
    pl_sum = 0
    for position in positions:
        if not isinstance(position, dict) or set(position) != _FACT_POSITION_FIELDS:
            _fail(ACCOUNT_FACT_FIELDS_INVALID)
        if position["sourceName"] != PROVIDER_TUPLE["positionSourceName"]:
            _fail("ACCOUNT_FACT_POSITION_SOURCE_NAME_INVALID")
        source_asset_id = position["sourceAssetId"]
        if (
            not isinstance(source_asset_id, str)
            or fact_v3._SOURCE_ASSET_ID_RE.fullmatch(source_asset_id) is None
        ):
            _fail("ACCOUNT_FACT_POSITION_SOURCE_ASSET_ID_INVALID")
        for field in _FACT_IDENTITY_FIELDS:
            _nonempty_str(position[field], "ACCOUNT_FACT_POSITION_IDENTITY_INVALID")
        pair = (position["sourceName"], source_asset_id)
        if pair in seen_pairs:
            _fail("ACCOUNT_FACT_POSITION_DUPLICATE")
        seen_pairs.add(pair)
        if position["canonicalInstrumentId"] in seen_instruments:
            _fail("ACCOUNT_FACT_DUPLICATE_CANONICAL_INSTRUMENT")
        seen_instruments.add(position["canonicalInstrumentId"])
        holding = _strict_int(
            position["holdingQuantity"],
            "ACCOUNT_FACT_HOLDING_QUANTITY_INVALID", nonnegative=True,
        )
        orderable = _strict_int(
            position["orderableQuantity"],
            "ACCOUNT_FACT_ORDERABLE_QUANTITY_INVALID", nonnegative=True,
        )
        if orderable > holding:
            _fail("ACCOUNT_FACT_ORDERABLE_ABOVE_HOLDING")
        market_sum += _entry(
            position["marketValueKrw"],
            fact_v3._POSITION_KIS_FIELDS["marketValueKrw"],
            "ACCOUNT_FACT_POSITION_MARKETVALUEKRW", nonnegative=True,
        )
        pl_sum += _entry(
            position["unrealizedPlKrw"],
            fact_v3._POSITION_KIS_FIELDS["unrealizedPlKrw"],
            "ACCOUNT_FACT_POSITION_UNREALIZEDPLKRW", nonnegative=False,
        )
    return market_sum, pl_sum


def _validate_capacity(fact: dict) -> None:
    entries = fact["instrumentBuyCapacity"]
    if not isinstance(entries, list) or len(entries) != 1:
        _fail("ACCOUNT_FACT_BUY_CAPACITY_NOT_EXACTLY_ONE_ENTRY")
    entry = entries[0]
    if not isinstance(entry, dict) or set(entry) != _FACT_CAPACITY_FIELDS:
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    if (
        entry["sourceName"] != PROVIDER_TUPLE["positionSourceName"]
        or entry["sourceAssetId"] != fact_v3.EXACT_CAPACITY_SOURCE_ASSET_ID
        or entry["canonicalInstrumentId"]
        != fact_v3.EXACT_CAPACITY_CANONICAL_INSTRUMENT_ID
        or entry["listingId"] != fact_v3.EXACT_CAPACITY_LISTING_ID
    ):
        _fail("ACCOUNT_FACT_BUY_CAPACITY_INSTRUMENT_NOT_EXACT_071050")
    _nonempty_str(
        entry["canonicalIssuerId"], "ACCOUNT_FACT_BUY_CAPACITY_IDENTITY_INVALID"
    )
    for name, raw_field in fact_v3._INSTRUMENT_CAPACITY_KIS_FIELDS.items():
        value = _entry(
            entry[name], raw_field,
            f"ACCOUNT_FACT_BUY_CAPACITY_{name.upper()}", nonnegative=True,
        )
        if name == "quantityCalculationPriceKrw" and value <= 0:
            _fail("ACCOUNT_FACT_BUY_CAPACITY_QUANTITY_CALCULATION_PRICE_NOT_POSITIVE")


def _validate_mapping_manifest(valuation_authority_document: dict) -> None:
    """Schema completeness comes from the live mapping manifest, never from
    whichever positions happen to be present -- an account with zero
    positions is valid and must not be treated as an incomplete mapping."""
    rows = valuation_authority_document.get("valuationSemanticAuthorityRecords")
    if not isinstance(rows, list) or len(rows) != 1:
        _fail("ACCOUNT_FACT_SEMANTIC_MANIFEST_UNAVAILABLE")
    approved = tuple(sorted(
        (mapping.get("rawKisField"), mapping.get("targetPath"))
        for mapping in rows[0].get("approvedMappings", [])
        if isinstance(mapping, dict)
    ))
    if approved != fact_v3._implemented_semantic_mapping_pairs():
        _fail("ACCOUNT_FACT_IMPLEMENTATION_MAPPING_MANIFEST_MISMATCH")


def _validate_authority_basis(fact: dict) -> None:
    basis = fact["authorityBasis"]
    if not isinstance(basis, dict) or set(basis) != _AUTHORITY_BASIS_FIELDS:
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    commit = basis["trustedCommit"]
    if (
        not isinstance(commit, str)
        or consumption_authority._FULL_COMMIT_RE.fullmatch(commit) is None
    ):
        _fail("ACCOUNT_FACT_TRUSTED_COMMIT_NOT_IMMUTABLE")
    if basis["providerAuthorityStatus"] != canonical_identity.RESOLVED:
        _fail("ACCOUNT_FACT_PROVIDER_AUTHORITY_STATUS_INVALID")
    consumption = basis["consumption"]
    if (
        not isinstance(consumption, dict)
        or set(consumption) != _CONSUMPTION_BASIS_FIELDS
    ):
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    if consumption["ruleId"] != consumption_authority.RULE_ID or type(
        consumption["ruleVersion"]
    ) is not int:
        _fail("ACCOUNT_FACT_CONSUMPTION_BASIS_INVALID")
    _sha256_field(
        consumption["businessPayloadSha256"], "ACCOUNT_FACT_CONSUMPTION_BASIS_INVALID"
    )
    _sha256_field(
        consumption["approvalEvidenceSha256"], "ACCOUNT_FACT_CONSUMPTION_BASIS_INVALID"
    )
    _parse_utc(
        consumption["realUsableFrom"], "ACCOUNT_FACT_CONSUMPTION_BASIS_INVALID"
    )
    for name in ("semantic", "freshness"):
        row = basis[name]
        if not isinstance(row, dict) or set(row) != _ROW_BASIS_FIELDS:
            _fail(ACCOUNT_FACT_FIELDS_INVALID)
        _sha256_field(
            row["businessPayloadSha256"], f"ACCOUNT_FACT_{name.upper()}_BASIS_INVALID"
        )
        _parse_utc(
            row["realUsableFrom"], f"ACCOUNT_FACT_{name.upper()}_BASIS_INVALID"
        )


def _assert_no_forbidden_vocabulary(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_FACT_VOCABULARY:
                _fail(f"ACCOUNT_FACT_FORBIDDEN_VOCABULARY:{key}")
            _assert_no_forbidden_vocabulary(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_vocabulary(child)


def validate_account_fact_structure(
    fact: object, *, valuation_authority_document: dict,
) -> dict:
    """Every invariant that can be checked from the fact alone.

    Deliberately independent of the producer: it never repairs a field and
    never returns a partially valid fact.
    """
    if not isinstance(fact, dict) or set(fact) != _FACT_FIELDS:
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    if (
        fact["contractVersion"] != TARGET_CONTRACT_VERSION
        or fact["providerTuple"] != PROVIDER_TUPLE
    ):
        _fail(ACCOUNT_FACT_CONTRACT_OR_TUPLE_INVALID)
    _assert_no_forbidden_vocabulary(fact)
    _sha256_field(
        fact["accountIdentityHash"], "ACCOUNT_FACT_ACCOUNT_BINDING_INVALID"
    )
    _nonempty_str(fact["consumerId"], "ACCOUNT_FACT_CONSUMER_ID_INVALID")
    decision = _parse_utc(fact["decisionAt"], "ACCOUNT_FACT_DECISION_AT_INVALID")
    _validate_observations(fact, decision)

    account = fact["account"]
    if not isinstance(account, dict) or set(account) != fact_v3._ACCOUNT_FIELDS:
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    account_values = {
        name: _entry(
            account[name], raw_field, f"ACCOUNT_FACT_ACCOUNT_{name.upper()}",
            nonnegative=True,
        )
        for name, raw_field in fact_v3._ACCOUNT_MAPPED_KIS_FIELDS.items()
    }
    raw = fact["rawReconciliation"]
    if not isinstance(raw, dict) or set(raw) != fact_v3._RAW_RECONCILIATION_FIELDS:
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    for raw_field in fact_v3._RAW_RECONCILIATION_FIELDS:
        _strict_int(
            raw[raw_field],
            f"ACCOUNT_FACT_RAW_RECONCILIATION_VALUE_INVALID:{raw_field}",
            nonnegative=raw_field != "evlu_pfls_smtl_amt",
        )

    market_sum, pl_sum = _validate_positions(fact)
    _validate_capacity(fact)
    _validate_mapping_manifest(valuation_authority_document)

    cash = account_values["cashDepositTotalKrw"]
    if market_sum != raw["evlu_amt_smtl_amt"]:
        _fail("ACCOUNT_FACT_POSITION_MARKET_VALUE_SUM_MISMATCH")
    if market_sum != raw["scts_evlu_amt"]:
        _fail("ACCOUNT_FACT_SECURITIES_VALUATION_SUM_MISMATCH")
    if pl_sum != raw["evlu_pfls_smtl_amt"]:
        _fail("ACCOUNT_FACT_POSITION_UNREALIZED_PL_SUM_MISMATCH")
    if cash + market_sum != raw["tot_evlu_amt"]:
        _fail("ACCOUNT_FACT_TOTAL_VALUATION_RELATIONSHIP_MISMATCH")
    if cash + market_sum != account_values["netAssetKrw"]:
        _fail("ACCOUNT_FACT_NET_ASSET_RELATIONSHIP_MISMATCH")

    freshness = fact["freshness"]
    if not isinstance(freshness, dict) or set(freshness) != _FRESHNESS_FIELDS:
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    _nonempty_str(freshness["clockField"], "ACCOUNT_FACT_FRESHNESS_INVALID")
    balance_available = _parse_utc(
        fact["sourceObservations"]["balance"]["availableAt"],
        "ACCOUNT_FACT_AVAILABLE_AT_INVALID",
    )
    capacity_available = _parse_utc(
        fact["sourceObservations"]["buyCapacity"]["availableAt"],
        "ACCOUNT_FACT_AVAILABLE_AT_INVALID",
    )
    expected_age = max(
        int((decision - balance_available).total_seconds()),
        int((decision - capacity_available).total_seconds()),
    )
    expected_gap = int(
        abs((capacity_available - balance_available).total_seconds())
    )
    if _strict_int(
        freshness["sourceAgeSeconds"], "ACCOUNT_FACT_FRESHNESS_INVALID",
        nonnegative=True,
    ) != expected_age:
        _fail("ACCOUNT_FACT_FRESHNESS_AGE_MISMATCH")
    if _strict_int(
        freshness["sourcePairGapSeconds"], "ACCOUNT_FACT_FRESHNESS_INVALID",
        nonnegative=True,
    ) != expected_gap:
        _fail("ACCOUNT_FACT_FRESHNESS_PAIR_GAP_MISMATCH")

    _validate_authority_basis(fact)
    _sha256_field(
        fact["sourceBundleSha256"], "ACCOUNT_FACT_SOURCE_BUNDLE_SHA_INVALID"
    )
    bindings = fact["sourceBindings"]
    if (
        not isinstance(bindings, dict)
        or set(bindings) != fact_v3._SOURCE_BINDING_FIELDS
    ):
        _fail(ACCOUNT_FACT_FIELDS_INVALID)
    for field in fact_v3._SOURCE_BINDING_FIELDS:
        _sha256_field(bindings[field], f"ACCOUNT_FACT_SOURCE_BINDING_INVALID:{field}")
    if (
        bindings["fullAccountRecordSha256"]
        != fact["sourceObservations"]["balance"]["sourceRecordSha256"]
    ):
        _fail("ACCOUNT_FACT_FULL_ACCOUNT_SOURCE_BINDING_MISMATCH")
    if (
        bindings["buyCapacityRecordSha256"]
        != fact["sourceObservations"]["buyCapacity"]["sourceRecordSha256"]
    ):
        _fail("ACCOUNT_FACT_BUY_CAPACITY_SOURCE_BINDING_MISMATCH")
    if fact["privateSourceValidationBoundary"] != PRIVATE_SOURCE_VALIDATION_BOUNDARY:
        _fail("ACCOUNT_FACT_PRIVATE_SOURCE_BOUNDARY_INVALID")
    authority = fact["authority"]
    if (
        not isinstance(authority, dict)
        or authority != ACCOUNT_FACT_AUTHORITY
        or any(type(value) is not bool for value in authority.values())
    ):
        _fail("ACCOUNT_FACT_AUTHORITY_BOUNDARY_INVALID")
    unsigned = {key: value for key, value in fact.items() if key != "factSha256"}
    if fact["factSha256"] != payload_sha256(unsigned):
        _fail("ACCOUNT_FACT_SHA_MISMATCH")
    return dict(fact)


def validate_kis_portfolio_account_fact_v3(
    fact: object, *, source_bundle: object, decision_at: str, consumer_id: str,
    provider_authority: dict, security_identity: dict,
    valuation_authority_document: dict, account_fact_authority_document: dict,
    trusted_commit: str | None = None,
) -> dict:
    """Validate a fact against its ORIGINAL source and pinned authority.

    A self-consistent fact is not enough: the caller must hand over the
    exact private source bundle the fact claims to come from.  That bundle
    is revalidated with the existing bundle validator, bound by exact
    ``sourceBundleSha256``, and the whole fact is then re-derived from it
    and required to match field for field.  Recomputing ``factSha256``
    after a coherent edit therefore does not help an attacker.
    """
    consumer_id = consumption_authority.require_consumer_id(consumer_id)
    _parse_utc(decision_at, "DECISION_AT_INVALID")
    validated_fact = validate_account_fact_structure(
        fact, valuation_authority_document=valuation_authority_document,
    )
    if validated_fact["consumerId"] != consumer_id:
        _fail("ACCOUNT_FACT_CONSUMER_BINDING_MISMATCH")
    if validated_fact["decisionAt"] != decision_at:
        _fail("ACCOUNT_FACT_DECISION_BINDING_MISMATCH")

    validated_bundle = fact_v3.validate_source_bundle(source_bundle)
    if validated_bundle["bundleSha256"] != validated_fact["sourceBundleSha256"]:
        _fail("ACCOUNT_FACT_SOURCE_BUNDLE_BINDING_MISMATCH")

    source_paths = _document_source_paths(
        provider_authority, security_identity, valuation_authority_document,
        account_fact_authority_document,
    )
    _, commit = consumption_authority.resolve_trusted_commit(
        source_paths=source_paths, trusted_commit=trusted_commit,
    )
    if validated_fact["authorityBasis"]["trustedCommit"] != commit:
        # Never silently accept a fact pinned to some other commit, and
        # never silently re-validate history against a later HEAD.
        _fail("ACCOUNT_FACT_TRUSTED_COMMIT_MISMATCH")

    rederived = build_kis_portfolio_account_fact_v3(
        bundle=validated_bundle, decision_at=decision_at,
        provider_authority=provider_authority,
        security_identity=security_identity,
        valuation_authority_document=valuation_authority_document,
        account_fact_authority_document=account_fact_authority_document,
        consumer_id=consumer_id, trusted_commit=commit,
    )
    if rederived["status"] != RESOLVED_ACCOUNT_FACT_PRODUCED:
        raise PortfolioAccountFactV3ProducerError(
            f"ACCOUNT_FACT_REDERIVATION_BLOCKED:{rederived['status']}"
        )
    if rederived["accountFact"] != validated_fact:
        _fail("ACCOUNT_FACT_REDERIVATION_MISMATCH")
    return validated_fact
