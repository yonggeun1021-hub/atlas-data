"""KR evidence-to-PAPER decision mechanism with explicit external trust anchors.

No deployment policy or source qualification is shipped here. Expected hashes
must come from the existing policy/source owners, never from the submitted
packet itself. Synthetic and replay results cannot enter the natural consumer.
Legacy regime_output/v1 and its closed operational authority remain unchanged.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from regime import decision_authority as COMMON
from regime import paper_regime_reference as REFERENCE
from decision import common_paper_candidate_funnel as FUNNEL
from market_judgement import krx_market_judgement as JUDGEMENT
from rotation import kr_internal_paper_theme_application as SESSION_PROFILE

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "kr_paper_runtime_source", ROOT / ".github/scripts/korea_market_signals.py"
)
SOURCE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SOURCE)
SCHEMA = "kr_paper_runtime_decision/1"
SESSION_BOUNDARY_SCHEMA = "kr_paper_runtime_decision/4"
SESSION_BOUNDARY_INPUT_SCHEMA = "kr_paper_runtime_session_boundary_freshness_input/3"
SESSION_BOUNDARY_POLICY_SCHEMA = "kr_paper_runtime_policy/4"
SESSION_BOUNDARY_QUALIFICATION_SCHEMA = "kr_paper_runtime_qualification/4"
SESSION_BOUNDARY_CONTRACT_PATH = (
    ROOT / "config" / "kr_internal_paper_session_boundary_freshness_contract.json"
)
CLASSES = {"LIVE_NATURAL", "HISTORICAL_REPLAY", "SYNTHETIC_OFFLINE_FIXTURE"}
SHA = re.compile(r"[0-9a-f]{64}")


class KRRuntimeError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise KRRuntimeError(code)


def digest(raw: bytes) -> str:
    require(isinstance(raw, bytes), "BYTES_REQUIRED")
    return hashlib.sha256(raw).hexdigest()


def _object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: require(False, "NONFINITE_JSON"))
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise KRRuntimeError("JSON_INVALID") from exc
    require(isinstance(value, dict), "OBJECT_REQUIRED")
    return value


def _keys(value, keys, code):
    require(isinstance(value, dict) and set(value) == set(keys.split()), code)


def _text(value, code):
    require(isinstance(value, str) and bool(value.strip()), code)


def _sha(value, code):
    require(isinstance(value, str) and SHA.fullmatch(value) is not None, code)


def _time(value):
    require(isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is not None,
        "UTC_TIMESTAMP_REQUIRED")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KRRuntimeError("UTC_TIMESTAMP_INVALID") from exc


def _date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
            "SESSION_DATE_INVALID")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KRRuntimeError("SESSION_DATE_INVALID") from exc


def _trusted(raw, expected, code):
    _sha(expected, code + "_TRUST_ANCHOR_MISSING")
    require(digest(raw) == expected, code + "_HASH_MISMATCH")
    return _object(raw)


def _expected_session_boundary_contract():
    return {
        "schema_version": "kr_internal_paper_session_boundary_freshness_contract/3",
        "decision_id": "KR_INTERNAL_PAPER_SESSION_BOUNDARY_FRESHNESS_V1",
        "application_scope": "KR_INTERNAL_PAPER_BASELINE_V0",
        "decision_evidence": {
            "path": "evidence/authority/kr_internal_paper_session_boundary_freshness_adoption_20260908.json",
            "sha256": "dd36637960e22d54ee9ca28771f604088a9914198cfa9c80038a6d1f91138afe",
        },
        "previous_session_decision": {
            "path": "evidence/authority/kr_internal_paper_previous_completed_session_context_adoption_20260908.json",
            "sha256": "2571be782cb70473aaba49ed6c6a2fc0e67cd6f6a5a1af3d8ec66433aec5022b",
        },
        "session_boundary": {
            "profile_contract_path": "config/kr_internal_paper_theme_next_session_contract.json",
            "profile_contract_sha256": "717fea4b6e7fca24e1cba607bd330f8455aa4777727cb0eb7a46edacd6e7c602",
            "required_proof": "independently verified calendar proves D is the immediately previous OPEN_REGULAR session before E",
            "calendar_validator": "market_data/krx_session_calendar_v2.py::validate_calendar",
            "calendar_validator_sha256": "6660af3f9d12cc1ca8f0d417186fad0c6919ef16ad6d05973ae3ab8104f117ca",
            "calendar_contract": "config/krx_session_calendar_sources_v2.json",
            "calendar_contract_sha256": "58698d8d8045c9e084f250bb37becc667cfb3161073b6789af22cacc9456e813",
            "calendar_coverage": "exact committed approved-source snapshots for every calendar date D through E; D and E OPEN_REGULAR; every intervening date CLOSED",
            "post_close_market_signals_relation_required": False,
            "calendar_day_subtraction_authorized": False,
            "assumed_holiday_authorized": False,
        },
        "freshness": {
            "market_timezone": "Asia/Seoul",
            "regular_session_close_local": "15:30:00",
            "freshness_from": "D session_close",
            "freshness_to_exclusive": "E session_close",
            "derived_ttl_seconds_rule": "E.session_close - D.session_close",
            "rolling_extension_from_current_time_authorized": False,
            "arbitrary_14400_seconds_authorized": False,
            "decision_or_fill_at_or_after_e_close_authorized": False,
        },
        "runtime_binding": {
            "input_schema_version": SESSION_BOUNDARY_INPUT_SCHEMA,
            "policy_schema_version": SESSION_BOUNDARY_POLICY_SCHEMA,
            "qualification_schema_version": SESSION_BOUNDARY_QUALIFICATION_SCHEMA,
            "decision_schema_version": SESSION_BOUNDARY_SCHEMA,
            "policy_and_qualification_must_bind_same_freshness": True,
        },
        "separate_evidence_gates": [
            "Actual LIVE_NATURAL source classification",
            "Original-byte retention",
            "Complete admitted source chain",
            "Common-v1 two-packet confirmation",
            "Independently supplied qualification receipt and trusted hash",
            "Actual E identity/master and qualified current forward observations",
        ],
        "authority": {
            "paper_policy_use_authorized": True,
            "actual_source_qualification_known": False,
            "source_qualification_auto_population_authorized": False,
            "new_entry_authorized": False,
            "production_authorized": False,
            "real_authority": False,
            "order_authorized": False,
            "trading_authorized": False,
            "profitability_validated": False,
        },
    }


def _session_boundary_decision(trusted_commit, now):
    contract_raw = SESSION_BOUNDARY_CONTRACT_PATH.read_bytes()
    contract = _object(contract_raw)
    require(contract == _expected_session_boundary_contract(),
            "SESSION_BOUNDARY_CONTRACT_MISMATCH")
    repo, commit = SESSION_PROFILE._repo_and_commit(
        SESSION_BOUNDARY_CONTRACT_PATH, trusted_commit
    )
    contract_first_seen = SESSION_PROFILE._require_exact_committed_bytes(
        repo, commit, SESSION_BOUNDARY_CONTRACT_PATH, contract_raw,
        "SESSION_BOUNDARY_CONTRACT_NOT_EXACT_COMMITTED_BYTES",
    )
    profile_path = repo / contract["session_boundary"]["profile_contract_path"]
    profile_raw = profile_path.read_bytes()
    require(digest(profile_raw) == contract["session_boundary"]["profile_contract_sha256"],
            "SESSION_BOUNDARY_PROFILE_CONTRACT_HASH_MISMATCH")
    SESSION_PROFILE._require_exact_committed_bytes(
        repo, commit, profile_path, profile_raw,
        "SESSION_BOUNDARY_PROFILE_CONTRACT_NOT_EXACT_COMMITTED_BYTES",
    )
    evidence_path = repo / contract["decision_evidence"]["path"]
    evidence_raw = evidence_path.read_bytes()
    require(digest(evidence_raw) == contract["decision_evidence"]["sha256"],
            "SESSION_BOUNDARY_DECISION_EVIDENCE_HASH_MISMATCH")
    evidence_first_seen = SESSION_PROFILE._require_exact_committed_bytes(
        repo, commit, evidence_path, evidence_raw,
        "SESSION_BOUNDARY_DECISION_EVIDENCE_NOT_EXACT_COMMITTED_BYTES",
    )
    evidence = _object(evidence_raw)
    require(
        evidence.get("schema_version")
        == "kr_internal_paper_session_boundary_freshness_adoption/1"
        and evidence.get("decision_id") == contract["decision_id"]
        and evidence.get("application_scope") == contract["application_scope"]
        and evidence.get("status") == "POLICY_ADOPTED_ACTUAL_SOURCE_QUALIFICATION_UNKNOWN"
        and evidence.get("implementation_authorized") is True
        and evidence.get("real_authority") is False,
        "SESSION_BOUNDARY_DECISION_EVIDENCE_MISMATCH",
    )
    previous_path = repo / contract["previous_session_decision"]["path"]
    previous_raw = previous_path.read_bytes()
    require(digest(previous_raw) == contract["previous_session_decision"]["sha256"],
            "SESSION_BOUNDARY_PREVIOUS_DECISION_HASH_MISMATCH")
    SESSION_PROFILE._require_exact_committed_bytes(
        repo, commit, previous_path, previous_raw,
        "SESSION_BOUNDARY_PREVIOUS_DECISION_NOT_EXACT_COMMITTED_BYTES",
    )
    usable = max(
        _time(evidence["recorded_after_decision_at_utc"]),
        _time(contract_first_seen),
        _time(evidence_first_seen),
    )
    require(usable <= now, "SESSION_BOUNDARY_POLICY_FUTURE_AT_EVALUATION")
    return contract, commit, usable


def _session_boundary_binding(value, now):
    _keys(
        value,
        "schema_version context_session_date execution_session_date "
        "context_session_close_at execution_session_close_at "
        "session_calendar_packet_paths trusted_commit",
        "SESSION_BOUNDARY_INPUT_SCHEMA_INVALID",
    )
    require(value["schema_version"] == SESSION_BOUNDARY_INPUT_SCHEMA,
            "SESSION_BOUNDARY_INPUT_SCHEMA_INVALID")
    require(
        isinstance(value["session_calendar_packet_paths"], list)
        and all(isinstance(path, str) and path for path in value["session_calendar_packet_paths"]),
        "SESSION_CALENDAR_PATHS_REQUIRED",
    )
    _text(value["trusted_commit"], "SESSION_BOUNDARY_TRUSTED_COMMIT_REQUIRED")
    contract, commit, usable = _session_boundary_decision(value["trusted_commit"], now)
    boundary = SESSION_PROFILE.derive_verified_session_boundary(
        [
            path if Path(path).is_absolute() else ROOT / path
            for path in value["session_calendar_packet_paths"]
        ],
        value["context_session_date"], value["execution_session_date"],
        value["context_session_close_at"], value["execution_session_close_at"],
        now.isoformat().replace("+00:00", "Z"), commit,
    )
    return {
        **boundary,
        "decision_id": contract["decision_id"],
        "decision_real_usable_from": usable.isoformat().replace("+00:00", "Z"),
        "paper_policy_use_authorized": True,
        "actual_source_qualification": "UNKNOWN",
    }


def _policy(raw, expected, common, now, session_boundary=None):
    policy = _trusted(raw, expected, "POLICY")
    fields = (
        "schema_version market evidence_class policy_id acceptance_refs "
        "effective_from effective_until reference_policy_sha256 "
        "common_policy_binding_sha256 source_contract_sha256 leadership_policy_sha256 ttl_seconds"
    )
    if session_boundary is not None:
        fields += " session_boundary_freshness"
    _keys(policy, fields, "POLICY_SCHEMA_INVALID")
    expected_schema = (
        SESSION_BOUNDARY_POLICY_SCHEMA if session_boundary is not None
        else "kr_paper_runtime_policy/1"
    )
    require(policy["schema_version"] == expected_schema
            and policy["market"] == "KR" and policy["evidence_class"] in CLASSES,
            "POLICY_SCOPE_INVALID")
    _text(policy["policy_id"], "POLICY_ID_REQUIRED")
    _keys(policy["acceptance_refs"], "normalization freshness pit common_runtime",
          "POLICY_ACCEPTANCE_REFS_REQUIRED")
    for ref in policy["acceptance_refs"].values():
        _text(ref, "POLICY_ACCEPTANCE_REF_INVALID")
    require(type(policy["ttl_seconds"]) is int and policy["ttl_seconds"] > 0,
            "EXPLICIT_TTL_REQUIRED")
    if session_boundary is not None:
        require(policy["session_boundary_freshness"] == session_boundary,
                "POLICY_SESSION_BOUNDARY_BINDING_MISMATCH")
        require(policy["ttl_seconds"] == session_boundary["derived_ttl_seconds"],
                "POLICY_DERIVED_TTL_MISMATCH")
    start, end = _time(policy["effective_from"]), _time(policy["effective_until"])
    require(start < end and start <= now < end, "POLICY_OUTSIDE_EFFECTIVE_WINDOW")
    require(policy["reference_policy_sha256"] == common["binding"]["paper_baseline_policy_sha256"],
            "REFERENCE_POLICY_BINDING_MISMATCH")
    require(policy["common_policy_binding_sha256"] == COMMON.payload_sha256(common["binding"]),
            "COMMON_POLICY_BINDING_MISMATCH")
    require(policy["source_contract_sha256"] == digest(SOURCE.CONTRACT_PATH.read_bytes()),
            "SOURCE_CONTRACT_BINDING_MISMATCH")
    require(policy["leadership_policy_sha256"] == digest(SOURCE.LEADERSHIP_POLICY_PATH.read_bytes()),
            "LEADERSHIP_POLICY_BINDING_MISMATCH")
    return policy


def _source(raw, row, policy, source_contract, reference_policy):
    _keys(row, "source_sha256 source_ref owner_receipt_sha256 as_of_date "
          "available_at session_close_at decision_at", "SOURCE_RECEIPT_SCHEMA_INVALID")
    require(digest(raw) == row["source_sha256"], "SOURCE_BYTES_MISMATCH")
    _text(row["source_ref"], "SOURCE_REF_REQUIRED")
    _sha(row["owner_receipt_sha256"], "OWNER_RECEIPT_HASH_REQUIRED")
    source = SOURCE.validate_packet(_object(raw), source_contract)
    session = _date(source["as_of_date"])
    require(_date(source["previous_date"]) < session, "SOURCE_SESSION_ORDER_INVALID")
    require(row["as_of_date"] == source["as_of_date"], "SOURCE_SESSION_MISMATCH")
    require(source.get("available_at") == source["generated_at"] == row["available_at"],
            "SOURCE_AVAILABILITY_MISMATCH")
    leadership = JUDGEMENT.KOREA_LEADERSHIP.load_policy(SOURCE.LEADERSHIP_POLICY_PATH)
    JUDGEMENT.KOREA_LEADERSHIP.require_ratified(leadership)
    JUDGEMENT._validate_market_measurements(source)
    JUDGEMENT._validate_leadership_binding(source, leadership)
    JUDGEMENT._validate_source_lineage(source, JUDGEMENT.load_contract())
    available, close, decision = map(_time, (
        row["available_at"], row["session_close_at"], row["decision_at"]))
    require(close.astimezone(ZoneInfo("Asia/Seoul")).date() == session,
            "SESSION_CLOSE_DATE_MISMATCH")
    require(close <= available <= decision, "SOURCE_NOT_AVAILABLE_AT_DECISION")
    earliest = dt.datetime.combine(session, dt.time.fromisoformat(
        leadership["earliest_usable_time"]), tzinfo=ZoneInfo("Asia/Seoul"))
    require(available >= earliest, "SOURCE_BEFORE_EXISTING_EARLIEST_USABLE_TIME")
    require(_time(policy["effective_from"]) <= decision < _time(policy["effective_until"]),
            "SOURCE_OUTSIDE_POLICY_WINDOW")
    require((decision - close).total_seconds() <= policy["ttl_seconds"], "SOURCE_STALE")
    requests = source["source"]["requests"]
    _keys(requests, "stock index", "SOURCE_LINEAGE_INVALID")
    fetched = []
    for family in ("stock", "index"):
        _keys(requests[family], "KOSPI KOSDAQ", "SOURCE_LINEAGE_INVALID")
        for market in ("KOSPI", "KOSDAQ"):
            request = requests[family][market]
            require(request.get("endpoint") == source_contract[family + "_endpoints"][market.lower()],
                    "SOURCE_ENDPOINT_MISMATCH")
            for period in ("previous", "current"):
                _sha(request.get(period + "_response_sha256"), "SOURCE_RESPONSE_HASH_INVALID")
                when = _time(request.get(period + "_fetched_at_utc"))
                require(when <= available, "SOURCE_LINEAGE_LOOKAHEAD")
                if period == "current":
                    require(close <= when, "SOURCE_FETCH_BEFORE_SESSION_CLOSE")
                fetched.append(when)
    require(max(fetched) == available, "SOURCE_GENERATION_TIME_MISMATCH")
    axes = REFERENCE.normalize_kr_measurements(source, reference_policy)
    # The existing arithmetic is reused. Measurement domains are enforced here
    # in addition to the owning packet's structural validator.
    from decimal import Decimal
    breadth = Decimal(source["axes"]["BREADTH"]["measurement"]["combined"]["advance_fraction"])
    move = Decimal(source["axes"]["RISK_VOL"]["measurement"]["combined_mean_absolute_stock_move_pct"])
    require(0 <= breadth <= 1 and move >= 0, "SOURCE_MEASUREMENT_DOMAIN_INVALID")
    return source, axes


def evaluate_kr_paper_runtime(*, source_packets: list[bytes], evaluation_at: str,
                              code_revision: str, experiment_policy: bytes | None = None,
                              expected_policy_sha256: str | None = None,
                              qualification_receipt: bytes | None = None,
                              expected_qualification_sha256: str | None = None,
                              session_boundary_freshness: dict | None = None) -> dict:
    """Pure calculation. No IO writes, registry ratification, or order authority.

    qualification_receipt is an owner-admitted ordered complete session chain,
    not an authentication mechanism. Its expected hash and the policy hash are
    trusted deployment inputs. This function verifies the exact admitted bytes
    and existing source schema; it cannot authenticate a broker or raw KRX feed.
    """
    now = _time(evaluation_at)
    require(isinstance(code_revision, str) and re.fullmatch(r"[0-9a-f]{40}", code_revision),
            "CODE_REVISION_REQUIRED")
    require(isinstance(source_packets, list), "SOURCE_LIST_REQUIRED")
    hashes = [digest(raw) for raw in source_packets]
    boundary_mode = session_boundary_freshness is not None
    implementation_files = [
        "regime/kr_paper_runtime.py", "regime/paper_regime_reference.py",
        "regime/decision_authority.py", ".github/scripts/korea_market_signals.py",
        "decision/common_paper_candidate_funnel.py",
        "market_judgement/krx_market_judgement.py", ".github/scripts/korea_leadership.py",
    ]
    if boundary_mode:
        implementation_files.extend([
            "rotation/kr_internal_paper_theme_application.py",
            "market_data/krx_session_bars.py",
        ])
    packet = {
        "schema_version": SESSION_BOUNDARY_SCHEMA if boundary_mode else SCHEMA,
        "market": "KR", "evaluation_at": evaluation_at,
        "code_revision": code_revision, "evidence_class": None,
        "implementation_sha256": {
            name: digest((ROOT / name).read_bytes()) for name in implementation_files
        },
        "source_sha256": hashes, "policy_sha256": None,
        "qualification_sha256": None, "policy_binding": None, "source_chain": [],
        "signed_axes": [], "aggregation": None, "decision_status": "BLOCKED",
        "paper_regime": "UNKNOWN", "runtime_regime": "UNKNOWN", "direction": "UNKNOWN",
        "confidence": None, "runtime_decision_available": False, "reasons": [],
        "authority": {"paper_experiment_calculation_only": True,
                      "operational_policy_ratified": False, "real_order_authorized": False,
                      "buy_authorized": False, "production_authorized": False,
                      "trading_authorized": False},
        "verification_scope": "OWNER_PINNED_SOURCE_BYTES_AND_STRUCTURAL_SOURCE_VALIDATOR",
        "raw_provider_bytes_authenticated": False,
    }
    if boundary_mode:
        packet.update(
            session_boundary_freshness=None,
            paper_policy_use_authorized=False,
            actual_source_qualification="UNKNOWN",
        )
    try:
        require(experiment_policy is not None, "EXPLICIT_PAPER_POLICY_MISSING")
        boundary = None
        if boundary_mode:
            boundary = _session_boundary_binding(session_boundary_freshness, now)
            packet.update(
                session_boundary_freshness=copy.deepcopy(boundary),
                paper_policy_use_authorized=True,
            )
        common = COMMON.load_common_v1_policy()
        policy = _policy(
            experiment_policy, expected_policy_sha256, common, now, boundary
        )
        reference_bytes = COMMON.PAPER_BASELINE_POLICY_PATH.read_bytes()
        require(digest(reference_bytes) == policy["reference_policy_sha256"],
                "REFERENCE_POLICY_BINDING_MISMATCH")
        reference_policy = _object(reference_bytes)
        packet.update(policy_sha256=expected_policy_sha256,
                      evidence_class=policy["evidence_class"], policy_binding=copy.deepcopy(policy))
        require(qualification_receipt is not None, "SOURCE_QUALIFICATION_MISSING")
        receipt = _trusted(qualification_receipt, expected_qualification_sha256, "QUALIFICATION")
        receipt_fields = (
            "schema_version market evidence_class policy_sha256 "
            "calendar_receipt_sha256 sources"
        )
        if boundary_mode:
            receipt_fields += " session_boundary_freshness"
        _keys(receipt, receipt_fields, "QUALIFICATION_SCHEMA_INVALID")
        expected_receipt_schema = (
            SESSION_BOUNDARY_QUALIFICATION_SCHEMA if boundary_mode
            else "kr_paper_runtime_qualification/1"
        )
        require(receipt["schema_version"] == expected_receipt_schema
                and receipt["market"] == "KR" and receipt["evidence_class"] == policy["evidence_class"]
                and receipt["policy_sha256"] == expected_policy_sha256, "QUALIFICATION_SCOPE_MISMATCH")
        _sha(receipt["calendar_receipt_sha256"], "CALENDAR_RECEIPT_HASH_REQUIRED")
        if boundary_mode:
            require(receipt["session_boundary_freshness"] == boundary,
                    "QUALIFICATION_SESSION_BOUNDARY_BINDING_MISMATCH")
            require(receipt["calendar_receipt_sha256"] == boundary["calendar_receipt_sha256"],
                    "QUALIFICATION_CALENDAR_BINDING_MISMATCH")
        require(isinstance(receipt["sources"], list) and len(receipt["sources"]) == len(source_packets)
                and bool(source_packets), "QUALIFIED_SOURCE_CHAIN_INCOMPLETE")
        require(len(set(hashes)) == len(hashes), "DUPLICATE_SOURCE_PACKET")
        packet["qualification_sha256"] = expected_qualification_sha256
        contract = SOURCE.load_contract()
        steps, previous_date, previous_time, latest_source = [], None, None, None
        for raw, row in zip(source_packets, receipt["sources"]):
            source, axes = _source(raw, row, policy, contract, reference_policy)
            latest_source = source
            if previous_date is not None:
                require(source["previous_date"] == previous_date, "SESSION_CHAIN_GAP")
                require(_time(row["decision_at"]) > previous_time, "DECISION_TIME_ORDER_INVALID")
            require(_time(row["decision_at"]) <= now, "FUTURE_DECISION_IN_HISTORY")
            previous_date, previous_time = source["as_of_date"], _time(row["decision_at"])
            steps.append({"packet_id": row["source_sha256"],
                          "as_of_date": source["as_of_date"],
                          "axes": {a["axis"]: {"status": "DEFINED", "direction": a["direction"]}
                                   for a in axes}})
            packet["signed_axes"].append({"source_sha256": row["source_sha256"],
                "axes": {a["axis"]: {"signed_direction": a["direction"],
                           "normalized_value": COMMON.common_v1_signed_value(common, a["direction"])} for a in axes}})
        if boundary_mode:
            require(latest_source["as_of_date"] == boundary["context_session_date"],
                    "LATEST_SOURCE_NOT_CONTEXT_SESSION_D")
            require(receipt["sources"][-1]["session_close_at"]
                    == boundary["context_session_close_at"],
                    "LATEST_SOURCE_CLOSE_NOT_CONTEXT_BOUNDARY")
        require((now - _time(receipt["sources"][-1]["session_close_at"])).total_seconds()
                <= policy["ttl_seconds"], "LATEST_SOURCE_STALE")
        packet["source_chain"] = copy.deepcopy(receipt["sources"])
        report = COMMON.replay_common_v1({"schema_version": 1, "market": "KR",
                   "case_id": "kr-paper-runtime", "steps": steps}, common)
        packet["aggregation"] = report
        packet["paper_regime"] = report["final_regime"]
        packet["direction"] = report["final_direction"]
        packet["confidence"] = report["final_confidence"]
        if report["final_regime"] == "UNKNOWN":
            packet["reasons"] = ["COMMON_CONFIRMATION_PENDING"]
        elif policy["evidence_class"] == "LIVE_NATURAL":
            packet.update(decision_status="PAPER_RUNTIME_CLASSIFIED",
                          runtime_regime=report["final_regime"], runtime_decision_available=True)
        else:
            packet["decision_status"] = "PAPER_SIMULATION_CLASSIFIED"
    except (KRRuntimeError, SOURCE.KoreaMarketSignalsError, REFERENCE.PaperRegimeReferenceError,
            COMMON.DecisionAuthorityError, JUDGEMENT.KrxMarketJudgementError,
            JUDGEMENT.KOREA_LEADERSHIP.KoreaLeadershipError,
            SESSION_PROFILE.ThemeApplicationError, OSError,
            KeyError, TypeError, ValueError) as exc:
        # Errors never expose raw source values or filesystem/provider details.
        code = str(exc).split(":", 1)[0] if isinstance(exc, (
            KRRuntimeError, SOURCE.KoreaMarketSignalsError, REFERENCE.PaperRegimeReferenceError,
            COMMON.DecisionAuthorityError, JUDGEMENT.KrxMarketJudgementError,
            JUDGEMENT.KOREA_LEADERSHIP.KoreaLeadershipError,
            SESSION_PROFILE.ThemeApplicationError)) else "INPUT_SHAPE_INVALID"
        packet["reasons"] = [code]
        packet["signed_axes"] = []
    packet["decision_id"] = "kr-paper-regime:" + COMMON.payload_sha256(packet)
    return packet


def validate_kr_paper_runtime(packet: dict, **inputs) -> dict:
    expected = evaluate_kr_paper_runtime(**inputs)
    require(COMMON.canonical_bytes(packet) == COMMON.canonical_bytes(expected),
            "RUNTIME_REDERIVATION_MISMATCH")
    return copy.deepcopy(expected)


def reduce_funnel_with_kr_regime(funnel_input: dict, runtime_packet: dict, **runtime_inputs) -> dict:
    """Attach the rederived natural decision to existing candidate sourceRefs.

    Does not derive/replace market scores, rotation, risk or entry/exit policy.
    The private adapter owns those inputs. An unavailable decision is rejected,
    leaving that owner to preserve its existing UNKNOWN/no-write behavior.
    """
    runtime = validate_kr_paper_runtime(runtime_packet, **runtime_inputs)
    require(runtime["runtime_decision_available"] is True, "REGIME_NOT_AVAILABLE")
    require(funnel_input.get("evaluationAt") == runtime["evaluation_at"], "CONSUMER_TIME_MISMATCH")
    # Validate before mutation so invalid and duplicate sourceRefs cannot be repaired.
    FUNNEL.reduce_funnel(funnel_input)
    bound = copy.deepcopy(funnel_input)
    for candidate in bound["candidates"]:
        require(candidate["market"] == "KOREA", "CONSUMER_MARKET_MISMATCH")
        if runtime["decision_id"] not in candidate["sourceRefs"]:
            candidate["sourceRefs"].append(runtime["decision_id"])
    return FUNNEL.reduce_funnel(bound)
