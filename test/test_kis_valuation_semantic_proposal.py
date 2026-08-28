#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
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


CAPTURED_AT = "2026-08-28T18:00:00Z"
ACCOUNT_IDENTITY_HASH = "a" * 64


def _private_bytes(value: dict) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def full_account_source_record() -> dict:
    value = {
        "schemaVersion": "kis_paper_full_account_snapshot/3",
        "provider": "KIS_PAPER_ACCOUNT",
        "accountScope": "KOREA",
        "verificationStatus": "BROKER_VERIFIED",
        "accountIdentityHash": ACCOUNT_IDENTITY_HASH,
        "capturedAt": CAPTURED_AT,
        "availableAt": CAPTURED_AT,
        "balanceCash": {"kisField": "ord_psbl_cash", "valueKrw": 900_000},
        "positions": [{
            "sourceName": "kis_paper_domestic_balance",
            "sourceAssetId": "071050",
            "holdingQuantity": 10,
            "orderableQuantity": 10,
            "observedValuation": {
                "marketValueKrw": {"kisField": "evlu_amt", "valueKrw": 700_000},
                "unrealizedPlKrw": {
                    "kisField": "evlu_pfls_amt", "valueKrw": -5_000,
                },
            },
        }],
        "observedAccountValuation": {
            "cashDepositTotalKrw": {"kisField": "dnca_tot_amt", "valueKrw": 1_000_000},
            "securitiesValuationKrw": {"kisField": "scts_evlu_amt", "valueKrw": 700_000},
            "totalValuationKrw": {"kisField": "tot_evlu_amt", "valueKrw": 1_700_000},
            "netAssetKrw": {"kisField": "nass_amt", "valueKrw": 1_700_000},
            "valuationSumKrw": {"kisField": "evlu_amt_smtl_amt", "valueKrw": 700_000},
            "unrealizedPlSumKrw": {
                "kisField": "evlu_pfls_smtl_amt", "valueKrw": -5_000,
            },
        },
        "rawResponseSha256": "b" * 64,
    }
    value["recordSha256"] = payload_sha256(value)
    return value


def buy_capacity_source_record() -> dict:
    value = {
        "schemaVersion": "kis_paper_buy_capacity_snapshot/1",
        "provider": "KIS_PAPER_ACCOUNT",
        "accountScope": "KOREA",
        "verificationStatus": "BROKER_VERIFIED",
        "accountIdentityHash": ACCOUNT_IDENTITY_HASH,
        "capturedAt": CAPTURED_AT,
        "availableAt": CAPTURED_AT,
        "instrument": {
            "sourceName": "kis_paper_domestic_balance",
            "sourceAssetId": "071050",
        },
        "referenceQuote": {"kisField": "stck_prpr", "value": 70_000},
        "buyCapacity": {
            "orderableCashKrw": {"kisField": "ord_psbl_cash", "value": 900_000},
            "noReceivableBuyAmountKrw": {
                "kisField": "nrcvb_buy_amt", "value": 840_000,
            },
            "noReceivableBuyQuantity": {
                "kisField": "nrcvb_buy_qty", "value": 12,
            },
            "quantityCalculationPriceKrw": {
                "kisField": "psbl_qty_calc_unpr", "value": 70_000,
            },
        },
        "query": {
            "trId": "VTTC8908R", "orderDivision": "01",
            "cmaIncluded": False, "overseasIncluded": False,
        },
        "quoteRawResponseSha256": "c" * 64,
        "capacityRawResponseSha256": "d" * 64,
    }
    value["recordSha256"] = payload_sha256(value)
    return value


def _account_binding_hash(account_identity_hash: str) -> str:
    return payload_sha256({
        "bindingKind": "KIS_PAPER_ACCOUNT_REVIEW_ONLY",
        "accountIdentityHash": account_identity_hash,
    })


def _instrument_binding_hash(source_name: str, source_asset_id: str) -> str:
    return payload_sha256({
        "bindingKind": "KIS_PAPER_INSTRUMENT_REVIEW_ONLY",
        "sourceName": source_name,
        "sourceAssetId": source_asset_id,
    })


def relationship_attestation(**overrides) -> dict:
    source = full_account_source_record()
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
        "sourceRecordSha256": source["recordSha256"],
        "capturedAt": source["capturedAt"],
        "availableAt": source["availableAt"],
        "accountBindingHash": _account_binding_hash(source["accountIdentityHash"]),
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    value.update(overrides)
    return _rehash(value, "attestationSha256")


def buy_capacity_attestation(**overrides) -> dict:
    source = buy_capacity_source_record()
    instrument = source["instrument"]
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
        "sourceRecordSha256": source["recordSha256"],
        "capturedAt": source["capturedAt"],
        "availableAt": source["availableAt"],
        "accountBindingHash": _account_binding_hash(source["accountIdentityHash"]),
        "instrumentBindingHash": _instrument_binding_hash(
            instrument["sourceName"], instrument["sourceAssetId"]
        ),
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


