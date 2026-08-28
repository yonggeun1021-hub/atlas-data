#!/usr/bin/env python3
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio_risk.kis_valuation_freshness_policy_proposal import (
    AUTHORITY_ALL_FALSE,
    MAX_PAIR_GAP_SECONDS,
    MAX_SOURCE_AGE_SECONDS,
    PRIVATE_PRECEDENT_COMMIT,
    PRIVATE_PRECEDENT_PATH,
    PRIVATE_PRECEDENT_SHA256,
    PROPOSAL_STATUS,
    TARGET_CONTRACT_VERSION,
    diagnose_proposed_freshness,
    freshness_policy_proposal,
    payload_sha256,
)
from portfolio_risk.kis_valuation_freshness_policy_review import (
    KisValuationFreshnessPolicyReviewError,
    review_freshness_policy_proposal,
)


PRECEDENT_SOURCE = b'''\
from dataclasses import dataclass\n\n
@dataclass(frozen=True)\n
class KisPaperOrderConfig:\n
    confirmation_ttl_seconds: int = 120\n
    max_decision_age_seconds: int = 300\n\n
    @classmethod\n
    def from_env(cls, values):\n
        confirmation_ttl = int(values.get("ATLAS_PAPER_CONFIRMATION_TTL_SECONDS", "120"))\n
        max_decision_age = int(values.get("ATLAS_PAPER_MAX_DECISION_AGE_SECONDS", "300"))\n
        if not 30 <= confirmation_ttl <= 300:\n
            raise ValueError\n
        if not 30 <= max_decision_age <= 900:\n
            raise ValueError\n
        return cls(confirmation_ttl_seconds=confirmation_ttl, max_decision_age_seconds=max_decision_age)\n\n
class Gateway:\n
    def _validate(self, now, decision_time):\n
        age = (now - decision_time).total_seconds()\n
        if age > self.config.max_decision_age_seconds:\n
            raise ValueError\n
'''


def _rehash(value: dict) -> dict:
    value["proposalSha256"] = payload_sha256({
        key: item for key, item in value.items() if key != "proposalSha256"
    })
    return value


class ProposalShapeTests(unittest.TestCase):
    def test_exact_candidate_is_proposal_only_and_all_false(self):
        packet = freshness_policy_proposal()
        self.assertEqual(packet["proposalStatus"], PROPOSAL_STATUS)
        self.assertEqual(packet["targetContractVersion"], TARGET_CONTRACT_VERSION)
        self.assertEqual(packet["candidatePolicy"]["maxSourceAgeSeconds"], 300)
        self.assertEqual(packet["candidatePolicy"]["maxPairGapSeconds"], 120)
        self.assertFalse(packet["candidatePolicy"]["callerOverridePermitted"])
        self.assertEqual(packet["authority"], AUTHORITY_ALL_FALSE)
        self.assertFalse(packet["canonicalAuthorityConfigMutated"])
        self.assertFalse(packet["existingPortfolioAccountFactV2Mutated"])
        self.assertFalse(packet["valuationSemanticProposalMutated"])

    def test_operating_precedent_is_exact_but_not_policy_authority(self):
        precedent = freshness_policy_proposal()["operationalPrecedent"]
        self.assertEqual(
            precedent["status"],
            "OPERATING_PRECEDENT_NOT_VALUATION_POLICY_EVIDENCE",
        )
        self.assertEqual(precedent["commitSha"], PRIVATE_PRECEDENT_COMMIT)
        self.assertEqual(precedent["filePath"], PRIVATE_PRECEDENT_PATH)
        self.assertEqual(precedent["contentSha256"], PRIVATE_PRECEDENT_SHA256)
        rationale = freshness_policy_proposal()["selectionRationale"]
        self.assertEqual(rationale["evidenceStatus"], "UNVALIDATED_NO_LIVE_PAIR_SAMPLE")
        self.assertEqual(rationale["livePairSampleCount"], 0)
        self.assertFalse(rationale["atomicCaptureSessionBindingPresent"])


class DiagnosticBoundaryTests(unittest.TestCase):
    def diagnose(self, relationship: str, capacity: str, review: str):
        return diagnose_proposed_freshness(
            relationship_available_at=relationship,
            buy_capacity_available_at=capacity,
            review_as_of=review,
        )

    def test_exact_300_second_age_and_120_second_gap_are_inclusive(self):
        result = self.diagnose(
            "2026-08-28T22:55:00Z", "2026-08-28T22:57:00Z",
            "2026-08-28T23:00:00Z",
        )
        self.assertEqual(result["relationshipAgeSeconds"], 300)
        self.assertEqual(result["pairGapSeconds"], 120)
        self.assertEqual(result["diagnosticStatus"], "DIAGNOSTIC_WITHIN_PROPOSED_WINDOW")
        self.assertFalse(result["policyAuthorityPresent"])
        self.assertEqual(result["authority"], AUTHORITY_ALL_FALSE)

    def test_301_second_source_age_is_outside(self):
        result = self.diagnose(
            "2026-08-28T22:54:59Z", "2026-08-28T22:54:59Z",
            "2026-08-28T23:00:00Z",
        )
        self.assertEqual(result["relationshipAgeSeconds"], 301)
        self.assertEqual(result["diagnosticStatus"], "DIAGNOSTIC_OUTSIDE_PROPOSED_WINDOW")

    def test_121_second_pair_gap_is_outside_with_both_sources_under_300_seconds(self):
        result = self.diagnose(
            "2026-08-28T22:56:59Z", "2026-08-28T22:59:00Z",
            "2026-08-28T23:00:00Z",
        )
        self.assertLessEqual(result["relationshipAgeSeconds"], 300)
        self.assertLessEqual(result["buyCapacityAgeSeconds"], 300)
        self.assertEqual(result["pairGapSeconds"], 121)
        self.assertEqual(result["diagnosticStatus"], "DIAGNOSTIC_OUTSIDE_PROPOSED_WINDOW")

    def test_future_source_is_outside_not_fresh_by_negative_age(self):
        result = self.diagnose(
            "2026-08-28T23:00:01Z", "2026-08-28T23:00:00Z",
            "2026-08-28T23:00:00Z",
        )
        self.assertFalse(result["pitSafe"])
        self.assertEqual(result["diagnosticStatus"], "DIAGNOSTIC_OUTSIDE_PROPOSED_WINDOW")


