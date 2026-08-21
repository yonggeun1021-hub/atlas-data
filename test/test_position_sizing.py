#!/usr/bin/env python3
"""P7-02 Position sizing regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "position_sizing.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("position_sizing", SOURCE)
BUCKET = load_module("p702_bucket_fixture", ROOT / "test" / "test_bucket_membership.py")
CONTRACT = MODULE.load_contract()


def policy(**changes):
    value = {
        "schema_version": "position_sizing_policy/1",
        "contract_version": "position_sizing/1",
        "policy_id": "POSITION.SIZING.TEST.V1",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "effective_from": "2026-08-20",
        "effective_to": None,
        "formula_id": "MIN_RATIFIED_LIMITS_THEN_TARGET_UTILIZATION_V1",
        "max_planned_loss_per_position_nav_fraction": "0.01",
        "target_utilization_fraction": "0.5",
        "policy_basis_ref": "notion://position-sizing/test-v1",
        "policy_basis_sha256": "9" * 64,
        "authority": copy.deepcopy(CONTRACT["policy_authority"]),
    }
    value.update(changes)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def sizing_input(evidence_state="forward_established", stop="95", **state_changes):
    state = {
        "current_deployed_nav_fraction": "0.3",
        "cash_available_nav_fraction": "0.5",
        "bucket_current_exposure_nav_fraction": "0.1",
        "current_portfolio_planned_loss_nav_fraction": "0.02",
        "portfolio_snapshot_sha256": "1" * 64,
        "loss_state_sha256": "2" * 64,
        "concentration_guard_packet_sha256": "3" * 64,
        "market_theme_budget_packet_sha256": "4" * 64,
        "crypto_exposure_limit_packet_sha256": None,
    }
    state.update(state_changes)
    value = {
        "schema_version": "position_sizing_input/1",
        "contract_version": "position_sizing/1",
        "snapshot_id": "POSITION.SIZING.TEST.20260821",
        "as_of_date": "2026-08-21",
        "candidate": {
            "asset_id": "US:XNAS:TSM",
            "market": "US",
            "evidence_state": evidence_state,
            "entry_price": "100",
            "planned_stop_price": stop,
            "asset_identity_sha256": "b" * 64,
            "discovery_result_sha256": "c" * 64,
            "rule_result_sha256": "d" * 64,
        },
        "portfolio_state": state,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def sources():
    constitution = BUCKET.ratified_constitution()
    return BUCKET.assignment_set(constitution), constitution


def build(value=None, ratified_policy=None):
    assignment, constitution = sources()
    return MODULE.build_packet(
        assignment,
        constitution,
        sizing_input() if value is None else value,
        policy() if ratified_policy is None else ratified_policy,
        "2026-08-21",
        CONTRACT,
    )


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class PositionSizingTests(unittest.TestCase):
    def test_contract_has_no_default_policy_and_closes_execution_authority(self):
        self.assertEqual(CONTRACT["repository_default_policy"], "ABSENT")
        self.assertEqual(
            CONTRACT["formula_id"],
            "MIN_RATIFIED_LIMITS_THEN_TARGET_UTILIZATION_V1",
        )
        self.assertTrue(CONTRACT["authority"]["ratified_limit_calculation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "ratified_limit_calculation_only":
                self.assertFalse(value, key)

    def test_maximum_and_target_are_minimum_of_displayed_ratified_limits(self):
        packet = build()
        self.assertEqual(packet["status"], "MAXIMUM_AND_TARGET_SIZED_NO_ACTION_AUTHORITY")
        self.assertEqual(packet["stop_distance_fraction"], "0.05")
        limits = {
            row["limit"]: row["maximum_position_weight_nav_fraction"]
            for row in packet["limits"]
        }
        self.assertEqual(limits, {
            "DEPLOYMENT_HEADROOM": "0.2",
            "CASH_AVAILABLE": "0.5",
            "BUCKET_HEADROOM": "0.15",
            "POSITION_MAX": "0.1",
            "EVIDENCE_MAX": "0.02",
            "PORTFOLIO_LOSS_HEADROOM": "0.6",
            "PER_POSITION_LOSS": "0.2",
        })
        self.assertEqual(packet["binding_limits"], ["EVIDENCE_MAX"])
        self.assertEqual(packet["maximum_position_weight_nav_fraction"], "0.02")
        self.assertEqual(packet["target_position_weight_nav_fraction"], "0.01")
        self.assertEqual(packet["planned_loss_at_max_nav_fraction"], "0.001")
        self.assertEqual(packet["planned_loss_at_target_nav_fraction"], "0.0005")
        self.assertIsNone(packet["action"])
        self.assertIsNone(packet["entry_trigger"])
        self.assertIsNone(packet["order_intent"])

    def test_exhausted_limit_or_stop_beyond_constitution_blocks_sizing(self):
        exhausted = build(
            sizing_input(bucket_current_exposure_nav_fraction="0.25")
        )
        self.assertEqual(exhausted["status"], "SIZING_BLOCKED")
        self.assertIn("NO_BUCKET_HEADROOM", exhausted["blocking_reasons"])
        self.assertEqual(exhausted["maximum_position_weight_nav_fraction"], "0")
        self.assertEqual(exhausted["target_position_weight_nav_fraction"], "0")

        wide_stop = build(sizing_input(stop="80"))
        self.assertIn(
            "STOP_DISTANCE_EXCEEDS_CONSTITUTION", wide_stop["blocking_reasons"]
        )
        self.assertEqual(wide_stop["maximum_position_weight_nav_fraction"], "0")

    def test_candidate_must_match_exact_active_bucket_membership_lineage(self):
        value = sizing_input()
        value["candidate"]["rule_result_sha256"] = "f" * 64
        normalized_value = copy.deepcopy(value)
        normalized_value.pop("packet_sha256")
        value["packet_sha256"] = MODULE.payload_sha256(normalized_value)
        with self.assertRaisesRegex(
            MODULE.PositionSizingError, "CANDIDATE_MEMBERSHIP_LINEAGE_MISMATCH"
        ):
            build(value)

    def test_repository_default_constitution_and_unratified_policy_fail_closed(self):
        assignment, _ = sources()
        default = json.loads((ROOT / "config" / "constitution.json").read_text())
        with self.assertRaisesRegex(
            MODULE.PositionSizingError, "BUCKET_MEMBERSHIP_VALIDATION_FAILED"
        ):
            MODULE.build_packet(
                assignment,
                default,
                sizing_input(),
                policy(),
                "2026-08-21",
                CONTRACT,
            )
        with self.assertRaisesRegex(MODULE.PositionSizingError, "POLICY_IDENTITY_INVALID"):
            build(ratified_policy=policy(status="DRAFT"))

    def test_invalid_portfolio_state_crypto_lineage_and_digest_fail_closed(self):
        excessive = sizing_input(cash_available_nav_fraction="0.8")
        with self.assertRaisesRegex(
            MODULE.PositionSizingError, "DEPLOYED_PLUS_CASH_EXCEEDS_NAV"
        ):
            build(excessive)

        crypto = sizing_input()
        crypto["candidate"]["market"] = "CRYPTO"
        crypto["packet_sha256"] = MODULE.payload_sha256(crypto)
        with self.assertRaisesRegex(
            MODULE.PositionSizingError, "CRYPTO_LINEAGE_PRESENCE_MISMATCH"
        ):
            build(crypto)

        digest = sizing_input()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.PositionSizingError, "INPUT_SHA_MISMATCH"):
            build(digest)

    def test_output_derivation_action_and_authority_tamper_fail_closed(self):
        original = build()
        variants = []
        size = copy.deepcopy(original)
        size["target_position_weight_nav_fraction"] = "0.5"
        variants.append(size)
        action = copy.deepcopy(original)
        action["action"] = {"type": "BUY"}
        variants.append(action)
        authority = copy.deepcopy(original)
        authority["authority"]["trading_authorized"] = True
        variants.append(authority)
        for packet in variants:
            packet["packet_sha256"] = MODULE.payload_sha256(
                {key: value for key, value in packet.items() if key != "packet_sha256"}
            )
            with self.assertRaisesRegex(
                MODULE.PositionSizingError, "OUTPUT_DERIVATION_MISMATCH"
            ):
                MODULE.validate_packet(packet, CONTRACT)

    def test_source_order_is_normalized_and_inputs_are_immutable(self):
        assignment, constitution = sources()
        assignment["buckets"] = [
            BUCKET.bucket("BUCKET_BETA", "2"),
            BUCKET.bucket(),
        ]
        assignment["assignments"] = [
            BUCKET.assignment(
                asset_id="US:XNAS:MSFT",
                bucket_id="BUCKET_BETA",
                marker="8",
            ),
            BUCKET.assignment(),
        ]
        normalized = copy.deepcopy(assignment)
        normalized["buckets"] = sorted(normalized["buckets"], key=lambda row: row["bucket_id"])
        normalized["assignments"] = sorted(
            normalized["assignments"],
            key=lambda row: (row["asset_id"], row["valid_from"], row["bucket_id"]),
        )
        normalized.pop("packet_sha256")
        assignment["packet_sha256"] = BUCKET.MODULE.payload_sha256(normalized)
        value, ratified_policy = sizing_input(), policy()
        before = MODULE.canonical_json([assignment, constitution, value, ratified_policy])
        first = MODULE.build_packet(
            assignment, constitution, value, ratified_policy, "2026-08-21", CONTRACT
        )
        assignment["buckets"].reverse()
        assignment["assignments"].reverse()
        second = MODULE.build_packet(
            assignment, constitution, value, ratified_policy, "2026-08-21", CONTRACT
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        assignment["buckets"].reverse()
        assignment["assignments"].reverse()
        self.assertEqual(
            MODULE.canonical_json([assignment, constitution, value, ratified_policy]),
            before,
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
            assignment, constitution = sources()
            assignment_path = write_json(temp / "assignment.json", assignment)
            constitution_path = write_json(temp / "constitution.json", constitution)
            input_path = write_json(temp / "input.json", sizing_input())
            policy_path = write_json(temp / "policy.json", policy())
            output = temp / "nested" / "sizing.json"
            self.assertEqual(
                MODULE.run(
                    assignment_path, constitution_path, input_path, policy_path,
                    "2026-08-21", output,
                ),
                0,
            )
            serialized = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(MODULE.validate_packet(serialized, CONTRACT), serialized)
            forbidden = ROOT / "data" / "position_sizing_test.json"
            self.assertEqual(
                MODULE.run(
                    assignment_path, constitution_path, input_path, policy_path,
                    "2026-08-21", forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
