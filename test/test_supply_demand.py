"""P3-09 policy-gated Supply-Demand / Scarcity radar regression."""
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
MODULE_PATH = ROOT / "discovery" / "supply_demand.py"
SPEC = importlib.util.spec_from_file_location("supply_demand", MODULE_PATH)
SD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SD)


MARKET_META = {
    "US": ("sec_edgar", "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"),
    "KOREA": ("krx_information_data_system_pykrx", "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"),
    "CRYPTO": ("defillama_stablecoins_api", "https://stablecoins.llama.fi/stablecoincharts/all"),
}


def source(market: str, marker: str) -> dict:
    source_id, url = MARKET_META[market]
    return {
        "source_id": source_id,
        "source_url": url,
        "source_sha256": marker * 64,
        "available_at": "2026-08-19",
        "retrieved_at_utc": "2026-08-19T23:00:00Z",
    }


def point(period: str, value: str, market: str = "US", marker: str = "a") -> dict:
    return {
        "period_end": period,
        "status": "EVIDENCE_AVAILABLE",
        "numeric_value": value,
        "missing_reasons": [],
        "source_identity": source(market, marker),
    }


def missing(period: str) -> dict:
    return {
        "period_end": period,
        "status": "EVIDENCE_UNRESOLVED",
        "numeric_value": None,
        "missing_reasons": ["EXACT_PERIOD_OBSERVATION_ABSENT"],
        "source_identity": None,
    }


def series(
    values=("100", "110", "130"),
    *,
    market="US",
    series_id="US.TEST.SECURITY_SUPPLY",
    asset_id="US:XNAS:TEST",
) -> dict:
    periods = ["2026-06-30", "2026-07-31", "2026-08-19"]
    market_fields = {
        "US": ("SECURITY_SUPPLY", "shares", "quarterly reported shares outstanding"),
        "KOREA": ("INVESTOR_NET_DEMAND_VALUE", "KRW", "KRX-only foreign investor net trading value"),
        "CRYPTO": ("AGGREGATE_TOKEN_SUPPLY", "USD_PEGGED_TOKEN", "same-vintage native USD-peg aggregate supply"),
    }
    metric_type, unit, measurement = market_fields[market]
    return {
        "series_id": series_id,
        "market": market,
        "asset_id": asset_id,
        "measurement_identity": measurement,
        "metric_type": metric_type,
        "unit": unit,
        "frequency": "IRREGULAR",
        "comparison_basis": "three exact caller-declared evidence dates, no fill",
        "expected_periods": periods,
        "evidence_points": [
            point(period, value, market, marker)
            for period, value, marker in zip(periods, values, ("a", "b", "c"))
        ],
    }


def payload(rows=None) -> dict:
    return {
        "schema_version": "supply_demand_radar_input/1",
        "as_of_utc": "2026-08-20T00:00:00Z",
        "series": rows if rows is not None else [series()],
    }


def policy(value=None, *, status="RATIFIED", direction="HIGHER_IS_IMPROVEMENT") -> dict:
    value = series() if value is None else value
    rule = {
        "market": value["market"],
        "series_id": value["series_id"],
        "measurement_identity": value["measurement_identity"],
        "metric_type": value["metric_type"],
        "unit": value["unit"],
        "frequency": value["frequency"],
        "comparison_basis": value["comparison_basis"],
        "improvement_direction": direction,
        "minimum_latest_change": "10",
        "minimum_acceleration_change": "5",
    }
    return {
        "schema_version": "supply_demand_candidate_policy/1",
        "policy_id": "POLICY.SD.1",
        "approval_status": status,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "ratified_by": "CIO" if status == "RATIFIED" else None,
        "ratified_at_utc": "2026-08-19T00:00:00Z" if status == "RATIFIED" else None,
        "rules": [rule],
    }


