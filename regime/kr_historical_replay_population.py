#!/usr/bin/env python3
"""P1-COM-05 KR 5-axis historical replay population — SHADOW only, NOT NATURAL.

CIO mandate (2026-09-04, "BUILD_KR_5_AXIS_HISTORICAL_REPLAY_POPULATION"): for a
caller-supplied list of historical KR trading dates, reconstruct the same
TREND/BREADTH/RISK_VOL/LIQUIDITY/LEADERSHIP five-axis official-KRX observation
that ``.github/scripts/korea_market_signals.py`` produces for "today", plus the
existing candidate normalization result from ``regime/paper_regime_reference.py``,
so the KR market can be replayed on already-designated historical trading days.

This module invents nothing new:

* Session fetch / pairing / packet construction reuse
  ``.github/scripts/korea_market_signals.py`` unmodified — ``load_contract``,
  ``discover_session_pair``, ``build_packet`` are imported and applied exactly
  as written today.  This file never reimplements KRX request/parse/aggregate
  semantics.
* Candidate normalization reuses ``regime.paper_regime_reference.build_kr``
  unmodified — no new threshold, scoring, or Regime policy is defined here.

Historical replay evidence != NATURAL evidence:

* Input dates are exactly and only what the caller supplies via ``--date``.
  This module never selects bull/bear/sideways/stress episodes on its own —
  regime-episode selection is a separate CIO policy gate.
* Every record in the population is tagged
  ``evidence_class = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"`` and every
  authority flag in the population stays ``false`` except the one read-only
  "this is shadow historical-replay evidence" marker.
* This module refuses to write its output anywhere inside this repository
  checkout — not the NATURAL ``data/observations/`` path, not any other
  tracked path.  The only accepted destinations are an external ``--out``
  path outside the checkout, or (when ``--out`` is omitted) a private
  system-temp file whose path is printed and never committed.

Point-in-time integrity is structural, not merely asserted: each requested
date is resolved and replayed independently via
``korea_market_signals.discover_session_pair(anchor=<that date>)``, which only
ever walks *backward* in calendar time from the requested date, so no session
after the requested date can ever be used and no other requested date's
outcome can influence this one.  A date this module cannot safely resolve
(malformed input, KRX source missing/incomplete, one axis unresolvable, or an
unrecognized retained/response shape) is recorded as one ``BLOCKED`` entry —
by an attributable code only, never by a leaked raw message — and never
aborts the rest of the population.

Provenance is part of the observation, not decoration — and the exact strength
of that binding is stated rather than implied.  Every ``OBSERVED`` record
carries the official KRX request lineage and the producer's packet digest, and
``validate_population`` re-binds them by reassembling that packet and handing it
to ``korea_market_signals.validate_packet``.  That rejects a record whose
provenance was deleted, whose declared endpoint or source identity is not the
pinned contract's, or whose hashes were edited without also re-deriving every
digest above them.

It is *not* proof of which bytes KRX actually returned.  ``payload_sha256`` is
an unkeyed digest over the packet's own mutable fields, so a payload that edits
a response hash and then recomputes the packet digest and the population digest
is internally consistent and **is accepted** — that limit is pinned by an
explicit regression rather than left to a reader's optimism.  Anchoring it
would require retaining the raw KRX responses or a provider signature to
compare against; this contract retains neither
(``raw_persistence``/``per_symbol_persistence`` are contract facts, not stored
bytes), and introducing one is a separate data decision.  What is proven here
is therefore internal consistency plus conformance to the pinned contract, and
that is what the field names and codes are meant to say.  Only a ``BLOCKED``
record — which has no evidence — may carry null provenance.
"""

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
import sys
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import paper_regime_reference as PRR  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The daily collector lives under `.github/scripts/`, which is not an
# importable Python package — this dynamic load is the same technique
# market_judgement/krx_market_judgement.py and
# regime/replay_population_readiness.py already use to reuse it unmodified.
KMS = _load_module(
    "atlas_kr_historical_replay_korea_market_signals",
    ROOT / ".github" / "scripts" / "korea_market_signals.py",
)


SCHEMA_VERSION = "regime_kr_historical_replay_population/v1"
MODE = "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL"
EVIDENCE_CLASS = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"
CANDIDATE_POLICY_PATH = "config/paper_regime_reference_policy_v1.json"
SOURCE_CONTRACT_PATH = "config/korea_market_signals_contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE8 = re.compile(r"^\d{8}$")

