#!/usr/bin/env python3
"""P1-KR-07 transient Korea Leadership regression (synthetic only)."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_leadership.py"
SPEC = importlib.util.spec_from_file_location("korea_leadership", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def record(identity, role, benchmark, start="2026-01-01", end=None):
    return {"series_identity": identity, "role": role, "benchmark_identity": benchmark, "effective_from": start, "effective_to": end, "reason": "synthetic PIT taxonomy"}


def write_policy(path: Path, records=None, **updates):
    policy = {
        "schema_version": 1,
        "policy_version": "korea_leadership/synthetic-test-v1",
        "approval_status": "RATIFIED",
        "effective_from": "2026-01-01",
        "source_name": "KRX_OPEN_API_INDEX_FIXTURE",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "allowed_run_modes": ["FORWARD_SHADOW", "HISTORICAL_REPLAY"],
        "session_calendar_source": "synthetic_xkrx_fixture/v1",
        "publication_timing_source": "synthetic_fixture_only/v1",
        "earliest_usable_time": "18:00:00",
        "lookback_sessions": 1,
        "records": records or [
            record("01::KOSPI", "KOSPI_BENCHMARK", "01::KOSPI"),
            record("02::KOSDAQ", "KOSDAQ_BENCHMARK", "02::KOSDAQ"),
            record("11::KOSPI_전기전자", "SECTOR", "01::KOSPI"),
            record("21::KOSDAQ_반도체", "THEME", "02::KOSDAQ"),
        ],
    }
    policy.update(updates)
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def payload(mode="FORWARD_SHADOW", prices=None):
    prices = prices or {"01::KOSPI": ["100", "105"], "02::KOSDAQ": ["100", "90"], "11::KOSPI_전기전자": ["100", "110"], "21::KOSDAQ_반도체": ["100", "99"]}
    dates = ["2026-08-14", "2026-08-18"]
    return {
        "schema_version": 1, "source_name": "KRX_OPEN_API_INDEX_FIXTURE", "market": "KOREA", "market_timezone": "Asia/Seoul", "run_mode": mode,
        "observation_date": dates[-1], "fetched_at": "2026-08-18T18:05:00+09:00" if mode == "FORWARD_SHADOW" else "2026-08-19T09:00:00+09:00", "available_at": "2026-08-18T18:00:00+09:00", "decision_at": "2026-08-18T18:10:00+09:00" if mode == "FORWARD_SHADOW" else None,
        "expected_session_dates": dates,
        "series_rows": [{"series_identity": identity, "rows": [{"session_date": day, "close": close} for day, close in zip(dates, prices[identity])]} for identity in sorted(prices)],
    }


class KoreaLeadershipTest(unittest.TestCase):
    def build(self, data=None, records=None, **updates):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", records, **updates)
            return MODULE.build_transform(data or payload(), policy)

    def test_default_policy_closes_calculation_authority(self):
        self.assertEqual(MODULE.load_policy()["approval_status"], "UNRATIFIED")
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "LEADERSHIP_POLICY_UNRATIFIED"):
            MODULE.build_transform(payload())

    def test_kospi_kosdaq_sector_theme_relative_strength_reproduces(self):
        result = self.build()
        rows = {row["series_identity"]: row for row in result["relative_strength_observations"]}
        self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
        self.assertEqual(rows["01::KOSPI"]["relative_strength_vs_benchmark"], "0")
        self.assertEqual(rows["02::KOSDAQ"]["relative_strength_vs_benchmark"], "0")
        expected = Decimal("1.10") / Decimal("1.05") - 1
        self.assertEqual(rows["11::KOSPI_전기전자"]["relative_strength_vs_benchmark"], MODULE.render_decimal(expected, 12))
        self.assertEqual(rows["21::KOSDAQ_반도체"]["relative_strength_vs_benchmark"], "0.1")
        for key in ("leader_classification_authorized", "ranking_authorized", "trend_direction_authorized", "breadth_direction_authorized", "regime_score_authorized", "production_wiring_authorized", "trading_action_authorized"):
            self.assertFalse(result[key])

    def test_replay_is_causal_only(self):
        result = self.build(payload("HISTORICAL_REPLAY"))
        self.assertEqual(result["status"], "CAUSAL_REPLAY_ONLY")
        self.assertFalse(result["temporal_eligibility"]["authoritative_historical_pit"])

    def test_missing_series_and_benchmark_fail_closed(self):
        missing = payload()
        missing["series_rows"].pop()
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "PIT_TAXONOMY_COVERAGE_MISMATCH"):
            self.build(missing)
        records = [record("01::KOSPI", "KOSPI_BENCHMARK", "01::KOSPI"), record("11::KOSPI_전기전자", "SECTOR", "99::MISSING")]
        two = payload(prices={"01::KOSPI": ["100", "105"], "11::KOSPI_전기전자": ["100", "110"]})
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "BENCHMARK_MISSING"):
            self.build(two, records=records)

    def test_identity_collision_gap_and_numeric_json_fail_closed(self):
        collision = payload()
        collision["series_rows"][1]["series_identity"] = collision["series_rows"][0]["series_identity"]
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "SERIES_IDENTITY_COLLISION"):
            self.build(collision)
        gap = payload()
        del gap["series_rows"][0]["rows"][0]
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "SESSION_COVERAGE_MISMATCH"):
            self.build(gap)
        numeric = payload()
        numeric["series_rows"][0]["rows"][0]["close"] = 100
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "NUMBER_MUST_BE_DECIMAL_STRING"):
            self.build(numeric)

    def test_overlapping_effective_taxonomy_fails_closed(self):
        records = [record("01::KOSPI", "KOSPI_BENCHMARK", "01::KOSPI"), record("01::KOSPI", "KOSPI_BENCHMARK", "01::KOSPI", start="2026-08-01")]
        one = payload(prices={"01::KOSPI": ["100", "105"]})
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "TAXONOMY_OVERLAP"):
            self.build(one, records=records)

    def test_timing_and_non_reconstructive_output(self):
        early = payload()
        early["available_at"] = "2026-08-18T17:59:59+09:00"
        with self.assertRaisesRegex(MODULE.KoreaLeadershipError, "TEMPORAL_INPUT_NOT_QUALIFIED"):
            self.build(early)
        result = self.build()
        self.assertNotIn("series_rows", result)
        self.assertNotIn("expected_session_dates", result)
        self.assertNotIn('"110"', json.dumps(result))

    def test_downstream_lineage_is_hash_bound_and_non_reconstructive(self):
        result = self.build()
        digest = result.pop("payload_sha256")
        self.assertEqual(digest, MODULE.canonical_payload_sha256(result))
        self.assertEqual(result["measurement"], "korea_index_relative_leadership_observation")
        self.assertEqual(result["window"]["lookback_sessions"], 1)
        self.assertTrue(result["window"]["exact_expected_sessions"])
        self.assertEqual(result["policy"]["approval_status"], "RATIFIED")
        self.assertEqual(len(result["policy"]["policy_sha256"]), 64)
        self.assertEqual(len(result["lineage"]["input_sha256"]), 64)
        self.assertFalse(result["lineage"]["current_membership_backfill_authorized"])
        self.assertFalse(result["retention"]["source_rows_emitted"])
        self.assertFalse(result["retention"]["source_closes_emitted"])


if __name__ == "__main__":
    unittest.main()
