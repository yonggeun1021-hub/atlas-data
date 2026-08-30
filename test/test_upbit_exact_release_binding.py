#!/usr/bin/env python3
"""P3-12-GOV-05 (v2 design): governance/upbit_exact_release_binding.py.

Two independent, one-way chains rooted entirely in fields the identity
registry / taxonomy documents carry on themselves -- no separate mutable
allowlist file. The content chain is the pre-existing
approval_evidence_ref/source_candidate_packet pointer these documents
already had; the code chain is the new code_approval_evidence_ref pointer,
absent from every real document on this branch, which is exactly what
keeps this PENDING without any external list needing to be pre-populated.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_test", ROOT / "governance" / "upbit_exact_release_binding.py"
)
ERB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ERB)

MARKETS_TO_IDS = {
    "KRW-BTC": "BTC", "KRW-ETH": "ETH", "KRW-LINK": "LINK", "KRW-SHIB": "SHIB",
    "KRW-SOL": "SOL", "KRW-SUI": "SUI", "KRW-WLD": "WLD", "KRW-XRP": "XRP",
}
_AUTHORITY_FALSE = {
    "identity_authorized": False, "taxonomy_authorized": False,
    "paper_eligible_promotion_authorized": False, "candidate_promotion_authorized": False,
    "paper_exit_authorized": False, "exchange_authorized": False,
    "order_authorized": False, "production_authorized": False,
    "real_capital_authorized": False, "trading_authorized": False,
}
_SCOPE_TRUE = {
    "atlas_internal_paper_virtual_buy": True, "atlas_internal_paper_virtual_hold": True,
    "atlas_internal_paper_virtual_stop_loss": True, "atlas_internal_paper_virtual_take_profit": True,
    "atlas_internal_paper_virtual_sell": True, "atlas_internal_paper_ledger": True,
}
_FORBIDDEN_AUTHORITY_FALSE = {
    "upbit_exchange_order_authorized": False, "upbit_withdrawal_authorized": False,
    "production_authorized": False, "real_capital_authorized": False, "trading_authorized": False,
}


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def build_full_chain(tmp: Path, *, mappings=None):
    """A fully self-consistent chain (content approval + candidate,
    consumer/validator/policy-contract code, code approval + successor
    candidate) under ``tmp``. Returns (registry_doc, taxonomy_doc,
    artifact paths dict). ``registry_doc``/``taxonomy_doc`` do NOT yet
    carry ``code_approval_evidence_ref`` -- call ``activate(...)`` to add
    it, mirroring how a real future approval would populate that field
    once, by hand.
    """
    mappings = mappings if mappings is not None else dict(MARKETS_TO_IDS)

    # -- immutable policy contract --
    policy_contract = {
        "schema_version": ERB.SCHEMA_VERSION,
        "content_approval_schema_version": "upbit_paper_identity_exact_hash_approval/1",
        "code_approval_schema_version": "upbit_exact_release_binding_code_approval/1",
        "successor_candidate_schema_version": "upbit_exact_release_binding_successor_candidate/2",
        "paper_scope_keys": sorted(_SCOPE_TRUE),
        "forbidden_authority_keys": sorted(_FORBIDDEN_AUTHORITY_FALSE),
        "authority": dict(_AUTHORITY_FALSE),
    }
    policy_contract["payload_sha256"] = ERB.payload_sha256(
        {k: v for k, v in policy_contract.items() if k != "payload_sha256"}
    )
    policy_contract_path = _write(tmp / "config" / "upbit_exact_release_binding_policy_contract.json", policy_contract)

    # -- code under test (placeholders; real bytes don't matter here) --
    consumer_path = tmp / "universe" / "upbit_tradeable_universe.py"
    consumer_path.parent.mkdir(parents=True, exist_ok=True)
    consumer_path.write_text("# synthetic consumer placeholder\n", encoding="utf-8")
    validator_path = tmp / "governance" / "upbit_exact_release_binding.py"
    validator_path.parent.mkdir(parents=True, exist_ok=True)
    validator_path.write_text("# synthetic validator placeholder\n", encoding="utf-8")

    # -- content: base candidate + content approval --
    proposed_registry = {"mappings": dict(mappings), "authority": dict(_AUTHORITY_FALSE)}
    proposed_taxonomy = {
        "records": [
            {"canonical_asset_id": asset, "category": "eligible_crypto", "effective_from": "2026-08-30"}
            for asset in mappings.values()
        ],
        "authority": dict(_AUTHORITY_FALSE),
    }
    base_candidate = {
        "proposed_registry": proposed_registry,
        "proposed_registry_payload_sha256": ERB.payload_sha256(proposed_registry),
        "proposed_taxonomy": proposed_taxonomy,
        "proposed_taxonomy_payload_sha256": ERB.payload_sha256(proposed_taxonomy),
        "authority": dict(_AUTHORITY_FALSE),
    }
    base_candidate["payload_sha256"] = ERB.payload_sha256(base_candidate)
    base_candidate_path = _write(tmp / "base_candidate.json", base_candidate)
    base_candidate_relative = str(base_candidate_path.relative_to(tmp))

    content_approval = {
        "schema_version": "upbit_paper_identity_exact_hash_approval/1",
        "approval_status": "RATIFIED", "ratified_by": "CIO_USER",
        "candidate": {
            "path": base_candidate_relative,
            "file_sha256": ERB.file_sha256(base_candidate_path),
            "payload_sha256": base_candidate["payload_sha256"],
            "registry_payload_sha256": base_candidate["proposed_registry_payload_sha256"],
            "taxonomy_payload_sha256": base_candidate["proposed_taxonomy_payload_sha256"],
        },
        "approved_scope": dict(_SCOPE_TRUE),
        "authority": dict(_FORBIDDEN_AUTHORITY_FALSE),
    }
    content_approval_path = _write(tmp / "content_approval.json", content_approval)
    content_approval_relative = str(content_approval_path.relative_to(tmp))
    content_approval_hash = ERB.file_sha256(content_approval_path)

    # -- code: successor candidate + code approval --
    successor = {
        "schema_version": "upbit_exact_release_binding_successor_candidate/2",
        "base_candidate": {
            "path": base_candidate_relative,
            "file_sha256": ERB.file_sha256(base_candidate_path),
            "payload_sha256": base_candidate["payload_sha256"],
        },
        "code_binding": {
            "consumer_file": {"path": "universe/upbit_tradeable_universe.py", "sha256": ERB.file_sha256(consumer_path)},
            "validator_file": {"path": "governance/upbit_exact_release_binding.py", "sha256": ERB.file_sha256(validator_path)},
            "policy_contract": {"path": "config/upbit_exact_release_binding_policy_contract.json", "sha256": ERB.file_sha256(policy_contract_path)},
        },
        "release_ready": False,
        "exact_hash_cio_approval_present": False,
        "authority": dict(_AUTHORITY_FALSE),
    }
    successor["payload_sha256"] = ERB.payload_sha256(successor)
    successor_path = _write(tmp / "successor_candidate.json", successor)
    successor_relative = str(successor_path.relative_to(tmp))

    code_approval = {
        "schema_version": "upbit_exact_release_binding_code_approval/1",
        "approval_status": "RATIFIED", "ratified_by": "CIO_USER",
        "successor_candidate": {
            "path": successor_relative,
            "file_sha256": ERB.file_sha256(successor_path),
            "payload_sha256": successor["payload_sha256"],
        },
        "authority": dict(_AUTHORITY_FALSE),
    }
    code_approval_path = _write(tmp / "code_approval.json", code_approval)
    code_approval_relative = str(code_approval_path.relative_to(tmp))
    code_approval_hash = ERB.file_sha256(code_approval_path)

    # -- freeze cross-reference --
    freeze = {
        "approval_resolution": {
            "candidate_packet_path": base_candidate_relative,
            "candidate_packet_file_sha256": ERB.file_sha256(base_candidate_path),
            "candidate_packet_payload_sha256": base_candidate["payload_sha256"],
            "registry_candidate_payload_sha256": base_candidate["proposed_registry_payload_sha256"],
            "taxonomy_candidate_payload_sha256": base_candidate["proposed_taxonomy_payload_sha256"],
        },
        "released_paper_markets": sorted(mappings),
    }
    _write(tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json", freeze)

    def _document(content_field):
        return {
            "approval_status": "RATIFIED",
            "approval_evidence_ref": content_approval_relative,
            "approval_evidence_sha256": content_approval_hash,
            "approved_candidate_payload_sha256": (
                base_candidate["proposed_registry_payload_sha256"] if content_field == "mappings"
                else base_candidate["proposed_taxonomy_payload_sha256"]
            ),
            "source_candidate_packet": {
                "path": base_candidate_relative,
                "file_sha256": ERB.file_sha256(base_candidate_path),
                "payload_sha256": base_candidate["payload_sha256"],
            },
            content_field: (
                dict(mappings) if content_field == "mappings" else copy.deepcopy(proposed_taxonomy["records"])
            ),
            "authority": dict(_AUTHORITY_FALSE),
        }

    registry_doc = _document("mappings")
    taxonomy_doc = _document("records")

    def activate(document):
        activated = copy.deepcopy(document)
        activated["code_approval_evidence_ref"] = code_approval_relative
        activated["code_approval_evidence_sha256"] = code_approval_hash
        return activated

    artifacts = {
        "policy_contract_path": policy_contract_path,
        "consumer_path": consumer_path,
        "validator_path": validator_path,
        "base_candidate_path": base_candidate_path,
        "content_approval_path": content_approval_path,
        "successor_path": successor_path,
        "code_approval_path": code_approval_path,
        "activate": activate,
    }
    return registry_doc, taxonomy_doc, artifacts


class ActivatedChainTests(unittest.TestCase):
    """Both chains present and consistent -> effective."""

    def test_activated_registry_and_taxonomy_validate_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, artifacts = build_full_chain(tmp)
            self.assertTrue(ERB.validate_exact_release(artifacts["activate"](registry), content_field="mappings", repo_root=tmp))
            self.assertTrue(ERB.validate_exact_release(artifacts["activate"](taxonomy), content_field="records", repo_root=tmp))

    def test_activated_registry_is_exactly_the_eight_approved_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp)
            activated = artifacts["activate"](registry)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            self.assertEqual(activated["mappings"], MARKETS_TO_IDS)
            self.assertEqual(len(activated["mappings"]), 8)


class UnactivatedIsPendingTests(unittest.TestCase):
    """Content chain alone (no code_approval_evidence_ref) is NOT enough --
    this is the exact shape every real committed document has right now."""

    def test_content_chain_alone_without_code_approval_stays_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, _ = build_full_chain(tmp)
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))
            self.assertFalse(ERB.validate_exact_release(taxonomy, content_field="records", repo_root=tmp))

    def test_explicit_null_code_approval_ref_stays_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = build_full_chain(tmp)
            registry["code_approval_evidence_ref"] = None
            registry["code_approval_evidence_sha256"] = None
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))


class RealRepoStaysPendingTests(unittest.TestCase):
    def test_real_committed_registry_has_no_code_approval_ref(self):
        registry = json.loads((ROOT / "config" / "upbit_asset_identity_registry.json").read_text(encoding="utf-8"))
        self.assertNotIn("code_approval_evidence_ref", registry)
        self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings"))

    def test_real_committed_taxonomy_has_no_code_approval_ref(self):
        taxonomy = json.loads((ROOT / "config" / "upbit_exclusion_taxonomy.json").read_text(encoding="utf-8"))
        self.assertNotIn("code_approval_evidence_ref", taxonomy)
        self.assertFalse(ERB.validate_exact_release(taxonomy, content_field="records"))


class NegativeTests(unittest.TestCase):
    """Any single-byte tamper anywhere in either chain -> False again."""

    def _activated(self, tmp, content_field="mappings"):
        registry, taxonomy, artifacts = build_full_chain(tmp)
        document = registry if content_field == "mappings" else taxonomy
        return artifacts["activate"](document), artifacts

    def test_ninth_market_added_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, _ = self._activated(tmp)
            activated["mappings"]["KRW-DOGE"] = "DOGE"
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_consumer_file_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            artifacts["consumer_path"].write_text("# tampered\n", encoding="utf-8")
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_validator_file_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            artifacts["validator_path"].write_text("# tampered\n", encoding="utf-8")
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_base_candidate_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            candidate = json.loads(artifacts["base_candidate_path"].read_text(encoding="utf-8"))
            candidate["proposed_registry"]["mappings"]["KRW-BTC"] = "TAMPERED"
            _write(artifacts["base_candidate_path"], candidate)
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_content_approval_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            approval = json.loads(artifacts["content_approval_path"].read_text(encoding="utf-8"))
            approval["ratified_by"] = "SOMEONE_ELSE"
            _write(artifacts["content_approval_path"], approval)
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_code_approval_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            approval = json.loads(artifacts["code_approval_path"].read_text(encoding="utf-8"))
            approval["authority"]["order_authorized"] = True
            approval_bytes = json.dumps(approval, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            artifacts["code_approval_path"].write_text(approval_bytes, encoding="utf-8")
            # sha256 pin in `activated` now stale -> mismatch, fails closed.
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_successor_candidate_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            successor = json.loads(artifacts["successor_path"].read_text(encoding="utf-8"))
            successor["exact_hash_cio_approval_present"] = True
            _write(artifacts["successor_path"], successor)
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_freeze_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, _ = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            freeze_path = tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json"
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
            freeze["released_paper_markets"] = ["KRW-BTC"]
            _write(freeze_path, freeze)
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_bytes_removed_then_restored_still_reflects_current_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            original = artifacts["consumer_path"].read_bytes()
            artifacts["consumer_path"].write_bytes(b"# different\n")
            self.assertFalse(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))
            artifacts["consumer_path"].write_bytes(original)
            self.assertTrue(ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp))

    def test_malformed_policy_contract_fails_closed_by_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            contract = json.loads(artifacts["policy_contract_path"].read_text(encoding="utf-8"))
            contract["paper_scope_keys"] = []
            _write(artifacts["policy_contract_path"], contract)
            with self.assertRaises(ERB.ExactReleaseBindingError):
                ERB.validate_exact_release(activated, content_field="mappings", repo_root=tmp)

    def test_approval_status_tamper_alone_does_not_help_a_broken_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = build_full_chain(tmp)
            registry["approval_status"] = "RATIFIED"  # already RATIFIED; no code chain exists
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))


if __name__ == "__main__":
    unittest.main()