RECORD_STATUSES = ("OBSERVED", "BLOCKED")

# The exact provenance an OBSERVED record must carry, and the exact per-request
# lineage ``korea_market_signals._source_lineage`` emits. Both are required key
# for key by ``validate_population``: a re-hashed payload that *deletes* an
# official-source hash must fail rather than pass by having nothing left to
# check.
SOURCE_HASH_KEYS = ("packet_payload_sha256", "requests")
REQUEST_FAMILIES = ("index", "stock")
REQUEST_LINEAGE_KEYS = (
    "current_fetched_at_utc",
    "current_response_sha256",
    "endpoint",
    "previous_fetched_at_utc",
    "previous_response_sha256",
)
RECORD_SOURCE_KEYS = ("contract_version", "source_name", "source_tier")

# The exact authority boundary of this population, declared once and required
# key-for-key by ``validate_population``. A payload that drops a flag must not
# pass merely because the flag it dropped is no longer there to be checked.
AUTHORITY_GRANTED_KEY = "historical_replay_evidence_authorized"
AUTHORITY = {
    "historical_replay_evidence_authorized": True,
    "natural_promotion_authorized": False,
    "sensor_normalization_ratification_authorized": False,
    "registry_promotion_authorized": False,
    "ttl_ratification_authorized": False,
    "pit_replay_acceptance_authorized": False,
    "runtime_regime_wiring_authorized": False,
    "strategy_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authorized": False,
}


class ReplayPopulationError(ValueError):
    """A requested historical KR replay population cannot be safely built."""


def fail(code: str, detail: str = "") -> None:
    raise ReplayPopulationError(f"{code}:{detail}" if detail else code)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReplayPopulationError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ReplayPopulationError(f"SOURCE_MISSING:{path}") from exc


def _load_candidate_policy() -> dict:
    policy = PRR.read_json(PRR.POLICY_PATH, "POLICY_INVALID")
    if policy.get("contract_version") != "paper_regime_reference_policy/v1":
        fail("POLICY_INVALID", "contract_version")
    return policy


def _iso_date(value: object) -> str | None:
    """Normalize a KRX ``YYYYMMDD`` or ISO ``YYYY-MM-DD`` *shape*, else ``None``.

    ``None`` for anything else on purpose: a malformed requested date is a
    legitimate ``BLOCKED`` record, not something to guess a calendar value for.

    Shape only. ``2026-02-31`` normalizes here and is not a day; use
    ``_calendar_date`` before comparing or storing a date.
    """
    if not isinstance(value, str):
        return None
    if KMS.DATE10.fullmatch(value) is not None:
        return value
    if DATE8.fullmatch(value) is not None:
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def _calendar_date(value: object) -> dt.date | None:
    """The real calendar date ``value`` denotes, or ``None`` if it denotes none.

    Date *shape* is not a date. ``2026-02-31`` and its KRX form ``20260231``
    both satisfy ``_iso_date`` and neither is a day; because ISO dates compare
    lexicographically, ``2026-02-31`` also sorts before ``2026-03-01`` and so
    reads as backward-looking against every later anchor. Every session date
    this module compares is therefore parsed before it is compared.
    """
    normalized = _iso_date(value)
    if normalized is None:
        return None
    try:
        return dt.date.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_requested_date(value: str) -> dt.date:
    if not isinstance(value, str) or KMS.DATE10.fullmatch(value) is None:
        fail("REQUESTED_DATE_FORMAT_INVALID")
    parsed = _calendar_date(value)
    if parsed is None:
        fail("REQUESTED_DATE_CALENDAR_INVALID")
    return parsed


def _blocked_record(requested_date: str, failure_reason: str) -> dict:
    return {
        "requested_date": requested_date,
        "status": "BLOCKED",
        "evidence_class": EVIDENCE_CLASS,
        "effective_trading_date": None,
        "previous_trading_date": None,
        "source": None,
        "five_axis": None,
        "candidate_normalized_result": None,
        "source_hashes": None,
        "failure_reason": failure_reason,
        "no_lookahead_attestation": {
            "anchor_requested_date": requested_date,
            "session_dates_used": [],
            "any_session_date_after_requested_date": False,
            "other_requested_dates_consulted": False,
        },
    }


