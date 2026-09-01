from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from paper_gate_2_3_three_market_aggregate import aggregate as module


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
FIXTURE = ROOT / "fixtures" / "current_blocked" / "input_bundle.json"


def source() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def by_market(rows: list[dict]) -> dict[str, dict]:
    return {row["market"]: row for row in rows}


class Gate23AggregateTests(unittest.TestCase):
    def test_exact_owner_and_common_receipts_are_pinned(self):
        module.validate_exact_pins()
        dependencies = module.EXACT_PINS["dependencies"]
        self.assertEqual(
            dependencies["PAPER_12_4_THREE_MARKET_REGIME"]["source_commit"],
            "25ee4b1ec0634ffb9b7f5f33cac4fd6e676371c1",
        )
        self.assertEqual(
            dependencies["PAPER_12_5_KRX_MARKET_JUDGEMENT"]["source_commit"],
            "bd9db7bd9cd631b7c56536a0077bacf5b475d574",
        )
        self.assertEqual(
            dependencies["PAPER_12_6_US_MARKET_JUDGEMENT"]["source_commit"],
            "f4e1d955d20442326d4f42bf0be2bbbe9e263c5d",
        )
        self.assertEqual(
            dependencies["PAPER_12_11_CRYPTO_MARKET_JUDGEMENT"]["source_commit"],
            "2b09c615a804ea68f058cc56de5aefdd34aa7aa6",
        )
        self.assertEqual(
            source()["exact_pins_sha256"],
            module.EXACT_PINS_SHA256,
        )
        self.assertEqual(
            dependencies["CRYPTO_SPOT_ADAPTER_PRIVATE_RELEASE"]["source_commit"],
            "742053c6aa0d88cd109d51d476376bd18f1331a8",
        )
        self.assertEqual(
            dependencies["COMMON_PAPER_CANDIDATE_FUNNEL_PUBLIC"]["source_commit"],
            "7e6021fcb866027b3b6caa28405dd0d9b3e90875",
        )

    def test_current_facts_remain_wait_unknown_hold(self):
        aggregate = module.build_aggregate(source())
        regimes = by_market(aggregate["market_regime_receipts"])
        rotations = by_market(aggregate["market_rotation_receipts"])
        for market in module.MARKETS:
            self.assertEqual(regimes[market]["receipt_status"], "WAIT")
            self.assertEqual(regimes[market]["regime"], "UNKNOWN")
            self.assertEqual(regimes[market]["disposition"], "HOLD")
            self.assertEqual(rotations[market]["rotation_readiness"], "BLOCKED")
            self.assertEqual(rotations[market]["disposition"], "HOLD")
            self.assertIsNone(rotations[market]["signed_direction"])
            self.assertIsNone(rotations[market]["rotation_weights"])
        self.assertEqual(regimes["KRX"]["coverage"], {"defined_count": 5, "required_count": 5})
        self.assertEqual(regimes["KRX"]["validation"]["coverage"], "PASS")
        self.assertEqual(regimes["KRX"]["policy_status"]["scoring"], "UNRATIFIED")
        self.assertEqual(regimes["US"]["coverage"], {"defined_count": 0, "required_count": 5})
        self.assertEqual(regimes["CRYPTO"]["policy_status"]["leadership"], "RATIFIED")
        self.assertEqual(regimes["CRYPTO"]["policy_status"]["coverage"], "UNRATIFIED")
        self.assertEqual(rotations["KRX"]["receipt_status"], "PENDING")
        self.assertEqual(rotations["US"]["receipt_status"], "DEGRADED")
        self.assertEqual(rotations["CRYPTO"]["receipt_status"], "DEGRADED")
        self.assertEqual(aggregate["headers"]["regime"]["status"], "PENDING")
        self.assertEqual(aggregate["headers"]["rotation"]["status"], "DEGRADED")

    def test_crypto_release_connects_eight_natural_candidates_but_zero_investment_paper(self):
        aggregate = module.build_aggregate(source())
        regimes = by_market(aggregate["market_regime_receipts"])
        crypto = regimes["CRYPTO"]
        connection = crypto["candidate_connection"]
        self.assertEqual(connection["natural_candidate_count"], 8)
        self.assertEqual(connection["investment_paper_count"], 0)
        self.assertEqual(connection["investment_paper_status"], "BLOCKED")
        self.assertEqual(crypto["receipt_status"], "WAIT")
        self.assertEqual(crypto["regime"], "UNKNOWN")
        self.assertEqual(crypto["disposition"], "HOLD")
        self.assertEqual(
            crypto["source_lineage"]["crypto_adapter_private_merge"],
            "742053c6aa0d88cd109d51d476376bd18f1331a8",
        )
        self.assertEqual(
            crypto["source_lineage"]["common_funnel_public_commit"],
            "7e6021fcb866027b3b6caa28405dd0d9b3e90875",
        )
        for gap in connection["evidence_gaps"]:
            self.assertIn(gap, crypto["blockers"])
        self.assertEqual(regimes["KRX"]["candidate_connection"], None)
        self.assertEqual(regimes["US"]["candidate_connection"], None)

    def test_signed_direction_freshness_pit_hysteresis_gaps_are_explicit(self):
        aggregate = module.build_aggregate(source())
        regimes = by_market(aggregate["market_regime_receipts"])
        self.assertEqual(regimes["KRX"]["validation"]["completed_bar"], "PASS")
        self.assertEqual(regimes["KRX"]["validation"]["source_time"], "PASS")
        self.assertEqual(regimes["KRX"]["validation"]["ttl_freshness"], "UNKNOWN")
        self.assertEqual(regimes["KRX"]["validation"]["pit"], "PASS")
        for market in module.MARKETS:
            receipt = regimes[market]
            self.assertEqual(receipt["validation"]["signed_direction"], "UNKNOWN")
            self.assertEqual(receipt["validation"]["hysteresis"], "UNKNOWN")
            self.assertIsNone(receipt["signed_direction"])
            self.assertIsNone(receipt["hysteresis_state"])
            self.assertIn("SIGNED_DIRECTION_POLICY_UNRATIFIED", receipt["blockers"])
            self.assertIn("HYSTERESIS_POLICY_UNRATIFIED", receipt["blockers"])
        self.assertEqual(regimes["US"]["validation"]["pit"], "UNKNOWN")
        self.assertEqual(regimes["CRYPTO"]["validation"]["pit"], "UNKNOWN")

    def test_missing_market_is_isolated(self):
        complete = module.build_aggregate(source())
        missing_source = source()
        missing_source["markets"] = [row for row in missing_source["markets"] if row["market"] != "US"]
        missing = module.build_aggregate(missing_source)
        complete_regimes = by_market(complete["market_regime_receipts"])
        missing_regimes = by_market(missing["market_regime_receipts"])
        complete_rotations = by_market(complete["market_rotation_receipts"])
        missing_rotations = by_market(missing["market_rotation_receipts"])
        self.assertEqual(missing_regimes["US"]["source_status"], "MISSING")
        self.assertEqual(missing_regimes["US"]["receipt_status"], "WAIT")
        for market in ("KRX", "CRYPTO"):
            self.assertEqual(
                complete_regimes[market]["receipt_sha256"],
                missing_regimes[market]["receipt_sha256"],
            )
            self.assertEqual(
                complete_rotations[market]["receipt_sha256"],
                missing_rotations[market]["receipt_sha256"],
            )

    def test_bad_exact_pin_rejects_only_its_market(self):
        changed = source()
        by_market(changed["markets"])["CRYPTO"]["receipt_sha256"] = "0" * 64
        aggregate = module.build_aggregate(changed)
        regimes = by_market(aggregate["market_regime_receipts"])
        self.assertEqual(regimes["CRYPTO"]["source_status"], "REJECTED")
        self.assertIn("OWNER_RECEIPT_SHA256_MISMATCH", regimes["CRYPTO"]["blockers"])
        self.assertEqual(regimes["KRX"]["source_status"], "VALIDATED_FAIL_CLOSED")
        self.assertEqual(regimes["US"]["source_status"], "VALIDATED_FAIL_CLOSED")

    def test_unratified_policy_and_coverage_cannot_be_promoted_by_input(self):
        changed = source()
        us = by_market(changed["markets"])["US"]
        us["facts"]["leadership_policy_status"] = "RATIFIED"
        us["facts"]["coverage_policy_status"] = "RATIFIED"
        us["facts"]["coverage"] = {"defined_count": 5, "required_count": 5}
        aggregate = module.build_aggregate(changed)
        regime = by_market(aggregate["market_regime_receipts"])["US"]
        self.assertEqual(regime["source_status"], "REJECTED")
        self.assertEqual(regime["receipt_status"], "WAIT")
        self.assertEqual(regime["regime"], "UNKNOWN")
        self.assertIn("OWNER_RECEIPT_FACTS_MISMATCH", regime["blockers"])

    def test_future_source_time_fails_closed_per_market(self):
        changed = source()
        krx = by_market(changed["markets"])["KRX"]
        krx["facts"]["source_time_utc"] = "2026-09-02T00:00:00Z"
        aggregate = module.build_aggregate(changed)
        regimes = by_market(aggregate["market_regime_receipts"])
        self.assertEqual(regimes["KRX"]["source_status"], "REJECTED")
        self.assertEqual(regimes["KRX"]["receipt_status"], "WAIT")
        self.assertEqual(regimes["US"]["source_status"], "VALIDATED_FAIL_CLOSED")

    def test_transition_ledger_is_exact_hash_chained_and_replay_aware(self):
        first = module.build_aggregate(source())
        self.assertEqual(first["transition_ledger"]["entry_count"], 8)
        self.assertTrue(all(row["transition"] == "INITIAL" for row in first["transition_ledger"]["entries"]))
        second = module.build_aggregate(source(), first)
        self.assertTrue(all(row["transition"] == "NO_CHANGE" for row in second["transition_ledger"]["entries"]))
        previous = None
        for row in second["transition_ledger"]["entries"]:
            self.assertEqual(row["previous_entry_sha256"], previous)
            previous = row["entry_sha256"]
        self.assertEqual(second["transition_ledger"]["tail_sha256"], previous)

    def test_rehashed_tamper_cannot_create_authority(self):
        aggregate = module.build_aggregate(source())
        aggregate["market_regime_receipts"][0]["authority"]["paper_authorized"] = True
        aggregate["market_regime_receipts"][0]["receipt_sha256"] = module.canonical_sha256(
            {key: value for key, value in aggregate["market_regime_receipts"][0].items() if key != "receipt_sha256"}
        )
        aggregate["aggregate_sha256"] = module.canonical_sha256(
            {key: value for key, value in aggregate.items() if key != "aggregate_sha256"}
        )
        with self.assertRaisesRegex(module.AggregateError, "REGIME_FAIL_CLOSED_INVALID"):
            module.validate_aggregate(aggregate, source())

    def test_hash_tamper_and_rebuild_mismatch_are_rejected(self):
        aggregate = module.build_aggregate(source())
        changed = copy.deepcopy(aggregate)
        changed["summary"]["rotation_discovery"] = "READY"
        with self.assertRaises(module.AggregateError):
            module.validate_aggregate(changed, source())

    def test_all_operational_effects_and_authorities_are_zero_or_false(self):
        aggregate = module.build_aggregate(source())
        self.assertTrue(all(value == 0 for value in aggregate["effects"].values()))
        self.assertTrue(all(value is False for value in aggregate["authority"].values()))
        self.assertEqual(aggregate["summary"]["candidate_state"], "NONE")
        self.assertEqual(aggregate["summary"]["strategy_engine_count"], 0)
        self.assertEqual(aggregate["summary"]["live_engine_count"], 0)

    def test_offline_cli_build_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "aggregate.json"
            build = subprocess.run(
                [
                    sys.executable, "-m", "paper_gate_2_3_three_market_aggregate.cli", "build",
                    "--input", str(FIXTURE), "--output", str(output),
                ],
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            verify = subprocess.run(
                [
                    sys.executable, "-m", "paper_gate_2_3_three_market_aggregate.cli", "verify",
                    "--input", str(FIXTURE), "--aggregate", str(output),
                ],
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.stdout.strip(), verify.stdout.strip())

    def test_report_rederives_exact_receipts_and_report_sha(self):
        aggregate = module.build_aggregate(source())
        report_path = ROOT / "REPORT.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        result = report["result"]
        self.assertEqual(result["aggregate_sha256"], aggregate["aggregate_sha256"])
        self.assertEqual(
            result["market_regime_receipts"],
            {row["market"]: row["receipt_sha256"] for row in aggregate["market_regime_receipts"]},
        )
        self.assertEqual(
            result["market_rotation_receipts"],
            {row["market"]: row["receipt_sha256"] for row in aggregate["market_rotation_receipts"]},
        )
        expected = (ROOT / "REPORT.sha256").read_text(encoding="utf-8").split()[0]
        self.assertEqual(hashlib.sha256(report_path.read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
