#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio_risk import kis_valuation_authority as authority_module
from portfolio_risk.kis_valuation_freshness_policy_proposal import (
    AUTHORITY_ALL_FALSE as FRESHNESS_PROPOSAL_AUTHORITY,
    PROPOSAL_STATUS as FRESHNESS_PROPOSAL_STATUS,
    freshness_policy_proposal,
)
from portfolio_risk.kis_valuation_semantic_proposal import (
    AUTHORITY_ALL_FALSE as SEMANTIC_PROPOSAL_AUTHORITY,
    PROPOSAL_STATUS as SEMANTIC_PROPOSAL_STATUS,
    valuation_semantic_mapping_proposal,
)


class KisValuationAuthorityRatifiedStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = authority_module.load_authority()

    def test_exact_singleton_semantic_and_freshness_records(self):
        self.assertEqual(
            len(self.authority["valuationSemanticAuthorityRecords"]), 1
        )
        self.assertEqual(len(self.authority["freshnessPolicyAuthorityRecords"]), 1)
        semantic = self.authority["valuationSemanticAuthorityRecords"][0]
        freshness = self.authority["freshnessPolicyAuthorityRecords"][0]
        self.assertEqual(semantic["approvalStatus"], "RATIFIED")
        self.assertEqual(freshness["approvalStatus"], "RATIFIED")
        for row in (semantic, freshness):
            self.assertEqual(row["ratifiedAt"], "2026-08-29T03:20:00Z")
            self.assertEqual(row["firstSeenAt"], row["ratifiedAt"])
            self.assertEqual(row["effectiveFrom"], row["ratifiedAt"])
            self.assertEqual(row["providerTuple"], authority_module.PROVIDER_TUPLE)
            self.assertEqual(row["targetContractVersion"], "portfolio_account_fact/3")

    def test_proposals_remain_unmodified_unratified_and_all_false(self):
        semantic = valuation_semantic_mapping_proposal()
        freshness = freshness_policy_proposal()
        self.assertEqual(semantic["proposalStatus"], SEMANTIC_PROPOSAL_STATUS)
        self.assertEqual(freshness["proposalStatus"], FRESHNESS_PROPOSAL_STATUS)
        self.assertEqual(semantic["authority"], SEMANTIC_PROPOSAL_AUTHORITY)
        self.assertEqual(freshness["authority"], FRESHNESS_PROPOSAL_AUTHORITY)
        self.assertFalse(semantic["canonicalAuthorityConfigMutated"])
        self.assertFalse(freshness["canonicalAuthorityConfigMutated"])

    def test_authority_is_narrow_and_never_grants_consumption_or_trading(self):
        semantic = self.authority["valuationSemanticAuthorityRecords"][0]
        freshness = self.authority["freshnessPolicyAuthorityRecords"][0]
        self.assertTrue(semantic["authority"]["valuationSemanticAuthorized"])
        self.assertFalse(semantic["authority"]["freshnessPolicyAuthorized"])
        self.assertTrue(freshness["authority"]["freshnessPolicyAuthorized"])
        self.assertFalse(freshness["authority"]["valuationSemanticAuthorized"])
        for row in (semantic, freshness):
            for field in (
                "accountFactAuthorized", "riskInputAuthorized", "stageAuthorized",
                "buyAuthorized", "actionAuthorized", "orderAuthorized",
                "productionAuthorized", "tradingAuthorized", "realCapitalAuthorized",
            ):
                self.assertIs(row["authority"][field], False, field)

    def test_freshness_ratification_is_explicitly_normative_not_empirical(self):
        row = self.authority["freshnessPolicyAuthorityRecords"][0]
        self.assertEqual(row["maxSourceAgeSeconds"], 300)
        self.assertEqual(row["maxPairGapSeconds"], 120)
        self.assertEqual(
            row["approvalBasis"],
            "CIO_NORMATIVE_CONSERVATIVE_GOVERNANCE_CHOICE_NOT_EMPIRICALLY_DERIVED",
        )
        self.assertEqual(row["livePairSampleCountAtRatification"], 0)
        self.assertFalse(row["atomicCaptureSessionBindingPresentAtRatification"])
        self.assertEqual(
            row["empiricalValidationStatus"],
            "NOT_ESTABLISHED_AT_RATIFICATION_MONITOR_SHADOW_ONLY",
        )
        self.assertFalse(row["retroactiveApplicationPermitted"])

    def test_approval_packets_and_source_hashes_match_exact_bytes(self):
        for row in (
            self.authority["valuationSemanticAuthorityRecords"][0],
            self.authority["freshnessPolicyAuthorityRecords"][0],
        ):
            path = ROOT / row["approvalEvidenceRef"]
            raw = path.read_bytes()
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(), row["approvalEvidenceSha256"]
            )
            packet = json.loads(raw)
            self.assertEqual(
                packet["approvedBusinessPayloadSha256"],
                row["businessPayloadSha256"],
            )
            self.assertEqual(packet["ratifiedAt"], row["ratifiedAt"])
            self.assertEqual(packet["authorityKind"], row["authorityKind"])
            for source in packet["sourceEvidence"]:
                self.assertEqual(
                    hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest(),
                    source["sha256"],
                )

    def test_real_git_provenance_resolves_only_from_first_seen_instant(self):
        too_early = "2026-08-29T03:19:59Z"
        usable = "2026-08-29T03:20:00Z"
        for resolver in (
            authority_module.resolve_semantic_authority,
            authority_module.resolve_freshness_authority,
        ):
            before = resolver(decision_at=too_early, authority=self.authority)
            self.assertEqual(
                before["status"],
                authority_module.NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE,
            )
            result = resolver(decision_at=usable, authority=self.authority)
            self.assertEqual(result["status"], authority_module.RESOLVED, result)
            self.assertEqual(result["realUsableFrom"], usable)
            self.assertFalse(result["authority"]["accountFactAuthorized"])
            self.assertFalse(result["authority"]["riskInputAuthorized"])
            self.assertFalse(result["authority"]["orderAuthorized"])

    def test_git_utc_z_committer_timestamp_is_reproduced_exactly(self):
        semantic = authority_module.resolve_semantic_authority(
            decision_at="2026-08-29T03:20:00Z", authority=self.authority
        )
        self.assertEqual(semantic["realUsableFrom"], "2026-08-29T03:20:00Z")

    def test_wrong_provider_tuple_or_target_never_borrows_authority(self):
        wrong_tuple = {**authority_module.PROVIDER_TUPLE, "currency": "USD"}
        for resolver in (
            authority_module.resolve_semantic_authority,
            authority_module.resolve_freshness_authority,
        ):
            result = resolver(
                decision_at="2026-08-29T03:20:00Z",
                authority=self.authority,
                provider_tuple=wrong_tuple,
            )
            self.assertEqual(
                result["status"], authority_module.NOT_COMPUTABLE_NO_AUTHORITY_RECORD
            )
            self.assertFalse(any(result["authority"].values()))
            wrong_target = resolver(
                decision_at="2026-08-29T03:20:00Z",
                authority=self.authority,
                target_contract_version="portfolio_account_fact/2",
            )
            self.assertEqual(
                wrong_target["status"],
                authority_module.NOT_COMPUTABLE_NO_AUTHORITY_RECORD,
            )


class KisValuationAuthorityCounterExamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = authority_module.load_authority()

    def test_rehashed_semantic_mapping_mutation_is_rejected(self):
        value = copy.deepcopy(self.authority)
        value.pop("_sourcePath")
        row = value["valuationSemanticAuthorityRecords"][0]
        row["approvedMappings"][0]["targetPath"] = "account.buyingPower"
        row["businessPayloadSha256"] = authority_module.payload_sha256(
            authority_module._business_payload(row)
        )
        with self.assertRaisesRegex(
            authority_module.KisValuationAuthorityError,
            "SEMANTIC_MAPPING_SET_MISMATCH",
        ):
            authority_module.validate_authority_document(value)

    def test_rehashed_freshness_threshold_or_live_sample_claim_is_rejected(self):
        for field, altered in (
            ("maxPairGapSeconds", 119),
            ("maxSourceAgeSeconds", 299),
            ("livePairSampleCountAtRatification", 1),
            ("atomicCaptureSessionBindingPresentAtRatification", True),
            ("empiricalValidationStatus", "EMPIRICALLY_VALIDATED"),
        ):
            with self.subTest(field=field):
                value = copy.deepcopy(self.authority)
                value.pop("_sourcePath")
                row = value["freshnessPolicyAuthorityRecords"][0]
                row[field] = altered
                row["businessPayloadSha256"] = authority_module.payload_sha256(
                    authority_module._business_payload(row)
                )
                with self.assertRaisesRegex(
                    authority_module.KisValuationAuthorityError,
                    "FRESHNESS_POLICY_FIELD_INVALID|FRESHNESS_PROPOSAL_HASH_MISMATCH",
                ):
                    authority_module.validate_authority_document(value)

    def test_account_fact_risk_or_trading_authority_injection_is_rejected(self):
        for collection in (
            "valuationSemanticAuthorityRecords", "freshnessPolicyAuthorityRecords"
        ):
            for field in (
                "accountFactAuthorized", "riskInputAuthorized", "stageAuthorized",
                "buyAuthorized", "actionAuthorized", "orderAuthorized",
                "productionAuthorized", "tradingAuthorized", "realCapitalAuthorized",
            ):
                with self.subTest(collection=collection, field=field):
                    value = copy.deepcopy(self.authority)
                    value.pop("_sourcePath")
                    row = value[collection][0]
                    row["authority"][field] = True
                    row["businessPayloadSha256"] = authority_module.payload_sha256(
                        authority_module._business_payload(row)
                    )
                    with self.assertRaisesRegex(
                        authority_module.KisValuationAuthorityError,
                        "AUTHORITY_BOUNDARY_INVALID",
                    ):
                        authority_module.validate_authority_document(value)

    def test_in_memory_clone_cannot_resolve_without_git_backing(self):
        value = copy.deepcopy(self.authority)
        value.pop("_sourcePath")
        for resolver in (
            authority_module.resolve_semantic_authority,
            authority_module.resolve_freshness_authority,
        ):
            with self.assertRaisesRegex(
                authority_module.KisValuationAuthorityError,
                "AUTHORITY_SOURCE_PATH_REQUIRED",
            ):
                resolver(decision_at="2026-08-29T03:20:00Z", authority=value)

    def test_memory_tamper_cannot_borrow_real_source_path(self):
        value = copy.deepcopy(self.authority)
        row = value["freshnessPolicyAuthorityRecords"][0]
        row["maxPairGapSeconds"] = 119
        row["businessPayloadSha256"] = authority_module.payload_sha256(
            authority_module._business_payload(row)
        )
        with self.assertRaisesRegex(
            authority_module.KisValuationAuthorityError,
            "FRESHNESS_POLICY_FIELD_INVALID|AUTHORITY_MEMORY_DISK_MISMATCH",
        ):
            authority_module.resolve_freshness_authority(
                decision_at="2026-08-29T03:20:00Z", authority=value
            )

    def test_mutable_or_abbreviated_trusted_commit_is_rejected(self):
        for candidate in ("HEAD", "main", "002dcb1"):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(
                    authority_module.KisValuationAuthorityError,
                    "AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE",
                ):
                    authority_module.resolve_semantic_authority(
                        decision_at="2026-08-29T03:20:00Z",
                        authority=self.authority,
                        trusted_commit=candidate,
                    )


if __name__ == "__main__":
    unittest.main()
