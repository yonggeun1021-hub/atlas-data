#!/usr/bin/env python3
"""P3-12-GOV-05 (v3 design): governance/upbit_exact_release_binding.py.

Two independent, one-way chains rooted in each document's approval
reference (plus the registry's redundant source pin when present) -- no separate mutable
allowlist file, contract-declared field/authority vocabularies actually
enforced (not merely declarative), code-binding paths exactly compared
(not just their sha256), base-candidate pins compared as exact
``{path, file_sha256, payload_sha256}`` tuples, and temporal ordering
that prevents a future approval from retroactively applying to a past
evaluation.
"""
from __future__ import annotations

import copy
import datetime as dt
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

BASE_CANDIDATE_GENERATED_AT = "2026-08-30T10:00:00Z"
CONTENT_APPROVAL_RATIFIED_AT = "2026-08-30T11:00:00Z"
SUCCESSOR_GENERATED_AT = "2026-08-30T12:00:00Z"
CODE_APPROVAL_RATIFIED_AT = "2026-08-30T13:00:00Z"
EVAL_AS_OF = "2026-08-30"


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def build_full_chain(
    tmp: Path, *, mappings=None,
    base_candidate_generated_at=BASE_CANDIDATE_GENERATED_AT,
    content_approval_ratified_at=CONTENT_APPROVAL_RATIFIED_AT,
    successor_generated_at=SUCCESSOR_GENERATED_AT,
    code_approval_ratified_at=CODE_APPROVAL_RATIFIED_AT,
    omit_base_candidate_generated_at=False,
    omit_content_approval_ratified_at=False,
    omit_successor_generated_at=False,
    omit_code_approval_ratified_at=False,
):
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
        "required_content_approval_fields": [
            "schema_version", "approval_status", "ratified_by", "ratified_at_utc",
            "candidate", "approved_scope", "authority",
        ],
        "required_code_approval_fields": [
            "schema_version", "approval_status", "ratified_by", "ratified_at_utc",
            "successor_candidate", "authority",
        ],
        "required_successor_candidate_fields": [
            "schema_version", "generated_at", "base_candidate", "code_binding",
            "release_ready", "exact_hash_cio_approval_present", "authority",
        ],
        "authority_keys": sorted(_AUTHORITY_FALSE),
        "forbidden_authority_keys": sorted(_FORBIDDEN_AUTHORITY_FALSE),
        "paper_scope_keys": sorted(_SCOPE_TRUE),
        "code_binding_labels": ["consumer_file", "validator_file", "policy_contract", "release_builder"],
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
    release_builder_path = tmp / "identity" / "upbit_exact_release_binding_release.py"
    release_builder_path.parent.mkdir(parents=True, exist_ok=True)
    release_builder_path.write_text("# synthetic release builder placeholder\n", encoding="utf-8")

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
    if not omit_base_candidate_generated_at:
        base_candidate["generated_at"] = base_candidate_generated_at
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
    if not omit_content_approval_ratified_at:
        content_approval["ratified_at_utc"] = content_approval_ratified_at
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
            "release_builder": {"path": "identity/upbit_exact_release_binding_release.py", "sha256": ERB.file_sha256(release_builder_path)},
        },
        "release_ready": False,
        "exact_hash_cio_approval_present": False,
        "authority": dict(_AUTHORITY_FALSE),
    }
    if not omit_successor_generated_at:
        successor["generated_at"] = successor_generated_at
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
    if not omit_code_approval_ratified_at:
        code_approval["ratified_at_utc"] = code_approval_ratified_at
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
        # Only a genuine, verify_code_chain()-produced resolution activates
        # the code chain -- this must exactly match what that function
        # would independently compute, never hand-assembled differently.
        "code_approval_resolution": {
            "code_approval_evidence_ref": code_approval_relative,
            "code_approval_evidence_sha256": code_approval_hash,
            "ratified_at_utc": code_approval.get("ratified_at_utc"),
            "successor_candidate_path": successor_relative,
            "successor_candidate_file_sha256": ERB.file_sha256(successor_path),
            "successor_candidate_payload_sha256": successor["payload_sha256"],
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
        "release_builder_path": release_builder_path,
        "base_candidate_path": base_candidate_path,
        "content_approval_path": content_approval_path,
        "successor_path": successor_path,
        "code_approval_path": code_approval_path,
        "activate": activate,
    }
    return registry_doc, taxonomy_doc, artifacts


