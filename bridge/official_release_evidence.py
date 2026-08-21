#!/usr/bin/env python3
"""P4-04 explicit official-release observations -> evidence envelopes.

Only two already approved capabilities are registered: TSMC monthly revenue from
Investor Relations and Microsoft's earnings release acquired as SEC EX-99.1. This
module does not create a global source hierarchy, choose a fallback, interpret a
number, evaluate a Rule, or grant Production/trading authority.

The adapter is pure after contract loading. Callers provide collector output plus
acquisition provenance. Regression/synthetic fixtures remain blocked; missing
availability, content identity, or verbatim-slice proof remains blocked.
"""
from __future__ import annotations

import calendar
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "official_release_evidence_contract.json"

ENVELOPE_SCHEMA_VERSION = "evidence_envelope/1"
BUNDLE_SCHEMA_VERSION = "official_release_evidence_bundle/1"
EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"
EVIDENCE_BLOCKED = "EVIDENCE_BLOCKED"
EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"
STATUSES = (EVIDENCE_AVAILABLE, EVIDENCE_BLOCKED, EVIDENCE_UNRESOLVED)

SOURCE_IDENTITY_INCOMPLETE = "SOURCE_IDENTITY_INCOMPLETE"
AVAILABLE_AT_UNOBSERVED = "AVAILABLE_AT_UNOBSERVED"
CAPTURE_NOT_LIVE_OR_VERBATIM = "CAPTURE_NOT_LIVE_OR_VERBATIM"
SOURCE_SLICE_NOT_VERBATIM = "SOURCE_SLICE_NOT_VERBATIM"
COLLECTOR_NOT_DECISION_READY = "COLLECTOR_NOT_DECISION_READY"
REVISION_AUTHORITY_UNRESOLVED = "REVISION_AUTHORITY_UNRESOLVED"
OBSERVATION_ABSENT = "OBSERVATION_ABSENT"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
PCT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


