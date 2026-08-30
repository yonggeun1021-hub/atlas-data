#!/usr/bin/env python3
"""P3-12-GOV-05 (v2 schema): identity/upbit_exact_release_binding_successor_candidate.py
and the committed append-only successor candidate packet."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_successor_candidate_test",
    ROOT / "identity" / "upbit_exact_release_binding_successor_candidate.py",
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

COMMITTED_PACKET_PATH = (
    ROOT / "data/observations/upbit_exact_release_binding_successor_candidate/2026-08-30"
    "/20260830T200000Z/packet.json"
)


class SuccessorCandidateBuilderTests(unittest.TestCase):
    def test_build_is_self_hash_consistent(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T20:00:00Z")
        recomputed = BUILDER.payload_sha256(
            {key: value for key, value in packet.items() if key != "payload_sha256"}
        )
        self.assertEqual(packet["payload_sha256"], recomputed)

    def test_never_release_ready_or_approved(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T20:00:00Z")
        self.assertFalse(packet["release_ready"])
        self.assertFalse(packet["exact_hash_cio_approval_present"])
        self.assertEqual(packet["review_status"], "PENDING_EXACT_HASH_REAPPROVAL")
        for key, value in packet["authority"].items():
            self.assertFalse(value, f"authority.{key} must stay false")

    def test_has_explicit_base_candidate_pins_not_a_bare_hash_list(self):
        # Item 2's requirement: explicit base payload/file pins that let the
        # base candidate's exact projection be re-verified, not merely a
        # reference-hash list.
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T20:00:00Z")
        base = packet["base_candidate"]
        self.assertEqual(set(base), {"path", "file_sha256", "payload_sha256"})
        self.assertEqual(BUILDER.file_sha256(ROOT / base["path"]), base["file_sha256"])
        base_document = json.loads((ROOT / base["path"]).read_text(encoding="utf-8"))
        self.assertEqual(base_document["payload_sha256"], base["payload_sha256"])

    def test_code_binding_pins_consumer_validator_and_immutable_policy_contract(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T20:00:00Z")
        binding = packet["code_binding"]
        self.assertEqual(set(binding), {"consumer_file", "validator_file", "policy_contract"})
        for entry in binding.values():
            self.assertEqual(BUILDER.file_sha256(ROOT / entry["path"]), entry["sha256"])

    def test_references_base_evidence_without_recapturing(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T20:00:00Z")
        base_approval = packet["base_approval_evidence"]
        self.assertEqual(BUILDER.file_sha256(ROOT / base_approval["path"]), base_approval["file_sha256"])
        self.assertIn("20260830T111117Z", packet["first_party_evidence_reference"]["path"])


class CommittedSuccessorCandidatePacketTests(unittest.TestCase):
    @unittest.skipUnless(COMMITTED_PACKET_PATH.is_file(), "committed successor candidate packet not present")
    def test_committed_packet_is_exact_current_build(self):
        committed = json.loads(COMMITTED_PACKET_PATH.read_text(encoding="utf-8"))
        rebuilt = BUILDER.build_successor_candidate(generated_at=committed["generated_at"])
        self.assertEqual(committed, rebuilt)

    @unittest.skipUnless(COMMITTED_PACKET_PATH.is_file(), "committed successor candidate packet not present")
    def test_committed_packet_self_hash_and_code_pins_are_activation_ready(self):
        """The REAL committed successor candidate (not a synthetic
        fixture) carries exactly the pins a genuine future code approval
        would need to reference verbatim: its own file hash, its own
        payload self-hash, and code_binding entries whose sha256 values
        match the CURRENT live consumer/validator/policy-contract files."""
        erb_spec = importlib.util.spec_from_file_location(
            "erb_for_successor_activation_test", ROOT / "governance" / "upbit_exact_release_binding.py",
        )
        erb = importlib.util.module_from_spec(erb_spec)
        erb_spec.loader.exec_module(erb)

        successor = json.loads(COMMITTED_PACKET_PATH.read_text(encoding="utf-8"))
        live_file_hash = erb.file_sha256(COMMITTED_PACKET_PATH)
        live_payload_hash = erb.payload_sha256(
            {key: value for key, value in successor.items() if key != "payload_sha256"}
        )
        self.assertEqual(successor["payload_sha256"], live_payload_hash)
        for label, path_const in (
            ("consumer_file", erb.CONSUMER_PATH),
            ("validator_file", erb.VALIDATOR_PATH),
            ("policy_contract", erb.POLICY_CONTRACT_PATH),
        ):
            self.assertEqual(
                successor["code_binding"][label]["sha256"], erb.file_sha256(path_const),
                f"{label} pin is stale relative to the current live file",
            )
        # sanity: this file's own live hash is what a code approval would pin
        self.assertEqual(len(live_file_hash), 64)


if __name__ == "__main__":
    unittest.main()
