#!/usr/bin/env python3
"""Pure PAPER source-event eligibility evaluation.

This module deliberately does not discover files, call GitHub/providers, read a
clock, classify a market, or grant runtime authority.  Callers provide the
adopted schema, explicit evaluation time, and exact retained bytes together
with hashes and identity fields obtained independently of those bytes.

Hash and identity checks prove that the supplied bytes match the caller's
expectations.  They do *not* authenticate how GitHub evidence was acquired or
whether a calendar provider is official.  Those provenance decisions belong
to separately reviewed adapters and remain visible in every result as
``external_authenticity_verified_by_helper = False``.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = "paper_regime_source_eligibility_evaluation/1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ISO_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
CURRENT_STATES = {"CURRENT", "CURRENT_AS_FETCHED_NOT_PIT"}
TERMINAL_EVIDENCE = {
    "GITHUB_WORKFLOW_RUN_COMPLETED_EVENT",
    "GITHUB_ACTIONS_REST_TERMINAL_READBACK",
}
NONTERMINAL_EVIDENCE = {"GITHUB_ACTIONS_REST_RUN_READBACK"}
TERMINAL_FAILURES = {"failure", "cancelled"}
DAILY_RULES = {
    "US_ETF_DAILY",
    "KR_FIVE_SIGNALS",
    "CRYPTO_BTC_AND_BREADTH",
    "CRYPTO_STABLECOIN",
}
SLOWER_RULES = {"US_VIXCLS", "US_WRESBAL", "US_TOTBKCR"}
AXES = ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]


class PaperRegimeEligibilityError(ValueError):
    """Raised only when the API contract/schema itself is unusable."""


class _EvidenceInvalid(ValueError):
    pass


class _EvidenceUnconfirmed(ValueError):
    pass


def canonical_bytes(value: object) -> bytes:
    """Return the repository's deterministic JSON representation."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PaperRegimeEligibilityError("CANONICAL_JSON_INVALID") from exc


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise _EvidenceInvalid(code)


def _date(value: object, label: str) -> dt.date:
    _require(isinstance(value, str), f"{label}_DATE_REQUIRED")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise _EvidenceInvalid(f"{label}_DATE_INVALID") from exc
    _require(parsed.isoformat() == value, f"{label}_DATE_INVALID")
    return parsed


