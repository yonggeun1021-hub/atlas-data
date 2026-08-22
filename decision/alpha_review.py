#!/usr/bin/env python3
"""P8-11 Anticipatory Alpha Review packet builder.

This module is a **pure composition and classification engine**. It consumes
three already-built, already-validated upstream packets —

  * `decision/forward_thesis.py`     (P8-08 Forward Thesis / Earnings Conversion)
  * `decision/expectations_gap.py`   (P8-09 Expectations Gap)
  * `decision/price_reflection.py`   (P8-10 Price Reflection)

— and assembles them into one `opportunity_state`-classified Alpha Review
packet. It never fetches evidence, never invents thesis content, and never
generates a Rule PASS/FAIL result or a Portfolio decision of its own. It is a
sibling capability to `decision/investment_decision_review.py` (P8-07), built
independently and in parallel — this module does not import from, modify, or
replace that module.

★ What this module answers: "given a Forward Thesis, an Expectations Gap, and
  a Price Reflection packet that all describe the same subject on the same
  decision_date, which one of a closed set of `opportunity_state` values
  best describes where this name sits right now, and why?"

⛔ What this module never does:
  ⛔ evaluate or generate any Rule (P5) PASS/FAIL result — `p5_rule_status` is
     a caller-supplied pass-through only, defaulting to `NOT_EVALUATED` when
     no rule packet reference is supplied. This module never calls into
     `rules/deterministic_rule_evaluator.py` or `rules/ratified_rule_decision.py`.
  ⛔ evaluate or generate any Portfolio Gate decision — `portfolio_status` is
     likewise a caller-supplied pass-through, defaulting to `NOT_EVALUATED`
     (no Portfolio Gate engine exists yet per the project Master Map).
  ⛔ produce a non-null `trade_proposal` — there is no ratified P5 PASS +
     Human Approval pathway wired to this module yet. `trade_proposal` is
     hard-coded to `None` in every packet this module can ever produce.
  ⛔ promote Stage/Candidate/Ready/Buy, authorize an action, an order, or any
     production/trading activity. See the `authority` dict below.

★ `opportunity_state` classification is a small, pure, deterministically
  ordered if/elif chain (see `classify_opportunity_state()` and
  `docs/alpha_review_contract.md` for the full decision table). `BLOCKED` and
  `REJECTED` are always checked first, before any positive-state
  classification, so a broken/negative case can never be shadowed by a
  positive one.
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
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "alpha_review_contract.json"
FORWARD_THESIS_PATH = ROOT / "decision" / "forward_thesis.py"
EXPECTATIONS_GAP_PATH = ROOT / "decision" / "expectations_gap.py"
PRICE_REFLECTION_PATH = ROOT / "decision" / "price_reflection.py"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/=-]{0,127}$")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ★ No package structure in this repo (no __init__.py) -- intra-repo cross
#   module imports load by file path, matching decision/investment_decision_review.py
#   and shadow/investment_review_shadow_ledger.py's convention exactly.
FORWARD_THESIS = _load_module("p8_11_forward_thesis", FORWARD_THESIS_PATH)
EXPECTATIONS_GAP = _load_module("p8_11_expectations_gap", EXPECTATIONS_GAP_PATH)
PRICE_REFLECTION = _load_module("p8_11_price_reflection", PRICE_REFLECTION_PATH)

# Single source of truth for the closed vocabularies this module cites --
# loaded once from each upstream module's own contract rather than
# re-declared here, so the two can never silently drift apart.
FT_CONTRACT = FORWARD_THESIS.load_contract()
EG_CONTRACT = EXPECTATIONS_GAP.load_contract()
PR_CONTRACT = PRICE_REFLECTION.load_contract()


class AlphaReviewError(ValueError):
    """Fail-closed P8-11 Alpha Review contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AlphaReviewError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "alpha_review/1",
        "output_schema_version": "alpha_review_packet/1",
        "opportunity_states": [
            "EARLY_DISCOVERY", "ANTICIPATORY_REVIEW", "WAIT_FOR_PULLBACK",
            "WAIT_FOR_EVIDENCE", "CONFIRMATION_REVIEW", "EXPECTATION_EXHAUSTED",
            "REJECTED", "BLOCKED",
        ],
        "p5_rule_statuses": ["PASS", "FAIL", "UNKNOWN", "UNDEFINED", "NOT_EVALUATED"],
        "default_p5_rule_status": "NOT_EVALUATED",
        "portfolio_statuses": ["PASS", "FAIL", "UNKNOWN", "NOT_EVALUATED"],
        "default_portfolio_status": "NOT_EVALUATED",
        "default_next_review_cadence_days": 30,
        "authority": {
            "alpha_review_assembly_only": True,
            "opportunity_state_classification_only": True,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "rule_result_generation_authorized": False,
            "portfolio_decision_authorized": False,
            "trade_proposal_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise AlphaReviewError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise AlphaReviewError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


# ── primitive validators (same shape as forward_thesis.py's) ───────────────
def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AlphaReviewError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise AlphaReviewError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AlphaReviewError(code)
    return value


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise AlphaReviewError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise AlphaReviewError(code) from exc


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise AlphaReviewError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise AlphaReviewError(code) from exc
    if parsed.isoformat() != value:
        raise AlphaReviewError(code)
    return parsed


def _texts(value, code: str, required: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (required and not value)
        or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in value)
    ):
        raise AlphaReviewError(code)
    return list(value)