class ActivatedChainTests(unittest.TestCase):
    """Both chains present, consistent, and correctly time-ordered ->
    effective, exactly at/after the ratification date."""

    def test_activated_registry_and_taxonomy_validate_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, artifacts = build_full_chain(tmp)
            self.assertTrue(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))
            self.assertTrue(ERB.validate_exact_release(
                artifacts["activate"](taxonomy), content_field="records", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_activated_taxonomy_without_redundant_source_pin_validates_true(self):
        """The shipped taxonomy has no source_candidate_packet field.

        Its canonical base-candidate pin must be resolved from the exact,
        hash-verified content approval rather than manufactured by a test
        fixture or required as a taxonomy-only schema addition.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, taxonomy, artifacts = build_full_chain(tmp)
            del taxonomy["source_candidate_packet"]
            self.assertTrue(ERB.validate_exact_release(
                artifacts["activate"](taxonomy), content_field="records",
                evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_explicit_taxonomy_source_pin_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, taxonomy, artifacts = build_full_chain(tmp)
            taxonomy["source_candidate_packet"]["payload_sha256"] = "0" * 64
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](taxonomy), content_field="records",
                evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_activated_registry_is_exactly_the_eight_approved_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp)
            activated = artifacts["activate"](registry)
            self.assertTrue(ERB.validate_exact_release(
                activated, content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))
            self.assertEqual(activated["mappings"], MARKETS_TO_IDS)
            self.assertEqual(len(activated["mappings"]), 8)

    def test_effective_exactly_on_ratification_date_not_only_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp)
            activated = artifacts["activate"](registry)
            # code_approval ratified at 2026-08-30T13:00:00Z -> effective for
            # evaluation_as_of == "2026-08-30" itself, not just strictly later.
            self.assertTrue(ERB.validate_exact_release(
                activated, content_field="mappings", evaluation_as_of="2026-08-30", repo_root=tmp,
            ))


class UnactivatedIsPendingTests(unittest.TestCase):
    """Content chain alone (no code_approval_evidence_ref) is NOT enough --
    this is the exact shape every real committed document has right now."""

    def test_content_chain_alone_without_code_approval_stays_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, _ = build_full_chain(tmp)
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp))
            self.assertFalse(ERB.validate_exact_release(taxonomy, content_field="records", evaluation_as_of=EVAL_AS_OF, repo_root=tmp))

    def test_explicit_null_code_approval_ref_stays_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = build_full_chain(tmp)
            registry["code_approval_evidence_ref"] = None
            registry["code_approval_evidence_sha256"] = None
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp))


class RealRepoStaysPendingTests(unittest.TestCase):
    def test_real_committed_registry_has_no_code_approval_ref(self):
        registry = json.loads((ROOT / "config" / "upbit_asset_identity_registry.json").read_text(encoding="utf-8"))
        self.assertNotIn("code_approval_evidence_ref", registry)
        self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", evaluation_as_of="2026-08-30"))

    def test_real_committed_taxonomy_has_no_code_approval_ref(self):
        taxonomy = json.loads((ROOT / "config" / "upbit_exclusion_taxonomy.json").read_text(encoding="utf-8"))
        self.assertNotIn("code_approval_evidence_ref", taxonomy)
        self.assertFalse(ERB.validate_exact_release(taxonomy, content_field="records", evaluation_as_of="2026-08-30"))

    def test_real_committed_taxonomy_content_chain_resolves_without_source_pin(self):
        taxonomy = json.loads((ROOT / "config" / "upbit_exclusion_taxonomy.json").read_text(encoding="utf-8"))
        self.assertNotIn("source_candidate_packet", taxonomy)
        contract = ERB.load_policy_contract()
        ok, candidate, canonical_pin = ERB.verify_content_chain(
            taxonomy, content_field="records", evaluation_as_of="2026-08-31",
            contract=contract, repo_root=ROOT,
        )
        self.assertTrue(ok)
        self.assertIsNotNone(candidate)
        self.assertEqual(
            canonical_pin,
            {
                "path": "data/observations/upbit_paper_identity_hardening_candidate/2026-08-30/20260830T111117Z/packet.json",
                "file_sha256": "770c722ffa3d9c185ad7629396037cdec1154bad38624cc9d887af3878bcd186",
                "payload_sha256": "bbc73029ec00dc4b6e3762d69d96f18efa4b2fbd611830c2bd71c976ef405abd",
            },
        )


class TemporalOrderingTests(unittest.TestCase):
    """CIO P1: a future approval must never apply retroactively to a past
    evaluation, and an approval can never precede the thing it approves."""

    def test_missing_content_approval_ratified_at_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp, omit_content_approval_ratified_at=True)
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_missing_base_candidate_generated_at_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp, omit_base_candidate_generated_at=True)
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_missing_successor_generated_at_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp, omit_successor_generated_at=True)
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_missing_code_approval_ratified_at_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp, omit_code_approval_ratified_at=True)
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_future_code_approval_does_not_apply_to_past_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(
                tmp, code_approval_ratified_at="2026-09-15T00:00:00Z",
            )
            activated = artifacts["activate"](registry)
            # Effective for an evaluation on/after the (later) ratification date...
            self.assertTrue(ERB.validate_exact_release(
                activated, content_field="mappings", evaluation_as_of="2026-09-15", repo_root=tmp,
            ))
            # ...but NOT retroactively for a past evaluation date.
            self.assertFalse(ERB.validate_exact_release(
                activated, content_field="mappings", evaluation_as_of="2026-08-30", repo_root=tmp,
            ))

    def test_future_content_approval_does_not_apply_to_past_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(
                tmp, content_approval_ratified_at="2026-09-15T00:00:00Z",
                successor_generated_at="2026-09-15T01:00:00Z",
                code_approval_ratified_at="2026-09-15T02:00:00Z",
            )
            activated = artifacts["activate"](registry)
            self.assertFalse(ERB.validate_exact_release(
                activated, content_field="mappings", evaluation_as_of="2026-08-30", repo_root=tmp,
            ))
            self.assertTrue(ERB.validate_exact_release(
                activated, content_field="mappings", evaluation_as_of="2026-09-15", repo_root=tmp,
            ))

    def test_approval_predating_its_own_candidate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(
                tmp,
                base_candidate_generated_at="2026-08-30T23:00:00Z",
                content_approval_ratified_at="2026-08-30T10:00:00Z",  # BEFORE the candidate it approves
            )
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_code_approval_predating_its_own_successor_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(
                tmp,
                successor_generated_at="2026-08-30T23:00:00Z",
                code_approval_ratified_at="2026-08-30T10:00:00Z",  # BEFORE the successor it approves
            )
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))


class NegativeTests(unittest.TestCase):
    """Any single-byte tamper anywhere in either chain -> False again."""

    def _activated(self, tmp, content_field="mappings"):
        registry, taxonomy, artifacts = build_full_chain(tmp)
        document = registry if content_field == "mappings" else taxonomy
        return artifacts["activate"](document), artifacts

    def _ok(self, activated, tmp, content_field="mappings"):
        return ERB.validate_exact_release(activated, content_field=content_field, evaluation_as_of=EVAL_AS_OF, repo_root=tmp)

    def test_ninth_market_added_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, _ = self._activated(tmp)
            activated["mappings"]["KRW-DOGE"] = "DOGE"
            self.assertFalse(self._ok(activated, tmp))

    def test_consumer_file_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            artifacts["consumer_path"].write_text("# tampered\n", encoding="utf-8")
            self.assertFalse(self._ok(activated, tmp))

    def test_validator_file_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            artifacts["validator_path"].write_text("# tampered\n", encoding="utf-8")
            self.assertFalse(self._ok(activated, tmp))

    def test_base_candidate_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            candidate = json.loads(artifacts["base_candidate_path"].read_text(encoding="utf-8"))
            candidate["proposed_registry"]["mappings"]["KRW-BTC"] = "TAMPERED"
            _write(artifacts["base_candidate_path"], candidate)
            self.assertFalse(self._ok(activated, tmp))

    def test_content_approval_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            approval = json.loads(artifacts["content_approval_path"].read_text(encoding="utf-8"))
            approval["ratified_by"] = "SOMEONE_ELSE"
            _write(artifacts["content_approval_path"], approval)
            self.assertFalse(self._ok(activated, tmp))

    def test_code_approval_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            approval = json.loads(artifacts["code_approval_path"].read_text(encoding="utf-8"))
            approval["authority"]["order_authorized"] = True
            _write(artifacts["code_approval_path"], approval)
            # sha256 pin in `activated` now stale -> mismatch, fails closed.
            self.assertFalse(self._ok(activated, tmp))

    def test_code_approval_authority_missing_key_fails(self):
        """CIO P1: authority declaring only one key (the rest silently
        dropped) must fail the exact-key-set check, not just an
        all-False-among-present-keys check."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            approval = json.loads(artifacts["code_approval_path"].read_text(encoding="utf-8"))
            approval["authority"] = {"trading_authorized": False}
            _write(artifacts["code_approval_path"], approval)
            self.assertFalse(self._ok(activated, tmp))

    def test_successor_candidate_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            successor = json.loads(artifacts["successor_path"].read_text(encoding="utf-8"))
            successor["exact_hash_cio_approval_present"] = True
            _write(artifacts["successor_path"], successor)
            self.assertFalse(self._ok(activated, tmp))

    def test_forged_code_binding_path_with_correct_sha_fails(self):
        """CIO P1: the validator must exact-compare the DECLARED path, not
        just trust whatever sha256 is present -- a forged path alongside a
        byte-correct sha256 must still fail."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            successor = json.loads(artifacts["successor_path"].read_text(encoding="utf-8"))
            successor["code_binding"]["consumer_file"]["path"] = "some/other/fake/path.py"
            successor["payload_sha256"] = ERB.payload_sha256(
                {k: v for k, v in successor.items() if k != "payload_sha256"}
            )
            _write(artifacts["successor_path"], successor)
            # re-sign the code approval's pin to the re-hashed successor
            approval = json.loads(artifacts["code_approval_path"].read_text(encoding="utf-8"))
            approval["successor_candidate"]["file_sha256"] = ERB.file_sha256(artifacts["successor_path"])
            approval["successor_candidate"]["payload_sha256"] = successor["payload_sha256"]
            _write(artifacts["code_approval_path"], approval)
            activated["code_approval_evidence_sha256"] = ERB.file_sha256(artifacts["code_approval_path"])
            self.assertFalse(self._ok(activated, tmp))

    def test_required_field_missing_from_policy_contract_fails_closed_by_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            contract = json.loads(artifacts["policy_contract_path"].read_text(encoding="utf-8"))
            del contract["required_code_approval_fields"]
            contract["payload_sha256"] = ERB.payload_sha256(
                {k: v for k, v in contract.items() if k != "payload_sha256"}
            )
            _write(artifacts["policy_contract_path"], contract)
            with self.assertRaises(ERB.ExactReleaseBindingError):
                ERB.validate_exact_release(activated, content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp)

    def test_different_base_path_same_bytes_fails(self):
        """CIO P1: successor.base_candidate must match the content chain's
        exact (path, file_sha256, payload_sha256) tuple -- a byte-identical
        copy of the base candidate at a DIFFERENT path must not pass."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            duplicate_path = tmp / "duplicate_base_candidate.json"
            duplicate_path.write_bytes(artifacts["base_candidate_path"].read_bytes())
            successor = json.loads(artifacts["successor_path"].read_text(encoding="utf-8"))
            successor["base_candidate"]["path"] = str(duplicate_path.relative_to(tmp))
            successor["payload_sha256"] = ERB.payload_sha256(
                {k: v for k, v in successor.items() if k != "payload_sha256"}
            )
            _write(artifacts["successor_path"], successor)
            approval = json.loads(artifacts["code_approval_path"].read_text(encoding="utf-8"))
            approval["successor_candidate"]["file_sha256"] = ERB.file_sha256(artifacts["successor_path"])
            approval["successor_candidate"]["payload_sha256"] = successor["payload_sha256"]
            _write(artifacts["code_approval_path"], approval)
            activated["code_approval_evidence_sha256"] = ERB.file_sha256(artifacts["code_approval_path"])
            self.assertFalse(self._ok(activated, tmp))

    def test_missing_required_successor_field_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            successor = json.loads(artifacts["successor_path"].read_text(encoding="utf-8"))
            del successor["release_ready"]
            successor["payload_sha256"] = ERB.payload_sha256(
                {k: v for k, v in successor.items() if k != "payload_sha256"}
            )
            _write(artifacts["successor_path"], successor)
            approval = json.loads(artifacts["code_approval_path"].read_text(encoding="utf-8"))
            approval["successor_candidate"]["file_sha256"] = ERB.file_sha256(artifacts["successor_path"])
            approval["successor_candidate"]["payload_sha256"] = successor["payload_sha256"]
            _write(artifacts["code_approval_path"], approval)
            activated["code_approval_evidence_sha256"] = ERB.file_sha256(artifacts["code_approval_path"])
            self.assertFalse(self._ok(activated, tmp))

    def test_freeze_tamper_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, _ = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            freeze_path = tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json"
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
            freeze["released_paper_markets"] = ["KRW-BTC"]
            _write(freeze_path, freeze)
            self.assertFalse(self._ok(activated, tmp))

    def test_bytes_removed_then_restored_still_reflects_current_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            original = artifacts["consumer_path"].read_bytes()
            artifacts["consumer_path"].write_bytes(b"# different\n")
            self.assertFalse(self._ok(activated, tmp))
            artifacts["consumer_path"].write_bytes(original)
            self.assertTrue(self._ok(activated, tmp))

    def test_malformed_policy_contract_fails_closed_by_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            contract = json.loads(artifacts["policy_contract_path"].read_text(encoding="utf-8"))
            contract["paper_scope_keys"] = []
            _write(artifacts["policy_contract_path"], contract)
            with self.assertRaises(ERB.ExactReleaseBindingError):
                ERB.validate_exact_release(activated, content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp)

    def test_approval_status_tamper_alone_does_not_help_a_broken_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = build_full_chain(tmp)
            registry["approval_status"] = "RATIFIED"  # already RATIFIED; no code chain exists
            self.assertFalse(ERB.validate_exact_release(registry, content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp))

    def test_freeze_missing_code_approval_resolution_block_fails(self):
        """CIO P1: hand-adding the two code_approval_evidence_* ref fields
        to a document, WITHOUT the freeze document also carrying a
        matching, builder-produced code_approval_resolution block, must
        still fail closed -- this is exactly the 'skip the release
        builder' bypass."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            freeze_path = tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json"
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
            del freeze["code_approval_resolution"]
            _write(freeze_path, freeze)
            self.assertFalse(self._ok(activated, tmp))

    def test_freeze_code_approval_resolution_tampered_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            freeze_path = tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json"
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
            freeze["code_approval_resolution"]["ratified_at_utc"] = "2099-01-01T00:00:00Z"
            _write(freeze_path, freeze)
            self.assertFalse(self._ok(activated, tmp))

    def test_absolute_code_approval_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            activated, artifacts = self._activated(tmp)
            self.assertTrue(self._ok(activated, tmp))
            activated["code_approval_evidence_ref"] = str(artifacts["code_approval_path"])  # absolute
            self.assertFalse(self._ok(activated, tmp))

    def test_equal_content_ratification_and_generation_timestamps_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(
                tmp, base_candidate_generated_at="2026-08-30T10:00:00Z",
                content_approval_ratified_at="2026-08-30T10:00:00Z",  # EQUAL, not strictly after
            )
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_equal_code_ratification_and_generation_timestamps_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(
                tmp, successor_generated_at="2026-08-30T12:00:00Z",
                code_approval_ratified_at="2026-08-30T12:00:00Z",  # EQUAL, not strictly after
            )
            self.assertFalse(ERB.validate_exact_release(
                artifacts["activate"](registry), content_field="mappings", evaluation_as_of=EVAL_AS_OF, repo_root=tmp,
            ))

    def test_real_shape_six_field_source_pin_still_validates(self):
        """CIO P1: a real registry/taxonomy's source_candidate_packet
        legally carries evaluation_as_of/review_status/snapshot_date in
        addition to the 3 identity keys -- the successor's simpler
        3-field base_candidate pin must still match it via the canonical
        3-tuple, without requiring those extra keys to be replicated."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = build_full_chain(tmp)
            registry["source_candidate_packet"] = dict(
                registry["source_candidate_packet"],
                evaluation_as_of="2026-08-30", review_status="RATIFIED", snapshot_date="2026-08-30",
            )
            activated = artifacts["activate"](registry)
            self.assertTrue(self._ok(activated, tmp))


if __name__ == "__main__":
    unittest.main()