OFFICIAL_BLOBS = {
    "examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py": (
        "COLUMN_MAPPING = {"
        "'nass_amt':'순자산금액','dnca_tot_amt':'예수금총금액',"
        "'evlu_amt':'평가금액','evlu_pfls_amt':'평가손익금액',"
        "'tot_evlu_amt':'총평가금액'}\n"
    ).encode("utf-8"),
    "examples_llm/domestic_stock/inquire_balance/inquire_balance.py": (
        "API_URL='/uapi/domestic-stock/v1/trading/inquire-balance'\n"
        "def inquire_balance(env_dv):\n"
        "    if env_dv == 'demo': tr_id='VTTC8434R'\n"
        "    body=res.getBody()\n"
        "    output1=body.output1\n"
        "    output2=body.output2\n"
        "    return output1, output2\n"
    ).encode("utf-8"),
    "examples_llm/domestic_stock/inquire_psbl_order/chk_inquire_psbl_order.py": (
        "COLUMN_MAPPING = {"
        "'ord_psbl_cash':'주문가능현금','psbl_qty_calc_unpr':'가능수량계산단가',"
        "'nrcvb_buy_amt':'미수없는매수금액','nrcvb_buy_qty':'미수없는매수수량'}\n"
    ).encode("utf-8"),
    "examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py": (
        "API_URL='/uapi/domestic-stock/v1/trading/inquire-psbl-order'\n"
        "def inquire_psbl_order(env_dv, pdno, ord_unpr):\n"
        "    if env_dv == 'demo': tr_id='VTTC8908R'\n"
        "    params={'PDNO':pdno,'ORD_UNPR':ord_unpr}\n"
        "    body=res.getBody()\n"
        "    return body.output\n"
    ).encode("utf-8"),
}


