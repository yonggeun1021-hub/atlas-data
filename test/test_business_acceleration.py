"""P3-05 Business Acceleration radar regression."""
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
MODULE_PATH = ROOT / "discovery" / "business_acceleration.py"
SPEC = importlib.util.spec_from_file_location("business_acceleration", MODULE_PATH)
BA = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BA)


MEASUREMENT = "Synthetic published monthly revenue YoY"


def available(period: str, value: str, subject: str = "TSM") -> dict:
    return {
        "schema_version": "evidence_envelope/1",
        "subject": subject,
        "measurement_identity": MEASUREMENT,
        "economic_period_end": period,
        "status": "EVIDENCE_AVAILABLE",
        "reasons": [],
        "consumable": True,
        "blocked_by": [],
        "acquisition_provenance_present": True,
        "source_identity": {
            "source_id": "tsmc_ir_monthly_revenue",
            "source_url": "https://investor.tsmc.com/english/monthly-revenue/2026",
            "source_sha256": (period.replace("-", "") + "a" * 64)[:64],
            "available_at": "2026-08-15",
            "retrieved_at_utc": "2026-08-15T01:00:00Z",
        },
        "audit_provenance": {"capture_kind": "LIVE_OFFICIAL_CAPTURE"},
        "observation": {
            "raw_value": f"{value}%",
            "numeric_value": value,
            "unit": "pct",
            "observed_by": "synthetic-test-adapter",
        },
    }


def unavailable(period: str, status: str = "EVIDENCE_UNRESOLVED") -> dict:
    return {
        "schema_version": "evidence_envelope/1",
        "subject": "TSM",
        "measurement_identity": MEASUREMENT,
        "economic_period_end": period,
        "status": status,
        "reasons": ["OBSERVATION_ABSENT"],
        "consumable": False,
        "blocked_by": [],
        "acquisition_provenance_present": False,
        "source_identity": None,
        "audit_provenance": None,
        "observation": None,
    }


def series(values=("10", "20", "30"), series_id="TSM_MONTHLY_REVENUE_GROWTH") -> dict:
    periods = ("2026-05-31", "2026-06-30", "2026-07-31")
    return {
        "series_id": series_id,
        "asset_id": "US:XNYS:TSM",
        "subject": "TSM",
        "metric_type": "REVENUE_GROWTH",
        "measurement_identity": MEASUREMENT,
        "frequency": "MONTHLY",
        "comparison_basis": "published monthly YoY percent, unchanged basis",
        "evidence_points": [available(period, value) for period, value in zip(periods, values)],
    }


def payload(rows=None) -> dict:
    return {
        "schema_version": "business_acceleration_radar_input/1",
        "as_of_utc": "2026-08-20T00:00:00Z",
        "series": rows if rows is not None else [series()],
    }


