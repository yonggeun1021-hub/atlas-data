#!/usr/bin/env python3
"""P3-01/P3-03 committed Korea source-coverage population regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


M = load("korea_global_universe_populate", ".github/scripts/korea_global_universe_populate.py")
FIXTURE = load("krx_global_universe_fixture", "test/test_krx_global_universe.py")
WORKFLOW = ROOT / ".github/workflows/p1-kr05-korea-breadth-live.yml"


def packet() -> dict:
    return FIXTURE.KRU.build_packet(FIXTURE.sample_input())


def write_candidate(root: Path, value: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / M.SOURCE_NAME
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class KoreaGlobalUniversePopulateTests(unittest.TestCase):
    def setUp(self):
        self.original_root = M.ROOT

    def tearDown(self):
        M.ROOT = self.original_root

    def test_exact_adapter_packet_is_persisted_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.ROOT = root
            derived = root / "derived"
            expected = packet()
            write_candidate(derived, expected)
            first = M.populate(derived)
            second = M.populate(derived)
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            saved = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved, expected)
            self.assertEqual(saved["total_count"], 3)
            self.assertEqual(saved["market_counts"], {"KOSDAQ": 1, "KOSPI": 2})

    def test_existing_same_date_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.ROOT = root
            derived = root / "derived"
            first = packet()
            write_candidate(derived, first)
            M.populate(derived)
            changed = copy.deepcopy(first)
            changed["asset_master"]["master_id"] = "OTHER"
            changed["asset_master"]["payload_sha256"] = FIXTURE.KRU.GAM.payload_sha256(
                {k: v for k, v in changed["asset_master"].items() if k != "payload_sha256"}
            )
            changed["payload_sha256"] = M.payload_sha256(
                {k: v for k, v in changed.items() if k != "payload_sha256"}
            )
            write_candidate(derived, changed)
            with self.assertRaisesRegex(M.PopulationError, "EXISTING_PACKET_DRIFT_OR_TAMPER"):
                M.populate(derived)

    def test_outer_hash_tamper_is_rejected(self):
        value = packet()
        value["market_counts"]["KOSPI"] = 99
        with self.assertRaisesRegex(M.PopulationError, "PACKET_SHA256_MISMATCH"):
            M.validate_packet(value)

    def test_nested_semantic_tamper_with_rehash_is_rejected(self):
        value = packet()
        value["asset_master"]["records"][0]["investable_eligible"] = True
        value["asset_master"]["payload_sha256"] = FIXTURE.KRU.GAM.payload_sha256(
            {k: v for k, v in value["asset_master"].items() if k != "payload_sha256"}
        )
        value["payload_sha256"] = M.payload_sha256(
            {k: v for k, v in value.items() if k != "payload_sha256"}
        )
        with self.assertRaisesRegex(M.PopulationError, "ASSET_MASTER_INVALID"):
            M.validate_packet(value)

    def test_rehashed_authority_promotion_is_rejected(self):
        value = packet()
        value["authority"]["trading_authorized"] = True
        value["payload_sha256"] = M.payload_sha256(
            {k: v for k, v in value.items() if k != "payload_sha256"}
        )
        with self.assertRaisesRegex(M.PopulationError, "AUTHORITY_MISMATCH"):
            M.validate_packet(value)

    def test_rehashed_effective_interval_extension_is_rejected(self):
        value = packet()
        value["effective_interval"]["valid_to"] = "2099-01-01"
        value["payload_sha256"] = M.payload_sha256(
            {k: v for k, v in value.items() if k != "payload_sha256"}
        )
        with self.assertRaisesRegex(M.PopulationError, "EFFECTIVE_INTERVAL_MISMATCH"):
            M.validate_packet(value)

    def test_partial_market_and_rehashed_counts_are_rejected(self):
        value = packet()
        value["market_counts"].pop("KOSDAQ")
        value["payload_sha256"] = M.payload_sha256(
            {k: v for k, v in value.items() if k != "payload_sha256"}
        )
        with self.assertRaisesRegex(M.PopulationError, "MARKET_COUNTS_MISMATCH"):
            M.validate_packet(value)

    def test_rehashed_outer_source_lineage_drift_is_rejected(self):
        value = packet()
        value["source_snapshots"][0]["source_sha256"] = "0" * 64
        value["payload_sha256"] = M.payload_sha256(
            {k: v for k, v in value.items() if k != "payload_sha256"}
        )
        with self.assertRaisesRegex(M.PopulationError, "SOURCE_LINEAGE_REDERIVATION_MISMATCH"):
            M.validate_packet(value)

    def test_public_packet_contains_no_raw_response_or_price_fields(self):
        value = packet()
        M.validate_packet(value)
        serialized = json.dumps(value, ensure_ascii=False)
        for forbidden in (
            "response_body_base64", "TDD_CLSPRC", "TDD_OPNPRC", "ACC_TRDVAL", "MKTCAP"
        ):
            self.assertNotIn(forbidden, serialized)

    def test_workflow_reuses_artifact_and_stages_both_outputs(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("korea_global_universe_populate.py", text)
        self.assertIn("--derived-dir \"$RUNNER_TEMP/p1-kr05-derived\"", text)
        add_line = next(line.strip() for line in text.splitlines() if line.strip().startswith("git add data/observations/korea_breadth_context"))
        self.assertIn("data/observations/krx_global_universe", add_line.split())
        self.assertEqual(
            text.count("python3 .github/scripts/korea_breadth_derived_outputs.py"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
