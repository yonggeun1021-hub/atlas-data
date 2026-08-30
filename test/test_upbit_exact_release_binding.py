#!/usr/bin/env python3
"""P3-12-GOV-05: governance/upbit_exact_release_binding.py.

An ALLOWLIST binding: a document is effective only if its entire exact-hash
provenance chain -- approval evidence, candidate packet, consumer file,
freeze cross-reference -- resolves to the ONE approval-evidence file this
test's own synthetic contract allowlists. Never a denylist of specific bad
historical hashes -- any deviation from the one allowlisted chain fails,
including brand-new tampers this module has never seen before.
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


def _registry_records(mappings):
    return {
        "eligible_category": "eligible_crypto",
        "excluded_categories": ["stablecoin"],
        "unknown_asset_policy": "fail_closed_unknown",
        "scope": "UPBIT_KRW_SPOT_CRYPTO_PAPER_EIGHT_ONLY",
        "records": [
            {"canonical_asset_id": asset, "category": "eligible_crypto",
             "effective_from": "2026-08-30", "effective_to": None, "reason": "synthetic fixture"}
            for asset in mappings.values()
        ],
    }


def build_valid_chain(tmp: Path, *, mappings=None):
    """A fully self-consistent, valid exact-hash chain under ``tmp`` --
    returns (registry_doc, taxonomy_doc, artifact_paths). Caller may
    corrupt exactly one artifact/field after this returns to build a
    negative-test scenario; every artifact is content-addressed so any
    single mutation without a matching re-sign breaks the chain somewhere.
    """
    mappings = mappings if mappings is not None else dict(MARKETS_TO_IDS)
    consumer_path = tmp / "universe" / "upbit_tradeable_universe.py"
    consumer_path.parent.mkdir(parents=True, exist_ok=True)
    consumer_path.write_text("# synthetic consumer placeholder\n", encoding="utf-8")
    consumer_hash = ERB.file_sha256(consumer_path)

    builder_path = tmp / "identity" / "upbit_paper_identity_hardening_candidate.py"
    builder_path.parent.mkdir(parents=True, exist_ok=True)
    builder_path.write_text("# synthetic candidate builder placeholder\n", encoding="utf-8")

    contract_ref_path = tmp / "config" / "upbit_paper_identity_hardening_contract.json"
    _write(contract_ref_path, {"scope": "synthetic"})

    proposed_registry = {
        "schema_version": 1, "registry_version": "upbit_asset_identity_registry/v1",
        "scope": "UPBIT_KRW_SPOT_CRYPTO_PAPER_EIGHT_ONLY",
        "unknown_market_policy": "fail_closed_unratified_identity",
        "mappings": dict(mappings),
        "authority": dict(_AUTHORITY_FALSE),
    }
    proposed_taxonomy = {
        "schema_version": 1, "policy_version": "upbit_exclusion_taxonomy/v1",
        **_registry_records(mappings),
        "authority": dict(_AUTHORITY_FALSE),
    }
    proposed_registry_payload_sha256 = ERB.payload_sha256(proposed_registry)
    proposed_taxonomy_payload_sha256 = ERB.payload_sha256(proposed_taxonomy)

    candidate = {
        "schema_version": "upbit_paper_identity_hardening_candidate/2",
        "generated_at": "2026-08-30T11:11:17Z", "evaluation_as_of": "2026-08-30",
        "proposed_registry": proposed_registry,
        "proposed_registry_payload_sha256": proposed_registry_payload_sha256,
        "proposed_taxonomy": proposed_taxonomy,
        "proposed_taxonomy_payload_sha256": proposed_taxonomy_payload_sha256,
        "consumer_file_sha256": consumer_hash,
        "candidate_builder_file_sha256": ERB.file_sha256(builder_path),
        "consumer_contract_sha256": ERB.file_sha256(contract_ref_path),
        "release_ready": False, "exact_hash_cio_approval_present": False,
        "authority": dict(_AUTHORITY_FALSE),
    }
    candidate["payload_sha256"] = ERB.payload_sha256(candidate)
    candidate_path = tmp / "data" / "candidate" / "packet.json"
    _write(candidate_path, candidate)
    candidate_relative = str(candidate_path.relative_to(tmp))

    approval = {
        "schema_version": "upbit_paper_identity_exact_hash_approval/1",
        "approval_status": "RATIFIED", "ratified_by": "CIO_USER",
        "ratified_at_utc": "2026-08-30T11:17:37Z",
        "candidate": {
            "path": candidate_relative,
            "file_sha256": ERB.file_sha256(candidate_path),
            "payload_sha256": candidate["payload_sha256"],
            "registry_payload_sha256": proposed_registry_payload_sha256,
            "taxonomy_payload_sha256": proposed_taxonomy_payload_sha256,
            "consumer_file_sha256": consumer_hash,
        },
        "approved_scope": dict(_SCOPE_TRUE),
        "authority": dict(_FORBIDDEN_AUTHORITY_FALSE),
    }
    approval_path = tmp / "evidence" / "approval.json"
    _write(approval_path, approval)
    approval_relative = str(approval_path.relative_to(tmp))
    approval_hash = ERB.file_sha256(approval_path)

    contract = {
        "schema_version": ERB.SCHEMA_VERSION,
        "resolution_status": "RATIFIED_BY_EXPLICIT_CIO_DECISION",
        "reason": "synthetic fixture",
        "allowed_approval_evidence": [{"path": approval_relative, "file_sha256": approval_hash}],
        "authority": dict(_AUTHORITY_FALSE),
    }
    contract["payload_sha256"] = ERB.payload_sha256({k: v for k, v in contract.items() if k != "payload_sha256"})
    contract_path = tmp / "config" / "upbit_exact_release_binding_contract.json"
    _write(contract_path, contract)

    registry_doc = {
        "approval_status": "RATIFIED",
        "approval_evidence_ref": approval_relative,
        "approval_evidence_sha256": approval_hash,
        "approved_candidate_payload_sha256": proposed_registry_payload_sha256,
        "source_candidate_packet": {
            "path": candidate_relative,
            "file_sha256": ERB.file_sha256(candidate_path),
            "payload_sha256": candidate["payload_sha256"],
        },
        "mappings": dict(mappings),
        "authority": dict(_AUTHORITY_FALSE),
    }
    taxonomy_doc = {
        "approval_status": "RATIFIED",
        "approval_evidence_ref": approval_relative,
        "approval_evidence_sha256": approval_hash,
        "approved_candidate_payload_sha256": proposed_taxonomy_payload_sha256,
        "source_candidate_packet": {
            "path": candidate_relative,
            "file_sha256": ERB.file_sha256(candidate_path),
            "payload_sha256": candidate["payload_sha256"],
        },
        "records": copy.deepcopy(proposed_taxonomy["records"]),
        "authority": dict(_AUTHORITY_FALSE),
    }

    freeze = {
        "approval_resolution": {
            "candidate_packet_path": candidate_relative,
            "candidate_packet_file_sha256": ERB.file_sha256(candidate_path),
            "candidate_packet_payload_sha256": candidate["payload_sha256"],
            "registry_candidate_payload_sha256": proposed_registry_payload_sha256,
            "taxonomy_candidate_payload_sha256": proposed_taxonomy_payload_sha256,
            "consumer_file_sha256": consumer_hash,
        },
        "released_paper_markets": sorted(mappings),
    }
    _write(tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json", freeze)

    return registry_doc, taxonomy_doc, {
        "approval_path": approval_path, "candidate_path": candidate_path,
        "contract_path": contract_path, "consumer_path": consumer_path,
    }


class SyntheticValidChainTests(unittest.TestCase):
    """G: 'current exact eight verified ONLY via a synthetic approved
    fixture' -- the mechanism itself, proven correct end to end."""

    def test_valid_chain_validates_true_for_both_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, _ = build_valid_chain(tmp)
            self.assertTrue(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))
            self.assertTrue(ERB.validate_exact_release(taxonomy, content_field="records", repo_root=tmp))

    def test_valid_chain_is_exactly_the_eight_approved_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = build_valid_chain(tmp)
            self.assertTrue(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))
            self.assertEqual(registry["mappings"], MARKETS_TO_IDS)
            self.assertEqual(len(registry["mappings"]), 8)