def rehash(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = BA.payload_sha256(value)
    return value


class BusinessAccelerationTests(unittest.TestCase):
    def test_two_consecutive_growth_rate_increases_create_radar_case(self):
        packet = BA.build_packet(payload())
        self.assertEqual(packet["case_count"], 1)
        result = packet["series_results"][0]
        self.assertEqual(result["pattern"], "TWO_STEP_ACCELERATION_OBSERVED")
        self.assertEqual(
            result["values_pct"],
            ["10.000000000000", "20.000000000000", "30.000000000000"],
        )
        self.assertEqual(result["prior_change_pp"], "10.000000000000")
        self.assertEqual(result["latest_change_pp"], "10.000000000000")
        case = packet["cases"][0]
        self.assertTrue(case["case_id"].startswith("RADAR-BA-"))
        self.assertEqual(len(case["confirmed_evidence"]), 3)
        self.assertEqual(case["importance"], "UNRATIFIED")
        self.assertFalse(case["candidate_eligible"])
        self.assertIsNone(case["candidate_rank"])
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["action"])

    def test_standalone_validator_accepts_persisted_packet(self):
        packet = BA.build_packet(payload())
        checked = BA.validate_packet(copy.deepcopy(packet))
        self.assertEqual(BA.canonical_json(checked), BA.canonical_json(packet))

    def test_standalone_validator_rejects_rehashed_arithmetic_tamper(self):
        # Tamper values_pct AND evidence_source in tandem (so the new
        # source-backed cross-check agrees) to isolate the pre-existing
        # arithmetic/pattern re-derivation check.
        packet = BA.build_packet(payload())
        packet["series_results"][0]["values_pct"][2] = "31.000000000000"
        packet["series_results"][0]["evidence_source"][2]["numeric_value"] = "31"
        with self.assertRaisesRegex(
            BA.BusinessAccelerationError, "OUTPUT_PATTERN_DERIVATION_MISMATCH"
        ):
            BA.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_values_pct_unbacked_by_evidence_source(self):
        # Tampering values_pct WITHOUT touching evidence_source must be
        # caught by the standalone source-backed re-proof -- not just by
        # self-consistent arithmetic -- for a series that never created a
        # case (LATEST_STEP_NOT_UP, no case exists to independently
        # corroborate the numbers).
        packet = BA.build_packet(payload([series(("10", "20", "19"))]))
        self.assertEqual(packet["series_results"][0]["pattern"], "LATEST_STEP_NOT_UP")
        packet["series_results"][0]["values_pct"][2] = "50.000000000000"
        with self.assertRaisesRegex(
            BA.BusinessAccelerationError, "OUTPUT_EVIDENCE_SOURCE_VALUE_MISMATCH"
        ):
            BA.validate_packet(rehash(packet))

    def test_non_case_series_persists_and_standalone_revalidates_evidence_source(self):
        # A LATEST_STEP_UP_ONLY series creates no case, yet its
        # series_result must still carry a frozen evidence_source snapshot
        # sufficient for validate_packet() to independently reprove source
        # completeness -- the exact limitation reported for non-case
        # packets before this fix.
        packet = BA.build_packet(payload([series(("20", "10", "15"))]))
        result = packet["series_results"][0]
        self.assertEqual(result["pattern"], "LATEST_STEP_UP_ONLY")
        self.assertEqual(packet["case_count"], 0)
        self.assertEqual(len(result["evidence_source"]), 3)
        for row in result["evidence_source"]:
            self.assertEqual(row["source_identity"]["source_id"], "tsmc_ir_monthly_revenue")
        checked = BA.validate_packet(copy.deepcopy(packet))
        self.assertEqual(BA.canonical_json(checked), BA.canonical_json(packet))

        # Standalone source re-validation (bad host) fires for a non-case
        # series too, not only for case-creating ones.
        tampered = copy.deepcopy(packet)
        tampered["series_results"][0]["evidence_source"][0]["source_identity"][
            "source_url"
        ] = "https://www.sec.gov/Archives/not-tsmc"
        with self.assertRaisesRegex(
            BA.BusinessAccelerationError, "SOURCE_URL_INVALID"
        ):
            BA.validate_packet(rehash(tampered))

    def test_unknown_evidence_series_has_no_evidence_source(self):
        value = series()
        value["evidence_points"][1] = unavailable("2026-06-30")
        packet = BA.build_packet(payload([value]))
        result = packet["series_results"][0]
        self.assertIsNone(result["evidence_source"])
        tampered = copy.deepcopy(packet)
        tampered["series_results"][0]["evidence_source"] = []
        with self.assertRaisesRegex(
            BA.BusinessAccelerationError, "OUTPUT_UNKNOWN_PATTERN_MISMATCH"
        ):
            BA.validate_packet(rehash(tampered))

    def test_standalone_validator_rejects_rehashed_case_evidence_tamper(self):
        packet = BA.build_packet(payload())
        packet["cases"][0]["confirmed_evidence"][2]["numeric_value"] = "31"
        with self.assertRaisesRegex(
            BA.BusinessAccelerationError, "OUTPUT_CASE_EVIDENCE_VALUE_MISMATCH"
        ):
            BA.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_case_authority_expansion(self):
        packet = BA.build_packet(payload())
        packet["cases"][0]["candidate_eligible"] = True
        with self.assertRaisesRegex(
            BA.BusinessAccelerationError, "OUTPUT_CASE_AUTHORITY_EXPANSION"
        ):
            BA.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_pattern_count_tamper(self):
        packet = BA.build_packet(payload())
        packet["pattern_counts"]["TWO_STEP_ACCELERATION_OBSERVED"] = 0
        with self.assertRaisesRegex(
            BA.BusinessAccelerationError, "OUTPUT_SUMMARY_OR_BOUNDARY_MISMATCH"
        ):
            BA.validate_packet(rehash(packet))

    def test_latest_step_only_and_non_up_do_not_create_cases(self):
        latest_only = BA.build_packet(payload([series(("20", "10", "15"))]))
        self.assertEqual(latest_only["series_results"][0]["pattern"], "LATEST_STEP_UP_ONLY")
        self.assertEqual(latest_only["case_count"], 0)

        not_up = BA.build_packet(payload([series(("10", "20", "19"))]))
        self.assertEqual(not_up["series_results"][0]["pattern"], "LATEST_STEP_NOT_UP")
        self.assertEqual(not_up["case_count"], 0)

    def test_unavailable_evidence_is_unknown_not_zero_or_neutral(self):
        value = series()
        value["evidence_points"][1] = unavailable("2026-06-30")
        packet = BA.build_packet(payload([value]))
        result = packet["series_results"][0]
        self.assertEqual(result["pattern"], "UNKNOWN_EVIDENCE")
        self.assertIsNone(result["values_pct"])
        self.assertIsNone(result["latest_change_pp"])
        self.assertEqual(result["unavailable_evidence"][0]["status"], "EVIDENCE_UNRESOLVED")
        self.assertEqual(packet["case_count"], 0)

    def test_periods_must_be_consecutive_month_ends(self):
        gap = series()
        gap["evidence_points"][1]["economic_period_end"] = "2026-05-31"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "PERIOD_DUPLICATE"):
            BA.build_packet(payload([gap]))

        skip = series()
        skip["evidence_points"][1]["economic_period_end"] = "2026-04-30"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "PERIOD_NOT_CONSECUTIVE"):
            BA.build_packet(payload([skip]))

        not_end = series()
        not_end["evidence_points"][1]["economic_period_end"] = "2026-06-29"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "PERIOD_NOT_CONSECUTIVE"):
            BA.build_packet(payload([not_end]))

    def test_consecutive_quarterly_periods_are_supported(self):
        value = series()
        value["series_id"] = "MSFT_QUARTERLY_REVENUE_GROWTH"
        value["asset_id"] = "US:XNAS:MSFT"
        value["subject"] = "MSFT"
        value["frequency"] = "QUARTERLY"
        periods = ("2025-12-31", "2026-03-31", "2026-06-30")
        value["evidence_points"] = [
            available(period, amount, subject="MSFT")
            for period, amount in zip(periods, ("20", "21", "23"))
        ]
        packet = BA.build_packet(payload([value]))
        self.assertEqual(packet["case_count"], 1)

    def test_input_and_evidence_order_do_not_change_output(self):
        first_series = series(series_id="TSM_MONTHLY_REVENUE_GROWTH")
        second_series = series(("5", "4", "3"), series_id="TSM_MONTHLY_ORDER_GROWTH")
        second_series["metric_type"] = "ORDER_GROWTH"
        first = BA.build_packet(payload([first_series, second_series]))
        first_series["evidence_points"].reverse()
        second_series["evidence_points"].reverse()
        second = BA.build_packet(payload([second_series, first_series]))
        self.assertEqual(BA.canonical_json(first), BA.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, BA.payload_sha256(second))

    def test_duplicate_series_subject_measurement_and_unit_fail_closed(self):
        duplicate = series()
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "SERIES_ID_DUPLICATE"):
            BA.build_packet(payload([duplicate, copy.deepcopy(duplicate)]))

        subject = series()
        subject["evidence_points"][0]["subject"] = "OTHER"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "SUBJECT_MISMATCH"):
            BA.build_packet(payload([subject]))

        measurement = series()
        measurement["evidence_points"][0]["measurement_identity"] = "different"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "MEASUREMENT_MISMATCH"):
            BA.build_packet(payload([measurement]))

        unit = series()
        unit["evidence_points"][0]["observation"]["unit"] = "USD"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "UNIT_MISMATCH"):
            BA.build_packet(payload([unit]))

    def test_float_nan_and_available_status_inconsistency_fail_closed(self):
        floating = series()
        floating["evidence_points"][0]["observation"]["numeric_value"] = 10.0
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "NOT_STRING"):
            BA.build_packet(payload([floating]))

        nan = series()
        nan["evidence_points"][0]["observation"]["numeric_value"] = "NaN"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "NUMERIC_VALUE_INVALID"):
            BA.build_packet(payload([nan]))

        inconsistent = series()
        inconsistent["evidence_points"][0]["consumable"] = False
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "AVAILABLE_INCONSISTENT"):
            BA.build_packet(payload([inconsistent]))

    def test_source_identity_and_as_of_temporal_order_fail_closed(self):
        unknown = series()
        unknown["evidence_points"][0]["source_identity"]["source_id"] = "unknown"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "SOURCE_ID_NOT_REGISTERED"):
            BA.build_packet(payload([unknown]))

        wrong_host = series()
        wrong_host["evidence_points"][0]["source_identity"]["source_url"] = (
            "https://www.sec.gov/Archives/not-tsmc"
        )
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "SOURCE_URL_INVALID"):
            BA.build_packet(payload([wrong_host]))

        bad_sha = series()
        bad_sha["evidence_points"][0]["source_identity"]["source_sha256"] = "bad"
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "SOURCE_SHA256_INVALID"):
            BA.build_packet(payload([bad_sha]))

        future = series()
        future["evidence_points"][0]["source_identity"]["retrieved_at_utc"] = (
            "2026-08-21T00:00:00Z"
        )
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "TEMPORAL_ORDER_INVALID"):
            BA.build_packet(payload([future]))

    def test_unavailable_envelope_cannot_hide_consumable_observation(self):
        value = series()
        hidden = unavailable("2026-06-30", status="EVIDENCE_BLOCKED")
        hidden["consumable"] = True
        hidden["observation"] = {"numeric_value": "999", "unit": "pct"}
        value["evidence_points"][1] = hidden
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "UNAVAILABLE_INCONSISTENT"):
            BA.build_packet(payload([value]))

    def test_contract_keeps_ranking_stage_production_and_trading_closed(self):
        packet = BA.build_packet(payload())
        authority = packet["authority"]
        self.assertTrue(authority["radar_case_recording_only"])
        for field in (
            "source_ranking_authorized",
            "importance_ranking_authorized",
            "candidate_ranking_authorized",
            "stage_promotion_authorized",
            "rule_evaluation_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertFalse(authority[field])
        self.assertEqual(packet["policy_status"]["importance_threshold"], "UNRATIFIED")
        self.assertEqual(packet["policy_status"]["source_hierarchy"], "UNRATIFIED")

    def test_contract_tampering_is_rejected_for_file_and_api(self):
        contract = BA.load_contract()
        contract["authority"]["stage_promotion_authorized"] = True
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(BA.BusinessAccelerationError, "CONTRACT_FIELD_MISMATCH"):
                BA.load_contract(path)
        with self.assertRaisesRegex(BA.BusinessAccelerationError, "CONTRACT_FIELD_MISMATCH"):
            BA.build_packet(payload(), contract)

    def test_cli_is_temp_only_atomic_and_preserves_output_on_failure(self):
        tracked_before = (ROOT / "data" / "event_records.jsonl").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            output_path = tmp / "output.json"
            input_path.write_text(json.dumps(payload()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["case_count"], 1)

            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            broken = payload()
            broken["series"][0]["evidence_points"] = []
            input_path.write_text(json.dumps(broken), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual((ROOT / "data" / "event_records.jsonl").read_bytes(), tracked_before)

    def test_module_has_no_network_or_tracked_default_output(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", text)
        self.assertNotIn("urlopen", text)
        self.assertNotIn("data/", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