def _observed_record(
    requested_date: str, previous: dict, current: dict, packet: dict, normalized: dict, contract: dict,
) -> dict:
    return {
        "requested_date": requested_date,
        "status": "OBSERVED",
        "evidence_class": EVIDENCE_CLASS,
        "effective_trading_date": packet["as_of_date"],
        "previous_trading_date": packet["previous_date"],
        "source": {
            "contract_version": contract["contract_version"],
            "source_name": contract["source_name"],
            "source_tier": contract["source_tier"],
        },
        "five_axis": {
            "status": packet["status"],
            "coverage": copy.deepcopy(packet["coverage"]),
            "axes": copy.deepcopy(packet["axes"]),
        },
        "candidate_normalized_result": copy.deepcopy(normalized),
        "source_hashes": {
            "packet_payload_sha256": packet["payload_sha256"],
            "requests": copy.deepcopy(packet["source"]["requests"]),
        },
        "failure_reason": None,
        "no_lookahead_attestation": {
            "anchor_requested_date": requested_date,
            "session_dates_used": [previous["date"], current["date"]],
            "any_session_date_after_requested_date": False,
            "other_requested_dates_consulted": False,
        },
    }


def _replay_one_requested_date(
    auth_key: str, requested_date: str, *, opener, contract: dict, policy: dict,
) -> dict:
    """Resolve and replay exactly one caller-supplied historical date.

    Takes only this one requested date plus the on-disk contract/candidate
    policy; ``discover_session_pair`` only ever walks backward from the
    anchor, so this call is structurally incapable of consulting a session
    after ``requested_date`` or the outcome of any other requested date.
    """
    try:
        anchor = _parse_requested_date(requested_date)
        previous, current = KMS.discover_session_pair(
            auth_key, anchor=anchor, opener=opener, contract=contract,
        )
        # Both session dates are parsed as calendar dates, never merely
        # shape-matched: a KRX response stamped ``20260231`` names no day, and
        # comparing that string against the anchor would let it through as an
        # ordinary earlier session.
        current_date = _calendar_date(current.get("date"))
        previous_date = _calendar_date(previous.get("date"))
        if current_date is None or previous_date is None:
            fail("REPLAY_SESSION_DATE_CALENDAR_INVALID")
        if previous_date >= current_date or current_date > anchor:
            fail("REPLAY_LOOKAHEAD_VIOLATION")
        packet = KMS.build_packet(previous, current, contract)
        normalized = PRR.build_kr(packet, policy)
    except (KMS.KoreaMarketSignalsError, PRR.PaperRegimeReferenceError, ReplayPopulationError) as exc:
        return _blocked_record(requested_date, str(exc))
    except Exception as exc:  # noqa: BLE001 — deliberate per-date containment,
        # mirrors regime/normalization_replay_readiness.py: one date with an
        # unrecognized shape degrades to "this date is not replayable"
        # evidence instead of aborting the whole population. Only the
        # exception *type* is recorded, never its message.
        return _blocked_record(requested_date, f"UNSUPPORTED_REPLAY_SHAPE_{type(exc).__name__}")
    return _observed_record(requested_date, previous, current, packet, normalized, contract)


def build_population(
    auth_key: str, requested_dates: list[str], *, opener=urlopen,
) -> dict:
    contract = KMS.load_contract()
    policy = _load_candidate_policy()
    # Deterministic regardless of caller ordering/duplication: sort the
    # distinct requested strings so a shuffled --date list reproduces the
    # exact same record order every time.
    unique_dates = sorted({str(value) for value in requested_dates})
    if not unique_dates:
        fail("NO_DATES_REQUESTED")
    records = [
        _replay_one_requested_date(auth_key, date, opener=opener, contract=contract, policy=policy)
        for date in unique_dates
    ]
    population = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "wbs": "P1-COM-05",
        "evidence_class": EVIDENCE_CLASS,
        "requested_dates": unique_dates,
        "source_contract": {
            "path": "config/korea_market_signals_contract.json",
            "sha256": file_sha256(KMS.CONTRACT_PATH),
            "contract_version": contract["contract_version"],
        },
        "candidate_policy": {
            "path": CANDIDATE_POLICY_PATH,
            "sha256": file_sha256(PRR.POLICY_PATH),
            "status": policy.get("status"),
        },
        "candidate_rule_source": "regime/paper_regime_reference.py::build_kr",
        "records": records,
        "authority": dict(AUTHORITY),
    }
    population["payload_sha256"] = payload_sha256(population)
    return population