class OfficialReleaseEvidenceError(ValueError):
    """Fail-closed contract violation."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfficialReleaseEvidenceError(f"CONTRACT_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise OfficialReleaseEvidenceError("CONTRACT_TOP_LEVEL_NOT_OBJECT")
    return value


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = _read_json(path)
    if contract.get("schema_version") != 1:
        raise OfficialReleaseEvidenceError("CONTRACT_SCHEMA_MISMATCH")
    if contract.get("source_hierarchy_status") != "UNRATIFIED":
        raise OfficialReleaseEvidenceError("SOURCE_HIERARCHY_MUST_REMAIN_UNRATIFIED")
    if contract.get("automatic_fallback_authorized") is not False:
        raise OfficialReleaseEvidenceError("AUTOMATIC_FALLBACK_MUST_REMAIN_FALSE")
    expected_authority = {
        "evidence_only": True,
        "source_ranking_authorized": False,
        "interpretation_authorized": False,
        "rule_evaluation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if contract.get("authority") != expected_authority:
        raise OfficialReleaseEvidenceError("AUTHORITY_BOUNDARY_MISMATCH")
    expected_profiles = {
        "tsmc_ir_monthly_revenue": {
            "subject": "TSM",
            "adapter": "tsmc_monthly/1",
            "identity_kind": "company_ir_web",
            "source_name": "TSMC Investor Relations — Historical Monthly Revenue",
            "canonical_url_prefix": "https://investor.tsmc.com/english/monthly-revenue/",
            "allowed_hosts": ["investor.tsmc.com"],
            "available_capture_kinds": ["LIVE_OFFICIAL_CAPTURE"],
        },
        "msft_official_earnings_release": {
            "subject": "MSFT",
            "adapter": "msft_azure_cc/1",
            "identity_kind": "company_official_release_sec_exhibit",
            "source_name": "Microsoft official earnings release filed as SEC EX-99.1",
            "canonical_url_prefix": "https://www.sec.gov/Archives/edgar/data/789019/",
            "allowed_hosts": ["www.sec.gov"],
            "available_capture_kinds": ["VERBATIM_SOURCE_SLICE"],
        },
    }
    if contract.get("profiles") != expected_profiles:
        raise OfficialReleaseEvidenceError("EXPLICIT_PROFILE_REGISTRY_MISMATCH")
    return contract


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _profile(contract: dict, source_id: str) -> dict:
    profile = (contract.get("profiles") or {}).get(source_id)
    if not isinstance(profile, dict):
        raise OfficialReleaseEvidenceError(f"SOURCE_PROFILE_NOT_REGISTERED:{source_id}")
    return profile


def _validate_url(url: str, profile: dict) -> None:
    parsed = urlparse(url or "")
    if (
        parsed.scheme != "https"
        or parsed.hostname not in profile["allowed_hosts"]
        or not url.startswith(profile["canonical_url_prefix"])
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OfficialReleaseEvidenceError(f"SOURCE_URL_OUTSIDE_PROFILE:{url!r}")


def _valid_sha(value) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _raw_pct(value) -> tuple[str, str]:
    raw = value if isinstance(value, str) else ""
    numeric = raw[:-1] if raw.endswith("%") else raw
    if not PCT_RE.fullmatch(numeric):
        raise OfficialReleaseEvidenceError(f"PERCENT_VALUE_INVALID:{value!r}")
    return raw if raw.endswith("%") else f"{raw}%", numeric


def _month_end(target_month: str) -> str:
    match = MONTH_RE.fullmatch(target_month or "")
    if not match:
        raise OfficialReleaseEvidenceError(f"TARGET_MONTH_INVALID:{target_month!r}")
    year, month = map(int, match.groups())
    if not 1 <= month <= 12:
        raise OfficialReleaseEvidenceError(f"TARGET_MONTH_INVALID:{target_month!r}")
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _period_end_iso(value: str) -> str:
    if isinstance(value, str) and DATE_RE.fullmatch(value):
        return value
    try:
        parsed = dt.datetime.strptime(value, "%B %d, %Y")
    except (TypeError, ValueError) as exc:
        raise OfficialReleaseEvidenceError(f"MSFT_PERIOD_END_INVALID:{value!r}") from exc
    if parsed.strftime("%B %-d, %Y") != value:
        raise OfficialReleaseEvidenceError(f"MSFT_PERIOD_END_INVALID:{value!r}")
    return parsed.date().isoformat()


def _common_capture(
    *, source_id: str, capture: dict, contract: dict
) -> tuple[dict, dict, list[str]]:
    if not isinstance(capture, dict):
        raise OfficialReleaseEvidenceError("CAPTURE_NOT_OBJECT")
    profile = _profile(contract, source_id)
    _validate_url(capture.get("source_url"), profile)
    blockers = []
    if not _valid_sha(capture.get("source_sha256")):
        blockers.append(SOURCE_IDENTITY_INCOMPLETE)
    retrieved = capture.get("retrieved_at_utc")
    if not isinstance(retrieved, str) or UTC_RE.fullmatch(retrieved) is None:
        blockers.append(SOURCE_IDENTITY_INCOMPLETE)
    available_at = capture.get("available_at")
    if not isinstance(available_at, str) or DATE_RE.fullmatch(available_at) is None:
        blockers.append(AVAILABLE_AT_UNOBSERVED)
    if capture.get("capture_kind") not in profile["available_capture_kinds"]:
        blockers.append(CAPTURE_NOT_LIVE_OR_VERBATIM)
    source_identity = {
        "identity_kind": profile["identity_kind"],
        "source_id": source_id,
        "source_name": profile["source_name"],
        "source_url": capture.get("source_url"),
        "source_sha256": capture.get("source_sha256"),
        "available_at": available_at,
        "retrieved_at_utc": retrieved,
    }
    return profile, source_identity, list(dict.fromkeys(blockers))


def _envelope(
    *, subject: str, measurement: str, period_end: str, source_identity: dict | None,
    audit_provenance: dict | None, observation: dict | None, blockers: list[str],
) -> dict:
    status = EVIDENCE_BLOCKED if blockers else EVIDENCE_AVAILABLE
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "subject": subject,
        "measurement_identity": measurement,
        "economic_period_end": period_end,
        "status": status,
        "reasons": list(blockers),
        "consumable": not blockers,
        "blocked_by": list(blockers),
        "acquisition_provenance_present": (
            source_identity is not None and audit_provenance is not None
        ),
        "source_identity": copy.deepcopy(source_identity),
        "audit_provenance": copy.deepcopy(audit_provenance),
        "observation": copy.deepcopy(observation) if not blockers else None,
    }


def unresolved_envelope(subject: str, measurement: str, period_end: str) -> dict:
    if not all(isinstance(x, str) and x for x in (subject, measurement, period_end)):
        raise OfficialReleaseEvidenceError("UNRESOLVED_KEY_INVALID")
    return validate_envelope({
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "subject": subject,
        "measurement_identity": measurement,
        "economic_period_end": period_end,
        "status": EVIDENCE_UNRESOLVED,
        "reasons": [OBSERVATION_ABSENT],
        "consumable": False,
        "blocked_by": [],
        "acquisition_provenance_present": False,
        "source_identity": None,
        "audit_provenance": None,
        "observation": None,
    })


def _valid_date(value) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_utc(value) -> bool:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return False
    try:
        return (
            dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            == value
        )
    except ValueError:
        return False


def _source_id_for_key(subject: str, measurement: str) -> str:
    if subject == "TSM" and measurement in {
        "TSMC consolidated net revenue monthly YoY",
        "TSMC consolidated net revenue cumulative YoY",
    }:
        return "tsmc_ir_monthly_revenue"
    if (
        subject == "MSFT"
        and measurement
        == "Azure and other cloud services revenue YoY constant currency"
    ):
        return "msft_official_earnings_release"
    raise OfficialReleaseEvidenceError("ENVELOPE_PROFILE_NOT_REGISTERED")


def _validate_source_identity(
    source: dict, source_id: str, audit: dict, contract: dict
) -> list[str]:
    profile = _profile(contract, source_id)
    common_fields = {
        "identity_kind",
        "source_id",
        "source_name",
        "source_url",
        "source_sha256",
        "available_at",
        "retrieved_at_utc",
    }
    expected_fields = (
        common_fields
        if source_id == "tsmc_ir_monthly_revenue"
        else common_fields
        | {"accession", "filing_date", "exhibit_type", "exhibit_document"}
    )
    if not isinstance(source, dict) or set(source) != expected_fields:
        raise OfficialReleaseEvidenceError("ENVELOPE_SOURCE_FIELDS_MISMATCH")
    if (
        source.get("identity_kind") != profile["identity_kind"]
        or source.get("source_id") != source_id
        or source.get("source_name") != profile["source_name"]
    ):
        raise OfficialReleaseEvidenceError("ENVELOPE_SOURCE_PROFILE_MISMATCH")
    _validate_url(source.get("source_url"), profile)
    blockers = []
    if not _valid_sha(source.get("source_sha256")) or not _valid_utc(
        source.get("retrieved_at_utc")
    ):
        blockers.append(SOURCE_IDENTITY_INCOMPLETE)
    available_at = source.get("available_at")
    if not _valid_date(available_at):
        blockers.append(AVAILABLE_AT_UNOBSERVED)
    if (
        _valid_date(available_at)
        and _valid_utc(source.get("retrieved_at_utc"))
        and dt.date.fromisoformat(available_at)
        > dt.datetime.strptime(
            source["retrieved_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).date()
    ):
        raise OfficialReleaseEvidenceError("ENVELOPE_SOURCE_TEMPORAL_ORDER_INVALID")
    capture_kind = audit.get("capture_kind") if isinstance(audit, dict) else None
    if capture_kind not in profile["available_capture_kinds"]:
        blockers.append(CAPTURE_NOT_LIVE_OR_VERBATIM)
    if source_id == "msft_official_earnings_release":
        accession = source.get("accession")
        exhibit = source.get("exhibit_document")
        if (
            not isinstance(accession, str)
            or re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) is None
            or source.get("exhibit_type") != "EX-99.1"
            or not isinstance(exhibit, str)
            or not exhibit.strip()
            or exhibit.strip() != exhibit
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_RELEASE_IDENTITY_INVALID")
        accession_path = accession.replace("-", "")
        source_path = urlparse(source["source_url"]).path
        if (
            f"/{accession_path}/" not in source_path
            or source_path.rsplit("/", 1)[-1] != exhibit
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_RELEASE_IDENTITY_INVALID")
        if source.get("filing_date") != available_at:
            blockers.append(AVAILABLE_AT_UNOBSERVED)
    return list(dict.fromkeys(blockers))


def _validate_observation(
    observation: dict,
    *,
    source_id: str,
    measurement: str,
    economic_period_end: str,
) -> None:
    fields = {
        "raw_value",
        "numeric_value",
        "unit",
        "sign_convention",
        "decision_column_identity",
        "row_label_raw",
        "period_end_raw",
        "observed_by",
    }
    if not isinstance(observation, dict) or set(observation) != fields:
        raise OfficialReleaseEvidenceError("ENVELOPE_OBSERVATION_FIELDS_MISMATCH")
    raw, numeric = _raw_pct(observation.get("raw_value"))
    if (
        observation.get("raw_value") != raw
        or observation.get("numeric_value") != numeric
        or observation.get("unit") != "pct"
        or not isinstance(observation.get("observed_by"), str)
        or not observation["observed_by"]
    ):
        raise OfficialReleaseEvidenceError("ENVELOPE_OBSERVATION_VALUE_MISMATCH")
    if source_id == "tsmc_ir_monthly_revenue":
        period_raw = observation.get("period_end_raw")
        if _month_end(period_raw) != economic_period_end:
            raise OfficialReleaseEvidenceError("ENVELOPE_OBSERVATION_PERIOD_MISMATCH")
        cumulative = measurement.endswith("cumulative YoY")
        expected_row = "Total" if cumulative else period_raw
        expected_column = "Total YoY Change" if cumulative else "YoY Change"
        if (
            observation.get("sign_convention") != "minus_or_none"
            or observation.get("row_label_raw") != expected_row
            or observation.get("decision_column_identity") != expected_column
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_OBSERVATION_IDENTITY_MISMATCH")
    else:
        if _period_end_iso(observation.get("period_end_raw")) != economic_period_end:
            raise OfficialReleaseEvidenceError("ENVELOPE_OBSERVATION_PERIOD_MISMATCH")
        if (
            observation.get("sign_convention") != "parens_minus_or_none"
            or observation.get("decision_column_identity")
            != "Percentage Change Y/Y Constant Currency"
            or observation.get("row_label_raw")
            != "Azure and other cloud services[ revenue]"
            or observation.get("observed_by") != "msft_azure_cc"
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_OBSERVATION_IDENTITY_MISMATCH")


def _validate_audit(
    audit: dict,
    *,
    source_id: str,
    measurement: str,
    economic_period_end: str,
) -> list[str]:
    blockers = []
    if source_id == "tsmc_ir_monthly_revenue":
        if not isinstance(audit, dict) or set(audit) != {
            "capture_kind",
            "collector_version",
            "source_locator",
        }:
            raise OfficialReleaseEvidenceError("ENVELOPE_AUDIT_FIELDS_MISMATCH")
        if not isinstance(audit.get("collector_version"), str) or not audit[
            "collector_version"
        ]:
            raise OfficialReleaseEvidenceError("ENVELOPE_COLLECTOR_VERSION_INVALID")
        locator = audit.get("source_locator")
        cumulative = measurement.endswith("cumulative YoY")
        expected_fields = (
            {"table", "row", "column", "through_month"}
            if cumulative
            else {"table", "row", "column"}
        )
        if not isinstance(locator, dict) or set(locator) != expected_fields:
            raise OfficialReleaseEvidenceError("ENVELOPE_SOURCE_LOCATOR_MISMATCH")
        target_month = locator.get("through_month") if cumulative else locator.get("row")
        if (
            _month_end(target_month) != economic_period_end
            or locator.get("table") != f"{target_month[:4]} Monthly Revenue"
            or locator.get("row") != ("Total" if cumulative else target_month)
            or locator.get("column") != "YoY Change"
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_SOURCE_LOCATOR_MISMATCH")
    else:
        if not isinstance(audit, dict) or set(audit) != {
            "capture_kind",
            "slice_sha256",
            "verbatim_substring_of_source",
            "source_locator",
        }:
            raise OfficialReleaseEvidenceError("ENVELOPE_AUDIT_FIELDS_MISMATCH")
        locator = audit.get("source_locator")
        if locator != {
            "table": "Selected Product and Service Constant Currency Reconciliation",
            "row": "Azure and other cloud services[ revenue]",
            "column": "Percentage Change Y/Y Constant Currency",
        }:
            raise OfficialReleaseEvidenceError("ENVELOPE_SOURCE_LOCATOR_MISMATCH")
        if (
            audit.get("verbatim_substring_of_source") is not True
            or not _valid_sha(audit.get("slice_sha256"))
        ):
            blockers.append(SOURCE_SLICE_NOT_VERBATIM)
    return blockers


def validate_envelope(envelope: dict, contract: dict | None = None) -> dict:
    """Validate a persisted official-release evidence envelope."""
    contract = contract or load_contract()
    fields = {
        "schema_version",
        "subject",
        "measurement_identity",
        "economic_period_end",
        "status",
        "reasons",
        "consumable",
        "blocked_by",
        "acquisition_provenance_present",
        "source_identity",
        "audit_provenance",
        "observation",
    }
    if not isinstance(envelope, dict) or set(envelope) != fields:
        raise OfficialReleaseEvidenceError("ENVELOPE_FIELDS_MISMATCH")
    subject = envelope.get("subject")
    measurement = envelope.get("measurement_identity")
    period_end = envelope.get("economic_period_end")
    status = envelope.get("status")
    reasons = envelope.get("reasons")
    blocked_by = envelope.get("blocked_by")
    if (
        envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION
        or not all(isinstance(value, str) and value for value in (subject, measurement))
        or not _valid_date(period_end)
        or status not in STATUSES
        or not isinstance(reasons, list)
        or not isinstance(blocked_by, list)
        or reasons != list(dict.fromkeys(reasons))
        or blocked_by != list(dict.fromkeys(blocked_by))
    ):
        raise OfficialReleaseEvidenceError("ENVELOPE_IDENTITY_OR_STATUS_INVALID")
    if status == EVIDENCE_UNRESOLVED:
        if (
            reasons != [OBSERVATION_ABSENT]
            or blocked_by != []
            or envelope.get("consumable") is not False
            or envelope.get("acquisition_provenance_present") is not False
            or envelope.get("source_identity") is not None
            or envelope.get("audit_provenance") is not None
            or envelope.get("observation") is not None
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_UNRESOLVED_STATE_MISMATCH")
        return copy.deepcopy(envelope)

    if reasons == [REVISION_AUTHORITY_UNRESOLVED]:
        audit = envelope.get("audit_provenance")
        hashes = (audit or {}).get("revision_candidate_source_sha256")
        if (
            status != EVIDENCE_BLOCKED
            or blocked_by != reasons
            or envelope.get("consumable") is not False
            or envelope.get("acquisition_provenance_present") is not False
            or envelope.get("source_identity") is not None
            or envelope.get("observation") is not None
            or not isinstance(audit, dict)
            or set(audit) != {"revision_candidate_source_sha256"}
            or not isinstance(hashes, list)
            or not hashes
            or hashes != sorted(set(hashes))
            or any(not _valid_sha(value) for value in hashes)
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_REVISION_STATE_MISMATCH")
        _source_id_for_key(subject, measurement)
        return copy.deepcopy(envelope)

    source_id = _source_id_for_key(subject, measurement)
    audit = envelope.get("audit_provenance")
    source = envelope.get("source_identity")
    if not isinstance(source, dict) or not isinstance(audit, dict):
        raise OfficialReleaseEvidenceError("ENVELOPE_PROVENANCE_MISSING")
    derived_blockers = _validate_source_identity(source, source_id, audit, contract)
    derived_blockers.extend(
        _validate_audit(
            audit,
            source_id=source_id,
            measurement=measurement,
            economic_period_end=period_end,
        )
    )
    derived_blockers = list(dict.fromkeys(derived_blockers))
    allowed = {
        SOURCE_IDENTITY_INCOMPLETE,
        AVAILABLE_AT_UNOBSERVED,
        CAPTURE_NOT_LIVE_OR_VERBATIM,
        SOURCE_SLICE_NOT_VERBATIM,
        COLLECTOR_NOT_DECISION_READY,
    }
    if any(reason not in allowed for reason in reasons) or any(
        blocker not in reasons for blocker in derived_blockers
    ):
        raise OfficialReleaseEvidenceError("ENVELOPE_BLOCKER_DERIVATION_MISMATCH")
    if status == EVIDENCE_AVAILABLE:
        if (
            reasons
            or blocked_by
            or envelope.get("consumable") is not True
            or envelope.get("acquisition_provenance_present") is not True
            or envelope.get("observation") is None
        ):
            raise OfficialReleaseEvidenceError("ENVELOPE_AVAILABLE_STATE_MISMATCH")
        _validate_observation(
            envelope["observation"],
            source_id=source_id,
            measurement=measurement,
            economic_period_end=period_end,
        )
    elif (
        not reasons
        or blocked_by != reasons
        or envelope.get("consumable") is not False
        or envelope.get("acquisition_provenance_present") is not True
        or envelope.get("observation") is not None
    ):
        raise OfficialReleaseEvidenceError("ENVELOPE_BLOCKED_STATE_MISMATCH")
    return copy.deepcopy(envelope)


def validate_bundle(value: dict, contract: dict | None = None) -> dict:
    """Validate a persisted bundle, every envelope, summary, authority, and digest."""
    contract = contract or load_contract()
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "source_hierarchy_status",
        "automatic_fallback_authorized",
        "authority",
        "summary",
        "envelopes",
        "bundle_sha256",
    }:
        raise OfficialReleaseEvidenceError("BUNDLE_FIELDS_MISMATCH")
    digest = value.get("bundle_sha256")
    unsigned = copy.deepcopy(value)
    unsigned.pop("bundle_sha256")
    if not _valid_sha(digest) or payload_sha256(unsigned) != digest:
        raise OfficialReleaseEvidenceError("BUNDLE_SHA256_MISMATCH")
    envelopes = value.get("envelopes")
    if not isinstance(envelopes, list):
        raise OfficialReleaseEvidenceError("BUNDLE_ENVELOPES_NOT_LIST")
    checked = [validate_envelope(envelope, contract) for envelope in envelopes]
    keys = [_key(envelope) for envelope in checked]
    if keys != sorted(set(keys)):
        raise OfficialReleaseEvidenceError("BUNDLE_ENVELOPE_ORDER_INVALID")
    counts = {status: 0 for status in STATUSES}
    for envelope in checked:
        counts[envelope["status"]] += 1
    if (
        value.get("schema_version") != BUNDLE_SCHEMA_VERSION
        or value.get("source_hierarchy_status") != contract["source_hierarchy_status"]
        or value.get("automatic_fallback_authorized")
        is not contract["automatic_fallback_authorized"]
        or value.get("authority") != contract["authority"]
        or value.get("summary") != {"total": len(checked), **counts}
    ):
        raise OfficialReleaseEvidenceError("BUNDLE_SUMMARY_OR_AUTHORITY_MISMATCH")
    return copy.deepcopy(value)


def tsmc_monthly_envelopes(
    normalized: dict, capture: dict, contract: dict | None = None
) -> list[dict]:
    """Normalize the existing TSMC collector output without applying thresholds."""
    if not isinstance(normalized, dict):
        raise OfficialReleaseEvidenceError("TSMC_COLLECTOR_OUTPUT_NOT_OBJECT")
    contract = contract or load_contract()
    source_id = "tsmc_ir_monthly_revenue"
    profile, source_identity, blockers = _common_capture(
        source_id=source_id, capture=capture, contract=contract
    )
    if profile["adapter"] != "tsmc_monthly/1" or normalized.get("source_url") != (
        "https://investor.tsmc.com/english/monthly-revenue"
    ):
        raise OfficialReleaseEvidenceError("TSMC_COLLECTOR_SOURCE_IDENTITY_MISMATCH")
    published_at = normalized.get("published_at")
    if not isinstance(published_at, str) or DATE_RE.fullmatch(published_at) is None:
        blockers.append(AVAILABLE_AT_UNOBSERVED)
    if capture.get("available_at") != published_at:
        blockers.append(AVAILABLE_AT_UNOBSERVED)
    if normalized.get("decision_ready") is not True:
        blockers.append(COLLECTOR_NOT_DECISION_READY)
    months = normalized.get("months")
    if not isinstance(months, dict) or not months:
        raise OfficialReleaseEvidenceError("TSMC_MONTHS_EMPTY")

    blockers = list(dict.fromkeys(blockers))
    out = []
    for target_month, row in sorted(months.items()):
        raw, numeric = _raw_pct(row.get("monthly_yoy_pct_published"))
        out.append(_envelope(
            subject="TSM",
            measurement="TSMC consolidated net revenue monthly YoY",
            period_end=_month_end(target_month),
            source_identity=source_identity,
            audit_provenance={
                "capture_kind": capture.get("capture_kind"),
                "collector_version": normalized.get("collector_version"),
                "source_locator": {
                    "table": f"{normalized.get('year')} Monthly Revenue",
                    "row": target_month,
                    "column": "YoY Change",
                },
            },
            observation={
                "raw_value": raw,
                "numeric_value": numeric,
                "unit": "pct",
                "sign_convention": "minus_or_none",
                "decision_column_identity": "YoY Change",
                "row_label_raw": target_month,
                "period_end_raw": target_month,
                "observed_by": normalized.get("collector_version"),
            },
            blockers=blockers,
        ))

    cumulative = normalized.get("cumulative") or {}
    through = cumulative.get("through_month")
    raw, numeric = _raw_pct(cumulative.get("cumulative_yoy_pct_published"))
    out.append(_envelope(
        subject="TSM",
        measurement="TSMC consolidated net revenue cumulative YoY",
        period_end=_month_end(through),
        source_identity=source_identity,
        audit_provenance={
            "capture_kind": capture.get("capture_kind"),
            "collector_version": normalized.get("collector_version"),
            "source_locator": {
                "table": f"{normalized.get('year')} Monthly Revenue",
                "row": "Total",
                "column": "YoY Change",
                "through_month": through,
            },
        },
        observation={
            "raw_value": raw,
            "numeric_value": numeric,
            "unit": "pct",
            "sign_convention": "minus_or_none",
            "decision_column_identity": "Total YoY Change",
            "row_label_raw": "Total",
            "period_end_raw": through,
            "observed_by": normalized.get("collector_version"),
        },
        blockers=blockers,
    ))
    return [validate_envelope(envelope, contract) for envelope in out]


def msft_azure_envelope(
    observation: dict, capture: dict, contract: dict | None = None
) -> dict:
    """Normalize the existing Azure constant-currency official-release observation."""
    if not isinstance(observation, dict):
        raise OfficialReleaseEvidenceError("MSFT_OBSERVATION_NOT_OBJECT")
    contract = contract or load_contract()
    source_id = "msft_official_earnings_release"
    profile, source_identity, blockers = _common_capture(
        source_id=source_id, capture=capture, contract=contract
    )
    if profile["adapter"] != "msft_azure_cc/1":
        raise OfficialReleaseEvidenceError("MSFT_ADAPTER_MISMATCH")
    period_end_raw = observation.get("period_end")
    period_end = _period_end_iso(period_end_raw)
    if observation.get("accession") != capture.get("accession") or (
        observation.get("exhibit") != capture.get("exhibit_document")
    ):
        raise OfficialReleaseEvidenceError("MSFT_RELEASE_IDENTITY_MISMATCH")
    filing_date = observation.get("filing_date")
    if not isinstance(filing_date, str) or DATE_RE.fullmatch(filing_date) is None:
        blockers.extend([SOURCE_IDENTITY_INCOMPLETE, AVAILABLE_AT_UNOBSERVED])
    if capture.get("available_at") != filing_date:
        blockers.append(AVAILABLE_AT_UNOBSERVED)
    if capture.get("verbatim_substring_of_source") is not True or not _valid_sha(
        capture.get("slice_sha256")
    ):
        blockers.append(SOURCE_SLICE_NOT_VERBATIM)
    source_identity.update({
        "accession": capture.get("accession"),
        "filing_date": filing_date,
        "exhibit_type": "EX-99.1",
        "exhibit_document": capture.get("exhibit_document"),
    })
    raw, numeric = _raw_pct(observation.get("azure_cc_growth_pct"))
    return validate_envelope(_envelope(
        subject="MSFT",
        measurement="Azure and other cloud services revenue YoY constant currency",
        period_end=period_end,
        source_identity=source_identity,
        audit_provenance={
            "capture_kind": capture.get("capture_kind"),
            "slice_sha256": capture.get("slice_sha256"),
            "verbatim_substring_of_source": capture.get("verbatim_substring_of_source"),
            "source_locator": {
                "table": "Selected Product and Service Constant Currency Reconciliation",
                "row": "Azure and other cloud services[ revenue]",
                "column": "Percentage Change Y/Y Constant Currency",
            },
        },
        observation={
            "raw_value": raw,
            "numeric_value": numeric,
            "unit": "pct",
            "sign_convention": "parens_minus_or_none",
            "decision_column_identity": "Percentage Change Y/Y Constant Currency",
            "row_label_raw": "Azure and other cloud services[ revenue]",
            "period_end_raw": period_end_raw,
            "observed_by": "msft_azure_cc",
        },
        blockers=list(dict.fromkeys(blockers)),
    ), contract)


def _key(envelope: dict) -> tuple[str, str, str]:
    return (
        envelope.get("subject"),
        envelope.get("measurement_identity"),
        envelope.get("economic_period_end"),
    )


def reconcile(
    envelopes: list[dict], contract: dict | None = None
) -> list[dict]:
    """Deduplicate identical envelopes; block distinct source revisions per key."""
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    contract = contract or load_contract()
    for raw_envelope in envelopes:
        envelope = validate_envelope(raw_envelope, contract)
        key = _key(envelope)
        if not all(isinstance(x, str) and x for x in key):
            raise OfficialReleaseEvidenceError("ENVELOPE_KEY_INVALID")
        grouped.setdefault(key, []).append(copy.deepcopy(envelope))

    out = []
    for key, candidates in sorted(grouped.items()):
        unique = {payload_sha256(candidate): candidate for candidate in candidates}
        if len(unique) == 1:
            out.append(next(iter(unique.values())))
            continue
        hashes = sorted({
            (candidate.get("source_identity") or {}).get("source_sha256")
            for candidate in unique.values()
            if (candidate.get("source_identity") or {}).get("source_sha256")
        })
        out.append(_envelope(
            subject=key[0],
            measurement=key[1],
            period_end=key[2],
            source_identity=None,
            audit_provenance={"revision_candidate_source_sha256": hashes},
            observation=None,
            blockers=[REVISION_AUTHORITY_UNRESOLVED],
        ))
    return [validate_envelope(envelope, contract) for envelope in out]


def bundle(envelopes: list[dict], contract: dict | None = None) -> dict:
    contract = contract or load_contract()
    reconciled = reconcile(envelopes, contract)
    counts = {status: 0 for status in STATUSES}
    for envelope in reconciled:
        counts[envelope["status"]] += 1
    body = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_hierarchy_status": contract["source_hierarchy_status"],
        "automatic_fallback_authorized": contract["automatic_fallback_authorized"],
        "authority": copy.deepcopy(contract["authority"]),
        "summary": {"total": len(reconciled), **counts},
        "envelopes": reconciled,
    }
    body["bundle_sha256"] = payload_sha256(body)
    return validate_bundle(body, contract)
