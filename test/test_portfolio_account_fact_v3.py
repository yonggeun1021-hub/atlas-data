#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity
from portfolio_risk import kis_valuation_authority
from portfolio_risk import portfolio_account_fact_v3 as fact_v3


DECISION_AT = "2026-08-29T03:20:00Z"


def _entry(raw_field: str, value: int) -> dict:
    return {"rawKisField": raw_field, "value": value}


def _bundle() -> dict:
    value = {
        "contractVersion": fact_v3.SOURCE_BUNDLE_VERSION,
        "providerTuple": dict(fact_v3.PROVIDER_TUPLE),
        "balanceObservation": {
            "sourceContractVersion": "kis_paper_full_account_snapshot/3",
            "sourceRecordSha256": "1" * 64,
            "accountIdentityHash": "2" * 64,
            "capturedAt": "2026-08-29T03:19:57Z",
            "availableAt": "2026-08-29T03:19:58Z",
            "account": {
                "netAssetKrw": _entry("nass_amt", 1_000_000),
                "cashDepositTotalKrw": _entry("dnca_tot_amt", 300_000),
                "securitiesValuationKrw": _entry("scts_evlu_amt", 700_000),
                "totalValuationKrw": _entry("tot_evlu_amt", 1_000_000),
                "valuationSumKrw": _entry("evlu_amt_smtl_amt", 700_000),
                "unrealizedPlSumKrw": _entry("evlu_pfls_smtl_amt", -5_000),
            },
            "positions": [{
                "sourceName": "kis_paper_domestic_balance",
                "sourceAssetId": "071050",
                "holdingQuantity": 10,
                "orderableQuantity": 7,
                "marketValueKrw": _entry("evlu_amt", 700_000),
                "unrealizedPlKrw": _entry("evlu_pfls_amt", -5_000),
            }],
        },
        "instrumentBuyCapacityObservation": {
            "sourceContractVersion": "kis_paper_buy_capacity_snapshot/1",
            "sourceRecordSha256": "3" * 64,
            "accountIdentityHash": "2" * 64,
            "capturedAt": "2026-08-29T03:19:59Z",
            "availableAt": "2026-08-29T03:19:59Z",
            "instrument": {
                "sourceName": "kis_paper_domestic_balance",
                "sourceAssetId": "071050",
            },
            "capacity": {
                "noReceivableBuyAmountKrw": _entry("nrcvb_buy_amt", 200_000),
                "noReceivableBuyQuantity": _entry("nrcvb_buy_qty", 2),
                "quantityCalculationPriceKrw": _entry("psbl_qty_calc_unpr", 90_000),
            },
        },
        "sourceBindings": {
            "fullAccountRecordSha256": "1" * 64,
            "buyCapacityRecordSha256": "3" * 64,
            "pairBindingRecordSha256": "4" * 64,
            "lockedRuntimeReceiptSha256": "5" * 64,
        },
    }
    value["bundleSha256"] = fact_v3.payload_sha256(value)
    return value


def _rehash(bundle: dict) -> None:
    bundle["bundleSha256"] = fact_v3.payload_sha256({
        key: value for key, value in bundle.items() if key != "bundleSha256"
    })


