#!/usr/bin/env python3
"""P0-2C KIS proposal packets: fail-closed review counterexamples."""
from __future__ import annotations

import copy
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.kis_provenance_proposal import (
    KisProvenanceProposalError,
    all_proposals,
    payload_sha256,
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


def _rehash(proposal: dict) -> None:
    proposal["proposalSha256"] = payload_sha256(
        {key: value for key, value in proposal.items() if key != "proposalSha256"}
    )


class BuildProposalShapeTests(unittest.TestCase):
    def test_all_three_are_v2_proposed_unratified_and_authority_false(self):
        proposals = all_proposals()
        self.assertEqual(len(proposals), 3)
        self.assertEqual(len({proposal["proposalId"] for proposal in proposals}), 3)
        for proposal in proposals:
            self.assertEqual(proposal["schemaVersion"], "kis_provenance_proposal/2")
            self.assertEqual(proposal["proposalStatus"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
            self.assertEqual(proposal["reviewAsOf"], "2026-08-28T00:00:00Z")
            self.assertTrue(proposal["authority"]["review_only"])
            self.assertTrue(all(
                value is False for key, value in proposal["authority"].items() if key != "review_only"
            ))
            self.assertFalse(proposal["canonicalAuthorityConfigMutated"])

    def test_unpinned_official_evidence_rejected_at_construction(self):
        from identity.kis_provenance_proposal import _pinned_github_evidence
        with self.assertRaisesRegex(KisProvenanceProposalError, "EVIDENCE_COMMIT_SHA_NOT_PINNED"):
            _pinned_github_evidence(
                repo="koreainvestment/open-trading-api", commit_sha="main",
                file_path="kis_devlp.yaml", content_sha256="a" * 64, note="x",
            )

    def test_no_authority_config_rows_are_added(self):
        providers = json.loads((ROOT / "config" / "data_provider_authority.json").read_text())
        identities = json.loads((ROOT / "config" / "canonical_security_identity.json").read_text())
        self.assertEqual(providers["provider_authority_records"], [])
        self.assertFalse(any(
            row.get("source_name") == "kis_paper_domestic_balance"
            for row in identities["source_aliases"]
        ))


class FailClosedReviewTests(unittest.TestCase):
    def test_real_provider_packet_is_incomplete_until_external_bytes_reproduced(self):
        result = review_provider_authority_proposal(provider_authority_proposal())
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertTrue(any(
            reason.startswith("EXTERNAL_SOURCE_BYTES_REPRODUCTION_REQUIRED:")
            for reason in result["reasons"]
        ))
        self.assertNotIn("PROVIDER_NOT_IN_CURRENT_IMPLEMENTATION_REGISTRY", result["reasons"])

    def test_real_alias_packets_are_incomplete_but_target_resolves_via_real_atlas_authority(self):
        for proposal_fn in (source_alias_proposal_005930, source_alias_proposal_000660):
            with self.subTest(proposal=proposal_fn.__name__):
                result = review_source_alias_proposal(proposal_fn())
                self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
                self.assertIn("MUTABLE_INSTRUMENT_EVIDENCE_UNPINNED", result["reasons"])
                self.assertFalse(any(
                    reason.startswith("ATLAS_CANONICAL_TARGET_NOT_RESOLVED")
                    for reason in result["reasons"]
                ))

    def test_review_apis_do_not_accept_caller_supplied_registry_or_identity(self):
        provider_parameters = inspect.signature(review_provider_authority_proposal).parameters
        alias_parameters = inspect.signature(review_source_alias_proposal).parameters
        self.assertEqual(set(provider_parameters), {"proposal"})
        self.assertEqual(set(alias_parameters), {"proposal"})

    def test_rehashed_official_hash_change_still_detected_against_manifest(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["evidenceLineage"][0]["contentSha256"] = "0" * 64
        _rehash(proposal)
        result = review_provider_authority_proposal(proposal)
        self.assertTrue(any(
            reason.startswith("OFFICIAL_EVIDENCE_HASH_DIFFERS_FROM_MANIFEST:")
            for reason in result["reasons"]
        ))

    def test_rehashed_official_commit_change_still_detected(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["evidenceLineage"][0]["commitSha"] = "1" * 40
        _rehash(proposal)
        result = review_provider_authority_proposal(proposal)
        self.assertIn("OFFICIAL_EVIDENCE_COMMIT_MISMATCH", result["reasons"])

    def test_rehashed_provider_tuple_divergence_detected_against_runtime_registry(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["claim"]["accountScope"] = "CRYPTO"
        _rehash(proposal)
        result = review_provider_authority_proposal(proposal)
        self.assertIn("CLAIM_ACCOUNT_SCOPE_MISMATCH", result["reasons"])

    def test_mutable_instrument_links_can_never_be_review_complete(self):
        proposal = source_alias_proposal_005930()
        result = review_source_alias_proposal(proposal)
        self.assertIn("MUTABLE_INSTRUMENT_EVIDENCE_UNPINNED", result["reasons"])
        self.assertNotEqual(result["reviewStatus"], "REVIEW_COMPLETE")

    def test_rehashed_cross_subject_evidence_binding_change_detected(self):
        proposal = copy.deepcopy(source_alias_proposal_000660())
        mutable = next(
            entry for entry in proposal["evidenceLineage"]
            if entry.get("kind") == "MUTABLE_PUBLIC_INSTRUMENT_CONFIRMATION"
        )
        mutable["sourceAssetId"] = "005930"
        _rehash(proposal)
        result = review_source_alias_proposal(proposal)
        self.assertIn(
            "INSTRUMENT_EVIDENCE_CLAIM_BINDING_MISMATCH:sourceAssetId", result["reasons"]
        )

    def test_005930_evidence_reused_in_000660_is_rejected(self):
        proposal_005930 = source_alias_proposal_005930()
        forged_000660 = copy.deepcopy(source_alias_proposal_000660())
        reused = [
            copy.deepcopy(entry) for entry in proposal_005930["evidenceLineage"]
            if entry.get("kind") in {
                "MUTABLE_PUBLIC_INSTRUMENT_CONFIRMATION", "ATLAS_CANONICAL_TARGET_REFERENCE",
            }
        ]
        forged_000660["evidenceLineage"].extend(reused)
        with self.assertRaisesRegex(
            KisProvenanceProposalReviewError, "INSTRUMENT_SPECIFIC_EVIDENCE_REUSED_ACROSS_PROPOSALS",
        ):
            reject_if_evidence_reused_across_alias_proposals(proposal_005930, forged_000660)

    def test_reused_urls_cannot_be_hidden_by_changing_only_narrative(self):
        proposal_005930 = source_alias_proposal_005930()
        forged_000660 = copy.deepcopy(source_alias_proposal_000660())
        source_entry = next(
            copy.deepcopy(entry) for entry in proposal_005930["evidenceLineage"]
            if entry.get("kind") == "MUTABLE_PUBLIC_INSTRUMENT_CONFIRMATION"
        )
        source_entry["claim"] = "different narrative text does not make reused sources independent"
        forged_000660["evidenceLineage"].append(source_entry)
        with self.assertRaisesRegex(
            KisProvenanceProposalReviewError, "INSTRUMENT_SPECIFIC_EVIDENCE_REUSED_ACROSS_PROPOSALS",
        ):
            reject_if_evidence_reused_across_alias_proposals(proposal_005930, forged_000660)

    def test_shared_general_pdno_shape_pin_is_not_instrument_specific_reuse(self):
        reject_if_evidence_reused_across_alias_proposals(
            source_alias_proposal_005930(), source_alias_proposal_000660(),
        )

    def test_rehashed_ratified_status_is_rejected(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["proposalStatus"] = "RATIFIED"
        _rehash(proposal)
        with self.assertRaisesRegex(KisProvenanceProposalReviewError, "FORBIDDEN_STATUS_STRING_PRESENT"):
            review_provider_authority_proposal(proposal)

    def test_rehashed_broker_verified_status_is_rejected(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["proposalStatus"] = "BROKER_VERIFIED"
        _rehash(proposal)
        with self.assertRaisesRegex(KisProvenanceProposalReviewError, "FORBIDDEN_STATUS_STRING_PRESENT"):
            review_provider_authority_proposal(proposal)

    def test_rehashed_authority_true_is_rejected(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["authority"]["order_authorized"] = True
        _rehash(proposal)
        with self.assertRaisesRegex(KisProvenanceProposalReviewError, "AUTHORITY_NOT_ALL_FALSE"):
            review_provider_authority_proposal(proposal)

    def test_rehashed_config_mutation_claim_is_rejected(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["canonicalAuthorityConfigMutated"] = True
        _rehash(proposal)
        with self.assertRaisesRegex(
            KisProvenanceProposalReviewError, "CANONICAL_AUTHORITY_CONFIG_MUTATION_CLAIMED",
        ):
            review_provider_authority_proposal(proposal)

    def test_rehashed_embedded_ratified_claim_is_rejected(self):
        proposal = copy.deepcopy(provider_authority_proposal())
        proposal["claim"]["approval_status"] = "RATIFIED"
        _rehash(proposal)
        with self.assertRaisesRegex(
            KisProvenanceProposalReviewError, "EMBEDDED_AUTHORITY_FIELD_FORBIDDEN:claim.approval_status",
        ):
            review_provider_authority_proposal(proposal)

    def test_all_current_packets_are_proposal_only_and_review_incomplete(self):
        results = [review_provider_authority_proposal(provider_authority_proposal())]
        results.extend([
            review_source_alias_proposal(source_alias_proposal_005930()),
            review_source_alias_proposal(source_alias_proposal_000660()),
        ])
        self.assertEqual({result["reviewStatus"] for result in results}, {"REVIEW_INCOMPLETE"})


if __name__ == "__main__":
    unittest.main()
