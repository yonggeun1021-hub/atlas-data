from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.candidate_identity_authority_proposal import (
    AUTHORITY_ALL_FALSE, COMPLETE, INCOMPLETE,
    CandidateIdentityAuthorityProposalError, _proposal, build_packet, validate_packet,
)


class CandidateIdentityAuthorityProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gaps = json.loads((ROOT / "evidence/operational/dynamic_clock/candidate_identity_gap_inventory.json").read_text())
        cls.taxonomy = ROOT / "config/crypto_breadth_exclusion_taxonomy.json"
        cls.raw = ROOT / "evidence/crypto/breadth/raw"
        cls.packet = build_packet(cls.gaps, cls.taxonomy, cls.raw)

    def test_real_gap_population_reconciles(self):
        self.assertEqual(self.packet["summary"]["gap_count"], 58)
        self.assertEqual(self.packet["summary"]["proposal_count"], 58)
        self.assertEqual(self.packet["summary"]["review_status_counts"], {COMPLETE: 56, INCOMPLETE: 2})
        doge = next(x for x in self.packet["proposals"] if x["subject"] == "DOGE/USD")
        self.assertEqual(doge["review_status"], INCOMPLETE)
        self.assertIn("KRAKEN_PAIR_IDENTITY_FIELDS_MISMATCH", doge["reason_codes"])

    def test_mechanical_proposals_remain_unratified_and_create_no_authority(self):
        self.assertEqual(self.packet["summary"]["canonical_authority_rows_created"], 0)
        self.assertFalse(self.packet["policy_boundary"]["proposal_is_identity_authority"])
        self.assertEqual(self.packet["authority"], AUTHORITY_ALL_FALSE)
        for row in self.packet["proposals"]:
            self.assertEqual(row["authority"], AUTHORITY_ALL_FALSE)
            self.assertNotEqual(row.get("proposal_status"), "RATIFIED")

    def test_exact_kraken_pair_and_taxonomy_are_both_required(self):
        gap = copy.deepcopy(self.gaps["identity_gaps"][0])
        gap["provider_pair_diagnostics"][0]["diagnostic_status"] = "TAXONOMY_RECORD_NOT_FOUND"
        row = _proposal(gap, {})
        self.assertEqual(row["review_status"], INCOMPLETE)

    def test_resigned_source_gap_tamper_is_independently_rejected(self):
        gaps = copy.deepcopy(self.gaps)
        gaps["identity_gaps"][0]["subject"] = "TAMPERED"
        unsigned = dict(gaps)
        unsigned.pop("packet_sha256", None)
        import hashlib
        gaps["packet_sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        with self.assertRaisesRegex(
            CandidateIdentityAuthorityProposalError,
            "SOURCE_GAP_INVENTORY_INDEPENDENT_VALIDATION_FAILED",
        ):
            build_packet(gaps, self.taxonomy, self.raw)

    def test_korea_direct_review_does_not_get_a_crypto_proposal(self):
        row = next(x for x in self.packet["proposals"] if x["market"] == "KOREA")
        self.assertEqual(row["subject"], "034020")
        self.assertEqual(row["review_status"], INCOMPLETE)
        self.assertIsNone(row["proposed_rows"])

    def test_no_canonical_authority_configuration_is_modified_or_embedded(self):
        self.assertFalse(self.packet["policy_boundary"]["canonical_config_modified"])
        proposal_text = json.dumps(self.packet["proposals"])
        self.assertNotIn('"approval_status": "RATIFIED"', proposal_text)
        self.assertNotIn('"ratified_at"', proposal_text)

    def test_validator_rebuilds_and_rejects_resigned_tamper(self):
        packet = copy.deepcopy(self.packet)
        packet["proposals"][0]["proposal_status"] = "RATIFIED"
        packet["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "PROPOSAL_PACKET_MISMATCH"):
            validate_packet(packet, self.gaps, self.taxonomy, self.raw)

    def test_missing_eligible_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td)
            with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "KRAKEN_CAPTURE_NOT_AVAILABLE"):
                build_packet(self.gaps, self.taxonomy, empty)

    def test_output_is_deterministic(self):
        self.assertEqual(self.packet, build_packet(copy.deepcopy(self.gaps), self.taxonomy, self.raw))

    def test_run_all_registers_proposal_contract(self):
        self.assertIn('"test/test_candidate_identity_authority_proposal.py"', (ROOT / "run_all.py").read_text())


if __name__ == "__main__":
    unittest.main()