# ── opportunity_state classification ────────────────────────────────────
CONFIRMED_EARNINGS_STATUSES = (
    "REVENUE_CONVERSION_EXPECTED", "MARGIN_CONVERSION_EXPECTED", "CONVERSION_CONFIRMED",
)
EARLY_EARNINGS_STATUSES = ("PRE_REVENUE_SIGNAL", "BACKLOG_BUILDING", "UNKNOWN")


def _no_future_dated_evidence(ft: dict, decision_date: dt.date) -> bool:
    """Defensive re-check (gate 7). Already guaranteed by forward_thesis's own
    validate_packet() at build time -- this re-asserts it independently."""
    for row in ft["evidence_lineage"]:
        filing_date = row.get("filing_date")
        if filing_date is not None and dt.date.fromisoformat(filing_date) > decision_date:
            return False
    for row in ft["observed_facts"]:
        if dt.date.fromisoformat(row["as_of"]) > decision_date:
            return False
    return True


def anticipatory_review_gates(ft: dict, gap: dict, pr: dict, decision_date: dt.date) -> dict:
    """The 7 ANTICIPATORY_REVIEW gates, each independently named and testable.

    `ft` is a validated forward_thesis packet, `gap` is the inner
    `expectations_gap` sub-object of a validated expectations_gap packet, `pr`
    is the inner `price_reflection` sub-object of a validated price_reflection
    packet. ALL 7 must be True for ANTICIPATORY_REVIEW to fire.
    """
    return {
        "gate1_catalyst_and_observed_evidence": (
            len(ft["catalysts"]) > 0
            and len(ft["observed_facts"]) > 0
            and len(ft["evidence_lineage"]) > 0
        ),
        "gate2_recipient_and_ticker_present": (
            bool(ft.get("revenue_recipient")) and bool(ft.get("atlas_linked_ticker"))
        ),
        "gate3_conversion_hypothesis_exists": ft["earnings_conversion"]["status"] != "UNKNOWN",
        "gate4_expectations_gap_supportive": (
            gap["status"] == "POSITIVE"
            or (gap["status"] != "NEGATIVE" and gap["market_expectation_basis"]["basis_type"] == "PROXY")
        ),
        "gate5_price_not_stretched_or_unknown": (
            pr["status"] not in ("FULLY_REFLECTED", "OVEREXTENDED", "UNKNOWN")
        ),
        "gate6_catalysts_and_invalidation_nonempty": (
            len(ft["catalysts"]) > 0 and len(ft["invalidation_conditions"]) > 0
        ),
        "gate7_no_future_dated_evidence": _no_future_dated_evidence(ft, decision_date),
    }


