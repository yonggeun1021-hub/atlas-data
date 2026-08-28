#!/usr/bin/env python3
"""071050 proposal-only packets and fail-closed review counterexamples."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import kis_071050_proposal as proposal_module
from identity import kis_071050_proposal_review as review_module
from identity.kis_071050_proposal import (
    AUTHORITY_ALL_FALSE,
    PROPOSAL_STATUS,
    all_071050_proposals,
    instrument_identity_proposal_071050,
    payload_sha256,
    source_alias_proposal_071050,
)
from identity.kis_071050_proposal_review import (
    Kis071050ProposalReviewError,
    _verify_atlas_identity_semantics,
    _verify_public_master_archive,
    _verify_public_master_exact_row,
    review_alias_proposal,
    review_identity_proposal,
    validate_alias_proposal,
    validate_identity_proposal,
)


def _rehash(packet: dict) -> None:
    packet["proposalSha256"] = payload_sha256(
        {key: value for key, value in packet.items() if key != "proposalSha256"}
    )


class ProposalShapeTests(unittest.TestCase):
    def test_two_separate_proposal_only_packets_have_all_false_authority(self):
        packets = all_071050_proposals()
        self.assertEqual([row["proposalKind"] for row in packets], [
            "ISSUER_INSTRUMENT_LISTING_IDENTITY", "EXACT_SOURCE_ALIAS",
        ])
        for packet in packets:
            self.assertEqual(packet["proposalStatus"], PROPOSAL_STATUS)
            self.assertEqual(packet["authority"], AUTHORITY_ALL_FALSE)
            self.assertTrue(all(value is False for value in packet["authority"].values()))
            self.assertFalse(packet["canonicalAuthorityConfigMutated"])

    def test_identity_claim_is_exact_instrument_not_generic_digits(self):
        claim = instrument_identity_proposal_071050()["claim"]
        self.assertEqual(claim["canonicalIssuerId"], "DART:00432102")
        self.assertEqual(claim["canonicalInstrumentId"], "KRX:071050:COMMON")
        self.assertEqual(claim["listingId"], "XKRX:071050")
        self.assertEqual(claim["standardProductNumber"], "KR7071050009")
        self.assertEqual(claim["instrumentType"], "COMMON_STOCK")
        self.assertIn("NO_GENERIC", claim["scope"])

    def test_public_master_exact_raw_row_is_hash_bound_and_parseable(self):
        packet = instrument_identity_proposal_071050()
        evidence = packet["evidence"][2]
        raw_row = base64.b64decode(evidence["rawBase64"], validate=True)
        self.assertEqual(len(raw_row), 288)
        self.assertEqual(hashlib.sha256(raw_row).hexdigest(), evidence["rowSha256"])
        self.assertEqual(evidence["rowLineNumber"], 1035)
        _verify_public_master_exact_row(packet)

    def test_alias_claim_is_exact_source_pair_not_generic_pdno_rule(self):
        claim = source_alias_proposal_071050()["claim"]
        self.assertEqual(claim["sourceName"], "kis_paper_domestic_balance")
        self.assertEqual(claim["sourceAssetId"], "071050")
        self.assertIn("NO_GENERIC_SIX_DIGIT_PDNO_RULE", claim["scope"])

    def test_failed_live_get_is_only_negative_fail_closed_evidence(self):
        failure = source_alias_proposal_071050()["evidence"][2]
        self.assertEqual(failure["status"], "NOT_OBTAINED_FAIL_CLOSED")
        self.assertTrue(failure["brokerReadAttempted"])
        self.assertFalse(failure["positiveEvidenceAccepted"])
        self.assertFalse(failure["orderSubmissionAttempted"])

    def test_committed_artifacts_are_exact_generator_bytes(self):
        expected = {
            "kis_071050_instrument_identity_proposal.json": instrument_identity_proposal_071050(),
            "kis_071050_source_alias_proposal.json": source_alias_proposal_071050(),
        }
        root = ROOT / "evidence" / "identity" / "proposals"
        for name, packet in expected.items():
            with self.subTest(name=name):
                self.assertEqual(json.loads((root / name).read_text()), packet)

    def test_authority_configs_remain_without_071050_kis_alias(self):
        identity = json.loads((ROOT / "config" / "canonical_security_identity.json").read_text())
        self.assertFalse(any(
            row.get("source_name") == "kis_paper_domestic_balance"
            and row.get("source_asset_id") == "071050"
            for row in identity["source_aliases"]
        ))


class CounterexampleTests(unittest.TestCase):
    def assert_rehashed_rejected(self, packet: dict, validator, code: str) -> None:
        _rehash(packet)
        with self.assertRaisesRegex(Kis071050ProposalReviewError, code):
            validator(packet)

    def test_generic_six_digit_claim_only_rejected(self):
        packet = source_alias_proposal_071050()
        packet["claim"] = {"sourceAssetIdPattern": "^[0-9]{6}$"}
        self.assert_rehashed_rejected(packet, validate_alias_proposal, "PROPOSAL_DIFFERS")

    def test_different_pdno_rejected(self):
        packet = source_alias_proposal_071050()
        packet["claim"]["sourceAssetId"] = "005930"
        self.assert_rehashed_rejected(packet, validate_alias_proposal, "PROPOSAL_DIFFERS")

    def test_master_archive_master_and_row_hash_mismatches_each_rejected(self):
        for field in ("archiveSha256", "masterSha256", "rowSha256"):
            packet = instrument_identity_proposal_071050()
            packet["evidence"][2][field] = "0" * 64
            with self.subTest(field=field):
                self.assert_rehashed_rejected(
                    packet, validate_identity_proposal, "PROPOSAL_DIFFERS"
                )

    def test_reviewer_independently_binds_archive_master_row_tuple(self):
        for field in ("archiveSha256", "masterSha256", "rowSha256", "rowLineNumber"):
            packet = instrument_identity_proposal_071050()
            packet["evidence"][2][field] = 1 if field == "rowLineNumber" else "0" * 64
            with self.subTest(field=field), self.assertRaisesRegex(
                Kis071050ProposalReviewError,
                "PUBLIC_MASTER_ARCHIVE_MASTER_ROW_BINDING_MISMATCH",
            ):
                _verify_public_master_exact_row(packet)

    def test_master_row_field_mismatches_each_rejected(self):
        for field, wrong in {
            "standardProductNumber": "KR0000000000",
            "koreanName": "다른회사",
            "preferredStockClassCode": "1",
        }.items():
            packet = instrument_identity_proposal_071050()
            packet["evidence"][2]["observation"][field] = wrong
            with self.subTest(field=field):
                self.assert_rehashed_rejected(packet, validate_identity_proposal, "PROPOSAL_DIFFERS")

    def test_pinned_kis_source_hash_mismatch_rejected(self):
        packet = source_alias_proposal_071050()
        packet["evidence"][0]["files"][0]["contentSha256"] = "1" * 64
        self.assert_rehashed_rejected(packet, validate_alias_proposal, "PROPOSAL_DIFFERS")

    def test_identity_evidence_cannot_be_reused_as_alias_evidence(self):
        packet = source_alias_proposal_071050()
        packet["evidence"] = copy.deepcopy(instrument_identity_proposal_071050()["evidence"])
        self.assert_rehashed_rejected(packet, validate_alias_proposal, "PROPOSAL_DIFFERS")

    def test_alias_evidence_cannot_be_reused_as_identity_evidence(self):
        packet = instrument_identity_proposal_071050()
        packet["evidence"] = copy.deepcopy(source_alias_proposal_071050()["evidence"])
        self.assert_rehashed_rejected(packet, validate_identity_proposal, "PROPOSAL_DIFFERS")

    def test_proposal_rehash_cannot_self_ratify(self):
        packet = instrument_identity_proposal_071050()
        packet["proposalStatus"] = "RATIFIED"
        _rehash(packet)
        with self.assertRaisesRegex(Kis071050ProposalReviewError, "FORBIDDEN_STATUS"):
            validate_identity_proposal(packet)

    def test_broker_verified_recursively_rejected(self):
        packet = source_alias_proposal_071050()
        packet["evidence"][2]["status"] = "BROKER_VERIFIED"
        _rehash(packet)
        with self.assertRaisesRegex(Kis071050ProposalReviewError, "FORBIDDEN_STATUS"):
            validate_alias_proposal(packet)

    def test_nested_authority_true_recursively_rejected(self):
        packet = source_alias_proposal_071050()
        packet["evidence"][2]["order_authorized"] = True
        _rehash(packet)
        with self.assertRaisesRegex(Kis071050ProposalReviewError, "AUTHORITY_TRUE_FORBIDDEN"):
            validate_alias_proposal(packet)

    def test_nested_config_mutation_recursively_rejected(self):
        packet = instrument_identity_proposal_071050()
        packet["evidence"][2]["canonicalAuthorityConfigMutated"] = True
        _rehash(packet)
        with self.assertRaisesRegex(Kis071050ProposalReviewError, "CONFIG_MUTATION_FORBIDDEN"):
            validate_identity_proposal(packet)

    def test_positive_live_read_claim_rejected_even_when_rehashed(self):
        packet = source_alias_proposal_071050()
        packet["evidence"][2]["positiveEvidenceAccepted"] = True
        self.assert_rehashed_rejected(packet, validate_alias_proposal, "PROPOSAL_DIFFERS")


class IndependentReviewTests(unittest.TestCase):
    def test_without_external_checkouts_review_stays_incomplete(self):
        identity = instrument_identity_proposal_071050()
        alias = source_alias_proposal_071050()
        self.assertEqual(review_identity_proposal(identity)["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertEqual(
            review_alias_proposal(alias, identity_packet=identity)["reviewStatus"],
            "REVIEW_INCOMPLETE",
        )

    def test_archive_reproduction_is_required_even_when_other_sources_reproduce(self):
        identity = instrument_identity_proposal_071050()
        resolution = {"resolutionStatus": "EXACT_GIT_BYTES_REPRODUCED"}
        with mock.patch(
            "identity.kis_071050_proposal_review._resolve_git_evidence",
            return_value=resolution,
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_required_fragments",
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_atlas_identity_semantics",
        ):
            result = review_identity_proposal(
                identity, official_checkout=Path("/official"), atlas_checkout=Path("/atlas")
            )
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("PUBLIC_MASTER_ARCHIVE_REPRODUCTION_REQUIRED", result["reasons"])

    def test_wrong_archive_bytes_cannot_make_review_ready(self):
        identity = instrument_identity_proposal_071050()
        result = review_identity_proposal(identity, public_master_archive=b"not-the-archive")
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertTrue(any(
            "PUBLIC_MASTER_ARCHIVE_HASH_MISMATCH" in reason
            for reason in result["reasons"]
        ))

    @staticmethod
    def _archive(member: str, master: bytes) -> bytes:
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr(member, master)
        return target.getvalue()

    def test_wrong_archive_member_is_rejected(self):
        packet = instrument_identity_proposal_071050()
        archive = self._archive("wrong.mst", b"master")
        digest = hashlib.sha256(archive).hexdigest()
        packet["evidence"][2]["archiveSha256"] = digest
        binding = dict(review_module._PUBLIC_MASTER_BINDING, archiveSha256=digest)
        with mock.patch.object(review_module, "_PUBLIC_MASTER_BINDING", binding), self.assertRaisesRegex(
            Kis071050ProposalReviewError, "PUBLIC_MASTER_ARCHIVE_MEMBER_INVALID",
        ):
            _verify_public_master_archive(packet, archive)

    def test_wrong_master_hash_is_rejected(self):
        packet = instrument_identity_proposal_071050()
        archive = self._archive("kospi_code.mst", b"wrong-master")
        digest = hashlib.sha256(archive).hexdigest()
        packet["evidence"][2]["archiveSha256"] = digest
        binding = dict(review_module._PUBLIC_MASTER_BINDING, archiveSha256=digest)
        with mock.patch.object(review_module, "_PUBLIC_MASTER_BINDING", binding), self.assertRaisesRegex(
            Kis071050ProposalReviewError, "PUBLIC_MASTER_HASH_MISMATCH",
        ):
            _verify_public_master_archive(packet, archive)

    def test_row_not_present_in_verified_master_is_rejected(self):
        packet = instrument_identity_proposal_071050()
        master = b"\n".join([b"not-a-master-row"] * 1035) + b"\n"
        archive = self._archive("kospi_code.mst", master)
        archive_digest = hashlib.sha256(archive).hexdigest()
        master_digest = hashlib.sha256(master).hexdigest()
        packet["evidence"][2].update({
            "archiveSha256": archive_digest,
            "masterSha256": master_digest,
        })
        binding = dict(
            review_module._PUBLIC_MASTER_BINDING,
            archiveSha256=archive_digest,
            masterSha256=master_digest,
        )
        with mock.patch.object(review_module, "_PUBLIC_MASTER_BINDING", binding), self.assertRaisesRegex(
            Kis071050ProposalReviewError, "PUBLIC_MASTER_EXACT_SYMBOL_NOT_UNIQUE",
        ):
            _verify_public_master_archive(packet, archive)

    def test_exact_external_reproduction_can_only_make_review_ready(self):
        identity = instrument_identity_proposal_071050()
        alias = source_alias_proposal_071050()
        resolution = {"resolutionStatus": "EXACT_GIT_BYTES_REPRODUCED"}
        with mock.patch(
            "identity.kis_071050_proposal_review._resolve_git_evidence",
            return_value=resolution,
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_required_fragments",
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_atlas_identity_semantics",
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_public_master_archive",
        ):
            identity_result = review_identity_proposal(
                identity, official_checkout=Path("/official"), atlas_checkout=Path("/atlas"),
                public_master_archive=b"verified-by-mock",
            )
            alias_result = review_alias_proposal(
                alias, identity_packet=identity, official_checkout=Path("/official")
            )
        self.assertEqual(identity_result["reviewStatus"], "REVIEW_READY_FOR_CIO")
        self.assertEqual(alias_result["reviewStatus"], "REVIEW_READY_FOR_CIO")
        for result in (identity_result, alias_result):
            self.assertEqual(result["proposalStatus"], PROPOSAL_STATUS)
            self.assertEqual(result["authority"], AUTHORITY_ALL_FALSE)
            self.assertFalse(result["canonicalAuthorityConfigMutated"])

    def test_rehashed_hardcoded_observation_cannot_ready_without_matching_raw_row(self):
        tampered_master = copy.deepcopy(proposal_module.PUBLIC_MASTER_OBSERVATION)
        tampered_master["observation"]["koreanName"] = "조작된회사"
        resolution = {"resolutionStatus": "EXACT_GIT_BYTES_REPRODUCED"}
        with mock.patch.object(
            proposal_module, "PUBLIC_MASTER_OBSERVATION", tampered_master,
        ), mock.patch(
            "identity.kis_071050_proposal_review._resolve_git_evidence",
            return_value=resolution,
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_required_fragments",
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_atlas_identity_semantics",
        ), mock.patch(
            "identity.kis_071050_proposal_review._verify_public_master_archive",
        ):
            packet = instrument_identity_proposal_071050()
            result = review_identity_proposal(
                packet, official_checkout=Path("/official"), atlas_checkout=Path("/atlas"),
                public_master_archive=b"verified-by-mock",
            )
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertTrue(any(
            reason.startswith("PUBLIC_MASTER_EXACT_ROW_FAILED:")
            for reason in result["reasons"]
        ))

    def test_current_pinned_atlas_semantics_are_reproduced(self):
        _verify_atlas_identity_semantics(ROOT)


if __name__ == "__main__":
    unittest.main()