class ReviewTests(unittest.TestCase):
    def review(self, packet=None, source=PRECEDENT_SOURCE):
        with mock.patch(
            "portfolio_risk.kis_valuation_freshness_policy_review._resolve_private_precedent",
            return_value=(source, []),
        ):
            return review_freshness_policy_proposal(
                packet or freshness_policy_proposal(),
                private_checkout=Path("/independent/private-checkout"),
            )

    def test_exact_packet_stays_incomplete_despite_semantically_parsed_precedent(self):
        result = self.review()
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertEqual(result["reasons"], [
            "VALUATION_PAIR_GAP_EVIDENCE_UNVALIDATED_NO_LIVE_PAIR_SAMPLE",
        ])
        self.assertEqual(result["authority"], AUTHORITY_ALL_FALSE)

    def test_missing_private_exact_commit_reproduction_is_incomplete(self):
        result = review_freshness_policy_proposal(freshness_policy_proposal())
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn(
            "PRIVATE_OPERATING_PRECEDENT_REPRODUCTION_REQUIRED", result["reasons"]
        )
        self.assertIn(
            "VALUATION_PAIR_GAP_EVIDENCE_UNVALIDATED_NO_LIVE_PAIR_SAMPLE",
            result["reasons"],
        )

    def test_self_rehashed_fake_live_sample_cannot_clear_evidence_blocker(self):
        packet = copy.deepcopy(freshness_policy_proposal())
        packet["selectionRationale"]["evidenceStatus"] = "VALIDATED"
        packet["selectionRationale"]["livePairSampleCount"] = 99
        packet["selectionRationale"]["atomicCaptureSessionBindingPresent"] = True
        packet["reviewReadiness"]["status"] = "REVIEW_READY_FOR_CIO"
        packet["reviewReadiness"]["blockingReason"] = None
        _rehash(packet)
        result = self.review(packet)
        self.assertIn("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT", result["reasons"])
        self.assertIn("SELECTION_RATIONALE_BOUNDARY_INVALID", result["reasons"])
        self.assertIn("REVIEW_READINESS_BOUNDARY_INVALID", result["reasons"])
        self.assertIn(
            "VALUATION_PAIR_GAP_EVIDENCE_UNVALIDATED_NO_LIVE_PAIR_SAMPLE",
            result["reasons"],
        )

    def test_self_rehashed_threshold_change_cannot_redefine_policy(self):
        packet = copy.deepcopy(freshness_policy_proposal())
        packet["candidatePolicy"]["maxSourceAgeSeconds"] = 86_400
        _rehash(packet)
        result = self.review(packet)
        self.assertIn("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT", result["reasons"])
        self.assertIn("MAX_SOURCE_AGE_NOT_EXACT_PROPOSAL", result["reasons"])

    def test_caller_override_or_retroactive_application_is_rejected(self):
        packet = copy.deepcopy(freshness_policy_proposal())
        packet["candidatePolicy"]["callerOverridePermitted"] = True
        packet["applicability"]["retroactiveApplicationPermitted"] = True
        _rehash(packet)
        result = self.review(packet)
        self.assertIn("CALLER_OVERRIDE_FORBIDDEN", result["reasons"])
        self.assertIn("APPLICABILITY_BOUNDARY_INVALID", result["reasons"])

    def test_numeric_bool_authority_alias_does_not_pass(self):
        packet = copy.deepcopy(freshness_policy_proposal())
        packet["authority"] = {key: int(value) for key, value in packet["authority"].items()}
        _rehash(packet)
        result = self.review(packet)
        self.assertIn("AUTHORITY_NOT_ALL_FALSE", result["reasons"])

    def test_embedded_ratification_is_hard_rejected(self):
        packet = copy.deepcopy(freshness_policy_proposal())
        packet["candidatePolicy"]["approvalStatus"] = "RATIFIED"
        _rehash(packet)
        with self.assertRaisesRegex(
            KisValuationFreshnessPolicyReviewError,
            "EMBEDDED_AUTHORITY_FIELD_FORBIDDEN",
        ):
            self.review(packet)

    def test_hash_matching_source_with_wrong_semantics_is_incomplete(self):
        source = PRECEDENT_SOURCE.replace(b"= 300", b"= 301", 1)
        result = self.review(source=source)
        self.assertIn(
            "PRIVATE_PRECEDENT_DECISION_AGE_DEFAULT_NOT_REPRODUCED",
            result["reasons"],
        )

    def test_strictly_greater_stale_boundary_must_be_reproduced(self):
        source = PRECEDENT_SOURCE.replace(
            b"age > self.config.max_decision_age_seconds",
            b"age >= self.config.max_decision_age_seconds",
        )
        result = self.review(source=source)
        self.assertIn(
            "PRIVATE_PRECEDENT_INCLUSIVE_BOUNDARY_NOT_REPRODUCED",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
