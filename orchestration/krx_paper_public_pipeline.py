#!/usr/bin/env python3
"""Fail-closed public KRX completed-bar -> Shadow -> P8-13 pipeline.

The module only emits a sanitized readiness/proposal packet.  It re-runs the
merged public validators and never contacts KIS, creates an order draft,
routes to a broker, or writes a quantity-bearing/private ledger artifact.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

from decision import krx_paper_proposal_bridge as PROPOSAL
from decision import krx_shadow_strategy as SHADOW
from market_data import krx_session_bars as MARKET_DATA
from shadow import krx_paper_gate as GATE


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_paper_public_pipeline_contract.json"
PIPELINE_INPUT_SCHEMA = "krx_paper_public_pipeline_input/1"
PIPELINE_OUTPUT_SCHEMA = "krx_paper_public_pipeline_packet/1"
PIPELINE_CONTRACT_VERSION = "krx_paper_public_pipeline/1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SYMBOL_RE = re.compile(r"^[0-9A-Z]{6}$")
KST = ZoneInfo("Asia/Seoul")

SOURCE_PIN_EXPECTATIONS = {
    "completed_bars": (
        "37e07659aa934e9a6e09b27b786dc49203060af1",
        "config/krx_market_data_contract.json",
        "krx_completed_market_data/1",
        "437b07ec2f1c35ee56236a5044e73bc9b566faa2350d7fe9bc14292ce8061649",
    ),
    "execution_measurement": (
        "7446cc2ba8261ab09cf40cd4daf2d3fe1a1bb17e",
        "config/krx_execution_measurement_contract.json",
        "krx_execution_measurement/1",
        "4271d20b1aface74834c5c7a4c529ab3cc05402ecfe0992eeb8441aea403fddc",
    ),
    "shadow": (
        "7353be0dc26af8d6cacf2115c07d68358b5d607f",
        "config/krx_shadow_strategy_contract.json",
        "krx_shadow_strategy/1",
        "8024e420b56287ede05796fb68a917b197d5ce855d586da80b06c5504c2a0a01",
    ),
    "proposal": (
        "f70249c306c3d069d7c3a549ac7c87f4f2bcf37f",
        "config/krx_paper_proposal_bridge_contract.json",
        "krx_paper_proposal_bridge/1",
        "c9f18ad2aca1d33aac7aaaffaa8e8c9c76206e95694fca6d785cdcf906275e1f",
    ),
}

AUTHORITY = {
    "public_readiness_only": True,
    "candidate_selection_authorized": False,
    "internal_virtual_ledger_authorized": False,
    "paper_order_write": False,
    "kis_submission_authorized": False,
    "live_account_authorized": False,
    "real_capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class KrxPaperPublicPipelineError(ValueError):
    """A public pipeline packet or contract is not semantically valid."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KrxPaperPublicPipelineError(f"JSON_READ_FAILED:{path}") from exc
    if not isinstance(value, dict):
        raise KrxPaperPublicPipelineError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise KrxPaperPublicPipelineError(code)
    return value


def _commit(value: object, code: str) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise KrxPaperPublicPipelineError(code)
    return value


def _utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise KrxPaperPublicPipelineError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise KrxPaperPublicPipelineError(code) from exc


def _date(value: object, code: str) -> str:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise KrxPaperPublicPipelineError(code)
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KrxPaperPublicPipelineError(code) from exc
    return value


def _stage_digest(value: dict, code: str) -> str:
    if not isinstance(value, dict):
        raise KrxPaperPublicPipelineError(code)
    claimed = _sha(value.get("payload_sha256"), code)
    unsigned = copy.deepcopy(value)
    unsigned.pop("payload_sha256", None)
    if payload_sha256(unsigned) != claimed:
        raise KrxPaperPublicPipelineError(code + "_MISMATCH")
    return claimed


