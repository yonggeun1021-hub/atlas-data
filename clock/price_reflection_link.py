#!/usr/bin/env python3
"""P8-12 <-> P8-10 integration (CIO's locked integration spec, 2026-08-23,
post PR #212 merge `4802dad`).

Connects two already-approved contracts PIT-safely: P8-10's real
`decision/price_reflection.py` (`price_reflection/6`, real `price_state`
momentum read, `reflection_status` structurally locked to `"UNKNOWN"`) and
P8-12's Dynamic Clock. Reuses `decision/price_evidence.py`'s
`assemble_price_evidence()` and `decision/price_reflection.py`'s
`build_packet()`/`validate_packet()` UNCHANGED (dynamically loaded via
`importlib`, mirroring `decision/pilot_evidence_intake.py`'s own
established call pattern -- not reimplemented).

★ Subject coverage (honest, not guessed): `decision/price_evidence.py`
  only has real evidence assembly for BTC (`CRYPTO_SUBJECT_ALIASES`) and
  KRX Korea codes (`_krx_code_from_subject`) -- crypto breadth altcoins
  (e.g. "AAVE/USD") have no price-evidence source in this repo at all.
  `price_reflection_supported()` below reflects that boundary exactly:
  BTC and KOREA-market subjects are linked for real; every CRYPTO-market
  (non-BTC) subject gets an honest `NOT_SUPPORTED_FOR_SUBJECT` status,
  never a fabricated or misleadingly-labeled attempt through the wrong
  evidence path (calling `assemble_price_evidence` on e.g. "AAVE/USD"
  would silently fall through to the US-equity IEX path and mislabel
  `data_source_scope`, which this module deliberately avoids).

★ Independent re-validation, never a trusted bare dict (integration spec
  item 3.7): `verify_and_extract()` re-runs
  `decision.price_reflection.validate_packet()` on every packet before
  reading a single field out of it, cross-checks `subject`/`decision_date`
  against what was actually requested, and re-asserts
  `reflection_status == "UNKNOWN"` independently of that validator's own
  enforcement (defense in depth against a hypothetical future edit to
  either module). ANY failure raises `PriceReflectionLinkError` --
  `link_price_reflection()` catches this per-subject and reports that ONE
  candidate as `LINK_FAILED` (item 3.8) rather than letting an exception
  propagate and take down the whole Dynamic Clock run.

★ Field allowlist (integration spec item 3): only `subject`,
  `decision_date`, `price_state`, `reflection_status`, `data_state`,
  `threshold_basis`, `price_as_of`, `contract_version`/`packet_sha256`
  (price-evidence lineage), and `reasons` ever flow out of this module --
  never `relative_strength`/`recent_return_windows`/`confidence`/the inert
  `event_reaction`/`reflection_reference` sub-objects.

★ Determinism: `generated_at` passed to `build_packet()` is derived purely
  from `decision_date` (`f"{decision_date}T23:59:59Z"`), never
  `datetime.now()` -- so re-running with the same `(subject, decision_date)`
  always produces byte-identical output, matching
  `decision/pilot_evidence_intake.py`'s own fixed-`generated_at` pattern
  for the same reason.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PriceReflectionLinkError(ValueError):
    pass


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PriceReflectionLinkError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PRICE_EVIDENCE = None
_PRICE_REFLECTION = None


def _price_evidence():
    global _PRICE_EVIDENCE
    if _PRICE_EVIDENCE is None:
        _PRICE_EVIDENCE = _load_module("p8_12_price_evidence", "decision/price_evidence.py")
    return _PRICE_EVIDENCE


def _price_reflection():
    global _PRICE_REFLECTION
    if _PRICE_REFLECTION is None:
        _PRICE_REFLECTION = _load_module("p8_12_price_reflection", "decision/price_reflection.py")
    return _PRICE_REFLECTION


ALLOWED_FIELDS = frozenset({
    "status", "subject", "decision_date", "price_state", "reflection_status",
    "data_state", "threshold_basis", "price_as_of", "reasons",
    "contract_version", "packet_sha256",
})


def price_reflection_supported(subject: str, market: str) -> bool:
    """True only where `decision/price_evidence.py` has a REAL evidence
    source for `subject` -- BTC and KRX Korea codes. CRYPTO-market
    (non-BTC) subjects are honestly unsupported; see module docstring."""
    if market == "BTC":
        return subject == "BTC"
    if market == "KOREA":
        return subject.isdigit() and len(subject) == 6
    return False


def _deterministic_generated_at(decision_date: str) -> str:
    return f"{decision_date}T23:59:59Z"


def verify_and_extract(packet: dict, expected_subject: str, expected_decision_date: str,
                        contract: dict | None = None) -> dict:
    """Independently re-validates `packet` -- NEVER trusts a bare injected
    dict (integration spec item 3.7) -- and extracts ONLY the allowlisted
    fields (item 3). Raises `PriceReflectionLinkError` on any validation
    failure, subject/decision_date mismatch, or non-`UNKNOWN`
    `reflection_status` (defense-in-depth re-assertion, independent of
    `decision.price_reflection.validate_packet()`'s own enforcement)."""
    price_reflection = _price_reflection()
    try:
        validated = price_reflection.validate_packet(packet, contract)
    except price_reflection.PriceReflectionError as exc:
        raise PriceReflectionLinkError(f"PACKET_VALIDATION_FAILED:{exc}") from exc

    if validated.get("subject") != expected_subject:
        raise PriceReflectionLinkError(
            f"SUBJECT_MISMATCH:expected={expected_subject}:got={validated.get('subject')}"
        )
    if validated.get("decision_date") != expected_decision_date:
        raise PriceReflectionLinkError(
            f"DECISION_DATE_MISMATCH:expected={expected_decision_date}:got={validated.get('decision_date')}"
        )

    pr = validated.get("price_reflection")
    if not isinstance(pr, dict):
        raise PriceReflectionLinkError("PRICE_REFLECTION_SUBOBJECT_MISSING")

    reflection_status = pr.get("reflection_status")
    if reflection_status != "UNKNOWN":
        # ★ Integration spec invariant 2: independent re-assertion, even
        #   though validate_packet() above already enforces this -- this
        #   module must never rely SOLELY on the upstream validator.
        raise PriceReflectionLinkError(
            f"REFLECTION_STATUS_NOT_UNKNOWN:{reflection_status}"
        )

    return {
        "status": "LINKED",
        "subject": validated["subject"],
        "decision_date": validated["decision_date"],
        "price_state": pr["price_state"],
        "reflection_status": reflection_status,
        "data_state": pr["data_state"],
        "threshold_basis": pr["threshold_basis"],
        "price_as_of": pr["price_as_of"],
        "reasons": list(pr["reasons"]),
        "contract_version": validated["contract_version"],
        "packet_sha256": validated["packet_sha256"],
    }


def link_price_reflection(subject: str, market: str, decision_date: str) -> dict:
    """Real, end-to-end P8-10 link for one subject, fail-closed per
    candidate (item 3.8) -- never raises; always returns a dict with at
    least a `status` key:

      - `NOT_SUPPORTED_FOR_SUBJECT` -- no real P8-10 evidence source exists
        for this subject/market (e.g. a crypto altcoin).
      - `LINK_FAILED` -- assembly, build, or independent re-validation
        raised for a genuine reason (`error` carries the code); this
        candidate's price_reflection is honestly unusable, not silently
        defaulted to something that looks fine.
      - `LINKED` -- real, independently-verified fields (see
        `verify_and_extract`).
    """
    if not price_reflection_supported(subject, market):
        return {"status": "NOT_SUPPORTED_FOR_SUBJECT", "subject": subject, "market": market}

    try:
        price_evidence = _price_evidence()
        price_reflection = _price_reflection()
        contract = price_reflection.load_contract()
        generated_at = _deterministic_generated_at(decision_date)
        evidence = price_evidence.assemble_price_evidence(subject, decision_date)
        packet = price_reflection.build_packet(
            subject=subject, decision_date=decision_date, generated_at=generated_at,
            contract=contract, **evidence,
        )
        return verify_and_extract(packet, subject, decision_date, contract)
    except Exception as exc:  # noqa: BLE001 -- fail-closed per candidate, never crash the whole run
        return {
            "status": "LINK_FAILED",
            "subject": subject,
            "market": market,
            "error": f"{type(exc).__name__}:{exc}",
        }


def to_price_reflection_status(link_result: dict) -> dict:
    """Turns a `link_price_reflection()` result into the
    `price_reflection_status` sub-dict `clock/review_candidate.py` attaches
    to a subject candidate -- the same `{"status": ..., "reason": ...}`
    shape `thesis_linkage` already uses, so both linkage channels are
    structurally uniform in `compute_tier()`."""
    status = link_result.get("status")
    if status == "LINKED":
        result = {k: v for k, v in link_result.items() if k in ALLOWED_FIELDS}
        return result
    if status == "NOT_SUPPORTED_FOR_SUBJECT":
        return {
            "status": "NOT_LINKED_THIS_SLICE",
            "reason": (
                "decision/price_evidence.py has no real price-evidence source for this subject "
                "(only BTC and KRX Korea codes are supported today)"
            ),
        }
    if status == "LINK_FAILED":
        return {
            "status": "NOT_LINKED_THIS_SLICE",
            "reason": f"price_reflection link failed closed for this candidate: {link_result.get('error')}",
        }
    raise PriceReflectionLinkError(f"UNKNOWN_LINK_STATUS:{status}")
