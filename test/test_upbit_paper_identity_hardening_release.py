"""P3-12 corrected exact-hash PAPER-only release regressions."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RELEASE = _load(
    "upbit_paper_identity_hardening_release_test",
    "identity/upbit_paper_identity_hardening_release.py",
)
UNIVERSE = _load("upbit_universe_for_release_test", "universe/upbit_tradeable_universe.py")


class UpbitPaperIdentityHardeningReleaseTests(unittest.TestCase):
    def test_committed_release_is_exact_deterministic_projection(self):
        RELEASE.validate_committed_release()
        documents = RELEASE.build_release_documents()
        self.assertEqual(documents["registry"], json.loads(RELEASE.REGISTRY_PATH.read_text()))
        self.assertEqual(documents["taxonomy"], json.loads(RELEASE.TAXONOMY_PATH.read_text()))
        self.assertEqual(documents["freeze"], json.loads(RELEASE.FREEZE_PATH.read_text()))

    def test_effective_registry_is_exact_eight_and_unknown_stays_closed(self):
        registry = UNIVERSE.load_identity_registry()
        mapping = UNIVERSE.effective_identity_mapping(registry, "2026-08-30")
        self.assertEqual(sorted(mapping), RELEASE.EXPECTED_MARKETS)
        self.assertNotIn("KRW-LIT", mapping)
        self.assertNotIn("KRW-USDT", mapping)

    def test_release_keeps_every_exchange_and_real_authority_false(self):
        documents = RELEASE.build_release_documents()
        for document in documents.values():
            self.assertTrue(all(value is False for value in document["authority"].values()))
        approval, candidate = RELEASE.validate_approval()
        self.assertTrue(all(value is False for value in approval["authority"].values()))
        self.assertTrue(all(value is False for value in candidate["authority"].values()))

    def test_wrong_consumer_hash_approval_fails_closed(self):
        approval = json.loads(RELEASE.APPROVAL_PATH.read_text())
        approval["candidate"]["consumer_file_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.json"
            path.write_text(json.dumps(approval), encoding="utf-8")
            with self.assertRaisesRegex(RELEASE.ReleaseError, "APPROVAL_EXACT_PIN_MISMATCH"):
                RELEASE.validate_approval(path, RELEASE.CANDIDATE_PATH)

    def test_scope_expansion_and_real_authority_fail_closed(self):
        base = json.loads(RELEASE.APPROVAL_PATH.read_text())
        mutations = (
            lambda value: value["approved_markets"].append("KRW-USDT"),
            lambda value: value["authority"].__setitem__("real_capital_authorized", True),
            lambda value: value["approved_scope"].__setitem__("atlas_internal_paper_virtual_buy", False),
        )
        expected = (
            "APPROVAL_MARKET_SCOPE_INVALID",
            "APPROVAL_AUTHORITY_INVALID",
            "APPROVAL_PAPER_SCOPE_INVALID",
        )
        for mutate, code in zip(mutations, expected):
            approval = copy.deepcopy(base)
            mutate(approval)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "approval.json"
                path.write_text(json.dumps(approval), encoding="utf-8")
                with self.assertRaisesRegex(RELEASE.ReleaseError, code):
                    RELEASE.validate_approval(path, RELEASE.CANDIDATE_PATH)

    def test_historical_frozen_record_remains_blocked(self):
        freeze = json.loads(RELEASE.FREEZE_PATH.read_text())
        self.assertIn(
            "a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12",
            freeze["blocked_universe_record_payload_sha256s"],
        )
        self.assertTrue(freeze["preserve_historical_evidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
