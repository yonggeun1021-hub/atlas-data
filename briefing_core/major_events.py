"""Major-event coverage gate and deterministic correction adapter."""

from __future__ import annotations

import copy
import re
from typing import Any


REGISTRY_SCHEMA = "major_event_registry/1"
COVERAGE_SCHEMA = "major_event_coverage/1"
VALIDATION_SCHEMA = "major_event_coverage_validation/1"

REQUIRED_CHANNELS = {
    "oil_shipping",
    "hormuz",
    "usd_rates",
    "equity_risk_appetite",
    "defense",
}


class MajorEventError(RuntimeError):
    pass


def validate_registry(value: dict, *, briefing_date: str, slot: str) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != REGISTRY_SCHEMA:
        raise MajorEventError("MAJOR_EVENT_REGISTRY_SCHEMA_INVALID")
    if value.get("briefing_date") != briefing_date or value.get("slot") != slot:
        raise MajorEventError("MAJOR_EVENT_REGISTRY_IDENTITY_MISMATCH")
    source_status = value.get("source_status")
    if source_status not in {"AVAILABLE", "UNAVAILABLE"}:
        raise MajorEventError("MAJOR_EVENT_SOURCE_STATUS_INVALID")
    events = value.get("events")
    if not isinstance(events, list):
        raise MajorEventError("MAJOR_EVENT_ARRAY_INVALID")
    if source_status == "UNAVAILABLE":
        if events:
            raise MajorEventError("MAJOR_EVENT_UNAVAILABLE_WITH_EVENTS")
        return value
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise MajorEventError("MAJOR_EVENT_INVALID")
        required = {
            "event_id", "importance", "detected_at", "display_headline_ko",
            "sources", "claims", "transmission_channels",
        }
        if set(event) != required:
            raise MajorEventError("MAJOR_EVENT_FIELDS_INVALID")
        event_id = event.get("event_id")
        if (
            not isinstance(event_id, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,99}", event_id) is None
            or event_id in seen
        ):
            raise MajorEventError("MAJOR_EVENT_ID_INVALID")
        seen.add(event_id)
        if event.get("importance") not in {"HIGH", "CRITICAL"}:
            raise MajorEventError("MAJOR_EVENT_IMPORTANCE_INVALID")
        headline = event.get("display_headline_ko")
        if not isinstance(headline, str) or not headline.strip():
            raise MajorEventError("MAJOR_EVENT_HEADLINE_MISSING")
        sources = event.get("sources")
        if not isinstance(sources, list) or len(sources) < 2:
            raise MajorEventError("MAJOR_EVENT_CROSSCHECK_MISSING")
        grades = {source.get("grade") for source in sources if isinstance(source, dict)}
        if not {"PRIMARY_OFFICIAL", "INDEPENDENT_MAJOR_MEDIA"}.issubset(grades):
            raise MajorEventError("MAJOR_EVENT_SOURCE_GRADE_MISSING")
        source_ids: set[str] = set()
        for source in sources:
            if not isinstance(source, dict) or set(source) != {
                "source_id", "grade", "title", "url", "published_at", "supports_claim_ids"
            }:
                raise MajorEventError("MAJOR_EVENT_SOURCE_FIELDS_INVALID")
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or not source_id or source_id in source_ids:
                raise MajorEventError("MAJOR_EVENT_SOURCE_ID_INVALID")
            source_ids.add(source_id)
            if not str(source.get("url", "")).startswith("https://"):
                raise MajorEventError("MAJOR_EVENT_SOURCE_URL_INVALID")
            if not isinstance(source.get("supports_claim_ids"), list):
                raise MajorEventError("MAJOR_EVENT_SOURCE_CLAIMS_INVALID")
        claims = event.get("claims")
        if not isinstance(claims, list) or not claims:
            raise MajorEventError("MAJOR_EVENT_CLAIMS_MISSING")
        claim_ids: set[str] = set()
        classifications: set[str] = set()
        for claim in claims:
            if not isinstance(claim, dict) or set(claim) != {
                "claim_id", "classification", "statement_ko", "source_ids"
            }:
                raise MajorEventError("MAJOR_EVENT_CLAIM_FIELDS_INVALID")
            claim_id = claim.get("claim_id")
            classification = claim.get("classification")
            if not isinstance(claim_id, str) or not claim_id or claim_id in claim_ids:
                raise MajorEventError("MAJOR_EVENT_CLAIM_ID_INVALID")
            if classification not in {"FACT", "INFERENCE", "UNKNOWN"}:
                raise MajorEventError("MAJOR_EVENT_CLAIM_CLASSIFICATION_INVALID")
            claim_ids.add(claim_id)
            classifications.add(classification)
            refs = claim.get("source_ids")
            if (
                not isinstance(refs, list)
                or any(ref not in source_ids for ref in refs)
                or (classification == "FACT" and not refs)
            ):
                raise MajorEventError("MAJOR_EVENT_CLAIM_SOURCE_INVALID")
        if "FACT" not in classifications or not {"INFERENCE", "UNKNOWN"}.intersection(classifications):
            raise MajorEventError("MAJOR_EVENT_FACT_INFERENCE_SPLIT_MISSING")
        for source in sources:
            if any(claim_id not in claim_ids for claim_id in source["supports_claim_ids"]):
                raise MajorEventError("MAJOR_EVENT_SOURCE_UNKNOWN_CLAIM")
        channels = event.get("transmission_channels")
        if not isinstance(channels, list) or {
            channel.get("channel") for channel in channels if isinstance(channel, dict)
        } != REQUIRED_CHANNELS:
            raise MajorEventError("MAJOR_EVENT_TRANSMISSION_CHANNELS_INCOMPLETE")
        for channel in channels:
            if not isinstance(channel, dict) or set(channel) != {
                "channel", "classification", "statement_ko", "source_claim_ids",
                "price_causality_confirmed",
            }:
                raise MajorEventError("MAJOR_EVENT_TRANSMISSION_FIELDS_INVALID")
            if channel.get("classification") not in {"INFERENCE", "UNKNOWN"}:
                raise MajorEventError("MAJOR_EVENT_TRANSMISSION_CAUSALITY_OVERCLAIM")
            if channel.get("price_causality_confirmed") is not False:
                raise MajorEventError("MAJOR_EVENT_PRICE_CAUSALITY_OVERCLAIM")
            if any(ref not in claim_ids for ref in channel.get("source_claim_ids", [])):
                raise MajorEventError("MAJOR_EVENT_TRANSMISSION_SOURCE_INVALID")
    return value


