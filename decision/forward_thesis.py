#!/usr/bin/env python3
"""P8-08 Forward Thesis / Earnings Conversion packet builder.

This module is a **pure evidence-assembly and forward-inference packet
builder**. It sits upstream of the P8-07 Evidence -> Thesis -> Buy Review
slice (`decision/investment_decision_review.py`) and does not import from or
modify that module.

★ What this module answers: "given caller-supplied observed facts, forward
  inferences, an earnings-conversion narrative, and evidence lineage, is the
  packet internally consistent, source-backed, and free of lookahead?"

⛔ What this module never does:
  ⛔ decide Stage, Candidate, Ready, or Buy promotion
  ⛔ produce a Rule PASS/FAIL result of any kind
  ⛔ authorize any trade, order, or production action
  ⛔ perform ticker identity mapping/resolution (that is the asset/security
     master's job -- this module only checks `atlas_linked_ticker` is a
     non-empty string)
  ⛔ gate on `earnings_conversion.status == CONVERSION_CONFIRMED` -- every
     status in the closed vocabulary, including early-stage/low-confidence
     ones, builds a valid packet. This module has zero opinion on what is
     "allowed" downstream; it only labels state honestly.

★ Fact / inference separation is load-bearing (hard rule 1). `observed_facts`
  entries must carry a `source_ref` that resolves into `evidence_lineage`, and
  their `as_of` must never be after `decision_date`. Forward-looking claims
  belong in `forward_inferences`, never in `observed_facts`.

★ No future timestamps anywhere (hard rule 2). `generated_at`, every
  `observed_facts[].as_of`, and every `evidence_lineage[].filing_date` must be
  <= a caller-supplied `as_of_ceiling` (an optional field on the raw input
  document, not persisted in the output packet) or <= `decision_date` if no
  ceiling is given. `validate_packet()` re-derives the packet from its own
  content using `decision_date` as the ceiling -- this is always safe because
  hard rule 1 (as_of <= decision_date) is enforced unconditionally at build
  time regardless of what ceiling was used, so re-deriving with the (looser
  or equal) `decision_date` fallback can never under-validate a packet that
  was legitimately built.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "forward_thesis_contract.json"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/=-]{0,127}$")

# ★ Same field names/shape as bridge/evidence_envelope.py's SOURCE_IDENTITY_FIELDS
#   (accession, filing_date, exhibit_type, exhibit_document, source_sha256).
#   Not imported from that module -- importing it pulls in observation/store.py
#   via a sys.path mutation, a coupling this pure-assembly validator avoids.
#   Field names are kept identical on purpose so a later PR can bridge the two.
SEC_DART_IDENTITY_FIELDS = (
    "accession", "filing_date", "exhibit_type", "exhibit_document", "source_sha256",
)

# ★ Amount markers that make a capital_commitment figure NOT a bare precise
#   number (hard rule 5). Either the literal UNKNOWN token or a visible range
#   separator is required unless a source_ref backs a precise figure.
_RANGE_MARKERS = ("-", "–", "—", "~", "..", " to ", "±")


class ForwardThesisError(ValueError):
    """Fail-closed P8-08 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardThesisError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "forward_thesis/1",
        "output_schema_version": "forward_thesis_packet/1",
        "earnings_conversion_statuses": [
            "PRE_REVENUE_SIGNAL", "BACKLOG_BUILDING", "REVENUE_CONVERSION_EXPECTED",
            "MARGIN_CONVERSION_EXPECTED", "CONVERSION_CONFIRMED",
            "CONVERSION_DISAPPOINTED", "UNKNOWN",
        ],
        "confidence_levels": ["LOW", "MEDIUM", "HIGH", "UNKNOWN"],
        "directions": ["UP", "DOWN", "FLAT", "UNKNOWN"],
        "observed_fact_source_classes": [
            "EXHIBIT_EXTRACTED", "NARRATIVE_SOURCED", "PRICE_FEED",
        ],
        "evidence_lineage_source_types": [
            "SEC_EXHIBIT", "DART_EXHIBIT", "NARRATIVE_SOURCED", "PRICE_FEED", "OTHER",
        ],
        "authority": {
            "thesis_assembly_only": True,
            "forward_inference_generation_authorized": True,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "rule_result_generation_authorized": False,
            "ticker_identity_mapping_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ForwardThesisError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ForwardThesisError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


# ── primitive validators ────────────────────────────────────────────────
def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ForwardThesisError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ForwardThesisError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ForwardThesisError(code)
    return value


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ForwardThesisError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ForwardThesisError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ForwardThesisError(code)
    return parsed


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ForwardThesisError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ForwardThesisError(code) from exc
    if parsed.isoformat() != value:
        raise ForwardThesisError(code)
    return parsed


def _texts(value, code: str, required: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (required and not value)
        or value != list(dict.fromkeys(value))
        or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in value)
    ):
        raise ForwardThesisError(code)
    return list(value)


