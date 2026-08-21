#!/usr/bin/env python3
"""P7-01 explicit-only Portfolio bucket membership regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "bucket_membership.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("bucket_membership", SOURCE)
CONTRACT = MODULE.load_contract()


def ratified_constitution():
    return {
        "_comment": "synthetic test-only ratified fixture",
        "status": "ratified",
        "ratified_at": "2026-08-20T00:00:00Z",
        "constitution_version": "test-constitution/v1",
        "B1_bucket_definition": {
            "definition_ref": "test://constitution/B1",
            "definition_sha256": "1" * 64,
        },
        "B2_cash_floor_pct": 50,
        "B3_bucket_max_pct": 25,
        "B4_position_max_pct": 10,
        "B5_stop_loss_pct": 10,
        "B6_portfolio_max_loss_pct": 5,
        "B7_evidence_state_max_pct": {
            "backtest_only": 0,
            "forward_early": 1,
            "forward_established": 2,
            "operating": 3,
        },
        "amendment_log": [],
    }


def bucket(bucket_id="BUCKET_ALPHA", marker="a"):
    return {
        "bucket_id": bucket_id,
        "definition_ref": f"test://bucket/{bucket_id}",
        "definition_sha256": marker * 64,
    }


def assignment(
    asset_id="US:XNAS:TSM",
    kind="CANDIDATE",
    market="US",
    bucket_id="BUCKET_ALPHA",
    start="2026-08-20",
    end=None,
    marker="b",
):
    return {
        "asset_id": asset_id,
        "subject_kind": kind,
        "market": market,
        "bucket_id": bucket_id,
        "valid_from": start,
        "valid_to": end,
        "asset_identity_sha256": marker * 64,
        "discovery_result_sha256": "c" * 64 if kind == "CANDIDATE" else None,
        "rule_result_sha256": "d" * 64,
        "holding_record_sha256": "e" * 64 if kind == "HOLDING" else None,
        "assignment_basis_ref": f"test://assignment/{asset_id}/{start}",
        "assignment_basis_sha256": "f" * 64,
    }


def assignment_set(constitution=None, buckets=None, assignments=None):
    constitution = ratified_constitution() if constitution is None else constitution
    value = {
        "schema_version": "bucket_assignment_set/1",
        "contract_version": "bucket_membership/1",
        "assignment_set_id": "TEST-BUCKET-SET-2026-08-20",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "constitution_version": constitution["constitution_version"],
        "constitution_sha256": MODULE.payload_sha256(constitution),
        "b1_bucket_definition_sha256": MODULE.payload_sha256(
            constitution["B1_bucket_definition"]
        ),
        "buckets": [bucket()] if buckets is None else buckets,
        "assignments": [assignment()] if assignments is None else assignments,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["buckets"] = sorted(normalized["buckets"], key=lambda row: row["bucket_id"])
    normalized["assignments"] = sorted(
        normalized["assignments"],
        key=lambda row: (row["asset_id"], row["valid_from"], row["bucket_id"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class BucketMembershipTests(unittest.TestCase):
    def test_contract_requires_explicit_ratification_and_closes_action_authority(self):
        self.assertEqual(CONTRACT["assignment_mode"], "EXPLICIT_RATIFIED_ONLY")
        self.assertEqual(
            CONTRACT["repository_default_status"],
            "BLOCKED_UNTIL_CONSTITUTION_B1_RATIFIED",
        )
        self.assertTrue(CONTRACT["authority"]["membership_registry_validation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "membership_registry_validation_only":
                self.assertFalse(value, key)

    def test_repository_default_constitution_blocks_membership(self):
        default = json.loads((ROOT / "config" / "constitution.json").read_text())
        with self.assertRaisesRegex(
            MODULE.BucketMembershipError,
            "CONSTITUTION_NOT_RATIFIED",
        ):
            MODULE.build_packet(
                assignment_set(), default, "2026-08-21", CONTRACT
            )

    def test_explicit_candidate_and_holding_create_one_membership_each(self):
        constitution = ratified_constitution()
        rows = [
            assignment(),
            assignment(
                asset_id="CRYPTO:KRAKEN:BTC",
                kind="HOLDING",
                market="CRYPTO",
                marker="9",
            ),
        ]
        result = MODULE.build_packet(
            assignment_set(constitution, assignments=rows),
            constitution,
            "2026-08-21",
            CONTRACT,
        )
        self.assertEqual(result["status"], "MEMBERSHIP_VALIDATED_EXPLICIT_ONLY")
        self.assertEqual(result["summary"]["subject_count"], 2)
        self.assertEqual(result["summary"]["active_membership_count"], 2)
        self.assertEqual(result["summary"]["by_subject_kind"], {
            "CANDIDATE": 1, "HOLDING": 1,
        })
        self.assertEqual(
            [row["asset_id"] for row in result["active_memberships"]],
            ["CRYPTO:KRAKEN:BTC", "US:XNAS:TSM"],
        )
        self.assertFalse(result["authority"]["automatic_assignment_authorized"])
        self.assertFalse(result["authority"]["position_sizing_authorized"])

    def test_sequential_history_resolves_exactly_one_active_membership(self):
        constitution = ratified_constitution()
        rows = [
            assignment(end="2026-08-21"),
            assignment(
                bucket_id="BUCKET_BETA",
                start="2026-08-21",
                marker="b",
            ),
        ]
        buckets = [bucket("BUCKET_BETA", "2"), bucket()]
        value = assignment_set(constitution, buckets, rows)
        result = MODULE.build_packet(value, constitution, "2026-08-21", CONTRACT)
        self.assertEqual(len(result["assignment_history"]), 2)
        self.assertEqual(result["active_memberships"][0]["bucket_id"], "BUCKET_BETA")

        permuted = copy.deepcopy(value)
        permuted["buckets"].reverse()
        permuted["assignments"].reverse()
        self.assertEqual(
            MODULE.canonical_json(result),
            MODULE.canonical_json(
                MODULE.build_packet(permuted, constitution, "2026-08-21", CONTRACT)
            ),
        )

    def test_overlapping_assignments_fail_closed(self):
        constitution = ratified_constitution()
        rows = [
            assignment(end="2026-08-22"),
            assignment(
                bucket_id="BUCKET_BETA",
                start="2026-08-21",
                marker="b",
            ),
        ]
        with self.assertRaisesRegex(
            MODULE.BucketMembershipError,
            "BUCKET_ASSIGNMENT_OVERLAP",
        ):
            MODULE.build_packet(
                assignment_set(
                    constitution,
                    [bucket(), bucket("BUCKET_BETA", "2")],
                    rows,
                ),
                constitution,
                "2026-08-21",
                CONTRACT,
            )

    def test_candidate_holding_and_rule_lineage_are_required(self):
        constitution = ratified_constitution()
        cases = []
        candidate = assignment()
        candidate["discovery_result_sha256"] = None
        cases.append((candidate, "CANDIDATE_LINEAGE_INVALID"))
        holding = assignment(kind="HOLDING")
        holding["holding_record_sha256"] = None
        cases.append((holding, "HOLDING_LINEAGE_INVALID"))
        rule = assignment()
        rule["rule_result_sha256"] = None
        cases.append((rule, "RULE_RESULT_SHA_INVALID"))
        for row, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.BucketMembershipError, error
            ):
                MODULE.build_packet(
                    assignment_set(constitution, assignments=[row]),
                    constitution,
                    "2026-08-21",
                    CONTRACT,
                )

    def test_unknown_duplicate_bucket_and_identity_collision_fail_closed(self):
        constitution = ratified_constitution()
        unknown = assignment(bucket_id="BUCKET_UNKNOWN")
        with self.assertRaisesRegex(MODULE.BucketMembershipError, "BUCKET_UNKNOWN"):
            MODULE.build_packet(
                assignment_set(constitution, assignments=[unknown]),
                constitution,
                "2026-08-21",
                CONTRACT,
            )

        duplicate = [bucket(), copy.deepcopy(bucket())]
        with self.assertRaisesRegex(MODULE.BucketMembershipError, "BUCKET_ID_DUPLICATE"):
            MODULE.build_packet(
                assignment_set(constitution, buckets=duplicate),
                constitution,
                "2026-08-21",
                CONTRACT,
            )

        collision = [assignment(), assignment(asset_id="US:XNAS:OTHER")]
        with self.assertRaisesRegex(MODULE.BucketMembershipError, "ASSET_IDENTITY_COLLISION"):
            MODULE.build_packet(
                assignment_set(constitution, assignments=collision),
                constitution,
                "2026-08-21",
                CONTRACT,
            )

    def test_constitution_and_b1_hash_drift_fail_closed(self):
        constitution = ratified_constitution()
        full = assignment_set(constitution)
        full["constitution_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.BucketMembershipError, "CONSTITUTION_SHA_MISMATCH"):
            MODULE.build_packet(full, constitution, "2026-08-21", CONTRACT)

        b1 = assignment_set(constitution)
        b1["b1_bucket_definition_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.BucketMembershipError, "CONSTITUTION_B1_SHA_MISMATCH"):
            MODULE.build_packet(b1, constitution, "2026-08-21", CONTRACT)

    def test_ratification_authority_and_packet_digest_drift_fail_closed(self):
        constitution = ratified_constitution()
        cases = []
        status = assignment_set(constitution)
        status["status"] = "DRAFT"
        cases.append((status, "ASSIGNMENT_SET_IDENTITY_INVALID"))
        authority = assignment_set(constitution)
        authority["authority"]["position_sizing_authorized"] = True
        cases.append((authority, "ASSIGNMENT_SET_IDENTITY_INVALID"))
        digest = assignment_set(constitution)
        digest["packet_sha256"] = "0" * 64
        cases.append((digest, "ASSIGNMENT_SET_SHA_MISMATCH"))
        for value, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.BucketMembershipError, error
            ):
                MODULE.build_packet(value, constitution, "2026-08-21", CONTRACT)

    def test_temporal_boundaries_fail_closed(self):
        constitution = ratified_constitution()
        inactive = assignment_set(constitution)
        with self.assertRaisesRegex(
            MODULE.BucketMembershipError,
            "ASSIGNMENT_SET_NOT_EFFECTIVE",
        ):
            MODULE.build_packet(inactive, constitution, "2026-08-19", CONTRACT)

        no_current = assignment(end="2026-08-21")
        with self.assertRaisesRegex(
            MODULE.BucketMembershipError,
            "ACTIVE_MEMBERSHIP_COUNT_INVALID",
        ):
            MODULE.build_packet(
                assignment_set(constitution, assignments=[no_current]),
                constitution,
                "2026-08-21",
                CONTRACT,
            )

    def test_output_is_deterministic_hash_bound_and_inputs_are_not_mutated(self):
        constitution = ratified_constitution()
        value = assignment_set(constitution)
        before_constitution = MODULE.canonical_json(constitution)
        before_value = MODULE.canonical_json(value)
        first = MODULE.build_packet(value, constitution, "2026-08-21", CONTRACT)
        second = MODULE.build_packet(value, constitution, "2026-08-21", CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(constitution), before_constitution)
        self.assertEqual(MODULE.canonical_json(value), before_value)
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_self_rehashed_output_semantic_tamper_fails_closed(self):
        constitution = ratified_constitution()
        packet = MODULE.build_packet(
            assignment_set(constitution), constitution, "2026-08-21", CONTRACT
        )
        packet["summary"]["subject_count"] += 1
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.BucketMembershipError,
            "OUTPUT_SUMMARY_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_source_is_offline_and_cli_writes_only_outside_repository(self):
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
            tmp = Path(tmp)
            constitution = ratified_constitution()
            assignment_path = write_json(tmp / "assignment.json", assignment_set(constitution))
            constitution_path = write_json(tmp / "constitution.json", constitution)
            output_path = tmp / "nested" / "membership.json"
            self.assertEqual(
                MODULE.run(
                    assignment_path,
                    constitution_path,
                    "2026-08-21",
                    output_path,
                ),
                0,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["active_membership_count"], 1)
            self.assertEqual(list(output_path.parent.glob(".membership.json.*")), [])

            forbidden = ROOT / "data" / "bucket_membership_test.json"
            self.assertEqual(
                MODULE.run(
                    assignment_path,
                    constitution_path,
                    "2026-08-21",
                    forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