def validate_population(value: dict) -> dict:
    """Integrity check — never re-fetches KRX, always re-derives normalization.

    Re-fetching would require re-issuing live KRX requests for every replayed
    date. Per the CIO mandate, an actual KRX network probe must stay separate
    from implementation verification and must never become a CI prerequisite,
    so ``--verify`` never touches the network. What it *can* do offline, and
    now does, is re-run the unmodified candidate rule over the five-axis
    evidence each record already carries: a stored ``candidate_regime`` or axis
    direction that the stored evidence does not actually produce is a forgery,
    not an observation, and checking the field only for presence would accept
    it under a freshly recomputed payload hash.

    "Shape" is deliberately exact rather than "whatever happens to be present":
    a re-hashed payload is a valid signature over whatever it contains, so a
    check that only inspects the keys it finds would accept a population that
    silently dropped its records or its explicit authority boundary. Every
    requested date must therefore map to exactly one record, in order, and the
    authority block must match ``AUTHORITY`` key for key.

    Re-derived normalization is still only half of an observation. It proves the
    stored state follows from the stored measurement, but says nothing about
    *which official KRX responses that measurement came from* — so an OBSERVED
    record's ``source_hashes`` is separately required, and re-bound by rebuilding
    the producer's own packet rather than merely inspected. That digest is
    unkeyed and covers only fields this payload carries, so the result is
    internal consistency plus pinned-contract conformance, not attribution to
    bytes KRX served; ``_validate_source_provenance`` states the boundary.
    """
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        fail("POPULATION_SCHEMA_INVALID")
    unsigned = copy.deepcopy(value)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or SHA256.fullmatch(claimed) is None or payload_sha256(unsigned) != claimed:
        fail("POPULATION_SHA_INVALID")
    if value.get("mode") != MODE or value.get("evidence_class") != EVIDENCE_CLASS:
        fail("POPULATION_MODE_INVALID")
    requested = value.get("requested_dates")
    if (
        not isinstance(requested, list)
        or not requested
        or any(not isinstance(date, str) for date in requested)
        or requested != sorted(set(requested))
    ):
        fail("POPULATION_DATE_ORDER_INVALID")
    _validate_records(
        value, requested, _revalidation_policy(value), _revalidation_contract(value),
    )
    _validate_authority(value)
    return copy.deepcopy(value)


def _revalidation_policy(value: dict) -> dict:
    """The candidate policy this population pinned, re-read for re-derivation.

    Recomputing a record's normalization is only meaningful against the *same*
    policy the population was built with, so the pinned sha256 is compared with
    the on-disk file rather than assumed. A checkout carrying a different
    candidate policy fails closed here with an attributable code instead of
    reporting a normalization mismatch the payload did not cause.
    """
    pinned = value.get("candidate_policy")
    if not isinstance(pinned, dict) or pinned.get("path") != CANDIDATE_POLICY_PATH:
        fail("POPULATION_CANDIDATE_POLICY_INVALID", "path")
    if pinned.get("sha256") != file_sha256(PRR.POLICY_PATH):
        fail("CANDIDATE_POLICY_SHA_MISMATCH", CANDIDATE_POLICY_PATH)
    policy = _load_candidate_policy()
    if pinned.get("status") != policy.get("status"):
        fail("POPULATION_CANDIDATE_POLICY_INVALID", "status")
    return policy


def _revalidation_contract(value: dict) -> dict:
    """The KRX source contract this population pinned, re-read for re-binding.

    An OBSERVED record's provenance is checked by rebuilding the producer's own
    packet, which is only meaningful against the *same* contract the population
    was built with — the packet carries the contract's timezone, persistence
    settings, endpoints, and authority block verbatim. The pinned sha256 is
    therefore compared with the on-disk file rather than assumed, so a checkout
    carrying a different contract fails closed with an attributable code instead
    of reporting a lineage mismatch the payload did not cause.
    """
    pinned = value.get("source_contract")
    if not isinstance(pinned, dict) or pinned.get("path") != SOURCE_CONTRACT_PATH:
        fail("POPULATION_SOURCE_CONTRACT_INVALID", "path")
    if pinned.get("sha256") != file_sha256(KMS.CONTRACT_PATH):
        fail("SOURCE_CONTRACT_SHA_MISMATCH", SOURCE_CONTRACT_PATH)
    try:
        contract = KMS.load_contract()
    except KMS.KoreaMarketSignalsError as exc:
        raise ReplayPopulationError(f"SOURCE_CONTRACT_UNREADABLE:{exc}") from exc
    if pinned.get("contract_version") != contract["contract_version"]:
        fail("POPULATION_SOURCE_CONTRACT_INVALID", "contract_version")
    return contract


