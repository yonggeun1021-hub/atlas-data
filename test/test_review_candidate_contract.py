#!/usr/bin/env python3
"""P8-12 output contract regression: every required field (item 3), the
authority block hard-false invariant (item 5), and round-trip validation."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.dynamic_clock import ClockEvent, build_episode_history  # noqa: E402
from clock.review_candidate import (  # noqa: E402
    AUTHORITY_ALL_FALSE, NOT_LINKED, ReviewCandidateError,
    build_expired_record, build_review_candidate, validate_review_candidate,
)

REQUIRED_FIELDS = {
    "subject", "market", "trigger_type", "detected_at", "evidence_available_at",
    "source", "evidence_hash", "thesis_linkage", "price_reflection_status",
    "urgency", "expiry", "next_review_at", "human_review_required",
}


def _episode(status="ACTIVE"):
    ev = ClockEvent(detected_at="2026-08-20", evidence_available_at="2026-08-19",
                     evidence_hash="a" * 64, source="test/fixture#BTC", strength=1.0)
    episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])
    ep = episodes[0]
    if status == "EXPIRED":
        ep = {**ep, "status": "EXPIRED"}
    return ep


class RequiredFieldsTests(unittest.TestCase):
    def test_every_task_required_field_is_present(self):
        record = build_review_candidate(_episode())
        missing = REQUIRED_FIELDS - set(record)
        self.assertEqual(missing, set(), f"output contract missing required fields: {missing}")

    def test_field_values_match_the_underlying_episode(self):
        record = build_review_candidate(_episode())
        self.assertEqual(record["subject"], "BTC")
        self.assertEqual(record["market"], "BTC")
        self.assertEqual(record["trigger_type"], "PRICE_CONFIRMATION")
        self.assertEqual(record["detected_at"], "2026-08-20")
        self.assertEqual(record["evidence_available_at"], "2026-08-19")
        self.assertEqual(record["source"], "test/fixture#BTC")
        self.assertEqual(record["evidence_hash"], "a" * 64)


class NonCouplingTests(unittest.TestCase):
    """thesis_linkage / price_reflection_status must be present and honest,
    never fabricated -- and must never come from importing decision/*."""

    def test_thesis_linkage_and_price_reflection_are_not_linked_by_design(self):
        record = build_review_candidate(_episode())
        self.assertEqual(record["thesis_linkage"]["status"], NOT_LINKED)
        self.assertEqual(record["price_reflection_status"]["status"], NOT_LINKED)
        self.assertIn("reason", record["thesis_linkage"])
        self.assertIn("reason", record["price_reflection_status"])

    def test_review_candidate_module_never_imports_decision_or_shadow(self):
        source = (ROOT / "clock" / "review_candidate.py").read_text(encoding="utf-8")
        import_lines = [ln.strip() for ln in source.splitlines()
                         if ln.strip().startswith(("import ", "from "))]
        for forbidden in ("import decision", "from decision", "import shadow", "from shadow"):
            self.assertFalse(
                any(ln.startswith(forbidden) for ln in import_lines),
                f"an actual import statement matching {forbidden!r} was found",
            )

    def test_no_clock_module_actually_imports_briefing_daily_orchestrator(self):
        for path in (ROOT / "clock").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            import_lines = [ln.strip() for ln in source.splitlines()
                             if ln.strip().startswith(("import ", "from "))]
            for ln in import_lines:
                self.assertNotIn("daily_orchestrator", ln, path)


class AuthorityHardFalseTests(unittest.TestCase):
    def test_authority_block_is_all_false(self):
        record = build_review_candidate(_episode())
        self.assertEqual(record["authority"], AUTHORITY_ALL_FALSE)
        self.assertIsNone(record["authority"]["trade_proposal"])
        for key, value in record["authority"].items():
            if key in ("trade_proposal",):
                continue
            self.assertIn(value, (False, 0), f"{key} must be False/0, got {value!r}")

    def test_human_review_required_is_always_true_for_active_candidates(self):
        record = build_review_candidate(_episode())
        self.assertTrue(record["human_review_required"])

    def test_expired_record_sets_human_review_required_false(self):
        record = build_expired_record(_episode(status="EXPIRED"))
        self.assertFalse(record["human_review_required"])
        self.assertEqual(record["authority"], AUTHORITY_ALL_FALSE)


class ValidationRoundTripTests(unittest.TestCase):
    def test_valid_record_round_trips(self):
        record = build_review_candidate(_episode())
        revalidated = validate_review_candidate(record)
        self.assertEqual(revalidated, record)

    def test_tampered_authority_block_rejected(self):
        record = build_review_candidate(_episode())
        tampered = {**record, "authority": {**record["authority"], "trading_authority": True}}
        with self.assertRaisesRegex(ReviewCandidateError, "AUTHORITY_BLOCK_TAMPERED"):
            validate_review_candidate(tampered)

    def test_tampered_human_review_required_rejected(self):
        record = build_review_candidate(_episode())
        tampered = {**record, "human_review_required": False}
        with self.assertRaisesRegex(ReviewCandidateError, "HUMAN_REVIEW_REQUIRED_MUST_BE_TRUE"):
            validate_review_candidate(tampered)

    def test_tampered_field_without_rehash_rejected(self):
        record = build_review_candidate(_episode())
        tampered = {**record, "urgency": "LOW"}
        with self.assertRaisesRegex(ReviewCandidateError, "RECORD_HASH_MISMATCH"):
            validate_review_candidate(tampered)


class StateGuardTests(unittest.TestCase):
    def test_build_review_candidate_rejects_expired_episode(self):
        with self.assertRaisesRegex(ReviewCandidateError, "EPISODE_NOT_ACTIVE"):
            build_review_candidate(_episode(status="EXPIRED"))

    def test_build_expired_record_rejects_active_episode(self):
        with self.assertRaisesRegex(ReviewCandidateError, "EPISODE_NOT_EXPIRED"):
            build_expired_record(_episode(status="ACTIVE"))


class DeterminismTests(unittest.TestCase):
    def test_record_hash_is_deterministic(self):
        r1 = build_review_candidate(_episode())
        r2 = build_review_candidate(_episode())
        self.assertEqual(r1["record_hash"], r2["record_hash"])
        self.assertEqual(r1["candidate_id"], r2["candidate_id"])


if __name__ == "__main__":
    unittest.main()