class ProposalShapeTests(unittest.TestCase):
    def test_proposal_is_unratified_all_false_and_additive_v3_only(self):
        proposal = valuation_semantic_mapping_proposal()
        self.assertEqual(proposal["proposalStatus"], PROPOSAL_STATUS)
        self.assertEqual(proposal["targetContractVersion"], TARGET_CONTRACT_VERSION)
        self.assertEqual(proposal["authority"], AUTHORITY_ALL_FALSE)
        self.assertFalse(proposal["canonicalAuthorityConfigMutated"])
        self.assertFalse(proposal["existingPortfolioAccountFactV2Mutated"])
        self.assertEqual(proposal["freshnessPolicy"], {
            "status": "UNRATIFIED_NO_NUMERIC_LIMIT",
            "maxSourceAgeSeconds": None,
            "maxPairGapSeconds": None,
            "effect": "REVIEW_INCOMPLETE_FAIL_CLOSED",
        })
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
    def review_ready(
        self,
        proposal=None,
        relationship=None,
        buy_capacity=None,
        relationship_source=None,
        buy_capacity_source=None,
        official_blobs=None,
    ):
        with mock.patch(
            "portfolio_risk.kis_valuation_semantic_review._resolve_git_evidence",
            side_effect=_exact_resolution,
        ), mock.patch(
            "portfolio_risk.kis_valuation_semantic_review._read_git_blob",
            side_effect=lambda checkout, path: (official_blobs or OFFICIAL_BLOBS)[path],
        ):
            return review_valuation_semantic_mapping_proposal(
                proposal or valuation_semantic_mapping_proposal(),
                official_checkout=Path("/independent/detached/kis"),
                relationship_attestation=relationship or relationship_attestation(),
                buy_capacity_attestation=buy_capacity or buy_capacity_attestation(),
                relationship_source_record=(
                    relationship_source
                    if relationship_source is not None
                    else _private_bytes(full_account_source_record())
                ),
                buy_capacity_source_record=(
                    buy_capacity_source
                    if buy_capacity_source is not None
                    else _private_bytes(buy_capacity_source_record())
                ),
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
        self.assertIn(
            f"PRIVATE_SOURCE_RECORD_REQUIRED:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )
        self.assertIn(
            f"PRIVATE_SOURCE_RECORD_REQUIRED:{BUY_CAPACITY_ATTESTATION_VERSION}",
            result["reasons"],
        )

    def test_exact_bytes_and_complete_attestations_stay_blocked_without_freshness_policy(self):
        result = self.review_ready()
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertEqual(result["reasons"], [
            "PRIVATE_SOURCE_FRESHNESS_POLICY_UNRATIFIED",
        ])
        self.assertEqual(result["authority"], AUTHORITY_ALL_FALSE)

    def test_old_relationship_and_current_capacity_cannot_claim_freshness(self):
        source = full_account_source_record()
        source["capturedAt"] = "2026-01-01T00:00:00Z"
        source["availableAt"] = "2026-01-01T00:00:00Z"
        source["recordSha256"] = payload_sha256({
            key: value for key, value in source.items() if key != "recordSha256"
        })
        relationship = relationship_attestation(
            sourceRecordSha256=source["recordSha256"],
            capturedAt=source["capturedAt"],
            availableAt=source["availableAt"],
        )
        result = self.review_ready(
            relationship=relationship,
            relationship_source=_private_bytes(source),
        )
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn(
            "PRIVATE_SOURCE_FRESHNESS_POLICY_UNRATIFIED",
            result["reasons"],
        )

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

    def test_self_rehashed_fake_attestations_cannot_replace_private_source_records(self):
        relationship = relationship_attestation(
            sourceRecordSha256="1" * 64,
            accountBindingHash="2" * 64,
            capturedAt="2099-01-01T00:00:00Z",
            availableAt="2099-01-01T00:00:00Z",
        )
        capacity = buy_capacity_attestation(
            sourceRecordSha256="3" * 64,
            accountBindingHash="2" * 64,
            instrumentBindingHash="4" * 64,
            capturedAt="2099-01-01T00:00:00Z",
            availableAt="2099-01-01T00:00:00Z",
        )
        result = self.review_ready(relationship=relationship, buy_capacity=capacity)
        self.assertEqual(result["reviewStatus"], "REVIEW_INCOMPLETE")
        self.assertIn(
            f"PRIVATE_ATTESTATION_SOURCE_RECORD_MISMATCH:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )
        self.assertIn(
            f"PRIVATE_ATTESTATION_ACCOUNT_BINDING_NOT_DERIVED:{BUY_CAPACITY_ATTESTATION_VERSION}",
            result["reasons"],
        )

    def test_private_record_bytes_are_canonical_hash_bound_and_pit_bounded(self):
        source = full_account_source_record()
        source["availableAt"] = "2099-01-01T00:00:00Z"
        source["recordSha256"] = payload_sha256({
            key: value for key, value in source.items() if key != "recordSha256"
        })
        relationship = relationship_attestation(
            sourceRecordSha256=source["recordSha256"],
            availableAt=source["availableAt"],
        )
        result = self.review_ready(
            relationship=relationship,
            relationship_source=_private_bytes(source),
        )
        self.assertIn(
            f"PRIVATE_SOURCE_AFTER_REVIEW_AS_OF:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )
        noncanonical = json.dumps(full_account_source_record()).encode("utf-8")
        result = self.review_ready(relationship_source=noncanonical)
        self.assertIn(
            f"PRIVATE_SOURCE_CANONICAL_BYTES_INVALID:{RELATIONSHIP_ATTESTATION_VERSION}",
            result["reasons"],
        )

    def test_private_relationships_are_rederived_from_source_values(self):
        source = full_account_source_record()
        source["observedAccountValuation"]["netAssetKrw"]["valueKrw"] += 1
        source["recordSha256"] = payload_sha256({
            key: value for key, value in source.items() if key != "recordSha256"
        })
        relationship = relationship_attestation(sourceRecordSha256=source["recordSha256"])
        result = self.review_ready(
            relationship=relationship,
            relationship_source=_private_bytes(source),
        )
        self.assertIn(
            f"PRIVATE_ATTESTATION_RELATIONSHIP_NOT_DERIVED:{RELATIONSHIP_ATTESTATION_VERSION}:netAssetEqualsCashDepositPlusPositionMarketValue",
            result["reasons"],
        )

    def test_buy_capacity_instrument_binding_and_ratified_alias_are_rederived(self):
        source = buy_capacity_source_record()
        source["instrument"]["sourceAssetId"] = "005930"
        source["recordSha256"] = payload_sha256({
            key: value for key, value in source.items() if key != "recordSha256"
        })
        capacity = buy_capacity_attestation(sourceRecordSha256=source["recordSha256"])
        result = self.review_ready(
            buy_capacity=capacity,
            buy_capacity_source=_private_bytes(source),
        )
        self.assertIn(
            f"PRIVATE_ATTESTATION_INSTRUMENT_BINDING_NOT_DERIVED:{BUY_CAPACITY_ATTESTATION_VERSION}",
            result["reasons"],
        )
        self.assertIn(
            "PRIVATE_SOURCE_INSTRUMENT_ALIAS_NOT_RATIFIED_AT_AVAILABLE_AT",
            result["reasons"],
        )

    def test_official_column_mapping_is_parsed_not_inferred_from_hash(self):
        blobs = dict(OFFICIAL_BLOBS)
        path = "examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py"
        blobs[path] = blobs[path].replace("순자산금액".encode(), "총평가금액".encode())
        result = self.review_ready(official_blobs=blobs)
        self.assertIn("OFFICIAL_FIELD_MEANING_MISMATCH:nass_amt", result["reasons"])
        self.assertIn("PROPOSAL_OFFICIAL_MEANING_NOT_REPRODUCED:nass_amt", result["reasons"])

    def test_official_buy_capacity_query_binding_is_parsed_from_implementation(self):
        blobs = dict(OFFICIAL_BLOBS)
        path = "examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py"
        blobs[path] = blobs[path].replace(b"'PDNO':pdno", b"'PDNO':ord_unpr")
        result = self.review_ready(official_blobs=blobs)
        self.assertIn(
            "OFFICIAL_BUY_CAPACITY_INSTRUMENT_QUERY_BINDING_INVALID",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