class PortfolioAccountFactV3ReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider_authority = canonical_identity.load_provider_authority()
        cls.security_identity = canonical_identity.load_authority()
        cls.valuation_authority = kis_valuation_authority.load_authority()
        cls.trusted_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def evaluate(self, bundle: dict, decision_at: str = DECISION_AT) -> dict:
        return fact_v3.evaluate_kis_portfolio_account_fact_v3_readiness(
            bundle=bundle,
            decision_at=decision_at,
            provider_authority=self.provider_authority,
            security_identity=self.security_identity,
            valuation_authority_document=self.valuation_authority,
            trusted_commit=self.trusted_commit,
        )

    def test_all_prerequisites_stop_at_separate_account_fact_authority(self):
        result = self.evaluate(_bundle())
        self.assertEqual(
            result["status"],
            fact_v3.NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED,
        )
        self.assertIsNone(result["accountFact"])
        self.assertEqual(
            result["authority"], fact_v3.CONSUMPTION_AUTHORITY_ALL_FALSE
        )
        self.assertEqual(result["providerAuthorityStatus"], "RESOLVED")
        self.assertEqual(result["semanticAuthorityStatus"], "RESOLVED")
        self.assertEqual(result["freshnessAuthorityStatus"], "RESOLVED")
        self.assertEqual(result["sourceAgeSeconds"], 2)
        self.assertEqual(result["sourcePairGapSeconds"], 1)
        self.assertEqual(
            result["privateSourceValidationBoundary"],
            "STRUCTURAL_HASH_BINDINGS_ONLY_PRIVATE_SOURCE_BYTES_MUST_BE_VALIDATED_BY_CALLER",
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("buyingPower", rendered)
        self.assertNotIn("accountIdentityHash", rendered)
        self.assertNotIn("noReceivableBuyAmountKrw", rendered)

    def test_semantic_authority_is_pit_blocked_before_ratification(self):
        bundle = _bundle()
        bundle["balanceObservation"]["capturedAt"] = "2026-08-29T03:19:55Z"
        bundle["balanceObservation"]["availableAt"] = "2026-08-29T03:19:56Z"
        bundle["instrumentBuyCapacityObservation"]["capturedAt"] = "2026-08-29T03:19:57Z"
        bundle["instrumentBuyCapacityObservation"]["availableAt"] = "2026-08-29T03:19:58Z"
        _rehash(bundle)
        result = self.evaluate(bundle, "2026-08-29T03:19:59Z")
        self.assertEqual(
            result["status"], fact_v3.NOT_COMPUTABLE_VALUATION_SEMANTIC_AUTHORITY
        )
        self.assertIsNone(result["accountFact"])

    def test_source_age_300_and_pair_gap_120_are_inclusive(self):
        bundle = _bundle()
        balance = bundle["balanceObservation"]
        capacity = bundle["instrumentBuyCapacityObservation"]
        balance["capturedAt"] = "2026-08-29T03:14:59Z"
        balance["availableAt"] = "2026-08-29T03:15:00Z"
        capacity["capturedAt"] = "2026-08-29T03:16:59Z"
        capacity["availableAt"] = "2026-08-29T03:17:00Z"
        _rehash(bundle)
        result = self.evaluate(bundle)
        self.assertEqual(
            result["status"],
            fact_v3.NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED,
        )
        self.assertEqual(result["sourceAgeSeconds"], 300)
        self.assertEqual(result["sourcePairGapSeconds"], 120)

    def test_source_age_301_is_blocked(self):
        bundle = _bundle()
        balance = bundle["balanceObservation"]
        balance["capturedAt"] = "2026-08-29T03:14:58Z"
        balance["availableAt"] = "2026-08-29T03:14:59Z"
        _rehash(bundle)
        self.assertEqual(
            self.evaluate(bundle)["status"],
            fact_v3.NOT_COMPUTABLE_SOURCE_STALE_OR_FUTURE,
        )

    def test_pair_gap_121_is_blocked(self):
        bundle = _bundle()
        balance = bundle["balanceObservation"]
        capacity = bundle["instrumentBuyCapacityObservation"]
        balance["capturedAt"] = "2026-08-29T03:17:58Z"
        balance["availableAt"] = "2026-08-29T03:17:59Z"
        capacity["capturedAt"] = "2026-08-29T03:19:59Z"
        capacity["availableAt"] = "2026-08-29T03:20:00Z"
        _rehash(bundle)
        self.assertEqual(
            self.evaluate(bundle)["status"],
            fact_v3.NOT_COMPUTABLE_SOURCE_PAIR_GAP_EXCEEDED,
        )

    def test_future_available_at_is_blocked(self):
        bundle = _bundle()
        capacity = bundle["instrumentBuyCapacityObservation"]
        capacity["capturedAt"] = "2026-08-29T03:20:01Z"
        capacity["availableAt"] = "2026-08-29T03:20:01Z"
        _rehash(bundle)
        self.assertEqual(
            self.evaluate(bundle)["status"],
            fact_v3.NOT_COMPUTABLE_SOURCE_STALE_OR_FUTURE,
        )

    def test_unratified_position_alias_blocks_the_whole_bundle(self):
        bundle = _bundle()
        bundle["balanceObservation"]["positions"][0]["sourceAssetId"] = "005930"
        _rehash(bundle)
        self.assertEqual(
            self.evaluate(bundle)["status"],
            fact_v3.NOT_COMPUTABLE_POSITION_IDENTITY_INCOMPLETE,
        )

    def test_account_binding_mismatch_is_rejected(self):
        bundle = _bundle()
        bundle["instrumentBuyCapacityObservation"]["accountIdentityHash"] = "f" * 64
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "SOURCE_ACCOUNT_BINDING_MISMATCH"
        ):
            self.evaluate(bundle)

    def test_cross_field_reconciliation_mismatch_is_rejected(self):
        bundle = _bundle()
        bundle["balanceObservation"]["account"]["netAssetKrw"]["value"] += 1
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "NET_ASSET_RELATIONSHIP_MISMATCH"
        ):
            self.evaluate(bundle)

    def test_raw_semantic_field_substitution_is_rejected(self):
        bundle = _bundle()
        bundle["balanceObservation"]["account"]["netAssetKrw"][
            "rawKisField"
        ] = "tot_evlu_amt"
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error,
            "BALANCE_ACCOUNT_NETASSETKRW_KIS_FIELD_MISMATCH",
        ):
            self.evaluate(bundle)

    def test_generic_buying_power_field_is_closed_schema_rejected(self):
        bundle = _bundle()
        bundle["balanceObservation"]["account"]["buyingPower"] = _entry(
            "ord_psbl_cash", 200_000
        )
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "BALANCE_ACCOUNT_FIELDS_INVALID"
        ):
            self.evaluate(bundle)

    def test_orderable_cash_is_not_an_instrument_capacity_mapping(self):
        bundle = _bundle()
        bundle["instrumentBuyCapacityObservation"]["capacity"][
            "orderableCashKrw"
        ] = _entry("ord_psbl_cash", 200_000)
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "BUY_CAPACITY_FIELDS_INVALID"
        ):
            self.evaluate(bundle)

    def test_no_receivable_amount_cannot_be_relabelled_orderable_cash(self):
        bundle = _bundle()
        bundle["instrumentBuyCapacityObservation"]["capacity"][
            "noReceivableBuyAmountKrw"
        ]["rawKisField"] = "ord_psbl_cash"
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error,
            "BUY_CAPACITY_NORECEIVABLEBUYAMOUNTKRW_KIS_FIELD_MISMATCH",
        ):
            self.evaluate(bundle)

    def test_caller_cannot_inject_account_fact_authority(self):
        bundle = _bundle()
        bundle["accountFactAuthorized"] = True
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "SOURCE_BUNDLE_FIELDS_INVALID"
        ):
            self.evaluate(bundle)

    def test_numeric_boolean_alias_is_rejected(self):
        bundle = _bundle()
        bundle["balanceObservation"]["positions"][0]["holdingQuantity"] = True
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "BALANCE_HOLDING_QUANTITY_INVALID"
        ):
            self.evaluate(bundle)

    def test_source_capture_sequence_is_strict(self):
        bundle = _bundle()
        bundle["instrumentBuyCapacityObservation"]["capturedAt"] = (
            "2026-08-29T03:19:57Z"
        )
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "SOURCE_CAPTURE_SEQUENCE_INVALID"
        ):
            self.evaluate(bundle)

    def test_tampered_bundle_hash_is_rejected(self):
        bundle = _bundle()
        bundle["bundleSha256"] = "f" * 64
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "SOURCE_BUNDLE_SHA_MISMATCH"
        ):
            self.evaluate(bundle)

    def test_wrong_provider_tuple_is_rejected_before_authority_resolution(self):
        bundle = _bundle()
        bundle["providerTuple"]["currency"] = "USD"
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error,
            "SOURCE_BUNDLE_PROVIDER_TUPLE_INVALID",
        ):
            self.evaluate(bundle)

    def test_source_binding_requires_real_sha_shape_not_boolean_alias(self):
        bundle = _bundle()
        bundle["sourceBindings"]["lockedRuntimeReceiptSha256"] = True
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "SOURCE_BINDING_SHA_INVALID"
        ):
            self.evaluate(bundle)

    def test_source_record_hash_must_match_its_structural_binding(self):
        bundle = _bundle()
        bundle["sourceBindings"]["fullAccountRecordSha256"] = "f" * 64
        _rehash(bundle)
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error,
            "FULL_ACCOUNT_SOURCE_BINDING_MISMATCH",
        ):
            self.evaluate(bundle)


if __name__ == "__main__":
    unittest.main()
