#!/usr/bin/env python3
"""P0-2C: KIS provider-authority and source-alias PROPOSED artifacts --
build + independent review, and the CIO-required counter-examples.
Never touches config/data_provider_authority.json or
config/canonical_security_identity.json.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.kis_provenance_proposal import (
    KisProvenanceProposalError,
    all_proposals,
    provider_authority_proposal,
    source_alias_proposal_000660,
    source_alias_proposal_005930,
)
from identity.kis_provenance_proposal_review import (
    KisProvenanceProposalReviewError,
    reject_if_evidence_reused_across_alias_proposals,
    review_provider_authority_proposal,
    review_source_alias_proposal,
)

REAL_PROVIDER_IMPLEMENTATIONS = {
    "KIS_PAPER_ACCOUNT": {
        "account_scope": "KOREA", "currency": "KRW",
        "position_source_name": "kis_paper_domestic_balance",
    },
}


def _real_security_identity():
    return json.loads((ROOT / "config" / "canonical_security_identity.json").read_text())


class BuildProposalShapeTests(unittest.TestCase):
    def test_provider_authority_proposal_is_proposed_unratified_with_authority_all_false(self):
        proposal = provider_authority_proposal()
        self.assertEqual(proposal["proposalStatus"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
        self.assertTrue(all(v is False for k, v in proposal["authority"].items() if k != "review_only"))
        self.assertTrue(proposal["authority"]["review_only"])
        self.assertFalse(proposal["canonicalAuthorityConfigMutated"])
        self.assertGreaterEqual(len(proposal["evidenceLineage"]), 1)

    def test_all_three_proposals_have_independent_proposal_ids(self):
        proposals = all_proposals()
        self.assertEqual(len(proposals), 3)
        ids = {p["proposalId"] for p in proposals}
        self.assertEqual(len(ids), 3)

    def test_unpinned_evidence_reference_is_rejected_at_construction(self):
        from identity.kis_provenance_proposal import _pinned_github_evidence
        with self.assertRaisesRegex(KisProvenanceProposalError, "EVIDENCE_COMMIT_SHA_NOT_PINNED"):
            _pinned_github_evidence(
                repo="koreainvestment/open-trading-api", commit_sha="main",
                file_path="kis_devlp.yaml", content_sha256="a" * 64, note="x",
            )


class ReviewCounterExampleTests(unittest.TestCase):
    """The CIO-required counter-examples, verbatim."""

    def test_review_complete_for_the_real_provider_authority_proposal(self):
        result = review_provider_authority_proposal(
            provider_authority_proposal(), provider_implementations=REAL_PROVIDER_IMPLEMENTATIONS,
        )
        self.assertEqual(result["reviewStatus"], "REVIEW_COMPLETE")

    def test_review_complete_for_both_real_alias_proposals(self):
        identity = _real_security_identity()
        for proposal_fn in (source_alias_proposal_005930, source_alias_proposal_000660):
            with self.subTest(proposal_fn=proposal_fn.__name__):
                result = review_source_alias_proposal(proposal_fn(), security_identity=identity)
                self.assertEqual(result["reviewStatus"], "REVIEW_COMPLETE")

    def test_mutable_unpinned_source_reference_is_review_incomplete(self):
        proposal = provider_authority_proposal()
        tampered = copy.deepcopy(proposal)
        tampered["evidenceLineage"][0]["commitSha"] = "main"
        result = review_provider_authority_proposal(tampered, provider_implementations=REAL_PROVIDER_IMPLEMENTATIONS)
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("EVIDENCE_COMMIT_SHA_NOT_PINNED", " ".join(result["reasons"]))

    def test_official_source_hash_mismatch_is_review_incomplete(self):
        proposal = provider_authority_proposal()
        tampered = copy.deepcopy(proposal)
        tampered["evidenceLineage"][0]["contentSha256"] = "0" * 64
        result = review_provider_authority_proposal(tampered, provider_implementations=REAL_PROVIDER_IMPLEMENTATIONS)
        # The proposal's OWN self-hash no longer matches its (now
        # tampered) content -- this alone forces REVIEW_INCOMPLETE.
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("PROPOSAL_HASH_MISMATCH", result["reasons"])

    def test_proposal_tuple_diverging_from_provider_implementations_is_review_incomplete(self):
        proposal = provider_authority_proposal()
        wrong_registry = {
            "KIS_PAPER_ACCOUNT": {
                "account_scope": "CRYPTO",  # diverges from the real registry
                "currency": "KRW", "position_source_name": "kis_paper_domestic_balance",
            },
        }
        result = review_provider_authority_proposal(proposal, provider_implementations=wrong_registry)
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("CLAIM_ACCOUNT_SCOPE_MISMATCH", result["reasons"])

    def test_alias_target_not_currently_ratified_is_review_incomplete(self):
        proposal = source_alias_proposal_005930()
        identity = _real_security_identity()
        stripped = copy.deepcopy(identity)
        stripped["listings"] = [row for row in stripped["listings"] if row["listing_id"] != "XKRX:005930"]
        result = review_source_alias_proposal(proposal, security_identity=stripped)
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("TARGET_LISTING_NOT_CURRENTLY_RATIFIED", result["reasons"])

    def test_005930_evidence_reused_in_000660_proposal_is_rejected(self):
        proposal_005930 = source_alias_proposal_005930()
        forged_000660 = copy.deepcopy(source_alias_proposal_000660())
        # Splice in 005930's instrument-specific evidence entries.
        instrument_specific = [
            e for e in proposal_005930["evidenceLineage"]
            if e.get("kind") in ("PUBLIC_THIRD_PARTY_CONFIRMATION", "EXISTING_RATIFIED_ATLAS_ALIAS")
        ]
        forged_000660["evidenceLineage"] = forged_000660["evidenceLineage"] + instrument_specific
        with self.assertRaisesRegex(
            KisProvenanceProposalReviewError, "INSTRUMENT_SPECIFIC_EVIDENCE_REUSED_ACROSS_PROPOSALS"
        ):
            reject_if_evidence_reused_across_alias_proposals(proposal_005930, forged_000660)

    def test_shared_general_pdno_shape_evidence_between_the_two_real_aliases_is_not_a_violation(self):
        # kis_domstk.py's general "PDNO is typically 6 digits" citation
        # legitimately appears in BOTH real alias proposals -- proving
        # the reuse check is instrument-specific, not blanket.
        reject_if_evidence_reused_across_alias_proposals(
            source_alias_proposal_005930(), source_alias_proposal_000660(),
        )  # must not raise

    def test_rehashing_a_proposal_never_grants_authority(self):
        from identity.kis_provenance_proposal import payload_sha256
        proposal = provider_authority_proposal()
        tampered = copy.deepcopy(proposal)
        tampered["proposalStatus"] = "RATIFIED"
        tampered["proposalSha256"] = payload_sha256(
            {k: v for k, v in tampered.items() if k != "proposalSha256"}
        )
        with self.assertRaisesRegex(KisProvenanceProposalReviewError, "FORBIDDEN_STATUS_STRING_PRESENT"):
            review_provider_authority_proposal(tampered, provider_implementations=REAL_PROVIDER_IMPLEMENTATIONS)

    def test_broker_verified_as_the_proposal_status_is_rejected(self):
        from identity.kis_provenance_proposal import payload_sha256
        proposal = provider_authority_proposal()
        tampered = copy.deepcopy(proposal)
        tampered["proposalStatus"] = "BROKER_VERIFIED"
        tampered["proposalSha256"] = payload_sha256(
            {k: v for k, v in tampered.items() if k != "proposalSha256"}
        )
        with self.assertRaisesRegex(KisProvenanceProposalReviewError, "FORBIDDEN_STATUS_STRING_PRESENT"):
            review_provider_authority_proposal(tampered, provider_implementations=REAL_PROVIDER_IMPLEMENTATIONS)

    def test_legitimate_narrative_mentioning_ratified_is_never_falsely_rejected(self):
        # claim.assertion / evidence notes routinely and safely say things
        # like "already RATIFIED under krx_open_api_stock_daily" -- proves
        # the forbidden-status check targets specific authority-bearing
        # fields only, never a blind substring scan that would reject
        # every real proposal (PROPOSAL_STATUS itself contains "RATIFIED"
        # as a substring of "UNRATIFIED").
        result = review_source_alias_proposal(source_alias_proposal_005930(), security_identity=_real_security_identity())
        self.assertEqual(result["reviewStatus"], "REVIEW_COMPLETE")

    def test_stage_buy_order_authority_flipped_true_is_rejected_even_rehashed(self):
        from identity.kis_provenance_proposal import payload_sha256
        proposal = provider_authority_proposal()
        tampered = copy.deepcopy(proposal)
        tampered["authority"]["order_authorized"] = True
        tampered["proposalSha256"] = payload_sha256(
            {k: v for k, v in tampered.items() if k != "proposalSha256"}
        )
        with self.assertRaisesRegex(KisProvenanceProposalReviewError, "AUTHORITY_NOT_ALL_FALSE"):
            review_provider_authority_proposal(tampered, provider_implementations=REAL_PROVIDER_IMPLEMENTATIONS)

    def test_canonical_authority_config_mutation_claim_is_rejected(self):
        from identity.kis_provenance_proposal import payload_sha256
        proposal = provider_authority_proposal()
        tampered = copy.deepcopy(proposal)
        tampered["canonicalAuthorityConfigMutated"] = True
        tampered["proposalSha256"] = payload_sha256(
            {k: v for k, v in tampered.items() if k != "proposalSha256"}
        )
        with self.assertRaisesRegex(KisProvenanceProposalReviewError, "CANONICAL_AUTHORITY_CONFIG_MUTATION_CLAIMED"):
            review_provider_authority_proposal(tampered, provider_implementations=REAL_PROVIDER_IMPLEMENTATIONS)

    def test_already_ratified_alias_makes_proposal_redundant_incomplete(self):
        proposal = source_alias_proposal_005930()
        identity = _real_security_identity()
        forged = copy.deepcopy(identity)
        forged["source_aliases"].append({
            **forged["source_aliases"][0],  # reuse a real row's authority fields
            "source_name": "kis_paper_domestic_balance", "source_asset_id": "005930",
            "listing_id": "XKRX:005930",
        })
        result = review_source_alias_proposal(proposal, security_identity=forged)
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("ALIAS_ALREADY_EXISTS_PROPOSAL_REDUNDANT", result["reasons"])


if __name__ == "__main__":
    unittest.main()