def classify_opportunity_state(ft: dict, gap: dict, pr: dict, decision_date: dt.date) -> str:
    """Pure, deterministic, auditable `opportunity_state` classification.

    Ordered if/elif chain -- BLOCKED and REJECTED are always checked first,
    before any positive-state classification. See
    `docs/alpha_review_contract.md` for the full decision table this
    implements.
    """
    earnings_status = ft["earnings_conversion"]["status"]
    gap_status = gap["status"]
    pr_status = pr["status"]

    # BLOCKED -- nothing here has a real evidentiary basis to review.
    no_real_evidence = len(ft["observed_facts"]) == 0 and len(ft["evidence_lineage"]) == 0
    triple_unknown = earnings_status == "UNKNOWN" and gap_status == "UNKNOWN" and pr_status == "UNKNOWN"
    if no_real_evidence or triple_unknown:
        return "BLOCKED"

    # REJECTED -- usable evidence, but the market/price/conversion signal says no.
    if (pr_status == "OVEREXTENDED" and gap_status == "NEGATIVE") or earnings_status == "CONVERSION_DISAPPOINTED":
        return "REJECTED"

    # ANTICIPATORY_REVIEW -- all 7 gates must hold simultaneously.
    if all(anticipatory_review_gates(ft, gap, pr, decision_date).values()):
        return "ANTICIPATORY_REVIEW"

    # EXPECTATION_EXHAUSTED -- a positive gap that price has already fully priced in.
    # Reserved specifically for FULLY_REFLECTED + POSITIVE; OVEREXTENDED (any
    # gap) and FULLY_REFLECTED-with-non-POSITIVE fall through to
    # WAIT_FOR_PULLBACK below, per the module spec's tie-break rule.
    if pr_status == "FULLY_REFLECTED" and gap_status == "POSITIVE":
        return "EXPECTATION_EXHAUSTED"

    # WAIT_FOR_PULLBACK -- good story, bad entry price/timing right now.
    if pr_status in ("FULLY_REFLECTED", "OVEREXTENDED") and gap_status != "NEGATIVE":
        return "WAIT_FOR_PULLBACK"

    # CONFIRMATION_REVIEW -- conversion is expected/confirmed and price hasn't run.
    if earnings_status in CONFIRMED_EARNINGS_STATUSES and pr_status not in ("OVEREXTENDED", "FULLY_REFLECTED"):
        return "CONFIRMATION_REVIEW"

    # WAIT_FOR_EVIDENCE -- too early, and the market's own view is itself unclear.
    if earnings_status in EARLY_EARNINGS_STATUSES and gap_status == "UNKNOWN" and pr_status != "UNDER_REFLECTED":
        return "WAIT_FOR_EVIDENCE"

    # EARLY_DISCOVERY -- default fallback: has real evidence, too early/thin to say more.
    return "EARLY_DISCOVERY"


def _check_opportunity_state_consistency(
    state: str, earnings_status: str, gap: dict, pr: dict, catalysts_count: int, invalidation_count: int
) -> None:
    """Tamper-detection sanity re-check for `validate_packet()`.

    Re-verifies the subset of `classify_opportunity_state()`'s invariants
    that are reconstructable from fields this packet actually retains
    (`earnings_conversion_status`, the embedded `expectations_gap`/
    `price_reflection` sub-objects, `catalyst_timing.catalysts` count, and
    `invalidation_conditions` count). Gates 1/2/7 of ANTICIPATORY_REVIEW, and
    the evidence-count arm of BLOCKED, depend on forward_thesis fields
    (`observed_facts`, `evidence_lineage`, `revenue_recipient`,
    `atlas_linked_ticker`, evidence dates) that are NOT persisted as
    stand-alone fields on this packet -- those gates were already enforced
    once, at `build_packet()` time, against the real, freshly re-validated
    forward_thesis packet. This is not a full re-derivation; it matches the
    same boundary `expectations_gap.py`/`price_reflection.py` already accept
    for their own upstream-supplied-then-not-persisted raw inputs.
    """
    gap_status = gap["status"]
    pr_status = pr["status"]
    if state == "REJECTED":
        ok = (pr_status == "OVEREXTENDED" and gap_status == "NEGATIVE") or earnings_status == "CONVERSION_DISAPPOINTED"
    elif state == "EXPECTATION_EXHAUSTED":
        ok = pr_status == "FULLY_REFLECTED" and gap_status == "POSITIVE"
    elif state == "WAIT_FOR_PULLBACK":
        ok = pr_status in ("FULLY_REFLECTED", "OVEREXTENDED") and gap_status != "NEGATIVE"
    elif state == "CONFIRMATION_REVIEW":
        ok = earnings_status in CONFIRMED_EARNINGS_STATUSES and pr_status not in ("OVEREXTENDED", "FULLY_REFLECTED")
    elif state == "WAIT_FOR_EVIDENCE":
        ok = earnings_status in EARLY_EARNINGS_STATUSES and gap_status == "UNKNOWN" and pr_status != "UNDER_REFLECTED"
    elif state == "ANTICIPATORY_REVIEW":
        ok = (
            earnings_status != "UNKNOWN"
            and (gap_status == "POSITIVE" or (gap_status != "NEGATIVE" and gap["market_expectation_basis"]["basis_type"] == "PROXY"))
            and pr_status not in ("FULLY_REFLECTED", "OVEREXTENDED", "UNKNOWN")
            and catalysts_count > 0
            and invalidation_count > 0
        )
    else:
        # BLOCKED / EARLY_DISCOVERY: no independent, fully-reconstructable
        # invariant beyond the closed-vocab + hash checks already performed.
        ok = True
    if not ok:
        raise AlphaReviewError(f"OPPORTUNITY_STATE_INCONSISTENT:{state}")