def unavailable_coverage(reason: str = "MAJOR_NEWS_SOURCE_UNAVAILABLE") -> dict:
    return {
        "schema_version": COVERAGE_SCHEMA,
        "section_title_ko": "오늘의 핵심 사건",
        "status": "DEGRADED",
        "user_message_ko": "주요 뉴스 검증 불가",
        "events": [],
        "reason_codes": [reason],
        "complete_market_conclusion_allowed": False,
        "risk_on_off_conclusion_allowed": False,
        "capital_allocation_conclusion_allowed": False,
    }


def validate_coverage(handoff: dict, registry: dict) -> dict:
    coverage = handoff.get("major_event_coverage")
    if registry.get("source_status") == "UNAVAILABLE":
        valid_degraded = (
            isinstance(coverage, dict)
            and coverage.get("status") == "DEGRADED"
            and coverage.get("complete_market_conclusion_allowed") is False
            and coverage.get("risk_on_off_conclusion_allowed") is False
            and coverage.get("capital_allocation_conclusion_allowed") is False
        )
        return {
            "schema_version": VALIDATION_SCHEMA,
            "status": "DEGRADED" if valid_degraded else "CORRECTION_REQUIRED",
            "portal_allowed": valid_degraded,
            "reason_codes": (
                ["MAJOR_NEWS_VERIFICATION_UNAVAILABLE"]
                if valid_degraded else ["MAJOR_EVENT_DEGRADED_DISCLOSURE_MISSING"]
            ),
        }
    expected = {event["event_id"] for event in registry["events"]}
    actual = {
        event.get("event_id")
        for event in coverage.get("events", [])
        if isinstance(coverage, dict) and isinstance(event, dict)
    } if isinstance(coverage, dict) else set()
    missing = sorted(expected - actual)
    if missing:
        return {
            "schema_version": VALIDATION_SCHEMA,
            "status": "CORRECTION_REQUIRED",
            "portal_allowed": False,
            "reason_codes": ["MAJOR_EVENT_COVERAGE_MISSING"],
            "missing_event_ids": missing,
        }
    if coverage.get("status") != "VERIFIED" or coverage.get("section_title_ko") != "오늘의 핵심 사건":
        return {
            "schema_version": VALIDATION_SCHEMA,
            "status": "CORRECTION_REQUIRED",
            "portal_allowed": False,
            "reason_codes": ["MAJOR_EVENT_SECTION_INVALID"],
            "missing_event_ids": [],
        }
    return {
        "schema_version": VALIDATION_SCHEMA,
        "status": "PASS",
        "portal_allowed": True,
        "reason_codes": [],
        "missing_event_ids": [],
    }


