#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio_risk import portfolio_snapshot_v2
from portfolio_risk.kis_valuation_semantic_proposal import (
    AUTHORITY_ALL_FALSE,
    KIS_OFFICIAL_COMMIT,
    KIS_OFFICIAL_REPO,
    PROPOSAL_STATUS,
    TARGET_CONTRACT_VERSION,
    payload_sha256,
    valuation_semantic_mapping_proposal,
)
from portfolio_risk.kis_valuation_semantic_review import (
    BUY_CAPACITY_ATTESTATION_VERSION,
    RELATIONSHIP_ATTESTATION_VERSION,
    KisValuationSemanticReviewError,
    review_valuation_semantic_mapping_proposal,
)


def _rehash(value: dict, field: str) -> dict:
    value[field] = payload_sha256({key: item for key, item in value.items() if key != field})
    return value


def relationship_attestation(**overrides) -> dict:
    value = {
        "contractVersion": RELATIONSHIP_ATTESTATION_VERSION,
        "status": "COMPLETE_RELATIONSHIP_OBSERVATION",
        "snapshotSchemaVersion": "kis_paper_full_account_snapshot/3",
        "positionValuationComplete": True,
        "accountValuationPresent": True,
        "positionMarketValueSumMatchesValuationSum": True,
        "positionUnrealizedPlSumMatchesUnrealizedPlSum": True,
        "securitiesValuationMatchesPositionMarketValueSum": True,
        "totalValuationEqualsCashDepositPlusSecuritiesValuation": True,
        "netAssetEqualsCashDepositPlusPositionMarketValue": True,
        "semanticMappingRatified": False,
        "orderSubmissionAttempted": False,
        "sourceRecordSha256": "1" * 64,
        "capturedAt": "2026-08-28T14:20:00Z",
        "availableAt": "2026-08-28T14:20:00Z",
        "accountBindingHash": "2" * 64,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    value.update(overrides)
    return _rehash(value, "attestationSha256")


def buy_capacity_attestation(**overrides) -> dict:
    value = {
        "contractVersion": BUY_CAPACITY_ATTESTATION_VERSION,
        "status": "CAPTURED_BUY_CAPACITY_COMPLETE",
        "snapshotSchemaVersion": "kis_paper_buy_capacity_snapshot/1",
        "capacityKisFields": [
            "nrcvb_buy_amt", "nrcvb_buy_qty", "ord_psbl_cash", "psbl_qty_calc_unpr",
        ],
        "orderableCashPresent": True,
        "noReceivableBuyAmountPresent": True,
        "noReceivableBuyQuantityPresent": True,
        "noReceivableBuyAmountNotAboveOrderableCash": True,
        "quantityCalculationPriceMatchesQuote": True,
        "semanticMappingRatified": False,
        "orderSubmissionAttempted": False,
        "sourceRecordSha256": "3" * 64,
        "capturedAt": "2026-08-28T14:21:00Z",
        "availableAt": "2026-08-28T14:21:00Z",
        "accountBindingHash": "2" * 64,
        "instrumentBindingHash": "4" * 64,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    value.update(overrides)
    return _rehash(value, "attestationSha256")


def _exact_resolution(*args, **kwargs):
    return {
        "resolutionStatus": "EXACT_GIT_BYTES_REPRODUCED",
        "repo": KIS_OFFICIAL_REPO,
        "commitSha": KIS_OFFICIAL_COMMIT,
    }


class ProposalShapeTests(unittest.TestCase):
    def test_proposal_is_unratified_all_false_and_additive_v3_only(self):
        proposal = valuation_semantic_mapping_proposal()
        self.assertEqual(proposal["proposalStatus"], PROPOSAL_STATUS)
        self.assertEqual(proposal["targetContractVersion"], TARGET_CONTRACT_VERSION)
        self.assertEqual(proposal["authority"], AUTHORITY_ALL_FALSE)
        self.assertFalse(proposal["canonicalAuthorityConfigMutated"])
        self.assertFalse(proposal["existingPortfolioAccountFactV2Mutated"])
        self.assertEqual(portfolio_snapshot_v2.SCHEMA_VERSION, "portfolio_account_fact/2")
        self.assertEqual(portfolio_snapshot_v2.PROVIDER_AUTHORITY_STATUS, "PROPOSED_UNRATIFIED")

    def test_exact_semantics_keep_account_and_instrument_capacity_separate(self):
        mappings = {
            row["rawKisField"]: row["targetPath"]
            for row in valuation_semantic_mapping_proposal()["mappings"]
        }
        self.assertEqual(mappings["nass_amt"], "account.netAssetKrw")
        self.assertEqual(mappings["dnca_tot_amt"], "account.cashDepositTotalKrw")
        self.assertEqual(mappings["evlu_amt"], "positions[].marketValueKrw")
        self.assertEqual(mappings["evlu_pfls_amt"], "positions[].unrealizedPlKrw")
        self.assertEqual(
            mappings["nrcvb_buy_amt"],
            "instrumentBuyCapacity[].noReceivableBuyAmountKrw",
        )
        self.assertNotIn("ord_psbl_cash", mappings)
        self.assertNotIn("account.buyingPower", set(mappings.values()))

    def test_no_generic_six_digit_alias_or_order_authority_is_smuggled_in(self):
        rendered = str(valuation_semantic_mapping_proposal())
        self.assertNotIn("6-digit", rendered)
        self.assertNotIn("same digits", rendered)
        self.assertNotIn("ORDER_APPROVED", rendered)


class ReviewTests(unittest.TestCase):
    def review_ready(self, proposal=None, relationship=None, buy_capacity=None):
        with mock.patch(
            "portfolio_risk.kis_valuation_semantic_review._resolve_git_evidence",
            side_effect=_exact_resolution,
        ):
            return review_valuation_semantic_mapping_proposal(
                proposal or valuation_semantic_mapping_proposal(),
                official_checkout=Path("/independent/detached/kis"),
                relationship_attestation=relationship or relationship_attestation(),
                buy_capacity_attestation=buy_capacity or buy_capacity_attestation(),
            )

    def test_all_three_evidence_layers_are_required(self):
        proposal = valuation_semantic_mapping_proposal()
        result = review_valuation_semantic_mapping_proposal(proposal)
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("EXTERNAL_SOURCE_BYTES_REPRODUCTION_REQUIRED", result["reasons"])
        self.assertIn(
            f"PRIVATE_ATTESTATION_REQUIRED:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )
        self.assertIn(
            f"PRIVATE_ATTESTATION_REQUIRED:{BUY_CAPACITY_ATTESTATION_VERSION}",
            result["reasons"],
        )

    def test_exact_bytes_and_complete_attestations_reach_review_only_ready(self):
        result = self.review_ready()
        self.assertEqual(result["reviewStatus"], "REVIEW_READY_FOR_CIO")
        self.assertEqual(result["authority"], AUTHORITY_ALL_FALSE)

    def test_rehashed_mapping_change_cannot_redefine_equity(self):
        proposal = copy.deepcopy(valuation_semantic_mapping_proposal())
        proposal["mappings"][0]["rawKisField"] = "tot_evlu_amt"
        _rehash(proposal, "proposalSha256")
        result = self.review_ready(proposal=proposal)
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT", result["reasons"])

    def test_rehashed_account_wide_buying_power_smuggling_is_rejected(self):
        proposal = copy.deepcopy(valuation_semantic_mapping_proposal())
        proposal["mappings"][4]["targetPath"] = "account.buyingPower"
        _rehash(proposal, "proposalSha256")
        result = self.review_ready(proposal=proposal)
        self.assertIn("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT", result["reasons"])

    def test_failed_operational_relationship_is_incomplete(self):
        relationship = relationship_attestation(
            netAssetEqualsCashDepositPlusPositionMarketValue=False
        )
        result = self.review_ready(relationship=relationship)
        self.assertIn(
            "VALUATION_RELATIONSHIP_NOT_PROVEN:netAssetEqualsCashDepositPlusPositionMarketValue",
            result["reasons"],
        )

    def test_wrong_or_incomplete_buy_capacity_is_incomplete(self):
        capacity = buy_capacity_attestation(
            noReceivableBuyAmountNotAboveOrderableCash=False,
            capacityKisFields=["nrcvb_buy_amt"],
        )
        result = self.review_ready(buy_capacity=capacity)
        self.assertIn("BUY_CAPACITY_KIS_FIELDS_INCOMPLETE", result["reasons"])
        self.assertIn(
            "BUY_CAPACITY_RELATIONSHIP_NOT_PROVEN:noReceivableBuyAmountNotAboveOrderableCash",
            result["reasons"],
        )

    def test_attestations_cannot_self_ratify_or_include_private_identity(self):
        relationship = relationship_attestation(
            semanticMappingRatified=True,
            accountIdentityHash="a" * 64,
        )
        result = self.review_ready(relationship=relationship)
        self.assertIn(
            f"PRIVATE_ATTESTATION_SELF_RATIFICATION_REJECTED:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )
        self.assertIn(
            f"PRIVATE_ATTESTATION_SENSITIVE_FIELD_FORBIDDEN:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )
        self.assertIn("VALUATION_RELATIONSHIP_ATTESTATION_FIELDS_INVALID", result["reasons"])

    def test_authority_numeric_bool_aliases_are_rejected(self):
        proposal = copy.deepcopy(valuation_semantic_mapping_proposal())
        proposal["authority"] = {
            key: int(value) for key, value in proposal["authority"].items()
        }
        _rehash(proposal, "proposalSha256")
        result = self.review_ready(proposal=proposal)
        self.assertIn("AUTHORITY_NOT_ALL_FALSE", result["reasons"])
        self.assertIn("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT", result["reasons"])

        relationship = relationship_attestation()
        relationship["authority"] = {
            key: int(value) for key, value in relationship["authority"].items()
        }
        _rehash(relationship, "attestationSha256")
        result = self.review_ready(relationship=relationship)
        self.assertIn(
            f"PRIVATE_ATTESTATION_AUTHORITY_INVALID:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )

    def test_money_free_attestation_rejects_any_extra_value_field(self):
        relationship = relationship_attestation(equityKrw=123)
        result = self.review_ready(relationship=relationship)
        self.assertIn("VALUATION_RELATIONSHIP_ATTESTATION_FIELDS_INVALID", result["reasons"])

    def test_private_attestations_must_bind_to_the_same_account(self):
        capacity = buy_capacity_attestation(accountBindingHash="9" * 64)
        result = self.review_ready(buy_capacity=capacity)
        self.assertIn("PRIVATE_ATTESTATION_ACCOUNT_BINDING_MISMATCH", result["reasons"])

    def test_attestation_source_hash_and_timing_are_fail_closed(self):
        relationship = relationship_attestation(
            sourceRecordSha256="bad",
            capturedAt="2026-08-28T14:22:00Z",
            availableAt="2026-08-28T14:21:00Z",
        )
        result = self.review_ready(relationship=relationship)
        self.assertIn(
            f"PRIVATE_ATTESTATION_SHA256_INVALID:{RELATIONSHIP_ATTESTATION_VERSION}:sourceRecordSha256",
            result["reasons"],
        )
        self.assertIn(
            f"PRIVATE_ATTESTATION_AVAILABLE_BEFORE_CAPTURED:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )

    def test_recursive_authority_injection_is_rejected_before_hash_readiness(self):
        proposal = copy.deepcopy(valuation_semantic_mapping_proposal())
        proposal["mappings"][0]["approval_status"] = "RATIFIED"
        _rehash(proposal, "proposalSha256")
        with self.assertRaisesRegex(
            KisValuationSemanticReviewError,
            "EMBEDDED_AUTHORITY_FIELD_FORBIDDEN",
        ):
            self.review_ready(proposal=proposal)

    def test_attestation_rehash_is_required(self):
        relationship = relationship_attestation()
        relationship["positionValuationComplete"] = False
        result = self.review_ready(relationship=relationship)
        self.assertIn(
            f"PRIVATE_ATTESTATION_HASH_MISMATCH:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