# ── derived-field builders (pass-through / summary only, no invented data) ──
def _catalyst_timing(ft: dict) -> dict:
    return {
        "catalysts": copy.deepcopy(ft["catalysts"]),
        "expected_start_window": ft["earnings_conversion"]["expected_start_window"],
    }


def _why_now(ft: dict, gap: dict) -> list[str]:
    """Every line cites a specific field from one of the three input packets."""
    lines = []
    for catalyst in ft["catalysts"]:
        lines.append(
            f"forward_thesis.catalysts: {catalyst['description']} "
            f"(expected {catalyst['expected_date_or_window']})"
        )
    for inference in ft["forward_inferences"]:
        lines.append(
            f"forward_thesis.forward_inferences: {inference['statement']} "
            f"(confidence={inference['confidence']})"
        )
    for reason in gap["gap_reasons"]:
        lines.append(f"expectations_gap.gap_reasons: {reason}")
    return lines


def _what_market_may_be_missing(ft: dict, gap: dict) -> list[str]:
    """Derived from expectations_gap.gap_reasons and forward_thesis.forward_inferences ONLY."""
    lines = [f"expectations_gap.gap_reasons: {reason}" for reason in gap["gap_reasons"]]
    lines += [
        f"forward_thesis.forward_inferences: {inference['statement']} (basis={inference['basis']})"
        for inference in ft["forward_inferences"]
    ]
    return lines


def _entry_conditions(ft: dict, gap: dict, pr: dict, opportunity_state: str) -> list[str]:
    lines = [
        f"opportunity_state={opportunity_state}; earnings_conversion.status="
        f"{ft['earnings_conversion']['status']}, expectations_gap.status={gap['status']}, "
        f"price_reflection.status={pr['status']}.",
    ]
    if opportunity_state in ("ANTICIPATORY_REVIEW", "CONFIRMATION_REVIEW", "EARLY_DISCOVERY"):
        lines.append(
            "Confirm at least one forward_thesis.catalysts entry has not yet been "
            "invalidated before sizing any review-only entry."
        )
    else:
        lines.append(
            f"No entry is supported while opportunity_state={opportunity_state}; "
            "wait for a state change before considering entry."
        )
    return lines


def _add_conditions(ft: dict, gap: dict, pr: dict) -> list[str]:
    return [
        "Add only on a confirmed upgrade of earnings_conversion.status toward "
        "REVENUE_CONVERSION_EXPECTED/MARGIN_CONVERSION_EXPECTED/CONVERSION_CONFIRMED, "
        "sourced from a newer forward_thesis packet version.",
        "Add only while price_reflection.status remains outside "
        "(FULLY_REFLECTED, OVEREXTENDED).",
    ]


def _reduce_conditions(ft: dict, gap: dict, pr: dict) -> list[str]:
    return [
        "Reduce if expectations_gap.status turns NEGATIVE.",
        "Reduce if price_reflection.status becomes OVEREXTENDED.",
    ]