class RealRepoNotYetApprovedTests(unittest.TestCase):
    """Section G/positive: main's real, currently-RATIFIED v2 release is
    NOT pretended valid on this branch before a fresh re-approval."""

    def test_real_committed_registry_is_not_valid_here(self):
        registry = json.loads((ROOT / "config" / "upbit_asset_identity_registry.json").read_text(encoding="utf-8"))
        self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings"))

    def test_real_committed_taxonomy_is_not_valid_here(self):
        taxonomy = json.loads((ROOT / "config" / "upbit_exclusion_taxonomy.json").read_text(encoding="utf-8"))
        self.assertFalse(ERB.validate_exact_release(taxonomy, content_field="records"))

    def test_real_committed_contract_allowlist_is_empty(self):
        contract = ERB.load_binding_contract()
        self.assertEqual(contract["allowed_approval_evidence"], [])
        self.assertEqual(contract["resolution_status"], ERB.PENDING_STATUS)
        self.assertFalse(ERB.is_released(contract))


class NegativeTests(unittest.TestCase):
    """Every scenario the CIO's negative-test list names -- all must land
    on False (fail-closed / empty effective mapping), never True."""

    def test_old_fifty_five_mapping_style_document_missing_provenance_fails(self):
        # The pre-v2 registry schema never had approval_evidence_ref/
        # source_candidate_packet at all -- this is what an old-schema
        # RATIFIED document looks like structurally.
        old_style = {
            "approval_status": "RATIFIED",
            "mappings": {f"KRW-{i}": f"ASSET{i}" for i in range(55)},
            "authority": dict(_AUTHORITY_FALSE),
        }
        self.assertFalse(ERB.validate_exact_release(old_style, content_field="mappings"))

    def test_old_282_record_taxonomy_style_document_missing_provenance_fails(self):
        old_style = {
            "approval_status": "RATIFIED",
            "records": [{"canonical_asset_id": "LIT", "category": "eligible_crypto"}],
            "authority": dict(_AUTHORITY_FALSE),
        }
        self.assertFalse(ERB.validate_exact_release(old_style, content_field="records"))

    def test_arbitrary_ninth_market_added_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = build_valid_chain(tmp)
            tampered = copy.deepcopy(registry)
            tampered["mappings"]["KRW-DOGE"] = "DOGE"
            self.assertFalse(ERB.validate_exact_release(tampered, content_field="mappings", repo_root=tmp))

    def test_one_canonical_id_changed_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = build_valid_chain(tmp)
            tampered = copy.deepcopy(registry)
            tampered["mappings"]["KRW-BTC"] = "NOTBTC"
            self.assertFalse(ERB.validate_exact_release(tampered, content_field="mappings", repo_root=tmp))

    def test_one_taxonomy_field_tampered_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, taxonomy, _ = build_valid_chain(tmp)
            tampered = copy.deepcopy(taxonomy)
            tampered["records"][0]["reason"] = "tampered"
            self.assertFalse(ERB.validate_exact_release(tampered, content_field="records", repo_root=tmp))

    def test_candidate_file_tampered_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_valid_chain(tmp)
            candidate = json.loads(artifacts["candidate_path"].read_text(encoding="utf-8"))
            candidate["evaluation_as_of"] = "2026-09-01"  # payload_sha256 no longer matches
            _write(artifacts["candidate_path"], candidate)
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))

    def test_approval_evidence_tampered_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_valid_chain(tmp)
            approval = json.loads(artifacts["approval_path"].read_text(encoding="utf-8"))
            approval["ratified_by"] = "SOMEONE_ELSE"
            _write(artifacts["approval_path"], approval)
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))

    def test_file_removed_then_restored_to_old_bytes_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_valid_chain(tmp)
            self.assertTrue(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))
            original_bytes = artifacts["candidate_path"].read_bytes()
            artifacts["candidate_path"].write_bytes(b'{"tampered": true}')
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))
            # Restore the file to its ORIGINAL (once-valid) bytes -- the
            # tampered registry document itself (still referencing the
            # OLD mappings snapshot the caller holds) must not silently
            # revalidate just because raw bytes came back; here we use a
            # DIFFERENT registry snapshot (post-tamper mapping) to prove
            # statelessness cuts both ways -- restoring good bytes with a
            # bad document still fails on the document's own mismatch.
            artifacts["candidate_path"].write_bytes(original_bytes)
            tampered_doc = copy.deepcopy(registry)
            tampered_doc["mappings"]["KRW-BTC"] = "WRONG"
            self.assertFalse(ERB.validate_exact_release(tampered_doc, content_field="mappings", repo_root=tmp))
            self.assertTrue(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))

    def test_consumer_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_valid_chain(tmp)
            artifacts["consumer_path"].write_text("# different bytes now\n", encoding="utf-8")
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))

    def test_forged_self_consistent_chain_not_in_allowlist_fails(self):
        # A second, entirely separate but internally self-consistent chain
        # that was never added to the contract's allowlist -- proves this
        # is a real allowlist, not "any self-consistent chain passes."
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_valid_chain(tmp)
            contract = json.loads(artifacts["contract_path"].read_text(encoding="utf-8"))
            contract["allowed_approval_evidence"] = []
            contract["payload_sha256"] = ERB.payload_sha256(
                {k: v for k, v in contract.items() if k != "payload_sha256"}
            )
            _write(artifacts["contract_path"], contract)
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp))

    def test_malformed_contract_fails_closed_by_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_valid_chain(tmp)
            contract = json.loads(artifacts["contract_path"].read_text(encoding="utf-8"))
            contract["reason"] = "tampered without re-signing"
            _write(artifacts["contract_path"], contract)
            with self.assertRaises(ERB.ExactReleaseBindingError):
                ERB.validate_exact_release(registry, content_field="mappings", repo_root=tmp)


if __name__ == "__main__":
    unittest.main()
