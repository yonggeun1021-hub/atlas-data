#!/usr/bin/env python3
"""P1-CR-08 public live-component registry regression."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "regime" / "crypto_live_component_registry.py"
SPEC = importlib.util.spec_from_file_location("crypto_live_component_registry", SOURCE)
REGISTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGISTRY)
DECISION_SPEC = importlib.util.spec_from_file_location(
    "crypto_live_registry_decision",
    ROOT / "decision" / "crypto_paper_decision_snapshot.py",
)
DECISION = importlib.util.module_from_spec(DECISION_SPEC)
DECISION_SPEC.loader.exec_module(DECISION)
AXIS = DECISION.LIVE_AXIS
BRIDGE_SPEC = importlib.util.spec_from_file_location(
    "crypto_live_registry_runtime_bridge",
    ROOT / "shadow" / "crypto_paper_runtime_bridge.py",
)
BRIDGE = importlib.util.module_from_spec(BRIDGE_SPEC)
BRIDGE_SPEC.loader.exec_module(BRIDGE)
BRIEFING_SPEC = importlib.util.spec_from_file_location(
    "crypto_live_registry_funnel_briefing",
    ROOT / "briefing" / "crypto_funnel_briefing.py",
)
BRIEFING = importlib.util.module_from_spec(BRIEFING_SPEC)
BRIEFING_SPEC.loader.exec_module(BRIEFING)
GENERATED_AT = "2026-08-29T07:35:18Z"
SOURCE_COMMIT = "a" * 40


def rehash(record: dict) -> dict:
    value = copy.deepcopy(record)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = REGISTRY.payload_sha256(value)
    return value


class CryptoLiveComponentRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = REGISTRY.build_registry(GENERATED_AT)

    def test_real_natural_sources_define_three_of_five_presence_axes(self):
        record = copy.deepcopy(self.record)
        self.assertEqual(
            list(record["rows"]),
            ["BTC_TREND", "BTC_RISK", "STABLECOIN_NET_ISSUANCE", "CRYPTO_BREADTH"],
        )
        self.assertEqual(len(record["source_directories"]), 3)
        factors = AXIS.build_axis_factors(record["rows"], GENERATED_AT)["CRYPTO"]
        self.assertEqual(
            {axis for axis, row in factors.items() if row["status"] == "DEFINED"},
            {"TREND", "RISK_VOL", "LIQUIDITY"},
        )
        self.assertEqual(factors["BREADTH"]["status"], "UNDEFINED")
        self.assertEqual(factors["LEADERSHIP"]["status"], "UNDEFINED")

    def test_registry_round_trips_and_every_source_directory_is_hash_bound(self):
        record = REGISTRY.validate_registry(
            copy.deepcopy(self.record), expected_generated_at=GENERATED_AT
        )
        for source in record["source_directories"]:
            self.assertRegex(source["tree_sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(source["file_count"], 0)
            self.assertLessEqual(source["downloaded_at"], GENERATED_AT)

    def test_source_available_after_cutoff_is_not_backfilled_into_old_packet(self):
        before_stablecoin = "2026-08-29T06:00:00Z"
        record = REGISTRY.build_registry(before_stablecoin)
        self.assertNotIn("STABLECOIN_NET_ISSUANCE", record["rows"])
        self.assertEqual(
            set(record["rows"]), {"BTC_TREND", "BTC_RISK", "CRYPTO_BREADTH"}
        )
        self.assertEqual(
            REGISTRY.validate_registry(
                copy.deepcopy(record), expected_generated_at=before_stablecoin
            ),
            record,
        )

    def test_self_rehashed_row_omission_or_tree_substitution_fails_rederivation(self):
        omitted = copy.deepcopy(self.record)
        omitted["rows"].pop("BTC_RISK")
        with self.assertRaisesRegex(
            REGISTRY.CryptoLiveComponentRegistryError,
            "REGISTRY_DERIVATION_MISMATCH",
        ):
            REGISTRY.validate_registry(
                rehash(omitted), expected_generated_at=GENERATED_AT
            )

        tree = copy.deepcopy(self.record)
        tree["source_directories"][0]["tree_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            REGISTRY.CryptoLiveComponentRegistryError,
            "REGISTRY_DERIVATION_MISMATCH",
        ):
            REGISTRY.validate_registry(rehash(tree), expected_generated_at=GENERATED_AT)

    def test_decision_snapshot_accepts_only_validated_registry_and_replays(self):
        record = DECISION.build_snapshot(
            generated_at=GENERATED_AT,
            source_commit=SOURCE_COMMIT,
            universe_entry=None,
            market_evidence_entry=None,
            realtime_entry=None,
            component_rows=copy.deepcopy(self.record),
        )
        without_registry = DECISION.build_snapshot(
            generated_at=GENERATED_AT,
            source_commit=SOURCE_COMMIT,
            universe_entry=None,
            market_evidence_entry=None,
            realtime_entry=None,
        )
        self.assertEqual(record["source_components"], self.record)
        self.assertNotEqual(record["generation_id"], without_registry["generation_id"])
        self.assertEqual(
            {axis for axis, row in record["crypto_regime_five_axis"].items()
             if row["status"] == "DEFINED"},
            {"TREND", "RISK_VOL", "LIQUIDITY"},
        )
        self.assertIn(
            "CRYPTO_BREADTH:TAXONOMY_COVERAGE_UNKNOWN",
            record["derivation_notes"],
        )
        self.assertIn(
            "CRYPTO_LEADERSHIP:"
            "DAILY_COMPONENT_ROW_PRODUCER_AND_DUAL_WINDOW_HISTORY_UNAVAILABLE",
            record["derivation_notes"],
        )
        self.assertEqual(
            DECISION.validate_output(copy.deepcopy(record), allow_external_sources=True),
            record,
        )

    def test_existing_runtime_bridge_revalidates_separate_observation_checkout(self):
        decision = DECISION.build_snapshot(
            generated_at=GENERATED_AT,
            source_commit=SOURCE_COMMIT,
            universe_entry=None,
            market_evidence_entry=None,
            realtime_entry=None,
            component_rows=copy.deepcopy(self.record),
        )
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".registry-observation-") as tmp:
            observation_root = Path(tmp)
            for source in self.record["source_directories"]:
                source_path = ROOT / source["path"]
                target_path = observation_root / source["path"]
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_path, target_path)
            validated = BRIDGE.validate_decision_snapshot(
                copy.deepcopy(decision),
                expected_source_commit=SOURCE_COMMIT,
                observation_root=observation_root,
            )
        self.assertEqual(validated, decision)

    def test_registry_decision_projects_same_three_axes_and_blockers_into_p8(self):
        decision = DECISION.build_snapshot(
            generated_at=GENERATED_AT,
            source_commit=SOURCE_COMMIT,
            universe_entry=None,
            market_evidence_entry=None,
            realtime_entry=None,
            component_rows=copy.deepcopy(self.record),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decision.json"
            path.write_text(json.dumps(decision, sort_keys=True), encoding="utf-8")
            briefing = BRIEFING.build_briefing(
                decision,
                source_path=str(path),
                source_file_sha256=BRIEFING._file_sha256(path),
                allow_external_sources=True,
            )
        self.assertEqual(briefing["regime"]["defined_axis_count"], 3)
        self.assertIn(
            "CRYPTO_BREADTH:TAXONOMY_COVERAGE_UNKNOWN", briefing["reasons"]
        )
        self.assertIn(
            "CRYPTO_LEADERSHIP:"
            "DAILY_COMPONENT_ROW_PRODUCER_AND_DUAL_WINDOW_HISTORY_UNAVAILABLE",
            briefing["reasons"],
        )
        self.assertFalse(briefing["authority"]["paper_order_authorized"])
        self.assertFalse(briefing["authority"]["exchange_order_authorized"])

    def test_registry_has_no_network_order_or_private_client(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for prohibited in (
            "requests", "urllib", "socket", "websockets", "http", "subprocess",
        ):
            self.assertNotIn(prohibited, imports)
        source = SOURCE.read_text(encoding="utf-8")
        for token in ("api.upbit.com", "/v1/orders", "myOrder", "myAsset"):
            self.assertNotIn(token, source)

    def test_workflow_enables_registry_before_decision_and_keeps_order_authority_false(self):
        workflow = (
            ROOT / ".github" / "workflows" / "upbit-realtime-capture.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("python3 test/test_crypto_live_component_registry.py", workflow)
        self.assertIn("--wire-regime-components", workflow)
        authority = REGISTRY.load_contract()["authority"]
        self.assertTrue(authority["evidence_registry_only"])
        for key, value in authority.items():
            if key != "evidence_registry_only":
                self.assertIs(value, False, key)


if __name__ == "__main__":
    unittest.main()
