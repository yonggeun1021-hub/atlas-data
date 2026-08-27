#!/usr/bin/env python3
"""P8-04 sanitized Korea Breadth private replay attestation regression."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from regime import korea_breadth_replay_attestation as MODULE  # noqa: E402


class KoreaBreadthReplayAttestationTest(unittest.TestCase):
    def setUp(self):
        self.contract = MODULE.load_contract()
        self.attestation = MODULE.load_approved_attestation()

    def test_real_attestation_is_exactly_rebuilt_from_approved_proof(self):
        self.assertEqual(self.attestation, MODULE.build_attestation(self.contract))
        self.assertEqual(
            self.attestation["payload_sha256"],
            "c791b7d3ab1a4e6ef2d1ea246260444db92f2c65e3e182644cd9cde323395392",
        )
        self.assertEqual(self.attestation["result"]["replay_status"], "MATCHED")
        self.assertTrue(
            self.attestation["result"]["independent_source_replay_available"]
        )

    def test_proof_binds_exact_private_and_public_lineage(self):
        proof = self.attestation["proof"]
        self.assertEqual(
            proof["private_evidence_commit_sha"],
            "5fbc9283211ffa773f4bcd573020ee5201afd766",
        )
        self.assertEqual(proof["private_workflow_run_id"], "33089264800")
        self.assertEqual(
            proof["public_code_commit_sha"],
            "8b9e0414ed94d4485085f6f2e0b67f98b9a7c979",
        )
        self.assertEqual(
            proof["public_bundle_payload_sha256"],
            "352ad44a23d3e1a57ff7305a68ddbdf30c55bf388260d2eb969e44d43e3a6b38",
        )
        self.assertEqual(proof["packet_link_count"], 4)
        self.assertEqual(proof["raw_response_count"], 8)

    def test_public_packet_contains_no_raw_body_row_or_response_hash_list(self):
        text = json.dumps(self.attestation, sort_keys=True)
        for forbidden in (
            "raw_responses",
            "packet_links",
            "previous_response_sha256",
            "current_response_sha256",
            "api_key_value",
        ):
            self.assertNotIn(forbidden, text)
        self.assertFalse(
            self.attestation["disclosure_boundary"]["raw_response_bodies_public"]
        )
        self.assertFalse(
            self.attestation["disclosure_boundary"]["raw_response_hashes_republished"]
        )
        self.assertFalse(
            self.attestation["disclosure_boundary"]["per_symbol_rows_public"]
        )

    def test_replay_match_never_grants_policy_or_trading_authority(self):
        self.assertTrue(all(value is False for value in self.attestation["authority"].values()))
        self.assertTrue(
            all(value is False for value in self.attestation["policy_boundary"].values())
        )

    def test_self_rehashed_lineage_tamper_is_not_approved(self):
        tampered = copy.deepcopy(self.attestation)
        tampered["proof"]["private_workflow_run_id"] = "999"
        unsigned = {key: value for key, value in tampered.items() if key != "payload_sha256"}
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.KoreaBreadthReplayAttestationError, "ATTESTATION_NOT_APPROVED"
        ):
            MODULE.validate_attestation(tampered, self.contract)

    def test_self_rehashed_authority_tamper_is_not_approved(self):
        tampered = copy.deepcopy(self.attestation)
        tampered["authority"]["trading_authorized"] = True
        unsigned = {key: value for key, value in tampered.items() if key != "payload_sha256"}
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.KoreaBreadthReplayAttestationError, "ATTESTATION_NOT_APPROVED"
        ):
            MODULE.validate_attestation(tampered, self.contract)

    def test_caller_supplied_contract_tamper_is_revalidated(self):
        tampered_contract = copy.deepcopy(self.contract)
        tampered_contract["policy_boundary"]["axis_promotion_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.KoreaBreadthReplayAttestationError, "CONTRACT_INVALID"
        ):
            MODULE.validate_attestation(self.attestation, tampered_contract)

    def test_missing_attestation_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / MODULE.CONTRACT_RELATIVE_PATH
            contract_path.parent.mkdir(parents=True)
            contract_path.write_text(
                json.dumps(self.contract, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MODULE.KoreaBreadthReplayAttestationError, "ATTESTATION_INVALID"
            ):
                MODULE.load_approved_attestation(root=root)


if __name__ == "__main__":
    unittest.main()
