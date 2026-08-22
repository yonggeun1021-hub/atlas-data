#!/usr/bin/env python3
"""P10-07 append-only zero-capital Alpha Shadow Ledger for P8-11 review packets.

Mirrors `shadow/investment_review_shadow_ledger.py` (P10-06)'s exact
append-only hash-chain pattern: each record links to the previous record's
hash, `build_record()` never accepts a `previous_entry_hash` that skips a
link, and `validate_record()` independently recomputes `entry_hash` from the
unsigned payload and rejects any mismatch. This module records every
`decision/alpha_review.py` (P8-11) packet -- `BLOCKED`/`REJECTED` included --
as a hypothetical, always-zero-capital shadow proposal for later
retrospective learning.

★ `shadow_proposal.capital` is hard-coded to the integer `0`. There is no
  parameter anywhere in this module's public API that can override it.
★ `shadow_proposal.human_approval_required` is hard-coded to `True`, for the
  same reason.
★ `shadow_proposal.action` is a pure, exhaustive function of the input
  packet's `opportunity_state` AND `p5_rule_status` (see
  `action_for_opportunity_state()` / `config/alpha_shadow_ledger_contract.
  json`'s `opportunity_state_to_action`) -- never independently chosen.
  `SHADOW_ENTRY_REVIEW` is reachable ONLY when BOTH (a) `opportunity_state`
  is one of the three entry-eligible states (`ANTICIPATORY_REVIEW`,
  `EARLY_DISCOVERY`, `CONFIRMATION_REVIEW`) AND (b) `p5_rule_status==PASS`.
  Whenever (a) holds but (b) does not (`NOT_EVALUATED`/`UNKNOWN`/
  `UNDEFINED`/`FAIL`), the action is downgraded to `WAIT` instead --
  contract_version `alpha_shadow_ledger/2` (CIO Gate Hardening, defense in
  depth alongside `decision/alpha_review.py`'s own gates; this is what
  currently blocks every real Pilot subject from ever reaching
  `SHADOW_ENTRY_REVIEW`, since no ratified P5 packet exists for any of them
  yet -- `p5_rule_status` is `NOT_EVALUATED` for all four).

⛔ Retrospective evaluation is explicitly OUT OF SCOPE for this stage. This
  module does NOT compute `catalyst_date`, `hypothetical_return`,
  `benchmark_relative_return`, `maximum_adverse_excursion`,
  `maximum_favorable_excursion`, `invalidation_hit`, or
  `thesis_confirmation` -- none of those fields exist anywhere in this
  module's output schema. A later follow-up stage may add a *separate*
  retrospective-evaluation capability on top of this ledger, but whenever it
  does, it MUST NEVER compute any of those fields using data dated after the
  owning record's own `signal_date` (anti-lookahead) -- exactly the same
  discipline `decision/forward_thesis.py` already enforces for
  `observed_facts[].as_of` vs `decision_date`. This module does not attempt
  any half-implementation of that follow-up work.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "alpha_shadow_ledger_contract.json"
ALPHA_REVIEW_PATH = ROOT / "decision" / "alpha_review.py"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/=-]{0,127}$")


def _load_alpha_review():
    spec = importlib.util.spec_from_file_location("p10_07_alpha_review", ALPHA_REVIEW_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("ALPHA_REVIEW_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ALPHA_REVIEW = _load_alpha_review()


class AlphaShadowLedgerError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AlphaShadowLedgerError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "alpha_shadow_ledger/2",
        "output_schema_version": "alpha_shadow_ledger_record/1",
        "actions": ["SHADOW_ENTRY_REVIEW", "WAIT", "REJECT"],
        # ★ CIO Gate Hardening (contract_version alpha_shadow_ledger/2): this
        #   table is the BASE mapping only. A base "SHADOW_ENTRY_REVIEW" is
        #   further gated on p5_rule_status=="PASS" by
        #   action_for_opportunity_state() below -- it downgrades to "WAIT"
        #   whenever p5_rule_status != "PASS". Every other base action
        #   (REJECT/WAIT) is p5-independent. See that function and the
        #   module docstring for the full rule.
        "opportunity_state_to_action": {
            "BLOCKED": "REJECT",
            "REJECTED": "REJECT",
            "ANTICIPATORY_REVIEW": "SHADOW_ENTRY_REVIEW",
            "EARLY_DISCOVERY": "SHADOW_ENTRY_REVIEW",
            "CONFIRMATION_REVIEW": "SHADOW_ENTRY_REVIEW",
            "WAIT_FOR_PULLBACK": "WAIT",
            "WAIT_FOR_EVIDENCE": "WAIT",
            "EXPECTATION_EXHAUSTED": "WAIT",
            "WAIT_FOR_PRICE": "WAIT",
            "WAIT_FOR_THESIS_REPAIR": "WAIT",
        },
        "authority": {
            "append_only_alpha_observation": True,
            "stage_change_authorized": False,
            "shadow_eligibility_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    if not isinstance(value, dict) or value != _expected_contract():
        raise AlphaShadowLedgerError("CONTRACT_IDENTITY_INVALID")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read(path))


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise AlphaShadowLedgerError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise AlphaShadowLedgerError(code)
    return value


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise AlphaShadowLedgerError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise AlphaShadowLedgerError(code) from exc
    if parsed.isoformat() != value:
        raise AlphaShadowLedgerError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise AlphaShadowLedgerError(code)
    return value


def action_for_opportunity_state(opportunity_state: str, p5_rule_status: str, contract: dict) -> str:
    """`SHADOW_ENTRY_REVIEW` may only ever be returned when BOTH the base
    table maps `opportunity_state` to `SHADOW_ENTRY_REVIEW` (i.e.
    `opportunity_state` is one of the three entry-eligible states) AND
    `p5_rule_status == "PASS"`. Otherwise a base `SHADOW_ENTRY_REVIEW` is
    downgraded to `WAIT` -- never silently dropped, never an error. Every
    other base action (`REJECT`/`WAIT`) is untouched by `p5_rule_status`.
    """
    mapping = contract["opportunity_state_to_action"]
    if opportunity_state not in mapping:
        raise AlphaShadowLedgerError(f"OPPORTUNITY_STATE_UNMAPPED:{opportunity_state}")
    base_action = mapping[opportunity_state]
    if base_action == "SHADOW_ENTRY_REVIEW" and p5_rule_status != "PASS":
        return "WAIT"
    return base_action


def _shadow_proposal(alpha_review_packet: dict, contract: dict) -> dict:
    action = action_for_opportunity_state(
        alpha_review_packet["opportunity_state"], alpha_review_packet["p5_rule_status"], contract
    )
    return {
        # ⛔ HARD-CODED. No parameter in build_record() can ever change this --
        #   see test_alpha_shadow_ledger.py's capital-always-zero regression,
        #   which also inspects build_record's live signature.
        "capital": 0,
        "action": action,
        "hypothetical_entry_condition": "; ".join(alpha_review_packet["entry_conditions"]),
        "hypothetical_invalidation": "; ".join(alpha_review_packet["invalidation_conditions"]),
        "hypothetical_add_condition": "; ".join(alpha_review_packet["add_conditions"]),
        # "exit" is mapped from Alpha Review's reduce_conditions -- invalidation
        # already carries the definitive thesis-broken exit trigger, so this
        # column captures the partial-trim/reduce signal instead of duplicating it.
        "hypothetical_exit_condition": "; ".join(alpha_review_packet["reduce_conditions"]),
        # expiry is the Alpha Review packet's own next_review_date, which is
        # already validated to be >= decision_date -- never independently
        # re-derived here.
        "expiry": alpha_review_packet["next_review_date"],
        # ⛔ HARD-CODED. See capital note above -- same regression coverage.
        "human_approval_required": True,
    }


def build_record(
    alpha_review_packet: dict,
    recorded_at: str,
    sequence: int,
    previous_entry_hash: str | None = None,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = ALPHA_REVIEW.validate_packet(alpha_review_packet)
    _utc(recorded_at, "RECORDED_AT_INVALID")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise AlphaShadowLedgerError("SEQUENCE_INVALID")
    if sequence == 1:
        if previous_entry_hash is not None:
            raise AlphaShadowLedgerError("GENESIS_PREVIOUS_HASH_MUST_BE_NULL")
    else:
        _sha(previous_entry_hash, "PREVIOUS_ENTRY_HASH_INVALID")

    record = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "sequence": sequence,
        "recorded_at": recorded_at,
        "signal_date": checked["decision_date"],
        "subject": checked["subject"],
        "alpha_review_packet_sha256": checked["packet_sha256"],
        "shadow_proposal": _shadow_proposal(checked, contract),
        "previous_entry_hash": previous_entry_hash,
        "authority": copy.deepcopy(contract["authority"]),
    }
    record["entry_hash"] = payload_sha256(record)
    return validate_record(record, contract)


def validate_record(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "sequence", "recorded_at", "signal_date",
        "subject", "alpha_review_packet_sha256", "shadow_proposal", "previous_entry_hash",
        "authority", "entry_hash",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise AlphaShadowLedgerError("RECORD_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["output_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["authority"]
    ):
        raise AlphaShadowLedgerError("RECORD_IDENTITY_INVALID")

    sequence = value.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise AlphaShadowLedgerError("RECORD_SEQUENCE_INVALID")
    _utc(value.get("recorded_at"), "RECORD_RECORDED_AT_INVALID")
    _date(value.get("signal_date"), "RECORD_SIGNAL_DATE_INVALID")
    _token(value.get("subject"), "RECORD_SUBJECT_INVALID")
    _sha(value.get("alpha_review_packet_sha256"), "RECORD_ALPHA_REVIEW_SHA_INVALID")

    if sequence == 1 and value.get("previous_entry_hash") is not None:
        raise AlphaShadowLedgerError("GENESIS_PREVIOUS_HASH_MUST_BE_NULL")
    if sequence > 1:
        _sha(value.get("previous_entry_hash"), "PREVIOUS_ENTRY_HASH_INVALID")

    proposal = value.get("shadow_proposal")
    proposal_fields = {
        "capital", "action", "hypothetical_entry_condition", "hypothetical_invalidation",
        "hypothetical_add_condition", "hypothetical_exit_condition", "expiry",
        "human_approval_required",
    }
    if not isinstance(proposal, dict) or set(proposal) != proposal_fields:
        raise AlphaShadowLedgerError("SHADOW_PROPOSAL_FIELDS_MISMATCH")
    if proposal.get("capital") != 0 or isinstance(proposal.get("capital"), bool):
        raise AlphaShadowLedgerError("SHADOW_PROPOSAL_CAPITAL_MUST_BE_ZERO")
    if proposal.get("human_approval_required") is not True:
        raise AlphaShadowLedgerError("SHADOW_PROPOSAL_HUMAN_APPROVAL_MUST_BE_TRUE")
    if proposal.get("action") not in contract["actions"]:
        raise AlphaShadowLedgerError("SHADOW_PROPOSAL_ACTION_INVALID")
    for key in (
        "hypothetical_entry_condition", "hypothetical_invalidation",
        "hypothetical_add_condition", "hypothetical_exit_condition",
    ):
        if not isinstance(proposal.get(key), str):
            raise AlphaShadowLedgerError(f"SHADOW_PROPOSAL_{key.upper()}_INVALID")
    expiry = _date(proposal.get("expiry"), "SHADOW_PROPOSAL_EXPIRY_INVALID")
    signal_date = _date(value.get("signal_date"), "RECORD_SIGNAL_DATE_INVALID")
    if expiry < signal_date:
        raise AlphaShadowLedgerError("SHADOW_PROPOSAL_EXPIRY_BEFORE_SIGNAL_DATE")

    digest = _sha(value.get("entry_hash"), "ENTRY_HASH_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("entry_hash")
    if payload_sha256(payload) != digest:
        raise AlphaShadowLedgerError("ENTRY_HASH_MISMATCH")
    return copy.deepcopy(value)