def _invalidation_conditions(ft: dict) -> list[str]:
    """Mostly pass-through of forward_thesis.invalidation_conditions (already
    guaranteed non-empty by that module's own contract), plus one
    price_reflection-derived condition. Always non-empty as a result."""
    conditions = list(ft["invalidation_conditions"])
    conditions.append(
        "If price_reflection.status becomes FULLY_REFLECTED or OVEREXTENDED "
        "before the earnings-conversion thesis firms up "
        "(see forward_thesis.earnings_conversion.status)."
    )
    return conditions


def _next_review_date(ft: dict, decision_date: dt.date, contract: dict) -> str:
    """One of forward_thesis.review_dates, at `decision_date` or later. If
    every review_date on file is stale (before decision_date), fall back to a
    fixed default cadence past decision_date -- still "later", never in the
    past."""
    candidates = sorted(
        parsed for parsed in (dt.date.fromisoformat(d) for d in ft["review_dates"])
        if parsed >= decision_date
    )
    if candidates:
        return candidates[0].isoformat()
    cadence_days = contract["default_next_review_cadence_days"]
    return (decision_date + dt.timedelta(days=cadence_days)).isoformat()


def _p5_rule_status(value, contract: dict) -> str:
    if value is None:
        return contract["default_p5_rule_status"]
    if value not in contract["p5_rule_statuses"]:
        raise AlphaReviewError("P5_RULE_STATUS_INVALID")
    return value


def _portfolio_status(value, contract: dict) -> str:
    if value is None:
        return contract["default_portfolio_status"]
    if value not in contract["portfolio_statuses"]:
        raise AlphaReviewError("PORTFOLIO_STATUS_INVALID")
    return value


# ── packet assembly ─────────────────────────────────────────────────────
OUTPUT_FIELDS = {
    "schema_version", "contract_version", "generated_at", "subject", "decision_date",
    "thesis_status", "earnings_conversion_status", "expectations_gap", "price_reflection",
    "catalyst_timing", "opportunity_state", "why_now", "what_market_may_be_missing",
    "entry_conditions", "add_conditions", "reduce_conditions", "invalidation_conditions",
    "next_review_date", "p5_rule_status", "portfolio_status", "trade_proposal",
    "authority", "packet_sha256",
}


