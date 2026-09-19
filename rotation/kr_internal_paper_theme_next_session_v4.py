#!/usr/bin/env python3
"""Bounded D-to-E next-session application, contract version 4.

This module is additive.  The version 3 module
``rotation/kr_internal_paper_theme_application.py``, its contract, and the
2026-09-08 adoption evidence are reused unmodified; nothing here edits them.

Version 4 implements user ratification KR-NEXT-SESSION-INPUT-POLICY-DBE-20260913
decision B for this application scope only: E identity and prospective
membership evidence may be either an exact E master (retained for replay and a
future deferred upgrade) or the latest published asset master actually
available at the evaluation instant, combined with ratified canonical listing
resolution at both the evaluation and forward execution instants and the
exact committed E calendar envelope.

The D context is admitted only from the next-session official daily sources
pinned in the contract.  Any other D source yields UNKNOWN unconditionally;
there is no code path that admits another D source.

Timing is bounded only by actual availability and the E regular close:
every input must be available at or before evaluation, evaluation and forward
execution must fall on the E local date and before the E close.  No numeric
age window exists.  All synthetic fixtures used by tests are never market
evidence, and nothing here authorizes an entry, order, or REAL authority.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from identity import canonical_identity as CI
from rotation import theme_taxonomy_authority as TTA


BASE_APPLICATION_RELATIVE = "rotation/kr_internal_paper_theme_application.py"
CONTRACT_V4_PATH = ROOT / "config" / "kr_internal_paper_theme_next_session_contract_v4.json"
NEXT_SESSION_CONTRACT_V4_SCHEMA = "kr_internal_paper_theme_next_session_contract/4"
NEXT_SESSION_OUTPUT_V4_SCHEMA = "kr_internal_paper_theme_next_session_application/4"
AMENDMENT_SCHEMA = "kr_internal_paper_previous_completed_session_context_amendment/1"
IDENTITY_MODE_EXACT_E_MASTER = "EXACT_E_MASTER"
IDENTITY_MODE_LATEST_PUBLISHED = "LATEST_PUBLISHED_MASTER_WITH_E_LISTING_RESOLUTION"
CONTEXT_MODE_OFFICIAL = "NEXT_SESSION_OFFICIAL_DAILY"
KRX_GLOBAL_UNIVERSE_DIR = "data/observations/krx_global_universe"
PUBLISHED_PACKET_RE = re.compile(
    r"^data/observations/krx_global_universe/(\d{4}-\d{2}-\d{2})/packet\.json$"
)
LISTING_SOURCE_NAME = "krx_open_api_stock_daily"
TARGET_IDENTITIES = {
    "KR:XKRX:000660": ("000660", "KRX:000660:COMMON", "DART:00164779", "XKRX:000660"),
    "KR:XKRX:005930": ("005930", "KRX:005930:COMMON", "DART:00126380", "XKRX:005930"),
}

STATUS_PRIORITY = (
    "CONTEXT_POST_CLOSE_SOURCE_NOT_ADMITTED",
    "EXECUTION_SESSION_DATE_NOT_EVALUATION_LOCAL_DATE",
    "E_IDENTITY_MASTER_AVAILABLE_AFTER_EVALUATION",
    "E_IDENTITY_MASTER_AS_OF_AFTER_CONTEXT_SESSION",
    "E_IDENTITY_MASTER_NEWER_AVAILABLE_PACKET_INVALID",
    "E_IDENTITY_MASTER_NOT_LATEST_AVAILABLE",
    "E_IDENTITY_TARGET_NOT_ACTIVE_KOSPI",
    "E_LISTING_NOT_RESOLVED_AT_EVALUATION",
    "E_LISTING_NOT_RESOLVED_AT_FORWARD_EXECUTION",
    "E_LISTING_IDENTITY_MISMATCH",
)


def _load_base_application():
    """Load the unmodified version 3 module from this module's own root.

    Loading by exact file location keeps a polluted ``sys.path`` from mixing
    another checkout's implementation into a trusted-root evaluation.
    """
    path = ROOT / BASE_APPLICATION_RELATIVE
    spec = importlib.util.spec_from_file_location(
        "kr_internal_paper_theme_next_session_v4_base_application", path
    )
    if spec is None or spec.loader is None:
        raise ValueError("V4_BASE_MODULE_ORIGIN_INVALID")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected_files = {
        module: path,
        CI: ROOT / "identity" / "canonical_identity.py",
        TTA: ROOT / "rotation" / "theme_taxonomy_authority.py",
        module.KCR: ROOT / "rotation" / "korea_capital_rotation.py",
    }
    if (
        module.ROOT != ROOT
        or module.CI is not CI
        or module.TTA is not TTA
        or any(
            Path(item.__file__).resolve() != expected.resolve()
            for item, expected in expected_files.items()
        )
    ):
        raise module.ThemeApplicationError("V4_BASE_MODULE_ORIGIN_INVALID")
    return module


APP = _load_base_application()
ThemeApplicationError = APP.ThemeApplicationError
KST = APP.KST


def _utc_text(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _expected_next_session_contract_v4() -> dict:
    return {
        "schema_version": NEXT_SESSION_CONTRACT_V4_SCHEMA,
        "decision_id": "KR_INTERNAL_PAPER_PREVIOUS_COMPLETED_SESSION_CONTEXT_V2",
        "application_scope": "KR_INTERNAL_PAPER_BASELINE_V0_ENTRY_FILTER",
        "base_profile": {
            "proposal_id": "KR_INTERNAL_PAPER_TWO_STOCK_THEME_APPLICATION_CONTRACT_V1",
            "commit": "d14b17a1c33beb1e25bfc5a9921e588ddf8e5971",
            "contract_path": "config/kr_internal_paper_theme_application_contract.json",
            "contract_sha256": "77453d7c6637b0a5b1538b8587e557ad3f5998c39e68527d1158121ec24d0472",
        },
        "predecessor_contract": {
            "schema_version": "kr_internal_paper_theme_next_session_contract/3",
            "contract_path": "config/kr_internal_paper_theme_next_session_contract.json",
            "contract_sha256": "717fea4b6e7fca24e1cba607bd330f8455aa4777727cb0eb7a46edacd6e7c602",
            "retained_unmodified_for_replay": True,
        },
        "decision_evidence": {
            "path": "evidence/authority/kr_internal_paper_previous_completed_session_context_amendment_20260913.json",
            "sha256": "b318ce526801278a5eb6b77fe37411cc8ee204594d96f6be4cfa2674d55ee470",
        },
        "predecessor_decision_evidence": {
            "path": "evidence/authority/kr_internal_paper_previous_completed_session_context_adoption_20260908.json",
            "sha256": "2571be782cb70473aaba49ed6c6a2fc0e67cd6f6a5a1af3d8ec66433aec5022b",
            "amended_rule_indices": [3],
        },
        "user_ratification": {
            "ratification_id": "KR-NEXT-SESSION-INPUT-POLICY-DBE-20260913",
            "ratified_at_utc": "2026-09-13T12:40:00Z",
            "ratification_sha256": "c18d397994324f09e95a0ba17baae5a722448d595ad1d401e1fb2536f0b0e3b5",
            "adopted_decisions": ["B_e_identity_substitute", "E_stage3_population_carry"],
            "conditional_decisions_not_activated": ["D_same_day_post_close_source"],
            "not_adopted_decisions": ["A_relax_D_definition"],
            "deferred_decisions": ["C_pre_open_E_master_source"],
        },
        "session_boundary": {
            "required_proof": "independently verified calendar proves D is the immediately previous OPEN_REGULAR session before E",
            "calendar_validator": "market_data/krx_session_calendar_v2.py::validate_calendar",
            "calendar_validator_sha256": "6660af3f9d12cc1ca8f0d417186fad0c6919ef16ad6d05973ae3ab8104f117ca",
            "calendar_contract": "config/krx_session_calendar_sources_v2.json",
            "calendar_contract_sha256": "58698d8d8045c9e084f250bb37becc667cfb3161073b6789af22cacc9456e813",
            "calendar_source_schema_version": "krx_date_specific_session_source/1",
            "calendar_coverage": "exact committed approved-source snapshot for every calendar date D through E; D and E OPEN_REGULAR; every intervening date CLOSED",
            "post_close_market_signals_relation_required": False,
            "calendar_day_subtraction_authorized": False,
            "d_minus_two_fallback_authorized": False,
        },
        "context_session": {
            "label": "PREVIOUS_COMPLETED_SESSION_CONTEXT",
            "required_same_date_inputs": ["D master", "D leadership"],
            "must_be_available_before_evaluation": True,
            "d_price_as_execution_fill_authorized": False,
            "context_source_modes": {
                "NEXT_SESSION_OFFICIAL_DAILY": {
                    "admitted": True,
                    "master_source_ids": ["krx_open_api_stock_daily"],
                    "leadership_source_names": ["KRX_OPEN_API_INDEX_LIVE"],
                },
                "SAME_DAY_POST_CLOSE": {
                    "ratified_decision": "D_same_day_post_close_source=ADOPT_CONDITIONAL",
                    "condition_precedent_result": "V1_FAILED",
                    "v1_validation_sha256": "41e99cb8ef812690c72b0d325ccc76c3bae480b7f235095579882f4d4ea0d8b2",
                    "status": "NOT_ACTIVATED_RETURNED_TO_USER",
                    "post_close_source_admitted": False,
                    "validator_activation_branch_present": False,
                    "unadmitted_behavior": "UNKNOWN_CONTEXT_POST_CLOSE_SOURCE_NOT_ADMITTED",
                },
            },
        },
        "execution_session": {
            "identity_evidence_modes": {
                "EXACT_E_MASTER": {
                    "admitted": True,
                    "required_same_date_inputs": [
                        "E master", "E candidate identity", "E prospective application membership",
                    ],
                },
                "LATEST_PUBLISHED_MASTER_WITH_E_LISTING_RESOLUTION": {
                    "admitted": True,
                    "ratified_decision": "B_e_identity_substitute=ADOPT",
                    "identity_master": {
                        "packet_validator": ".github/scripts/korea_global_universe_populate.py::validate_packet",
                        "published_path_pattern": "data/observations/krx_global_universe/<as_of_date>/packet.json",
                        "exact_committed_bytes_at_trusted_commit": True,
                        "available_at_rule": "max(git first_seen of exact bytes, every source_snapshots[].retrieved_at_utc)",
                        "availability_predicate": "available_at <= evaluation_at",
                        "selection_rule": "greatest as_of_date among exact committed packets at trusted_commit whose available_at <= evaluation_at",
                        "as_of_date_predicate": "as_of_date <= D",
                        "newer_available_packet_invalid_behavior": "FAIL_CLOSED",
                        "maximum_age_authorized": None,
                        "target_row_predicate": "market KOREA, asset_class EQUITY, exact primary_symbol, active UNIVERSE=KOSPI membership at the master's own as_of_date",
                    },
                    "e_canonical_listing_resolution": {
                        "resolver": "identity/canonical_identity.py::resolve_instrument_identity",
                        "authority": "config/canonical_security_identity.json verified at trusted_commit",
                        "source_name": "krx_open_api_stock_daily",
                        "market": "KOREA",
                        "decision_instants": ["evaluation_at", "forward_execution_at"],
                        "instant_format": "UTC YYYY-MM-DDTHH:MM:SSZ floored to whole seconds",
                        "required_status": "RESOLVED",
                        "exact_ids": ["canonical_instrument_id", "canonical_issuer_id", "listing_id"],
                        "listing_row_uniquely_active_with_exact_ticker_and_market": True,
                    },
                    "e_calendar_predicate": "E envelope exact committed, OPEN_REGULAR, available_at and first_seen <= evaluation_at",
                    "execution_session_date_rule": "E = Asia/Seoul local date of evaluation_at; forward_execution_at local date == E; last session calendar envelope as_of_date == E",
                    "master_effective_interval_extended": False,
                    "scope": "E identity and prospective membership evidence for this application_scope only",
                },
            },
            "market_timezone": "Asia/Seoul",
            "regular_session_close_local": "15:30:00",
            "decision_to_forward_execution_max_seconds": 600,
            "active_predicate": "membership_from <= evaluation_at <= forward_execution_at < E_regular_session_close",
            "e_plus_one_carry_authorized": False,
        },
        "membership": {
            "membership_from_rule": "max(E 00:00:00 KST, source_admission_real_usable_from, D/E_decision_real_usable_from)",
            "membership_to_rule": "E 15:30:00 KST",
            "d_master_interval_extension_authorized": False,
            "backdating_authorized": False,
        },
        "separate_required_inputs": [
            "Exact D rotation TOP bucket",
            "Qualified current E price/spread/impact and causal execution observations",
            "Actual E tradability evidence",
            "Any separately required E-day regime state",
        ],
        "authority": {
            "previous_completed_session_context_input_only": True,
            "baseline_entry_eligibility_authorized": False,
            "new_entry_authorized": False,
            "regime_gate_authorized": False,
            "global_taxonomy_authority_changed": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "real_authority": False,
            "order_authorized": False,
            "trading_authorized": False,
            "profitability_claimed": False,
        },
    }


def load_next_session_contract_v4(path: Path | None = None) -> dict:
    value = APP._read_json(Path(CONTRACT_V4_PATH if path is None else path))[1]
    if value != _expected_next_session_contract_v4():
        raise ThemeApplicationError("NEXT_SESSION_CONTRACT_V4_MISMATCH")
    if value["session_boundary"] != APP._expected_next_session_contract()["session_boundary"]:
        raise ThemeApplicationError("NEXT_SESSION_V4_CALENDAR_BINDING_DIVERGED")
    return copy.deepcopy(value)


def _verify_predecessor_pins(repo: Path, commit: str, contract: dict) -> tuple[dict, str]:
    """Require the version 3 contract and the 0908 evidence to be byte-pinned."""
    predecessor = contract["predecessor_contract"]
    predecessor_path = repo / predecessor["contract_path"]
    try:
        predecessor_raw = predecessor_path.read_bytes()
    except OSError as exc:
        raise ThemeApplicationError("NEXT_SESSION_PREDECESSOR_CONTRACT_PIN_MISMATCH") from exc
    if (
        APP.sha256_bytes(predecessor_raw) != predecessor["contract_sha256"]
        or TTA._git_blob(repo, commit, predecessor["contract_path"]) != predecessor_raw
    ):
        raise ThemeApplicationError("NEXT_SESSION_PREDECESSOR_CONTRACT_PIN_MISMATCH")

    evidence_pin = contract["predecessor_decision_evidence"]
    evidence_path = repo / evidence_pin["path"]
    try:
        evidence_raw = evidence_path.read_bytes()
        evidence = json.loads(evidence_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ThemeApplicationError("NEXT_SESSION_PREDECESSOR_DECISION_PIN_MISMATCH") from exc
    if (
        not isinstance(evidence, dict)
        or APP.sha256_bytes(evidence_raw) != evidence_pin["sha256"]
        or TTA._git_blob(repo, commit, evidence_pin["path"]) != evidence_raw
    ):
        raise ThemeApplicationError("NEXT_SESSION_PREDECESSOR_DECISION_PIN_MISMATCH")
    first_seen = TTA._first_seen_exact_bytes(repo, commit, evidence_pin["path"], evidence_raw)
    if first_seen is None:
        raise ThemeApplicationError("NEXT_SESSION_PREDECESSOR_DECISION_PIN_MISMATCH")
    return evidence, first_seen


def _validate_amendment_document(amendment: dict, contract: dict, predecessor_evidence: dict) -> dt.datetime:
    """Check the amendment record fields; return its recorded instant."""
    ratification = contract["user_ratification"]
    predecessor_pin = contract["predecessor_decision_evidence"]
    amends = amendment.get("amends") if isinstance(amendment, dict) else None
    recorded_ratification = amendment.get("user_ratification") if isinstance(amendment, dict) else None
    amendment_contract = amendment.get("contract") if isinstance(amendment, dict) else None
    implementation = amendment.get("implementation") if isinstance(amendment, dict) else None
    rules = predecessor_evidence.get("rules")
    if (
        not isinstance(amendment, dict)
        or not isinstance(amends, dict)
        or not isinstance(recorded_ratification, dict)
        or not isinstance(amendment_contract, dict)
        or not isinstance(implementation, dict)
        or not isinstance(rules, list)
        or len(rules) <= 3
    ):
        raise ThemeApplicationError("NEXT_SESSION_AMENDMENT_EVIDENCE_MISMATCH")
    before = amends.get("rules_3_before")
    after = amends.get("rules_3_after")
    if (
        amendment.get("schema_version") != AMENDMENT_SCHEMA
        or amendment.get("decision_id") != contract["decision_id"]
        or amendment.get("application_scope") != contract["application_scope"]
        or amendment.get("status") != "ADOPTED_UNVALIDATED_INTERNAL_PAPER_HYPOTHESIS"
        or amends.get("decision_id") != predecessor_evidence.get("decision_id")
        or amends.get("path") != predecessor_pin["path"]
        or amends.get("sha256") != predecessor_pin["sha256"]
        or amends.get("amended_rule_indices") != [3]
        or predecessor_pin["amended_rule_indices"] != [3]
        or amends.get("predecessor_file_modified") is not False
        or not isinstance(before, str)
        or before != rules[3]
        or not isinstance(after, str)
        or not after.startswith(before)
        or after == before
        or recorded_ratification.get("ratification_id") != ratification["ratification_id"]
        or recorded_ratification.get("ratification_sha256") != ratification["ratification_sha256"]
        or recorded_ratification.get("ratified_at_utc") != ratification["ratified_at_utc"]
        or amendment_contract.get("schema_version") != contract["schema_version"]
        or amendment_contract.get("predecessor_contract_sha256")
        != contract["predecessor_contract"]["contract_sha256"]
        or implementation.get("predecessor_module_modified") is not False
        or amendment.get("authority_changes") != {
            "real": False,
            "production": False,
            "global_taxonomy": False,
            "profitability_claim": False,
        }
    ):
        raise ThemeApplicationError("NEXT_SESSION_AMENDMENT_EVIDENCE_MISMATCH")
    recorded = APP._timestamp(
        amendment.get("recorded_after_decision_at_utc"),
        "NEXT_SESSION_AMENDMENT_TIME_INVALID",
    )
    ratified = APP._timestamp(ratification["ratified_at_utc"], "NEXT_SESSION_RATIFICATION_TIME_INVALID")
    if recorded < ratified:
        raise ThemeApplicationError("NEXT_SESSION_AMENDMENT_BACKDATED")
    return recorded


def resolve_next_session_decision_v4(trusted_commit: str, contract_path: Path | None = None) -> dict:
    path = Path(CONTRACT_V4_PATH if contract_path is None else contract_path)
    contract_raw, contract = APP._read_json(path)
    if contract != _expected_next_session_contract_v4():
        raise ThemeApplicationError("NEXT_SESSION_CONTRACT_V4_MISMATCH")
    if contract["session_boundary"] != APP._expected_next_session_contract()["session_boundary"]:
        raise ThemeApplicationError("NEXT_SESSION_V4_CALENDAR_BINDING_DIVERGED")
    repo, commit = APP._repo_and_commit(path, trusted_commit)
    contract_first_seen = APP._require_exact_committed_bytes(
        repo, commit, path, contract_raw,
        "NEXT_SESSION_CONTRACT_V4_NOT_EXACT_COMMITTED_BYTES",
    )
    base = contract["base_profile"]
    try:
        base_raw = (repo / base["contract_path"]).read_bytes()
    except OSError as exc:
        raise ThemeApplicationError("NEXT_SESSION_BASE_PROFILE_PIN_MISMATCH") from exc
    if (
        APP.sha256_bytes(base_raw) != base["contract_sha256"]
        or TTA._git_blob(repo, base["commit"], base["contract_path"]) != base_raw
        or TTA._git_blob(repo, commit, base["contract_path"]) != base_raw
    ):
        raise ThemeApplicationError("NEXT_SESSION_BASE_PROFILE_PIN_MISMATCH")

    predecessor_evidence, predecessor_first_seen = _verify_predecessor_pins(repo, commit, contract)

    amendment_path = repo / contract["decision_evidence"]["path"]
    amendment_raw, amendment = APP._read_json(amendment_path)
    if APP.sha256_bytes(amendment_raw) != contract["decision_evidence"]["sha256"]:
        raise ThemeApplicationError("NEXT_SESSION_AMENDMENT_EVIDENCE_SHA_MISMATCH")
    amendment_first_seen = APP._require_exact_committed_bytes(
        repo, commit, amendment_path, amendment_raw,
        "NEXT_SESSION_AMENDMENT_EVIDENCE_NOT_EXACT_COMMITTED_BYTES",
    )
    recorded = _validate_amendment_document(amendment, contract, predecessor_evidence)
    usable = max(
        recorded,
        APP._timestamp(amendment_first_seen, "NEXT_SESSION_AMENDMENT_FIRST_SEEN_INVALID"),
        APP._timestamp(contract_first_seen, "NEXT_SESSION_CONTRACT_V4_FIRST_SEEN_INVALID"),
        APP._timestamp(predecessor_first_seen, "NEXT_SESSION_PREDECESSOR_FIRST_SEEN_INVALID"),
    )
    return {
        "status": "ADOPTED_EXACT_D_TO_E_SCOPE_V2",
        "decision_id": contract["decision_id"],
        "contract_first_seen_at": contract_first_seen,
        "amendment_evidence_first_seen_at": amendment_first_seen,
        "predecessor_decision_evidence_first_seen_at": predecessor_first_seen,
        "user_ratification_id": contract["user_ratification"]["ratification_id"],
        "decision_real_usable_from": _utc_text(usable),
        "authority": dict(APP.NEXT_SESSION_AUTHORITY),
    }


def _population_module():
    return APP._load_module(
        "kr_internal_paper_next_session_v4_global_universe_population",
        ".github/scripts/korea_global_universe_populate.py",
    )


def _identity_evidence_mode(value) -> tuple[str, Path]:
    if isinstance(value, dict):
        mode = value.get("mode")
        field = {
            IDENTITY_MODE_EXACT_E_MASTER: "executionMasterPacketPath",
            IDENTITY_MODE_LATEST_PUBLISHED: "identityMasterPacketPath",
        }.get(mode)
        if (
            field is not None
            and set(value) == {"mode", field}
            and isinstance(value[field], (str, Path))
            and str(value[field])
        ):
            return mode, Path(value[field])
    raise ThemeApplicationError("E_IDENTITY_EVIDENCE_MODE_INVALID")


def _context_source_is_official_daily(d_master: dict, d_leadership_wrapper: dict, contract: dict) -> str | None:
    """Return None only when every D source is the pinned official daily source."""
    official = contract["context_session"]["context_source_modes"][CONTEXT_MODE_OFFICIAL]
    source_ids: set = set()

    def collect(value) -> None:
        if isinstance(value, dict):
            identity = value.get("source_identity")
            if isinstance(identity, dict):
                source_ids.add(identity.get("source_id"))
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(d_master["asset_master"]["records"])
    leadership_packet = d_leadership_wrapper.get("leadership_packet")
    policy = leadership_packet.get("policy") if isinstance(leadership_packet, dict) else None
    leadership_source = policy.get("source_name") if isinstance(policy, dict) else None
    if (
        official["admitted"] is True
        and source_ids
        and source_ids <= set(official["master_source_ids"])
        and leadership_source in official["leadership_source_names"]
    ):
        return None
    return "CONTEXT_POST_CLOSE_SOURCE_NOT_ADMITTED"


def _packet_available_at(packet: dict, first_seen: str) -> dt.datetime:
    return APP._master_latest_available_at(packet, first_seen)


def _select_latest_available_identity_master(
    repo: Path,
    commit: str,
    evaluation: dt.datetime,
    context_date: str,
    population=None,
) -> tuple[dict | None, str | None]:
    """Select the latest published master actually available at evaluation.

    Only the trusted commit tree is listed; worktree files are never read.
    Directories dated after D are not opened.  A packet not yet available is
    skipped (it did not exist at evaluation).  An available packet that fails
    validation stops the search: the result fails closed instead of silently
    descending to an older packet.  There is no age limit.
    """
    population = _population_module() if population is None else population
    context_day = APP._date(context_date, "CONTEXT_SESSION_DATE_INVALID")
    listing = TTA._run_git(repo, "ls-tree", "-r", "--name-only", commit, "--", KRX_GLOBAL_UNIVERSE_DIR)
    candidates = []
    for relative in ([] if not listing else listing.splitlines()):
        match = PUBLISHED_PACKET_RE.fullmatch(relative)
        if match is None:
            continue
        try:
            folder_day = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if folder_day.isoformat() != match.group(1) or folder_day > context_day:
            continue
        candidates.append((folder_day, relative))

    for folder_day, relative in sorted(candidates, reverse=True):
        raw = TTA._git_blob(repo, commit, relative)
        first_seen = None if raw is None else TTA._first_seen_exact_bytes(repo, commit, relative, raw)
        if raw is None or first_seen is None:
            return None, "E_IDENTITY_MASTER_NEWER_AVAILABLE_PACKET_INVALID"
        first_seen_at = APP._timestamp(first_seen, "E_IDENTITY_MASTER_FIRST_SEEN_INVALID")
        if first_seen_at > evaluation:
            continue
        try:
            packet = json.loads(raw.decode("utf-8"))
            master = population.validate_packet(packet)
            if master["as_of_date"] != folder_day.isoformat():
                raise ValueError("FOLDER_DATE_MISMATCH")
            available_at = _packet_available_at(master, first_seen)
        except (
            UnicodeDecodeError, json.JSONDecodeError, ValueError,
            KeyError, TypeError, population.PopulationError,
        ):
            return None, "E_IDENTITY_MASTER_NEWER_AVAILABLE_PACKET_INVALID"
        if available_at > evaluation:
            continue
        return {
            "as_of_date": master["as_of_date"],
            "path": relative,
            "payload_sha256": master["payload_sha256"],
            "first_seen": first_seen,
            "available_at": _utc_text(available_at),
        }, None
    return None, "E_IDENTITY_MASTER_NOT_LATEST_AVAILABLE"


def _identity_master_evidence(
    identity_master_packet_path: Path,
    repo: Path,
    commit: str,
    evaluation: dt.datetime,
    context_date: str,
    population=None,
) -> tuple[dict, dict, str | None]:
    """Verify the caller-specified identity master is the latest available one."""
    population = _population_module() if population is None else population
    path = Path(identity_master_packet_path)
    if not path.is_absolute():
        path = repo / path
    packet, first_seen = APP._load_exact_packet(
        path, repo, commit, "E_IDENTITY_MASTER_NOT_EXACT_COMMITTED_BYTES"
    )
    try:
        master = population.validate_packet(packet)
    except population.PopulationError as exc:
        raise ThemeApplicationError(f"E_IDENTITY_MASTER_INVALID:{exc}") from exc
    available_at = _packet_available_at(master, first_seen)
    evidence = {
        "identity_master_as_of_date": master["as_of_date"],
        "identity_master_payload_sha256": master["payload_sha256"],
        "identity_master_first_seen_at": first_seen,
        "identity_master_available_at": _utc_text(available_at),
    }
    if available_at > evaluation:
        return master, evidence, "E_IDENTITY_MASTER_AVAILABLE_AFTER_EVALUATION"
    if master["as_of_date"] > context_date:
        return master, evidence, "E_IDENTITY_MASTER_AS_OF_AFTER_CONTEXT_SESSION"
    selected, reason = _select_latest_available_identity_master(
        repo, commit, evaluation, context_date, population
    )
    if reason is not None:
        return master, evidence, reason
    if (
        selected["payload_sha256"] != master["payload_sha256"]
        or selected["path"] != TTA._relative(repo, path)
    ):
        return master, evidence, "E_IDENTITY_MASTER_NOT_LATEST_AVAILABLE"
    return master, evidence, None


def _whole_second_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _basis_times(value) -> list[dt.datetime]:
    times = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"verified_row_first_seen_at", "verified_evidence_first_seen_at", "ratified_at"} and isinstance(item, str):
                try:
                    times.append(CI._parse_temporal(item)[0])
                except CI.IdentityError as exc:
                    raise ThemeApplicationError("E_LISTING_IDENTITY_BASIS_TIME_INVALID") from exc
            else:
                times.extend(_basis_times(item))
    return times


def _e_identities_by_listing_resolution(
    identity_master: dict,
    evaluation: dt.datetime,
    execution: dt.datetime,
    commit: str,
) -> tuple[list[dict], list[dict], dt.datetime | None, str | None, str | None]:
    """Resolve both target listings at the evaluation and forward execution instants."""
    rows = {row["asset_id"]: row for row in identity_master["asset_master"]["records"]}
    for asset_id in sorted(TARGET_IDENTITIES):
        row = rows.get(asset_id)
        ticker = TARGET_IDENTITIES[asset_id][0]
        if (
            row is None
            or row.get("market") != "KOREA"
            or row.get("asset_class") != "EQUITY"
            or row.get("primary_symbol") != ticker
            or not any(
                membership.get("membership_type") == "UNIVERSE"
                and membership.get("membership_id") == "KOSPI"
                for membership in row.get("active_memberships", [])
            )
        ):
            return [], [], None, "E_IDENTITY_TARGET_NOT_ACTIVE_KOSPI", asset_id

    authority = CI.load_authority()
    instants = (
        ("EVALUATION", _whole_second_utc(evaluation), "E_LISTING_NOT_RESOLVED_AT_EVALUATION"),
        ("FORWARD_EXECUTION", _whole_second_utc(execution), "E_LISTING_NOT_RESOLVED_AT_FORWARD_EXECUTION"),
    )
    identities = []
    receipts = []
    reasons: dict[str, str] = {}
    basis_times: list[dt.datetime] = []
    for asset_id in sorted(TARGET_IDENTITIES):
        ticker, instrument_id, issuer_id, listing_id = TARGET_IDENTITIES[asset_id]
        for role, instant, unresolved_code in instants:
            try:
                resolved = CI.resolve_instrument_identity(
                    LISTING_SOURCE_NAME, ticker, "KOREA", instant,
                    authority, trusted_commit=commit,
                )
            except CI.IdentityError as exc:
                raise ThemeApplicationError(f"E_LISTING_RESOLVER_ERROR:{exc}") from exc
            receipts.append({
                "asset_id": asset_id,
                "instant_role": role,
                "instant": instant,
                "status": resolved.get("status"),
                "listing_id": resolved.get("listing_id"),
            })
            if resolved.get("status") != CI.RESOLVED:
                reasons.setdefault(unresolved_code, asset_id)
                continue
            if (
                resolved.get("canonical_instrument_id") != instrument_id
                or resolved.get("canonical_issuer_id") != issuer_id
                or resolved.get("listing_id") != listing_id
            ):
                reasons.setdefault("E_LISTING_IDENTITY_MISMATCH", asset_id)
                continue
            try:
                active_rows = [
                    listing for listing in authority.get("listings", [])
                    if listing.get("listing_id") == listing_id and CI._row_active(listing, instant)
                ]
            except CI.IdentityError:
                active_rows = []
            if (
                len(active_rows) != 1
                or active_rows[0].get("ticker") != ticker
                or active_rows[0].get("market") != "KOREA"
            ):
                reasons.setdefault("E_LISTING_IDENTITY_MISMATCH", asset_id)
                continue
            basis_times.extend(_basis_times(resolved.get("identity_basis")))
        identities.append({
            "asset_id": asset_id,
            "canonical_instrument_id": instrument_id,
            "canonical_issuer_id": issuer_id,
            "listing_id": listing_id,
        })
    for code in STATUS_PRIORITY:
        if code in reasons:
            return [], receipts, None, code, reasons[code]
    identity_available_at = max(basis_times) if basis_times else None
    if identity_available_at is None:
        raise ThemeApplicationError("E_LISTING_IDENTITY_BASIS_TIME_INVALID")
    return identities, receipts, identity_available_at, None, None


def evaluate_next_session_application_v4(
    context_master_packet_path: Path,
    context_leadership_packet_path: Path,
    execution_identity_evidence: dict,
    session_calendar_packet_paths: list[Path],
    evaluation_at: str,
    forward_execution_at: str,
    trusted_commit: str,
) -> dict:
    """Validate D context for only the immediately following execution session E.

    The function never subtracts calendar days, extends the D master interval,
    carries D into E+1, or authorizes an entry.  It emits a bounded input only.
    """
    mode, identity_packet_path = _identity_evidence_mode(execution_identity_evidence)
    admission = APP.resolve_source_admission(trusted_commit)
    decision = resolve_next_session_decision_v4(trusted_commit)
    contract = load_next_session_contract_v4()
    if contract["execution_session"]["identity_evidence_modes"][mode]["admitted"] is not True:
        raise ThemeApplicationError("E_IDENTITY_EVIDENCE_MODE_INVALID")
    repo, commit = APP._repo_and_commit(CONTRACT_V4_PATH, trusted_commit)
    evaluation = APP._timestamp(evaluation_at, "NEXT_SESSION_EVALUATION_AT_INVALID")
    execution = APP._timestamp(forward_execution_at, "NEXT_SESSION_FORWARD_EXECUTION_AT_INVALID")

    d_master_raw, d_master_first_seen = APP._load_exact_packet(
        Path(context_master_packet_path), repo, commit,
        "CONTEXT_MASTER_NOT_EXACT_COMMITTED_BYTES",
    )
    d_leadership_wrapper, d_leadership_first_seen = APP._load_exact_packet(
        Path(context_leadership_packet_path), repo, commit,
        "CONTEXT_LEADERSHIP_NOT_EXACT_COMMITTED_BYTES",
    )
    population = _population_module()
    e_master = None
    e_master_first_seen = None
    if mode == IDENTITY_MODE_EXACT_E_MASTER:
        e_master_raw, e_master_first_seen = APP._load_exact_packet(
            identity_packet_path, repo, commit,
            "EXECUTION_MASTER_NOT_EXACT_COMMITTED_BYTES",
        )
    try:
        d_master = population.validate_packet(d_master_raw)
        if mode == IDENTITY_MODE_EXACT_E_MASTER:
            e_master = population.validate_packet(e_master_raw)
    except population.PopulationError as exc:
        raise ThemeApplicationError(f"NEXT_SESSION_MASTER_INVALID:{exc}") from exc
    d_leadership = APP._validate_leadership_wrapper(d_leadership_wrapper)
    context_date = d_master["as_of_date"]
    if d_leadership_wrapper["observation_date"] != context_date:
        raise ThemeApplicationError("CONTEXT_SAME_DATE_MISMATCH")

    if mode == IDENTITY_MODE_EXACT_E_MASTER:
        execution_date = e_master["as_of_date"]
    else:
        execution_date = evaluation.astimezone(KST).date().isoformat()

    date_reason = None
    if (
        evaluation.astimezone(KST).date().isoformat() != execution_date
        or execution.astimezone(KST).date().isoformat() != execution_date
    ):
        date_reason = "EXECUTION_SESSION_DATE_NOT_EVALUATION_LOCAL_DATE"
    elif isinstance(session_calendar_packet_paths, list) and session_calendar_packet_paths:
        last_path = Path(session_calendar_packet_paths[-1])
        if not last_path.is_absolute():
            last_path = repo / last_path
        try:
            last_envelope, _ = APP._load_exact_packet(
                last_path, repo, commit, "SESSION_CALENDAR_NOT_EXACT_COMMITTED_BYTES"
            )
        except ThemeApplicationError:
            last_envelope = None
        if last_envelope is not None and last_envelope.get("as_of_date") != execution_date:
            date_reason = "EXECUTION_SESSION_DATE_NOT_EVALUATION_LOCAL_DATE"

    session_calendar = None
    session_calendar_reason = None
    if date_reason is None:
        try:
            session_calendar = APP.verify_immediate_session_calendar(
                session_calendar_packet_paths,
                context_date,
                execution_date,
                evaluation_at,
                repo,
                commit,
            )
        except ThemeApplicationError as exc:
            session_calendar_reason = str(exc).split(":", 1)[0]

    context_mode_reason = _context_source_is_official_daily(d_master, d_leadership_wrapper, contract)
    context_identities = APP._target_identities_for_session(d_master, context_date, commit)

    identity_reason = None
    identity_detail = None
    identity_available_at = None
    listing_receipts: list[dict] = []
    identity_evidence = {
        "identity_master_as_of_date": None,
        "identity_master_payload_sha256": None,
        "identity_master_first_seen_at": None,
        "identity_master_available_at": None,
    }
    execution_availability = []
    if mode == IDENTITY_MODE_EXACT_E_MASTER:
        execution_identities = APP._target_identities_for_session(e_master, execution_date, commit)
        execution_availability.append(APP._master_latest_available_at(e_master, e_master_first_seen))
    else:
        identity_master, identity_evidence, identity_reason = _identity_master_evidence(
            identity_packet_path, repo, commit, evaluation, context_date, population
        )
        execution_availability.append(
            APP._timestamp(identity_evidence["identity_master_available_at"], "E_IDENTITY_MASTER_AVAILABLE_AT_INVALID")
        )
        execution_identities = []
        if identity_reason is None:
            (
                execution_identities, listing_receipts, identity_available_at,
                identity_reason, identity_detail,
            ) = _e_identities_by_listing_resolution(identity_master, evaluation, execution, commit)
            if identity_available_at is not None:
                execution_availability.append(identity_available_at)

    series = d_leadership["rows"].get(admission["rotation_series_identity"])
    if series is None or series["role"] not in {"SECTOR", "THEME"}:
        raise ThemeApplicationError("CONTEXT_BOUND_ROTATION_SERIES_MISSING")

    e_day = APP._date(execution_date, "EXECUTION_SESSION_DATE_INVALID")
    e_start = dt.datetime.combine(e_day, dt.time.min, tzinfo=KST).astimezone(dt.timezone.utc)
    close_time = dt.time.fromisoformat(contract["execution_session"]["regular_session_close_local"])
    e_close = dt.datetime.combine(e_day, close_time, tzinfo=KST).astimezone(dt.timezone.utc)
    if session_calendar is not None:
        e_close = APP._timestamp(
            session_calendar["execution_session_close_at"],
            "EXECUTION_SESSION_CALENDAR_CLOSE_INVALID",
        )
    membership_from = max(
        e_start,
        APP._timestamp(admission["admission_real_usable_from"], "ADMISSION_REAL_USABLE_FROM_INVALID"),
        APP._timestamp(decision["decision_real_usable_from"], "NEXT_SESSION_DECISION_REAL_USABLE_INVALID"),
    )

    context_available_by = max(
        APP._master_latest_available_at(d_master, d_master_first_seen),
        d_leadership["available_at"],
        APP._timestamp(d_leadership_first_seen, "CONTEXT_LEADERSHIP_FIRST_SEEN_INVALID"),
    )
    if session_calendar is not None:
        execution_availability.extend(
            APP._timestamp(row[field], "SESSION_CALENDAR_AVAILABILITY_INVALID")
            for row in session_calendar["sessions"]
            for field in ("available_at", "first_seen_at")
        )
    execution_available_by = max(execution_availability)
    latest_available = max(context_available_by, execution_available_by)
    inputs_available = latest_available <= evaluation
    interval_nonempty = membership_from < e_close
    evaluation_active = interval_nonempty and membership_from <= evaluation < e_close
    execution_active = interval_nonempty and membership_from <= execution < e_close
    ordered = evaluation <= execution
    ttl_seconds = contract["execution_session"]["decision_to_forward_execution_max_seconds"]
    within_ttl = ordered and (execution - evaluation).total_seconds() <= ttl_seconds
    active = (
        session_calendar is not None and inputs_available and evaluation_active
        and execution_active and within_ttl
        and context_mode_reason is None and identity_reason is None and date_reason is None
    )

    status_reason_detail = None
    if session_calendar_reason is not None:
        status = "UNKNOWN_" + session_calendar_reason
    elif context_mode_reason is not None:
        status = "UNKNOWN_" + context_mode_reason
    elif date_reason is not None:
        status = "UNKNOWN_" + date_reason
    elif identity_reason is not None:
        status = "UNKNOWN_" + identity_reason
        status_reason_detail = identity_detail
    elif session_calendar is None:
        status = "UNKNOWN_SESSION_CALENDAR_UNVERIFIED"
    elif not interval_nonempty:
        status = "UNKNOWN_EMPTY_EXECUTION_MEMBERSHIP_INTERVAL"
    elif not inputs_available:
        status = "UNKNOWN_INPUT_AVAILABLE_AFTER_EVALUATION"
    elif not evaluation_active:
        status = "UNKNOWN_EVALUATION_OUTSIDE_EXECUTION_SESSION_MEMBERSHIP"
    elif not execution_active:
        status = "UNKNOWN_EXECUTION_MEMBERSHIP_EXPIRED"
    elif not within_ttl:
        status = "UNKNOWN_FORWARD_EXECUTION_ORDER_OR_600_SECOND_TTL"
    else:
        status = "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT"

    output = {
        "schema_version": NEXT_SESSION_OUTPUT_V4_SCHEMA,
        "status": status,
        "status_reason_detail": status_reason_detail,
        "application_scope": admission["application_scope"],
        "context_label": "PREVIOUS_COMPLETED_SESSION_CONTEXT",
        "context_session_date": context_date,
        "execution_session_date": execution_date,
        "context_source_mode": CONTEXT_MODE_OFFICIAL if context_mode_reason is None else None,
        "execution_identity_evidence_mode": mode,
        "execution_identity_evidence": {
            **identity_evidence,
            "listing_resolution": listing_receipts,
            "reason": identity_reason,
        },
        "immediate_session_predecessor_verified": session_calendar is not None,
        "session_calendar_verified": session_calendar is not None,
        "session_calendar_reason": session_calendar_reason,
        "theme_id": admission["theme_id"],
        "rotation_series_identity": admission["rotation_series_identity"],
        "context_series_observation": {
            "role": series["role"],
            "benchmark_identity": series["benchmark_identity"],
            "relative_strength_vs_benchmark": str(series["relative_strength_vs_benchmark"]),
            "top_bucket_verified": False,
        },
        "context_identities": context_identities,
        "execution_identities": execution_identities,
        "execution_membership": {
            "membership_from": _utc_text(membership_from),
            "membership_to": _utc_text(e_close),
            "evaluation_at": _utc_text(evaluation),
            "forward_execution_at": _utc_text(execution),
            "interval_nonempty": interval_nonempty,
            "evaluation_active": evaluation_active,
            "forward_execution_active": execution_active,
            "decision_to_execution_seconds": (
                (execution - evaluation).total_seconds() if ordered else None
            ),
            "within_600_second_window": within_ttl,
        },
        "inputs_available_by_evaluation": inputs_available,
        "latest_required_input_available_at": _utc_text(latest_available),
        "lineage": {
            "trusted_commit": commit,
            "decision_id": decision["decision_id"],
            "user_ratification_id": decision["user_ratification_id"],
            "source_admission_real_usable_from": admission["admission_real_usable_from"],
            "next_session_decision_real_usable_from": decision["decision_real_usable_from"],
            "next_session_contract_v4_first_seen_at": decision["contract_first_seen_at"],
            "amendment_evidence_first_seen_at": decision["amendment_evidence_first_seen_at"],
            "context_master_payload_sha256": d_master["payload_sha256"],
            "context_master_first_seen_at": d_master_first_seen,
            "context_leadership_payload_sha256": d_leadership_wrapper["payload_sha256"],
            "context_leadership_first_seen_at": d_leadership_first_seen,
            "execution_master_payload_sha256": None if e_master is None else e_master["payload_sha256"],
            "execution_master_first_seen_at": e_master_first_seen,
            "execution_identity_master_payload_sha256": identity_evidence["identity_master_payload_sha256"],
            "session_calendar_receipt_sha256": (
                None if session_calendar is None
                else session_calendar["calendar_receipt_sha256"]
            ),
            "session_calendar": (
                [] if session_calendar is None
                else copy.deepcopy(session_calendar["sessions"])
            ),
        },
        "separate_required_inputs": copy.deepcopy(contract["separate_required_inputs"]),
        "authority": {
            "previous_completed_session_context_input_authorized": active,
            **APP.NEXT_SESSION_AUTHORITY,
        },
    }
    output["payload_sha256"] = APP.payload_sha256(output)
    return output