# ── composite field validators ──────────────────────────────────────────
def _validate_capital_commitment(value, contract: dict) -> dict:
    fields = {"description", "amount_range_or_unknown", "currency_or_unknown", "source_ref"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError("CAPITAL_COMMITMENT_FIELDS_MISMATCH")
    description = _text(value.get("description"), "CAPITAL_COMMITMENT_DESCRIPTION_INVALID")
    amount = value.get("amount_range_or_unknown")
    if not isinstance(amount, str) or not amount.strip() or amount != amount.strip():
        raise ForwardThesisError("CAPITAL_COMMITMENT_AMOUNT_INVALID")
    currency = _text(value.get("currency_or_unknown"), "CAPITAL_COMMITMENT_CURRENCY_INVALID")
    source_ref = value.get("source_ref")
    if source_ref is not None:
        source_ref = _token(source_ref, "CAPITAL_COMMITMENT_SOURCE_REF_INVALID")
    is_unknown = amount.upper() == "UNKNOWN"
    is_range = any(marker in amount for marker in _RANGE_MARKERS)
    if not is_unknown and not is_range and source_ref is None:
        # hard rule 5: a bare precise figure with no source_ref and no
        # UNKNOWN/range marker is a fabrication risk -- reject.
        raise ForwardThesisError("CAPITAL_COMMITMENT_PRECISE_FIGURE_WITHOUT_SOURCE")
    return {
        "description": description,
        "amount_range_or_unknown": amount,
        "currency_or_unknown": currency,
        "source_ref": source_ref,
    }


def _validate_evidence_lineage_entry(value, index: int, decision_date: dt.date, ceiling: dt.date, contract: dict) -> dict:
    context = f"evidence_lineage:{index}"
    fields = set(SEC_DART_IDENTITY_FIELDS) | {"source_ref", "source_type"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError(f"EVIDENCE_LINEAGE_FIELDS_MISMATCH:{context}")
    source_ref = _token(value.get("source_ref"), f"EVIDENCE_LINEAGE_SOURCE_REF_INVALID:{context}")
    source_type = value.get("source_type")
    if source_type not in contract["evidence_lineage_source_types"]:
        raise ForwardThesisError(f"EVIDENCE_LINEAGE_SOURCE_TYPE_INVALID:{context}")
    if source_type in {"SEC_EXHIBIT", "DART_EXHIBIT"}:
        accession = _text(value.get("accession"), f"EVIDENCE_LINEAGE_ACCESSION_INVALID:{context}")
        filing_date = _date(value.get("filing_date"), f"EVIDENCE_LINEAGE_FILING_DATE_INVALID:{context}")
        if filing_date > decision_date:
            raise ForwardThesisError(f"EVIDENCE_LINEAGE_FILING_DATE_AFTER_DECISION_DATE:{context}")
        if filing_date > ceiling:
            raise ForwardThesisError(f"EVIDENCE_LINEAGE_FILING_DATE_AFTER_CEILING:{context}")
        exhibit_type = _text(value.get("exhibit_type"), f"EVIDENCE_LINEAGE_EXHIBIT_TYPE_INVALID:{context}")
        exhibit_document = _text(value.get("exhibit_document"), f"EVIDENCE_LINEAGE_EXHIBIT_DOCUMENT_INVALID:{context}")
        source_sha256 = _sha(value.get("source_sha256"), f"EVIDENCE_LINEAGE_SOURCE_SHA_INVALID:{context}")
        filing_date_out = filing_date.isoformat()
    else:
        if (
            value.get("accession") is not None
            or value.get("exhibit_type") is not None
            or value.get("exhibit_document") is not None
        ):
            raise ForwardThesisError(f"EVIDENCE_LINEAGE_NARRATIVE_IDENTITY_FIELDS_MUST_BE_NULL:{context}")
        accession = None
        exhibit_type = None
        exhibit_document = None
        filing_date_out = None
        if value.get("filing_date") is not None:
            filing_date = _date(value.get("filing_date"), f"EVIDENCE_LINEAGE_FILING_DATE_INVALID:{context}")
            if filing_date > decision_date or filing_date > ceiling:
                raise ForwardThesisError(f"EVIDENCE_LINEAGE_FILING_DATE_AFTER_CEILING:{context}")
            filing_date_out = filing_date.isoformat()
        source_sha256 = None if value.get("source_sha256") is None else _sha(
            value.get("source_sha256"), f"EVIDENCE_LINEAGE_SOURCE_SHA_INVALID:{context}"
        )
    return {
        "source_ref": source_ref,
        "source_type": source_type,
        "accession": accession,
        "filing_date": filing_date_out,
        "exhibit_type": exhibit_type,
        "exhibit_document": exhibit_document,
        "source_sha256": source_sha256,
    }


def _validate_observed_fact(value, index: int, decision_date: dt.date, ceiling: dt.date, evidence_refs: set, contract: dict) -> dict:
    context = f"observed_facts:{index}"
    fields = {"statement", "source_ref", "source_class", "as_of"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError(f"OBSERVED_FACT_FIELDS_MISMATCH:{context}")
    statement = _text(value.get("statement"), f"OBSERVED_FACT_STATEMENT_INVALID:{context}")
    # hard rule 1: a fact with no source_ref is a disguised inference -- reject.
    source_ref = _token(value.get("source_ref"), f"OBSERVED_FACT_SOURCE_REF_INVALID:{context}")
    if source_ref not in evidence_refs:
        raise ForwardThesisError(f"OBSERVED_FACT_SOURCE_REF_DANGLING:{context}")
    source_class = value.get("source_class")
    if source_class not in contract["observed_fact_source_classes"]:
        raise ForwardThesisError(f"OBSERVED_FACT_SOURCE_CLASS_INVALID:{context}")
    as_of = _date(value.get("as_of"), f"OBSERVED_FACT_AS_OF_INVALID:{context}")
    # hard rule 1 + 2: a fact dated after decision_date/ceiling is a
    # future-fact/lookahead violation, not an observed fact -- reject.
    if as_of > decision_date:
        raise ForwardThesisError(f"OBSERVED_FACT_AS_OF_AFTER_DECISION_DATE:{context}")
    if as_of > ceiling:
        raise ForwardThesisError(f"OBSERVED_FACT_AS_OF_AFTER_CEILING:{context}")
    return {
        "statement": statement,
        "source_ref": source_ref,
        "source_class": source_class,
        "as_of": as_of.isoformat(),
    }


def _validate_forward_inference(value, index: int, contract: dict) -> dict:
    context = f"forward_inferences:{index}"
    fields = {"statement", "basis", "confidence"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError(f"FORWARD_INFERENCE_FIELDS_MISMATCH:{context}")
    statement = _text(value.get("statement"), f"FORWARD_INFERENCE_STATEMENT_INVALID:{context}")
    basis = _text(value.get("basis"), f"FORWARD_INFERENCE_BASIS_INVALID:{context}")
    confidence = value.get("confidence")
    if confidence not in contract["confidence_levels"]:
        raise ForwardThesisError(f"FORWARD_INFERENCE_CONFIDENCE_INVALID:{context}")
    return {"statement": statement, "basis": basis, "confidence": confidence}


def _validate_assumption(value, index: int) -> dict:
    context = f"assumptions:{index}"
    fields = {"statement", "why_unconfirmed"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError(f"ASSUMPTION_FIELDS_MISMATCH:{context}")
    return {
        "statement": _text(value.get("statement"), f"ASSUMPTION_STATEMENT_INVALID:{context}"),
        "why_unconfirmed": _text(value.get("why_unconfirmed"), f"ASSUMPTION_WHY_UNCONFIRMED_INVALID:{context}"),
    }


def _validate_counter_evidence(value, index: int) -> dict:
    context = f"counter_evidence:{index}"
    fields = {"statement", "source_ref"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError(f"COUNTER_EVIDENCE_FIELDS_MISMATCH:{context}")
    return {
        "statement": _text(value.get("statement"), f"COUNTER_EVIDENCE_STATEMENT_INVALID:{context}"),
        "source_ref": _token(value.get("source_ref"), f"COUNTER_EVIDENCE_SOURCE_REF_INVALID:{context}"),
    }


def _validate_catalyst(value, index: int) -> dict:
    context = f"catalysts:{index}"
    fields = {"description", "expected_date_or_window", "source_ref"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError(f"CATALYST_FIELDS_MISMATCH:{context}")
    source_ref = value.get("source_ref")
    if source_ref is not None:
        source_ref = _token(source_ref, f"CATALYST_SOURCE_REF_INVALID:{context}")
    return {
        "description": _text(value.get("description"), f"CATALYST_DESCRIPTION_INVALID:{context}"),
        "expected_date_or_window": _text(value.get("expected_date_or_window"), f"CATALYST_EXPECTED_WINDOW_INVALID:{context}"),
        "source_ref": source_ref,
    }


def _validate_earnings_conversion(value, contract: dict) -> dict:
    fields = {
        "status", "mechanism", "expected_start_window", "expected_duration",
        "revenue_direction", "margin_direction", "confidence", "blockers",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ForwardThesisError("EARNINGS_CONVERSION_FIELDS_MISMATCH")
    status = value.get("status")
    if status not in contract["earnings_conversion_statuses"]:
        raise ForwardThesisError("EARNINGS_CONVERSION_STATUS_INVALID")
    revenue_direction = value.get("revenue_direction")
    if revenue_direction not in contract["directions"]:
        raise ForwardThesisError("EARNINGS_CONVERSION_REVENUE_DIRECTION_INVALID")
    margin_direction = value.get("margin_direction")
    if margin_direction not in contract["directions"]:
        raise ForwardThesisError("EARNINGS_CONVERSION_MARGIN_DIRECTION_INVALID")
    confidence = value.get("confidence")
    if confidence not in contract["confidence_levels"]:
        raise ForwardThesisError("EARNINGS_CONVERSION_CONFIDENCE_INVALID")
    return {
        "status": status,
        "mechanism": _text(value.get("mechanism"), "EARNINGS_CONVERSION_MECHANISM_INVALID"),
        "expected_start_window": _text(value.get("expected_start_window"), "EARNINGS_CONVERSION_START_WINDOW_INVALID"),
        "expected_duration": _text(value.get("expected_duration"), "EARNINGS_CONVERSION_DURATION_INVALID"),
        "revenue_direction": revenue_direction,
        "margin_direction": margin_direction,
        "confidence": confidence,
        "blockers": _texts(value.get("blockers"), "EARNINGS_CONVERSION_BLOCKERS_INVALID", required=False),
    }


def _validate_review_dates(value, decision_date: dt.date) -> list[str]:
    if not isinstance(value, list):
        raise ForwardThesisError("REVIEW_DATES_INVALID")
    seen = set()
    out = []
    for index, item in enumerate(value):
        parsed = _date(item, f"REVIEW_DATE_INVALID:{index}")
        rendered = parsed.isoformat()
        if rendered in seen:
            raise ForwardThesisError(f"REVIEW_DATE_DUPLICATE:{index}")
        seen.add(rendered)
        out.append(rendered)
    return out


# ── packet assembly ─────────────────────────────────────────────────────
INPUT_FIELDS = {
    "schema_version", "contract_version", "generated_at", "subject", "decision_date",
    "thesis_id", "thesis_version", "spend_owner", "capital_commitment",
    "revenue_recipient", "atlas_linked_ticker", "observed_facts", "forward_inferences",
    "assumptions", "counter_evidence", "unknowns", "earnings_conversion", "catalysts",
    "invalidation_conditions", "review_dates", "evidence_lineage", "as_of_ceiling",
}

OUTPUT_FIELDS = (INPUT_FIELDS - {"as_of_ceiling"}) | {"authority", "packet_sha256"}


def build_packet_unvalidated(input_value: dict, as_of_ceiling_override, contract: dict) -> dict:
    if not isinstance(input_value, dict) or set(input_value) != INPUT_FIELDS:
        raise ForwardThesisError("INPUT_FIELDS_MISMATCH")
    if (
        input_value.get("schema_version") != contract["output_schema_version"]
        or input_value.get("contract_version") != contract["contract_version"]
    ):
        raise ForwardThesisError("INPUT_IDENTITY_INVALID")

    decision_date = _date(input_value.get("decision_date"), "DECISION_DATE_INVALID")
    ceiling_raw = as_of_ceiling_override if as_of_ceiling_override is not None else input_value.get("as_of_ceiling")
    ceiling = decision_date if ceiling_raw is None else _date(ceiling_raw, "AS_OF_CEILING_INVALID")

    generated_at = _utc(input_value.get("generated_at"), "GENERATED_AT_INVALID")
    if generated_at.date() > ceiling:
        raise ForwardThesisError("GENERATED_AT_AFTER_CEILING")

    subject = _token(input_value.get("subject"), "SUBJECT_INVALID")
    thesis_id = _token(input_value.get("thesis_id"), "THESIS_ID_INVALID")
    thesis_version = input_value.get("thesis_version")
    if isinstance(thesis_version, bool) or not isinstance(thesis_version, int) or thesis_version < 1:
        raise ForwardThesisError("THESIS_VERSION_INVALID")

    spend_owner = input_value.get("spend_owner")
    if spend_owner is not None:
        spend_owner = _text(spend_owner, "SPEND_OWNER_INVALID")

    revenue_recipient = _text(input_value.get("revenue_recipient"), "REVENUE_RECIPIENT_INVALID")
    atlas_linked_ticker = _token(input_value.get("atlas_linked_ticker"), "ATLAS_LINKED_TICKER_INVALID")

    evidence_lineage_raw = input_value.get("evidence_lineage")
    if not isinstance(evidence_lineage_raw, list):
        raise ForwardThesisError("EVIDENCE_LINEAGE_INVALID")
    evidence_lineage = [
        _validate_evidence_lineage_entry(row, index, decision_date, ceiling, contract)
        for index, row in enumerate(evidence_lineage_raw)
    ]
    refs = [row["source_ref"] for row in evidence_lineage]
    if len(refs) != len(set(refs)):
        raise ForwardThesisError("EVIDENCE_LINEAGE_SOURCE_REF_DUPLICATE")
    evidence_refs = set(refs)

    observed_facts_raw = input_value.get("observed_facts")
    if not isinstance(observed_facts_raw, list):
        raise ForwardThesisError("OBSERVED_FACTS_INVALID")
    observed_facts = [
        _validate_observed_fact(row, index, decision_date, ceiling, evidence_refs, contract)
        for index, row in enumerate(observed_facts_raw)
    ]

    forward_inferences_raw = input_value.get("forward_inferences")
    if not isinstance(forward_inferences_raw, list):
        raise ForwardThesisError("FORWARD_INFERENCES_INVALID")
    forward_inferences = [
        _validate_forward_inference(row, index, contract)
        for index, row in enumerate(forward_inferences_raw)
    ]

    assumptions_raw = input_value.get("assumptions")
    if not isinstance(assumptions_raw, list):
        raise ForwardThesisError("ASSUMPTIONS_INVALID")
    assumptions = [_validate_assumption(row, index) for index, row in enumerate(assumptions_raw)]

    counter_evidence_raw = input_value.get("counter_evidence")
    if not isinstance(counter_evidence_raw, list):
        raise ForwardThesisError("COUNTER_EVIDENCE_INVALID")
    counter_evidence = [_validate_counter_evidence(row, index) for index, row in enumerate(counter_evidence_raw)]

    unknowns = _texts(input_value.get("unknowns"), "UNKNOWNS_INVALID", required=False)

    earnings_conversion = _validate_earnings_conversion(input_value.get("earnings_conversion"), contract)

    catalysts_raw = input_value.get("catalysts")
    if not isinstance(catalysts_raw, list):
        raise ForwardThesisError("CATALYSTS_INVALID")
    catalysts = [_validate_catalyst(row, index) for index, row in enumerate(catalysts_raw)]

    # hard rule 4: a thesis with zero invalidation conditions is a contract violation.
    invalidation_conditions = _texts(
        input_value.get("invalidation_conditions"), "INVALIDATION_CONDITIONS_EMPTY", required=True
    )

    review_dates = _validate_review_dates(input_value.get("review_dates"), decision_date)

    capital_commitment = _validate_capital_commitment(input_value.get("capital_commitment"), contract)

    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": input_value["generated_at"],
        "subject": subject,
        "decision_date": decision_date.isoformat(),
        "thesis_id": thesis_id,
        "thesis_version": thesis_version,
        "spend_owner": spend_owner,
        "capital_commitment": capital_commitment,
        "revenue_recipient": revenue_recipient,
        "atlas_linked_ticker": atlas_linked_ticker,
        "observed_facts": observed_facts,
        "forward_inferences": forward_inferences,
        "assumptions": assumptions,
        "counter_evidence": counter_evidence,
        "unknowns": unknowns,
        "earnings_conversion": earnings_conversion,
        "catalysts": catalysts,
        "invalidation_conditions": invalidation_conditions,
        "review_dates": review_dates,
        "evidence_lineage": evidence_lineage,
        "authority": copy.deepcopy(contract["authority"]),
    }


def build_packet(input_value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet = build_packet_unvalidated(input_value, None, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(packet, dict) or set(packet) != OUTPUT_FIELDS:
        raise ForwardThesisError("OUTPUT_FIELDS_MISMATCH")
    if packet.get("authority") != contract["authority"]:
        raise ForwardThesisError("OUTPUT_AUTHORITY_MISMATCH")

    reconstructed_input = {key: value for key, value in packet.items() if key not in ("authority", "packet_sha256")}
    reconstructed_input["as_of_ceiling"] = None
    # ★ tamper detection: independently rebuild from the packet's own content
    #   (using decision_date as the ceiling fallback -- see module docstring)
    #   and compare. Recomputing the stored hash alone can never legitimize a
    #   changed result; content must match the fresh rebuild too.
    expected = build_packet_unvalidated(reconstructed_input, None, contract)

    digest = _sha(packet.get("packet_sha256"), "OUTPUT_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ForwardThesisError("OUTPUT_SHA_MISMATCH")
    if unsigned != expected:
        raise ForwardThesisError("OUTPUT_CONTENT_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ForwardThesisError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
        write_json_atomic(output_path, build_packet(_read_json(input_path)))
        return 0
    except (ForwardThesisError, OSError, TypeError, ValueError) as exc:
        print(f"Forward Thesis build failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