def _validate_records(
    value: dict, requested: list[str], policy: dict, contract: dict,
) -> None:
    """Exactly one record per requested date, in the same order — no omissions.

    ``build_population`` emits one record for each sorted, de-duplicated
    requested date, so list equality is the whole bijection: a dropped,
    duplicated, reordered, or invented record all fail here rather than
    producing a population whose coverage counts silently disagree with the
    replay it claims to describe.
    """
    records = value.get("records")
    if not isinstance(records, list) or len(records) != len(requested):
        fail("POPULATION_RECORDS_NOT_BIJECTIVE", "count")
    dates = [
        record.get("requested_date") if isinstance(record, dict) else None
        for record in records
    ]
    if dates != requested:
        fail("POPULATION_RECORDS_NOT_BIJECTIVE", "requested_date")
    for record, requested_date in zip(records, requested):
        _validate_record(record, requested_date, policy, contract)


def _validate_record(
    record: dict, requested_date: str, policy: dict, contract: dict,
) -> None:
    """One record, checked against its own claims rather than trusted.

    A status is not a free label: an ``OBSERVED`` record must carry the
    five-axis packet and candidate normalization an observation is made of, and
    a ``BLOCKED`` one must carry neither plus an attributable reason. Without
    this, a re-hashed payload could keep the status and drop the evidence.

    Carrying the evidence is necessary but not sufficient: the normalization
    must also be *derived from* it, and the evidence must in turn be bound to
    the official KRX responses it was measured from. Both are re-checked below,
    in that order, so a moved measurement is still reported as a normalization
    mismatch rather than as the broken source lineage it also is.
    """
    if not isinstance(record, dict) or record.get("evidence_class") != EVIDENCE_CLASS:
        fail("RECORD_EVIDENCE_CLASS_INVALID")
    status = record.get("status")
    if status not in RECORD_STATUSES:
        fail("RECORD_STATUS_INVALID")
    five_axis = record.get("five_axis")
    candidate = record.get("candidate_normalized_result")
    if status == "OBSERVED":
        if not isinstance(five_axis, dict) or not isinstance(candidate, dict):
            fail("OBSERVED_RECORD_MUST_CARRY_ITS_EVIDENCE", requested_date)
        axes = five_axis.get("axes")
        if not isinstance(axes, dict) or sorted(axes) != sorted(PRR.AXES):
            fail("OBSERVED_RECORD_AXIS_SET_INVALID", requested_date)
        if record.get("failure_reason") is not None:
            fail("OBSERVED_RECORD_MUST_NOT_CARRY_A_FAILURE", requested_date)
        _validate_candidate_is_derived_from_its_evidence(
            record, five_axis, candidate, policy, requested_date,
        )
        _validate_source_provenance(record, five_axis, contract, requested_date)
    else:
        if five_axis is not None or candidate is not None:
            fail("BLOCKED_RECORD_MUST_NOT_CARRY_EVIDENCE", requested_date)
        # Provenance is the evidence's lineage, so a date that produced no
        # evidence must not carry one either — otherwise "null provenance" would
        # be a shape an OBSERVED record could borrow.
        if record.get("source_hashes") is not None or record.get("source") is not None:
            fail("BLOCKED_RECORD_MUST_NOT_CARRY_PROVENANCE", requested_date)
        if not isinstance(record.get("failure_reason"), str) or not record["failure_reason"]:
            fail("BLOCKED_RECORD_MUST_BE_ATTRIBUTED", requested_date)
    _validate_no_lookahead(record, requested_date)


def _rederive_candidate(record: dict, five_axis: dict, policy: dict) -> dict:
    """Re-run the existing candidate rule over this record's *stored* evidence.

    ``PRR.build_kr`` is called unmodified on a packet reassembled from exactly
    what the record already carries — its five-axis packet plus its own
    effective trading date — so no new rule, threshold, or vocabulary enters
    here. The reassembly is the inverse of ``_observed_record``, which is what
    makes the comparison below a derivation rather than a second opinion.
    """
    packet = {
        "status": five_axis.get("status"),
        "coverage": five_axis.get("coverage"),
        "axes": five_axis.get("axes"),
        "as_of_date": record.get("effective_trading_date"),
    }
    return PRR.build_kr(packet, policy)


