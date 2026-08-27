#!/usr/bin/env python3
"""P3-04 deterministic taxonomy gap review inventory regression."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_taxonomy_gap_inventory.py"
WORKFLOW = ROOT / ".github" / "workflows" / "crypto-breadth-capture.yml"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


INVENTORY = _load("crypto_taxonomy_gap_inventory", SCRIPT)
FIXTURES = _load("crypto_breadth_test_fixtures", ROOT / "test" / "test_crypto_breadth.py")


def gap_fixture(root: Path):
    prices = {
        "BTC": (100, 101, 999),
        "A": (90, 91, 800),
        "B": (80, 81, 700),
        "C": (70, 71, 600),
    }
    snapshot = FIXTURES.write_snapshot(root / "raw", prices=prices)
    policy = FIXTURES.write_policy(root / "policy.json", target=3)
    taxonomy = FIXTURES.write_taxonomy(
        root / "taxonomy.json",
        {"BTC": "eligible_crypto", "A": "eligible_crypto", "C": "stablecoin"},
    )
    return snapshot, policy, taxonomy


class CryptoTaxonomyGapInventoryTests(unittest.TestCase):
    def test_gap_inventory_is_diagnostic_only_and_creates_no_classification(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            result = INVENTORY.populate(
                snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            record = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "REVIEW_INVENTORY_ONLY")
            self.assertEqual(
                record["source_outcome"]["unknown_reason"],
                "TAXONOMY_COVERAGE_UNKNOWN",
            )
            self.assertEqual(
                [row["canonical_asset_id"] for row in record["review_population"]["taxonomy_unknown_before_cutoff"]],
                ["B"],
            )
            self.assertEqual(record["authority"]["classifications_created"], 0)
            self.assertEqual(record["authority"]["records_ratified"], 0)
            for key, value in record["authority"].items():
                if key not in {"classifications_created", "records_ratified"}:
                    self.assertFalse(value, key)

    def test_source_date_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(INVENTORY.InventoryError, "SOURCE_DATE_INVALID"):
                INVENTORY.build_inventory("../2026-08-26", raw_root=Path(raw))

    def test_record_binds_exact_manifest_policy_and_taxonomy_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            record = INVENTORY.build_inventory(
                snapshot.name,
                raw_root=snapshot.parent,
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            transform = INVENTORY.CB.build_transform(
                snapshot,
                universe_policy_path=policy,
                exclusion_taxonomy_path=taxonomy,
            )
            self.assertEqual(record["lineage"]["manifest_sha256"], transform["lineage"]["manifest_sha256"])
            self.assertEqual(record["lineage"]["universe_policy_sha256"], transform["universe"]["policy_sha256"])
            self.assertEqual(record["lineage"]["taxonomy_policy_sha256"], transform["universe"]["taxonomy"]["policy_sha256"])

    def test_rebuild_is_byte_identical_and_existing_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            args = dict(
                source_date=snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            first = INVENTORY.populate(**args)
            before = Path(first["path"]).read_bytes()
            second = INVENTORY.populate(**args)
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(before, Path(second["path"]).read_bytes())
            self.assertEqual(first["payload_sha256"], second["payload_sha256"])

    def test_tamper_and_self_rehash_still_fail_independent_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            result = INVENTORY.populate(
                snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            target = Path(result["path"])
            record = json.loads(target.read_text(encoding="utf-8"))
            record["authority"]["records_ratified"] = 1
            record["payload_sha256"] = INVENTORY.payload_sha256(
                {key: value for key, value in record.items() if key != "payload_sha256"}
            )
            target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(INVENTORY.InventoryError, "DRIFT_OR_TAMPER"):
                INVENTORY.populate(
                    snapshot.name,
                    raw_root=snapshot.parent,
                    data_root=Path(data),
                    universe_policy_path=policy,
                    taxonomy_path=taxonomy,
                )

    def test_real_latest_archive_yields_ranked_review_population_without_authority(self):
        raw_root = ROOT / "evidence" / "crypto" / "breadth" / "raw"
        source_date = sorted(path.name for path in raw_root.iterdir() if path.is_dir())[-1]
        record = INVENTORY.build_inventory(source_date, raw_root=raw_root)
        self.assertEqual(record["status"], "REVIEW_INVENTORY_ONLY")
        self.assertGreater(record["selection_context"]["unknown_before_cutoff_count"], 0)
        ranks = [
            row["rank_before_taxonomy"]
            for row in record["review_population"]["taxonomy_unknown_before_cutoff"]
        ]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(record["authority"]["records_ratified"], 0)
        self.assertFalse(record["authority"]["investability_authorized"])

    def test_workflow_reuses_capture_and_commits_inventory_after_raw(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count('cron: "40 0 * * *"'), 1)
        self.assertIn("crypto_taxonomy_gap_inventory.py", text)
        self.assertIn("Populate P3-04 taxonomy review inventory", text)
        self.assertIn("Commit P3-04 taxonomy review inventory", text)
        self.assertNotIn("repository_dispatch", text)
        capture = text.index("Capture complete append-only Kraken USD universe")
        inventory = text.index("Populate P3-04 taxonomy review inventory")
        raw_commit = text.index("Commit immutable raw snapshot and run telemetry")
        inventory_commit = text.index("Commit P3-04 taxonomy review inventory")
        self.assertLess(capture, inventory)
        self.assertLess(inventory, raw_commit)
        self.assertLess(raw_commit, inventory_commit)


if __name__ == "__main__":
    unittest.main()