def rehash(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = SD.payload_sha256(value)
    return value


class SupplyDemandTests(unittest.TestCase):
    def test_raw_features_without_policy_create_no_case(self):
        packet = SD.build_packet(payload())
        self.assertEqual(packet["status"], "SUPPLY_DEMAND_FEATURES_OBSERVED")
        self.assertEqual(packet["case_count"], 0)
        self.assertIsNone(packet["candidate_policy"])
        result = packet["series_results"][0]
        self.assertEqual(result["values"], ["100.000000000000", "110.000000000000", "130.000000000000"])
        self.assertEqual(result["prior_change"], "10.000000000000")
        self.assertEqual(result["latest_change"], "20.000000000000")
        self.assertEqual(result["acceleration_change"], "10.000000000000")
        self.assertEqual(
            [item["source_identity"]["source_sha256"] for item in result["evidence_lineage"]],
            ["a" * 64, "b" * 64, "c" * 64],
        )
        self.assertEqual(result["candidate_policy_status"], "ABSENT_OR_UNRATIFIED")
        self.assertIsNone(result["candidate_policy_match"])

    def test_ratified_higher_policy_creates_lineage_complete_case(self):
        value = series()
        packet = SD.build_packet(payload([value]), policy(value))
        self.assertEqual(packet["case_count"], 1)
        result = packet["series_results"][0]
        self.assertEqual(result["candidate_policy_status"], "RATIFIED_EXACT_RULE_APPLIED")
        self.assertTrue(result["candidate_policy_match"])
        case = packet["cases"][0]
        self.assertTrue(case["case_id"].startswith("RADAR-SD-"))
        self.assertEqual(len(case["confirmed_evidence"]), 3)
        self.assertEqual(case["confirmed_evidence"][2]["source_identity"]["source_sha256"], "c" * 64)
        self.assertEqual(case["candidate_policy"]["policy_id"], "POLICY.SD.1")
        self.assertEqual(case["importance"], "UNRATIFIED")
        self.assertFalse(case["investable_eligible"])
        self.assertIsNone(case["candidate_rank"])
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["action"])

    def test_standalone_validator_accepts_raw_policy_and_unknown_packets(self):
        unknown = series()
        unknown["evidence_points"][1] = missing("2026-07-31")
        packets = (
            SD.build_packet(payload()),
            SD.build_packet(payload(), policy()),
            SD.build_packet(payload([unknown]), policy(unknown)),
        )
        for packet in packets:
            with self.subTest(status=packet["series_results"][0]["feature_status"]):
                checked = SD.validate_packet(copy.deepcopy(packet))
                self.assertEqual(SD.canonical_json(checked), SD.canonical_json(packet))

    def test_standalone_validator_rejects_rehashed_arithmetic_tamper(self):
        packet = SD.build_packet(payload())
        packet["series_results"][0]["latest_change"] = "21.000000000000"
        with self.assertRaisesRegex(SD.SupplyDemandError, "OUTPUT_FEATURE_ARITHMETIC_MISMATCH"):
            SD.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_lineage_tamper(self):
        packet = SD.build_packet(payload())
        packet["series_results"][0]["evidence_lineage"][0]["source_identity"][
            "source_url"
        ] = "https://example.com/not-provider"
        with self.assertRaisesRegex(SD.SupplyDemandError, "SOURCE_URL_INVALID"):
            SD.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_case_evidence_tamper(self):
        packet = SD.build_packet(payload(), policy())
        packet["cases"][0]["confirmed_evidence"][2]["numeric_value"] = "131"
        with self.assertRaisesRegex(SD.SupplyDemandError, "OUTPUT_CASE_EVIDENCE_MISMATCH"):
            SD.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_source_policy_threshold_tamper(self):
        packet = SD.build_packet(payload(), policy())
        packet["source_policy"]["rules"][0]["minimum_latest_change"] = "100"
        forged_sha = SD.payload_sha256(packet["source_policy"])
        packet["candidate_policy"]["policy_sha256"] = forged_sha
        packet["cases"][0]["candidate_policy"]["policy_sha256"] = forged_sha
        with self.assertRaisesRegex(SD.SupplyDemandError, "OUTPUT_POLICY_RESULT_MISMATCH"):
            SD.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_authority_expansion(self):
        packet = SD.build_packet(payload(), policy())
        packet["cases"][0]["investable_eligible"] = True
        with self.assertRaisesRegex(SD.SupplyDemandError, "OUTPUT_CASE_AUTHORITY_EXPANSION"):
            SD.validate_packet(rehash(packet))

    def test_lower_is_improvement_is_only_external_policy_semantics(self):
        value = series(("130", "120", "100"))
        lower = policy(value, direction="LOWER_IS_IMPROVEMENT")
        packet = SD.build_packet(payload([value]), lower)
        self.assertEqual(packet["case_count"], 1)
        self.assertEqual(packet["cases"][0]["why_found"]["improvement_direction"], "LOWER_IS_IMPROVEMENT")

        higher = policy(value, direction="HIGHER_IS_IMPROVEMENT")
        self.assertEqual(SD.build_packet(payload([value]), higher)["case_count"], 0)

    def test_thresholds_are_direction_adjusted_and_exact(self):
        value = series()
        exact = policy(value)
        exact["rules"][0]["minimum_latest_change"] = "20"
        exact["rules"][0]["minimum_acceleration_change"] = "10"
        self.assertEqual(SD.build_packet(payload([value]), exact)["case_count"], 1)
        exact["rules"][0]["minimum_acceleration_change"] = "10.0001"
        self.assertEqual(SD.build_packet(payload([value]), exact)["case_count"], 0)

    def test_unknown_evidence_is_not_zero_neutral_or_case(self):
        value = series()
        value["evidence_points"][1] = missing("2026-07-31")
        packet = SD.build_packet(payload([value]), policy(value))
        result = packet["series_results"][0]
        self.assertEqual(result["feature_status"], "UNKNOWN_EVIDENCE")
        self.assertIsNone(result["values"])
        self.assertIsNone(result["latest_change"])
        self.assertEqual(result["unavailable_evidence"][0]["missing_reasons"], ["EXACT_PERIOD_OBSERVATION_ABSENT"])
        self.assertEqual(result["candidate_policy_status"], "NOT_EVALUATED_UNKNOWN_EVIDENCE")
        self.assertIsNone(result["evidence_lineage"][1]["source_identity"])
        self.assertEqual(packet["case_count"], 0)

    def test_unratified_policy_never_creates_case_or_carries_fake_proof(self):
        unratified = policy(status="UNRATIFIED")
        packet = SD.build_packet(payload(), unratified)
        self.assertEqual(packet["case_count"], 0)
        self.assertEqual(packet["series_results"][0]["candidate_policy_status"], "ABSENT_OR_UNRATIFIED")
        unratified["ratified_by"] = "CIO"
        with self.assertRaisesRegex(SD.SupplyDemandError, "PROOF_FORBIDDEN"):
            SD.build_packet(payload(), unratified)

    def test_policy_must_bind_exact_measurement_basis_unit_and_frequency(self):
        value = series()
        for field, replacement in (
            ("measurement_identity", "another measurement"),
            ("comparison_basis", "another basis"),
            ("unit", "USD"),
            ("frequency", "MONTHLY"),
        ):
            changed = policy(value)
            changed["rules"][0][field] = replacement
            packet = SD.build_packet(payload([value]), changed)
            self.assertEqual(packet["case_count"], 0)
            self.assertEqual(packet["series_results"][0]["candidate_policy_status"], "EXACT_RULE_IDENTITY_MISMATCH")

    def test_all_markets_have_distinct_registered_source_and_metric_boundaries(self):
        rows = [
            series(),
            series(market="KOREA", series_id="KOREA.005930.FOREIGN.NET.VALUE", asset_id="KRX:005930"),
            series(market="CRYPTO", series_id="CRYPTO.STABLECOIN.NATIVE.SUPPLY", asset_id="CRYPTO:USD_PEGGED_AGGREGATE"),
        ]
        packet = SD.build_packet(payload(rows))
        self.assertEqual([item["market"] for item in packet["series_results"]], ["CRYPTO", "KOREA", "US"])
        self.assertEqual(packet["source_coverage"]["KOREA"], "PARTIAL_KRX_ONLY_RELEASE_TIME_UNVERIFIED")
        self.assertEqual(packet["source_coverage"]["CRYPTO"], "OPERATIONAL_PIT_POPULATION_WIRED")
        self.assertIn("US_METRIC_SERIES_NOT_SELECTED", packet["unresolved_boundaries"])

    def test_source_identity_host_hash_and_temporal_order_fail_closed(self):
        wrong_id = series()
        wrong_id["evidence_points"][0]["source_identity"]["source_id"] = "defillama_stablecoins_api"
        with self.assertRaisesRegex(SD.SupplyDemandError, "SOURCE_ID_MISMATCH"):
            SD.build_packet(payload([wrong_id]))
        wrong_host = series()
        wrong_host["evidence_points"][0]["source_identity"]["source_url"] = "https://example.com/x"
        with self.assertRaisesRegex(SD.SupplyDemandError, "SOURCE_URL_INVALID"):
            SD.build_packet(payload([wrong_host]))
        bad_hash = series()
        bad_hash["evidence_points"][0]["source_identity"]["source_sha256"] = "bad"
        with self.assertRaisesRegex(SD.SupplyDemandError, "SOURCE_SHA256_INVALID"):
            SD.build_packet(payload([bad_hash]))
        future = series()
        future["evidence_points"][0]["source_identity"]["retrieved_at_utc"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(SD.SupplyDemandError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            SD.build_packet(payload([future]))

    def test_missing_and_available_statuses_are_strict(self):
        hidden = series()
        hidden["evidence_points"][0] = missing("2026-06-30")
        hidden["evidence_points"][0]["numeric_value"] = "0"
        with self.assertRaisesRegex(SD.SupplyDemandError, "UNAVAILABLE_EVIDENCE_INCONSISTENT"):
            SD.build_packet(payload([hidden]))
        unexplained = series()
        unexplained["evidence_points"][0] = missing("2026-06-30")
        unexplained["evidence_points"][0]["missing_reasons"] = []
        with self.assertRaisesRegex(SD.SupplyDemandError, "UNAVAILABLE_EVIDENCE_INCONSISTENT"):
            SD.build_packet(payload([unexplained]))
        contradictory = series()
        contradictory["evidence_points"][0]["missing_reasons"] = ["NOT_REALLY_AVAILABLE"]
        with self.assertRaisesRegex(SD.SupplyDemandError, "AVAILABLE_EVIDENCE_INCONSISTENT"):
            SD.build_packet(payload([contradictory]))

    def test_input_period_and_numeric_shapes_fail_closed(self):
        floating = series()
        floating["evidence_points"][0]["numeric_value"] = 100.0
        with self.assertRaisesRegex(SD.SupplyDemandError, "DECIMAL_NOT_STRING"):
            SD.build_packet(payload([floating]))
        nan = series()
        nan["evidence_points"][0]["numeric_value"] = "NaN"
        with self.assertRaisesRegex(SD.SupplyDemandError, "DECIMAL_INVALID"):
            SD.build_packet(payload([nan]))
        duplicate_period = series()
        duplicate_period["expected_periods"][1] = duplicate_period["expected_periods"][0]
        with self.assertRaisesRegex(SD.SupplyDemandError, "EXPECTED_PERIODS_INVALID"):
            SD.build_packet(payload([duplicate_period]))
        coverage_gap = series()
        coverage_gap["evidence_points"][1]["period_end"] = "2026-07-30"
        with self.assertRaisesRegex(SD.SupplyDemandError, "PERIOD_COVERAGE_MISMATCH"):
            SD.build_packet(payload([coverage_gap]))

    def test_order_is_deterministic_and_duplicate_series_fails(self):
        us = series()
        crypto = series(market="CRYPTO", series_id="CRYPTO.STABLECOIN.NATIVE.SUPPLY", asset_id="CRYPTO:USD_PEGGED_AGGREGATE")
        first_input = payload([us, crypto])
        first = SD.build_packet(first_input)
        first_input["series"].reverse()
        for item in first_input["series"]:
            item["evidence_points"].reverse()
        second = SD.build_packet(first_input)
        self.assertEqual(SD.canonical_json(first), SD.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, SD.payload_sha256(second))
        with self.assertRaisesRegex(SD.SupplyDemandError, "SERIES_DUPLICATE"):
            SD.build_packet(payload([us, copy.deepcopy(us)]))

    def test_policy_structure_scope_threshold_and_point_in_time_fail_closed(self):
        negative = policy()
        negative["rules"][0]["minimum_latest_change"] = "-1"
        with self.assertRaisesRegex(SD.SupplyDemandError, "DECIMAL_INVALID"):
            SD.build_packet(payload(), negative)
        bad_direction = policy()
        bad_direction["rules"][0]["improvement_direction"] = "AUTO"
        with self.assertRaisesRegex(SD.SupplyDemandError, "DIRECTION_INVALID"):
            SD.build_packet(payload(), bad_direction)
        duplicate = policy()
        duplicate["rules"].append(copy.deepcopy(duplicate["rules"][0]))
        with self.assertRaisesRegex(SD.SupplyDemandError, "RULE_DUPLICATE"):
            SD.build_packet(payload(), duplicate)
        future = policy()
        future["ratified_at_utc"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(SD.SupplyDemandError, "RATIFIED_AFTER_AS_OF"):
            SD.build_packet(payload(), future)

    def test_contract_keeps_policy_ranking_stage_production_and_trading_closed(self):
        packet = SD.build_packet(payload())
        self.assertEqual(packet["policy_status"]["default_candidate_policy"], "ABSENT")
        self.assertEqual(packet["policy_status"]["improvement_direction"], "UNRATIFIED")
        authority = packet["authority"]
        self.assertTrue(authority["raw_feature_observation_only_without_ratified_policy"])
        self.assertTrue(authority["radar_case_recording_only_with_ratified_policy"])
        for field in (
            "source_ranking_authorized", "cross_market_scoring_authorized",
            "importance_ranking_authorized", "candidate_ranking_authorized",
            "stage_promotion_authorized", "rule_evaluation_authorized",
            "production_authorized", "trading_authorized",
        ):
            self.assertFalse(authority[field])

    def test_contract_and_input_tampering_are_rejected(self):
        contract = SD.load_contract()
        contract["authority"]["stage_promotion_authorized"] = True
        with self.assertRaisesRegex(SD.SupplyDemandError, "CONTRACT_FIELD_MISMATCH"):
            SD.build_packet(payload(), contract=contract)
        extra = payload()
        extra["default_direction"] = "HIGHER"
        with self.assertRaisesRegex(SD.SupplyDemandError, "INPUT_FIELDS_MISMATCH"):
            SD.build_packet(extra)

    def test_cli_is_temp_only_atomic_and_preserves_output_on_failure(self):
        tracked_before = (ROOT / "data" / "event_records.jsonl").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            policy_path = tmp / "policy.json"
            output_path = tmp / "output.json"
            input_path.write_text(json.dumps(payload()), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--policy", str(policy_path), "--out", str(output_path)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["case_count"], 1)
            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            input_path.write_text(json.dumps(payload([])), encoding="utf-8")
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
            tracked_target = ROOT / "supply-demand-output.json"
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
        self.assertFalse((ROOT / "config" / "supply_demand_candidate_policy.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