def _timestamp(value: object, label: str, *, utc: bool = False) -> dt.datetime:
    _require(isinstance(value, str), f"{label}_TIMESTAMP_REQUIRED")
    _require(ISO_TIMESTAMP.fullmatch(value) is not None, f"{label}_TIMESTAMP_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _EvidenceInvalid(f"{label}_TIMESTAMP_INVALID") from exc
    _require(parsed.tzinfo is not None, f"{label}_TIMEZONE_REQUIRED")
    if utc:
        _require(parsed.utcoffset() == dt.timedelta(0), f"{label}_MUST_BE_UTC")
    return parsed


def _path(value: Mapping[str, Any], dotted: str) -> object:
    current: object = value
    for part in dotted.split("."):
        _require(isinstance(current, Mapping) and part in current,
                 f"IDENTITY_FIELD_MISSING:{dotted}")
        current = current[part]
    return current


def _artifact(
    envelope: Mapping[str, Any],
    label: str,
    consumed: list[dict[str, str]],
) -> dict[str, Any]:
    """Validate exact bytes against an external hash and identity selection."""
    _require(isinstance(envelope, Mapping), f"{label}_ENVELOPE_REQUIRED")
    raw = envelope.get("raw")
    expected = envelope.get("expected_sha256")
    _require(isinstance(raw, bytes), f"{label}_RAW_BYTES_REQUIRED")
    _require(isinstance(expected, str) and SHA256.fullmatch(expected) is not None,
             f"{label}_EXPECTED_SHA256_INVALID")
    actual = sha256(raw)
    _require(actual == expected, f"{label}_HASH_MISMATCH")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _EvidenceInvalid(f"{label}_JSON_INVALID") from exc
    _require(isinstance(value, dict), f"{label}_OBJECT_REQUIRED")
    identities = envelope.get("expected_identity", {})
    _require(isinstance(identities, Mapping), f"{label}_IDENTITY_MAP_INVALID")
    for dotted, wanted in identities.items():
        _require(isinstance(dotted, str), f"{label}_IDENTITY_PATH_INVALID")
        _require(_path(value, dotted) == wanted, f"{label}_IDENTITY_MISMATCH:{dotted}")
    consumed.append({"kind": label, "sha256": actual})
    return value


def _validate_schema(
    schema: Mapping[str, Any], schema_raw: bytes, expected_schema_sha256: str
) -> tuple[dict[str, Any], str]:
    if not isinstance(schema, Mapping) or not isinstance(schema_raw, bytes):
        raise PaperRegimeEligibilityError("SCHEMA_AND_EXACT_BYTES_REQUIRED")
    if not isinstance(expected_schema_sha256, str) or SHA256.fullmatch(
        expected_schema_sha256
    ) is None:
        raise PaperRegimeEligibilityError("SCHEMA_EXPECTED_SHA256_INVALID")
    actual = sha256(schema_raw)
    if actual != expected_schema_sha256:
        raise PaperRegimeEligibilityError("SCHEMA_HASH_MISMATCH")
    try:
        decoded = json.loads(schema_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperRegimeEligibilityError("SCHEMA_JSON_INVALID") from exc
    if decoded != schema:
        raise PaperRegimeEligibilityError("SCHEMA_BYTES_VALUE_MISMATCH")
    required = {
        "authority", "comparability_and_caveats", "contract_version",
        "eligibility_states", "event_receipt", "source_frequency_rules",
        "unchanged_and_hysteresis",
    }
    if not required.issubset(schema):
        raise PaperRegimeEligibilityError("SCHEMA_FIELDS_MISSING")
    authority = schema["authority"]
    forbidden = (
        "action_authorized", "buy_authorized", "capital_authorized",
        "order_authorized", "position_size_authorized",
        "runtime_production_regime_authorized", "stage_authorized",
        "strategy_authorized", "target_weight_authorized",
        "trading_authorized", "real_trading",
    )
    if not isinstance(authority, Mapping) or any(authority.get(key) is not False for key in forbidden):
        raise PaperRegimeEligibilityError("SCHEMA_AUTHORITY_NOT_FAIL_CLOSED")
    return copy.deepcopy(dict(schema)), actual


def _invalid_result(base: dict[str, Any], reason: str) -> dict[str, Any]:
    base.update({
        "market_eligibility": "SOURCE_INVALID",
        "component_eligibility": {},
        "current_eligible": False,
        "classification_may_be_displayed": False,
        "required_assessments_complete": False,
        "hysteresis": {"increment": 0, "new_market_observation": False},
        "event_facts": {
            "evidence_type": None,
            "observed_at": None,
            "completed_at_utc": None,
            "event_schedule": None,
            "slot_id": None,
            "expected_at_utc": None,
            "expected_at_kst": None,
            "future_scheduled_slots": [],
        },
        "source_facts": {},
        "reasons": [reason],
    })
    return _finish(base)


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    identity_body = copy.deepcopy(result)
    identity_body.pop("generation_id", None)
    result["generation_id"] = "paper-regime-eligibility:" + sha256(
        canonical_bytes(identity_body)
    )
    return result


def _base_result(
    schema: Mapping[str, Any], market: str, evaluation_at: str,
    schema_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_version": schema["contract_version"],
        "evaluation_at": evaluation_at,
        "market": market,
        "market_eligibility": "DUE_COLLECTION_UNCONFIRMED",
        "component_eligibility": {},
        "current_eligible": False,
        "classification_may_be_displayed": False,
        "judgement": None,
        "displayed_as_of_date": None,
        "decision_date": None,
        "price_date": None,
        "hysteresis": {"increment": 0, "new_market_observation": False},
        "event_facts": {
            "evidence_type": None,
            "observed_at": None,
            "completed_at_utc": None,
            "event_schedule": None,
            "slot_id": None,
            "expected_at_utc": None,
            "expected_at_kst": None,
            "future_scheduled_slots": [],
        },
        "source_facts": {},
        "comparison": {
            "comparison_key": schema["comparability_and_caveats"]["comparison_key"],
            "three_market_comparison_status": "PARTIAL_OR_NON_COMPARABLE",
            "same_date_groups": {},
            "complete": False,
        },
        "caveats": [],
        "authority": copy.deepcopy(schema["authority"]),
        "evidence_boundary": {
            "content_hash_and_identity_checked": True,
            "external_authenticity_verified_by_helper": False,
            "detail": schema["event_receipt"]["external_evidence_trust"],
        },
        "consumed_evidence_hashes": [{"kind": "schema", "sha256": schema_hash}],
        "reasons": [],
        "legacy": {"rederivation_unchanged": True, "hash_rewritten": False},
    }


def _calendar(
    market: str,
    envelope: Mapping[str, Any] | None,
    consumed: list[dict[str, str]],
) -> dict[str, Any] | None:
    if market == "CRYPTO":
        return None
    if envelope is None:
        return {"status": "UNKNOWN", "latest_completed_session": None}
    value = _artifact(envelope, "official_calendar", consumed)
    _require(value.get("market") == market, "CALENDAR_MARKET_MISMATCH")
    status = value.get("status")
    _require(status in {"OPEN_REGULAR", "OPEN_EARLY_CLOSE", "CLOSED", "UNKNOWN", "CONFLICTING"},
             "CALENDAR_STATUS_INVALID")
    session = value.get("latest_completed_session")
    if session is not None:
        _date(session, "LATEST_COMPLETED_SESSION")
    if status == "CLOSED":
        _date(value.get("session_date"), "CLOSED_SESSION")
    return value


def _validate_workflow_identity(receipt: Mapping[str, Any]) -> None:
    _require(receipt.get("schema_version") == "paper_regime_source_event_receipt/1",
             "EVENT_SCHEMA_VERSION_INVALID")
    workflow = receipt.get("workflow")
    _require(isinstance(workflow, Mapping), "WORKFLOW_IDENTITY_MISSING")
    _require(isinstance(workflow.get("path"), str) and workflow["path"],
             "WORKFLOW_PATH_MISSING")
    _require(isinstance(workflow.get("github_head_sha"), str)
             and re.fullmatch(r"[0-9a-f]{40}", workflow["github_head_sha"]) is not None,
             "GITHUB_HEAD_SHA_MISSING_OR_INVALID")
    _require(isinstance(workflow.get("run_id"), int) and workflow["run_id"] > 0,
             "WORKFLOW_RUN_ID_INVALID")
    _require(isinstance(workflow.get("run_attempt"), int) and workflow["run_attempt"] > 0,
             "WORKFLOW_RUN_ATTEMPT_INVALID")
    workflow_hash = workflow.get("file_sha256")
    _require(workflow_hash is None or (
        isinstance(workflow_hash, str) and SHA256.fullmatch(workflow_hash) is not None
    ), "WORKFLOW_FILE_SHA256_INVALID")
    authority = receipt.get("authority")
    _require(isinstance(authority, Mapping), "EVENT_AUTHORITY_MISSING")
    _require(authority.get("operations_telemetry_only") is True,
             "EVENT_AUTHORITY_CLASS_INVALID")
    for key in ("decision_authorized", "order_authorized", "capital_authorized", "trading_authorized"):
        _require(authority.get(key) is False, f"EVENT_AUTHORITY_NOT_FAIL_CLOSED:{key}")


def _event_time_checks(receipt: Mapping[str, Any], evaluation_at: dt.datetime) -> None:
    observed = _timestamp(receipt.get("observed_at"), "OBSERVED_AT", utc=True)
    _require(observed <= evaluation_at, "FUTURE_OBSERVED_AT")
    event = receipt.get("event")
    _require(isinstance(event, Mapping), "EVENT_IDENTITY_MISSING")
    expected_utc = event.get("expected_at_utc")
    expected_kst = event.get("expected_at_kst")
    parsed_utc = None
    parsed_kst = None
    if expected_utc is not None:
        parsed_utc = _timestamp(expected_utc, "EXPECTED_AT_UTC", utc=True)
        _require(parsed_utc <= evaluation_at, "EVENT_SLOT_NOT_DUE")
        _require(parsed_utc <= observed, "EVENT_OBSERVED_BEFORE_DUE")
    if expected_kst is not None:
        parsed_kst = _timestamp(expected_kst, "EXPECTED_AT_KST")
        _require(parsed_kst.utcoffset() == dt.timedelta(hours=9), "EXPECTED_AT_KST_OFFSET_INVALID")
        _require(parsed_kst <= evaluation_at, "EVENT_SLOT_NOT_DUE")
        if expected_utc is not None:
            _require(parsed_kst == parsed_utc, "EVENT_SLOT_CLOCKS_INCONSISTENT")
    scheduled_date = event.get("scheduled_event_date_kst")
    if scheduled_date is not None:
        scheduled = _date(scheduled_date, "SCHEDULED_EVENT_DATE_KST")
        if parsed_kst is not None:
            _require(scheduled == parsed_kst.date(), "SCHEDULED_EVENT_DATE_INCONSISTENT")


def _event_status(
    event_envelopes: Sequence[Mapping[str, Any]],
    evaluation_at: dt.datetime,
    source_id: str,
    consumed: list[dict[str, str]],
) -> tuple[str, dict[str, Any] | None]:
    """Return the latest supplied due run state and its decoded evidence."""
    decoded: list[dict[str, Any]] = []
    for index, envelope in enumerate(event_envelopes):
        identities = envelope.get("expected_identity") if isinstance(envelope, Mapping) else None
        required_identity = {
            "source_id", "workflow.path", "workflow.github_head_sha",
            "workflow.run_id", "workflow.run_attempt",
        }
        _require(isinstance(identities, Mapping)
                 and required_identity.issubset(identities),
                 "EVENT_EXTERNAL_IDENTITY_BINDING_INCOMPLETE")
        receipt = _artifact(envelope, f"event[{index}]", consumed)
        _require(receipt.get("source_id") == source_id, "EVENT_SOURCE_ID_MISMATCH")
        _validate_workflow_identity(receipt)
        _event_time_checks(receipt, evaluation_at)
        kind = receipt.get("evidence_type")
        terminal = receipt.get("terminal")
        _require(isinstance(terminal, Mapping), "TERMINAL_BLOCK_MISSING")
        status = terminal.get("status")
        if status in {"queued", "in_progress"}:
            _require(kind in NONTERMINAL_EVIDENCE, "NONTERMINAL_EVIDENCE_TYPE_INVALID")
        elif status == "completed":
            _require(kind in TERMINAL_EVIDENCE, "SELF_OR_UNSUPPORTED_TERMINAL_EVIDENCE")
            conclusion = terminal.get("conclusion")
            _require(conclusion in {"success", *TERMINAL_FAILURES}, "TERMINAL_CONCLUSION_INVALID")
            completed = terminal.get("completed_at_utc")
            if completed is not None:
                completed_at = _timestamp(completed, "COMPLETED_AT_UTC", utc=True)
                _require(completed_at <= evaluation_at, "FUTURE_COMPLETED_AT")
                observed_at = _timestamp(receipt["observed_at"], "OBSERVED_AT", utc=True)
                _require(completed_at <= observed_at, "COMPLETED_AFTER_OBSERVED")
                expected_at = receipt["event"].get("expected_at_utc")
                if expected_at is not None:
                    _require(_timestamp(expected_at, "EXPECTED_AT_UTC", utc=True) <= completed_at,
                             "COMPLETED_BEFORE_DUE")
        else:
            raise _EvidenceInvalid("EVENT_STATUS_INVALID")
        decoded.append(receipt)
    if not decoded:
        return "MISSING", None
    latest = max(
        decoded,
        key=lambda value: (
            value["workflow"]["run_id"], value["workflow"]["run_attempt"]
        ),
    )
    terminal = latest["terminal"]
    if terminal["status"] in {"queued", "in_progress"}:
        return "RUNNING", latest
    if terminal["conclusion"] in TERMINAL_FAILURES:
        return "FAILED", latest
    return "SUCCESS", latest


def _validate_source(
    component: Mapping[str, Any],
    receipt: Mapping[str, Any],
    evaluation_at: dt.datetime,
    expected_date: str | None,
    consumed: list[dict[str, str]],
) -> tuple[str, str, dict[str, Any]]:
    source_id = component.get("source_id")
    rule = component.get("frequency_rule")
    _require(isinstance(source_id, str) and source_id, "SOURCE_ID_REQUIRED")
    _require(rule in DAILY_RULES | SLOWER_RULES, f"SOURCE_FREQUENCY_RULE_INVALID:{source_id}")
    source = _artifact(component.get("artifact"), f"source[{source_id}]", consumed)
    output = receipt.get("source_output")
    _require(isinstance(output, Mapping), "SUCCESS_SOURCE_OUTPUT_MISSING")
    required_output = (
        "path", "file_sha256", "payload_sha256", "observation_identity_sha256",
        "observation_date", "available_at", "publication_result",
    )
    if not all(output.get(key) is not None for key in required_output):
        raise _EvidenceUnconfirmed("SUCCESS_CAUSAL_OUTPUT_IDENTITY_UNCONFIRMED")
    _require(output["path"] == component.get("path"), "SOURCE_OUTPUT_PATH_MISMATCH")
    actual_file_hash = next(
        row["sha256"] for row in reversed(consumed) if row["kind"] == f"source[{source_id}]"
    )
    _require(output["file_sha256"] == actual_file_hash, "SOURCE_OUTPUT_FILE_HASH_MISMATCH")
    _require(isinstance(output["payload_sha256"], str)
             and SHA256.fullmatch(output["payload_sha256"]) is not None,
             "SOURCE_PAYLOAD_SHA256_INVALID")
    _require(source.get("payload_sha256") == output["payload_sha256"],
             "SOURCE_PAYLOAD_IDENTITY_MISMATCH")
    _require(source.get("observation_identity_sha256") == output["observation_identity_sha256"],
             "SOURCE_OBSERVATION_IDENTITY_MISMATCH")
    _require(isinstance(output["observation_identity_sha256"], str)
             and SHA256.fullmatch(output["observation_identity_sha256"]) is not None,
             "SOURCE_OBSERVATION_IDENTITY_INVALID")
    pointer = component.get("source_pointer")
    facts: object = _path(source, pointer) if pointer is not None else source
    _require(isinstance(facts, Mapping), "SOURCE_COMPONENT_FACTS_INVALID")
    _require(source.get("observation_date") == output["observation_date"],
             "SOURCE_OUTPUT_OBSERVATION_DATE_MISMATCH")
    observation_date = facts.get("observation_date")
    _date(observation_date, "SOURCE_OBSERVATION")
    _require(_date(observation_date, "SOURCE_OBSERVATION") <= evaluation_at.date(),
             "FUTURE_SOURCE_OBSERVATION_DATE")
    available_at = _timestamp(output["available_at"], "SOURCE_AVAILABLE_AT", utc=True)
    _require(available_at <= evaluation_at, "FUTURE_SOURCE_AVAILABLE_AT")
    _require(source.get("available_at") == output["available_at"],
             "SOURCE_AVAILABLE_AT_MISMATCH")
    price_date = facts.get("price_date")
    _require(source.get("price_date") == output.get("price_date"),
             "SOURCE_OUTPUT_PRICE_DATE_MISMATCH")
    if price_date is not None:
        _date(price_date, "SOURCE_PRICE")
        _require(_date(price_date, "SOURCE_PRICE") <= evaluation_at.date(),
                 "FUTURE_SOURCE_PRICE_DATE")
    publication = output["publication_result"]
    _require(publication in {"published", "skipped_existing"}, "PUBLICATION_RESULT_INVALID")

    event = receipt["event"]
    if event.get("expected_at_utc") is None and event.get("expected_at_kst") is None:
        raise _EvidenceUnconfirmed("SUCCESS_DUE_SLOT_UNCONFIRMED")
    if rule in DAILY_RULES:
        if expected_date is None:
            raise _EvidenceUnconfirmed("EXPECTED_DAILY_DATE_UNCONFIRMED")
        _date(expected_date, "EXPECTED_DAILY")
        if observation_date != expected_date:
            if rule.startswith("CRYPTO_"):
                raise _EvidenceInvalid("CRYPTO_FINALIZED_DAY_MISSING_OR_INVALID")
            state = "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
        else:
            state = "CURRENT"
    else:
        state = "CURRENT_AS_FETCHED_NOT_PIT"
    return state, observation_date, {
        "frequency_rule": rule,
        "observation_date": observation_date,
        "price_date": price_date,
        "measurement_date_preserved": True,
    }


def compare_market_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Group markets by their unchanged decision date; never use price date."""
    groups: dict[str, list[str]] = {}
    eligible = True
    assessments_complete = True
    markets: list[str] = []
    for row in rows:
        market = row.get("market")
        decision_date = row.get("decision_date")
        if market in {"US", "KR", "CRYPTO"} and isinstance(decision_date, str):
            _date(decision_date, "COMPARISON_DECISION")
            groups.setdefault(decision_date, []).append(market)
            markets.append(market)
        else:
            eligible = False
        eligible = eligible and row.get("market_eligibility") in CURRENT_STATES
        judgement = row.get("judgement")
        candidate = (
            judgement.get("candidate_regime")
            if isinstance(judgement, Mapping)
            else row.get("candidate_regime")
        )
        eligible = eligible and candidate in {
            "RISK_ON", "RISK_OFF", "NEUTRAL", "STRESS"
        }
        assessments_complete = assessments_complete and row.get(
            "required_assessments_complete", False
        ) is True
    normalized = {key: sorted(value) for key, value in sorted(groups.items())}
    complete = (
        len(rows) == 3
        and set(markets) == {"US", "KR", "CRYPTO"}
        and len(markets) == len(set(markets))
        and len(groups) == 1
        and eligible
        and assessments_complete
    )
    return {
        "three_market_comparison_status": (
            "COMPLETE" if complete else "PARTIAL_OR_NON_COMPARABLE"
        ),
        "same_date_groups": normalized,
        "complete": complete,
        "price_date_substitution_used": False,
    }


def evaluate_paper_regime_eligibility(
    schema: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    *,
    schema_raw: bytes,
    expected_schema_sha256: str,
) -> dict[str, Any]:
    """Evaluate one market from caller-supplied retained evidence.

    ``evaluation`` is data, not an instruction to discover it.  Artifact
    envelopes have ``raw`` exact JSON bytes, an independently supplied
    ``expected_sha256``, and optional dotted ``expected_identity`` bindings.
    A completed success must causally name the exact source bytes.  Failed or
    cancelled completed runs require no source output.  Nullable unavailable
    facts stay unavailable and therefore cannot manufacture CURRENT.
    """
    frozen_schema, schema_hash = _validate_schema(
        schema, schema_raw, expected_schema_sha256
    )
    if not isinstance(evaluation, Mapping):
        raise PaperRegimeEligibilityError("EVALUATION_OBJECT_REQUIRED")
    market = evaluation.get("market")
    if market not in {"US", "KR", "CRYPTO"}:
        raise PaperRegimeEligibilityError("MARKET_INVALID")
    evaluation_text = evaluation.get("evaluation_at")
    try:
        evaluation_at = _timestamp(evaluation_text, "EVALUATION_AT")
    except _EvidenceInvalid as exc:
        raise PaperRegimeEligibilityError(str(exc)) from exc
    base = _base_result(frozen_schema, market, evaluation_text, schema_hash)
    consumed = base["consumed_evidence_hashes"]

    try:
        for index, envelope in enumerate(evaluation.get("legacy_artifacts", [])):
            _artifact(envelope, f"legacy[{index}]", consumed)

        normalization = evaluation.get("normalization_binding")
        if normalization is not None:
            _require(isinstance(normalization, Mapping), "NORMALIZATION_BINDING_INVALID")
            _require(normalization.get("actual_version") == normalization.get("expected_version"),
                     "NORMALIZATION_PARAMETER_VERSION_MISMATCH")

        calendar = _calendar(market, evaluation.get("official_session"), consumed)
        if calendar is not None and calendar["status"] in {"UNKNOWN", "CONFLICTING"}:
            base.update({
                "market_eligibility": "SESSION_CALENDAR_UNKNOWN",
                "displayed_as_of_date": evaluation.get("prior_as_of_date"),
                "reasons": ["OFFICIAL_CALENDAR_MISSING_OR_CONFLICTING"],
            })
            return _finish(base)
        if calendar is not None and calendar["status"] == "CLOSED":
            closed_session = _date(calendar.get("session_date"), "CLOSED_SESSION")
            expected_session = _date(
                evaluation.get("expected_session_date"), "EXPECTED_CLOSED_SESSION"
            )
            _require(closed_session == expected_session,
                     "CLOSED_SESSION_DATE_MISMATCH")
            _require(closed_session <= evaluation_at.date(),
                     "FUTURE_CLOSED_SESSION_DATE")
            latest = calendar.get("latest_completed_session")
            latest_session = None
            if latest is not None:
                latest_session = _date(latest, "LATEST_COMPLETED_SESSION")
                _require(latest_session <= evaluation_at.date(),
                         "FUTURE_LATEST_COMPLETED_SESSION")
                _require(latest_session < closed_session,
                         "LATEST_COMPLETED_SESSION_NOT_BEFORE_CLOSED_SESSION")
            prior = evaluation.get("prior_as_of_date")
            _require(prior is not None, "HOLIDAY_PRIOR_OBSERVATION_MISSING")
            prior_date = _date(prior, "PRIOR_AS_OF")
            _require(prior_date <= evaluation_at.date(), "FUTURE_PRIOR_AS_OF_DATE")
            if latest_session is not None:
                _require(prior_date <= latest_session,
                         "PRIOR_AS_OF_AFTER_LATEST_COMPLETED_SESSION")
            prior_price = evaluation.get("prior_price_date")
            if prior_price is not None:
                _require(_date(prior_price, "PRIOR_PRICE") <= evaluation_at.date(),
                         "FUTURE_PRIOR_PRICE_DATE")
            prior_judgement = None
            if evaluation.get("prior_judgement") is not None:
                prior_judgement = _artifact(
                    evaluation["prior_judgement"], "prior_judgement", consumed
                )
                judgement_date = prior_judgement.get(
                    "decision_date", prior_judgement.get("as_of_date")
                )
                _require(_date(judgement_date, "PRIOR_JUDGEMENT") <= evaluation_at.date(),
                         "FUTURE_PRIOR_JUDGEMENT_DATE")
                judgement_price = prior_judgement.get("price_date")
                if judgement_price is not None:
                    _require(
                        _date(judgement_price, "PRIOR_JUDGEMENT_PRICE")
                        <= evaluation_at.date(),
                        "FUTURE_PRIOR_JUDGEMENT_PRICE_DATE",
                    )
            base.update({
                "market_eligibility": "OFFICIAL_HOLIDAY_CARRY",
                "displayed_as_of_date": prior,
                "decision_date": prior,
                "price_date": prior_price,
                "judgement": prior_judgement,
                "reasons": ["OFFICIAL_DATE_SPECIFIC_CALENDAR_CLOSED"],
            })
            return _finish(base)

        expected_date = evaluation.get("expected_observation_date")
        if calendar is not None:
            expected_date = calendar.get("latest_completed_session")
        if expected_date is not None:
            expected = _date(expected_date, "EXPECTED_OBSERVATION")
            _require(expected <= evaluation_at.date(), "FUTURE_EXPECTED_OBSERVATION_DATE")

        events = evaluation.get("events", [])
        _require(isinstance(events, Sequence) and not isinstance(events, (str, bytes)),
                 "EVENT_LIST_INVALID")
        source_id = evaluation.get("event_source_id")
        _require(isinstance(source_id, str) and source_id, "EVENT_SOURCE_ID_REQUIRED")

        scheduled_slots = evaluation.get("scheduled_slots", [])
        _require(isinstance(scheduled_slots, Sequence)
                 and not isinstance(scheduled_slots, (str, bytes)),
                 "SCHEDULED_SLOT_LIST_INVALID")
        future_slots: list[dict[str, Any]] = []
        for slot in scheduled_slots:
            _require(isinstance(slot, Mapping), "SCHEDULED_SLOT_INVALID")
            due = _timestamp(slot.get("expected_at"), "SCHEDULED_SLOT_EXPECTED_AT")
            _require(slot.get("status") == "NOT_DUE" and due > evaluation_at,
                     "SCHEDULED_SLOT_STATUS_INCONSISTENT")
            future_slots.append({
                "slot_id": slot.get("slot_id"),
                "expected_at": slot["expected_at"],
                "status": "NOT_DUE",
            })
        base["event_facts"]["future_scheduled_slots"] = future_slots

        due_at_text = evaluation.get("first_due_at")
        due_at = None
        if due_at_text is not None:
            due_at = _timestamp(due_at_text, "FIRST_DUE_AT")
        if not events and due_at is not None and evaluation_at < due_at:
            prior = evaluation.get("prior_as_of_date")
            if prior is None:
                base.update({
                    "market_eligibility": "DUE_COLLECTION_UNCONFIRMED",
                    "reasons": ["NO_PRIOR_OBSERVATION_BEFORE_DUE"],
                })
                return _finish(base)
            _date(prior, "PRIOR_AS_OF")
            base.update({
                "market_eligibility": "AWAITING_SCHEDULED_PUBLICATION",
                "displayed_as_of_date": prior,
                "decision_date": prior,
                "reasons": ["PUBLICATION_SLOT_NOT_DUE"],
            })
            return _finish(base)

        event_state, receipt = _event_status(events, evaluation_at, source_id, consumed)
        if event_state == "MISSING":
            base.update({
                "market_eligibility": "DUE_COLLECTION_UNCONFIRMED",
                "displayed_as_of_date": evaluation.get("prior_as_of_date"),
                "reasons": ["DUE_EVENT_HAS_NO_MATCHING_READBACK"],
            })
            return _finish(base)
        if receipt is not None:
            base["event_facts"] = {
                "evidence_type": receipt.get("evidence_type"),
                "observed_at": receipt.get("observed_at"),
                "completed_at_utc": receipt["terminal"].get("completed_at_utc"),
                "event_schedule": receipt["workflow"].get("event_schedule"),
                "slot_id": receipt["event"].get("slot_id"),
                "expected_at_utc": receipt["event"].get("expected_at_utc"),
                "expected_at_kst": receipt["event"].get("expected_at_kst"),
                "future_scheduled_slots": future_slots,
            }
        if event_state == "RUNNING":
            base.update({
                "market_eligibility": "COLLECTION_RUNNING_OR_QUEUED",
                "reasons": ["MATCHING_UPSTREAM_RUN_NOT_TERMINAL"],
            })
            return _finish(base)
        if event_state == "FAILED":
            base.update({
                "market_eligibility": "COLLECTION_FAILED",
                "displayed_as_of_date": evaluation.get("prior_as_of_date"),
                "reasons": [f"UPSTREAM_TERMINAL_{receipt['terminal']['conclusion'].upper()}"],
            })
            return _finish(base)

        components = evaluation.get("source_components", [])
        _require(isinstance(components, Sequence) and components,
                 "SUCCESS_SOURCE_COMPONENTS_REQUIRED")
        component_states: dict[str, str] = {}
        source_facts: dict[str, dict[str, Any]] = {}
        for component in components:
            _require(isinstance(component, Mapping), "SOURCE_COMPONENT_INVALID")
            state, observation_date, facts = _validate_source(
                component, receipt, evaluation_at, expected_date, consumed
            )
            component_states[component["source_id"]] = state
            source_facts[component["source_id"]] = facts

        if "SOURCE_NOT_ADVANCED_EXPECTED_SESSION" in component_states.values():
            state = "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
        elif "CURRENT_AS_FETCHED_NOT_PIT" in component_states.values():
            state = "CURRENT_AS_FETCHED_NOT_PIT"
        else:
            state = "CURRENT"

        judgement = _artifact(evaluation.get("judgement"), "judgement", consumed)
        _require("candidate_regime" in judgement, "JUDGEMENT_REGIME_MISSING")
        decision_date = judgement.get("decision_date", judgement.get("as_of_date"))
        decision = _date(decision_date, "JUDGEMENT_DECISION")
        _require(decision <= evaluation_at.date(), "FUTURE_JUDGEMENT_DECISION_DATE")
        price_date = judgement.get("price_date")
        if price_date is not None:
            price = _date(price_date, "JUDGEMENT_PRICE")
            _require(price <= evaluation_at.date(), "FUTURE_JUDGEMENT_PRICE_DATE")
        if judgement["candidate_regime"] != "UNKNOWN":
            _require(judgement.get("axes") == AXES, "JUDGEMENT_FIVE_AXIS_COVERAGE_REQUIRED")
        if state in CURRENT_STATES and expected_date is not None:
            _require(decision_date == expected_date, "JUDGEMENT_DECISION_DATE_NOT_CURRENT")

        caveats = list(judgement.get("caveats", []))
        _require(all(isinstance(value, str) for value in caveats), "CAVEATS_INVALID")
        if market == "CRYPTO":
            required = frozen_schema["comparability_and_caveats"]["required_crypto_caveats"]
            _require(all(value in caveats for value in required), "CRYPTO_CAVEATS_MISSING")

        prior_identity = evaluation.get("prior_observation_identity_sha256")
        prior_absent = evaluation.get("prior_observation_absent_confirmed")
        current_identity = receipt["source_output"].get("observation_identity_sha256")
        if prior_identity is not None:
            _require(isinstance(prior_identity, str) and SHA256.fullmatch(prior_identity),
                     "PRIOR_OBSERVATION_IDENTITY_INVALID")
            _require(prior_absent is not True,
                     "PRIOR_OBSERVATION_STATE_CONTRADICTORY")
        if prior_absent is not None:
            _require(type(prior_absent) is bool,
                     "PRIOR_OBSERVATION_ABSENCE_INVALID")
        new_observation = (
            state in CURRENT_STATES
            and (
                (prior_identity is not None and current_identity != prior_identity)
                or (prior_identity is None and prior_absent is True)
            )
        )
        if receipt["source_output"].get("publication_result") == "skipped_existing":
            new_observation = False
        required_assessments_complete = judgement.get(
            "required_assessments_complete", True
        ) is True
        classification_display = (
            state in CURRENT_STATES and judgement["candidate_regime"] != "UNKNOWN"
        )

        base.update({
            "market_eligibility": state,
            "component_eligibility": component_states,
            "source_facts": source_facts,
            "current_eligible": state in CURRENT_STATES,
            "classification_may_be_displayed": classification_display,
            "required_assessments_complete": required_assessments_complete,
            "judgement": dict(judgement),
            "displayed_as_of_date": decision_date,
            "decision_date": decision_date,
            "price_date": price_date,
            "hysteresis": {
                "increment": 1 if new_observation else 0,
                "new_market_observation": new_observation,
            },
            "caveats": caveats,
            "reasons": [state],
        })
        comparison_rows = evaluation.get("comparison_rows")
        if comparison_rows is not None:
            _require(isinstance(comparison_rows, Sequence), "COMPARISON_ROWS_INVALID")
            base["comparison"].update(compare_market_rows(comparison_rows))
        base["comparison"]["complete"] = (
            base["comparison"]["complete"] and required_assessments_complete
        )
        if not required_assessments_complete:
            base["comparison"]["three_market_comparison_status"] = "PARTIAL_OR_NON_COMPARABLE"
            base["reasons"].append("REQUIRED_ASSESSMENT_UNASSESSED")
        return _finish(base)
    except _EvidenceUnconfirmed as exc:
        base.update({
            "market_eligibility": "DUE_COLLECTION_UNCONFIRMED",
            "component_eligibility": {},
            "current_eligible": False,
            "classification_may_be_displayed": False,
            "hysteresis": {"increment": 0, "new_market_observation": False},
            "reasons": [str(exc)],
        })
        return _finish(base)
    except _EvidenceInvalid as exc:
        return _invalid_result(base, str(exc))


__all__ = [
    "PaperRegimeEligibilityError",
    "canonical_bytes",
    "compare_market_rows",
    "evaluate_paper_regime_eligibility",
    "sha256",
]
