#!/usr/bin/env python3
"""P5-08 Crypto Candidate Promotion Rule.

Per-market state machine, continuing P3-12's own state machine one step
further, over already-produced evidence packets only:

    TRADEABLE_UNIVERSE / PAPER_ELIGIBLE (P3-12)
        -> WATCH            (one or more criteria UNKNOWN, none FAILED)
        -> FOCUSED_REVIEW    (every criterion PASSED)
        -> BLOCKED           (one or more criteria FAILED)

This module never captures anything itself. It is a pure derivation over
already-built, already-validated evidence packets from four upstream,
independently-ratified-or-honestly-unratified sources:

* ``universe/upbit_tradeable_universe.py`` (P3-12)             -- identity,
  tradability, and the Upbit ``market_event.caution`` flag.
* ``regime/output_contract.py`` (P1-COM-01, bound live by P1-CR-08)  -- the
  Regime aggregate for market="CRYPTO".
* ``microstructure/upbit_market_evidence.py`` (P4-07)          -- finalized
  1d/4h candles, orderbook + trades. The packet is retained as evidence,
  but it cannot satisfy TREND or VOLUME_LIQUIDITY while its policy is
  unratified and no ratified candidate-level transform exists.
* ``.github/scripts/crypto_leadership.py`` (P1-CR-07)          -- the
  ratified BTC-reference relative-strength measurement.

Every output row's ``authority`` block is hardcoded all-``false``: a
``FOCUSED_REVIEW`` classification is a review-queue label, never an
investable/PAPER/Stage/order grant. Turning this into real authority is a
separate, later, explicitly-ratified change this module cannot make.

Scope boundary vs P5-09 (Crypto PAPER Buy Eligibility, next WBS item): this
module stops at FOCUSED_REVIEW/WATCH/BLOCKED classification with reasons. It
never computes entry zone, invalidation price, planned stop, PAPER quantity,
fee/slippage assumptions, planned loss vs. Crypto risk headroom, expiry/next
review time, a duplicate-guard key, or "PAPER_READY" readiness -- those
fields are explicitly P5-09's job per the Notion policy doc's own
``Focused Review -> PAPER_READY`` state-machine step.

--------------------------------------------------------------------------
Per-criterion evidence basis (see docs/crypto_candidate_promotion_contract.md
for the full table -- this is the short version):

  IDENTITY           ratified/deterministic -- reused P3-12 gate.
  TRADABILITY        ratified/deterministic -- reused P3-12 gate.
  REGIME             UNKNOWN by construction -- P1-CR-08's own boundary:
                      regime/output_contract.py authorizes only "UNKNOWN" for
                      every market until P1-COM-05 ratifies a minimum
                      coverage gate. No RISK_ON/NEUTRAL/RISK_OFF/STRESS value
                      is ever readable. This directly matches the Notion
                      policy's own text: "Regime가 ... UNKNOWN이면 WATCH만
                      허용" -- a correct literal reading, not a workaround.
  TREND              UNKNOWN by construction -- there is no ratified
                      candidate-level daily/4h trend transform. A two-close
                      comparison would itself invent the missing rule.
  RELATIVE_STRENGTH  FAIL when the ratified BTC leg is non-positive;
                      otherwise UNKNOWN because the required peer-group leg
                      is explicitly UNRATIFIED.
  VOLUME_LIQUIDITY   UNKNOWN by construction -- family presence is coverage,
                      not confirmation, and the relevant P4-07 thresholds are
                      PROPOSED_UNRATIFIED.
  OVEREXTENSION      UNKNOWN by construction -- no mechanical or ratified
                      definition of "과열·급등 추격" exists anywhere in this
                      repository; inventing one is exactly what this module
                      must never do.
  MATERIAL_BLOCKER    FAIL when Upbit caution is active; otherwise UNKNOWN
                      because security/network-outage coverage is missing.

The rows above describe contract/2, which stays the default and
byte-identical (published decision packets are re-derived by a pinned
runtime). Contract/3 is opt-in (``build_promotion_packet(...,
contract_version=3, crypto_runtime_decision=...)``). It changes exactly two
criterion evaluators and the state rule, all bound by hash in ``config/crypto_candidate_promotion_contract_v3.json``:

  REGIME             read from the user-ratified CRYPTO_PAPER_RUNTIME_V1
                      decision (``regime/crypto_paper_runtime.py``) in force
                      at the reference instant and mapped through the
                      PAPER-MARKET-ALLOCATION-V2 new-buy table: RISK_ON and
                      NEUTRAL (selective) PASS, RISK_OFF and STRESS FAIL,
                      UNKNOWN / missing / not-current UNKNOWN (no carry).
                      A KNOWN value never raises.
  VOLUME_LIQUIDITY   read from the RATIFIED P4-07 policy only (hash-bound;
                      the proposal file is never read). Ratified thresholds
                      met on PASS evidence -> PASS; any breach or non-PASS
                      evidence -> UNKNOWN (P4-07 fail_closed_unknown); an
                      absent/invalid ratified policy -> UNKNOWN.

The other six evaluators are unchanged. The contract/3 state rule is
RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1 (user ratification B2, record
bound by hash). Promotion blocks only on the six T2 minimum conditions:
T2_IDENTITY, T2_POPULATION_MEMBERSHIP, T2_LIQUIDITY (ratified Upbit 30-day
average KRW turnover via P3-12), T2_PRICE_DATA (latest completed UTC day),
T2_ROTATION_MEMBERSHIP (not wired yet, so UNKNOWN) and
T2_REGIME_PERMITS_NEW_BUYS. TREND and OVEREXTENSION are record-only
entry-stage features (RULE.ENTRY.PAPER_BASELINE_B.V1). RELATIVE_STRENGTH is a
score, VOLUME_LIQUIDITY is a quality warning, and MATERIAL_BLOCKER is a
warning. All of them are emitted as ``warnings`` and never change the state.
--------------------------------------------------------------------------
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoCandidatePromotionError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CryptoCandidatePromotionError(ValueError):
    """Fail-closed P5-08 candidate-promotion contract violation."""


UPBIT_UNIVERSE = _load("crypto_candidate_promotion_universe", "universe/upbit_tradeable_universe.py")
REGIME_OUTPUT_CONTRACT = _load("crypto_candidate_promotion_regime_output_contract", "regime/output_contract.py")
CRYPTO_LEADERSHIP = _load("crypto_candidate_promotion_leadership", ".github/scripts/crypto_leadership.py")
MARKET_EVIDENCE = _load("crypto_candidate_promotion_market_evidence", "microstructure/upbit_market_evidence.py")

# Loaded only on the opt-in contract/3 path, so the default contract/2 import
# graph (and every byte it produces) is exactly what it was.
_CRYPTO_RUNTIME_MODULE = None


def _crypto_runtime():
    global _CRYPTO_RUNTIME_MODULE
    if _CRYPTO_RUNTIME_MODULE is None:
        _CRYPTO_RUNTIME_MODULE = _load(
            "crypto_candidate_promotion_crypto_paper_runtime", "regime/crypto_paper_runtime.py",
        )
    return _CRYPTO_RUNTIME_MODULE


# Loaded only when a rotation confirmation packet is supplied on contract/3
# (crypto PAPER wiring v2), so every existing import graph is unchanged.
_ROTATION_WIRING_MODULE = None


def _rotation_wiring():
    global _ROTATION_WIRING_MODULE
    if _ROTATION_WIRING_MODULE is None:
        _ROTATION_WIRING_MODULE = _load(
            "crypto_candidate_promotion_rotation_wiring", "rotation/rotation_confirmation_wiring.py",
        )
    return _ROTATION_WIRING_MODULE


CONTRACT_PATH = ROOT / "config" / "crypto_candidate_promotion_contract.json"
OUTPUT_SCHEMA_VERSION = "crypto_candidate_promotion_packet/2"

# Opt-in contract/3 (ratified P4-07 reader + CRYPTO_PAPER_RUNTIME_V1 regime).
CONTRACT_V3_PATH = ROOT / "config" / "crypto_candidate_promotion_contract_v3.json"
CONTRACT_V3_SHA256 = "cc5e372be3f2b9cb2419824c92820ea0a78947a01000cb278f76f2ff75fe91e2"
OUTPUT_SCHEMA_VERSION_V3 = "crypto_candidate_promotion_packet/3"
CONTRACT_VERSIONS = (2, 3)

# PAPER-MARKET-ALLOCATION-V2-20260913 (record sha256 345801ab...) crypto rows,
# verbatim: (criterion status, new_buys_by_market_state,
# per_market_state_multiplier_of_base, UNKNOWN hold-current cap). The v3
# contract file must carry exactly these; the code never follows a changed
# number silently.
ALLOCATION_V2_RATIFICATION_ID = "PAPER-MARKET-ALLOCATION-V2-20260913"
ALLOCATION_V2_RECORD_SHA256 = "345801ab907f75c4761097670430fb097e5e8d3b1e595217850fe20fd240a4c8"
REGIME_GATE_V3 = {
    "RISK_ON": ("PASS", "PERMIT", "1.00", None),
    "NEUTRAL": ("PASS", "PERMIT_SELECTIVE", "0.70", None),
    "RISK_OFF": ("FAIL", "DENY", "0.25", None),
    "STRESS": ("FAIL", "DENY", "0.00", None),
    "UNKNOWN": ("UNKNOWN", "DENY", None, "0.50"),
}
# RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1 (user ratification
# USER_RATIFICATION_PAPER_B2_B3_SIZE_ASSEMBLY_20260915, decision B2): promotion
# blocks only on the six T2 minimum conditions of CANDIDATE-PIPELINE-REBUILD-
# 20260913; every other criterion is a score/warning, and TREND/OVEREXTENSION
# are record-only entry-stage features (RULE.ENTRY.PAPER_BASELINE_B.V1).
T2_RULE_ID = "RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1"
T2_RULE_RECORD_SHA256 = "0e2691e072f4193b6fcd07c14cf2c87be469c4acb9167eca0cbd5d72a390e1c5"
T2_DEFINITION_RECORD_SHA256 = "6870b4572fe46901e9e2ce14e07e01d89c54ba2860ab42a0087f16a4c4703625"
ENTRY_BASELINE_RULE_ID = "RULE.ENTRY.PAPER_BASELINE_B.V1"
ENTRY_BASELINE_RECORD_SHA256 = "b2a905c4eaf23d44749d3e5bcd59b2efe34ff0b0ab5c955a8ce1e0870163154f"
CRYPTO_RUNTIME_RULE_ID = "RULE.CRYPTO.RUNTIME.V1"
ALLOCATION_V2_RULE_ID = "RULE.ALLOCATION.V2"
ROTATION_T2_RULE_ID = "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1"
ROTATION_RECORD_SHA256 = "c6f5dbbe36f3eabc104db9c547ba99d84300fd5b7ef4d801a71c76a071b47116"
RULE_SOURCES_V3 = {
    T2_RULE_ID: T2_RULE_RECORD_SHA256,
    ENTRY_BASELINE_RULE_ID: ENTRY_BASELINE_RECORD_SHA256,
    CRYPTO_RUNTIME_RULE_ID: "e2f9f69461088d52300258ab22f17d7f287bd7c2fd6efd1d49962b44f16fffe1",
    ALLOCATION_V2_RULE_ID: ALLOCATION_V2_RECORD_SHA256,
    ROTATION_T2_RULE_ID: ROTATION_RECORD_SHA256,
}
RULE_VERSIONS_V3 = {ALLOCATION_V2_RULE_ID: 2}
T2_REQUIRED_CONDITIONS = (
    "T2_IDENTITY", "T2_POPULATION_MEMBERSHIP", "T2_LIQUIDITY",
    "T2_PRICE_DATA", "T2_ROTATION_MEMBERSHIP", "T2_REGIME_PERMITS_NEW_BUYS",
)
NON_BLOCKING_CRITERIA_V3 = {
    "TREND": "RECORD_ONLY_ENTRY_STAGE",
    "OVEREXTENSION": "RECORD_ONLY_ENTRY_STAGE",
    "RELATIVE_STRENGTH": "SCORE",
    "VOLUME_LIQUIDITY": "QUALITY_WARNING",
    "MATERIAL_BLOCKER": "WARNING",
}
ROTATION_NOT_WIRED_REASON = "CRYPTO_ROTATION_CONFIRMATION_NOT_WIRED"

CRYPTO_RUNTIME_DECISION_KEYS = frozenset({
    "schema_version", "market", "evaluation_at", "code_revision", "policy_identity",
    "policy_sha256", "scope", "evidence_class", "current_decision_date", "decision_at",
    "decision_status", "paper_regime", "runtime_regime", "direction", "confidence",
    "runtime_decision_available", "acceptance", "current_observation", "chain",
    "aggregation", "reasons", "caveats", "authority", "decision_id",
})

STATE_WATCH = "WATCH"
STATE_FOCUSED_REVIEW = "FOCUSED_REVIEW"
STATE_BLOCKED = "BLOCKED"
PROMOTION_STATES = (STATE_WATCH, STATE_FOCUSED_REVIEW, STATE_BLOCKED)

CRITERIA = (
    "IDENTITY", "TRADABILITY", "REGIME", "TREND",
    "RELATIVE_STRENGTH", "VOLUME_LIQUIDITY", "OVEREXTENSION", "MATERIAL_BLOCKER",
)
CRITERION_STATUSES = ("PASS", "FAIL", "UNKNOWN")

# The ratified PRIMARY window from config/crypto_leadership_policy.json.
# Hardcoded, not re-derived: if that policy's window_id ever changes, this
# module must be updated deliberately, not silently follow along.
LEADERSHIP_PRIMARY_WINDOW_ID = "primary_30d"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# Hardcoded, never policy-driven: no evaluator in this module may set any of
# these to true. Turning a FOCUSED_REVIEW classification into real
# investable/PAPER/Stage/order authority is a separate, later,
# explicitly-ratified change (P5-09's job at the earliest, not this one's).
_ROW_AUTHORITY = {
    "investable_eligible": False,
    "paper_eligible": False,
    "focused_review_authorized": False,
    "entry_authorized": False,
    "stage_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "order_authorized": False,
}


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoCandidatePromotionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or value.get("contract_version") != "crypto_candidate_promotion_contract/2"
    ):
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:contract_version")
    if tuple(value.get("criteria", [])) != CRITERIA:
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:criteria")
    if tuple(value.get("criterion_statuses", [])) != CRITERION_STATUSES:
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:criterion_statuses")
    if tuple(value.get("promotion_states", [])) != PROMOTION_STATES:
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:promotion_states")
    for key, expected in value.get("authority", {}).items():
        if expected is not False:
            raise CryptoCandidatePromotionError(f"CONTRACT_AUTHORITY_NOT_FALSE:{key}")
    if set(value.get("authority", {})) != set(_ROW_AUTHORITY):
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:authority_keys")
    return copy.deepcopy(value)


def load_contract_v3(path: Path = CONTRACT_V3_PATH) -> dict:
    """Load the hash-pinned opt-in contract/3 and prove every binding it names."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CryptoCandidatePromotionError(f"CONTRACT_V3_READ_FAILED:{exc}") from exc
    if hashlib.sha256(raw).hexdigest() != CONTRACT_V3_SHA256:
        raise CryptoCandidatePromotionError("CONTRACT_V3_HASH_MISMATCH")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoCandidatePromotionError(f"CONTRACT_V3_JSON_INVALID:{exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 3
        or value.get("contract_version") != "crypto_candidate_promotion_contract/3"
    ):
        raise CryptoCandidatePromotionError("CONTRACT_V3_FIELD_MISMATCH:contract_version")
    if tuple(value.get("criteria", [])) != CRITERIA:
        raise CryptoCandidatePromotionError("CONTRACT_V3_FIELD_MISMATCH:criteria")
    if tuple(value.get("criterion_statuses", [])) != CRITERION_STATUSES:
        raise CryptoCandidatePromotionError("CONTRACT_V3_FIELD_MISMATCH:criterion_statuses")
    if tuple(value.get("promotion_states", [])) != PROMOTION_STATES:
        raise CryptoCandidatePromotionError("CONTRACT_V3_FIELD_MISMATCH:promotion_states")
    authority = value.get("authority", {})
    if set(authority) != set(_ROW_AUTHORITY) or any(item is not False for item in authority.values()):
        raise CryptoCandidatePromotionError("CONTRACT_V3_AUTHORITY_NOT_FALSE")
    gate = value.get("regime_gate") or {}
    expected_states = {
        state: {
            "criterion_status": status,
            "new_buys": new_buys,
            "state_multiplier_of_base": multiplier,
            "hold_current_max_multiplier_of_base": hold_cap,
        }
        for state, (status, new_buys, multiplier, hold_cap) in REGIME_GATE_V3.items()
    }
    if (
        gate.get("market") != "CRYPTO"
        or gate.get("source_ratification_id") != ALLOCATION_V2_RATIFICATION_ID
        or gate.get("source_record_sha256") != ALLOCATION_V2_RECORD_SHA256
        or gate.get("states") != expected_states
    ):
        raise CryptoCandidatePromotionError("CONTRACT_V3_REGIME_GATE_NOT_RATIFIED")
    runtime = _crypto_runtime()
    source = value.get("regime_source") or {}
    if (
        source.get("decision_schema_version") != runtime.SCHEMA_VERSION
        or source.get("policy_sha256") != runtime.POLICY_SHA256
        or source.get("ratification_identity") != runtime.RATIFICATION_IDENTITY
        or source.get("ratification_record_sha256") != runtime.RATIFICATION_RECORD_SHA256
    ):
        raise CryptoCandidatePromotionError("CONTRACT_V3_REGIME_SOURCE_BINDING_MISMATCH")
    evidence_policy = value.get("market_evidence_policy") or {}
    try:
        evidence_contract = MARKET_EVIDENCE.load_contract()
    except MARKET_EVIDENCE.MarketEvidenceError as exc:
        raise CryptoCandidatePromotionError(f"CONTRACT_V3_MARKET_EVIDENCE_CONTRACT_INVALID:{exc}") from exc
    if (
        evidence_policy.get("path") != "config/upbit_market_evidence_policy_ratified.json"
        or evidence_policy.get("proposal_policy_read") is not False
        or evidence_policy.get("policy_id") != evidence_contract.get("ratified_policy_id")
        or evidence_policy.get("policy_version") != evidence_contract.get("ratified_policy_version")
        or evidence_policy.get("packet_sha256") != evidence_contract.get("ratified_policy_sha256")
    ):
        raise CryptoCandidatePromotionError("CONTRACT_V3_MARKET_EVIDENCE_POLICY_BINDING_MISMATCH")
    rule = value.get("promotion_rule") or {}
    records = (
        (rule.get("source_record") or {}, T2_RULE_RECORD_SHA256),
        (rule.get("t2_definition_record") or {}, T2_DEFINITION_RECORD_SHA256),
        (rule.get("entry_baseline_record") or {}, ENTRY_BASELINE_RECORD_SHA256),
    )
    if (
        rule.get("rule_id") != T2_RULE_ID
        or rule.get("version") != 1
        or tuple(rule.get("blocking_conditions") or ()) != T2_REQUIRED_CONDITIONS
        or rule.get("non_blocking_criteria") != NON_BLOCKING_CRITERIA_V3
        or value.get("rule_sources") != RULE_SOURCES_V3
        or any(record.get("sha256") != expected for record, expected in records)
    ):
        raise CryptoCandidatePromotionError("CONTRACT_V3_PROMOTION_RULE_NOT_RATIFIED")
    loaded = []
    for record, expected in records:
        repo_path = record.get("repo_path")
        if not isinstance(repo_path, str) or not repo_path.startswith("evidence/authority/"):
            raise CryptoCandidatePromotionError("CONTRACT_V3_RATIFICATION_RECORD_PATH_INVALID")
        try:
            record_raw = (ROOT / repo_path).read_bytes()
        except OSError as exc:
            raise CryptoCandidatePromotionError(f"CONTRACT_V3_RATIFICATION_RECORD_MISSING:{repo_path}") from exc
        if hashlib.sha256(record_raw).hexdigest() != expected:
            raise CryptoCandidatePromotionError(f"CONTRACT_V3_RATIFICATION_RECORD_HASH_MISMATCH:{repo_path}")
        loaded.append(json.loads(record_raw.decode("utf-8")))
    b2_record, definition_record, _entry_record = loaded
    b2 = (b2_record.get("decisions") or {}).get("B2") or {}
    if b2.get("rule_id") != T2_RULE_ID or b2.get("status") != "RATIFIED":
        raise CryptoCandidatePromotionError("CONTRACT_V3_B2_RECORD_SCOPE_INVALID")
    conditions = (definition_record.get("ratified") or {}).get("t2_minimum_conditions") or []
    if len(conditions) != len(T2_REQUIRED_CONDITIONS):
        raise CryptoCandidatePromotionError("CONTRACT_V3_T2_DEFINITION_MISMATCH")
    return copy.deepcopy(value)