def _validate_candidate_is_derived_from_its_evidence(
    record: dict, five_axis: dict, candidate: dict, policy: dict, requested_date: str,
) -> None:
    """A stored normalization must be what the stored evidence actually yields.

    Checking ``candidate_normalized_result`` for presence alone would let a
    re-hashed payload keep a genuine five-axis packet and publish any
    ``candidate_regime``, score, confidence, or axis direction beside it — the
    evidence would look intact while the state it supposedly supports was
    forged. Requiring exact equality with the unmodified rule's own output over
    that same evidence closes it, and an axis packet the rule cannot consume at
    all is reported as unnormalizable rather than quietly accepted.
    """
    try:
        expected = _rederive_candidate(record, five_axis, policy)
    except PRR.PaperRegimeReferenceError as exc:
        raise ReplayPopulationError(
            f"OBSERVED_RECORD_EVIDENCE_NOT_NORMALIZABLE:{requested_date}:{exc}"
        ) from exc
    if candidate != expected:
        fail("OBSERVED_RECORD_CANDIDATE_NOT_DERIVED_FROM_ITS_EVIDENCE", requested_date)


def _validate_source_provenance(
    record: dict, five_axis: dict, contract: dict, requested_date: str,
) -> None:
    """An OBSERVED record must carry a KRX lineage consistent with its evidence.

    ``five_axis`` and ``candidate_normalized_result`` describe *what* was
    measured; ``source_hashes`` is the only thing that says *which* official KRX
    responses it was measured from. Checking the packet and the normalization
    alone accepts a record whose provenance was deleted or replaced under a
    freshly recomputed payload hash — the evidence would look intact while
    nothing tied it to a KRX response any more.

    The binding is therefore not a shape check. The producer's own packet is
    reassembled from exactly what this record and the pinned contract carry and
    handed to ``korea_market_signals.validate_packet``, so the stored
    ``packet_payload_sha256`` is only accepted when it is the digest that
    producer computes over these axes, these session dates, and these request
    hashes, against the pinned contract's endpoints, identity, and authority.
    Removing a response hash, editing one, or moving an axis without re-deriving
    every digest above it all fail here.

    What this cannot prove, stated plainly so the code and the claim agree:
    ``payload_sha256`` is unkeyed and is computed over the same mutable fields
    being checked, so a *coordinated* edit — change a response hash, recompute
    ``packet_payload_sha256``, recompute the population digest — satisfies every
    check in this function and is accepted. This is consistency and contract
    conformance, not attribution to bytes KRX served; the retained evidence
    contains no immutable anchor (raw responses, or a KRX signature over them)
    that could distinguish the two, and adding one is a data decision outside
    this replay module. ``test_kr_historical_replay_population.py`` pins both
    sides of that boundary so it cannot silently widen into an over-claim.
    """
    hashes = record.get("source_hashes")
    if not isinstance(hashes, dict) or sorted(hashes) != sorted(SOURCE_HASH_KEYS):
        fail("OBSERVED_RECORD_MUST_CARRY_ITS_SOURCE_HASHES", requested_date)
    digest = hashes["packet_payload_sha256"]
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        fail("OBSERVED_RECORD_SOURCE_HASH_SYNTAX_INVALID", requested_date)
    requests = _validated_request_lineage(hashes["requests"], contract, requested_date)
    _validate_record_source_identity(record, contract, requested_date)
    packet = _reassemble_packet(record, five_axis, requests, digest, contract)
    try:
        KMS.validate_packet(packet, contract)
    except (KMS.KoreaMarketSignalsError, AttributeError, KeyError, TypeError) as exc:
        # A record the producer's own validator cannot consume is not a valid
        # lineage, whichever way it is malformed. Only the attributable code (or
        # exception type) travels, never a raw message.
        detail = exc if isinstance(exc, KMS.KoreaMarketSignalsError) else type(exc).__name__
        raise ReplayPopulationError(
            f"OBSERVED_RECORD_PACKET_LINEAGE_INVALID:{requested_date}:{detail}"
        ) from exc