def _not_fixture(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KrxPaperPublicPipelineError(code)
    lowered = value.lower()
    if any(token in lowered for token in ("fixture", "synthetic", "test://")):
        raise KrxPaperPublicPipelineError(code)
    return value


def validate_contract(value: object) -> dict:
    if not isinstance(value, dict):
        raise KrxPaperPublicPipelineError("CONTRACT_NOT_OBJECT")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != PIPELINE_CONTRACT_VERSION
        or value.get("input_schema_version") != PIPELINE_INPUT_SCHEMA
        or value.get("output_schema_version") != PIPELINE_OUTPUT_SCHEMA
        or value.get("market") != "KOREA"
        or value.get("required_completed_bar_intervals") != ["15m", "1h", "1d"]
        or value.get("authority_bindings") != {
            "universe_identity": [],
            "open_day_snapshot": [],
            "p9_01_policy": [],
            "kis_interval_semantics": [],
            "execution_measurement": [],
        }
        or value.get("ratified_policy_bindings") != []
        or value.get("authority") != AUTHORITY
    ):
        raise KrxPaperPublicPipelineError("CONTRACT_IDENTITY_OR_AUTHORITY_DRIFT")
    pins = value.get("source_pins")
    if not isinstance(pins, dict) or set(pins) != set(SOURCE_PIN_EXPECTATIONS):
        raise KrxPaperPublicPipelineError("CONTRACT_SOURCE_PINS_INVALID")
    for name, expected in SOURCE_PIN_EXPECTATIONS.items():
        row = pins.get(name)
        if not isinstance(row, dict) or (
            row.get("merge_commit"),
            row.get("contract_path"),
            row.get("contract_version"),
            row.get("contract_file_sha256"),
        ) != expected:
            raise KrxPaperPublicPipelineError(f"CONTRACT_SOURCE_PIN_DRIFT:{name}")
    public = value.get("public_output")
    if not isinstance(public, dict) or public != {
        "locked_symbol": "NONE",
        "locked_proposal": "NONE",
        "quantity": 0,
        "order_draft": None,
        "broker_route": None,
        "kis_submission": None,
        "broker_post_count": 0,
        "kis_post_count": 0,
    }:
        raise KrxPaperPublicPipelineError("CONTRACT_PUBLIC_BOUNDARY_DRIFT")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(path))


def verify_source_pins(contract: dict) -> dict:
    """Verify pinned commits and contract bytes from the local full Git history."""
    result = {}
    for name, row in contract["source_pins"].items():
        commit = row["merge_commit"]
        path = row["contract_path"]
        try:
            data = subprocess.check_output(
                ["git", "show", f"{commit}:{path}"], cwd=ROOT, stderr=subprocess.DEVNULL
            )
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
        except (OSError, subprocess.CalledProcessError) as exc:
            raise KrxPaperPublicPipelineError(f"SOURCE_PIN_UNAVAILABLE:{name}") from exc
        digest = hashlib.sha256(data).hexdigest()
        current = ROOT / path
        if (
            digest != row["contract_file_sha256"]
            or not current.is_file()
            or hashlib.sha256(current.read_bytes()).hexdigest() != digest
            or not ancestor
        ):
            raise KrxPaperPublicPipelineError(f"SOURCE_PIN_MISMATCH:{name}")
        result[name] = {
            "merge_commit": commit,
            "contract_version": row["contract_version"],
            "contract_file_sha256": digest,
        }
    return result