def correct_handoff(handoff: dict, registry: dict) -> dict:
    """Apply a source-bound correction without overwriting the draft."""
    corrected = copy.deepcopy(handoff)
    events = []
    for event in registry["events"]:
        facts = [claim for claim in event["claims"] if claim["classification"] == "FACT"]
        inferences = [claim for claim in event["claims"] if claim["classification"] == "INFERENCE"]
        unknowns = [claim for claim in event["claims"] if claim["classification"] == "UNKNOWN"]
        events.append({
            "event_id": event["event_id"],
            "importance": event["importance"],
            "headline_ko": event["display_headline_ko"],
            "facts": facts,
            "inferences": inferences,
            "unknowns": unknowns,
            "transmission_channels": event["transmission_channels"],
            "source_ids": [source["source_id"] for source in event["sources"]],
        })
    corrected["major_event_coverage"] = {
        "schema_version": COVERAGE_SCHEMA,
        "section_title_ko": "오늘의 핵심 사건",
        "status": "VERIFIED",
        "user_message_ko": events[0]["headline_ko"] if events else "확인된 중대 사건 없음",
        "events": events,
        "reason_codes": [],
        "complete_market_conclusion_allowed": False,
        "risk_on_off_conclusion_allowed": False,
        "capital_allocation_conclusion_allowed": False,
    }
    history = corrected.setdefault("correction_history", [])
    history.append({
        "correction_type": "MAJOR_EVENT_COVERAGE",
        "reason": "MAJOR_EVENT_COVERAGE_MISSING",
        "source": "CODEX_DETERMINISTIC_SOURCE_BOUND_CORRECTION",
        "overwrites_prior_revision": False,
    })
    return corrected


def render_corrected_briefing(original: bytes, coverage: dict) -> bytes:
    """Render a new user-facing revision; never mutate the sealed source bytes."""
    if coverage.get("status") != "VERIFIED" or not coverage.get("events"):
        raise MajorEventError("MAJOR_EVENT_CORRECTED_BRIEFING_NOT_VERIFIED")
    try:
        source = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MajorEventError("MAJOR_EVENT_BRIEFING_NOT_UTF8") from exc
    lines = ["## 오늘의 핵심 사건", ""]
    for event in coverage["events"]:
        lines.extend([f"### {event['headline_ko']}", "", "확인된 사실:"])
        lines.extend(f"- {claim['statement_ko']}" for claim in event["facts"])
        lines.extend(["", "상황평가:"])
        lines.extend(f"- {claim['statement_ko']}" for claim in event["inferences"])
        lines.extend(["", "아직 확인되지 않은 점:"])
        lines.extend(f"- {claim['statement_ko']}" for claim in event["unknowns"])
        lines.extend(["", "시장 전달경로(인과 확정 아님):"])
        lines.extend(
            f"- `{channel['channel']}`: {channel['statement_ko']}"
            for channel in event["transmission_channels"]
        )
        lines.append("")
    section = "\n".join(lines).rstrip() + "\n\n"
    source = source.lstrip("\ufeff")
    source_lines = source.splitlines(keepends=True)
    if source_lines and source_lines[0].startswith("# "):
        rendered = (
            source_lines[0].rstrip("\r\n")
            + "\n\n"
            + section
            + "".join(source_lines[1:]).lstrip("\r\n")
        )
    else:
        rendered = section + source
    return rendered.rstrip().encode("utf-8") + b"\n"
