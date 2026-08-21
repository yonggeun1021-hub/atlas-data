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
ENTRY_EXIT_FIXTURE = load_module(
    "intraday_risk_entry_exit_fixture",
    ROOT / "test" / "test_entry_exit_trigger_eligibility.py",
)
IMPORTANT_EVENT_FIXTURE = load_module(
    "intraday_risk_important_event_fixture",
    ROOT / "test" / "test_important_event_detector.py",
)
CONTRACT = MODULE.load_contract()


def entry_exit_packet(generated_at="2026-08-21T02:12:00Z"):
    return ENTRY_EXIT_FIXTURE.MODULE.build_packet(
        ENTRY_EXIT_FIXTURE.unified(),
        ENTRY_EXIT_FIXTURE.freshness(),
        generated_at,
        ENTRY_EXIT_FIXTURE.CONTRACT,
    )


def important_event_packet(detected_at="2026-08-21T03:05:00Z"):
    return IMPORTANT_EVENT_FIXTURE.MODULE.build_packet(
        IMPORTANT_EVENT_FIXTURE.batch(),
        IMPORTANT_EVENT_FIXTURE.policy(),
        detected_at,
        IMPORTANT_EVENT_FIXTURE.CONTRACT,
    )


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
        "schema_version": CONTRACT["policy_schema_version"],
        "contract_version": CONTRACT["contract_version"],
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
        "provider_timestamp": "2026-08-21T03:09:30Z",
        "received_at": "2026-08-21T03:09:35Z",
        "source_ref": f"test://intraday-risk/{subject_id}",
        "source_sha256": "d" * 64,
    }


