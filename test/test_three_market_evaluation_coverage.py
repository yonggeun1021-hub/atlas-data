#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery" / "three_market_evaluation_coverage.py"
SPEC = importlib.util.spec_from_file_location("three_market_evaluation_coverage", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

KR_UNIVERSE = ROOT / "data/observations/krx_global_universe/2026-09-10/packet.json"
KR_REVIEW = ROOT / "data/latest_korea_symbol_market_review.json"
US_UNIVERSE = ROOT / "data/observations/us_global_universe/2026-09-11/packet.json"
US_REVIEW = ROOT / "data/latest_us_symbol_market_review.json"
US_RAW_SNAPSHOT = ROOT / "evidence/us_breadth/raw/2026-09-11"
US_MARKET_DATA = ROOT / "data/latest_free_market_data.json"
CRYPTO_UNIVERSE = ROOT / "data/observations/upbit_tradeable_universe/2026-09-12/packet.json"
CRYPTO_IDENTITY_REVIEW = ROOT / "data/observations/upbit_identity_review/2026-09-12/packet.json"
CRYPTO_SNAPSHOT = ROOT / "evidence/crypto/upbit/raw/2026-09-12"
PRIOR_IDENTITY_EVIDENCE = ROOT / "config/upbit_bounded_identity_evidence.json"
CRYPTO_DECISION = ROOT / (
    "evidence/crypto_paper_decision/2026-09-12/1351/"
    "4aaa821be20c357e201f83fe3b1a2ae3adfd9b533fbe7c7cbc25d31eac06c6ad/"
    "packet.json"
)


def build(**overrides):
    values = {
        "generated_at": "2026-09-12T14:15:00Z",
        "kr_universe_path": KR_UNIVERSE,
        "kr_review_path": KR_REVIEW,
        "us_universe_path": US_UNIVERSE,
        "us_review_path": US_REVIEW,
        "us_raw_snapshot_dir": US_RAW_SNAPSHOT,
        "us_market_data_path": US_MARKET_DATA,
        "crypto_universe_path": CRYPTO_UNIVERSE,
        "crypto_decision_path": CRYPTO_DECISION,
        "crypto_identity_review_path": CRYPTO_IDENTITY_REVIEW,
        "crypto_snapshot_dir": CRYPTO_SNAPSHOT,
        "prior_identity_evidence_path": PRIOR_IDENTITY_EVIDENCE,
    }
    values.update(overrides)
    return MODULE.build_report(**values)


class NaturalCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build()
        cls.by_market = {row["market"]: row for row in cls.report["markets"]}

    def test_exact_natural_universe_and_current_output_counts(self):
        self.assertEqual(self.by_market["KR"]["universe_count"], 2766)
        self.assertEqual(self.by_market["KR"]["bounded_current_output_count"], 3)
        self.assertEqual(self.by_market["US"]["universe_count"], 13214)
        self.assertEqual(self.by_market["US"]["bounded_current_output_count"], 2)
        self.assertEqual(
            self.by_market["US"]["bounded_output_scope"],
            "SUPPORTED_PIPELINE_SUBJECTS_ONLY_NOT_POPULATION_EVALUATION",
        )
        self.assertEqual(
            self.by_market["US"]["bounded_output_state_counts"],
            {"BLOCKED": 1, "WAIT": 1},
        )
        self.assertEqual(
            self.by_market["US"]["population_evaluation_connection"],
            {
                "status": "NOT_CONNECTED",
                "required_input_schema": "us_investable_snapshot/1",
                "existing_evaluator_output_schema": "us_investable_registry_result/1",
                "connected_source_universe_is_investability": False,
                "required_fail_closed_facts": [
                    "security_type", "listing", "trading_halt",
                    "scheduled_delisting", "corporate_action_state", "liquidity",
                ],
                "liquidity_policy_status": (
                    "ABSENT_EXTERNAL_RATIFIED_POLICY_REQUIRED"
                ),
                "reason": (
                    "NATURAL_POPULATION_INPUT_AND_LIQUIDITY_POLICY_NOT_CONNECTED"
                ),
            },
        )
        us_readiness = self.by_market["US"]["investable_input_readiness"]
        self.assertEqual(us_readiness["current_fully_closable_natural_symbol_count"], 0)
        self.assertEqual(
            {
                row["fact"]: row["available_count"]
                for row in us_readiness["field_source_matrix"]
            },
            {
                "asset_id_symbol_source_exchange_identity": 13214,
                "normalized_listing_venue": 0,
                "etf_indicator": 13214,
                "test_issue": 13214,
                "financial_status": 5605,
                "listing": 13214,
                "trading_halt": 0,
                "scheduled_delisting": 0,
                "corporate_action_state": 0,
                "liquidity_ohlcv_inputs": 18,
                "liquidity": 0,
            },
        )
        self.assertEqual(
            us_readiness["first_bounded_source_aligned_target"][
                "directory_etf_type_proven_symbol_count"
            ],
            15,
        )
        self.assertEqual(
            us_readiness["adapter_decision"],
            "DO_NOT_CREATE_ADAPTER_UNTIL_FACT_SOURCES_AND_POLICY_EXIST",
        )
        self.assertEqual(
            us_readiness["existing_source_constructible_scope"],
            {
                "population_count": 13214,
                "fully_available_fields": [
                    "asset_id",
                    "symbol",
                    "source_exchange_identity",
                    "etf_indicator",
                    "test_issue",
                    "current_directory_membership_observation",
                ],
                "partially_available_fields": {
                    "financial_status": 5605,
                    "liquidity_ohlcv_inputs": 18,
                },
                "resulting_artifact_boundary": (
                    "SOURCE_FACT_INPUTS_ONLY_NOT_US_INVESTABLE_SNAPSHOT"
                ),
            },
        )
        self.assertEqual(
            {
                key: value["path"]
                for key, value in us_readiness["input_connections"].items()
                if isinstance(value, dict)
            },
            {
                "source_population_packet": (
                    "data/observations/us_global_universe/2026-09-11/packet.json"
                ),
                "nasdaq_listed_raw": (
                    "evidence/us_breadth/raw/2026-09-11/nasdaqlisted.txt.gz"
                ),
                "other_listed_raw": (
                    "evidence/us_breadth/raw/2026-09-11/otherlisted.txt.gz"
                ),
                "partial_iex_market_data": "data/latest_free_market_data.json",
                "existing_evaluator_contract": (
                    "config/us_investable_registry_contract.json"
                ),
                "existing_evaluator": "universe/us_investable_registry.py",
            },
        )
        self.assertEqual(
            us_readiness["delivery_cost"],
            {
                "retained_input_additional_external_call_count": 0,
                "new_adapter_file_count": 0,
                "new_policy_or_threshold_count": 0,
                "missing_source_call_count": (
                    "NOT_DETERMINED_UNTIL_CIO_SELECTS_SOURCES"
                ),
                "missing_source_monetary_cost": (
                    "NOT_DETERMINED_UNTIL_CIO_SELECTS_SOURCES"
                ),
            },
        )
        self.assertIn(
            "EXISTING_US_INVESTABLE_REGISTRY_VALIDATES_THE_NATURAL_SNAPSHOT",
            us_readiness["completion_conditions"],
        )
        self.assertIn(
            "EXTERNAL_RATIFIED_LIQUIDITY_POLICY",
            us_readiness["next_source_requirements"],
        )
        self.assertEqual(self.by_market["CRYPTO"]["universe_count"], 282)
        self.assertEqual(self.by_market["CRYPTO"]["observation_pool_count"], 274)
        self.assertEqual(self.by_market["CRYPTO"]["evaluated_count"], 8)
        self.assertEqual(self.by_market["CRYPTO"]["candidate_count"], 0)
        self.assertEqual(
            self.by_market["CRYPTO"]["candidate_count_semantics"],
            "SOURCE_FOCUSED_REVIEW_COUNT",
        )
        self.assertEqual(self.by_market["CRYPTO"]["held_count"], 8)
        self.assertEqual(self.by_market["CRYPTO"]["excluded_count"], 274)
        self.assertEqual(
            self.by_market["CRYPTO"]["excluded_count_semantics"],
            "SOURCE_MEMBERS_NOT_ADMITTED_TO_CURRENT_EVALUATION_INPUT",
        )
        self.assertEqual(
            self.by_market["CRYPTO"]["excluded_state_counts"],
            {"OBSERVATION_POOL": 274, "BLOCKED": 0},
        )
        self.assertEqual(
            self.by_market["CRYPTO"]["excluded_reason_counts"],
            {"IDENTITY_UNRATIFIED": 267, "INVESTMENT_WARNING_ACTIVE": 7},
        )
        self.assertEqual(
            self.by_market["CRYPTO"]["pre_evaluation_reason_classification"],
            {
                "IDENTITY_UNRATIFIED": {
                    "market_count": 267,
                    "classification": "OUTSIDE_RATIFIED_IDENTITY_SCOPE",
                    "approval_scope": "UPBIT_KRW_SPOT_CRYPTO_PAPER_EIGHT_ONLY",
                    "interpretation": "NOT_AN_INVESTMENT_CONDITION_FAILURE",
                },
                "INVESTMENT_WARNING_ACTIVE": {
                    "market_count": 7,
                    "classification": "SAFETY_EXCLUSION",
                    "interpretation": (
                        "UPBIT_CAUTION_HARD_EXCLUSION_NOT_A_CANDIDATE_SCORE"
                    ),
                },
            },
        )
        held = self.by_market["CRYPTO"]["held_reason_analysis"]
        self.assertEqual(
            held["criterion_occurrence_counts"],
            {
                "UNRATIFIED_POLICY_OR_AUTHORITY": 33,
                "SOURCE_DATA_OR_COVERAGE_INSUFFICIENT": 15,
                "CONSUMER_INPUT_NOT_CONNECTED": 0,
                "OTHER_UNKNOWN_REASON": 0,
            },
        )
        self.assertEqual(
            held["affected_market_counts"],
            {
                "UNRATIFIED_POLICY_OR_AUTHORITY": 8,
                "SOURCE_DATA_OR_COVERAGE_INSUFFICIENT": 8,
                "CONSUMER_INPUT_NOT_CONNECTED": 0,
                "OTHER_UNKNOWN_REASON": 0,
            },
        )
        self.assertEqual(self.by_market["CRYPTO"]["paper_ready_count"], 0)
        self.assertEqual(
            self.by_market["CRYPTO"]["coverage_status"],
            "SOURCE_POPULATION_EVALUATION_DISPOSITION_ACCOUNTED",
        )
        self.assertEqual(self.by_market["CRYPTO"]["missing_reasons"], [])
        crypto_readiness = self.by_market["CRYPTO"][
            "evaluation_only_scope_readiness"
        ]
        self.assertEqual(crypto_readiness["status"], "PROPOSAL_ONLY_NOT_ADOPTED")
        self.assertEqual(crypto_readiness["selection"]["market_count"], 267)
        self.assertEqual(
            crypto_readiness["identity_verification"],
            {
                "proposal_count": 282,
                "proposal_status": "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY",
                "collision_finding_count": 0,
                "cross_reference_check_status": (
                    "NOT_RUN_RATIFIED_BROAD_REGISTRY_ABSENT"
                ),
                "prior_bounded_research_row_count": 80,
                "prior_bounded_research_overlap_count": 70,
                "prior_verdict_counts": {
                    "HOLD_MISSING_SECOND_SOURCE": 198,
                    "HOLD_TICKER_COLLISION": 24,
                    "VERIFIED_CANDIDATE": 45,
                },
                "external_cross_reference_still_required_count": 198,
                "zero_findings_meaning": (
                    "No duplicate candidate target was found. This does not "
                    "ratify any proposal and does not prove cross-registry identity."
                ),
            },
        )
        partition = crypto_readiness["identity_evaluation_partition"]
        self.assertEqual(
            {
                key: value["count"]
                for key, value in partition.items()
                if isinstance(value, dict)
            },
            {
                "verified_candidate": 45,
                "ticker_collision_hold": 24,
                "missing_second_source_hold": 198,
            },
        )
        partition_markets = [
            market
            for key in (
                "verified_candidate",
                "ticker_collision_hold",
                "missing_second_source_hold",
            )
            for market in partition[key]["markets"]
        ]
        self.assertEqual(len(partition_markets), len(set(partition_markets)))
        self.assertEqual(len(partition_markets), 267)
        source_universe = json.loads(CRYPTO_UNIVERSE.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(partition_markets),
            sorted(
                row["market"]
                for row in source_universe["packet"]["markets"]
                if row["reason"] == "IDENTITY_UNRATIFIED"
            ),
        )
        self.assertEqual(
            {
                key: value["path"]
                for key, value in crypto_readiness["input_connections"].items()
                if isinstance(value, dict)
            },
            {
                "evaluation_scope_disposition": (
                    "data/observations/upbit_tradeable_universe/2026-09-12/packet.json"
                ),
                "identity_proposal_review": (
                    "data/observations/upbit_identity_review/2026-09-12/packet.json"
                ),
                "bounded_identity_evidence": (
                    "config/upbit_bounded_identity_evidence.json"
                ),
                "retained_market_data_manifest": (
                    "evidence/crypto/upbit/raw/2026-09-12/_manifest.json"
                ),
                "existing_market_data_gate_policy": (
                    "config/upbit_tradeable_universe_policy.json"
                ),
            },
        )
        self.assertEqual(
            crypto_readiness["source_call_cost"][
                "verified_candidate_additional_identity_research_call_count"
            ],
            0,
        )
        self.assertIn(
            "SEPARATE_EVALUATION_IDENTITY_SCOPE_IS_EXPLICITLY_ADOPTED",
            crypto_readiness["completion_conditions"]["verified_candidate_45"],
        )
        self.assertEqual(
            crypto_readiness["completion_conditions"]["completion_does_not_grant"],
            [
                "INVESTABILITY",
                "CANDIDATE_PROMOTION",
                "PAPER_ELIGIBILITY",
                "ORDER_OR_TRADING_AUTHORITY",
            ],
        )
        self.assertEqual(
            crypto_readiness["retained_source_availability_counts"],
            {
                "market_metadata": 267,
                "warning_state": 267,
                "orderbook": 267,
                "daily_candles": 267,
            },
        )
        self.assertEqual(
            crypto_readiness["existing_policy_market_data_gate_pass_counts"],
            {
                "listing_history": 239,
                "complete_turnover_history": 260,
                "turnover": 41,
                "spread": 54,
                "slippage": 255,
            },
        )
        self.assertEqual(
            crypto_readiness["all_existing_market_data_gates_pass_count"], 13
        )
        self.assertEqual(
            crypto_readiness["raw_scope_gap"],
            {
                "raw_krw_market_count": 288,
                "classifier_market_count": 282,
                "regex_excluded_krw_market_count": 6,
                "regex_excluded_krw_markets": [
                    "KRW-A", "KRW-F", "KRW-G", "KRW-O", "KRW-T", "KRW-W",
                ],
                "decision_required": "SEPARATE_IDENTITY_CONTRACT_DECISION",
            },
        )
        self.assertEqual(
            crypto_readiness["source_call_cost"],
            {
                "retained_snapshot_additional_call_count": 0,
                "fresh_full_capture_successful_http_call_count": 291,
                "fresh_full_capture_minimum_candle_pacing_seconds": "301.35",
                "authentication_required": False,
                "order_or_withdrawal_endpoints_called": False,
                "verified_candidate_additional_identity_research_call_count": 0,
                "unresolved_identity_research_call_count": (
                    "NOT_DETERMINED_UNTIL_SECOND_SOURCE_PLAN_IS_SELECTED"
                ),
                "unresolved_identity_research_monetary_cost": (
                    "NOT_DETERMINED_UNTIL_SECOND_SOURCE_PLAN_IS_SELECTED"
                ),
            },
        )
        self.assertTrue(
            all(value is False for value in crypto_readiness["authority"].values())
        )
        self.assertTrue(
            crypto_readiness["minimum_change_design"][
                "separate_evaluation_identity_authority_required"
            ]
        )
        self.assertFalse(
            crypto_readiness["minimum_change_design"][
                "market_data_adapter_required"
            ]
        )
        self.assertIn(
            "ADOPT_OR_REJECT_EXACT_267_EVALUATION_ONLY_SCOPE",
            crypto_readiness["cio_decision_targets"],
        )

    def test_bounded_reviews_never_become_population_evaluation_counts(self):
        for market in ("KR", "US"):
            row = self.by_market[market]
            self.assertEqual(row["evaluated_count"], MODULE.NOT_COUNTED)
            self.assertEqual(row["candidate_count"], MODULE.NOT_COUNTED)
            self.assertEqual(row["excluded_count"], MODULE.NOT_COUNTED)
        self.assertEqual(self.by_market["CRYPTO"]["excluded_count"], 274)
        self.assertEqual(
            self.report["summary"]["full_population_evaluation_disposition_available"],
            1,
        )
        self.assertEqual(
            self.report["summary"][
                "us_fully_closable_natural_investable_input_count"
            ],
            0,
        )
        for row in self.by_market.values():
            self.assertEqual(row["state_lifetime"], {
                "snapshot_only": True,
                "automatic_carry_forward": False,
                "reevaluation_required": True,
            })

    def test_report_is_read_only_and_has_no_authority(self):
        self.assertTrue(all(value is False for value in self.report["authority"].values()))
        unsigned = copy.deepcopy(self.report)
        claimed = unsigned.pop("payload_sha256")
        self.assertEqual(MODULE.payload_sha256(unsigned), claimed)


class TamperTests(unittest.TestCase):
    OBSERVED_AT = MODULE.dt.datetime(
        2026, 9, 12, 14, 15, tzinfo=MODULE.dt.timezone.utc
    )

    def _write(self, value: dict) -> Path:
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        with handle:
            json.dump(value, handle)
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_rehashed_kr_universe_count_cannot_replace_asset_master_count(self):
        value = json.loads(KR_UNIVERSE.read_text(encoding="utf-8"))
        value["total_count"] += 1
        value["payload_sha256"] = MODULE.payload_sha256(
            {key: item for key, item in value.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketEvaluationCoverageError, "KR_UNIVERSE_COUNT_INVALID"
        ):
            MODULE._validated_global_universe(
                self._write(value), "KR", self.OBSERVED_AT
            )

    def test_rehashed_review_symbol_count_cannot_become_population_count(self):
        value = json.loads(US_REVIEW.read_text(encoding="utf-8"))
        value["summary"]["symbol_count"] = 13214
        value["packet_sha256"] = MODULE.payload_sha256(
            {key: item for key, item in value.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketEvaluationCoverageError,
            "US_BOUNDED_REVIEW_INVALID",
        ):
            MODULE._validated_review(self._write(value), "US", self.OBSERVED_AT)

    def test_rehashed_crypto_funnel_count_fails_full_rederivation(self):
        value = json.loads(CRYPTO_DECISION.read_text(encoding="utf-8"))
        value["funnel_counts"]["focused_review_count"] = 1
        value["payload_sha256"] = MODULE.payload_sha256(
            {key: item for key, item in value.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketEvaluationCoverageError,
            "CRYPTO_DECISION_INVALID:OUTPUT_DERIVATION_MISMATCH",
        ):
            build(crypto_decision_path=self._write(value))

    def test_future_current_output_cannot_be_carried_backward(self):
        before_source = MODULE.dt.datetime(
            2026, 9, 11, 20, 0, tzinfo=MODULE.dt.timezone.utc
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketEvaluationCoverageError, "US_REVIEW_FROM_FUTURE"
        ):
            MODULE._validated_review(US_REVIEW, "US", before_source)

    def test_rehashed_candidate_substitution_cannot_escape_exact_admitted_input(self):
        value = json.loads(CRYPTO_DECISION.read_text(encoding="utf-8"))
        value["candidates"][0]["market"] = "KRW-NOT-ADMITTED"
        value["payload_sha256"] = MODULE.payload_sha256(
            {key: item for key, item in value.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketEvaluationCoverageError,
            "CRYPTO_DECISION_INVALID:OUTPUT_DERIVATION_MISMATCH",
        ):
            build(crypto_decision_path=self._write(value))

    def test_rehashed_identity_proposal_cannot_define_evaluation_scope(self):
        value = json.loads(CRYPTO_IDENTITY_REVIEW.read_text(encoding="utf-8"))
        value["proposals"][0]["claim"]["upbitMarket"] = "KRW-TAMPER"
        value["payload_sha256"] = MODULE.payload_sha256(
            {key: item for key, item in value.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketEvaluationCoverageError,
            "CRYPTO_IDENTITY_REVIEW_DERIVATION_MISMATCH",
        ):
            build(crypto_identity_review_path=self._write(value))

    def test_rehashed_partial_market_data_cannot_expand_us_readiness(self):
        value = json.loads(US_MARKET_DATA.read_text(encoding="utf-8"))
        value["alpaca"]["source_scope"] = "FULL_US_MARKET"
        value["packet_sha256"] = MODULE.payload_sha256(
            {key: item for key, item in value.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketEvaluationCoverageError,
            "US_FREE_MARKET_DATA_ALPACA_INVALID",
        ):
            build(us_market_data_path=self._write(value))


if __name__ == "__main__":
    unittest.main()