def _validated_request_lineage(requests: object, contract: dict, requested_date: str) -> dict:
    """Exactly the per-request lineage the producer emits — every family/market.

    Exact rather than "whatever happens to be present": a payload that dropped
    one market's response hashes would otherwise leave that market's contribution
    to the packet unattributed, and the endpoint is compared with the pinned
    contract so a request cannot claim to come from an unofficial source.
    """
    if not isinstance(requests, dict) or sorted(requests) != sorted(REQUEST_FAMILIES):
        fail("OBSERVED_RECORD_REQUEST_LINEAGE_INVALID", f"{requested_date}:families")
    for family in REQUEST_FAMILIES:
        markets = requests[family]
        if not isinstance(markets, dict) or sorted(markets) != sorted(
            market.upper() for market in KMS.MARKETS
        ):
            fail("OBSERVED_RECORD_REQUEST_LINEAGE_INVALID", f"{requested_date}:{family}")
        for market in KMS.MARKETS:
            row = markets[market.upper()]
            label = f"{requested_date}:{family}.{market}"
            if not isinstance(row, dict) or sorted(row) != sorted(REQUEST_LINEAGE_KEYS):
                fail("OBSERVED_RECORD_REQUEST_LINEAGE_INVALID", label)
            if row["endpoint"] != contract[f"{family}_endpoints"][market]:
                fail("OBSERVED_RECORD_REQUEST_ENDPOINT_INVALID", label)
            for key in ("previous_response_sha256", "current_response_sha256"):
                if not isinstance(row[key], str) or SHA256.fullmatch(row[key]) is None:
                    fail("OBSERVED_RECORD_SOURCE_HASH_SYNTAX_INVALID", f"{label}.{key}")
            for key in ("previous_fetched_at_utc", "current_fetched_at_utc"):
                if not isinstance(row[key], str) or KMS.UTC_SECOND.fullmatch(row[key]) is None:
                    fail("OBSERVED_RECORD_REQUEST_TIMESTAMP_INVALID", f"{label}.{key}")
    return requests


def _validate_record_source_identity(record: dict, contract: dict, requested_date: str) -> None:
    """The record's declared source must be the pinned contract's own identity."""
    source = record.get("source")
    if not isinstance(source, dict) or sorted(source) != sorted(RECORD_SOURCE_KEYS):
        fail("OBSERVED_RECORD_SOURCE_IDENTITY_INVALID", requested_date)
    if (
        source["contract_version"] != contract["contract_version"]
        or source["source_name"] != contract["source_name"]
        or source["source_tier"] != contract["source_tier"]
    ):
        fail("OBSERVED_RECORD_SOURCE_IDENTITY_INVALID", requested_date)


def _reassemble_packet(
    record: dict, five_axis: dict, requests: dict, digest: str, contract: dict,
) -> dict:
    """The producer's own packet, rebuilt from exactly what this record stores.

    The inverse of ``_observed_record``: every field comes from the record or
    from the pinned contract, and ``generated_at``/``available_at`` are recovered
    the way ``korea_market_signals.build_packet`` computes them — the latest
    fetch timestamp across all of the requests validated above. That recovery is
    what makes the stored digest checkable offline instead of merely present.
    """
    fetched = [
        requests[family][market.upper()][key]
        for family in REQUEST_FAMILIES
        for market in KMS.MARKETS
        for key in ("previous_fetched_at_utc", "current_fetched_at_utc")
    ]
    generated_at = max(fetched)
    source = record["source"]
    return {
        "schema_version": KMS.SCHEMA_VERSION,
        "contract_version": source["contract_version"],
        "status": five_axis.get("status"),
        "market": "KOREA",
        "market_timezone": contract["market_timezone"],
        "previous_date": record.get("previous_trading_date"),
        "as_of_date": record.get("effective_trading_date"),
        "generated_at": generated_at,
        "available_at": generated_at,
        "source": {
            "name": source["source_name"],
            "tier": source["source_tier"],
            "raw_persistence": contract["raw_persistence"],
            "per_symbol_persistence": contract["per_symbol_persistence"],
            "requests": copy.deepcopy(requests),
        },
        "axes": copy.deepcopy(five_axis.get("axes")),
        "coverage": copy.deepcopy(five_axis.get("coverage")),
        "authority": copy.deepcopy(contract["authority"]),
        "payload_sha256": digest,
    }