def batch(rows=None, **changes):
    entry_exit = entry_exit_packet()
    important_event = important_event_packet()
    observations = [observation()] if rows is None else rows
    order = {market: index for index, market in enumerate(CONTRACT["markets"])}
    normalized_rows = sorted(
        copy.deepcopy(observations),
        key=lambda row: (order[row["market"]], row["subject_id"]),
    )
    value = {
        "schema_version": CONTRACT["input_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "batch_id": "INTRADAY.RISK.BATCH.20260821",
        "observed_at": "2026-08-21T03:10:00Z",
        "observations": observations,
        "upstream_lineage": {
            "entry_exit_trigger_eligibility_packet_sha256": entry_exit[
                "packet_sha256"
            ],
            "important_event_detection_packet_sha256": important_event[
                "packet_sha256"
            ],
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


def build(
    batch_value=None,
    policy_value=None,
    entry_exit_value=None,
    important_event_value=None,
):
    return MODULE.build_packet(
        batch() if batch_value is None else batch_value,
        policy() if policy_value is None else policy_value,
        entry_exit_packet() if entry_exit_value is None else entry_exit_value,
        important_event_packet()
        if important_event_value is None
        else important_event_value,
        CONTRACT,
    )


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
        self.assertEqual(
            CONTRACT["validated_upstream_packets"],
            ["ENTRY_EXIT_TRIGGER_ELIGIBILITY", "IMPORTANT_EVENT_DETECTION"],
        )
        self.assertEqual(
            CONTRACT["lineage_only_upstreams"],
            ["CONCENTRATION_GUARD", "PLANNED_LOSS_BUDGET"],
        )

    def test_all_four_metrics_alert_but_create_no_candidate_action_or_order(self):
        packet = build()
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
        row = build(batch([boundary]))["results"][0]
        self.assertEqual(row["risk_status"], "NORMAL")
        self.assertEqual(row["alert_reasons"], [])
        self.assertEqual([metric["result"] for metric in row["metrics"]], ["PASS"] * 4)

    def test_market_specific_thresholds_and_permutation_are_deterministic(self):
        korea = observation("KR.XKRX.005930", "KOREA")
        crypto = observation("CRYPTO.KRAKEN.BTC", "CRYPTO")
        rows = [crypto, observation(), korea]
        first = build(batch(rows))
        second = build(batch(list(reversed(rows))))
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
                build(policy_value=value)

    def test_input_time_price_lineage_authority_and_digest_fail_closed(self):
        crossed = observation(bid="101", ask="100")
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "CROSSED_QUOTE_INVALID"
        ):
            build(batch([crossed]))

        future = observation()
        future["received_at"] = "2026-08-21T03:11:00Z"
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "OBSERVATION_TIME_ORDER_INVALID"
        ):
            build(batch([future]))

        lineage = batch()
        lineage["upstream_lineage"]["planned_loss_budget_packet_sha256"] = "bad"
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "UPSTREAM_SHA_INVALID"
        ):
            build(lineage)

        authority = batch()
        authority["authority"]["action_generation_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "BATCH_IDENTITY_INVALID"
        ):
            build(authority)

        digest = batch()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "BATCH_SHA_MISMATCH"
        ):
            build(digest)

    def test_output_derivation_and_authority_smuggling_fail_closed(self):
        original = build()
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

    def test_exact_upstream_packets_are_semantically_validated_and_time_bound(self):
        tampered_entry_exit = entry_exit_packet()
        tampered_entry_exit["authority"]["action_generation_authorized"] = True
        tampered_entry_exit["packet_sha256"] = ENTRY_EXIT_FIXTURE.MODULE.payload_sha256(
            {
                key: value
                for key, value in tampered_entry_exit.items()
                if key != "packet_sha256"
            }
        )
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError, "ENTRY_EXIT_PACKET_INVALID"
        ):
            build(entry_exit_value=tampered_entry_exit)

        alternate_event = IMPORTANT_EVENT_FIXTURE.MODULE.build_packet(
            IMPORTANT_EVENT_FIXTURE.batch(
                [IMPORTANT_EVENT_FIXTURE.event("SEC.20260821.0099")]
            ),
            IMPORTANT_EVENT_FIXTURE.policy(),
            "2026-08-21T03:05:00Z",
            IMPORTANT_EVENT_FIXTURE.CONTRACT,
        )
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError,
            "IMPORTANT_EVENT_PACKET_SHA_MISMATCH",
        ):
            build(important_event_value=alternate_event)

        future_event = important_event_packet("2026-08-21T03:11:00Z")
        future_batch = batch()
        future_batch["upstream_lineage"][
            "important_event_detection_packet_sha256"
        ] = future_event["packet_sha256"]
        future_batch.pop("packet_sha256")
        future_batch["packet_sha256"] = MODULE.payload_sha256(future_batch)
        with self.assertRaisesRegex(
            MODULE.IntradayRiskEscalationError,
            "IMPORTANT_EVENT_PACKET_FROM_FUTURE",
        ):
            build(future_batch, important_event_value=future_event)

    def test_only_p7_guard_packets_remain_lineage_only(self):
        packet = build()
        self.assertEqual(
            packet["unresolved_boundaries"][0],
            "P7_GUARD_PACKETS_ARE_LINEAGE_ONLY_NOT_SEMANTIC_AUTHORITY",
        )
        self.assertEqual(
            packet["source_batch"]["upstream_lineage"][
                "concentration_guard_packet_sha256"
            ],
            "3" * 64,
        )
        self.assertEqual(
            packet["source_batch"]["upstream_lineage"][
                "planned_loss_budget_packet_sha256"
            ],
            "4" * 64,
        )

    def test_input_objects_are_immutable_and_lineage_is_exact(self):
        source_batch = batch()
        source_policy = policy()
        source_entry_exit = entry_exit_packet()
        source_important_event = important_event_packet()
        before = MODULE.canonical_json(
            [
                source_batch,
                source_policy,
                source_entry_exit,
                source_important_event,
            ]
        )
        packet = MODULE.build_packet(
            source_batch,
            source_policy,
            source_entry_exit,
            source_important_event,
            CONTRACT,
        )
        self.assertEqual(
            MODULE.canonical_json(
                [
                    source_batch,
                    source_policy,
                    source_entry_exit,
                    source_important_event,
                ]
            ),
            before,
        )
        self.assertEqual(
            packet["lineage"]["observation_batch_sha256"],
            source_batch["packet_sha256"],
        )
        self.assertEqual(packet["lineage"]["policy_sha256"], source_policy["packet_sha256"])
        self.assertEqual(packet["policy_packet"], source_policy)
        self.assertEqual(
            packet["source_packets"]["ENTRY_EXIT_TRIGGER_ELIGIBILITY"],
            source_entry_exit,
        )
        self.assertEqual(
            packet["source_packets"]["IMPORTANT_EVENT_DETECTION"],
            source_important_event,
        )

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
            entry_exit_path = write_json(
                temp / "entry-exit.json", entry_exit_packet()
            )
            important_event_path = write_json(
                temp / "important-event.json", important_event_packet()
            )
            output = temp / "nested" / "risk.json"
            self.assertEqual(
                MODULE.run(
                    batch_path,
                    policy_path,
                    entry_exit_path,
                    important_event_path,
                    output,
                ),
                0,
            )
            self.assertTrue(output.exists())
            serialized = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(MODULE.validate_packet(serialized, CONTRACT), serialized)
            forbidden = ROOT / "data" / "intraday_risk_escalation_test.json"
            self.assertEqual(
                MODULE.run(
                    batch_path,
                    policy_path,
                    entry_exit_path,
                    important_event_path,
                    forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
