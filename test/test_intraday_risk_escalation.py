#!/usr/bin/env python3
"""P9-05 intraday risk escalation regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "execution" / "intraday_risk_escalation.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("intraday_risk_escalation", SOURCE)
CONTRACT = MODULE.load_contract()


def thresholds(marker="a"):
    return {
        "max_drawdown_fraction": "0.05",
        "max_down_gap_fraction": "0.03",
        "max_spread_bps": "25",
        "min_relative_volume_fraction": "0.5",
        "policy_basis_ref": f"notion://risk-policy/{marker}",
        "policy_basis_sha256": marker * 64,
    }


def policy(rows=None, **changes):
    value = {
        "schema_version": "intraday_risk_escalation_policy/1",
        "contract_version": "intraday_risk_escalation/1",
        "policy_id": "INTRADAY.RISK.TEST.V1",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "effective_from": "2026-08-21T00:00:00Z",
        "effective_to": "2026-08-22T00:00:00Z",
        "thresholds_by_market": {
            "US": thresholds("a"),
            "KOREA": thresholds("b"),
            "CRYPTO": thresholds("c"),
        }
        if rows is None
        else rows,
        "authority": copy.deepcopy(CONTRACT["policy_authority"]),
    }
    value.update(changes)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def observation(
    subject_id="US.XNAS.TSM",
    market="US",
    reference="100",
    opened="96",
    last="94",
    bid="93",
    ask="95",
    cumulative="40",
    expected="100",
):
    return {
        "subject_id": subject_id,
        "market": market,
        "reference_close": reference,
        "open_price": opened,
        "last_price": last,
        "bid_price": bid,
        "ask_price": ask,
        "cumulative_volume": cumulative,
        "expected_volume_to_time": expected,
        "provider_timestamp": "2026-08-21T01:09:30Z",
        "received_at": "2026-08-21T01:09:35Z",
        "source_ref": f"test://intraday-risk/{subject_id}",
        "source_sha256": "d" * 64,
    }


def batch(rows=None, **changes):
    observations = [observation()] if rows is None else rows
    order = {market: index for index, market in enumerate(CONTRACT["markets"])}
    normalized_rows = sorted(
        copy.deepcopy(observations),
        key=lambda row: (order[row["market"]], row["subject_id"]),
    )
    value = {
        "schema_version": "intraday_risk_observation_batch/1",
        "contract_version": "intraday_risk_escalation/1",
        "batch_id": "INTRADAY.RISK.BATCH.20260821",
        "observed_at": "2026-08-21T01:10:00Z",
        "observations": observations,
        "upstream_lineage": {
            "entry_exit_trigger_eligibility_packet_sha256": "1" * 64,
            "important_event_detection_packet_sha256": "2" * 64,
            "concentration_guard_packet_sha256": "3" * 64,
            "planned_loss_budget_packet_sha256": "4" * 64,
        },
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value.update(changes)
    normalized = copy.deepcopy(value)
    normalized["observations"] = normalized_rows
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class IntradayRiskEscalationTests(unittest.TestCase):
    def test_contract_requires_external_policy_and_closes_execution_authority(self):
        self.assertEqual(CONTRACT["repository_default_policy"], "ABSENT")
        self.assertEqual(
            CONTRACT["policy_requirement"], "EXTERNAL_RATIFIED_POLICY_REQUIRED"
        )
        self.assertTrue(CONTRACT["authority"]["intraday_risk_evaluation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "intraday_risk_evaluation_only":
                self.assertFalse(value, key)

    def test_all_four_metrics_alert_but_create_no_candidate_action_or_order(self):
        packet = MODULE.build_packet(batch(), policy(), CONTRACT)
        row = packet["results"][0]
        self.assertEqual(row["risk_status"], "ALERT")
        self.assertEqual(row["alert_reasons"], CONTRACT["metrics"])
        self.assertEqual(
            [metric["result"] for metric in row["metrics"]], ["ALERT"] * 4
        )
        self.assertIsNone(row["exposure_reduction_candidate"])
        self.assertIsNone(row["stop_candidate"])
        self.assertIsNone(row["action"])
        self.assertIsNone(row["position_size"])
        self.assertIsNone(row["order_intent"])
        self.assertEqual(packet["summary"]["alert_count"], 1)
        self.assertEqual(packet["summary"]["exposure_reduction_candidate_count"], 0)
        self.assertEqual(packet["summary"]["stop_candidate_count"], 0)

    def test_equality_at_every_threshold_is_pass(self):
        boundary = observation(
            opened="97",
            last="95",
            bid="99.875",
            ask="100.125",
            cumulative="50",
            expected="100",
        )
        row = MODULE.build_packet(batch([boundary]), policy(), CONTRACT)["results"][0]
        self.assertEqual(row["risk_status"], "NORMAL")
        self.assertEqual(row["alert_reasons"], [])
        self.assertEqual([metric["result"] for metric in row["metrics"]], ["PASS"] * 4)

    def test_market_specific_thresholds_and_permutation_are_deterministic(self):
        korea = observation("KR.XKRX.005930", "KOREA")
        crypto = observation("CRYPTO.KRAKEN.BTC", "CRYPTO")
        rows = [crypto, observation(), korea]
        first = MODULE.build_packet(batch(rows), policy(), CONTRACT)
        second = MODULE.build_packet(batch(list(reversed(rows))), policy(), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(
            [row["market"] for row in first["results"]], ["US", "KOREA", "CRYPTO"]
        )

    def test_unratified_incomplete_or_ineffective_policy_fails_closed(self):
        incomplete = {
            "US": thresholds("a"),
            "KOREA": thresholds("b"),
        }
        cases = [
            (policy(status="DRAFT"), "POLICY_IDENTITY_INVALID"),
            (policy(incomplete), "POLICY_MARKET_COVERAGE_INVALID"),
            (
                policy(effective_to="2026-08-21T01:00:00Z"),
                "POLICY_NOT_EFFECTIVE",
            ),
        ]
        for value, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.IntradayRiskEscalationError, error
            ):
                MODULE.build_packet(batch(), value, CONTRACT)

    def test_input_time_price_lineage_authority_and_digest_fail_closed(self):
        crossed = observation(bid="101", ask="100")
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "CROSSED_QUOTE_INVALID"
        ):
            MODULE.build_packet(batch([crossed]), policy(), CONTRACT)

        future = observation()
        future["received_at"] = "2026-08-21T01:11:00Z"
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "OBSERVATION_TIME_ORDER_INVALID"
        ):
            MODULE.build_packet(batch([future]), policy(), CONTRACT)

        lineage = batch()
        lineage["upstream_lineage"]["planned_loss_budget_packet_sha256"] = "bad"
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "UPSTREAM_SHA_INVALID"
        ):
            MODULE.build_packet(lineage, policy(), CONTRACT)

        authority = batch()
        authority["authority"]["action_generation_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "BATCH_IDENTITY_INVALID"
        ):
            MODULE.build_packet(authority, policy(), CONTRACT)

        digest = batch()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "BATCH_SHA_MISMATCH"
        ):
            MODULE.build_packet(digest, policy(), CONTRACT)

    def test_output_derivation_and_authority_smuggling_fail_closed(self):
        original = MODULE.build_packet(batch(), policy(), CONTRACT)
        variants = []
        action = copy.deepcopy(original)
        action["results"][0]["action"] = {"type": "REDUCE"}
        variants.append(action)
        candidate = copy.deepcopy(original)
        candidate["results"][0]["stop_candidate"] = True
        variants.append(candidate)
        authority = copy.deepcopy(original)
        authority["authority"]["trading_authorized"] = True
        variants.append(authority)
        for packet in variants:
            packet["packet_sha256"] = MODULE.payload_sha256(
                {key: value for key, value in packet.items() if key != "packet_sha256"}
            )
            with self.assertRaisesRegex(
                MODULE.IntradayRiskEscalationError, "OUTPUT_DERIVATION_MISMATCH"
            ):
                MODULE.validate_packet(packet, CONTRACT)

    def test_input_objects_are_immutable_and_lineage_is_exact(self):
        source_batch = batch()
        source_policy = policy()
        before = MODULE.canonical_json([source_batch, source_policy])
        packet = MODULE.build_packet(source_batch, source_policy, CONTRACT)
        self.assertEqual(MODULE.canonical_json([source_batch, source_policy]), before)
        self.assertEqual(
            packet["lineage"]["observation_batch_sha256"],
            source_batch["packet_sha256"],
        )
        self.assertEqual(packet["lineage"]["policy_sha256"], source_policy["packet_sha256"])
        self.assertEqual(packet["policy_packet"], source_policy)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            batch_path = write_json(temp / "batch.json", batch())
            policy_path = write_json(temp / "policy.json", policy())
            output = temp / "nested" / "risk.json"
            self.assertEqual(MODULE.run(batch_path, policy_path, output), 0)
            self.assertTrue(output.exists())
            serialized = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(MODULE.validate_packet(serialized, CONTRACT), serialized)
            forbidden = ROOT / "data" / "intraday_risk_escalation_test.json"
            self.assertEqual(MODULE.run(batch_path, policy_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
