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
CRYPTO_UNIVERSE = ROOT / "data/observations/upbit_tradeable_universe/2026-09-12/packet.json"
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
        "crypto_universe_path": CRYPTO_UNIVERSE,
        "crypto_decision_path": CRYPTO_DECISION,
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
        self.assertEqual(self.by_market["CRYPTO"]["paper_ready_count"], 0)
        self.assertEqual(
            self.by_market["CRYPTO"]["coverage_status"],
            "SOURCE_POPULATION_EVALUATION_DISPOSITION_ACCOUNTED",
        )
        self.assertEqual(self.by_market["CRYPTO"]["missing_reasons"], [])

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
            "CRYPTO_DECISION_STATE_COUNTS_INVALID",
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
            "CRYPTO_DECISION_COUNTS_INVALID",
        ):
            build(crypto_decision_path=self._write(value))


if __name__ == "__main__":
    unittest.main()
