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

★ `opportunity_state` classification (`classify_opportunity_state()`, see
  `docs/alpha_review_contract.md` for the full decision table) is a small,
  pure, deterministically ordered if/elif chain: `BLOCKED`, then the
  Expectations-Gap-negative gate (`REJECTED`/`WAIT_FOR_THESIS_REPAIR`), then
  an UNCONDITIONAL `WAIT_FOR_PRICE` -- see that function's own docstring for
  why this last gate is now unconditional rather than reflection-status-
  dependent.

★ SCOPE: Reflection Evidence Authority deferred (CIO PR #212, 2026-08-23,
  same ruling as `decision/price_reflection.py`'s own docstring). Only 4 of
  this module's 10 `opportunity_state` vocabulary members remain reachable
  through a real, validated packet: `BLOCKED`/`REJECTED`/`WAIT_FOR_THESIS_
  REPAIR`/`WAIT_FOR_PRICE`. `WAIT_FOR_RULE_RATIFICATION` is retired from the
  vocabulary entirely (contract `alpha_review/6`); the other 6 (`EARLY_
  DISCOVERY`/`ANTICIPATORY_REVIEW`/`WAIT_FOR_PULLBACK`/`WAIT_FOR_EVIDENCE`/
  `CONFIRMATION_REVIEW`/`EXPECTATION_EXHAUSTED`) remain legal vocabulary
  members (no further bump if reintroduced) but their classification logic
  has been REMOVED from `classify_opportunity_state()`, not merely made
  unreachable, and `validate_packet()` independently, unconditionally
  rejects any packet whose embedded `price_reflection.reflection_status !=
  "UNKNOWN"` regardless of what `opportunity_state` it claims -- closing the
  path where a forged/hand-constructed packet, or a direct call to
  `classify_opportunity_state()` bypassing `build_packet()`'s own upstream
  validation, could still reach one of them. Real production behavior is
  unaffected: none of the 6 were reachable through the real, unmocked
  pipeline even before this closing fix (reflection_status has been
  unconditionally `"UNKNOWN"` for every real subject since the scope
  reduction). Deferred, not abandoned -- see `decision/price_reflection.py`'s
  own docstring for the future-workstream note.
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
        "contract_version": "alpha_review/6",
        "output_schema_version": "alpha_review_packet/6",
        # ★ CIO closing-fix ruling (2026-08-23): `WAIT_FOR_RULE_RATIFICATION`
        #   is retired. It named a "reflection is confidently known, but the
        #   ratification policy behind it isn't approved yet" state that
        #   depended on a ratification-authority concept this repo has never
        #   genuinely implemented (mirroring the deleted `decision/event_
        #   evidence.py` Reflection Evidence Authority engine's own
        #   ratification registry). The remaining 6 reflection-status-
        #   dependent positive states (`EARLY_DISCOVERY`/`ANTICIPATORY_
        #   REVIEW`/`WAIT_FOR_PULLBACK`/`WAIT_FOR_EVIDENCE`/`CONFIRMATION_
        #   REVIEW`/`EXPECTATION_EXHAUSTED`) stay in this vocabulary --
        #   reserved, currently structurally unreachable (see `classify_
        #   opportunity_state`/`validate_packet` below) -- for the same
        #   future, P5-Rule-Authority-co-designed Reflection Evidence
        #   Authority workstream `decision/price_reflection.py`'s own
        #   `reflection_status` vocabulary defers to.
        "opportunity_states": [
            "EARLY_DISCOVERY", "ANTICIPATORY_REVIEW", "WAIT_FOR_PULLBACK",
            "WAIT_FOR_EVIDENCE", "CONFIRMATION_REVIEW", "EXPECTATION_EXHAUSTED",
            "REJECTED", "BLOCKED", "WAIT_FOR_PRICE",
            "WAIT_FOR_THESIS_REPAIR",
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


def classify_opportunity_state(ft: dict, gap: dict, pr: dict, decision_date: dt.date) -> str:
    """Pure, deterministic, auditable `opportunity_state` classification.

    Ordered if/elif chain, CIO Gate Hardening priority order -- each rule,
    once matched, returns immediately (no fallthrough). See
    `docs/alpha_review_contract.md` for the full decision table this
    implements.

    1. BLOCKED
    2. Expectations-Gap-negative gate (REJECTED / WAIT_FOR_THESIS_REPAIR;
       CONVERSION_DISAPPOINTED folds in here too, so REJECTED has exactly
       one point of truth)
    3. Price-Reflection-not-UNKNOWN gate (WAIT_FOR_PRICE) -- unconditional
       in this reduced scope (CIO closing-fix ruling, 2026-08-23): see the
       long comment on the gate itself.

    `decision_date` is accepted but currently unused by this function --
    kept in the signature for call-site/output-shape stability and because
    a future, redesigned positive-state chain (deferred workstream) will
    very likely need it again (the old ANTICIPATORY_REVIEW gate 7 used it
    for a future-dated-evidence check).
    """
    earnings_status = ft["earnings_conversion"]["status"]
    gap_status = gap["status"]
    reflection_status = pr["reflection_status"]

    # 1. BLOCKED -- nothing here has a real evidentiary basis to review.
    no_real_evidence = len(ft["observed_facts"]) == 0 and len(ft["evidence_lineage"]) == 0
    triple_unknown = earnings_status == "UNKNOWN" and gap_status == "UNKNOWN" and reflection_status == "UNKNOWN"
    if no_real_evidence or triple_unknown:
        return "BLOCKED"

    # 2. Expectations-Gap-negative gate -- REJECTED's single point of truth.
    #    CONVERSION_DISAPPOINTED forces REJECTED independent of gap status.
    #    A NEGATIVE gap with no live earnings-conversion hypothesis
    #    (UNKNOWN) has nothing left to hold onto -- REJECTED. A NEGATIVE gap
    #    WITH a real earnings-conversion hypothesis still standing may yet
    #    repair once the market re-prices -- WAIT_FOR_THESIS_REPAIR, not
    #    REJECTED. (OVEREXTENDED+NEGATIVE, the old REJECTED clause, is now a
    #    strict subset of this broader NEGATIVE-gap rule.)
    if earnings_status == "CONVERSION_DISAPPOINTED":
        return "REJECTED"
    if gap_status == "NEGATIVE":
        if earnings_status == "UNKNOWN":
            return "REJECTED"
        return "WAIT_FOR_THESIS_REPAIR"

    # 3. Reflection-not-UNKNOWN gate -- UNCONDITIONAL in this reduced scope.
    #
    #    ★ CIO closing-fix ruling (2026-08-23, immediately after the P8-10
    #    scope reduction): `decision/price_reflection.py`'s
    #    `validate_packet()` now unconditionally rejects any packet whose
    #    `reflection_status != "UNKNOWN"` -- so a `pr` sub-object that
    #    reaches this function via the normal `build_packet()` entry point
    #    (which independently re-validates it through `PRICE_REFLECTION.
    #    validate_packet()` first) can only ever carry `reflection_status
    #    =="UNKNOWN"`. But `classify_opportunity_state()` is ALSO called
    #    directly, with a caller-constructed `pr` dict, bypassing that
    #    upstream check entirely -- this module's OWN gate-level tests do
    #    exactly that. Trusting `reflection_status`/`price_state`/
    #    `threshold_basis` at face value here would leave a second,
    #    independent path by which a forged/hand-built `pr` could still
    #    reach a positive/differentiated `opportunity_state`, even with
    #    `price_reflection.validate_packet()` itself fully locked down.
    #
    #    This function therefore enforces the SAME boundary again, on its
    #    own, unconditionally: for ANY `reflection_status` other than the
    #    literal `"UNKNOWN"` -- REGARDLESS of what `price_state`/
    #    `threshold_basis` claim -- the result is `WAIT_FOR_PRICE`, full
    #    stop. The entire former positive-state decision tree this
    #    subsumes (the narrative-only-core-evidence gate, `EXPECTATION_
    #    EXHAUSTED`, `WAIT_FOR_PULLBACK`, `CONFIRMATION_REVIEW`, the old
    #    early-earnings `WAIT_FOR_EVIDENCE` rule, `ANTICIPATORY_REVIEW`'s 7
    #    gates, and the `EARLY_DISCOVERY` fallback, plus `WAIT_FOR_RULE_
    #    RATIFICATION`, now retired from the vocabulary entirely) depended
    #    on a confidently-known `reflection_status` that no code anywhere
    #    in this repository can produce any more -- that logic has been
    #    REMOVED, not merely made unreachable, and moved to the same
    #    future, P5-Rule-Authority-co-designed Reflection Evidence
    #    Authority workstream `decision/price_reflection.py`'s own removal
    #    already deferred to. Real production behavior is UNCHANGED by
    #    this: none of the removed branches were reachable through the
    #    real, unmocked `build_packet()` pipeline even before this fix
    #    (reflection_status has been unconditionally `"UNKNOWN"` for every
    #    real subject since the scope reduction) -- this closes a forged-
    #    input/direct-call bypass surface only.
    # (reflection_status is deliberately NOT branched on further -- gates 1-2
    #  above have already passed, and every remaining case, "UNKNOWN" or
    #  otherwise, returns the same safe result.)
    return "WAIT_FOR_PRICE"


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
    reflection_status = pr["reflection_status"]
    price_state = pr["price_state"]
    threshold_ratified = pr["threshold_basis"] == "RATIFIED"
    if state == "REJECTED":
        # Gate 2's single point of truth: CONVERSION_DISAPPOINTED forces
        # REJECTED independent of gap status; a NEGATIVE gap with no live
        # earnings-conversion hypothesis (UNKNOWN) is REJECTED too.
        # OVEREXTENDED+NEGATIVE (the pre-hardening REJECTED clause) is now a
        # strict subset of the broader NEGATIVE-gap arm below, so it needs
        # no separate check here.
        ok = earnings_status == "CONVERSION_DISAPPOINTED" or (gap_status == "NEGATIVE" and earnings_status == "UNKNOWN")
    elif state == "WAIT_FOR_THESIS_REPAIR":
        ok = gap_status == "NEGATIVE" and earnings_status != "UNKNOWN"
    elif state == "WAIT_FOR_PRICE":
        # ★ CIO closing-fix ruling (2026-08-23): `classify_opportunity_
        #   state()` now returns WAIT_FOR_PRICE unconditionally once gates
        #   1-2 pass (see that function's own docstring) -- WAIT_FOR_RULE_
        #   RATIFICATION is retired, and its old "reflection known but
        #   unratified" case now folds into this same state. `validate_
        #   packet()`'s own unconditional `pr["reflection_status"] !=
        #   "UNKNOWN"` rejection (checked before this function ever runs)
        #   independently guarantees `reflection_status == "UNKNOWN"` for
        #   any packet that reaches this point at all, so this remains a
        #   real (if now redundant, defense-in-depth) invariant rather than
        #   a vacuous one.
        ok = reflection_status == "UNKNOWN"
    elif state == "EXPECTATION_EXHAUSTED":
        ok = reflection_status == "FULLY_REFLECTED" and gap_status == "POSITIVE" and threshold_ratified
    elif state == "WAIT_FOR_PULLBACK":
        ok = (
            (reflection_status == "FULLY_REFLECTED" or price_state == "OVEREXTENDED")
            and gap_status != "NEGATIVE"
            and threshold_ratified
        )
    elif state == "CONFIRMATION_REVIEW":
        # reflection_status != UNKNOWN is now part of this invariant too --
        # gate 3 (WAIT_FOR_PRICE) always intercepts an UNKNOWN reflection
        # (or a non-RATIFIED threshold_basis, CIO round 3) before this
        # positive state can ever be reached (the exact bug the original
        # hardening closed: CONFIRMATION_REVIEW used to be reachable with
        # price_reflection.status==UNKNOWN; CIO round 2 retargeted the same
        # invariant onto reflection_status/price_state after the price/
        # reflection split).
        ok = (
            earnings_status in CONFIRMED_EARNINGS_STATUSES
            and reflection_status not in ("FULLY_REFLECTED", "UNKNOWN")
            and price_state != "OVEREXTENDED"
            and threshold_ratified
        )
    elif state in ("WAIT_FOR_EVIDENCE", "EARLY_DISCOVERY"):
        # Both states are reachable via more than one path now (the old
        # earnings/gap/price rule or gate-4's narrative-only-core-evidence
        # rule for WAIT_FOR_EVIDENCE; several positive-state fallthroughs for
        # EARLY_DISCOVERY) whose full precondition (observed_facts'
        # source_class) is not persisted as a stand-alone field on this
        # packet -- not fully reconstructable, see this function's
        # docstring. The one invariant that IS reconstructable and holds for
        # every path to either state: gates 1-3 must already have passed,
        # i.e. this is real evidence (not BLOCKED), the gap is not NEGATIVE,
        # reflection is not UNKNOWN, and the threshold basis is RATIFIED
        # (CIO round 3).
        ok = (
            gap_status != "NEGATIVE"
            and reflection_status != "UNKNOWN"
            and earnings_status != "CONVERSION_DISAPPOINTED"
            and threshold_ratified
        )
    elif state == "ANTICIPATORY_REVIEW":
        ok = (
            earnings_status != "UNKNOWN"
            and (gap_status == "POSITIVE" or (gap_status != "NEGATIVE" and gap["market_expectation_basis"]["basis_type"] == "PROXY"))
            and reflection_status not in ("FULLY_REFLECTED", "UNKNOWN")
            and price_state != "OVEREXTENDED"
            and catalysts_count > 0
            and invalidation_count > 0
            and threshold_ratified
        )
    else:
        # BLOCKED: no independent, fully-reconstructable invariant beyond
        # the closed-vocab + hash checks already performed.
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
        f"price_reflection.reflection_status={pr['reflection_status']}, "
        f"price_reflection.price_state={pr['price_state']}.",
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
        "Add only while price_reflection.reflection_status remains outside FULLY_REFLECTED "
        "and price_reflection.price_state remains outside OVEREXTENDED.",
    ]


def _reduce_conditions(ft: dict, gap: dict, pr: dict) -> list[str]:
    return [
        "Reduce if expectations_gap.status turns NEGATIVE.",
        "Reduce if price_reflection.price_state becomes OVEREXTENDED.",
    ]


def _invalidation_conditions(ft: dict) -> list[str]:
    """Mostly pass-through of forward_thesis.invalidation_conditions (already
    guaranteed non-empty by that module's own contract), plus one
    price_reflection-derived condition. Always non-empty as a result."""
    conditions = list(ft["invalidation_conditions"])
    conditions.append(
        "If price_reflection.reflection_status becomes FULLY_REFLECTED or "
        "price_reflection.price_state becomes OVEREXTENDED before the "
        "earnings-conversion thesis firms up (see "
        "forward_thesis.earnings_conversion.status)."
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
        "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
        "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
        "reflection_reference", "valuation_context", "reasons", "missing_inputs",
        "data_source_scope",
    }
    if not isinstance(pr, dict) or set(pr) != pr_fields:
        raise AlphaReviewError("PRICE_REFLECTION_FIELDS_MISMATCH")
    if pr.get("price_state") not in PR_CONTRACT["allowed_price_state"]:
        raise AlphaReviewError("PRICE_REFLECTION_PRICE_STATE_INVALID")
    if pr.get("reflection_status") not in PR_CONTRACT["allowed_reflection_status"]:
        raise AlphaReviewError("PRICE_REFLECTION_REFLECTION_STATUS_INVALID")
    # ★ CIO closing-fix ruling (2026-08-23): the SAME unconditional lock
    #   `price_reflection.validate_packet()` now enforces on its own output
    #   (`decision/price_reflection.py`'s own docstring), re-asserted here
    #   INDEPENDENTLY on the EMBEDDED `price_reflection` sub-object of an
    #   Alpha Review packet -- this module's own `validate_packet()` never
    #   re-calls `PRICE_REFLECTION.validate_packet()` on an already-
    #   assembled packet's embedded `pr` (only `build_packet()` does, on
    #   the way in), so without this check a tampered/forged Alpha Review
    #   packet could claim an embedded `reflection_status` of e.g.
    #   `"FULLY_REFLECTED"` paired with a forged positive `opportunity_
    #   state`, re-signed, and this function would never independently
    #   catch it. `UNDER_REFLECTED`/`PARTIALLY_REFLECTED`/`FULLY_REFLECTED`
    #   remain legal `PR_CONTRACT` vocabulary members (checked just above,
    #   no contract bump) but may never appear on any packet THIS function
    #   accepts while the Reflection Evidence Authority they'd require
    #   remains deferred, future work.
    if pr.get("reflection_status") != "UNKNOWN":
        raise AlphaReviewError("PRICE_REFLECTION_REFLECTION_STATUS_MUST_BE_UNKNOWN_IN_THIS_REDUCED_SCOPE")
    if pr.get("confidence") not in PR_CONTRACT["allowed_confidence"]:
        raise AlphaReviewError("PRICE_REFLECTION_CONFIDENCE_INVALID")
    if pr.get("threshold_basis") not in PR_CONTRACT["allowed_threshold_basis"]:
        raise AlphaReviewError("PRICE_REFLECTION_THRESHOLD_BASIS_INVALID")
    # ★ CIO round 3, required item 4: re-assert the same tamper-evident
    #   invariant classify_opportunity_state()/_check_opportunity_state_
    #   consistency() enforce -- a non-RATIFIED threshold_basis can never
    #   coexist with a positive/differentiated opportunity_state on any
    #   packet this function accepts, however constructed. Now redundant
    #   with the unconditional reflection_status check above in practice
    #   (every positive state already required reflection_status !=
    #   "UNKNOWN", which can no longer reach this point at all), kept as
    #   independent defense-in-depth rather than removed.
    if pr.get("threshold_basis") != "RATIFIED" and packet.get("opportunity_state") not in (
        "BLOCKED", "REJECTED", "WAIT_FOR_THESIS_REPAIR", "WAIT_FOR_PRICE",
    ):
        raise AlphaReviewError("OUTPUT_UNRATIFIED_THRESHOLD_BASIS_UNLOCKED_OPPORTUNITY_STATE")
    # ★ CIO round 4, required item 6 (new, independent invariant): a genuine
    #   reflection_status==UNKNOWN (real-evidence gap) can never coexist
    #   with any opportunity_state other than the closed fail-set below --
    #   regardless of threshold_basis. This is deliberately independent of
    #   the check above: a RATIFIED threshold_basis with an UNKNOWN
    #   reflection_status was not previously re-asserted here at all (the
    #   old check above only fired for threshold_basis != RATIFIED), which
    #   is exactly the gap this closes.
    if pr.get("reflection_status") == "UNKNOWN" and packet.get("opportunity_state") not in (
        "BLOCKED", "REJECTED", "WAIT_FOR_THESIS_REPAIR", "WAIT_FOR_PRICE",
    ):
        raise AlphaReviewError("OUTPUT_UNKNOWN_REFLECTION_STATUS_UNLOCKED_OPPORTUNITY_STATE")

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
