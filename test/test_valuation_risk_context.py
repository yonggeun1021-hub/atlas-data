"""P3-10 Valuation / Risk context regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "discovery" / "valuation_risk_context.py"
SPEC = importlib.util.spec_from_file_location("valuation_risk_context", MODULE_PATH)
VR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VR)


SOURCE_META = {
    "sec_edgar": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
    "tiingo_us_daily_price": "https://api.tiingo.com/tiingo/daily/TEST/prices",
    "dart_open_api": "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
    "krx_open_api_stock_daily": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "kraken_public_api": "https://api.kraken.com/0/public/OHLC?pair=XBTUSD",
}


def source(source_id: str, marker: str) -> dict:
    return {
        "source_id": source_id,
        "source_url": SOURCE_META[source_id],
        "source_sha256": marker * 64,
        "available_at": "2026-08-19",
        "retrieved_at_utc": "2026-08-19T23:00:00Z",
    }


def candidate(
    *,
    case_id="RADAR-MB-AAAAAAAAAAAAAAAA",
    schema="market_behavior_case/1",
    market="US",
    asset_id="US:XNAS:TEST",
) -> dict:
    return {
        "case_id": case_id,
        "case_schema_version": schema,
        "market": market,
        "asset_id": asset_id,
        "observation_date": "2026-08-19",
        "case_payload_sha256": "f" * 64,
    }


def point(period: str, value: str, source_ids: tuple[str, ...]) -> dict:
    return {
        "period_end": period,
        "status": "EVIDENCE_AVAILABLE",
        "numeric_value": value,
        "missing_reasons": [],
        "source_identities": [source(source_id, marker) for source_id, marker in zip(source_ids, "abcde")],
    }


def missing(period: str) -> dict:
    return {
        "period_end": period,
        "status": "EVIDENCE_UNRESOLVED",
        "numeric_value": None,
        "missing_reasons": ["EXACT_CONTEXT_EVIDENCE_ABSENT"],
        "source_identities": [],
    }


def context(
    *,
    dimension="VALUATION",
    context_id="US.TEST.VAL.PE",
    case_id="RADAR-MB-AAAAAAAAAAAAAAAA",
    market="US",
    asset_id="US:XNAS:TEST",
    values=("20", "25"),
) -> dict:
    fields = {
        ("US", "VALUATION"): ("EARNINGS_MULTIPLE", "ratio", "price to trailing reported earnings", ("sec_edgar", "tiingo_us_daily_price")),
        ("US", "RISK"): ("REALIZED_VOLATILITY", "fraction", "annualized RMS simple-return volatility", ("tiingo_us_daily_price",)),
        ("KOREA", "VALUATION"): ("BOOK_MULTIPLE", "ratio", "price to reported book value", ("dart_open_api", "krx_open_api_stock_daily")),
        ("KOREA", "RISK"): ("CURRENT_DRAWDOWN", "fraction", "close peak-to-current drawdown", ("krx_open_api_stock_daily",)),
        ("CRYPTO", "RISK"): ("MAXIMUM_DRAWDOWN", "fraction", "finalized UTC close maximum drawdown", ("kraken_public_api",)),
    }
    metric_type, unit, measurement, source_ids = fields[(market, dimension)]
    periods = ["2026-07-31", "2026-08-19"]
    return {
        "context_id": context_id,
        "case_id": case_id,
        "market": market,
        "asset_id": asset_id,
        "dimension": dimension,
        "measurement_identity": measurement,
        "metric_type": metric_type,
        "unit": unit,
        "comparison_basis": "two exact caller-declared dates, unchanged method",
        "expected_periods": periods,
        "evidence_points": [point(period, value, source_ids) for period, value in zip(periods, values)],
    }


def payload(candidates=None, contexts=None) -> dict:
    return {
        "schema_version": "valuation_risk_context_input/1",
        "as_of_utc": "2026-08-20T00:00:00Z",
        "candidates": candidates if candidates is not None else [candidate()],
        "context_observations": contexts if contexts is not None else [
            context(),
            context(dimension="RISK", context_id="US.TEST.RISK.RV", values=("0.2", "0.35")),
        ],
    }


def policy(value=None, *, status="RATIFIED", direction="HIGHER_IS_DETERIORATION") -> dict:
    value = context() if value is None else value
    return {
        "schema_version": "valuation_risk_interpretation_policy/1",
        "policy_id": "POLICY.VR.1",
        "approval_status": status,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "ratified_by": "CIO" if status == "RATIFIED" else None,
        "ratified_at_utc": "2026-08-19T00:00:00Z" if status == "RATIFIED" else None,
        "rules": [{
            "market": value["market"],
            "context_id": value["context_id"],
            "dimension": value["dimension"],
            "measurement_identity": value["measurement_identity"],
            "metric_type": value["metric_type"],
            "unit": value["unit"],
            "comparison_basis": value["comparison_basis"],
            "deterioration_direction": direction,
            "minimum_change": "5",
        }],
    }


class ValuationRiskContextTests(unittest.TestCase):
    def test_raw_context_attaches_both_dimensions_without_interpretation(self):
        packet = VR.build_packet(payload())
        self.assertEqual(packet["status"], "VALUATION_RISK_CONTEXT_ATTACHED")
        self.assertEqual(packet["candidate_count"], 1)
        self.assertEqual(packet["context_observation_count"], 2)
        self.assertEqual(packet["context_complete_candidate_count"], 1)
        result = packet["candidate_contexts"][0]
        self.assertTrue(result["context_complete"])
        valuation = result["valuation"]
        self.assertEqual(valuation["status"], "OBSERVED_RAW_CONTEXT")
        self.assertEqual(valuation["contexts"][0]["values"], ["20.000000000000", "25.000000000000"])
        self.assertEqual(valuation["contexts"][0]["change"], "5.000000000000")
        self.assertEqual(valuation["contexts"][0]["interpretation_status"], "ABSENT_OR_UNRATIFIED_POLICY")
        self.assertIsNone(valuation["contexts"][0]["deterioration_policy_match"])
        self.assertEqual(
            [item["source_id"] for item in valuation["contexts"][0]["evidence_lineage"][0]["source_identities"]],
            ["sec_edgar", "tiingo_us_daily_price"],
        )
        self.assertIsNone(result["candidate_rank"])
        self.assertIsNone(result["stage_transition"])
        self.assertIsNone(result["rule_evaluation"])
        self.assertIsNone(result["portfolio_action"])
        self.assertIsNone(result["trading_action"])

    def test_missing_dimension_is_explicit_and_not_safe_or_zero(self):
        packet = VR.build_packet(payload(contexts=[context()]))
        result = packet["candidate_contexts"][0]
        self.assertFalse(result["context_complete"])
        self.assertEqual(result["risk"], {
            "status": "EVIDENCE_ABSENT",
            "context_count": 0,
            "deterioration_match_count": 0,
            "contexts": [],
        })

    def test_unknown_evidence_preserves_missing_state(self):
        value = context()
        value["evidence_points"][1] = missing("2026-08-19")
        packet = VR.build_packet(payload(contexts=[value]), policy(value))
        result = packet["candidate_contexts"][0]["valuation"]["contexts"][0]
        self.assertEqual(result["feature_status"], "UNKNOWN_EVIDENCE")
        self.assertIsNone(result["values"])
        self.assertIsNone(result["change"])
        self.assertEqual(result["interpretation_status"], "NOT_EVALUATED_UNKNOWN_EVIDENCE")
        self.assertIsNone(result["deterioration_policy_match"])

    def test_ratified_exact_policy_labels_deterioration_with_proof(self):
        value = context()
        packet = VR.build_packet(payload(contexts=[value]), policy(value))
        result = packet["candidate_contexts"][0]["valuation"]["contexts"][0]
        self.assertTrue(result["deterioration_policy_match"])
        self.assertEqual(result["interpretation_status"], "RATIFIED_EXACT_RULE_APPLIED")
        self.assertEqual(result["interpretation_policy"]["policy_id"], "POLICY.VR.1")
        self.assertEqual(result["interpretation_policy"]["ratified_by"], "CIO")
        self.assertEqual(packet["candidate_contexts"][0]["total_deterioration_match_count"], 1)

    def test_lower_is_deterioration_comes_only_from_external_policy(self):
        value = context(values=("25", "20"))
        lower = policy(value, direction="LOWER_IS_DETERIORATION")
        self.assertTrue(
            VR.build_packet(payload(contexts=[value]), lower)["candidate_contexts"][0]["valuation"]["contexts"][0]["deterioration_policy_match"]
        )
        higher = policy(value, direction="HIGHER_IS_DETERIORATION")
        self.assertFalse(
            VR.build_packet(payload(contexts=[value]), higher)["candidate_contexts"][0]["valuation"]["contexts"][0]["deterioration_policy_match"]
        )

    def test_unratified_policy_never_labels_or_carries_fake_proof(self):
        value = context()
        unratified = policy(value, status="UNRATIFIED")
        result = VR.build_packet(payload(contexts=[value]), unratified)["candidate_contexts"][0]["valuation"]["contexts"][0]
        self.assertIsNone(result["deterioration_policy_match"])
        self.assertEqual(result["interpretation_status"], "ABSENT_OR_UNRATIFIED_POLICY")
        unratified["ratified_by"] = "CIO"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "PROOF_FORBIDDEN"):
            VR.build_packet(payload(contexts=[value]), unratified)

    def test_policy_must_bind_exact_context_identity(self):
        value = context()
        for field, replacement in (
            ("measurement_identity", "other measurement"),
            ("unit", "USD"),
            ("comparison_basis", "other basis"),
        ):
            changed = policy(value)
            changed["rules"][0][field] = replacement
            result = VR.build_packet(payload(contexts=[value]), changed)["candidate_contexts"][0]["valuation"]["contexts"][0]
            self.assertEqual(result["interpretation_status"], "EXACT_RULE_IDENTITY_MISMATCH")
            self.assertFalse(result["deterioration_policy_match"])

    def test_valuation_requires_fundamental_and_price_source_groups(self):
        value = context()
        value["evidence_points"][0]["source_identities"] = [source("sec_edgar", "a")]
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "REQUIRED_SOURCE_GROUP_MISSING"):
            VR.build_packet(payload(contexts=[value]))

    def test_market_specific_risk_sources_and_crypto_valuation_boundary(self):
        korea_candidate = candidate(case_id="RADAR-MB-KOREA", market="KOREA", asset_id="KRX:005930")
        crypto_candidate = candidate(case_id="RADAR-MB-CRYPTO", market="CRYPTO", asset_id="CRYPTO:BTCUSD")
        contexts = [
            context(dimension="RISK", context_id="KOREA.005930.RISK.DD", case_id=korea_candidate["case_id"], market="KOREA", asset_id=korea_candidate["asset_id"], values=("0.1", "0.2")),
            context(dimension="RISK", context_id="CRYPTO.BTC.RISK.DD", case_id=crypto_candidate["case_id"], market="CRYPTO", asset_id=crypto_candidate["asset_id"], values=("0.2", "0.4")),
        ]
        packet = VR.build_packet(payload(candidates=[korea_candidate, crypto_candidate], contexts=contexts))
        self.assertEqual([item["candidate_ref"]["market"] for item in packet["candidate_contexts"]], ["CRYPTO", "KOREA"])
        self.assertEqual(packet["source_coverage"]["CRYPTO"]["VALUATION"], "UNDEFINED_NO_RATIFIED_METRIC")

        invalid = copy.deepcopy(contexts[1])
        invalid["dimension"] = "VALUATION"
        invalid["metric_type"] = "EARNINGS_MULTIPLE"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "CONTEXT_METRIC_TYPE_INVALID"):
            VR.build_packet(payload(candidates=[crypto_candidate], contexts=[invalid]))

    def test_risk_values_are_nonnegative_but_valuation_may_preserve_negative(self):
        risk = context(dimension="RISK", context_id="US.TEST.RISK.RV", values=("0.2", "-0.1"))
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "RISK_VALUE_NEGATIVE"):
            VR.build_packet(payload(contexts=[risk]))
        valuation = context(values=("-2", "-1"))
        result = VR.build_packet(payload(contexts=[valuation]))["candidate_contexts"][0]["valuation"]["contexts"][0]
        self.assertEqual(result["values"], ["-2.000000000000", "-1.000000000000"])

    def test_candidate_reference_is_immutable_lineage_and_strict(self):
        original = candidate()
        packet = VR.build_packet(payload(candidates=[original], contexts=[]))
        self.assertEqual(packet["candidate_contexts"][0]["candidate_ref"], original)
        bad_schema = candidate(schema="unknown_case/1")
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "CASE_SCHEMA_VERSION_INVALID"):
            VR.build_packet(payload(candidates=[bad_schema], contexts=[]))
        bad_hash = candidate()
        bad_hash["case_payload_sha256"] = "bad"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "CASE_PAYLOAD_SHA256_INVALID"):
            VR.build_packet(payload(candidates=[bad_hash], contexts=[]))

    def test_context_candidate_identity_and_reference_fail_closed(self):
        unknown = context(case_id="RADAR-MB-UNKNOWN")
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "CASE_REF_UNKNOWN"):
            VR.build_packet(payload(contexts=[unknown]))
        mismatch = context()
        mismatch["asset_id"] = "US:XNAS:OTHER"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "IDENTITY_MISMATCH"):
            VR.build_packet(payload(contexts=[mismatch]))

    def test_source_host_hash_time_and_duplicate_fail_closed(self):
        wrong_host = context()
        wrong_host["evidence_points"][0]["source_identities"][0]["source_url"] = "https://example.com/x"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "SOURCE_URL_INVALID"):
            VR.build_packet(payload(contexts=[wrong_host]))
        bad_hash = context()
        bad_hash["evidence_points"][0]["source_identities"][0]["source_sha256"] = "bad"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "SOURCE_SHA256_INVALID"):
            VR.build_packet(payload(contexts=[bad_hash]))
        future = context()
        future["evidence_points"][0]["source_identities"][0]["retrieved_at_utc"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            VR.build_packet(payload(contexts=[future]))
        before_period = context()
        before_period["evidence_points"][0]["source_identities"][0]["available_at"] = "2026-07-30"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            VR.build_packet(payload(contexts=[before_period]))
        duplicate = context()
        duplicate["evidence_points"][0]["source_identities"].append(copy.deepcopy(duplicate["evidence_points"][0]["source_identities"][0]))
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "SOURCE_ID_DUPLICATE"):
            VR.build_packet(payload(contexts=[duplicate]))

    def test_evidence_shapes_float_nan_period_and_status_fail_closed(self):
        floating = context()
        floating["evidence_points"][0]["numeric_value"] = 20.0
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "DECIMAL_NOT_STRING"):
            VR.build_packet(payload(contexts=[floating]))
        nan = context()
        nan["evidence_points"][0]["numeric_value"] = "NaN"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "DECIMAL_INVALID"):
            VR.build_packet(payload(contexts=[nan]))
        duplicate_period = context()
        duplicate_period["expected_periods"][1] = duplicate_period["expected_periods"][0]
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "EXPECTED_PERIODS_INVALID"):
            VR.build_packet(payload(contexts=[duplicate_period]))
        hidden = context()
        hidden["evidence_points"][0] = missing("2026-07-31")
        hidden["evidence_points"][0]["numeric_value"] = "0"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "UNAVAILABLE_EVIDENCE_INCONSISTENT"):
            VR.build_packet(payload(contexts=[hidden]))

    def test_order_is_deterministic_and_duplicate_ids_fail(self):
        second_candidate = candidate(case_id="RADAR-MB-BBBBBBBBBBBBBBBB", asset_id="US:XNAS:OTHER")
        second_context = context(context_id="US.OTHER.VAL.PE", case_id=second_candidate["case_id"], asset_id=second_candidate["asset_id"])
        first_input = payload(candidates=[candidate(), second_candidate], contexts=[context(), second_context])
        first = VR.build_packet(first_input)
        first_input["candidates"].reverse()
        first_input["context_observations"].reverse()
        for item in first_input["context_observations"]:
            item["evidence_points"].reverse()
            for evidence in item["evidence_points"]:
                evidence["source_identities"].reverse()
        second = VR.build_packet(first_input)
        self.assertEqual(VR.canonical_json(first), VR.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, VR.payload_sha256(second))

        with self.assertRaisesRegex(VR.ValuationRiskContextError, "CANDIDATE_DUPLICATE"):
            VR.build_packet(payload(candidates=[candidate(), candidate()], contexts=[]))
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "CONTEXT_ID_DUPLICATE"):
            VR.build_packet(payload(contexts=[context(), copy.deepcopy(context())]))

    def test_policy_scope_threshold_and_point_in_time_fail_closed(self):
        negative = policy()
        negative["rules"][0]["minimum_change"] = "-1"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "DECIMAL_INVALID"):
            VR.build_packet(payload(contexts=[context()]), negative)
        bad_direction = policy()
        bad_direction["rules"][0]["deterioration_direction"] = "AUTO"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "DIRECTION_INVALID"):
            VR.build_packet(payload(contexts=[context()]), bad_direction)
        future = policy()
        future["ratified_at_utc"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "RATIFIED_AFTER_AS_OF"):
            VR.build_packet(payload(contexts=[context()]), future)

    def test_contract_keeps_candidate_rule_portfolio_and_trading_closed(self):
        packet = VR.build_packet(payload())
        self.assertEqual(packet["policy_status"]["default_interpretation_policy"], "ABSENT")
        authority = packet["authority"]
        self.assertTrue(authority["raw_context_attachment_without_policy"])
        self.assertTrue(authority["deterioration_label_only_with_ratified_policy"])
        for field in (
            "candidate_mutation_authorized", "candidate_ranking_authorized",
            "stage_promotion_authorized", "rule_evaluation_authorized",
            "portfolio_action_authorized", "production_authorized", "trading_authorized",
        ):
            self.assertFalse(authority[field])

    def test_contract_and_input_tampering_are_rejected(self):
        contract = VR.load_contract()
        contract["authority"]["stage_promotion_authorized"] = True
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "CONTRACT_FIELD_MISMATCH"):
            VR.build_packet(payload(), contract=contract)
        extra = payload()
        extra["default_metric"] = "PE"
        with self.assertRaisesRegex(VR.ValuationRiskContextError, "INPUT_FIELDS_MISMATCH"):
            VR.build_packet(extra)

    def test_cli_is_temp_only_atomic_and_preserves_output_on_failure(self):
        tracked_before = (ROOT / "data" / "event_records.jsonl").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            policy_path = tmp / "policy.json"
            output_path = tmp / "output.json"
            input_path.write_text(json.dumps(payload(contexts=[context()])), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--policy", str(policy_path), "--out", str(output_path)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["candidate_count"], 1)
            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            input_path.write_text(json.dumps(payload(candidates=[], contexts=[])), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual((ROOT / "data" / "event_records.jsonl").read_bytes(), tracked_before)

        with tempfile.TemporaryDirectory() as raw:
            input_path = Path(raw) / "input.json"
            input_path.write_text(json.dumps(payload()), encoding="utf-8")
            tracked_target = ROOT / "valuation-risk-output.json"
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(tracked_target)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("TRACKED_OUTPUT_FORBIDDEN", result.stdout)
            self.assertFalse(tracked_target.exists())

    def test_module_has_no_network_tracked_output_or_default_policy(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", text)
        self.assertNotIn("urlopen", text)
        self.assertNotIn("data/", text)
        self.assertFalse((ROOT / "config" / "valuation_risk_interpretation_policy.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
