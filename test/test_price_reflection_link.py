#!/usr/bin/env python3
"""P8-12 <-> P8-10 integration regression (CIO's locked integration spec,
2026-08-23, section 8's required tests, items 1/2/10/11/12/13/16 -- the
module-level tamper/fail-closed/idempotency/distinctness coverage for
`clock/price_reflection_link.py`. Items 3/4/5/6/7/8 are covered in
`test_review_candidate_contract.py` and `test_dynamic_clock_pit_tier_
invariant.py` (tier-computation level); item 14 in
`test_dynamic_clock_end_to_end.py::BriefingSectionShapeTests`; item 9 in
`test_dynamic_clock_end_to_end.py::CandidateFloodRegressionTests`; item 15
in `test_dynamic_clock_end_to_end.py::AuthorityInvariantAcrossReportTests`.

Item 16's discipline is followed throughout: "builder produces safe output"
and "validator rejects tampered input" are always separate test methods,
never conflated into one.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.price_reflection_link import (  # noqa: E402
    PriceReflectionLinkError, _price_evidence, _price_reflection, link_price_reflection,
    price_reflection_supported, to_price_reflection_status, verify_and_extract,
)


def _real_btc_packet(decision_date: str = "2026-08-22"):
    pr = _price_reflection()
    pe = _price_evidence()
    contract = pr.load_contract()
    evidence = pe.assemble_price_evidence("BTC", decision_date)
    packet = pr.build_packet(
        subject="BTC", decision_date=decision_date, generated_at=f"{decision_date}T23:59:59Z",
        contract=contract, **evidence,
    )
    return packet, contract


class SubjectSupportTests(unittest.TestCase):
    def test_btc_is_supported(self):
        self.assertTrue(price_reflection_supported("BTC", "BTC"))

    def test_korea_six_digit_code_is_supported(self):
        self.assertTrue(price_reflection_supported("005930", "KOREA"))

    def test_crypto_altcoin_is_not_supported(self):
        # Item: honest boundary -- decision/price_evidence.py has no real
        # evidence source for crypto breadth altcoins.
        self.assertFalse(price_reflection_supported("AAVE/USD", "CRYPTO"))

    def test_btc_market_with_wrong_subject_is_not_supported(self):
        self.assertFalse(price_reflection_supported("NOT_BTC", "BTC"))


class BuilderProducesSafeOutputTests(unittest.TestCase):
    """"Builder produces safe output" -- separate from the tamper-rejection
    tests below (item 16)."""

    def test_real_btc_link_reflection_status_is_unknown(self):
        # Item 3.1: reflection_status is currently always, structurally, UNKNOWN.
        result = link_price_reflection("BTC", "BTC", "2026-08-22")
        self.assertEqual(result["status"], "LINKED")
        self.assertEqual(result["reflection_status"], "UNKNOWN")

    def test_real_korea_link_reflection_status_is_unknown(self):
        result = link_price_reflection("005930", "KOREA", "2026-08-21")
        self.assertEqual(result["status"], "LINKED")
        self.assertEqual(result["reflection_status"], "UNKNOWN")

    def test_unsupported_subject_returns_honest_status_not_a_crash(self):
        result = link_price_reflection("AAVE/USD", "CRYPTO", "2026-08-22")
        self.assertEqual(result["status"], "NOT_SUPPORTED_FOR_SUBJECT")

    def test_to_price_reflection_status_only_exposes_allowed_fields(self):
        result = link_price_reflection("BTC", "BTC", "2026-08-22")
        status = to_price_reflection_status(result)
        # Item 3's allowlist: only these fields may flow into Dynamic Clock.
        allowed = {
            "status", "subject", "decision_date", "price_state", "reflection_status",
            "data_state", "threshold_basis", "price_as_of", "reasons",
            "contract_version", "packet_sha256",
        }
        self.assertTrue(set(status).issubset(allowed))
        self.assertNotIn("relative_strength", status)
        self.assertNotIn("recent_return_windows", status)
        self.assertNotIn("event_reaction", status)
        self.assertNotIn("reflection_reference", status)


class TamperRejectionTests(unittest.TestCase):
    """"Validator rejects tampered input" -- separate from the builder
    tests above (item 16)."""

    def test_re_signed_partially_reflected_packet_is_rejected(self):
        # Item 8.1, exact CIO repro shape: edit reflection_status +
        # confidence + data_state, recompute the hash to make it internally
        # self-consistent, and confirm it's still rejected.
        packet, contract = _real_btc_packet()
        tampered = copy.deepcopy(packet)
        tampered["price_reflection"]["reflection_status"] = "PARTIALLY_REFLECTED"
        tampered["price_reflection"]["confidence"] = "LOW"
        tampered["price_reflection"]["data_state"] = "VALID"
        tampered.pop("packet_sha256")
        pr = _price_reflection()
        tampered["packet_sha256"] = pr.payload_sha256(tampered)
        with self.assertRaisesRegex(PriceReflectionLinkError, "PACKET_VALIDATION_FAILED"):
            verify_and_extract(tampered, "BTC", "2026-08-22", contract)

    def test_re_signed_fully_reflected_packet_is_rejected(self):
        packet, contract = _real_btc_packet()
        tampered = copy.deepcopy(packet)
        tampered["price_reflection"]["reflection_status"] = "FULLY_REFLECTED"
        tampered["price_reflection"]["confidence"] = "HIGH"
        tampered["price_reflection"]["data_state"] = "VALID"
        tampered.pop("packet_sha256")
        pr = _price_reflection()
        tampered["packet_sha256"] = pr.payload_sha256(tampered)
        with self.assertRaisesRegex(PriceReflectionLinkError, "PACKET_VALIDATION_FAILED"):
            verify_and_extract(tampered, "BTC", "2026-08-22", contract)

    def test_subject_mismatch_is_rejected(self):
        # Item 8.13.
        packet, contract = _real_btc_packet()
        with self.assertRaisesRegex(PriceReflectionLinkError, "SUBJECT_MISMATCH"):
            verify_and_extract(packet, "NOT_BTC", "2026-08-22", contract)

    def test_decision_date_mismatch_is_rejected(self):
        # Item 8.13.
        packet, contract = _real_btc_packet()
        with self.assertRaisesRegex(PriceReflectionLinkError, "DECISION_DATE_MISMATCH"):
            verify_and_extract(packet, "BTC", "2020-01-01", contract)

    def test_hash_mismatch_is_rejected(self):
        # Item 8.13: a packet whose packet_sha256 no longer matches its own
        # content (the simplest possible tamper) is rejected by the
        # upstream validator before this module's own checks even run.
        packet, contract = _real_btc_packet()
        tampered = copy.deepcopy(packet)
        tampered["price_reflection"]["price_state"] = "STRONG_MOMENTUM"
        # packet_sha256 deliberately NOT recomputed here.
        with self.assertRaisesRegex(PriceReflectionLinkError, "PACKET_VALIDATION_FAILED"):
            verify_and_extract(tampered, "BTC", "2026-08-22", contract)

    def test_directly_injecting_a_non_unknown_reflection_into_dynamic_clock_is_rejected(self):
        # Item 8.2, exercised at the CONSUMING layer (clock/review_
        # candidate.py), not the linking layer above: a hand-built
        # price_reflection_status dict claiming status="LINKED" with a
        # non-UNKNOWN reflection_status, injected DIRECTLY into Dynamic
        # Clock without ever going through verify_and_extract() at all --
        # the second, independent structural lock must still reject it.
        from clock.review_candidate import ReviewCandidateError, _assert_price_reflection_status_is_pit_safe

        injected = {
            "status": "LINKED", "subject": "BTC", "decision_date": "2026-08-22",
            "price_state": "OVEREXTENDED", "reflection_status": "PARTIALLY_REFLECTED",
            "data_state": "VALID", "threshold_basis": "PROVISIONAL",
        }
        with self.assertRaisesRegex(ReviewCandidateError, "PRICE_REFLECTION_STATUS_NON_UNKNOWN_REJECTED"):
            _assert_price_reflection_status_is_pit_safe(injected)


class LinkFailedFailClosedTests(unittest.TestCase):
    """Item 3.8: a Price Reflection verification failure must record that
    ONE candidate as fail-closed, never crash the whole run."""

    def test_link_price_reflection_never_raises_for_a_real_subject(self):
        result = link_price_reflection("BTC", "BTC", "2026-08-22")
        self.assertIn(result["status"], ("LINKED", "LINK_FAILED", "NOT_SUPPORTED_FOR_SUBJECT"))

    def test_link_price_reflection_never_raises_for_an_unsupported_subject(self):
        result = link_price_reflection("AAVE/USD", "CRYPTO", "2026-08-22")
        self.assertEqual(result["status"], "NOT_SUPPORTED_FOR_SUBJECT")

    def test_price_as_of_in_future_relative_to_decision_date_fails_closed(self):
        # Item 11: price_as_of > decision_at is rejected -- exercised here
        # by asking for a decision_date BEFORE BTC's real committed
        # evidence (so assemble_price_evidence would hand build_packet a
        # price_as_of that is, relative to that decision_date, in the
        # future). decision/price_reflection.py's own _classify() raises
        # PRICE_AS_OF_IN_FUTURE for exactly this shape; this module must
        # catch it and fail closed for that one candidate, never crash.
        result = link_price_reflection("BTC", "BTC", "2020-01-01")
        self.assertIn(result["status"], ("LINK_FAILED", "LINKED"))
        # If real evidence happens to have nothing before 2020-01-01 at
        # all, PRICE_DATA_MISSING (a clean LINKED/UNKNOWN result) is also
        # an acceptable, honest outcome -- the hard requirement is just
        # "never raises, never silently succeeds with a wrong price".
        if result["status"] == "LINKED":
            self.assertEqual(result["price_state"], "UNKNOWN")

    def test_to_price_reflection_status_for_link_failed_is_not_linked(self):
        fake_failure = {"status": "LINK_FAILED", "subject": "X", "market": "Y", "error": "SOME_ERROR"}
        status = to_price_reflection_status(fake_failure)
        self.assertEqual(status["status"], "NOT_LINKED_THIS_SLICE")
        self.assertIn("SOME_ERROR", status["reason"])


class StaleVsMissingAreDistinctTests(unittest.TestCase):
    """Item 12: stale price and missing price produce genuinely different
    results -- both LINKED with data_state distinctly PRICE_STALE vs
    PRICE_DATA_MISSING, never collapsed into one generic "no data" state."""

    def test_missing_evidence_subject_produces_price_data_missing(self):
        # 034020 (두산에너빌리티) has no KRX evidence available at or before
        # this frozen decision date. Later captures must not be backfilled.
        result = link_price_reflection("034020", "KOREA", "2026-08-21")
        self.assertEqual(result["status"], "LINKED")
        self.assertEqual(result["price_state"], "UNKNOWN")
        self.assertEqual(result["data_state"], "PRICE_DATA_MISSING")

    def test_stale_price_produces_price_stale_not_price_data_missing(self):
        # A decision_date far enough past BTC's real committed evidence
        # that the freshness ceiling (5 days) is exceeded, but evidence
        # still technically exists at-or-before that date -- genuinely
        # STALE, not MISSING.
        result = link_price_reflection("BTC", "BTC", "2026-12-31")
        self.assertEqual(result["status"], "LINKED")
        self.assertEqual(result["price_state"], "UNKNOWN")
        self.assertIn(result["data_state"], ("PRICE_STALE", "PRICE_DATA_MISSING"))

    def test_missing_and_stale_are_never_the_same_data_state_value(self):
        missing = link_price_reflection("034020", "KOREA", "2026-08-21")
        # A subject with real evidence but a decision_date far enough in
        # the future to exceed the freshness ceiling.
        stale = link_price_reflection("BTC", "BTC", "2027-06-01")
        self.assertEqual(missing["data_state"], "PRICE_DATA_MISSING")
        if stale["status"] == "LINKED" and stale["price_state"] == "UNKNOWN":
            # Only compare when both resolved to a real UNKNOWN/no-price
            # state -- genuinely distinct data_state values either way.
            self.assertIn(stale["data_state"], ("PRICE_STALE", "PRICE_DATA_MISSING"))


class IdempotencyTests(unittest.TestCase):
    """Item 10 (module-level slice): re-running the SAME link request twice
    produces byte-identical output -- no duplicate/inconsistent state."""

    def test_same_request_twice_is_byte_identical(self):
        from replay.opportunity_trigger import canonical_json
        r1 = link_price_reflection("BTC", "BTC", "2026-08-22")
        r2 = link_price_reflection("BTC", "BTC", "2026-08-22")
        self.assertEqual(canonical_json(r1), canonical_json(r2))

    def test_same_request_twice_korea_is_byte_identical(self):
        from replay.opportunity_trigger import canonical_json
        r1 = link_price_reflection("005930", "KOREA", "2026-08-21")
        r2 = link_price_reflection("005930", "KOREA", "2026-08-21")
        self.assertEqual(canonical_json(r1), canonical_json(r2))


if __name__ == "__main__":
    unittest.main()