def _validate_universe(value: object, business_date: str, evaluated_at: dt.datetime) -> dict:
    fields = {
        "schema_version", "evidence_kind", "source_commit", "business_date",
        "symbol", "security_id", "shadow_canonical_instrument_id",
        "identity_snapshot_sha256", "registry_packet_sha256",
        "decision_eligibility", "authority_status", "available_at_utc",
        "valid_until_utc", "source_ref", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise KrxPaperPublicPipelineError("UNIVERSE_FIELDS_INVALID")
    _stage_digest(value, "UNIVERSE_PAYLOAD_SHA_INVALID")
    if (
        value["schema_version"] != "krx_public_universe_identity/1"
        or value["evidence_kind"] != "NATURAL"
        or _commit(value["source_commit"], "UNIVERSE_SOURCE_COMMIT_INVALID")
        != SOURCE_PIN_EXPECTATIONS["execution_measurement"][0]
        or value["business_date"] != business_date
    ):
        raise KrxPaperPublicPipelineError("UNIVERSE_IDENTITY_OR_DATE_INVALID")
    if not isinstance(value["symbol"], str) or SYMBOL_RE.fullmatch(value["symbol"]) is None:
        raise KrxPaperPublicPipelineError("UNIVERSE_SYMBOL_INVALID")
    if not isinstance(value["security_id"], str) or not value["security_id"].startswith("KR:XKRX:"):
        raise KrxPaperPublicPipelineError("UNIVERSE_SECURITY_ID_INVALID")
    if value["shadow_canonical_instrument_id"] != f"KRX:{value['symbol']}:COMMON":
        raise KrxPaperPublicPipelineError("UNIVERSE_SHADOW_IDENTITY_MAPPING_INVALID")
    _sha(value["identity_snapshot_sha256"], "UNIVERSE_IDENTITY_SHA_INVALID")
    _sha(value["registry_packet_sha256"], "UNIVERSE_PACKET_SHA_INVALID")
    available = _utc(value["available_at_utc"], "UNIVERSE_AVAILABLE_AT_INVALID")
    valid_until = _utc(value["valid_until_utc"], "UNIVERSE_VALID_UNTIL_INVALID")
    if not available <= evaluated_at < valid_until:
        raise KrxPaperPublicPipelineError("UNIVERSE_STALE_OR_LOOKAHEAD")
    _not_fixture(value["source_ref"], "UNIVERSE_NATURAL_SOURCE_REQUIRED")
    return copy.deepcopy(value)


def _validate_interval_semantics(value: object, evaluated_at: dt.datetime) -> dict:
    fields = {
        "schema_version", "evidence_kind", "source_commit", "status", "provider_id",
        "endpoint_ids", "raw_timestamp_field", "semantics", "ratified_by",
        "ratified_at_utc", "effective_from_utc", "effective_to_utc", "source_ref",
        "source_sha256", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise KrxPaperPublicPipelineError("INTERVAL_SEMANTICS_FIELDS_INVALID")
    _stage_digest(value, "INTERVAL_SEMANTICS_PAYLOAD_SHA_INVALID")
    if (
        value["schema_version"] != "kis_krx_interval_semantics/1"
        or value["evidence_kind"] != "NATURAL"
        or value["source_commit"] != SOURCE_PIN_EXPECTATIONS["completed_bars"][0]
        or value["status"] != "RATIFIED_EFFECTIVE"
        or value["provider_id"] != "KIS_OPEN_API"
        or value["endpoint_ids"] != ["FHKST03010200", "FHKST03010230"]
        or value["raw_timestamp_field"] != "stck_cntg_hour"
        or value["semantics"] != "INTERVAL_START_RATIFIED"
    ):
        raise KrxPaperPublicPipelineError("KIS_INTERVAL_START_SEMANTICS_MISSING")
    ratified = _utc(value["ratified_at_utc"], "INTERVAL_RATIFIED_AT_INVALID")
    start = _utc(value["effective_from_utc"], "INTERVAL_EFFECTIVE_FROM_INVALID")
    end = _utc(value["effective_to_utc"], "INTERVAL_EFFECTIVE_TO_INVALID")
    if ratified > start or not start <= evaluated_at < end:
        raise KrxPaperPublicPipelineError("KIS_INTERVAL_SEMANTICS_NOT_EFFECTIVE")
    _not_fixture(value["ratified_by"], "INTERVAL_RATIFIER_INVALID")
    _not_fixture(value["source_ref"], "INTERVAL_NATURAL_SOURCE_REQUIRED")
    _sha(value["source_sha256"], "INTERVAL_SOURCE_SHA_INVALID")
    return copy.deepcopy(value)


def _validate_execution(value: object, universe: dict, business_date: str, evaluated_at: dt.datetime) -> dict:
    fields = {
        "schema_version", "evidence_kind", "source_commit", "business_date", "status",
        "identity_snapshot_sha256", "captured_at_utc", "http_method", "coverage",
        "public_packet_sha256", "broker_post_count", "authority", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise KrxPaperPublicPipelineError("EXECUTION_FIELDS_INVALID")
    _stage_digest(value, "EXECUTION_PAYLOAD_SHA_INVALID")
    coverage = value.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {
        "turnover", "depth", "spread", "slippage"
    } or any(type(count) is not int or count < 1 for count in coverage.values()):
        raise KrxPaperPublicPipelineError("EXECUTION_COVERAGE_INCOMPLETE")
    authority = value.get("authority")
    if not isinstance(authority, dict) or not authority or any(authority.values()):
        raise KrxPaperPublicPipelineError("EXECUTION_AUTHORITY_INVALID")
    captured = _utc(value["captured_at_utc"], "EXECUTION_CAPTURED_AT_INVALID")
    if (
        value["schema_version"] != "krx_execution_measurement_readiness/1"
        or value["evidence_kind"] != "NATURAL"
        or value["source_commit"] != SOURCE_PIN_EXPECTATIONS["execution_measurement"][0]
        or value["business_date"] != business_date
        or value["status"] != "CAPTURE_COMPLETED_READ_ONLY"
        or value["identity_snapshot_sha256"] != universe["identity_snapshot_sha256"]
        or value["http_method"] != "GET"
        or value["broker_post_count"] != 0
        or captured > evaluated_at
    ):
        raise KrxPaperPublicPipelineError("EXECUTION_LINEAGE_OR_BOUNDARY_INVALID")
    _sha(value["public_packet_sha256"], "EXECUTION_PUBLIC_PACKET_SHA_INVALID")
    return copy.deepcopy(value)


def _validate_policy(value: object, evaluated_at: dt.datetime) -> dict:
    fields = {
        "schema_version", "status", "policy_id", "policy_source_sha256",
        "ratified_by", "ratified_at_utc", "effective_from_utc", "effective_to_utc",
        "strategy_policy_ratified", "entry_policy_ratified", "hold_exit_policy_ratified",
        "position_size_policy_ratified", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise KrxPaperPublicPipelineError("POLICY_AUTHORITY_FIELDS_INVALID")
    _stage_digest(value, "POLICY_AUTHORITY_PAYLOAD_SHA_INVALID")
    _sha(value["policy_source_sha256"], "POLICY_SOURCE_SHA_INVALID")
    ratified = _utc(value["ratified_at_utc"], "POLICY_RATIFIED_AT_INVALID")
    start = _utc(value["effective_from_utc"], "POLICY_EFFECTIVE_FROM_INVALID")
    end = _utc(value["effective_to_utc"], "POLICY_EFFECTIVE_TO_INVALID")
    flags = (
        value["strategy_policy_ratified"], value["entry_policy_ratified"],
        value["hold_exit_policy_ratified"], value["position_size_policy_ratified"],
    )
    if (
        value["schema_version"] != "krx_policy_authority/1"
        or value["status"] != "RATIFIED_EFFECTIVE"
        or ratified > start
        or not start <= evaluated_at < end
        or any(type(flag) is not bool or flag is not True for flag in flags)
    ):
        raise KrxPaperPublicPipelineError("RATIFIED_EFFECTIVE_POLICY_MISSING")
    _not_fixture(value["ratified_by"], "POLICY_RATIFIER_INVALID")
    return copy.deepcopy(value)


def _fresh_market_data(
    wrapper: object, universe: dict, business_date: str, evaluated_at: dt.datetime
) -> tuple[dict, dict[str, dict]]:
    if not isinstance(wrapper, dict) or set(wrapper) != {
        "evidence_kind", "input", "expected_result_sha256"
    }:
        raise KrxPaperPublicPipelineError("MARKET_DATA_WRAPPER_INVALID")
    if wrapper["evidence_kind"] != "NATURAL":
        raise KrxPaperPublicPipelineError("NATURAL_MARKET_DATA_REQUIRED")
    source = wrapper["input"]
    if not isinstance(source, dict):
        raise KrxPaperPublicPipelineError("MARKET_DATA_INPUT_INVALID")
    if source.get("decision_at") != evaluated_at.astimezone(KST).isoformat(timespec="seconds"):
        raise KrxPaperPublicPipelineError("MARKET_DATA_DECISION_TIME_MISMATCH")
    calendar = source.get("calendar")
    if (
        not isinstance(calendar, dict)
        or calendar.get("session_date") != business_date
        or calendar.get("status") != "OPEN_REGULAR"
        or calendar.get("provider_id") != "KIS_OPEN_API_DOMESTIC_HOLIDAY_CTCA0903R"
    ):
        raise KrxPaperPublicPipelineError("NATURAL_OPEN_DAY_SNAPSHOT_MISSING")
    _not_fixture(calendar.get("source_ref"), "CALENDAR_NATURAL_SOURCE_REQUIRED")
    policy = source.get("freshness_policy")
    if (
        not isinstance(policy, dict)
        or policy.get("approval_status") != "RATIFIED"
        or policy.get("max_provider_age_seconds_by_market", {}).get("KOREA") is None
        or policy.get("max_transport_delay_seconds_by_market", {}).get("KOREA") is None
    ):
        raise KrxPaperPublicPipelineError("P9_01_RATIFIED_KOREA_POLICY_MISSING")
    _not_fixture(policy.get("ratified_by"), "P9_01_RATIFIER_INVALID")
    result = MARKET_DATA.evaluate_packet(source)
    expected = _sha(wrapper["expected_result_sha256"], "MARKET_DATA_RESULT_SHA_INVALID")
    if result["packet_sha256"] != expected:
        raise KrxPaperPublicPipelineError("MARKET_DATA_RESULT_SHA_MISMATCH")
    series = result.get("series")
    by_interval = {row.get("timeframe"): row for row in series or [] if isinstance(row, dict)}
    if set(by_interval) != {"15m", "1h", "1d"} or len(series or []) != 3:
        raise KrxPaperPublicPipelineError("COMPLETED_BAR_INTERVAL_SET_INVALID")
    upstream_reasons = sorted({
        reason
        for row in by_interval.values()
        for reason in row.get("reasons", [])
        if isinstance(reason, str)
    })
    if upstream_reasons:
        raise KrxPaperPublicPipelineError(
            "COMPLETED_BARS_OR_FRESHNESS_NOT_PASS:" + "|".join(upstream_reasons)
        )
    if any(
        row.get("asset_id") != universe["security_id"]
        or row.get("status") != "PASS"
        or row.get("freshness_status") != "FRESH"
        or row.get("exact_duplicate_count") != 0
        or not isinstance(row.get("p9_policy_lineage"), dict)
        for row in by_interval.values()
    ):
        raise KrxPaperPublicPipelineError("COMPLETED_BARS_OR_FRESHNESS_NOT_PASS")
    latest = {}
    for interval, row in by_interval.items():
        bars = row.get("bars")
        if not isinstance(bars, list) or not bars:
            raise KrxPaperPublicPipelineError(f"COMPLETED_BAR_MISSING:{interval}")
        bar = bars[-1]
        closed = dt.datetime.fromisoformat(bar["close_at"])
        if closed.astimezone(KST).date().isoformat() != business_date:
            raise KrxPaperPublicPipelineError(f"CROSS_DATE_BAR:{interval}")
        latest[interval] = bar
    return result, latest


def _gate_status(assessment: dict, gate_id: str) -> str:
    rows = assessment.get("gate_results")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("gate_id") == gate_id:
            return str(row.get("status"))
    return "UNKNOWN"


def _block(blockers: list[str], code: str) -> None:
    if code not in blockers:
        blockers.append(code)


def _safe_stage(blockers: list[str], label: str, fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # every upstream validation failure is fail-closed
        raw = str(exc).strip().replace(" ", "_")[:180]
        detail = raw if re.fullmatch(r"[A-Z0-9_:.|+\-]+", raw or "") else exc.__class__.__name__
        _block(blockers, f"{label}:{detail}")
        return None


def _cross_validate_shadow(
    shadow_input: dict, universe: dict, market_result: dict,
    latest_bars: dict[str, dict], execution: dict, business_date: str
) -> dict:
    packet = SHADOW.build_packet(shadow_input)
    SHADOW.validate_packet(packet)
    if shadow_input.get("business_date") != business_date:
        raise KrxPaperPublicPipelineError("SHADOW_BUSINESS_DATE_MISMATCH")
    candidates = shadow_input.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise KrxPaperPublicPipelineError("SHADOW_ONE_CANDIDATE_REQUIRED")
    candidate = candidates[0]
    if (
        candidate.get("symbol") != universe["symbol"]
        or candidate.get("identity", {}).get("source_sha256") != universe["payload_sha256"]
        or candidate.get("identity", {}).get("canonical_instrument_id")
        != universe["shadow_canonical_instrument_id"]
        or candidate.get("eligibility", {}).get("status") != universe["decision_eligibility"]
        or candidate.get("liquidity", {}).get("source_sha256") != execution["payload_sha256"]
        or candidate.get("market_context", {}).get("source_sha256") != market_result["packet_sha256"]
    ):
        raise KrxPaperPublicPipelineError("SHADOW_UPSTREAM_LINEAGE_MISMATCH")
    for interval, source_bar in latest_bars.items():
        target = candidate.get("bars", {}).get(interval, {})
        source = source_bar.get("source", {})
        if (
            target.get("completed") is not True
            or target.get("source_sha256") != source.get("snapshot_sha256")
            or _utc(target.get("closed_at"), "SHADOW_BAR_CLOSE_INVALID")
            != dt.datetime.fromisoformat(source_bar["close_at"]).astimezone(dt.timezone.utc)
        ):
            raise KrxPaperPublicPipelineError(f"SHADOW_BAR_LINEAGE_MISMATCH:{interval}")
    return packet


def _cross_validate_proposal(
    proposal_input: dict, shadow_packet: dict, universe: dict,
    latest_bars: dict[str, dict], gate_assessment: dict
) -> dict:
    decisions = shadow_packet.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != 1:
        raise KrxPaperPublicPipelineError("SHADOW_ONE_DECISION_REQUIRED")
    decision = decisions[0]
    shadow = proposal_input.get("shadow", {})
    proposal_policy = proposal_input.get("policy", {})
    proposal_entry = proposal_policy.get("entry_zone", {})
    shadow_entry = decision.get("entry", {})
    shadow_risk = decision.get("risk_plan", {})
    if (
        proposal_input.get("universe", {}).get("symbol") != universe["symbol"]
        or proposal_input.get("universe", {}).get("security_id") != universe["security_id"]
        or proposal_input.get("universe", {}).get("packet_sha256") != universe["registry_packet_sha256"]
        or proposal_input.get("universe", {}).get("source_sha256") != universe["payload_sha256"]
        or shadow.get("symbol") != universe["symbol"]
        or shadow.get("decision_key") != decision.get("decision_key")
        or shadow.get("action") != decision.get("action")
        or shadow.get("diagnostic_action") != decision.get("diagnostic_action")
        or shadow.get("packet_sha256") != shadow_packet.get("packet_sha256")
        or proposal_entry.get("minimum_price_units") != shadow_entry.get("reference_price_krw")
        or proposal_entry.get("maximum_price_units") != shadow_entry.get("max_entry_price_krw")
        or proposal_policy.get("stop_price_units") != shadow_risk.get("stop_price_krw")
        or proposal_policy.get("first_take_profit_price_units")
        != shadow_risk.get("take_profit_1_price_krw")
        or proposal_policy.get("final_take_profit_price_units")
        != shadow_risk.get("final_take_profit_price_krw")
        or proposal_policy.get("expires_at_utc") != shadow_risk.get("expires_at")
    ):
        raise KrxPaperPublicPipelineError("PROPOSAL_UPSTREAM_LINEAGE_MISMATCH")
    if proposal_input.get("gate_assessment", {}).get("assessment_sha256") != gate_assessment.get("assessment_sha256"):
        raise KrxPaperPublicPipelineError("PROPOSAL_GATE_LINEAGE_MISMATCH")
    for interval, source_bar in latest_bars.items():
        target = proposal_input.get("bars", {}).get(interval, {})
        if (
            target.get("completed") is not True
            or target.get("source_sha256") != source_bar.get("source", {}).get("snapshot_sha256")
        ):
            raise KrxPaperPublicPipelineError(f"PROPOSAL_BAR_LINEAGE_MISMATCH:{interval}")
    packet = PROPOSAL.build_packet(proposal_input)
    PROPOSAL.validate_packet(packet, proposal_input)
    return packet


def _safe_metadata(value: object) -> tuple[str | None, str | None, dt.datetime | None]:
    if not isinstance(value, dict):
        return None, None, None
    try:
        business_date = _date(value.get("business_date"), "BUSINESS_DATE_INVALID")
    except KrxPaperPublicPipelineError:
        business_date = None
    try:
        evaluated_text = value.get("evaluated_at_utc")
        evaluated = _utc(evaluated_text, "EVALUATED_AT_INVALID")
    except KrxPaperPublicPipelineError:
        evaluated_text = None
        evaluated = None
    return business_date, evaluated_text, evaluated


def build_packet(input_packet: object, contract: dict | None = None) -> dict:
    locked_contract = validate_contract(contract if contract is not None else load_contract())
    blockers: list[str] = []
    source_pins = _safe_stage(blockers, "SOURCE_PINS", verify_source_pins, locked_contract)
    business_date, evaluated_text, evaluated_at = _safe_metadata(input_packet)
    if business_date is None or evaluated_at is None:
        _block(blockers, "INPUT_METADATA_INVALID")
    row = input_packet if isinstance(input_packet, dict) else {}
    expected_input_fields = {
        "schema_version", "run_id", "business_date", "evaluated_at_utc",
        "universe_identity", "market_data", "interval_semantics",
        "execution_measurement", "policy_authority", "gate_assessment",
        "gate_evidence_input", "shadow_input", "proposal_input",
        "prior_receipts", "authority", "packet_sha256",
    }
    if set(row) != expected_input_fields:
        _block(blockers, "INPUT_FIELDS_INVALID")
    if row.get("schema_version") != PIPELINE_INPUT_SCHEMA:
        _block(blockers, "INPUT_SCHEMA_INVALID")
    if row.get("authority") != AUTHORITY:
        _block(blockers, "INPUT_AUTHORITY_INVALID")
    claimed_input_sha = row.get("packet_sha256")
    unsigned = copy.deepcopy(row)
    unsigned.pop("packet_sha256", None)
    if not isinstance(claimed_input_sha, str) or SHA_RE.fullmatch(claimed_input_sha) is None:
        _block(blockers, "INPUT_PACKET_SHA256_INVALID")
        claimed_input_sha = payload_sha256(unsigned)
    elif payload_sha256(unsigned) != claimed_input_sha:
        _block(blockers, "INPUT_PACKET_SHA256_MISMATCH")

    universe = market_result = latest_bars = interval = execution = policy = None
    gate_assessment = shadow_packet = proposal_packet = None
    if business_date is not None and evaluated_at is not None:
        universe = _safe_stage(
            blockers, "UNIVERSE", _validate_universe,
            row.get("universe_identity"), business_date, evaluated_at,
        )
        if universe is not None:
            universe_binding = {
                "identity_snapshot_sha256": universe["identity_snapshot_sha256"],
                "registry_packet_sha256": universe["registry_packet_sha256"],
                "authority_packet_sha256": universe["payload_sha256"],
            }
            if universe_binding not in locked_contract["authority_bindings"]["universe_identity"]:
                _block(blockers, "UNIVERSE_AUTHORITY_BINDING_ABSENT")
            market_value = _safe_stage(
                blockers, "MARKET_DATA", _fresh_market_data,
                row.get("market_data"), universe, business_date, evaluated_at,
            )
            if market_value is not None:
                market_result, latest_bars = market_value
                calendar = row["market_data"]["input"]["calendar"]
                open_day_binding = {
                    "business_date": business_date,
                    "source_sha256": calendar["source_sha256"],
                }
                p9_policy = row["market_data"]["input"]["freshness_policy"]
                p9_binding = {
                    "policy_id": p9_policy["policy_id"],
                    "policy_sha256": p9_policy["packet_sha256"],
                }
                if open_day_binding not in locked_contract["authority_bindings"]["open_day_snapshot"]:
                    _block(blockers, "OPEN_DAY_SNAPSHOT_AUTHORITY_BINDING_ABSENT")
                if p9_binding not in locked_contract["authority_bindings"]["p9_01_policy"]:
                    _block(blockers, "P9_01_POLICY_AUTHORITY_BINDING_ABSENT")
            interval = _safe_stage(
                blockers, "INTERVAL_SEMANTICS", _validate_interval_semantics,
                row.get("interval_semantics"), evaluated_at,
            )
            if interval is not None and {
                "source_sha256": interval["source_sha256"],
                "authority_packet_sha256": interval["payload_sha256"],
            } not in locked_contract["authority_bindings"]["kis_interval_semantics"]:
                _block(blockers, "KIS_INTERVAL_SEMANTICS_AUTHORITY_BINDING_ABSENT")
            execution = _safe_stage(
                blockers, "EXECUTION_MEASUREMENT", _validate_execution,
                row.get("execution_measurement"), universe, business_date, evaluated_at,
            )
            if execution is not None and {
                "public_packet_sha256": execution["public_packet_sha256"],
                "readiness_packet_sha256": execution["payload_sha256"],
            } not in locked_contract["authority_bindings"]["execution_measurement"]:
                _block(blockers, "EXECUTION_MEASUREMENT_BINDING_ABSENT")
        policy = _safe_stage(
            blockers, "POLICY", _validate_policy,
            row.get("policy_authority"), evaluated_at,
        )
        if policy is not None and {
            "policy_id": policy["policy_id"],
            "source_sha256": policy["policy_source_sha256"],
            "authority_packet_sha256": policy["payload_sha256"],
        } not in locked_contract["ratified_policy_bindings"]:
            _block(blockers, "RATIFIED_POLICY_BINDING_ABSENT")
        gate_assessment = _safe_stage(
            blockers, "GATE", GATE.validate_assessment,
            row.get("gate_assessment"), row.get("gate_evidence_input"),
        )
        if gate_assessment is not None:
            if _utc(gate_assessment.get("as_of_utc"), "GATE_AS_OF_INVALID") > evaluated_at:
                _block(blockers, "GATE_LOOKAHEAD_OR_CROSS_DATE")
            if _gate_status(gate_assessment, "COMMON_SAFETY") != "PASS":
                _block(blockers, "COMMON_SAFETY_NOT_PASS")
            if _gate_status(gate_assessment, "KRX_SHADOW") != "PASS":
                _block(blockers, "EFFECTIVE_KRX_SHADOW_NOT_PASS")

    if all(item is not None for item in (universe, market_result, latest_bars, execution)):
        shadow_packet = _safe_stage(
            blockers, "SHADOW", _cross_validate_shadow,
            row.get("shadow_input"), universe, market_result, latest_bars,
            execution, business_date,
        )
    if all(item is not None for item in (universe, latest_bars, shadow_packet, gate_assessment)):
        proposal_packet = _safe_stage(
            blockers, "PROPOSAL", _cross_validate_proposal,
            row.get("proposal_input"), shadow_packet, universe, latest_bars,
            gate_assessment,
        )

    if universe is not None:
        if universe["decision_eligibility"] != "ELIGIBLE" or universe["authority_status"] != "RATIFIED_EFFECTIVE":
            _block(blockers, "AUTHORITY_BEARING_ELIGIBILITY_MISSING")
    if shadow_packet is not None:
        decision = shadow_packet["decisions"][0]
        if decision["action"] == "NO_TRADE":
            _block(blockers, "SHADOW_ACTION_NOT_AUTHORIZED")
    if proposal_packet is not None and proposal_packet["machine_proposal"]["status"] != "NON_NONE":
        _block(blockers, "P8_13_PROPOSAL_NONE")

    lineage = {
        "source_pins": source_pins or {},
        "universe_identity_sha256": universe.get("payload_sha256") if universe else None,
        "market_data_result_sha256": market_result.get("packet_sha256") if market_result else None,
        "p9_policy_sha256": (
            market_result["series"][0].get("p9_policy_lineage", {}).get("policy_sha256")
            if market_result and market_result.get("series") else None
        ),
        "interval_semantics_sha256": interval.get("payload_sha256") if interval else None,
        "execution_measurement_sha256": execution.get("payload_sha256") if execution else None,
        "policy_authority_sha256": policy.get("payload_sha256") if policy else None,
        "gate_assessment_sha256": gate_assessment.get("assessment_sha256") if gate_assessment else None,
        "shadow_packet_sha256": shadow_packet.get("packet_sha256") if shadow_packet else None,
        "p8_13_packet_sha256": proposal_packet.get("packet_sha256") if proposal_packet else None,
    }
    identity_sha = payload_sha256({
        "business_date": business_date,
        "evaluated_at_utc": evaluated_text,
        "lineage": lineage,
    })
    proposal_sha = (
        proposal_packet.get("machine_proposal", {}).get("proposal_sha256")
        if proposal_packet else None
    )
    priors = row.get("prior_receipts", [])
    if not isinstance(priors, list):
        _block(blockers, "PRIOR_RECEIPTS_INVALID")
        priors = []
    valid_prior_fields = {"identity_sha256", "proposal_sha256"}
    for prior in priors:
        proposal_digest = prior.get("proposal_sha256") if isinstance(prior, dict) else None
        if (
            not isinstance(prior, dict)
            or set(prior) != valid_prior_fields
            or not isinstance(prior.get("identity_sha256"), str)
            or SHA_RE.fullmatch(prior["identity_sha256"]) is None
            or not (
                proposal_digest is None
                or isinstance(proposal_digest, str) and SHA_RE.fullmatch(proposal_digest) is not None
            )
        ):
            _block(blockers, "PRIOR_RECEIPT_FIELDS_OR_HASH_INVALID")
    matches = [item for item in priors if isinstance(item, dict) and item.get("identity_sha256") == identity_sha]
    replay_result = "NEW_IDENTITY"
    if len(matches) > 1:
        _block(blockers, "DUPLICATE_PROPOSAL_IDENTITY")
        replay_result = "LOCKED_DUPLICATE"
    elif len(matches) == 1:
        prior = matches[0]
        if prior.get("proposal_sha256") != proposal_sha:
            _block(blockers, "CONFLICTING_PROPOSAL_FOR_IDENTITY")
            replay_result = "LOCKED_CONFLICT"
        else:
            replay_result = "NO_CHANGE"
    if len({item.get("proposal_sha256") for item in matches if isinstance(item, dict)}) > 1:
        _block(blockers, "CONFLICTING_DUPLICATE_PROPOSAL")

    blockers = sorted(set(blockers))
    ready = not blockers and proposal_packet is not None
    output_status = "NO_CHANGE" if replay_result == "NO_CHANGE" else (
        "READY_PUBLIC_PROPOSAL" if ready else "LOCKED_FAIL_CLOSED"
    )
    symbol = universe["symbol"] if ready else "NONE"
    action = proposal_packet["machine_proposal"]["action"] if ready else "NONE"
    proposal_status = "PUBLIC_REVIEW_ONLY" if ready else "NONE"
    result = {
        "schema_version": PIPELINE_OUTPUT_SCHEMA,
        "contract_version": PIPELINE_CONTRACT_VERSION,
        "market": "KOREA",
        "business_date": business_date,
        "evaluated_at_utc": evaluated_text,
        "status": output_status,
        "readiness": {
            "status": "READY_PUBLIC_PROPOSAL" if ready else "LOCKED_FAIL_CLOSED",
            "symbol": symbol,
            "proposal": proposal_status,
            "blockers": blockers,
        },
        "proposal": {
            "status": proposal_status,
            "symbol": symbol,
            "action": action,
            "quantity": 0,
            "order_draft": None,
            "broker_route": None,
            "kis_submission": None,
            "broker_post_count": 0,
            "kis_post_count": 0,
        },
        "replay": {
            "result": replay_result,
            "identity_sha256": identity_sha,
            "proposal_sha256": proposal_sha,
        },
        "lineage": lineage,
        "source": {"input_packet_sha256": claimed_input_sha},
        "authority": copy.deepcopy(AUTHORITY),
    }
    result["packet_sha256"] = payload_sha256(result)
    return result


def validate_packet(packet: object, input_packet: object, contract: dict | None = None) -> dict:
    expected = build_packet(input_packet, contract)
    if not isinstance(packet, dict) or packet != expected:
        raise KrxPaperPublicPipelineError("OUTPUT_DERIVATION_MISMATCH")
    if packet["authority"] != AUTHORITY:
        raise KrxPaperPublicPipelineError("OUTPUT_AUTHORITY_ESCALATION")
    proposal = packet["proposal"]
    if proposal["quantity"] != 0 or any(
        proposal[key] is not None for key in ("order_draft", "broker_route", "kis_submission")
    ) or proposal["broker_post_count"] != 0 or proposal["kis_post_count"] != 0:
        raise KrxPaperPublicPipelineError("OUTPUT_ORDER_BOUNDARY_ESCALATION")
    if packet["readiness"]["status"] != "READY_PUBLIC_PROPOSAL" and (
        packet["readiness"]["symbol"] != "NONE" or proposal["status"] != "NONE"
    ):
        raise KrxPaperPublicPipelineError("LOCKED_OUTPUT_NOT_SANITIZED")
    return copy.deepcopy(packet)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    packet = build_packet(_read_json(args.input))
    encoded = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
