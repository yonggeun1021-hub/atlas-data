from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.candidate_identity_authority_review_inventory import (
    AUTHORITY_ALL_FALSE,
    COHERENT,
    CONFLICT,
    EVIDENCE_INCOMPLETE,
    SOURCE_BINDING_STALE,
    CandidateIdentityAuthorityReviewInventoryError,
    _conflicts,
    build_inventory,
    validate_inventory,
)


class CandidateIdentityAuthorityReviewInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "evidence/identity/proposals/candidate_identity_authority_proposal.json"
        cls.proposal = json.loads(cls.path.read_text())
        cls.inventory = build_inventory(cls.proposal, proposal_path=cls.path)

    def _build_synthetic_stale_inventory(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["source_gap_inventory_packet_sha256"] = "0" * 64
        unsigned = dict(proposal)
        unsigned.pop("packet_sha256")
        from identity.candidate_identity_authority_review_inventory import _sha
        proposal["packet_sha256"] = _sha(unsigned)
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            path = Path(td) / "proposal.json"
            path.write_text(json.dumps(proposal, sort_keys=True))
            return build_inventory(proposal, proposal_path=path), proposal

    def test_real_proposal_is_current_and_coherent_without_creating_authority(self):
        population = len(self.proposal["proposals"])
        self.assertEqual(self.inventory["summary"], {
            "population_count": population,
            "review_status_counts": {COHERENT: population},
            "conflict_candidate_count": 0,
            "canonical_authority_rows_created": 0,
        })
        self.assertEqual(self.inventory["source_binding_status"], "CURRENT_EXACT_BINDING")
        self.assertEqual(len(self.inventory["rows"]), population)
        self.assertEqual(self.inventory["authority"], AUTHORITY_ALL_FALSE)
        self.assertFalse(self.inventory["policy_boundary"]["mechanical_coherence_is_identity_approval"])

    def test_synthetic_stale_proposal_is_fail_closed_without_reusing_rows(self):
        inventory, proposal = self._build_synthetic_stale_inventory()
        self.assertEqual(inventory["summary"], {
            "population_count": 0,
            "source_proposal_population_count": len(proposal["proposals"]),
            "review_status_counts": {SOURCE_BINDING_STALE: 1},
            "conflict_candidate_count": 0,
            "canonical_authority_rows_created": 0,
        })
        self.assertEqual(inventory["source_binding_status"], SOURCE_BINDING_STALE)
        self.assertEqual(inventory["rows"], [])
        self.assertEqual(inventory["authority"], AUTHORITY_ALL_FALSE)
        self.assertFalse(inventory["policy_boundary"]["mechanical_coherence_is_identity_approval"])

    def test_synthetic_stale_binding_preserves_both_exact_hashes(self):
        inventory, proposal = self._build_synthetic_stale_inventory()
        source = inventory["source_proposal"]
        self.assertEqual(source["bound_gap_inventory_packet_sha256"], proposal["source_gap_inventory_packet_sha256"])
        self.assertNotEqual(source["bound_gap_inventory_packet_sha256"], source["current_gap_inventory_packet_sha256"])

    def test_contradictory_listing_payload_flags_both_candidates_and_chooses_no_winner(self):
        rows = [copy.deepcopy(row) for row in self.proposal["proposals"] if row.get("proposed_rows")][:2]
        rows[1]["proposed_rows"]["listing"]["listing_id"] = rows[0]["proposed_rows"]["listing"]["listing_id"]
        conflicts = _conflicts(rows)
        self.assertIn("LISTING_ID_CONTRADICTORY_PAYLOAD", conflicts[rows[0]["candidate_id"]])
        self.assertIn("LISTING_ID_CONTRADICTORY_PAYLOAD", conflicts[rows[1]["candidate_id"]])

    def test_contradictory_provider_alias_to_listing_flags_both_candidates(self):
        rows = [copy.deepcopy(row) for row in self.proposal["proposals"] if row.get("proposed_rows")][:2]
        rows[1]["proposed_rows"]["source_alias"]["source_name"] = rows[0]["proposed_rows"]["source_alias"]["source_name"]
        rows[1]["proposed_rows"]["source_alias"]["source_asset_id"] = rows[0]["proposed_rows"]["source_alias"]["source_asset_id"]
        conflicts = _conflicts(rows)
        self.assertIn("SOURCE_ALIAS_CONTRADICTORY_LISTING", conflicts[rows[0]["candidate_id"]])
        self.assertIn("SOURCE_ALIAS_CONTRADICTORY_LISTING", conflicts[rows[1]["candidate_id"]])

    def test_same_issuer_payload_across_multiple_instruments_is_not_a_conflict(self):
        rows = [copy.deepcopy(row) for row in self.proposal["proposals"] if row.get("proposed_rows")][:2]
        rows[1]["proposed_rows"]["issuer"] = copy.deepcopy(rows[0]["proposed_rows"]["issuer"])
        rows[1]["proposed_rows"]["instrument"]["canonical_issuer_id"] = rows[0]["proposed_rows"]["issuer"]["canonical_issuer_id"]
        conflicts = _conflicts(rows)
        self.assertFalse(conflicts[rows[0]["candidate_id"]])
        self.assertFalse(conflicts[rows[1]["candidate_id"]])

    def test_resigned_authority_or_status_escalation_is_rejected(self):
        tampered = copy.deepcopy(self.inventory)
        tampered["authority"]["buy_authority"] = True
        unsigned = dict(tampered)
        unsigned.pop("packet_sha256")
        from identity.candidate_identity_authority_review_inventory import _sha
        tampered["packet_sha256"] = _sha(unsigned)
        with self.assertRaisesRegex(CandidateIdentityAuthorityReviewInventoryError, "REVIEW_INVENTORY_MISMATCH"):
            validate_inventory(tampered, self.proposal, proposal_path=self.path)

    def test_resigned_source_proposal_substitution_is_rejected_before_audit(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["proposals"][0]["subject"] = "TAMPERED"
        with self.assertRaisesRegex(CandidateIdentityAuthorityReviewInventoryError, "SOURCE_PROPOSAL_BYTES_MISMATCH"):
            build_inventory(proposal, proposal_path=self.path)

    def test_source_proposal_byte_substitution_is_visible(self):
        self.assertEqual(self.inventory["source_proposal"]["path"], "evidence/identity/proposals/candidate_identity_authority_proposal.json")
        self.assertEqual(len(self.inventory["source_proposal"]["bytes_sha256"]), 64)
        self.assertEqual(self.inventory["source_proposal"]["packet_sha256"], self.proposal["packet_sha256"])

    def test_re_signed_stale_proposal_is_rejected_before_binding_classification(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["authority"]["buy_authority"] = True
        unsigned = dict(proposal)
        unsigned.pop("packet_sha256")
        from identity.candidate_identity_authority_review_inventory import _sha
        proposal["packet_sha256"] = _sha(unsigned)
        with self.assertRaisesRegex(CandidateIdentityAuthorityReviewInventoryError, "SOURCE_PROPOSAL_BYTES_MISMATCH"):
            build_inventory(proposal, proposal_path=self.path)

    def test_output_is_deterministic_and_registered(self):
        self.assertEqual(self.inventory, build_inventory(copy.deepcopy(self.proposal), proposal_path=self.path))
        self.assertIn('"test/test_candidate_identity_authority_review_inventory.py"', (ROOT / "run_all.py").read_text())


if __name__ == "__main__":
    unittest.main()
