"""P3-12 corrected exact-hash PAPER-only release regressions."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


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


def _consumer_hash_pinned_as_originally_approved():
    """P3-12-GOV-05: universe/upbit_tradeable_universe.py now carries the
    runtime exact-approval-binding wiring, so its live bytes no longer match
    what the v2 candidate/approval originally pinned -- see
    test_committed_release_is_exact_deterministic_projection below, which
    asserts this exact invalidation directly. The other tests in this file
    are exercising OTHER checks inside validate_candidate()/validate_approval()
    (scope tampering, approval-vs-candidate pin cross-checks) that would
    otherwise be masked by that same, now-permanent mismatch firing first;
    this patches CANDIDATE.file_sha256 to answer with the originally-pinned
    value for CONSUMER_PATH specifically (every other path still gets its
    real, live hash) so those checks can still be exercised on their own
    terms. Never a production bypass -- test-only, and it does not touch
    universe/upbit_tradeable_universe.py's own real behavior at all.
    """
    real_file_sha256 = RELEASE.CANDIDATE.file_sha256
    original_consumer_hash = json.loads(RELEASE.CANDIDATE_PATH.read_text())["consumer_file_sha256"]

    def shim(path):
        if Path(path) == RELEASE.CANDIDATE.CONSUMER_PATH:
            return original_consumer_hash
        return real_file_sha256(path)

    return mock.patch.object(RELEASE.CANDIDATE, "file_sha256", side_effect=shim)


class UpbitPaperIdentityHardeningReleaseTests(unittest.TestCase):
    def test_committed_release_is_exact_deterministic_projection(self):
        # P3-12-GOV-05: universe/upbit_tradeable_universe.py changed on this
        # branch (runtime exact-approval-binding wiring), so its live bytes
        # no longer match the v2 candidate's pinned consumer_file_sha256 --
        # the ALREADY-COMMITTED, pre-existing candidate builder's own live
        # hash check (identity/upbit_paper_identity_hardening_candidate.py,
        # untouched by GOV-05) now correctly reports the v2 release as
        # invalid here, exactly as required: "기존 v2 approval은 새
        # 브랜치에서 자동으로 유효하지 않아야 한다."
        with self.assertRaisesRegex(RELEASE.CANDIDATE.HardeningError, "CONSUMER_FILE_HASH_MISMATCH"):
            RELEASE.validate_committed_release()

    def test_effective_registry_is_exact_eight_and_unknown_stays_closed(self):
        # P3-12-GOV-05: effective_identity_mapping() now additionally
        # requires the runtime exact-approval-binding allowlist, which is
        # empty on this branch (PENDING_EXACT_HASH_REAPPROVAL) -- the real
        # committed registry's raw mappings are still exactly the eight
        # approved markets, but "effective" (authoritative-for-consumption)
        # is correctly empty here regardless.
        registry = UNIVERSE.load_identity_registry()
        self.assertEqual(sorted(registry["mappings"]), RELEASE.EXPECTED_MARKETS)
        self.assertNotIn("KRW-LIT", registry["mappings"])
        self.assertNotIn("KRW-USDT", registry["mappings"])
        mapping = UNIVERSE.effective_identity_mapping(registry, "2026-08-30")
        self.assertEqual(mapping, {})

    def test_release_keeps_every_exchange_and_real_authority_false(self):
        # Read the raw committed documents directly -- build_release_documents()/
        # validate_approval() now correctly refuse to run to completion on
        # this branch (see test_committed_release_is_exact_deterministic_projection),
        # but the authority-false invariant on the actual committed bytes is
        # still exactly what this test is about.
        for path in (RELEASE.REGISTRY_PATH, RELEASE.TAXONOMY_PATH, RELEASE.FREEZE_PATH):
            document = json.loads(path.read_text())
            self.assertTrue(all(value is False for value in document["authority"].values()), path)
        approval = json.loads(RELEASE.APPROVAL_PATH.read_text())
        candidate = json.loads(RELEASE.CANDIDATE_PATH.read_text())
        self.assertTrue(all(value is False for value in approval["authority"].values()))
        self.assertTrue(all(value is False for value in candidate["authority"].values()))

    def test_wrong_consumer_hash_approval_fails_closed(self):
        with _consumer_hash_pinned_as_originally_approved():
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
        with _consumer_hash_pinned_as_originally_approved():
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
