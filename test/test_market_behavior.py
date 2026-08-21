"""P3-07 policy-gated Market Behavior radar regression."""
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
MODULE_PATH = ROOT / "discovery" / "market_behavior.py"
SPEC = importlib.util.spec_from_file_location("market_behavior", MODULE_PATH)
MB = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MB)


MARKET_META = {
    "US": ("tiingo_us_daily_price", "https://api.tiingo.com/tiingo/daily/test"),
    "KOREA": (
        "krx_open_api_stock_daily",
        "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    ),
    "CRYPTO": (
        "kraken_public_api",
        "https://api.kraken.com/0/public/OHLC?pair=XBTUSD",
    ),
}


def source(market: str, marker: str) -> dict:
    source_id, url = MARKET_META[market]
    return {
        "source_id": source_id,
        "source_url": url,
        "source_sha256": marker * 64,
        "available_at": "2026-08-20",
        "retrieved_at_utc": "2026-08-20T00:05:00Z",
    }


def rows(closes=("100", "110", "120"), volumes=("10", "20", "60")) -> list:
    days = ("2026-08-17", "2026-08-18", "2026-08-19")
    return [
        {"session_date": day, "close": close, "volume": volume}
        for day, close, volume in zip(days, closes, volumes)
    ]


def window(
    market="US",
    window_id="US.W1",
    benchmark="US:XNAS:QQQ",
    asset="US:XNAS:NVDA",
    asset_rows=None,
    benchmark_rows=None,
) -> dict:
    return {
        "window_id": window_id,
        "market": market,
        "benchmark_asset_id": benchmark,
        "price_basis": "unadjusted official session close",
        "expected_sessions": ["2026-08-17", "2026-08-18", "2026-08-19"],
        "series": [
            {
                "asset_id": benchmark,
                "price_basis": "unadjusted official session close",
                "source_identity": source(market, "a"),
                "rows": benchmark_rows or rows(("100", "101", "102"), ("100", "100", "100")),
            },
            {
                "asset_id": asset,
                "price_basis": "unadjusted official session close",
                "source_identity": source(market, "b"),
                "rows": asset_rows or rows(),
            },
        ],
    }


def payload(windows=None) -> dict:
    return {
        "schema_version": "market_behavior_radar_input/1",
        "as_of_utc": "2026-08-20T01:00:00Z",
        "market_windows": windows if windows is not None else [window()],
    }


def policy(*, status="RATIFIED", feature="LATEST_VS_PRIOR_MEAN", rules=None) -> dict:
    if rules is None:
        rules = [
            {
                "market": "US",
                "window_id": "US.W1",
                "relative_strength_min": "0.10",
                "volume_ratio_feature": feature,
                "volume_ratio_min": "2",
            }
        ]
    return {
        "schema_version": "market_behavior_candidate_policy/1",
        "policy_id": "POLICY.MB.1",
        "approval_status": status,
        "effective_from": "2026-08-01",
        "effective_to": None,
        "ratified_by": "CIO" if status == "RATIFIED" else None,
        "ratified_at_utc": "2026-08-19T00:00:00Z" if status == "RATIFIED" else None,
        "rules": rules,
    }


def rehash(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = MB.payload_sha256(value)
    return value


class MarketBehaviorTests(unittest.TestCase):
    def test_raw_features_without_policy_create_no_case(self):
        packet = MB.build_packet(payload())
        self.assertEqual(packet["status"], "MARKET_BEHAVIOR_FEATURES_OBSERVED")
        self.assertEqual(packet["case_count"], 0)
        self.assertIsNone(packet["candidate_policy"])
        result = packet["market_windows"][0]
        feature = next(item for item in result["features"] if not item["is_benchmark"])
        self.assertEqual(feature["relative_strength_vs_benchmark"], "0.176470588235")
        self.assertEqual(feature["latest_volume_vs_prior_mean"], "4.000000000000")
        self.assertEqual(feature["latest_volume_vs_prior_median"], "4.000000000000")
        self.assertIsNone(feature["candidate_policy_match"])
        self.assertFalse(result["raw_rows_emitted"])
        self.assertFalse(result["reconstructive_price_volume_series_emitted"])

    def test_ratified_external_policy_creates_lineage_complete_case(self):
        packet = MB.build_packet(payload(), policy())
        self.assertEqual(packet["case_count"], 1)
        case = packet["cases"][0]
        self.assertTrue(case["case_id"].startswith("RADAR-MB-"))
        self.assertEqual(case["why_found"]["benchmark_asset_id"], "US:XNAS:QQQ")
        self.assertEqual(case["why_found"]["window"]["session_count"], 3)
        self.assertEqual(case["source_identity"]["asset"]["source_sha256"], "b" * 64)
        self.assertEqual(case["source_identity"]["benchmark"]["source_sha256"], "a" * 64)
        self.assertEqual(case["candidate_policy"]["policy_id"], "POLICY.MB.1")
        self.assertEqual(case["importance"], "UNRATIFIED")
        self.assertFalse(case["investable_eligible"])
        self.assertIsNone(case["candidate_rank"])
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["action"])

    def test_standalone_validator_accepts_raw_and_policy_packets(self):
        for packet in (MB.build_packet(payload()), MB.build_packet(payload(), policy())):
            with self.subTest(policy=packet["candidate_policy"] is not None):
                checked = MB.validate_packet(copy.deepcopy(packet))
                self.assertEqual(MB.canonical_json(checked), MB.canonical_json(packet))

    def test_standalone_validator_rejects_rehashed_benchmark_feature_tamper(self):
        packet = MB.build_packet(payload())
        benchmark = next(
            feature
            for feature in packet["market_windows"][0]["features"]
            if feature["is_benchmark"]
        )
        benchmark["relative_strength_vs_benchmark"] = "0.100000000000"
        with self.assertRaisesRegex(
            MB.MarketBehaviorError, "OUTPUT_BENCHMARK_RELATIVE_STRENGTH_MISMATCH"
        ):
            MB.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_policy_case_set_tamper(self):
        packet = MB.build_packet(payload(), policy())
        feature = next(
            feature
            for feature in packet["market_windows"][0]["features"]
            if feature["radar_case_created"]
        )
        feature["candidate_policy_match"] = False
        feature["radar_case_created"] = False
        with self.assertRaisesRegex(MB.MarketBehaviorError, "OUTPUT_CASE_IDENTITY_MISMATCH"):
            MB.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_source_lineage_tamper(self):
        packet = MB.build_packet(payload())
        packet["market_windows"][0]["features"][0]["source_identity"][
            "source_url"
        ] = "https://example.com/not-provider"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "SOURCE_URL_INVALID"):
            MB.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_authority_expansion(self):
        packet = MB.build_packet(payload(), policy())
        packet["cases"][0]["investable_eligible"] = True
        with self.assertRaisesRegex(MB.MarketBehaviorError, "OUTPUT_CASE_AUTHORITY_EXPANSION"):
            MB.validate_packet(rehash(packet))

    def test_unratified_policy_never_creates_case_or_accepts_fake_proof(self):
        packet = MB.build_packet(payload(), policy(status="UNRATIFIED"))
        self.assertEqual(packet["case_count"], 0)
        self.assertEqual(
            packet["market_windows"][0]["candidate_policy_status"],
            "ABSENT_OR_UNRATIFIED",
        )
        fake = policy(status="UNRATIFIED")
        fake["ratified_by"] = "CIO"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "PROOF_FORBIDDEN"):
            MB.build_packet(payload(), fake)

    def test_mean_and_median_are_separate_explicit_policy_methods(self):
        value = window(
            asset_rows=rows(("100", "110", "120"), ("1", "100", "10"))
        )
        value["expected_sessions"] = [
            "2026-08-16",
            "2026-08-17",
            "2026-08-18",
            "2026-08-19",
        ]
        value["series"][0]["rows"] = [
            {"session_date": d, "close": c, "volume": "100"}
            for d, c in zip(value["expected_sessions"], ("100", "100", "101", "102"))
        ]
        value["series"][1]["rows"] = [
            {"session_date": d, "close": c, "volume": v}
            for d, c, v in zip(
                value["expected_sessions"],
                ("100", "105", "110", "120"),
                ("1", "1", "100", "10"),
            )
        ]
        mean_policy = policy(feature="LATEST_VS_PRIOR_MEAN")
        mean_policy["rules"][0]["volume_ratio_min"] = "5"
        median_policy = policy(feature="LATEST_VS_PRIOR_MEDIAN")
        median_policy["rules"][0]["volume_ratio_min"] = "5"
        self.assertEqual(MB.build_packet(payload([value]), mean_policy)["case_count"], 0)
        self.assertEqual(MB.build_packet(payload([value]), median_policy)["case_count"], 1)

    def test_zero_prior_volume_is_unknown_not_zero_or_neutral(self):
        value = window(asset_rows=rows(volumes=("0", "0", "60")))
        packet = MB.build_packet(payload([value]), policy())
        feature = next(
            item for item in packet["market_windows"][0]["features"] if not item["is_benchmark"]
        )
        self.assertIsNone(feature["latest_volume_vs_prior_mean"])
        self.assertIsNone(feature["latest_volume_vs_prior_median"])
        self.assertEqual(feature["volume_baseline_status"], "ZERO_BASELINE_UNKNOWN")
        self.assertEqual(packet["case_count"], 0)

    def test_all_three_markets_require_registered_source_identity(self):
        windows = [
            window(),
            window("KOREA", "KOREA.W1", "KRX:KOSPI", "KRX:005930"),
            window("CRYPTO", "CRYPTO.W1", "CRYPTO:BTCUSD", "CRYPTO:ETHUSD"),
        ]
        packet = MB.build_packet(payload(windows))
        self.assertEqual(packet["window_count"], 3)
        self.assertEqual(
            [item["market"] for item in packet["market_windows"]],
            ["CRYPTO", "KOREA", "US"],
        )

    def test_series_window_and_policy_rule_order_are_deterministic(self):
        korea = window("KOREA", "KOREA.W1", "KRX:KOSPI", "KRX:005930")
        first_input = payload([window(), korea])
        first_policy = policy(
            rules=[
                policy()["rules"][0],
                {
                    "market": "KOREA",
                    "window_id": "KOREA.W1",
                    "relative_strength_min": "0",
                    "volume_ratio_feature": "LATEST_VS_PRIOR_MEAN",
                    "volume_ratio_min": "2",
                },
            ]
        )
        first = MB.build_packet(first_input, first_policy)
        first_input["market_windows"].reverse()
        for item in first_input["market_windows"]:
            item["series"].reverse()
        first_policy["rules"].reverse()
        second = MB.build_packet(first_input, first_policy)
        self.assertEqual(MB.canonical_json(first), MB.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, MB.payload_sha256(second))

    def test_empty_duplicate_missing_and_session_contracts_fail_closed(self):
        with self.assertRaisesRegex(MB.MarketBehaviorError, "MARKET_WINDOWS_EMPTY"):
            MB.build_packet(payload([]))
        duplicate = window()
        with self.assertRaisesRegex(MB.MarketBehaviorError, "WINDOW_DUPLICATE"):
            MB.build_packet(payload([duplicate, copy.deepcopy(duplicate)]))
        missing = window()
        missing["series"] = missing["series"][1:]
        with self.assertRaisesRegex(MB.MarketBehaviorError, "BENCHMARK_MISSING"):
            MB.build_packet(payload([missing]))
        gap = window()
        gap["series"][1]["rows"][1]["session_date"] = "2026-08-16"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "SESSION_COVERAGE_MISMATCH"):
            MB.build_packet(payload([gap]))

    def test_numeric_price_basis_and_row_shape_fail_closed(self):
        floating = window()
        floating["series"][1]["rows"][0]["close"] = 100.0
        with self.assertRaisesRegex(MB.MarketBehaviorError, "DECIMAL_NOT_STRING"):
            MB.build_packet(payload([floating]))
        nan = window()
        nan["series"][1]["rows"][0]["close"] = "NaN"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "DECIMAL_INVALID"):
            MB.build_packet(payload([nan]))
        negative = window()
        negative["series"][1]["rows"][0]["volume"] = "-1"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "DECIMAL_INVALID"):
            MB.build_packet(payload([negative]))
        mismatch = window()
        mismatch["series"][1]["price_basis"] = "adjusted close"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "PRICE_BASIS_MISMATCH"):
            MB.build_packet(payload([mismatch]))
        extra = window()
        extra["series"][1]["rows"][0]["open"] = "90"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "ROW_FIELDS_MISMATCH"):
            MB.build_packet(payload([extra]))

    def test_source_identity_host_hash_and_time_fail_closed(self):
        wrong_id = window()
        wrong_id["series"][1]["source_identity"]["source_id"] = "kraken_public_api"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "SOURCE_ID_MISMATCH"):
            MB.build_packet(payload([wrong_id]))
        wrong_host = window()
        wrong_host["series"][1]["source_identity"]["source_url"] = "https://example.com/x"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "SOURCE_URL_INVALID"):
            MB.build_packet(payload([wrong_host]))
        bad_sha = window()
        bad_sha["series"][1]["source_identity"]["source_sha256"] = "bad"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "SOURCE_SHA256_INVALID"):
            MB.build_packet(payload([bad_sha]))
        future = window()
        future["series"][1]["source_identity"]["retrieved_at_utc"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            MB.build_packet(payload([future]))

    def test_policy_structure_scope_and_point_in_time_fail_closed(self):
        empty = policy(rules=[])
        with self.assertRaisesRegex(MB.MarketBehaviorError, "RULES_EMPTY"):
            MB.build_packet(payload(), empty)
        duplicate = policy()
        duplicate["rules"].append(copy.deepcopy(duplicate["rules"][0]))
        with self.assertRaisesRegex(MB.MarketBehaviorError, "RULE_DUPLICATE"):
            MB.build_packet(payload(), duplicate)
        negative = policy()
        negative["rules"][0]["volume_ratio_min"] = "-1"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "DECIMAL_INVALID"):
            MB.build_packet(payload(), negative)
        future = policy()
        future["ratified_at_utc"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "RATIFIED_AFTER_AS_OF"):
            MB.build_packet(payload(), future)
        no_match = policy()
        no_match["rules"][0]["window_id"] = "US.OTHER"
        packet = MB.build_packet(payload(), no_match)
        self.assertEqual(packet["case_count"], 0)
        self.assertEqual(
            packet["market_windows"][0]["candidate_policy_status"],
            "NO_EFFECTIVE_MATCHING_RULE",
        )

    def test_contract_keeps_default_policy_ranking_stage_and_trading_closed(self):
        packet = MB.build_packet(payload())
        self.assertEqual(packet["policy_status"]["default_candidate_policy"], "ABSENT")
        self.assertEqual(packet["policy_status"]["anomaly_threshold"], "UNRATIFIED")
        authority = packet["authority"]
        self.assertTrue(authority["raw_feature_observation_only_without_ratified_policy"])
        self.assertTrue(authority["radar_case_recording_only_with_ratified_policy"])
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

    def test_contract_and_input_tampering_are_rejected(self):
        contract = MB.load_contract()
        contract["authority"]["stage_promotion_authorized"] = True
        with self.assertRaisesRegex(MB.MarketBehaviorError, "CONTRACT_FIELD_MISMATCH"):
            MB.build_packet(payload(), contract=contract)
        extra = payload()
        extra["candidate_threshold"] = "1"
        with self.assertRaisesRegex(MB.MarketBehaviorError, "INPUT_FIELDS_MISMATCH"):
            MB.build_packet(extra)
        source_extra = window()
        source_extra["series"][0]["source_identity"]["rank"] = 1
        with self.assertRaisesRegex(MB.MarketBehaviorError, "SOURCE_IDENTITY_FIELDS_MISMATCH"):
            MB.build_packet(payload([source_extra]))

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
                [
                    sys.executable,
                    str(MODULE_PATH),
                    str(input_path),
                    "--policy",
                    str(policy_path),
                    "--out",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["case_count"], 1)
            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            input_path.write_text(json.dumps(payload([])), encoding="utf-8")
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

    def test_module_has_no_network_tracked_output_or_default_policy(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", text)
        self.assertNotIn("urlopen", text)
        self.assertNotIn("data/", text)
        self.assertFalse((ROOT / "config" / "market_behavior_candidate_policy.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
