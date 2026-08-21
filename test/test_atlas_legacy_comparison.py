#!/usr/bin/env python3
"""P10-02 Atlas versus existing-judgment comparison regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "atlas_legacy_comparison.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("atlas_legacy_comparison", SOURCE)
SHADOW_FIXTURE = load_module(
    "comparison_shadow_fixture", ROOT / "test" / "test_three_market_shadow_ledger.py"
)
CONTRACT = MODULE.load_contract()


def shadow_ledger():
    return SHADOW_FIXTURE.append(
        SHADOW_FIXTURE.decision(), "2026-08-21T02:15:00Z"
    )


def judgment(market, action="WATCH"):
    return {
        "decision_id": "atlas-2026-08-21-morning",
        "decision_date": "2026-08-21",
        "slot": "morning",
        "market": market,
        "decided_at": "2026-08-21T02:12:00Z",
        "action_label": action,
        "source_ref": f"test://legacy/{market}",
        "source_sha256": {"US": "a", "KOREA": "b", "CRYPTO": "c"}[market] * 64,
    }


def legacy(rows=None):
    value = {
        "schema_version": CONTRACT["legacy_batch_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "batch_id": "LEGACY.BATCH.001",
        "observed_at": "2026-08-21T03:00:00Z",
        "judgments": [judgment(market) for market in CONTRACT["markets"]] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def outcome(market, label="POSITIVE"):
    return {
        "decision_id": "atlas-2026-08-21-morning",
        "market": market,
        "observed_at": "2026-08-21T02:55:00Z",
        "outcome_label": label,
        "source_ref": f"test://outcome/{market}",
        "source_sha256": {"US": "d", "KOREA": "e", "CRYPTO": "f"}[market] * 64,
    }


def outcomes(rows=None):
    value = {
        "schema_version": CONTRACT["outcome_batch_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "batch_id": "OUTCOME.BATCH.001",
        "observed_at": "2026-08-21T03:00:00Z",
        "evaluation_window_id": "WINDOW.SAME.DAY.001",
        "outcomes": [outcome(market) for market in CONTRACT["markets"]] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class AtlasLegacyComparisonTests(unittest.TestCase):
    def test_contract_is_alignment_only_and_forbids_winner_claims(self):
        self.assertTrue(CONTRACT["authority"]["same_period_evidence_alignment_only"])
        self.assertEqual(CONTRACT["comparison_key"], "DECISION_ID_MARKET")
        for key, value in CONTRACT["authority"].items():
            if key != "same_period_evidence_alignment_only":
                self.assertFalse(value, key)

    def test_same_period_rows_align_all_three_markets_by_exact_key(self):
        packet = MODULE.build_packet(
            shadow_ledger(), legacy(), outcomes(), "2026-08-21T03:05:00Z", CONTRACT
        )
        self.assertEqual(len(packet["comparisons"]), 3)
        self.assertEqual(
            [row["market"] for row in packet["comparisons"]],
            ["US", "KOREA", "CRYPTO"],
        )
        self.assertEqual(packet["summary"]["legacy_matched_count"], 3)
        self.assertEqual(packet["summary"]["outcome_matched_count"], 3)
        for row in packet["comparisons"]:
            self.assertEqual(row["decision_id"], "atlas-2026-08-21-morning")
            self.assertEqual(row["legacy_judgment"]["market"], row["market"])
            self.assertEqual(row["outcome"]["market"], row["market"])

    def test_undefined_atlas_action_is_not_mislabeled_no_action(self):
        packet = MODULE.build_packet(
            shadow_ledger(), legacy(), outcomes(), "2026-08-21T03:05:00Z", CONTRACT
        )
        for row in packet["comparisons"]:
            self.assertIsNone(row["atlas_action"])
            self.assertEqual(row["legacy_action"], "WATCH")
            self.assertEqual(row["action_alignment"], "UNDEFINED")
            self.assertIn("ATLAS_ACTION_UNDEFINED", row["comparison_reasons"])

    def test_outcomes_are_preserved_but_effectiveness_and_winner_stay_closed(self):
        packet = MODULE.build_packet(
            shadow_ledger(), legacy(), outcomes(), "2026-08-21T03:05:00Z", CONTRACT
        )
        for row in packet["comparisons"]:
            self.assertEqual(row["outcome"]["outcome_label"], "POSITIVE")
            self.assertEqual(row["effectiveness"], "NOT_EVALUATED")
            self.assertIsNone(row["winner"])
        self.assertEqual(packet["summary"]["effectiveness_evaluated_count"], 0)
        self.assertEqual(packet["summary"]["winner_count"], 0)

    def test_missing_legacy_and_outcome_are_explicit_not_imputed(self):
        packet = MODULE.build_packet(
            shadow_ledger(), legacy([]), outcomes([]),
            "2026-08-21T03:05:00Z", CONTRACT,
        )
        self.assertEqual(packet["summary"]["legacy_matched_count"], 0)
        self.assertEqual(packet["summary"]["outcome_matched_count"], 0)
        for row in packet["comparisons"]:
            self.assertIsNone(row["legacy_action"])
            self.assertIsNone(row["legacy_judgment"])
            self.assertIsNone(row["outcome"])
            self.assertIn("LEGACY_JUDGMENT_MISSING", row["comparison_reasons"])
            self.assertIn("OUTCOME_MISSING", row["comparison_reasons"])

    def test_duplicate_keys_future_inputs_and_authority_drift_fail_closed(self):
        duplicate = legacy([judgment("US"), judgment("US")])
        with self.assertRaisesRegex(
            MODULE.AtlasLegacyComparisonError, "LEGACY_ROW_KEY_DUPLICATE"
        ):
            MODULE.build_packet(
                shadow_ledger(), duplicate, outcomes(),
                "2026-08-21T03:05:00Z", CONTRACT,
            )
        future = outcomes()
        future["observed_at"] = "2026-08-21T03:06:00Z"
        future["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in future.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.AtlasLegacyComparisonError, "OUTCOME_BATCH_FROM_FUTURE"
        ):
            MODULE.build_packet(
                shadow_ledger(), legacy(), future,
                "2026-08-21T03:05:00Z", CONTRACT,
            )
        drift = legacy()
        drift["authority"]["performance_interpretation_authorized"] = True
        drift["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in drift.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.AtlasLegacyComparisonError, "LEGACY_BATCH_IDENTITY_INVALID"
        ):
            MODULE.build_packet(
                shadow_ledger(), drift, outcomes(),
                "2026-08-21T03:05:00Z", CONTRACT,
            )

        extra_row = judgment("US")
        extra_row["decision_date"] = "2026-08-20"
        extra_row["decision_id"] = "atlas-2026-08-20-morning"
        extra = legacy([extra_row])
        with self.assertRaisesRegex(
            MODULE.AtlasLegacyComparisonError, "LEGACY_KEY_NOT_IN_SHADOW"
        ):
            MODULE.build_packet(
                shadow_ledger(), extra, outcomes([]),
                "2026-08-21T03:05:00Z", CONTRACT,
            )

    def test_tampered_shadow_lineage_summary_and_winner_fail_closed(self):
        shadow = shadow_ledger()
        broken = copy.deepcopy(shadow)
        broken["records"][0]["record_sha256"] = "0" * 64
        broken["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in broken.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.AtlasLegacyComparisonError, "SHADOW_LEDGER_INVALID"
        ):
            MODULE.build_packet(
                broken, legacy(), outcomes(), "2026-08-21T03:05:00Z", CONTRACT
            )

        original = MODULE.build_packet(
            shadow, legacy(), outcomes(), "2026-08-21T03:05:00Z", CONTRACT
        )
        winner = copy.deepcopy(original)
        winner["comparisons"][0]["winner"] = "ATLAS"
        with self.assertRaisesRegex(
            MODULE.AtlasLegacyComparisonError, "PACKET_CONTENT_MISMATCH"
        ):
            MODULE.validate_packet(winner, CONTRACT)

    def test_lineage_binds_all_three_exact_input_packets(self):
        shadow = shadow_ledger()
        old = legacy()
        result = outcomes()
        packet = MODULE.build_packet(
            shadow, old, result, "2026-08-21T03:05:00Z", CONTRACT
        )
        self.assertEqual(packet["lineage"]["shadow_ledger_sha256"], shadow["packet_sha256"])
        self.assertEqual(packet["lineage"]["legacy_batch_sha256"], old["packet_sha256"])
        self.assertEqual(packet["lineage"]["outcome_batch_sha256"], result["packet_sha256"])
        self.assertEqual(packet["source_packets"]["SHADOW_LEDGER"], shadow)
        self.assertEqual(packet["source_packets"]["LEGACY_BATCH"], old)
        self.assertEqual(packet["source_packets"]["OUTCOME_BATCH"], result)
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)

    def test_self_rehashed_embedded_source_tamper_fails_closed(self):
        packet = MODULE.build_packet(
            shadow_ledger(), legacy(), outcomes(), "2026-08-21T03:05:00Z", CONTRACT
        )
        packet["source_packets"]["LEGACY_BATCH"]["authority"][
            "performance_interpretation_authorized"
        ] = True
        source = packet["source_packets"]["LEGACY_BATCH"]
        source["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in source.items() if key != "packet_sha256"}
        )
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.AtlasLegacyComparisonError, "LEGACY_BATCH_IDENTITY_INVALID"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_build_is_deterministic_and_inputs_are_immutable(self):
        shadow = shadow_ledger()
        old = legacy()
        result = outcomes()
        before = MODULE.canonical_json([shadow, old, result])
        first = MODULE.build_packet(
            shadow, old, result, "2026-08-21T03:05:00Z", CONTRACT
        )
        second = MODULE.build_packet(
            shadow, old, result, "2026-08-21T03:05:00Z", CONTRACT
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json([shadow, old, result]), before)

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
            paths = []
            for name, value in (
                ("shadow", shadow_ledger()), ("legacy", legacy()), ("outcome", outcomes())
            ):
                path = temp / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            output = temp / "out" / "comparison.json"
            self.assertEqual(
                MODULE.run(*paths, "2026-08-21T03:05:00Z", output), 0
            )
            self.assertEqual(json.loads(output.read_text())["summary"]["comparison_row_count"], 3)
            forbidden = ROOT / "data" / "atlas_legacy_comparison_test.json"
            self.assertEqual(
                MODULE.run(*paths, "2026-08-21T03:05:00Z", forbidden), 1
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
