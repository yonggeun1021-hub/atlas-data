#!/usr/bin/env python3
"""P3-12-GOV-05: identity/upbit_exact_release_binding_successor_candidate.py
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
    "/20260830T143000Z/packet.json"
)


class SuccessorCandidateBuilderTests(unittest.TestCase):
    def test_build_is_self_hash_consistent(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T14:30:00Z")
        recomputed = BUILDER.payload_sha256(
            {key: value for key, value in packet.items() if key != "payload_sha256"}
        )
        self.assertEqual(packet["payload_sha256"], recomputed)

    def test_never_release_ready_or_approved(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T14:30:00Z")
        self.assertFalse(packet["release_ready"])
        self.assertFalse(packet["exact_hash_cio_approval_present"])
        self.assertEqual(packet["review_status"], "PENDING_EXACT_HASH_REAPPROVAL")
        for key, value in packet["authority"].items():
            self.assertFalse(value, f"authority.{key} must stay false")

    def test_references_base_evidence_without_recapturing(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T14:30:00Z")
        base_approval = packet["supersedes"]["base_approval_evidence"]
        self.assertEqual(
            BUILDER.file_sha256(ROOT / base_approval["path"]), base_approval["file_sha256"],
        )
        base_candidate = packet["supersedes"]["base_candidate_packet"]
        self.assertEqual(
            BUILDER.file_sha256(ROOT / base_candidate["path"]), base_candidate["file_sha256"],
        )
        # No new capture directory under the first-party evidence root --
        # only the ORIGINAL capture this packet references by hash.
        self.assertIn("20260830T111117Z", packet["first_party_evidence_reference"]["path"])

    def test_changed_consumer_and_new_validator_hashes_match_current_files(self):
        packet = BUILDER.build_successor_candidate(generated_at="2026-08-30T14:30:00Z")
        self.assertEqual(
            BUILDER.file_sha256(ROOT / packet["changed_consumer"]["path"]),
            packet["changed_consumer"]["file_sha256"],
        )
        self.assertEqual(
            BUILDER.file_sha256(ROOT / packet["new_runtime_validator"]["path"]),
            packet["new_runtime_validator"]["file_sha256"],
        )
        self.assertEqual(
            BUILDER.file_sha256(ROOT / packet["new_binding_contract"]["path"]),
            packet["new_binding_contract"]["file_sha256"],
        )


class CommittedSuccessorCandidatePacketTests(unittest.TestCase):
    @unittest.skipUnless(COMMITTED_PACKET_PATH.is_file(), "committed successor candidate packet not present")
    def test_committed_packet_is_exact_current_build(self):
        committed = json.loads(COMMITTED_PACKET_PATH.read_text(encoding="utf-8"))
        rebuilt = BUILDER.build_successor_candidate(generated_at=committed["generated_at"])
        self.assertEqual(committed, rebuilt)


if __name__ == "__main__":
    unittest.main()
