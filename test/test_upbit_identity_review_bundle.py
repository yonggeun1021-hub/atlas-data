"""P3-12 full unratified Upbit identity proposal review bundle."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "upbit_identity_review_bundle.py"
NATURAL = ROOT / "data" / "observations" / "upbit_identity_review" / "2026-08-29" / "packet.json"


def load_module():
    spec = importlib.util.spec_from_file_location("upbit_identity_review_bundle_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


B = load_module()


class IdentityReviewBundleTests(unittest.TestCase):
    def test_natural_bundle_is_full_hash_bound_and_review_only(self):
        packet = json.loads(NATURAL.read_text(encoding="utf-8"))
        self.assertFalse(NATURAL.is_symlink())
        expected_hash = B.payload_sha256({k: v for k, v in packet.items() if k != "payload_sha256"})
        self.assertEqual(packet["payload_sha256"], expected_hash)
        self.assertEqual(packet["review_status"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
        self.assertEqual(packet["summary"]["proposal_count"], 282)
        self.assertEqual(len(packet["proposals"]), 282)
        self.assertEqual(packet["summary"]["finding_count"], len(packet["findings"]))
        self.assertEqual(packet["review_boundary"]["broad_ratified_canonical_registry_status"], "ABSENT")
        self.assertTrue(packet["authority"]["review_only"])
        for field, value in packet["authority"].items():
            if field != "review_only":
                self.assertIs(value, False, field)

    def test_every_proposal_is_individually_hash_bound_and_unratified(self):
        packet = json.loads(NATURAL.read_text(encoding="utf-8"))
        markets = []
        for proposal in packet["proposals"]:
            expected = B.IDP.payload_sha256({k: v for k, v in proposal.items() if k != "proposalSha256"})
            self.assertEqual(proposal["proposalSha256"], expected)
            self.assertEqual(proposal["proposalStatus"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
            self.assertFalse(proposal["canonicalAuthorityConfigMutated"])
            markets.append(proposal["claim"]["upbitMarket"])
        self.assertEqual(markets, sorted(set(markets)))
        self.assertIn("KRW-BTC", markets)
        self.assertIn("KRW-ETH", markets)

    def test_source_hashes_remain_historical_after_effective_dated_ratification(self):
        packet = json.loads(NATURAL.read_text(encoding="utf-8"))
        source = packet["source"]
        self.assertNotEqual(
            source["universe_policy_file_sha256"],
            B.file_sha256(ROOT / source["universe_policy_path"]),
        )
        self.assertNotEqual(
            source["taxonomy_file_sha256"],
            B.file_sha256(ROOT / source["taxonomy_path"]),
        )
        self.assertEqual(len(source["universe_policy_file_sha256"]), 64)
        self.assertEqual(len(source["taxonomy_file_sha256"]), 64)

    def test_natural_bundle_verifies_as_historical_without_rewrite(self):
        natural_0830 = ROOT / "data" / "observations" / "upbit_identity_review" / "2026-08-30" / "packet.json"
        original = natural_0830.read_bytes()
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "2026-08-30" / "packet.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(original)
            result = B.populate("2026-08-30", data_root=Path(temp_dir))
            self.assertEqual(result["outcome"], "verified_historical")
            self.assertEqual(target.read_bytes(), original)

    def test_population_is_idempotent_and_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            first = B.populate("2026-08-29", data_root=output)
            second = B.populate("2026-08-29", data_root=output)
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            target = Path(first["path"])
            packet = json.loads(target.read_text(encoding="utf-8"))
            packet["summary"]["proposal_count"] = 0
            target.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaises(B.IdentityReviewBundleError):
                B.populate("2026-08-29", data_root=output)

    def test_no_canonical_or_policy_file_is_written_by_producer(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("write_text", source.split("def build_bundle", 1)[1].split("def output_path", 1)[0])
        self.assertNotIn('"approval_status": "RATIFIED"', source)


if __name__ == "__main__":
    unittest.main()
