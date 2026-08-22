#!/usr/bin/env python3
"""P8-09 Expectations Gap builder — free/official-data proxy, no paid feed required.

This module answers one question: *does the caller-supplied evidence, about
what a company is signalling versus what the market currently expects,
suggest the expectation is running ahead of, behind, or in line with what
Atlas can independently observe?*

A paid consensus-estimate feed is explicitly **not** a required dependency.
The caller may supply any subset (including none) of nine evidence
categories — eight free/official-data proxy categories plus one optional
`public_estimates` category that stands in for an actual consensus feed when
the caller happens to have one. The single hardest rule in this module is
structural: the **absence** of `public_estimates` must never block or fail
packet construction. It only ever downgrades
`market_expectation_basis.basis_type` from `CONSENSUS` to `PROXY` or
`UNKNOWN` and the module keeps building a valid, maximally honest packet.

This module does not fetch evidence itself. It assembles whatever the caller
already has into a closed-vocabulary, deterministic, tamper-evident packet.
It never grants Rule PASS/FAIL, Stage, or trading authority.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "expectations_gap_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ExpectationsGapError(ValueError):
    """Fail-closed P8-09 Expectations Gap contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpectationsGapError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "expectations_gap/1",
        "input_schema_version": "expectations_gap_input/1",
        "output_schema_version": "expectations_gap_packet/1",
        "input_categories": [
            "backlog_or_new_orders", "capex_or_expansion", "earnings_reaction",
            "guidance_changes", "official_ir_targets", "pricing_or_lead_time",
            "public_estimates", "relative_strength_volume", "revenue_margin_trend",
        ],
        "proxy_categories": [
            "backlog_or_new_orders", "capex_or_expansion", "earnings_reaction",
            "guidance_changes", "official_ir_targets", "pricing_or_lead_time",
            "relative_strength_volume", "revenue_margin_trend",
        ],
        "consensus_category": "public_estimates",
        "allowed_directions": ["POSITIVE", "NEGATIVE", "NEUTRAL", "UNKNOWN"],
        "allowed_status": ["POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN"],
        "allowed_magnitude": ["SMALL", "MEDIUM", "LARGE", "UNKNOWN"],
        "allowed_confidence": ["LOW", "MEDIUM", "HIGH"],
        "allowed_basis_type": ["CONSENSUS", "PROXY", "UNKNOWN"],
        "magnitude_thresholds": {
            "large_min_scored_count": 4, "large_min_ratio": "0.75",
            "medium_min_scored_count": 2, "medium_min_ratio": "0.5",
        },
        "confidence_thresholds": {
            "high_min_scored_count": 4, "high_min_ratio": "0.75",
            "medium_min_scored_count": 2,
        },
        "authority": {
            "expectations_gap_assembly_only": True,
            "rule_authority_substitution_authorized": False,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ExpectationsGapError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ExpectationsGapError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ExpectationsGapError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ExpectationsGapError(code) from exc
    return parsed


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ExpectationsGapError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ExpectationsGapError(code) from exc
    if parsed.isoformat() != value:
        raise ExpectationsGapError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ExpectationsGapError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ExpectationsGapError(code)
    return value


def _validate_category_row(value, category: str, contract: dict) -> dict:
    """A supplied category is a caller-classified {direction, evidence_note} pair.

    This module does not read raw filings, transcripts, or price series
    itself — the caller (an evidence-assembly layer upstream of this one)
    classifies each category's directional read and supplies a short,
    auditable note. This module only aggregates already-classified signals
    under a closed vocabulary; it never invents a direction from raw text.
    """
    fields = {"direction", "evidence_note"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ExpectationsGapError(f"CATEGORY_FIELDS_MISMATCH:{category}")
    direction = value.get("direction")
    if direction not in contract["allowed_directions"]:
        raise ExpectationsGapError(f"CATEGORY_DIRECTION_INVALID:{category}")
    note = _text(value.get("evidence_note"), f"CATEGORY_EVIDENCE_NOTE_INVALID:{category}")
    return {"direction": direction, "evidence_note": note}


def _validate_earnings_reaction(value, decision_date: dt.date, contract: dict) -> dict:
    fields = {"event_date", "direction", "evidence_note"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ExpectationsGapError("EARNINGS_REACTION_FIELDS_MISMATCH")
    event_date = _date(value.get("event_date"), "EARNINGS_REACTION_EVENT_DATE_INVALID")
    if event_date > decision_date:
        raise ExpectationsGapError("EARNINGS_REACTION_EVENT_DATE_IN_FUTURE")
    direction = value.get("direction")
    if direction not in contract["allowed_directions"]:
        raise ExpectationsGapError("EARNINGS_REACTION_DIRECTION_INVALID")
    note = _text(value.get("evidence_note"), "EARNINGS_REACTION_EVIDENCE_NOTE_INVALID")
    return {"event_date": event_date.isoformat(), "direction": direction, "evidence_note": note}


def _derive(supplied: dict, contract: dict) -> dict:
    """Pure derivation from validated per-category rows. No I/O, no network."""
    categories = contract["input_categories"]
    proxy_categories = set(contract["proxy_categories"])
    consensus_category = contract["consensus_category"]

    missing_inputs = sorted(cat for cat in categories if cat not in supplied)
    supplied_categories = sorted(supplied)

    scored = [
        (cat, row["direction"])
        for cat, row in sorted(supplied.items())
        if row["direction"] in {"POSITIVE", "NEGATIVE", "NEUTRAL"}
    ]
    net_score = sum({"POSITIVE": 1, "NEGATIVE": -1, "NEUTRAL": 0}[d] for _, d in scored)
    scored_count = len(scored)

    if scored_count == 0:
        status = "UNKNOWN"
    elif net_score > 0:
        status = "POSITIVE"
    elif net_score < 0:
        status = "NEGATIVE"
    else:
        status = "NEUTRAL"

    ratio = (Decimal(abs(net_score)) / Decimal(scored_count)) if scored_count else Decimal(0)
    mag_t = contract["magnitude_thresholds"]
    if status == "UNKNOWN":
        magnitude = "UNKNOWN"
    elif scored_count >= mag_t["large_min_scored_count"] and ratio >= Decimal(mag_t["large_min_ratio"]):
        magnitude = "LARGE"
    elif scored_count >= mag_t["medium_min_scored_count"] and ratio >= Decimal(mag_t["medium_min_ratio"]):
        magnitude = "MEDIUM"
    else:
        magnitude = "SMALL"

    conf_t = contract["confidence_thresholds"]
    has_basis = bool(set(supplied) & (proxy_categories | {consensus_category}))
    if status == "UNKNOWN":
        confidence = "LOW"
    elif (
        scored_count >= conf_t["high_min_scored_count"]
        and ratio >= Decimal(conf_t["high_min_ratio"])
        and has_basis
    ):
        confidence = "HIGH"
    elif scored_count >= conf_t["medium_min_scored_count"] and has_basis:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    consensus_supplied = consensus_category in supplied
    proxy_supplied = bool(set(supplied) & proxy_categories)
    if consensus_supplied:
        basis_type = "CONSENSUS"
        basis_description = (
            "Caller supplied an actual consensus/public-estimates observation "
            "(public_estimates); this is not a paid feed requirement, but when "
            "present it is used as the market-expectation basis."
        )
    elif proxy_supplied:
        basis_type = "PROXY"
        basis_description = (
            "No public_estimates observation supplied. Market expectation is "
            "proxied from free/official-data signals: "
            + ", ".join(cat for cat in sorted(supplied) if cat in proxy_categories) + "."
        )
    else:
        basis_type = "UNKNOWN"
        basis_description = (
            "No public_estimates and no free/official-data proxy inputs were "
            "supplied. Market expectation basis is unknown; this does not "
            "block packet construction."
        )

    if supplied_categories:
        atlas_forward_basis = (
            "Atlas forward view derived from: " + ", ".join(supplied_categories) + "."
        )
    else:
        atlas_forward_basis = (
            "Atlas forward view is UNKNOWN: no input categories were supplied."
        )

    if scored:
        gap_reasons = [f"{cat}:{direction}" for cat, direction in scored]
        unscored = sorted(
            cat for cat, row in supplied.items()
            if row["direction"] not in {"POSITIVE", "NEGATIVE", "NEUTRAL"}
        )
        gap_reasons += [f"{cat}:UNKNOWN" for cat in unscored]
    elif supplied_categories:
        gap_reasons = [f"{cat}:UNKNOWN" for cat in supplied_categories]
    else:
        gap_reasons = ["NO_INPUT_CATEGORIES_SUPPLIED"]

    return {
        "status": status,
        "magnitude": magnitude,
        "confidence": confidence,
        "market_expectation_basis": {"basis_type": basis_type, "description": basis_description},
        "atlas_forward_basis": atlas_forward_basis,
        "gap_reasons": gap_reasons,
        "missing_inputs": missing_inputs,
    }


def build_packet(input_value: dict, contract: dict | None = None) -> dict:
    """Build an Expectations Gap packet.

    `input_value` must contain `subject`, `decision_date`, `generated_at`,
    plus zero or more of the nine input categories named in the contract.
    Every category is optional and individually independent. Zero supplied
    categories is a valid input — it yields a maximally honest UNKNOWN packet,
    never an exception.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(input_value, dict):
        raise ExpectationsGapError("INPUT_NOT_OBJECT")
    categories = set(contract["input_categories"])
    required_fields = {"subject", "decision_date", "generated_at"}
    unknown = set(input_value) - required_fields - categories
    if unknown:
        raise ExpectationsGapError(f"INPUT_FIELDS_UNKNOWN:{sorted(unknown)}")
    if not required_fields.issubset(input_value):
        raise ExpectationsGapError("INPUT_FIELDS_MISSING")

    subject = _token(input_value.get("subject"), "SUBJECT_INVALID")
    decision_date = _date(input_value.get("decision_date"), "DECISION_DATE_INVALID")
    generated_at = _utc(input_value.get("generated_at"), "GENERATED_AT_INVALID")

    supplied = {}
    for category in contract["input_categories"]:
        if category not in input_value:
            continue
        if category == "earnings_reaction":
            supplied[category] = _validate_earnings_reaction(
                input_value[category], decision_date, contract
            )
        else:
            supplied[category] = _validate_category_row(input_value[category], category, contract)

    derived = _derive(supplied, contract)

    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": input_value["generated_at"],
        "subject": subject,
        "decision_date": input_value["decision_date"],
        "expectations_gap": derived,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Self-contained structural + cross-field validation and tamper check.

    Recomputes packet_sha256 from the unsigned payload and rejects any
    mismatch (tamper detection). Also enforces every closed-enum and
    cross-field hard rule declared in the module docstring.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "generated_at", "subject",
        "decision_date", "expectations_gap", "authority", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ExpectationsGapError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("authority") != contract["authority"]
    ):
        raise ExpectationsGapError("OUTPUT_IDENTITY_INVALID")
    _utc(packet.get("generated_at"), "OUTPUT_GENERATED_AT_INVALID")
    _token(packet.get("subject"), "OUTPUT_SUBJECT_INVALID")
    _date(packet.get("decision_date"), "OUTPUT_DECISION_DATE_INVALID")

    gap = packet.get("expectations_gap")
    gap_fields = {
        "status", "magnitude", "confidence", "market_expectation_basis",
        "atlas_forward_basis", "gap_reasons", "missing_inputs",
    }
    if not isinstance(gap, dict) or set(gap) != gap_fields:
        raise ExpectationsGapError("OUTPUT_EXPECTATIONS_GAP_FIELDS_MISMATCH")
    status = gap.get("status")
    magnitude = gap.get("magnitude")
    confidence = gap.get("confidence")
    if status not in contract["allowed_status"]:
        raise ExpectationsGapError("OUTPUT_STATUS_INVALID")
    if magnitude not in contract["allowed_magnitude"]:
        raise ExpectationsGapError("OUTPUT_MAGNITUDE_INVALID")
    if confidence not in contract["allowed_confidence"]:
        raise ExpectationsGapError("OUTPUT_CONFIDENCE_INVALID")
    if status == "UNKNOWN" and confidence != "LOW":
        raise ExpectationsGapError("OUTPUT_UNKNOWN_STATUS_REQUIRES_LOW_CONFIDENCE")
    if status == "UNKNOWN" and magnitude != "UNKNOWN":
        raise ExpectationsGapError("OUTPUT_UNKNOWN_STATUS_REQUIRES_UNKNOWN_MAGNITUDE")

    basis = gap.get("market_expectation_basis")
    if not isinstance(basis, dict) or set(basis) != {"basis_type", "description"}:
        raise ExpectationsGapError("OUTPUT_BASIS_FIELDS_MISMATCH")
    basis_type = basis.get("basis_type")
    if basis_type not in contract["allowed_basis_type"]:
        raise ExpectationsGapError("OUTPUT_BASIS_TYPE_INVALID")
    _text(basis.get("description"), "OUTPUT_BASIS_DESCRIPTION_INVALID")
    _text(gap.get("atlas_forward_basis"), "OUTPUT_ATLAS_FORWARD_BASIS_INVALID")

    missing = gap.get("missing_inputs")
    categories = contract["input_categories"]
    if (
        not isinstance(missing, list)
        or missing != sorted(set(missing))
        or any(cat not in categories for cat in missing)
    ):
        raise ExpectationsGapError("OUTPUT_MISSING_INPUTS_INVALID")
    supplied_categories = sorted(set(categories) - set(missing))

    # The single hardest rule: absence of a paid consensus feed (public_estimates)
    # must never block the packet. It only ever forces basis_type away from
    # CONSENSUS.
    consensus_category = contract["consensus_category"]
    proxy_categories = set(contract["proxy_categories"])
    consensus_supplied = consensus_category not in missing
    proxy_supplied = bool(set(supplied_categories) & proxy_categories)
    if basis_type == "CONSENSUS" and not consensus_supplied:
        raise ExpectationsGapError("OUTPUT_BASIS_CONSENSUS_WITHOUT_PUBLIC_ESTIMATES")
    if basis_type == "PROXY" and not proxy_supplied:
        raise ExpectationsGapError("OUTPUT_BASIS_PROXY_WITHOUT_PROXY_INPUT")
    if basis_type == "UNKNOWN" and (consensus_supplied or proxy_supplied):
        raise ExpectationsGapError("OUTPUT_BASIS_UNKNOWN_WITH_USABLE_INPUT")
    if not supplied_categories and (status != "UNKNOWN" or basis_type != "UNKNOWN"):
        raise ExpectationsGapError("OUTPUT_ZERO_INPUT_MUST_BE_UNKNOWN")

    reasons = gap.get("gap_reasons")
    if not isinstance(reasons, list) or not reasons or any(
        not isinstance(item, str) or not item.strip() for item in reasons
    ):
        raise ExpectationsGapError("OUTPUT_GAP_REASONS_INVALID")

    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ExpectationsGapError("OUTPUT_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ExpectationsGapError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ExpectationsGapError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, output_path: Path) -> int:
    try:
        packet = build_packet(_read_json(input_path))
        write_json_atomic(output_path, packet)
        return 0
    except (ExpectationsGapError, OSError, TypeError, ValueError) as exc:
        print(f"Expectations Gap build failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