def _decimal(value, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CryptoCandidatePromotionError(f"DECIMAL_INVALID:{label}:{value!r}") from exc


def _criterion(status: str, reason: str, **extra) -> dict:
    if status not in CRITERION_STATUSES:
        raise CryptoCandidatePromotionError(f"CRITERION_STATUS_INVALID:{status}")
    return {"status": status, "reason": reason, **extra}


def _parse_date(value: object, label: str) -> dt.date:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise CryptoCandidatePromotionError(f"DATE_INVALID:{label}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CryptoCandidatePromotionError(f"DATE_INVALID:{label}") from exc


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise CryptoCandidatePromotionError(f"UTC_INVALID:{label}")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoCandidatePromotionError(f"UTC_INVALID:{label}") from exc


def _require_false_authority(value: object, expected: dict, label: str) -> None:
    if value != expected or any(item is not False for item in value.values()):
        raise CryptoCandidatePromotionError(f"AUTHORITY_INVALID:{label}")


def _validate_payload_hash(packet: dict, label: str) -> None:
    claimed = packet.get("payload_sha256")
    if not isinstance(claimed, str) or not _SHA_RE.fullmatch(claimed):
        raise CryptoCandidatePromotionError(f"PAYLOAD_SHA256_INVALID:{label}")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256", None)
    if payload_sha256(unsigned) != claimed:
        raise CryptoCandidatePromotionError(f"PAYLOAD_SHA256_MISMATCH:{label}")


def _validate_universe_packet(packet: dict, evaluation_as_of: str) -> dict:
    """Validate the P3-12 consumer boundary before trusting any row state.

    P3-12 does not yet export a public validator, so the consumer pins its
    complete emitted schema, hash, authority, summary, and current local
    policy/taxonomy ratification state here. A self-consistent fabricated
    PAPER_ELIGIBLE row cannot bypass an unratified local policy.
    """
    expected_keys = {
        "schema_version", "snapshot_date", "evaluation_as_of", "available_at",
        "manifest_sha256", "policy_version", "policy_ratified", "taxonomy_version",
        "taxonomy_ratified", "duplicate_market_codes", "summary", "markets",
        "authority", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoCandidatePromotionError("UNIVERSE_PACKET_SCHEMA_MISMATCH")
    if packet["schema_version"] != UPBIT_UNIVERSE.OUTPUT_SCHEMA_VERSION:
        raise CryptoCandidatePromotionError("UNIVERSE_PACKET_SCHEMA_MISMATCH")
    _validate_payload_hash(packet, "universe")
    _require_false_authority(packet["authority"], UPBIT_UNIVERSE._ROW_AUTHORITY, "universe")

    source_date = _parse_date(packet["evaluation_as_of"], "universe.evaluation_as_of")
    if source_date != _parse_date(evaluation_as_of, "evaluation_as_of"):
        raise CryptoCandidatePromotionError("UNIVERSE_EVALUATION_DATE_MISMATCH")
    _parse_date(packet["snapshot_date"], "universe.snapshot_date")
    available_at = _parse_utc(packet["available_at"], "universe.available_at")
    evaluation_end = dt.datetime.combine(source_date, dt.time.max, tzinfo=dt.timezone.utc)
    if available_at > evaluation_end:
        raise CryptoCandidatePromotionError("UNIVERSE_AVAILABLE_AT_FUTURE_DATED")
    if not isinstance(packet["manifest_sha256"], str) or not _SHA_RE.fullmatch(packet["manifest_sha256"]):
        raise CryptoCandidatePromotionError("UNIVERSE_MANIFEST_SHA256_INVALID")

    policy = UPBIT_UNIVERSE.load_policy()
    taxonomy = UPBIT_UNIVERSE.load_taxonomy()
    identity_registry = UPBIT_UNIVERSE.load_identity_registry()
    effective_identity_registry = UPBIT_UNIVERSE.effective_identity_mapping(
        identity_registry, evaluation_as_of,
    )
    expected_policy_ratified = UPBIT_UNIVERSE._policy_approval_effective(
        policy, evaluation_as_of, date_field="effective_date",
    )
    expected_taxonomy_ratified = UPBIT_UNIVERSE._identity_taxonomy_exact_bound_effective(
        taxonomy, evaluation_as_of, date_field="effective_from", content_field="records",
    )
    if packet["policy_version"] != policy.get("policy_version") or packet["policy_ratified"] is not expected_policy_ratified:
        raise CryptoCandidatePromotionError("UNIVERSE_POLICY_PIN_MISMATCH")
    if packet["taxonomy_version"] != taxonomy.get("policy_version") or packet["taxonomy_ratified"] is not expected_taxonomy_ratified:
        raise CryptoCandidatePromotionError("UNIVERSE_TAXONOMY_PIN_MISMATCH")

    rows = packet["markets"]
    if not isinstance(rows, list):
        raise CryptoCandidatePromotionError("UNIVERSE_MARKETS_INVALID")
    market_ids = [row.get("market") if isinstance(row, dict) else None for row in rows]
    if any(not isinstance(market, str) or not market for market in market_ids):
        raise CryptoCandidatePromotionError("UNIVERSE_MARKETS_INVALID")
    if market_ids != sorted(set(market_ids)):
        raise CryptoCandidatePromotionError("UNIVERSE_MARKETS_INVALID")
    states = (
        UPBIT_UNIVERSE.STATE_OBSERVATION_POOL, UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE,
        UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE, UPBIT_UNIVERSE.STATE_BLOCKED,
    )
    row_keys = {
        "market", "state", "reason", "candidate_canonical_asset_id",
        "market_event_warning", "market_event_caution_any", "observed_daily_candle_count",
        "trailing_30d_krw_turnover", "kraken_cross_exchange_reference", "authority",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_keys or row["state"] not in states:
            raise CryptoCandidatePromotionError("UNIVERSE_ROW_INVALID")
        _require_false_authority(row["authority"], UPBIT_UNIVERSE._ROW_AUTHORITY, f"universe:{row.get('market')}")
        if row["state"] in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
            if not (expected_policy_ratified and expected_taxonomy_ratified):
                raise CryptoCandidatePromotionError("UNIVERSE_IN_SCOPE_STATE_WITH_UNRATIFIED_POLICY")
            if not isinstance(row["candidate_canonical_asset_id"], str) or not row["candidate_canonical_asset_id"]:
                raise CryptoCandidatePromotionError("UNIVERSE_IN_SCOPE_IDENTITY_INVALID")
            if effective_identity_registry.get(row["market"]) != row["candidate_canonical_asset_id"]:
                raise CryptoCandidatePromotionError(
                    "UNIVERSE_IN_SCOPE_STATE_WITH_UNRATIFIED_IDENTITY"
                )
        if row["state"] == UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE and row["reason"] != "PAPER_ELIGIBLE_ALL_GATES_PASSED":
            raise CryptoCandidatePromotionError("UNIVERSE_PAPER_ELIGIBLE_REASON_INVALID")
    expected_summary = {
        "market_count": len(rows),
        "observation_pool_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_OBSERVATION_POOL for row in rows),
        "tradeable_universe_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE for row in rows),
        "paper_eligible_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE for row in rows),
        "blocked_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_BLOCKED for row in rows),
    }
    if packet["summary"] != expected_summary:
        raise CryptoCandidatePromotionError("UNIVERSE_SUMMARY_MISMATCH")
    return copy.deepcopy(packet)


_LEGACY_POLICY_RESOLUTION = object()


def _validate_market_evidence_packet(
    packet: dict, market: str, evaluation_as_of: str, *,
    ratified_policy=_LEGACY_POLICY_RESOLUTION,
) -> dict:
    """Validate one P4-07 packet at the consumer boundary.

    ``ratified_policy`` is contract/3 only: the already hash-bound ratified
    policy (or ``None`` when it is absent/invalid). On that path the proposal
    policy file is never read; a packet that cannot be pinned to the ratified
    policy stays structurally validated and its VOLUME_LIQUIDITY criterion is
    UNKNOWN. The default keeps the contract/2 resolution unchanged.
    """
    expected_keys = {
        "schema_version", "market", "as_of", "captured_at", "policy_version",
        "policy_ratified", "candles", "trades", "orderbook", "authority", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_SCHEMA_MISMATCH:{market}")
    if packet["schema_version"] != MARKET_EVIDENCE.OUTPUT_SCHEMA_VERSION or packet["market"] != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_IDENTITY_MISMATCH:{market}")
    _validate_payload_hash(packet, f"market_evidence:{market}")
    _require_false_authority(packet["authority"], MARKET_EVIDENCE._EVIDENCE_AUTHORITY, f"market_evidence:{market}")
    # Evidence packets must be checked against the policy authority they
    # actually declare.  Once the exact ratified policy exists, continuing to
    # compare a ratified packet with the proposal file makes every legitimate
    # packet fail closed as a status mismatch.  Loading the ratified path here
    # also preserves its exact-hash/contract-pin validation; a packet cannot
    # self-assert ratification and bypass that check.
    if ratified_policy is _LEGACY_POLICY_RESOLUTION:
        if packet["policy_ratified"] is True:
            policy = MARKET_EVIDENCE.load_ratified_policy()
        elif packet["policy_ratified"] is False:
            policy = MARKET_EVIDENCE.load_policy()
        else:
            raise CryptoCandidatePromotionError(
                f"MARKET_EVIDENCE_POLICY_STATUS_MISMATCH:{market}"
            )
        if packet["policy_version"] != policy.get("policy_version"):
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_POLICY_PIN_MISMATCH:{market}")
        if packet["policy_ratified"] is not (policy.get("approval_status") == "RATIFIED"):
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_POLICY_STATUS_MISMATCH:{market}")
    else:
        if packet["policy_ratified"] not in (True, False):
            raise CryptoCandidatePromotionError(
                f"MARKET_EVIDENCE_POLICY_STATUS_MISMATCH:{market}"
            )
        if not isinstance(packet["policy_version"], str) or not packet["policy_version"]:
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_POLICY_PIN_MISMATCH:{market}")
        if (
            packet["policy_ratified"] is True
            and ratified_policy is not None
            and packet["policy_version"] != ratified_policy.get("policy_version")
        ):
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_POLICY_PIN_MISMATCH:{market}")
    as_of = _parse_utc(packet["as_of"], f"market_evidence.{market}.as_of")
    captured_at = _parse_utc(packet["captured_at"], f"market_evidence.{market}.captured_at")
    evaluation_end = dt.datetime.combine(_parse_date(evaluation_as_of, "evaluation_as_of"), dt.time.max, tzinfo=dt.timezone.utc)
    if captured_at < as_of or captured_at > evaluation_end:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_TIME_INVALID:{market}")
    if set(packet["candles"]) != set(MARKET_EVIDENCE.finalization.TIMEFRAMES):
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_TIMEFRAMES_MISMATCH:{market}")
    for timeframe, candle in packet["candles"].items():
        if candle.get("market") != market or candle.get("timeframe") != timeframe:
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_CANDLE_IDENTITY_MISMATCH:{market}:{timeframe}")
        if candle.get("finalized_candle_count") != len(candle.get("finalized_candles", [])):
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_CANDLE_COUNT_MISMATCH:{market}:{timeframe}")
        _require_false_authority(candle.get("authority"), MARKET_EVIDENCE._EVIDENCE_AUTHORITY, f"candle:{market}:{timeframe}")
    for label in ("trades", "orderbook"):
        value = packet[label]
        if not isinstance(value, dict) or value.get("market") != market:
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_{label.upper()}_INVALID:{market}")
        _require_false_authority(value.get("authority"), MARKET_EVIDENCE._EVIDENCE_AUTHORITY, f"{label}:{market}")
    return copy.deepcopy(packet)


def _validate_leadership_output(output: dict, evaluation_as_of: str) -> dict:
    if not isinstance(output, dict) or output.get("schema_version") != 2:
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_SCHEMA_MISMATCH")
    contract = CRYPTO_LEADERSHIP.load_contract()
    policy = CRYPTO_LEADERSHIP.load_leadership_policy()
    CRYPTO_LEADERSHIP.require_ratified_leadership_policy(policy)
    if output.get("contract_version") != contract["contract_version"] or output.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_HEADER_MISMATCH")
    if _parse_date(output.get("as_of_date"), "leadership.as_of_date") > _parse_date(evaluation_as_of, "evaluation_as_of"):
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_FUTURE_DATED")
    leadership_pin = (output.get("policies") or {}).get("leadership") or {}
    if (
        leadership_pin.get("policy_version") != policy["policy_version"]
        or leadership_pin.get("policy_sha256") != CRYPTO_LEADERSHIP.file_sha256(CRYPTO_LEADERSHIP.LEADERSHIP_POLICY_PATH)
        or leadership_pin.get("approval_status") != "RATIFIED"
        or leadership_pin.get("group_coverage_policy_status") != "UNRATIFIED"
    ):
        raise CryptoCandidatePromotionError("LEADERSHIP_POLICY_PIN_MISMATCH")
    authority = CRYPTO_LEADERSHIP.authority_boundary()
    for key, expected in authority.items():
        if output.get(key) is not expected:
            raise CryptoCandidatePromotionError(f"LEADERSHIP_AUTHORITY_INVALID:{key}")
    windows = output.get("windows")
    if not isinstance(windows, list) or {row.get("window_id") for row in windows} != {
        item["window_id"] for item in policy["windows"]
    }:
        raise CryptoCandidatePromotionError("LEADERSHIP_WINDOWS_INVALID")
    return copy.deepcopy(output)


# ---------------------------------------------------------------------------
# Individual criterion evaluators
# ---------------------------------------------------------------------------

def evaluate_identity(universe_row: dict) -> dict:
    """P3-12 already requires a ratified ``canonical_asset_id`` mapping
    before any market can leave OBSERVATION_POOL -- see
    ``universe/upbit_tradeable_universe.py::build_classification``'s
    ``IDENTITY_UNRATIFIED`` gate. For any row this module is scoped to
    process (state TRADEABLE_UNIVERSE/PAPER_ELIGIBLE), this is therefore
    unreachable-as-UNKNOWN by construction; the UNKNOWN branch below is a
    defensive check, not a real production path.
    """
    if universe_row.get("candidate_canonical_asset_id") is not None:
        return _criterion("PASS", "IDENTITY_RATIFIED_VIA_P3_12")
    return _criterion("UNKNOWN", "IDENTITY_UNRATIFIED")


def evaluate_tradability(universe_row: dict) -> dict:
    """A market only reaches this module's input set after clearing every
    P3-12 turnover/spread/listing-history/capture-freshness gate. This
    criterion is a documented echo of that fact, never a new check.
    """
    state = universe_row.get("state")
    if state in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
        return _criterion("PASS", f"P3_12_STATE:{state}")
    raise CryptoCandidatePromotionError(f"UNIVERSE_ROW_OUT_OF_SCOPE:{state}")


def evaluate_regime(regime_payload: dict) -> dict:
    """See module docstring's REGIME row. ``regime_payload`` must already be
    a schema-valid ``regime/output_contract.py`` payload for market="CRYPTO"
    (``build_promotion_packet`` validates this before calling here). Its
    ``regime`` field can never legally be anything but "UNKNOWN" while
    ``runtime_authorized_regimes == ["UNKNOWN"]`` -- ``validate_output``
    itself raises otherwise. The defensive check below still fails closed
    (raises, never guesses an interpretation) if that invariant is ever
    violated without this module being updated first.
    """
    if regime_payload.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError(f"REGIME_PAYLOAD_MARKET_MISMATCH:{regime_payload.get('market')}")
    regime_value = regime_payload.get("regime")
    if regime_value != "UNKNOWN":
        raise CryptoCandidatePromotionError(f"REGIME_VALUE_NOT_UNDERSTOOD:{regime_value}")
    return _criterion("UNKNOWN", "REGIME_AGGREGATE_UNAUTHORIZED_PENDING_P1_COM_05")


def _candle_close(candle_row: dict) -> Decimal:
    return _decimal(candle_row.get("trade_price"), "trade_price")


def _direction(finalized_candles) -> str | None:
    """The minimal-parameter mechanical directional fact: compares the two
    most-recently-finalized candles' close prices for one timeframe. Returns
    ``None`` (never a guess) when fewer than two finalized candles exist.
    """
    if not isinstance(finalized_candles, list) or len(finalized_candles) < 2:
        return None
    ordered = sorted(finalized_candles, key=lambda row: row["close_time"])
    latest = _candle_close(ordered[-1])
    previous = _candle_close(ordered[-2])
    if latest > previous:
        return "UP"
    if latest < previous:
        return "DOWN"
    return "FLAT"


def evaluate_trend(market: str, market_evidence_packet: dict | None) -> dict:
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    if market_evidence_packet.get("market") != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_PACKET_MARKET_MISMATCH:{market}")
    candles = market_evidence_packet.get("candles") or {}
    daily = (candles.get("1d") or {}).get("finalized_candles")
    four_hour = (candles.get("4h") or {}).get("finalized_candles")
    daily_direction = _direction(daily)
    four_hour_direction = _direction(four_hour)
    if daily_direction is None or four_hour_direction is None:
        return _criterion(
            "UNKNOWN", "INSUFFICIENT_FINALIZED_CANDLES",
            daily_direction=daily_direction, four_hour_direction=four_hour_direction,
        )
    return _criterion(
        "UNKNOWN", "NO_RATIFIED_CANDIDATE_TREND_RULE",
        daily_direction=daily_direction, four_hour_direction=four_hour_direction,
    )


def evaluate_relative_strength(canonical_asset_id: str | None, leadership_output: dict | None) -> dict:
    """Evaluate the known BTC leg without pretending the peer leg exists.

    The criterion is conjunctive in the canonical policy: non-positive BTC
    relative strength is enough to FAIL, while a positive BTC leg remains
    UNKNOWN until the peer-group leg is ratified and measured.
    """
    if canonical_asset_id is None:
        return _criterion("UNKNOWN", "IDENTITY_UNRATIFIED")
    if leadership_output is None:
        return _criterion("UNKNOWN", "LEADERSHIP_OUTPUT_MISSING")
    if canonical_asset_id == "BTC":
        return _criterion("UNKNOWN", "BTC_SELF_REFERENCE_RULE_UNRATIFIED")
    if leadership_output.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_MARKET_MISMATCH")
    windows = {w.get("window_id"): w for w in leadership_output.get("windows", [])}
    window = windows.get(LEADERSHIP_PRIMARY_WINDOW_ID)
    if window is None or window.get("role") != "PRIMARY":
        raise CryptoCandidatePromotionError("LEADERSHIP_PRIMARY_WINDOW_MISSING")
    if window.get("status") != "OBSERVED_UNCLASSIFIED":
        return _criterion("UNKNOWN", f"LEADERSHIP_WINDOW_UNKNOWN:{window.get('unknown_reason')}")
    partial_ids = {item["canonical_asset_id"] for item in window.get("partial_window_assets", [])}
    if canonical_asset_id in partial_ids:
        return _criterion("UNKNOWN", "LEADERSHIP_ASSET_WINDOW_INCOMPLETE")
    asset_row = next(
        (item for item in window.get("asset_relative_strength", []) if item.get("canonical_asset_id") == canonical_asset_id),
        None,
    )
    if asset_row is None:
        return _criterion("UNKNOWN", "LEADERSHIP_ASSET_NOT_COVERED")
    rs_text = asset_row.get("relative_strength_vs_btc")
    rs = _decimal(rs_text, "relative_strength_vs_btc")
    if rs > 0:
        return _criterion(
            "UNKNOWN", "PEER_RELATIVE_STRENGTH_UNRATIFIED",
            btc_leg_status="PASS", relative_strength_vs_btc=rs_text,
        )
    return _criterion("FAIL", f"RELATIVE_STRENGTH_VS_BTC_NOT_POSITIVE:{rs_text}", relative_strength_vs_btc=rs_text)


def evaluate_volume_liquidity(market: str, market_evidence_packet: dict | None) -> dict:
    """See module docstring's VOLUME_LIQUIDITY row: structural presence
    only, never the unratified spread/slippage/staleness thresholds.
    """
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    if market_evidence_packet.get("market") != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_PACKET_MARKET_MISMATCH:{market}")
    candles = market_evidence_packet.get("candles") or {}
    price_family_present = bool((candles.get("1d") or {}).get("finalized_candle_count")) and bool(
        (candles.get("4h") or {}).get("finalized_candle_count")
    )
    orderbook = market_evidence_packet.get("orderbook") or {}
    trades = market_evidence_packet.get("trades") or {}
    liquidity_family_present = orderbook.get("best_bid") is not None and orderbook.get("best_ask") is not None
    volume_family_present = bool(trades.get("trade_count"))
    if price_family_present and liquidity_family_present and volume_family_present:
        return _criterion(
            "UNKNOWN", "VOLUME_LIQUIDITY_THRESHOLDS_UNRATIFIED",
            price_family_present=True,
            liquidity_family_present=True,
            volume_family_present=True,
        )
    return _criterion(
        "UNKNOWN", "EVIDENCE_FAMILY_INCOMPLETE",
        price_family_present=price_family_present,
        liquidity_family_present=liquidity_family_present,
        volume_family_present=volume_family_present,
    )


def load_ratified_market_evidence_policy(contract_v3: dict) -> tuple[dict | None, str | None]:
    """Contract/3 P4-07 reader: the ratified policy only, bound by hash.

    Returns ``(policy, None)`` or ``(None, reason)``. Absence or any
    validation failure is a named UNKNOWN reason, never a fallback to the
    proposal file and never an exception that aborts the whole packet.
    """
    try:
        policy = MARKET_EVIDENCE.load_ratified_policy()
    except MARKET_EVIDENCE.MarketEvidenceError as exc:
        return None, "P4_07_RATIFIED_POLICY_UNAVAILABLE:" + str(exc).split(":", 1)[0]
    binding = contract_v3["market_evidence_policy"]
    if (
        policy.get("packet_sha256") != binding["packet_sha256"]
        or policy.get("policy_id") != binding["policy_id"]
        or policy.get("policy_version") != binding["policy_version"]
    ):
        return None, "P4_07_RATIFIED_POLICY_UNAVAILABLE:CONTRACT_V3_BINDING_MISMATCH"
    return copy.deepcopy(policy), None


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def evaluate_volume_liquidity_ratified(
    market: str,
    market_evidence_packet: dict | None,
    *,
    ratified_policy: dict | None,
    policy_unavailable_reason: str | None,
) -> dict:
    """Contract/3 VOLUME_LIQUIDITY over the ratified P4-07 thresholds.

    PASS only when the packet is bound to the ratified policy, captured
    inside its effective window, and every ratified threshold is met on PASS
    evidence. A breach or non-PASS evidence keeps P4-07's own
    ``fail_closed_unknown`` meaning (UNKNOWN with reasons), never FAIL.
    """
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    if market_evidence_packet.get("market") != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_PACKET_MARKET_MISMATCH:{market}")
    candles = market_evidence_packet.get("candles") or {}
    price_family_present = bool((candles.get("1d") or {}).get("finalized_candle_count")) and bool(
        (candles.get("4h") or {}).get("finalized_candle_count")
    )
    orderbook = market_evidence_packet.get("orderbook") or {}
    trades = market_evidence_packet.get("trades") or {}
    liquidity_family_present = orderbook.get("best_bid") is not None and orderbook.get("best_ask") is not None
    volume_family_present = bool(trades.get("trade_count"))
    families = {
        "price_family_present": price_family_present,
        "liquidity_family_present": liquidity_family_present,
        "volume_family_present": volume_family_present,
    }
    if not (price_family_present and liquidity_family_present and volume_family_present):
        return _criterion("UNKNOWN", "EVIDENCE_FAMILY_INCOMPLETE", **families)
    if ratified_policy is None:
        return _criterion(
            "UNKNOWN", policy_unavailable_reason or "P4_07_RATIFIED_POLICY_UNAVAILABLE", **families,
        )
    policy_sha256 = ratified_policy.get("packet_sha256")
    if market_evidence_packet.get("policy_ratified") is not True:
        return _criterion(
            "UNKNOWN", "MARKET_EVIDENCE_PACKET_NOT_BOUND_TO_RATIFIED_POLICY",
            policy_packet_sha256=policy_sha256, **families,
        )
    captured_at = _parse_utc(market_evidence_packet.get("captured_at"), f"market_evidence.{market}.captured_at")
    effective_from = _parse_utc(ratified_policy.get("effective_from_utc"), "p4_07.effective_from_utc")
    effective_to = _parse_utc(ratified_policy.get("effective_to_utc"), "p4_07.effective_to_utc")
    if not effective_from <= captured_at <= effective_to:
        return _criterion(
            "UNKNOWN", "MARKET_EVIDENCE_CAPTURED_OUTSIDE_RATIFIED_POLICY_WINDOW",
            policy_packet_sha256=policy_sha256, **families,
        )

    reasons = []
    for timeframe in ("1d", "4h"):
        evidence = candles.get(timeframe) or {}
        if evidence.get("evidence_status") != "PASS":
            reasons.extend(
                f"{timeframe}:{reason}" for reason in (evidence.get("fail_closed_reasons") or ["EVIDENCE_NOT_PASS"])
            )
    if trades.get("evidence_status") != "PASS":
        reasons.extend(f"TRADES:{reason}" for reason in (trades.get("fail_closed_reasons") or ["EVIDENCE_NOT_PASS"]))
    orderbook_status = (orderbook.get("freshness") or {}).get("status")
    if orderbook_status != "FRESH":
        reasons.append(f"ORDERBOOK_{orderbook_status}")
    depth_levels = ratified_policy["orderbook_depth_levels"]
    if ((orderbook.get("depth") or {}).get("levels_available") or 0) < depth_levels:
        reasons.append("ORDERBOOK_DEPTH_LEVELS_PARTIAL")
    notional = _decimal_or_none(orderbook.get("slippage_estimate_notional_krw"))
    if notional is None or notional != Decimal(str(ratified_policy["paper_slippage_estimate_notional_krw"])):
        reasons.append("SLIPPAGE_NOTIONAL_NOT_RATIFIED")
    max_spread = Decimal(str(ratified_policy["max_spread_bps_normal"]))
    max_slippage = Decimal(str(ratified_policy["max_slippage_bps_normal"]))
    spread_bps = _decimal_or_none(orderbook.get("spread_bps"))
    slippage_bps = _decimal_or_none(orderbook.get("slippage_bps"))
    if spread_bps is None:
        reasons.append("SPREAD_NOT_COMPUTABLE")
    elif spread_bps > max_spread:
        reasons.append("SPREAD_ABOVE_RATIFIED_MAX")
    if slippage_bps is None:
        reasons.append("SLIPPAGE_NOT_COMPUTABLE")
    elif slippage_bps > max_slippage:
        reasons.append("SLIPPAGE_ABOVE_RATIFIED_MAX")

    measured = {
        "spread_bps": orderbook.get("spread_bps"),
        "slippage_bps": orderbook.get("slippage_bps"),
        "slippage_estimate_notional_krw": orderbook.get("slippage_estimate_notional_krw"),
        "max_spread_bps_normal": str(ratified_policy["max_spread_bps_normal"]),
        "max_slippage_bps_normal": str(ratified_policy["max_slippage_bps_normal"]),
        "policy_packet_sha256": policy_sha256,
    }
    if reasons:
        return _criterion(
            "UNKNOWN", "P4_07_RATIFIED_EVIDENCE_NOT_PASSED",
            ratified_evidence_reasons=sorted(set(reasons)), **measured, **families,
        )
    return _criterion("PASS", "P4_07_RATIFIED_THRESHOLDS_MET", **measured, **families)


def _validate_crypto_runtime_decision(decision: object, contract_v3: dict) -> dict:
    """Integrity boundary for a CRYPTO_PAPER_RUNTIME_V1 decision packet.

    Checks schema, exact key set, content-addressed ``decision_id``, the
    hash-bound ratified policy identity, authority, and that a KNOWN regime is
    only ever a fully accepted, classified decision. Full rederivation needs
    the day records and stays the publisher's job
    (``regime/crypto_paper_runtime_publication.py``).
    """
    runtime = _crypto_runtime()
    if not isinstance(decision, dict) or set(decision) != CRYPTO_RUNTIME_DECISION_KEYS:
        raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_DECISION_SCHEMA_MISMATCH")
    unsigned = {key: value for key, value in decision.items() if key != "decision_id"}
    if decision["decision_id"] != "crypto-paper-regime:" + runtime.payload_sha256(unsigned):
        raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_DECISION_ID_MISMATCH")
    source = contract_v3["regime_source"]
    if (
        decision["schema_version"] != source["decision_schema_version"]
        or decision["market"] != "CRYPTO"
        or decision["policy_identity"] != source["ratification_identity"]
        or decision["policy_sha256"] != source["policy_sha256"]
        or decision["scope"] != "CRYPTO_INTERNAL_VIRTUAL_PAPER_ONLY"
    ):
        raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_DECISION_IDENTITY_MISMATCH")
    evaluation_at = _parse_utc(decision["evaluation_at"], "crypto_runtime.evaluation_at")
    regime = decision["runtime_regime"]
    if regime not in REGIME_GATE_V3:
        raise CryptoCandidatePromotionError(f"CRYPTO_RUNTIME_REGIME_VALUE_INVALID:{regime}")
    authority = decision["authority"]
    known = regime != "UNKNOWN"
    if (
        not isinstance(authority, dict)
        or set(authority) != set(runtime.AUTHORITY_CLOSED)
        or authority.get("paper_runtime_display_authorized") is not known
        or any(value is not False for key, value in authority.items() if key != "paper_runtime_display_authorized")
    ):
        raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_DECISION_AUTHORITY_INVALID")
    decision_date = decision["current_decision_date"]
    if decision_date is not None:
        expected_date = runtime.current_decision_date(evaluation_at)
        if (
            decision_date != expected_date.isoformat()
            or decision["decision_at"] != runtime.utc_text(runtime.decision_at_for(expected_date))
        ):
            raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_DECISION_DATE_INCONSISTENT")
    if known:
        acceptance = decision["acceptance"] or {}
        aggregation = decision["aggregation"] or {}
        if not (
            decision["decision_status"] == "PAPER_RUNTIME_CLASSIFIED"
            and decision["paper_regime"] == regime
            and decision["runtime_decision_available"] is True
            and decision["reasons"] == []
            and decision["evidence_class"] == runtime.LIVE_NATURAL
            and decision_date is not None
            and acceptance.get("status") == runtime.ACCEPTED
            and aggregation.get("final_regime") == regime
        ):
            raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_KNOWN_DECISION_INCONSISTENT")
    elif not (
        decision["runtime_decision_available"] is False
        and decision["paper_regime"] == "UNKNOWN"
        and decision["confidence"] is None
    ):
        raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_UNKNOWN_DECISION_INCONSISTENT")
    return copy.deepcopy(decision)


def _regime_gate_criterion(effective_regime: str, reason: str, **extra) -> dict:
    status, new_buys, multiplier, hold_cap = REGIME_GATE_V3[effective_regime]
    return _criterion(
        status, reason,
        effective_regime=effective_regime,
        new_buys=new_buys,
        state_multiplier_of_base=multiplier,
        hold_current_max_multiplier_of_base=hold_cap,
        gate_source_ratification_id=ALLOCATION_V2_RATIFICATION_ID,
        **extra,
    )


def evaluate_crypto_runtime_regime(runtime_decision: dict | None, *, reference_at: str) -> dict:
    """Contract/3 REGIME: the CRYPTO_PAPER_RUNTIME_V1 decision in force at
    ``reference_at`` mapped through the allocation-v2 new-buy table.

    ``runtime_decision`` must already have passed
    ``_validate_crypto_runtime_decision``. Every KNOWN value maps; nothing
    here raises for a valid KNOWN regime. A decision computed after
    ``reference_at`` is lookahead and raises. A decision for an earlier UTC
    decision date is not carried: UNKNOWN.
    """
    reference = _parse_utc(reference_at, "regime.reference_at")
    runtime = _crypto_runtime()
    expected_date = runtime.current_decision_date(reference).isoformat()
    if runtime_decision is None:
        return _regime_gate_criterion(
            "UNKNOWN", "CRYPTO_RUNTIME_DECISION_MISSING",
            source_runtime_regime=None, runtime_decision_id=None,
            runtime_decision_date=None, expected_decision_date=expected_date, runtime_reasons=[],
        )
    if _parse_utc(runtime_decision["evaluation_at"], "crypto_runtime.evaluation_at") > reference:
        raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_DECISION_LOOKAHEAD")
    source_regime = runtime_decision["runtime_regime"]
    lineage = {
        "source_runtime_regime": source_regime,
        "runtime_decision_id": runtime_decision["decision_id"],
        "runtime_decision_date": runtime_decision["current_decision_date"],
        "expected_decision_date": expected_date,
    }
    if runtime_decision["current_decision_date"] != expected_date:
        return _regime_gate_criterion(
            "UNKNOWN",
            f"CRYPTO_RUNTIME_DECISION_NOT_CURRENT:{runtime_decision['current_decision_date']}",
            runtime_reasons=list(runtime_decision["reasons"]), **lineage,
        )
    if source_regime != "UNKNOWN" and source_regime not in runtime.load_runtime_authorized_regimes():
        return _regime_gate_criterion(
            "UNKNOWN", "CRYPTO_RUNTIME_POLICY_UNAVAILABLE",
            runtime_reasons=list(runtime_decision["reasons"]), **lineage,
        )
    new_buys = REGIME_GATE_V3[source_regime][1]
    return _regime_gate_criterion(
        source_regime, f"CRYPTO_RUNTIME_REGIME:{source_regime}:NEW_BUYS_{new_buys}",
        runtime_reasons=list(runtime_decision["reasons"]), **lineage,
    )


def evaluate_t2_population_membership(universe_row: dict, *, snapshot_date: str) -> dict:
    """In-scope row of the already-validated P3-12 population packet whose
    evaluation date equals this promotion's (``_validate_universe_packet``)."""
    state = universe_row.get("state")
    if state not in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
        raise CryptoCandidatePromotionError(f"UNIVERSE_ROW_OUT_OF_SCOPE:{state}")
    return _criterion("PASS", f"P3_12_POPULATION_SNAPSHOT:{snapshot_date}", p3_12_state=state)


def evaluate_t2_liquidity(universe_row: dict) -> dict:
    """The ratified Upbit liquidity threshold is P3-12's
    ``min_30d_avg_krw_turnover``; an in-scope P3-12 row has already passed it
    on complete 30-day history. The threshold is re-reported for lineage."""
    policy = UPBIT_UNIVERSE.load_policy()
    lineage = {
        "min_30d_avg_krw_turnover": str(policy["min_30d_avg_krw_turnover"]),
        "universe_policy_version": policy.get("policy_version"),
        "trailing_30d_krw_turnover": universe_row.get("trailing_30d_krw_turnover"),
    }
    if _decimal_or_none(universe_row.get("trailing_30d_krw_turnover")) is None:
        return _criterion("UNKNOWN", "TRAILING_30D_KRW_TURNOVER_MISSING", **lineage)
    return _criterion("PASS", "UPBIT_RATIFIED_MIN_30D_AVG_KRW_TURNOVER_MET_VIA_P3_12", **lineage)


def evaluate_t2_price_data(market: str, market_evidence_packet: dict | None, *, reference_at: str) -> dict:
    """Finalized 1d candle for the latest completed UTC day, available no
    later than the reference instant. Absent evidence is UNKNOWN."""
    reference = _parse_utc(reference_at, "t2_price.reference_at")
    expected_close = dt.datetime.combine(reference.date(), dt.time(0, 0), tzinfo=dt.timezone.utc)
    expected_text = expected_close.strftime("%Y-%m-%dT%H:%M:%SZ")
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING", expected_latest_close_time=expected_text)
    if market_evidence_packet.get("market") != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_PACKET_MARKET_MISMATCH:{market}")
    daily = (market_evidence_packet.get("candles") or {}).get("1d") or {}
    closes = sorted(
        row.get("close_time") for row in daily.get("finalized_candles") or []
        if isinstance(row, dict) and isinstance(row.get("close_time"), str)
    )
    lineage = {
        "expected_latest_close_time": expected_text,
        "latest_finalized_close_time": closes[-1] if closes else None,
    }
    if expected_text not in closes:
        return _criterion("UNKNOWN", "PRICE_LATEST_COMPLETED_SESSION_MISSING", **lineage)
    available_at = daily.get("available_at") or market_evidence_packet.get("captured_at")
    if _parse_utc(available_at, f"market_evidence.{market}.1d.available_at") > reference:
        return _criterion("UNKNOWN", "PRICE_AVAILABLE_AFTER_REFERENCE", **lineage)
    return _criterion("PASS", "PRICE_LATEST_COMPLETED_SESSION_PRESENT", **lineage)


def rotation_bucket(canonical_asset_id: str | None) -> str | None:
    if canonical_asset_id is None:
        return None
    return canonical_asset_id if canonical_asset_id in ("BTC", "ETH") else "ALT"


def evaluate_t2_rotation_membership(canonical_asset_id: str | None) -> dict:
    """RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1 C5. No ratified confirmation
    state source is wired into P5-08 yet, so this required condition is
    UNKNOWN (fail closed) -- never inferred from the leadership ranking."""
    return _criterion(
        "UNKNOWN", ROTATION_NOT_WIRED_REASON,
        rotation_bucket=rotation_bucket(canonical_asset_id),
        required_states=["STRONG_CONFIRMED", "STRONG_HELD"],
    )


ROTATION_CRYPTO_MARKET = "CRYPTO"


def _validate_rotation_confirmation(packet: object, *, reference_at: str) -> dict:
    """Integrity boundary for a ``rotation_confirmation_packet/1`` (CRYPTO).

    Packet hash, market and the ratified rotation policy identity are checked
    by the #752 wiring itself. A packet for the reference UTC day (or later)
    is lookahead: the as-of day's leadership close is not complete yet.
    """
    wiring = _rotation_wiring()
    try:
        checked = wiring.RC.validate_packet(copy.deepcopy(packet))
    except ValueError as exc:
        raise CryptoCandidatePromotionError(f"ROTATION_CONFIRMATION_INVALID:{exc}") from exc
    if checked.get("market") != ROTATION_CRYPTO_MARKET:
        raise CryptoCandidatePromotionError("ROTATION_CONFIRMATION_MARKET_MISMATCH")
    if checked.get("policy") != wiring.RC.policy_identity(wiring.RC.load_policy()):
        raise CryptoCandidatePromotionError("ROTATION_CONFIRMATION_POLICY_MISMATCH")
    reference_day = _parse_utc(reference_at, "rotation.reference_at").date()
    if _parse_date(checked.get("as_of_date"), "rotation.as_of_date") >= reference_day:
        raise CryptoCandidatePromotionError("ROTATION_CONFIRMATION_LOOKAHEAD")
    return checked


def evaluate_t2_rotation_membership_confirmed(
    canonical_asset_id: str | None, rotation_confirmation: dict, *, reference_at: str,
) -> dict:
    """RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1 C5 read from the ratified crypto
    rotation confirmation packet (``rotation/rotation_confirmation_wiring.py``).

    PASS only for a STRONG_CONFIRMED / STRONG_HELD bucket; another observed
    state FAILs; an unobserved, stale, not-yet-effective packet or an
    unresolved bucket is UNKNOWN (fail closed, never inferred).
    """
    wiring = _rotation_wiring()
    policy = wiring.RC.load_policy()
    pass_states = list(policy["common"]["t2_c5_pass_states"])
    bucket = rotation_bucket(canonical_asset_id)
    scope_id = policy["markets"][ROTATION_CRYPTO_MARKET]["scope_id"]
    evaluation_date = _parse_utc(reference_at, "rotation.reference_at").date().isoformat()
    lineage = {
        "rotation_bucket": bucket,
        "required_states": pass_states,
        "confirmation_as_of_date": rotation_confirmation["as_of_date"],
        "confirmation_payload_sha256": rotation_confirmation["payload_sha256"],
        "scope_id": scope_id,
    }
    try:
        blocker = wiring.packet_blocker(rotation_confirmation, ROTATION_CRYPTO_MARKET, evaluation_date, policy)
    except ValueError as exc:
        raise CryptoCandidatePromotionError(f"ROTATION_CONFIRMATION_INVALID:{exc}") from exc
    if blocker is not None:
        return _criterion("UNKNOWN", f"ROTATION_CONFIRMATION_UNKNOWN:{blocker}",
                          sector_state=None, strong_confirmed_on=None, **lineage)
    if bucket is None:
        return _criterion("UNKNOWN", "ROTATION_BUCKET_UNRESOLVED",
                          sector_state=None, strong_confirmed_on=None, **lineage)
    entity = wiring._entity(rotation_confirmation, scope_id, bucket)
    if entity is None:
        return _criterion("UNKNOWN", "ROTATION_BUCKET_NOT_OBSERVED",
                          sector_state=None, strong_confirmed_on=None, **lineage)
    state = entity["state"]
    if state in pass_states:
        return _criterion("PASS", f"ROTATION_{state}", sector_state=state,
                          strong_confirmed_on=entity["strong_confirmed_on"], **lineage)
    return _criterion("FAIL", f"ROTATION_NOT_STRONG_CONFIRMED_OR_HELD:{state}", sector_state=state,
                      strong_confirmed_on=None, **lineage)


def aggregate_t2_state(t2_conditions: dict) -> tuple[str, str]:
    """Contract/3 state rule: only the six T2 required conditions decide."""
    if set(t2_conditions) != set(T2_REQUIRED_CONDITIONS):
        raise CryptoCandidatePromotionError(f"T2_CONDITION_SET_INVALID:{sorted(t2_conditions)}")
    failed = sorted(name for name, result in t2_conditions.items() if result["status"] == "FAIL")
    if failed:
        return STATE_BLOCKED, "T2_REQUIRED_FAILED:" + ",".join(failed)
    unknown = sorted(name for name, result in t2_conditions.items() if result["status"] == "UNKNOWN")
    if unknown:
        return STATE_WATCH, "T2_REQUIRED_UNKNOWN:" + ",".join(unknown)
    return STATE_FOCUSED_REVIEW, "T2_REQUIRED_ALL_PASSED"


def _rule_ref(rule_id: str, role: str) -> dict:
    return {
        "rule_id": rule_id,
        "version": RULE_VERSIONS_V3.get(rule_id, 1),
        "registry_sha256": None,
        "source_record_sha256": RULE_SOURCES_V3[rule_id],
        "role": role,
    }


def evaluate_overextension() -> dict:
    """See module docstring's OVEREXTENSION row: no mechanical or ratified
    definition of "과열·급등 추격" exists anywhere in this repository.
    """
    return _criterion("UNKNOWN", "NO_RATIFIED_OVEREXTENSION_THRESHOLD")


def evaluate_material_blocker(universe_row: dict) -> dict:
    """See module docstring's MATERIAL_BLOCKER row."""
    caution_any = universe_row.get("market_event_caution_any")
    if caution_any is None:
        return _criterion("UNKNOWN", "CAUTION_FLAG_STATUS_UNKNOWN")
    if caution_any is True:
        return _criterion("FAIL", "UPBIT_MARKET_EVENT_CAUTION_ACTIVE")
    return _criterion(
        "UNKNOWN", "SECURITY_AND_NETWORK_OUTAGE_COVERAGE_MISSING",
        upbit_market_event_caution_active=False,
    )


def evaluate_criteria(
    universe_row: dict,
    *,
    regime_payload: dict,
    market_evidence_packet: dict | None,
    leadership_output: dict | None,
) -> dict:
    market = universe_row["market"]
    return {
        "IDENTITY": evaluate_identity(universe_row),
        "TRADABILITY": evaluate_tradability(universe_row),
        "REGIME": evaluate_regime(regime_payload),
        "TREND": evaluate_trend(market, market_evidence_packet),
        "RELATIVE_STRENGTH": evaluate_relative_strength(
            universe_row.get("candidate_canonical_asset_id"), leadership_output
        ),
        "VOLUME_LIQUIDITY": evaluate_volume_liquidity(market, market_evidence_packet),
        "OVEREXTENSION": evaluate_overextension(),
        "MATERIAL_BLOCKER": evaluate_material_blocker(universe_row),
    }


# ---------------------------------------------------------------------------
# State-machine transition rule -- a pure function of already-computed
# criteria, independent of how each criterion was sourced. This is what
# proves the RULE reaches FOCUSED_REVIEW given an all-PASS input, even
# though REGIME's own evaluator can never itself produce PASS today.
# ---------------------------------------------------------------------------

def aggregate_state(criteria: dict) -> tuple[str, str]:
    if set(criteria) != set(CRITERIA):
        raise CryptoCandidatePromotionError(f"CRITERIA_SET_INVALID:{sorted(criteria)}")
    failed = sorted(name for name, result in criteria.items() if result["status"] == "FAIL")
    if failed:
        return STATE_BLOCKED, "CRITERIA_FAILED:" + ",".join(failed)
    unknown = sorted(name for name, result in criteria.items() if result["status"] == "UNKNOWN")
    if unknown:
        return STATE_WATCH, "CRITERIA_UNKNOWN:" + ",".join(unknown)
    return STATE_FOCUSED_REVIEW, "ALL_CRITERIA_PASSED"


def evaluate_candidate(
    universe_row: dict,
    *,
    regime_payload: dict,
    market_evidence_packet: dict | None,
    leadership_output: dict | None,
) -> dict:
    criteria = evaluate_criteria(
        universe_row,
        regime_payload=regime_payload,
        market_evidence_packet=market_evidence_packet,
        leadership_output=leadership_output,
    )
    return _candidate_row(universe_row, criteria)


def _candidate_row(universe_row: dict, criteria: dict) -> dict:
    state, reason = aggregate_state(criteria)
    return {
        "market": universe_row["market"],
        "canonical_asset_id": universe_row.get("candidate_canonical_asset_id"),
        "p3_12_state": universe_row["state"],
        "criteria": criteria,
        "promotion_state": state,
        "promotion_reason": reason,
        "authority": dict(_ROW_AUTHORITY),
    }


def evaluate_candidate_v3(
    universe_row: dict,
    *,
    regime_criterion: dict,
    market_evidence_packet: dict | None,
    leadership_output: dict | None,
    ratified_policy: dict | None,
    policy_unavailable_reason: str | None,
    snapshot_date: str,
    reference_at: str,
    rotation_confirmation: dict | None = None,
) -> dict:
    """Contract/3 row under RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1.

    The state comes only from the six T2 required conditions. The eight
    contract/2-named criteria are still emitted for lineage (REGIME and
    VOLUME_LIQUIDITY with the contract/3 evaluators); the five non-blocking
    ones surface as warnings and never change the state.
    """
    market = universe_row["market"]
    canonical_asset_id = universe_row.get("candidate_canonical_asset_id")
    criteria = {
        "IDENTITY": evaluate_identity(universe_row),
        "TRADABILITY": evaluate_tradability(universe_row),
        "REGIME": copy.deepcopy(regime_criterion),
        "TREND": evaluate_trend(market, market_evidence_packet),
        "RELATIVE_STRENGTH": evaluate_relative_strength(canonical_asset_id, leadership_output),
        "VOLUME_LIQUIDITY": evaluate_volume_liquidity_ratified(
            market, market_evidence_packet,
            ratified_policy=ratified_policy, policy_unavailable_reason=policy_unavailable_reason,
        ),
        "OVEREXTENSION": evaluate_overextension(),
        "MATERIAL_BLOCKER": evaluate_material_blocker(universe_row),
    }
    t2 = {
        "T2_IDENTITY": copy.deepcopy(criteria["IDENTITY"]),
        "T2_POPULATION_MEMBERSHIP": evaluate_t2_population_membership(universe_row, snapshot_date=snapshot_date),
        "T2_LIQUIDITY": evaluate_t2_liquidity(universe_row),
        "T2_PRICE_DATA": evaluate_t2_price_data(market, market_evidence_packet, reference_at=reference_at),
        "T2_ROTATION_MEMBERSHIP": (
            evaluate_t2_rotation_membership(canonical_asset_id) if rotation_confirmation is None
            else evaluate_t2_rotation_membership_confirmed(
                canonical_asset_id, rotation_confirmation, reference_at=reference_at,
            )
        ),
        "T2_REGIME_PERMITS_NEW_BUYS": copy.deepcopy(regime_criterion),
    }
    state, reason = aggregate_t2_state(t2)
    warnings = sorted(
        f"{name}:{role}:{criteria[name]['status']}:{criteria[name]['reason']}"
        for name, role in NON_BLOCKING_CRITERIA_V3.items() if criteria[name]["status"] != "PASS"
    )
    regime_role = "BLOCKED_BY" if t2["T2_REGIME_PERMITS_NEW_BUYS"]["status"] == "FAIL" else "APPLIED"
    refs = [
        _rule_ref(T2_RULE_ID, "BLOCKED_BY" if state == STATE_BLOCKED else "APPLIED"),
        _rule_ref(ENTRY_BASELINE_RULE_ID, "APPLIED"),
        _rule_ref(CRYPTO_RUNTIME_RULE_ID, regime_role),
        _rule_ref(ALLOCATION_V2_RULE_ID, regime_role),
    ]
    if rotation_confirmation is None:
        unapplied = [{"rule_id": ROTATION_T2_RULE_ID, "reason_code": ROTATION_NOT_WIRED_REASON}]
    else:
        unapplied = []
        refs.append(_rule_ref(
            ROTATION_T2_RULE_ID,
            "BLOCKED_BY" if t2["T2_ROTATION_MEMBERSHIP"]["status"] == "FAIL" else "APPLIED",
        ))
    rule_refs = sorted(refs, key=lambda item: (item["rule_id"], item["role"]))
    return {
        "market": market,
        "canonical_asset_id": canonical_asset_id,
        "p3_12_state": universe_row["state"],
        "t2_required_conditions": t2,
        "criteria": criteria,
        "warnings": warnings,
        "promotion_state": state,
        "promotion_reason": reason,
        "rule_refs": rule_refs,
        "unapplied_rules": unapplied,
        "authority": dict(_ROW_AUTHORITY),
    }


def build_promotion_packet(
    universe_packet: dict,
    regime_payload: dict,
    market_evidence_by_market: dict | None,
    leadership_output: dict | None,
    *,
    evaluation_as_of: str,
    contract_version: int = 2,
    crypto_runtime_decision: dict | None = None,
    rotation_confirmation: dict | None = None,
) -> dict:
    """Pure derivation over four already-built, already-timestamped
    upstream evidence packets. Deterministic: the same inputs always
    produce byte-identical output (no wall-clock or random value is read
    inside this function).

    ``contract_version=2`` (default) is the unchanged
    ``crypto_candidate_promotion_packet/2`` derivation. ``contract_version=3``
    additionally consumes ``crypto_runtime_decision`` (a
    ``crypto_paper_runtime_decision/1`` packet or ``None``) for REGIME,
    reads VOLUME_LIQUIDITY from the ratified P4-07 policy only, and emits
    ``crypto_candidate_promotion_packet/3``. The regime reference instant is
    the validated P1-CR-08 envelope's ``generated_at``.

    ``rotation_confirmation`` (contract/3 only, crypto PAPER wiring v2): a
    CRYPTO ``rotation_confirmation_packet/1`` whose as-of date is before the
    reference UTC day. When supplied, T2_ROTATION_MEMBERSHIP reads the
    ratified bucket state and the packet is retained under
    ``source_packets.rotation_confirmation``; when omitted the contract/3
    output is byte-identical to before (condition UNKNOWN, not wired).
    """
    if contract_version not in CONTRACT_VERSIONS or type(contract_version) is not int:
        raise CryptoCandidatePromotionError(f"CONTRACT_VERSION_UNSUPPORTED:{contract_version!r}")
    v3 = contract_version == 3
    if not v3 and crypto_runtime_decision is not None:
        raise CryptoCandidatePromotionError("CRYPTO_RUNTIME_DECISION_REQUIRES_CONTRACT_V3")
    if not v3 and rotation_confirmation is not None:
        raise CryptoCandidatePromotionError("ROTATION_CONFIRMATION_REQUIRES_CONTRACT_V3")
    contract_v3 = load_contract_v3() if v3 else None
    _parse_date(evaluation_as_of, "evaluation_as_of")
    universe_packet = _validate_universe_packet(universe_packet, evaluation_as_of)
    try:
        regime_payload = REGIME_OUTPUT_CONTRACT.validate_output(regime_payload)
    except REGIME_OUTPUT_CONTRACT.OutputContractError as exc:
        raise CryptoCandidatePromotionError(f"REGIME_PAYLOAD_INVALID:{exc}") from exc
    if regime_payload.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError("REGIME_PAYLOAD_MARKET_MISMATCH")
    regime_generated_at = _parse_utc(regime_payload.get("generated_at"), "regime.generated_at")
    evaluation_date = _parse_date(evaluation_as_of, "evaluation_as_of")
    evaluation_end = dt.datetime.combine(
        evaluation_date, dt.time.max, tzinfo=dt.timezone.utc
    )
    if regime_generated_at > evaluation_end:
        raise CryptoCandidatePromotionError("REGIME_PAYLOAD_FUTURE_DATED")
    if regime_generated_at.date() != evaluation_date:
        raise CryptoCandidatePromotionError("REGIME_PAYLOAD_DATE_MISMATCH")

    market_evidence_by_market = market_evidence_by_market or {}
    if not isinstance(market_evidence_by_market, dict):
        raise CryptoCandidatePromotionError("MARKET_EVIDENCE_MAP_INVALID")
    universe_markets = {row["market"] for row in universe_packet["markets"]}
    if any(not isinstance(market, str) or market not in universe_markets for market in market_evidence_by_market):
        raise CryptoCandidatePromotionError("MARKET_EVIDENCE_OUT_OF_UNIVERSE")
    if v3:
        ratified_policy, policy_unavailable_reason = load_ratified_market_evidence_policy(contract_v3)
        normalized_market_evidence = {
            market: _validate_market_evidence_packet(
                packet, market, evaluation_as_of, ratified_policy=ratified_policy,
            )
            for market, packet in sorted(market_evidence_by_market.items())
        }
        normalized_runtime = (
            _validate_crypto_runtime_decision(crypto_runtime_decision, contract_v3)
            if crypto_runtime_decision is not None else None
        )
        regime_criterion = evaluate_crypto_runtime_regime(
            normalized_runtime, reference_at=regime_payload["generated_at"],
        )
        normalized_rotation = (
            _validate_rotation_confirmation(rotation_confirmation, reference_at=regime_payload["generated_at"])
            if rotation_confirmation is not None else None
        )
    else:
        normalized_market_evidence = {
            market: _validate_market_evidence_packet(packet, market, evaluation_as_of)
            for market, packet in sorted(market_evidence_by_market.items())
        }
    normalized_leadership = (
        _validate_leadership_output(leadership_output, evaluation_as_of)
        if leadership_output is not None else None
    )

    rows = []
    for row in universe_packet.get("markets", []):
        if row.get("state") not in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
            continue
        if v3:
            rows.append(
                evaluate_candidate_v3(
                    row,
                    regime_criterion=regime_criterion,
                    market_evidence_packet=normalized_market_evidence.get(row["market"]),
                    leadership_output=normalized_leadership,
                    ratified_policy=ratified_policy,
                    policy_unavailable_reason=policy_unavailable_reason,
                    snapshot_date=universe_packet["snapshot_date"],
                    reference_at=regime_payload["generated_at"],
                    rotation_confirmation=normalized_rotation,
                )
            )
        else:
            rows.append(
                evaluate_candidate(
                    row,
                    regime_payload=regime_payload,
                    market_evidence_packet=normalized_market_evidence.get(row["market"]),
                    leadership_output=normalized_leadership,
                )
            )

    source_packets = {
        "universe": copy.deepcopy(universe_packet),
        "regime": copy.deepcopy(regime_payload),
        "market_evidence_by_market": copy.deepcopy(normalized_market_evidence),
        "leadership": copy.deepcopy(normalized_leadership),
    }
    if v3:
        source_packets["crypto_runtime_decision"] = copy.deepcopy(normalized_runtime)
        if normalized_rotation is not None:
            source_packets["rotation_confirmation"] = copy.deepcopy(normalized_rotation)
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION_V3 if v3 else OUTPUT_SCHEMA_VERSION,
        "contract_version": (contract_v3 if v3 else load_contract())["contract_version"],
        "evaluation_as_of": evaluation_as_of,
        "universe_snapshot_date": universe_packet.get("snapshot_date"),
        "universe_manifest_sha256": universe_packet.get("manifest_sha256"),
        "regime_contract_version": regime_payload.get("contract_version"),
        "regime_generated_at": regime_payload.get("generated_at"),
        "leadership_as_of_date": (leadership_output or {}).get("as_of_date"),
        "source_packets": source_packets,
        "candidates": rows,
        "summary": {
            "candidate_count": len(rows),
            "watch_count": sum(1 for r in rows if r["promotion_state"] == STATE_WATCH),
            "focused_review_count": sum(1 for r in rows if r["promotion_state"] == STATE_FOCUSED_REVIEW),
            "blocked_count": sum(1 for r in rows if r["promotion_state"] == STATE_BLOCKED),
        },
        "authority": dict(_ROW_AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_output(packet: dict) -> dict:
    """Re-validate embedded sources and reproduce the full derivation.

    Downstream P5-09 must call this function instead of trusting a cached
    state label or a caller-supplied subset of fields.
    """
    expected_keys = {
        "schema_version", "contract_version", "evaluation_as_of",
        "universe_snapshot_date", "universe_manifest_sha256",
        "regime_contract_version", "regime_generated_at", "leadership_as_of_date",
        "source_packets", "candidates", "summary", "authority", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoCandidatePromotionError("OUTPUT_SCHEMA_MISMATCH")
    schema_version = packet.get("schema_version")
    if schema_version == OUTPUT_SCHEMA_VERSION:
        contract_version = 2
        contract = load_contract()
    elif schema_version == OUTPUT_SCHEMA_VERSION_V3:
        contract_version = 3
        contract = load_contract_v3()
    else:
        raise CryptoCandidatePromotionError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    if packet.get("contract_version") != contract["contract_version"]:
        raise CryptoCandidatePromotionError("OUTPUT_CONTRACT_VERSION_MISMATCH")
    _validate_payload_hash(packet, "promotion_output")
    _require_false_authority(packet.get("authority"), _ROW_AUTHORITY, "promotion_output")
    sources = packet.get("source_packets")
    expected_sources = {"universe", "regime", "market_evidence_by_market", "leadership"}
    if contract_version == 3:
        expected_sources.add("crypto_runtime_decision")
        if isinstance(sources, dict) and "rotation_confirmation" in sources:
            expected_sources.add("rotation_confirmation")
    if not isinstance(sources, dict) or set(sources) != expected_sources:
        raise CryptoCandidatePromotionError("OUTPUT_SOURCE_PACKETS_INVALID")
    extra = (
        {"contract_version": 3, "crypto_runtime_decision": sources["crypto_runtime_decision"]}
        if contract_version == 3 else {}
    )
    if "rotation_confirmation" in expected_sources:
        extra["rotation_confirmation"] = sources["rotation_confirmation"]
    rebuilt = build_promotion_packet(
        sources["universe"],
        sources["regime"],
        sources["market_evidence_by_market"],
        sources["leadership"],
        evaluation_as_of=packet["evaluation_as_of"],
        **extra,
    )
    if canonical_json(rebuilt) != canonical_json(packet):
        raise CryptoCandidatePromotionError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)