def _validate_no_lookahead(record: dict, requested_date: str) -> None:
    """Re-check, never trust, that this record only ever looked backward.

    ``no_lookahead_attestation`` is a *claim*; the session dates it names are
    the evidence. Both are compared against the requested date here, so a
    payload cannot assert "no lookahead" over a session it could not have seen.

    Each date is parsed as a calendar date before it is compared. Shape is not a
    calendar: ``20260231`` is date-shaped, is no day, and sorts before every
    later anchor, so the string comparison this replaced cleared it as an
    ordinary earlier session.
    """
    attestation = record.get("no_lookahead_attestation")
    if not isinstance(attestation, dict):
        fail("RECORD_ATTESTATION_MISSING", requested_date)
    if attestation.get("anchor_requested_date") != requested_date:
        fail("RECORD_ATTESTATION_ANCHOR_INVALID", requested_date)
    if (
        attestation.get("any_session_date_after_requested_date") is not False
        or attestation.get("other_requested_dates_consulted") is not False
    ):
        fail("RECORD_ATTESTATION_CLAIM_INVALID", requested_date)
    sessions = attestation.get("session_dates_used")
    if not isinstance(sessions, list):
        fail("RECORD_ATTESTATION_SESSIONS_INVALID", requested_date)
    anchor = _calendar_date(requested_date)
    if anchor is None:
        # A malformed or calendar-impossible requested date is itself a
        # legitimate BLOCKED record — ``_parse_requested_date`` produces exactly
        # that — so there is no anchor to compare against and no claim is made.
        return
    for value in list(sessions) + [
        record.get("effective_trading_date"), record.get("previous_trading_date"),
    ]:
        if _iso_date(value) is None:
            # Not date-shaped at all: nothing is claimed about it here, exactly
            # as before.
            continue
        # Date-shaped but not a real day cannot be compared, and must not be
        # cleared by the string ordering that would place 2026-02-31 before
        # 2026-03-01. It fails this record closed instead.
        consulted = _calendar_date(value)
        if consulted is None:
            fail("RECORD_SESSION_DATE_CALENDAR_INVALID", requested_date)
        if consulted > anchor:
            fail("RECORD_LOOKAHEAD_VIOLATION", requested_date)


def _validate_authority(value: dict) -> None:
    """The authority block must be present, complete, and exactly as declared."""
    authority = value.get("authority")
    if not isinstance(authority, dict) or sorted(authority) != sorted(AUTHORITY):
        fail("POPULATION_AUTHORITY_SCHEMA_INVALID")
    for key, allowed in AUTHORITY.items():
        if authority[key] is not allowed:
            fail("POPULATION_AUTHORITY_INVALID", key)


def _forbid_tracked_output(root: Path, path: Path) -> None:
    """Fail closed if ``path`` resolves inside this repository checkout.

    Historical replay evidence must never land in any tracked location —
    NATURAL ``data/observations/`` included — so the guard is a blanket
    "not inside the checkout at all", not a NATURAL-path denylist that a new
    tracked directory could slip past.
    """
    root_resolved = Path(root).resolve()
    path_resolved = Path(path).resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError:
        return
    fail("TRACKED_OUTPUT_FORBIDDEN", str(path_resolved))


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_population(population: dict, out_path: Path, *, root: Path = ROOT) -> Path:
    _forbid_tracked_output(root, out_path)
    text = json.dumps(population, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(Path(out_path), text)
    return Path(out_path)


def _default_temp_out() -> Path:
    fd, name = tempfile.mkstemp(prefix="kr_historical_replay_population.", suffix=".json")
    os.close(fd)
    return Path(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", action="append", default=[], dest="dates",
        help="Historical KR trading date, YYYY-MM-DD. Repeatable. No date is ever selected automatically.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="External output path (must be outside this checkout). Defaults to a private system-temp file.",
    )
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()

    if args.verify:
        value = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        validate_population(value)
        print(f"PASS_KR_HISTORICAL_REPLAY_POPULATION_VERIFIED:{value['payload_sha256']}")
        return 0

    if not args.dates:
        fail("NO_DATES_REQUESTED")

    population = build_population(os.environ.get("KRX_API_KEY", ""), args.dates)
    out_path = args.out if args.out is not None else _default_temp_out()
    write_population(population, out_path)
    observed = sum(1 for r in population["records"] if r["status"] == "OBSERVED")
    print(json.dumps(
        {
            "out": str(out_path),
            "payload_sha256": population["payload_sha256"],
            "records": len(population["records"]),
            "observed": observed,
            "blocked": len(population["records"]) - observed,
        },
        ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