def build_packet(
    *,
    forward_thesis_packet: dict,
    expectations_gap_packet: dict,
    price_reflection_packet: dict,
    generated_at: str,
    p5_rule_status: str | None = None,
    portfolio_status: str | None = None,
    contract: dict | None = None,
) -> dict:
    """Build an Alpha Review packet from three already-built upstream packets.

    All three sub-packets are required and independently re-validated against
    their own module's `validate_packet()` -- this module never trusts a
    caller-supplied dict without re-checking it. `p5_rule_status` and
    `portfolio_status` are optional, caller-supplied, closed-vocabulary
    pass-throughs -- this module never computes either one.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()

    if not isinstance(forward_thesis_packet, dict):
        raise AlphaReviewError("FORWARD_THESIS_PACKET_MISSING")
    if not isinstance(expectations_gap_packet, dict):
        raise AlphaReviewError("EXPECTATIONS_GAP_PACKET_MISSING")
    if not isinstance(price_reflection_packet, dict):
        raise AlphaReviewError("PRICE_REFLECTION_PACKET_MISSING")

    ft = FORWARD_THESIS.validate_packet(forward_thesis_packet, FT_CONTRACT)
    eg_packet = EXPECTATIONS_GAP.validate_packet(expectations_gap_packet, EG_CONTRACT)
    pr_packet = PRICE_REFLECTION.validate_packet(price_reflection_packet, PR_CONTRACT)

    if not (ft["subject"] == eg_packet["subject"] == pr_packet["subject"]):
        raise AlphaReviewError("SUBJECT_MISMATCH_ACROSS_INPUT_PACKETS")
    if not (ft["decision_date"] == eg_packet["decision_date"] == pr_packet["decision_date"]):
        raise AlphaReviewError("DECISION_DATE_MISMATCH_ACROSS_INPUT_PACKETS")

    subject = ft["subject"]
    decision_date = _date(ft["decision_date"], "DECISION_DATE_INVALID")
    _utc(generated_at, "GENERATED_AT_INVALID")

    gap = eg_packet["expectations_gap"]
    pr = pr_packet["price_reflection"]

    opportunity_state = classify_opportunity_state(ft, gap, pr, decision_date)

    invalidation_conditions = _invalidation_conditions(ft)
    if not invalidation_conditions:
        raise AlphaReviewError("INVALIDATION_CONDITIONS_EMPTY")

    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": subject,
        "decision_date": ft["decision_date"],
        "thesis_status": ft["earnings_conversion"]["status"],
        "earnings_conversion_status": ft["earnings_conversion"]["status"],
        "expectations_gap": copy.deepcopy(gap),
        "price_reflection": copy.deepcopy(pr),
        "catalyst_timing": _catalyst_timing(ft),
        "opportunity_state": opportunity_state,
        "why_now": _why_now(ft, gap),
        "what_market_may_be_missing": _what_market_may_be_missing(ft, gap),
        "entry_conditions": _entry_conditions(ft, gap, pr, opportunity_state),
        "add_conditions": _add_conditions(ft, gap, pr),
        "reduce_conditions": _reduce_conditions(ft, gap, pr),
        "invalidation_conditions": invalidation_conditions,
        "next_review_date": _next_review_date(ft, decision_date, contract),
        "p5_rule_status": _p5_rule_status(p5_rule_status, contract),
        "portfolio_status": _portfolio_status(portfolio_status, contract),
        # ⛔ HARD-CODED None. There is no ratified P5 PASS + Human Approval
        #   pathway wired to this module in this MVP. No code path above can
        #   ever set this to anything else -- see test_alpha_review.py's
        #   trade_proposal-always-null regression.
        "trade_proposal": None,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(packet, dict) or set(packet) != OUTPUT_FIELDS:
        raise AlphaReviewError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("authority") != contract["authority"]
    ):
        raise AlphaReviewError("OUTPUT_IDENTITY_INVALID")

    # ★ Hash/tamper check runs early, before any deeper semantic/consistency
    #   validation below -- a packet whose content was changed without being
    #   rehashed is rejected uniformly here, regardless of which field was
    #   touched, exactly like forward_thesis.py's OUTPUT_SHA_MISMATCH.
    #   Rehashing alone can never legitimize a corrupted result, though --
    #   the semantic/consistency checks below still run against the
    #   (possibly still-corrupted) rehashed content.
    digest = _sha(packet.get("packet_sha256"), "OUTPUT_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise AlphaReviewError("OUTPUT_SHA_MISMATCH")

    _utc(packet.get("generated_at"), "OUTPUT_GENERATED_AT_INVALID")
    _token(packet.get("subject"), "OUTPUT_SUBJECT_INVALID")
    decision_date = _date(packet.get("decision_date"), "OUTPUT_DECISION_DATE_INVALID")

    # trade_proposal MUST always be null in this MVP.
    if packet.get("trade_proposal") is not None:
        raise AlphaReviewError("TRADE_PROPOSAL_MUST_BE_NULL")

    earnings_status = packet.get("earnings_conversion_status")
    if earnings_status not in FT_CONTRACT["earnings_conversion_statuses"]:
        raise AlphaReviewError("EARNINGS_CONVERSION_STATUS_INVALID")
    if packet.get("thesis_status") != earnings_status:
        raise AlphaReviewError("THESIS_STATUS_MISMATCH")

    gap = packet.get("expectations_gap")
    gap_fields = {
        "status", "magnitude", "confidence", "market_expectation_basis",
        "atlas_forward_basis", "gap_reasons", "missing_inputs",
    }
    if not isinstance(gap, dict) or set(gap) != gap_fields:
        raise AlphaReviewError("EXPECTATIONS_GAP_FIELDS_MISMATCH")
    if gap.get("status") not in EG_CONTRACT["allowed_status"]:
        raise AlphaReviewError("EXPECTATIONS_GAP_STATUS_INVALID")
    if gap.get("magnitude") not in EG_CONTRACT["allowed_magnitude"]:
        raise AlphaReviewError("EXPECTATIONS_GAP_MAGNITUDE_INVALID")
    if gap.get("confidence") not in EG_CONTRACT["allowed_confidence"]:
        raise AlphaReviewError("EXPECTATIONS_GAP_CONFIDENCE_INVALID")
    basis = gap.get("market_expectation_basis")
    if not isinstance(basis, dict) or basis.get("basis_type") not in EG_CONTRACT["allowed_basis_type"]:
        raise AlphaReviewError("EXPECTATIONS_GAP_BASIS_INVALID")
    if not isinstance(gap.get("gap_reasons"), list) or not gap["gap_reasons"]:
        raise AlphaReviewError("EXPECTATIONS_GAP_REASONS_INVALID")

    pr = packet.get("price_reflection")
    pr_fields = {
        "status", "confidence", "price_as_of", "relative_strength",
        "recent_return_windows", "event_reaction", "valuation_context",
        "reasons", "missing_inputs", "data_source_scope",
    }
    if not isinstance(pr, dict) or set(pr) != pr_fields:
        raise AlphaReviewError("PRICE_REFLECTION_FIELDS_MISMATCH")
    if pr.get("status") not in PR_CONTRACT["allowed_status"]:
        raise AlphaReviewError("PRICE_REFLECTION_STATUS_INVALID")
    if pr.get("confidence") not in PR_CONTRACT["allowed_confidence"]:
        raise AlphaReviewError("PRICE_REFLECTION_CONFIDENCE_INVALID")

    catalyst_timing = packet.get("catalyst_timing")
    if not isinstance(catalyst_timing, dict) or set(catalyst_timing) != {"catalysts", "expected_start_window"}:
        raise AlphaReviewError("CATALYST_TIMING_FIELDS_MISMATCH")
    catalysts = catalyst_timing.get("catalysts")
    if not isinstance(catalysts, list):
        raise AlphaReviewError("CATALYST_TIMING_CATALYSTS_INVALID")
    for row in catalysts:
        if not isinstance(row, dict) or set(row) != {"description", "expected_date_or_window", "source_ref"}:
            raise AlphaReviewError("CATALYST_TIMING_CATALYST_ROW_INVALID")
    _text(catalyst_timing.get("expected_start_window"), "CATALYST_TIMING_EXPECTED_START_WINDOW_INVALID")

    opportunity_state = packet.get("opportunity_state")
    if opportunity_state not in contract["opportunity_states"]:
        raise AlphaReviewError("OPPORTUNITY_STATE_INVALID")

    invalidation_conditions = _texts(packet.get("invalidation_conditions"), "INVALIDATION_CONDITIONS_EMPTY", required=True)
    _texts(packet.get("why_now"), "WHY_NOW_INVALID", required=False)
    _texts(packet.get("what_market_may_be_missing"), "WHAT_MARKET_MAY_BE_MISSING_INVALID", required=False)
    _texts(packet.get("entry_conditions"), "ENTRY_CONDITIONS_EMPTY", required=True)
    _texts(packet.get("add_conditions"), "ADD_CONDITIONS_EMPTY", required=True)
    _texts(packet.get("reduce_conditions"), "REDUCE_CONDITIONS_EMPTY", required=True)

    _check_opportunity_state_consistency(
        opportunity_state, earnings_status, gap, pr, len(catalysts), len(invalidation_conditions)
    )

    next_review_date = _date(packet.get("next_review_date"), "NEXT_REVIEW_DATE_INVALID")
    if next_review_date < decision_date:
        raise AlphaReviewError("NEXT_REVIEW_DATE_IN_PAST")

    if packet.get("p5_rule_status") not in contract["p5_rule_statuses"]:
        raise AlphaReviewError("P5_RULE_STATUS_INVALID")
    if packet.get("portfolio_status") not in contract["portfolio_statuses"]:
        raise AlphaReviewError("PORTFOLIO_STATUS_INVALID")

    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise AlphaReviewError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
        envelope = _read_json(input_path)
        if not isinstance(envelope, dict):
            raise AlphaReviewError("INPUT_ENVELOPE_NOT_OBJECT")
        packet = build_packet(**envelope)
        write_json_atomic(output_path, packet)
        return 0
    except (AlphaReviewError, OSError, TypeError, ValueError) as exc:
        print(f"Alpha Review build failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
