#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rotation import theme_taxonomy_population as POP


def run_git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ThemeTaxonomyPopulationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        run_git(self.root, "init", "-q")
        run_git(self.root, "config", "user.email", "atlas@example.invalid")
        run_git(self.root, "config", "user.name", "Atlas Test")
        registry = json.loads((ROOT / "config/theme_taxonomy_source_fact_registry.json").read_text())
        paths = {
            registry["theme_contract"]["path"],
            registry["authority_registry"]["path"],
            *(item["path"] for item in registry["sources"]),
            *(item["path"] for item in registry["consumers"]),
        }
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-qm", "source facts")
        source_commit = run_git(self.root, "rev-parse", "HEAD")
        for source in registry["sources"]:
            source["first_seen_commit"] = source_commit
        self.registry_path = self.root / "config/theme_taxonomy_source_fact_registry.json"
        write_json(self.registry_path, registry)
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-qm", "population registry")

    def tearDown(self):
        self.temp.cleanup()

    def commit_file_and_repin(self, relative: str, value: dict) -> None:
        path = self.root / relative
        write_json(path, value)
        registry = json.loads(self.registry_path.read_text())
        for source in registry["sources"]:
            if source["path"] == relative:
                source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                source["record_count"] = len(value["records"])
        write_json(self.registry_path, registry)
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-qm", "mutated source")
        commit = run_git(self.root, "rev-parse", "HEAD")
        registry = json.loads(self.registry_path.read_text())
        for source in registry["sources"]:
            if source["path"] == relative:
                source["first_seen_commit"] = commit
        write_json(self.registry_path, registry)
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-qm", "repin source")

    def build(self) -> dict:
        return POP.build_population("2026-09-01", self.registry_path, run_git(self.root, "rev-parse", "HEAD"))

    def test_actual_population_is_deterministic_and_fail_closed(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["authority_registry_record_count"], 0)
        self.assertEqual(first["ratified_graph_authority_record_count"], 0)
        self.assertEqual(first["market_population"]["KOREA"]["ratified_source_fact_count"], 48)
        self.assertEqual(first["market_population"]["US"]["ratified_source_fact_count"], 0)
        self.assertEqual(first["market_population"]["CRYPTO"]["ratified_source_fact_count"], 160)
        self.assertEqual(first["market_population"]["CRYPTO"]["unique_active_identity_count"], 152)
        self.assertEqual(first["market_population"]["CRYPTO"]["consistent_cross_source_overlap_count"], 8)
        self.assertTrue(all(not item["authority_compatible"] for item in first["consumer_contract_pins"]))
        self.assertTrue(all(value is False for key, value in first["authority"].items() if key != "source_fact_audit_authorized"))

    def test_source_byte_tamper_fails(self):
        path = self.root / "config/korea_leadership_policy.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(POP.ThemeTaxonomyPopulationError, "PIN_BYTES_MISMATCH"):
            self.build()

    def test_registry_byte_tamper_fails(self):
        self.registry_path.write_bytes(self.registry_path.read_bytes() + b" ")
        with self.assertRaisesRegex(POP.ThemeTaxonomyPopulationError, "REGISTRY_GIT_BYTES_MISMATCH"):
            self.build()

    def test_duplicate_active_identity_fails(self):
        relative = "config/korea_leadership_policy.json"
        value = json.loads((self.root / relative).read_text())
        value["records"].append(copy.deepcopy(value["records"][0]))
        self.commit_file_and_repin(relative, value)
        with self.assertRaisesRegex(POP.ThemeTaxonomyPopulationError, "SOURCE_IDENTITY_DUPLICATE"):
            self.build()

    def test_cross_source_crypto_collision_fails(self):
        relative = "config/upbit_exclusion_taxonomy.json"
        value = json.loads((self.root / relative).read_text())
        value["records"][0]["category"] = "stablecoin"
        self.commit_file_and_repin(relative, value)
        with self.assertRaisesRegex(POP.ThemeTaxonomyPopulationError, "CROSS_SOURCE_IDENTITY_COLLISION:BTC"):
            self.build()


if __name__ == "__main__":
    unittest.main()
