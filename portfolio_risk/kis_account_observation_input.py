#!/usr/bin/env python3
"""``kis_account_observation_input/1`` builder and independent validator.

This is the PUBLIC half of the adopted KIS PAPER account-observation
consumer.  It is a thin, closed envelope around exactly one
``portfolio_account_fact/3`` produced by the unchanged
``portfolio_risk.portfolio_account_fact_v3_producer``.  Nothing here
re-implements, re-derives or re-interprets the fact: the original fact is
carried VERBATIM, and the whole question "is this fact real?" is delegated
to that module's original-source-bound validator, which rebuilds the fact
from the caller-supplied ORIGINAL private source bundle against one
immutable trusted commit.

Three properties are deliberate.

*Nothing is trusted on its own word.*  Building requires the separately
supplied original bundle, the explicit decision instant, the explicit
consumer and the pinned authority documents; validating repeats the entire
derivation and requires the rebuilt packet to equal the supplied one field
for field.  ``packetSha256`` is checked, but a correctly recomputed
``packetSha256`` after a coherent edit buys an attacker nothing, because
the packet is rederived from the original source anyway.

*The envelope is a single account observation, not a portfolio.*
``coverage`` is closed to KOREA/KRW/``SINGLE_KIS_ACCOUNT_OBSERVATION``
with ``wholePortfolioComplete=False``.  Whole-portfolio NAV, FX
conversion, cross-provider equity, account purchasing power and
``risk_capacity_inputs`` are absent by construction and refused by name.
``netAssetKrw`` stays ``netAssetKrw``; ``noReceivableBuyAmountKrw`` stays
``noReceivableBuyAmountKrw``; no price is derived from a market value and
a quantity.

*The consumer identifier is not a grant.*  It is the adopted routing
scope of one consumer and must be stated explicitly by the caller.  The
real allowlist lives in the separately ratified account-fact consumption
authority registry, which ships EMPTY, and this module neither reads
around it nor supplies a substitute for it.

No network, broker, credential, provider, account, order or persistence
operation happens here, and no Portfolio Risk Input, sizing, Stage, Buy,
Action, Order, Production, Trading or REAL authority is created.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
import re

from portfolio_risk import (
    portfolio_account_fact_consumption_authority as consumption_authority,
)
from portfolio_risk import portfolio_account_fact_v3 as fact_v3
from portfolio_risk import portfolio_account_fact_v3_producer as producer


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "kis_account_observation_input_contract.json"

CONTRACT_VERSION = "kis_account_observation_input/1"
CONTRACT_SCHEMA_VERSION = 1
CONTRACT_STATUS = "ADOPTED_MECHANISM_ONLY_NOT_A_RUNTIME_GRANT"

# The adopted TECHNICAL identifier of the one consumer this contract is
# shaped for -- a routing scope, never authentication and never an
# allowlist entry.  The real allowlist stays in the separately ratified
# consumption authority registry, which is still committed empty.
ADOPTED_CONSUMER_ID = (
    "atlas-private-evidence.shadow_matrix.kis_account_observation.v1"
)
PERMITTED_USE = "READ_ONLY_KIS_PAPER_ACCOUNT_OBSERVATION"

PROVIDER_TUPLE = dict(producer.PROVIDER_TUPLE)
TARGET_CONTRACT_VERSION = producer.TARGET_CONTRACT_VERSION
SOURCE_BUNDLE_CONTRACT_VERSION = producer.SOURCE_BUNDLE_VERSION

# The exact adopted ten fields, in the exact adopted order.
PACKET_FIELDS = (
    "contractVersion",
    "consumerId",
    "decisionAt",
    "providerTuple",
    "accountIdentityHash",
    "accountFact",
    "sourceBundleSha256",
    "coverage",
    "authority",
    "packetSha256",
)

COVERAGE_KIND = "SINGLE_KIS_ACCOUNT_OBSERVATION"
COVERAGE = {
    "accountScope": "KOREA",
    "currency": "KRW",
    "coverageKind": COVERAGE_KIND,
    "wholePortfolioComplete": False,
}

# Exactly the fact-only permission the validated fact already carries:
# accountFactAuthorized true, every downstream flag false.  Preserved,
# never minted.
OBSERVATION_AUTHORITY = dict(producer.ACCOUNT_FACT_AUTHORITY)

# Envelope vocabulary that must never appear around the carried fact.
FORBIDDEN_OBSERVATION_VOCABULARY = frozenset({
    "buyingPower", "buying_power", "candidateCount", "cells",
    "currencyConversion", "equity", "fxRate", "nav", "netAssetValue",
    "positionSize", "price", "riskBudget", "riskCapacity",
    "riskCapacityInputs", "risk_capacity_inputs", "unitPrice",
    "wholePortfolioNav",
})

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_PACKET_FIELD_SET = set(PACKET_FIELDS)
_COVERAGE_FIELDS = set(COVERAGE)
_AUTHORITY_FIELDS = set(OBSERVATION_AUTHORITY)

_CONTRACT_FIELDS = {
    "schema_version", "contract_version", "status", "purpose", "consumer_id",
    "permitted_use", "real_consumer_allowlist", "real_consumer_allowlist_note",
    "packet_fields", "provider_tuple", "upstream", "coverage", "coverage_note",
    "authority", "authority_note", "forbidden_exposure",
    "forbidden_exposure_note", "downstream_risk_consumption",
    "real_data_persistence", "runtime_ratified", "operationally_applied",
}
_CONTRACT_UPSTREAM_FIELDS = {
    "target_contract_version", "source_bundle_contract_version",
    "producer_module", "producer_validator", "note",
}
_CONTRACT_DOWNSTREAM_FIELDS = {"risk_input_authorized", "note"}
_CONTRACT_PERSISTENCE_FIELDS = {"status", "note"}


class KisAccountObservationInputError(ValueError):
    pass


def canonical_json(value: object) -> str:
    return fact_v3.canonical_json(value)


def payload_sha256(value: object) -> str:
    return fact_v3.payload_sha256(value)


def _fail(code: str) -> None:
    raise KisAccountObservationInputError(code)


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = dt.datetime.strptime(value, _TIMESTAMP_FORMAT).replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        raise KisAccountObservationInputError(code) from None
    if parsed.strftime(_TIMESTAMP_FORMAT) != value:
        _fail(code)
    return parsed


def _sha256_field(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _assert_no_forbidden_vocabulary(value: object, code: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_OBSERVATION_VOCABULARY:
                _fail(f"{code}:{key}")
            _assert_no_forbidden_vocabulary(child, code)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_vocabulary(child, code)


def require_adopted_consumer_id(consumer_id: object) -> str:
    """The caller must name itself, and it must be the adopted consumer.

    Blankness is refused by the existing account-fact rule so "explicit
    caller" keeps exactly one definition across both modules.
    """
    consumer_id = consumption_authority.require_consumer_id(consumer_id)
    if consumer_id != ADOPTED_CONSUMER_ID:
        _fail("KIS_OBSERVATION_CONSUMER_NOT_ADOPTED")
    return consumer_id


# ---------------------------------------------------------------------------
# Mechanical closed contract
# ---------------------------------------------------------------------------

def validate_contract_document(document: object) -> dict:
    """Require the committed contract to agree exactly with live code.

    Deliberately not an authority document: it grants nothing, so it needs
    no git provenance or approval evidence.  What it does need is to be
    unable to drift.  Every field is compared against a live constant, so
    a mutation of either side fails closed instead of quietly redefining
    the packet, the coverage or the permission.
    """
    if not isinstance(document, dict) or set(document) != _CONTRACT_FIELDS:
        _fail("KIS_OBSERVATION_CONTRACT_DOCUMENT_FIELDS_INVALID")
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        _fail("KIS_OBSERVATION_CONTRACT_SCHEMA_INVALID")
    if document["contract_version"] != CONTRACT_VERSION:
        _fail("KIS_OBSERVATION_CONTRACT_VERSION_INVALID")
    if document["status"] != CONTRACT_STATUS:
        _fail("KIS_OBSERVATION_CONTRACT_STATUS_INVALID")
    if document["consumer_id"] != ADOPTED_CONSUMER_ID:
        _fail("KIS_OBSERVATION_CONTRACT_CONSUMER_INVALID")
    if document["permitted_use"] != PERMITTED_USE:
        _fail("KIS_OBSERVATION_CONTRACT_PERMITTED_USE_INVALID")
    if document["real_consumer_allowlist"] != []:
        # The technical consumer identifier is not a grant, and this file
        # is not where a real consumer is ever admitted.
        _fail("KIS_OBSERVATION_CONTRACT_ALLOWLIST_NOT_EMPTY")
    if document["packet_fields"] != list(PACKET_FIELDS):
        _fail("KIS_OBSERVATION_CONTRACT_PACKET_FIELDS_INVALID")
    if document["provider_tuple"] != PROVIDER_TUPLE:
        _fail("KIS_OBSERVATION_CONTRACT_PROVIDER_TUPLE_INVALID")
    upstream = document["upstream"]
    if not isinstance(upstream, dict) or set(upstream) != _CONTRACT_UPSTREAM_FIELDS:
        _fail("KIS_OBSERVATION_CONTRACT_UPSTREAM_INVALID")
    if (
        upstream["target_contract_version"] != TARGET_CONTRACT_VERSION
        or upstream["source_bundle_contract_version"]
        != SOURCE_BUNDLE_CONTRACT_VERSION
        or upstream["producer_module"] != producer.__name__
        or upstream["producer_validator"]
        != producer.validate_kis_portfolio_account_fact_v3.__name__
    ):
        _fail("KIS_OBSERVATION_CONTRACT_UPSTREAM_INVALID")
    validate_coverage(document["coverage"])
    validate_fact_only_authority(document["authority"])
    if document["forbidden_exposure"] != sorted(FORBIDDEN_OBSERVATION_VOCABULARY):
        _fail("KIS_OBSERVATION_CONTRACT_FORBIDDEN_EXPOSURE_INVALID")
    downstream = document["downstream_risk_consumption"]
    if (
        not isinstance(downstream, dict)
        or set(downstream) != _CONTRACT_DOWNSTREAM_FIELDS
        or downstream["risk_input_authorized"] is not False
    ):
        _fail("KIS_OBSERVATION_CONTRACT_DOWNSTREAM_INVALID")
    persistence = document["real_data_persistence"]
    if (
        not isinstance(persistence, dict)
        or set(persistence) != _CONTRACT_PERSISTENCE_FIELDS
        or persistence["status"] != "NO_PERSISTENCE_IN_THIS_CONTRACT"
    ):
        _fail("KIS_OBSERVATION_CONTRACT_PERSISTENCE_INVALID")
    for field in (
        "purpose", "real_consumer_allowlist_note", "coverage_note",
        "authority_note", "forbidden_exposure_note",
    ):
        if not isinstance(document[field], str) or not document[field].strip():
            _fail(f"KIS_OBSERVATION_CONTRACT_TEXT_FIELD_INVALID:{field}")
    if document["runtime_ratified"] is not False:
        _fail("KIS_OBSERVATION_CONTRACT_RUNTIME_RATIFIED_INVALID")
    if document["operationally_applied"] is not False:
        _fail("KIS_OBSERVATION_CONTRACT_OPERATIONALLY_APPLIED_INVALID")
    return dict(document)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        document = json.loads(Path(path).resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise KisAccountObservationInputError(
            "KIS_OBSERVATION_CONTRACT_DOCUMENT_READ_FAILED"
        ) from error
    return validate_contract_document(document)


# ---------------------------------------------------------------------------
# Closed envelope invariants
# ---------------------------------------------------------------------------

def validate_coverage(coverage: object) -> dict:
    """One KIS account in its own currency -- never a portfolio."""
    if not isinstance(coverage, dict) or set(coverage) != _COVERAGE_FIELDS:
        _fail("KIS_OBSERVATION_COVERAGE_INVALID")
    if type(coverage["wholePortfolioComplete"]) is not bool:
        # An int 0 is not a false boolean here.
        _fail("KIS_OBSERVATION_COVERAGE_INVALID")
    if coverage["wholePortfolioComplete"] is not False:
        _fail("KIS_OBSERVATION_WHOLE_PORTFOLIO_EXPOSURE_FORBIDDEN")
    if coverage["coverageKind"] != COVERAGE_KIND:
        _fail("KIS_OBSERVATION_COVERAGE_INVALID")
    if (
        coverage["accountScope"] != PROVIDER_TUPLE["accountScope"]
        or coverage["currency"] != PROVIDER_TUPLE["currency"]
    ):
        _fail("KIS_OBSERVATION_COVERAGE_PROVIDER_BINDING_MISMATCH")
    if coverage != COVERAGE:
        _fail("KIS_OBSERVATION_COVERAGE_INVALID")
    return dict(coverage)


def validate_fact_only_authority(authority: object) -> dict:
    """Fact-only permission, checked structurally as well as by equality."""
    if not isinstance(authority, dict) or set(authority) != _AUTHORITY_FIELDS:
        _fail("KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID")
    if any(type(flag) is not bool for flag in authority.values()):
        _fail("KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID")
    if authority["accountFactAuthorized"] is not True:
        _fail("KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID")
    if any(
        flag is not False
        for name, flag in authority.items()
        if name != "accountFactAuthorized"
    ):
        # Every risk/stage/buy/action/order/production/trading/real-capital
        # flag stays false; an upgraded block is refused, never downgraded.
        _fail("KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID")
    if authority != OBSERVATION_AUTHORITY:
        _fail("KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID")
    return dict(authority)


def validate_observation_packet_structure(packet: object) -> dict:
    """Every invariant checkable from the packet alone.

    This is necessary and never sufficient: it cannot tell whether the
    carried fact is real.  Only the original-source rederivation in
    :func:`validate_kis_account_observation_input` can.
    """
    if not isinstance(packet, dict) or set(packet) != _PACKET_FIELD_SET:
        _fail("KIS_OBSERVATION_PACKET_FIELDS_INVALID")
    if packet["contractVersion"] != CONTRACT_VERSION:
        _fail("KIS_OBSERVATION_CONTRACT_VERSION_INVALID")
    if packet["consumerId"] != ADOPTED_CONSUMER_ID:
        _fail("KIS_OBSERVATION_CONSUMER_NOT_ADOPTED")
    _parse_utc(packet["decisionAt"], "KIS_OBSERVATION_DECISION_AT_INVALID")
    if packet["providerTuple"] != PROVIDER_TUPLE:
        _fail("KIS_OBSERVATION_PROVIDER_TUPLE_INVALID")
    _sha256_field(
        packet["accountIdentityHash"], "KIS_OBSERVATION_ACCOUNT_BINDING_INVALID"
    )
    _sha256_field(
        packet["sourceBundleSha256"],
        "KIS_OBSERVATION_SOURCE_BUNDLE_SHA_INVALID",
    )
    validate_coverage(packet["coverage"])
    validate_fact_only_authority(packet["authority"])
    # Defence in depth. The closed ten-field set is the real guarantee, so
    # this cannot fire today; it refuses a whole-portfolio, FX, equity,
    # purchasing-power or risk-capacity alias by name if that ever changes.
    _assert_no_forbidden_vocabulary(
        {name: value for name, value in packet.items() if name != "accountFact"},
        "KIS_OBSERVATION_FORBIDDEN_ENVELOPE_VOCABULARY",
    )

    fact = packet["accountFact"]
    if fact is None:
        _fail("KIS_OBSERVATION_ACCOUNT_FACT_MISSING")
    if not isinstance(fact, dict) or not fact:
        _fail("KIS_OBSERVATION_ACCOUNT_FACT_INVALID")
    if fact.get("contractVersion") != TARGET_CONTRACT_VERSION:
        _fail("KIS_OBSERVATION_ACCOUNT_FACT_CONTRACT_INVALID")
    if fact.get("accountIdentityHash") != packet["accountIdentityHash"]:
        _fail("KIS_OBSERVATION_ACCOUNT_BINDING_MISMATCH")
    if fact.get("sourceBundleSha256") != packet["sourceBundleSha256"]:
        _fail("KIS_OBSERVATION_SOURCE_BUNDLE_BINDING_MISMATCH")
    if fact.get("consumerId") != packet["consumerId"]:
        _fail("KIS_OBSERVATION_FACT_CONSUMER_BINDING_MISMATCH")
    if fact.get("decisionAt") != packet["decisionAt"]:
        _fail("KIS_OBSERVATION_FACT_DECISION_BINDING_MISMATCH")
    if fact.get("providerTuple") != PROVIDER_TUPLE:
        _fail("KIS_OBSERVATION_PROVIDER_TUPLE_INVALID")

    unsigned = {
        name: value for name, value in packet.items() if name != "packetSha256"
    }
    if packet["packetSha256"] != payload_sha256(unsigned):
        _fail("KIS_OBSERVATION_PACKET_SHA_MISMATCH")
    return dict(packet)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_kis_account_observation_input(
    *, account_fact: object, source_bundle: object, decision_at: str,
    consumer_id: str, provider_authority: dict, security_identity: dict,
    valuation_authority_document: dict, account_fact_authority_document: dict,
    trusted_commit: str | None = None,
) -> dict:
    """Wrap ONE independently validated account fact for ONE consumer.

    The supplied fact is not taken on trust.  It is revalidated against
    the separately supplied ORIGINAL source bundle, the explicit decision
    instant and consumer, and the pinned authority documents by the
    existing producer validator, which rebuilds the entire fact from that
    bundle.  Only the fact that survives is carried -- verbatim, under its
    own native field names.

    A missing fact, an unusable authority document or a source that does
    not match is a precise closed error or the existing producer error.
    No positive envelope is ever fabricated, and no provider call or
    persistence happens.
    """
    contract = load_contract()
    consumer_id = require_adopted_consumer_id(consumer_id)
    _parse_utc(decision_at, "KIS_OBSERVATION_DECISION_AT_INVALID")
    if account_fact is None:
        _fail("KIS_OBSERVATION_ACCOUNT_FACT_MISSING")
    if not isinstance(account_fact, dict) or not account_fact:
        _fail("KIS_OBSERVATION_ACCOUNT_FACT_INVALID")

    validated_fact = producer.validate_kis_portfolio_account_fact_v3(
        account_fact, source_bundle=source_bundle, decision_at=decision_at,
        consumer_id=consumer_id, provider_authority=provider_authority,
        security_identity=security_identity,
        valuation_authority_document=valuation_authority_document,
        account_fact_authority_document=account_fact_authority_document,
        trusted_commit=trusted_commit,
    )
    # Re-asserted at the module boundary rather than assumed. The producer
    # enforces both today, so these cannot fire now; they exist so a later
    # producer-side change can never silently widen this envelope.
    if validated_fact["contractVersion"] != TARGET_CONTRACT_VERSION:
        _fail("KIS_OBSERVATION_ACCOUNT_FACT_CONTRACT_INVALID")
    provider_tuple = validated_fact["providerTuple"]
    if provider_tuple != PROVIDER_TUPLE:
        _fail("KIS_OBSERVATION_PROVIDER_TUPLE_INVALID")

    packet = {
        "contractVersion": CONTRACT_VERSION,
        "consumerId": consumer_id,
        "decisionAt": validated_fact["decisionAt"],
        "providerTuple": dict(provider_tuple),
        "accountIdentityHash": _sha256_field(
            validated_fact["accountIdentityHash"],
            "KIS_OBSERVATION_ACCOUNT_BINDING_INVALID",
        ),
        # Carried verbatim. Deep-copied only so a later mutation of the
        # caller's object cannot reach inside an already built packet.
        "accountFact": copy.deepcopy(validated_fact),
        "sourceBundleSha256": _sha256_field(
            validated_fact["sourceBundleSha256"],
            "KIS_OBSERVATION_SOURCE_BUNDLE_SHA_INVALID",
        ),
        # Derived from the validated fact's own tuple, then required to be
        # the exact adopted single-account coverage.
        "coverage": validate_coverage({
            "accountScope": provider_tuple["accountScope"],
            "currency": provider_tuple["currency"],
            "coverageKind": COVERAGE_KIND,
            "wholePortfolioComplete": False,
        }),
        # Preserved from the independently verified fact, never minted.
        "authority": validate_fact_only_authority(validated_fact["authority"]),
    }
    # Defence in depth. The closed ten-field set is the real guarantee, so
    # this cannot fire today; it refuses a whole-portfolio, FX, equity,
    # purchasing-power or risk-capacity alias by name if that ever changes.
    _assert_no_forbidden_vocabulary(
        {name: value for name, value in packet.items() if name != "accountFact"},
        "KIS_OBSERVATION_FORBIDDEN_ENVELOPE_VOCABULARY",
    )
    packet["packetSha256"] = payload_sha256(packet)
    if tuple(packet) != PACKET_FIELDS:
        _fail("KIS_OBSERVATION_PACKET_FIELDS_INVALID")
    _assert_contract_agreement(contract, packet)
    return packet


def _assert_contract_agreement(contract: dict, packet: dict) -> None:
    """The committed contract is load bearing, not decorative."""
    if (
        contract["packet_fields"] != list(packet)
        or contract["consumer_id"] != packet["consumerId"]
        or contract["contract_version"] != packet["contractVersion"]
        or contract["provider_tuple"] != packet["providerTuple"]
        or contract["coverage"] != packet["coverage"]
        or contract["authority"] != packet["authority"]
    ):
        _fail("KIS_OBSERVATION_CONTRACT_PACKET_DISAGREEMENT")


# ---------------------------------------------------------------------------
# Independent, original-source-bound validator
# ---------------------------------------------------------------------------

def validate_kis_account_observation_input(
    packet: object, *, source_bundle: object, decision_at: str,
    consumer_id: str, provider_authority: dict, security_identity: dict,
    valuation_authority_document: dict, account_fact_authority_document: dict,
    trusted_commit: str | None = None,
) -> dict:
    """Rederive the WHOLE packet and require exact equality.

    The self-hash is checked, and it is never the evidence.  The caller
    must hand over the exact original source bundle, the decision instant,
    the consumer and the pinned authority documents; the entire packet --
    envelope, coverage, authority and the carried fact, which is itself
    rebuilt from that bundle by the existing producer validator -- is
    reconstructed and compared field for field.  A previously validated
    status is never replayed as a substitute.
    """
    load_contract()
    consumer_id = require_adopted_consumer_id(consumer_id)
    _parse_utc(decision_at, "KIS_OBSERVATION_DECISION_AT_INVALID")
    if not isinstance(packet, dict) or set(packet) != _PACKET_FIELD_SET:
        _fail("KIS_OBSERVATION_PACKET_FIELDS_INVALID")
    if packet["consumerId"] != consumer_id:
        _fail("KIS_OBSERVATION_CONSUMER_BINDING_MISMATCH")
    if packet["decisionAt"] != decision_at:
        _fail("KIS_OBSERVATION_DECISION_BINDING_MISMATCH")

    validated_packet = validate_observation_packet_structure(packet)

    # Bind the ORIGINAL bundle before any rederivation, so a wrong source
    # with a correctly recomputed bundle hash is refused by name.
    validated_bundle = fact_v3.validate_source_bundle(source_bundle)
    if validated_bundle["bundleSha256"] != validated_packet["sourceBundleSha256"]:
        _fail("KIS_OBSERVATION_ORIGINAL_SOURCE_BINDING_MISMATCH")

    rederived = build_kis_account_observation_input(
        account_fact=validated_packet["accountFact"],
        source_bundle=validated_bundle, decision_at=decision_at,
        consumer_id=consumer_id, provider_authority=provider_authority,
        security_identity=security_identity,
        valuation_authority_document=valuation_authority_document,
        account_fact_authority_document=account_fact_authority_document,
        trusted_commit=trusted_commit,
    )
    if rederived != validated_packet:
        _fail("KIS_OBSERVATION_REDERIVATION_MISMATCH")
    return copy.deepcopy(validated_packet)
