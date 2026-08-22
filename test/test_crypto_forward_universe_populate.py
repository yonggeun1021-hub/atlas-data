#!/usr/bin/env python3
"""P3-04 Crypto source-coverage scheduled population wiring regression."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_forward_universe_populate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "crypto-breadth-capture.yml"
FIXTURE_TEST = ROOT / "test" / "test_crypto_global_universe.py"

SPEC = importlib.util.spec_from_file_location("crypto_forward_universe_populate", SCRIPT)
POPULATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(POPULATE)

FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "crypto_global_universe_fixtures", FIXTURE_TEST
)
FIXTURES = importlib.util.module_from_spec(FIXTURE_SPEC)
assert FIXTURE_SPEC.loader is not None
FIXTURE_SPEC.loader.exec_module(FIXTURES)

CGU = POPULATE.CGU


def full_coverage_fixture(root: Path):
    return FIXTURES.make_fixture(root)


def taxonomy_unknown_fixture(root: Path):
    return FIXTURES.make_fixture(
        root,
        taxonomy={
            "BTC": "eligible_crypto",
            "SOL": "eligible_crypto",
            "USDT": "stablecoin",
        },
    )


class CryptoForwardUniversePopulateTests(unittest.TestCase):
    def test_full_coverage_snapshot_populates_a_p3_04_packet(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy, identity = full_coverage_fixture(Path(raw) / "fx")
            result = POPULATE.populate(
                "2026-08-20",
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
                identity_path=identity,
            )
            self.assertEqual(result["outcome"], "populated")
            record = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], POPULATE.RECORD_SCHEMA_VERSION)
            self.assertEqual(record["source_date"], "2026-08-20")
            packet = record["packet"]
            self.assertEqual(packet["selected_count"], 3)
            self.assertEqual(packet["target_count"], 3)
            self.assertEqual(
                record["raw_bundle"]["manifest_sha256"],
                packet["snapshot_lineage"]["manifest_sha256"],
            )
            for field in (
                "investable_universe_authorized", "stage_promotion_authorized",
                "production_authorized", "trading_authorized",
            ):
                self.assertFalse(record["authority"][field])
                self.assertFalse(packet["authority"][field])
            self.assertTrue(record["authority"]["source_coverage_population_only"])

    def test_verify_skips_rewrite_and_repair_needs_no_network(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy, identity = full_coverage_fixture(Path(raw) / "fx")
            data_root = Path(data)
            first = POPULATE.populate(
                "2026-08-20", raw_root=snapshot.parent, data_root=data_root,
                universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
            )
            self.assertEqual(first["outcome"], "populated")
            mtime_before = Path(first["path"]).stat().st_mtime_ns
            second = POPULATE.populate(
                "2026-08-20", raw_root=snapshot.parent, data_root=data_root,
                universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
            )
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(second["payload_sha256"], first["payload_sha256"])
            self.assertEqual(Path(second["path"]).stat().st_mtime_ns, mtime_before)

    def test_taxonomy_unknown_is_blocked_not_promoted_and_writes_no_packet(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy, identity = taxonomy_unknown_fixture(Path(raw) / "fx")
            data_root = Path(data)
            result = POPULATE.populate(
                "2026-08-20", raw_root=snapshot.parent, data_root=data_root,
                universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
            )
            self.assertEqual(result["outcome"], "blocked")
            self.assertIn("TAXONOMY_COVERAGE_UNKNOWN", result["reason"])
            self.assertIsNone(result["path"])
            self.assertFalse(POPULATE.output_path("2026-08-20", data_root).exists())
            self.assertEqual(list(data_root.iterdir()), [])

    def test_full_coverage_required_is_blocked_not_a_partial_universe(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy, identity = FIXTURES.make_fixture(
                Path(raw) / "fx", coverage_bps=6000, omit_latest=["ETH"]
            )
            result = POPULATE.populate(
                "2026-08-20", raw_root=snapshot.parent, data_root=Path(data),
                universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
            )
            self.assertEqual(result["outcome"], "blocked")
            self.assertIn("FULL_COVERAGE_REQUIRED", result["reason"])
            self.assertIsNone(result["path"])

    def test_missing_raw_bundle_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            with self.assertRaisesRegex(POPULATE.PopulationError, "RAW_BUNDLE_MISSING"):
                POPULATE.populate("2026-08-20", raw_root=Path(raw), data_root=Path(data))

    def test_manifest_tamper_is_a_genuine_error_not_a_blocked_outcome(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy, identity = full_coverage_fixture(Path(raw) / "fx")
            manifest = json.loads((snapshot / "_manifest.json").read_text())
            manifest["catalog_counts"]["assets"] += 1
            (snapshot / "_manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaises(CGU.CryptoUniverseError):
                POPULATE.populate(
                    "2026-08-20", raw_root=snapshot.parent, data_root=Path(data),
                    universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
                )
            self.assertFalse(POPULATE.output_path("2026-08-20", Path(data)).exists())

    def test_persisted_packet_self_rehash_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy, identity = full_coverage_fixture(Path(raw) / "fx")
            data_root = Path(data)
            result = POPULATE.populate(
                "2026-08-20", raw_root=snapshot.parent, data_root=data_root,
                universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
            )
            target = Path(result["path"])
            record = json.loads(target.read_text(encoding="utf-8"))
            record["packet"]["selected_count"] = 1
            record["payload_sha256"] = POPULATE.payload_sha256(
                {key: value for key, value in record.items() if key != "payload_sha256"}
            )
            target.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                POPULATE.PopulationError, "EXISTING_PACKET_DRIFT_OR_TAMPER"
            ):
                POPULATE.populate(
                    "2026-08-20", raw_root=snapshot.parent, data_root=data_root,
                    universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
                )

    def test_same_snapshot_builds_byte_identical_packets_twice(self):
        with tempfile.TemporaryDirectory() as raw:
            snapshot, policy, taxonomy, identity = full_coverage_fixture(Path(raw) / "fx")
            with tempfile.TemporaryDirectory() as data_a, tempfile.TemporaryDirectory() as data_b:
                first = POPULATE.populate(
                    "2026-08-20", raw_root=snapshot.parent, data_root=Path(data_a),
                    universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
                )
                second = POPULATE.populate(
                    "2026-08-20", raw_root=snapshot.parent, data_root=Path(data_b),
                    universe_policy_path=policy, taxonomy_path=taxonomy, identity_path=identity,
                )
                self.assertEqual(first["payload_sha256"], second["payload_sha256"])
                self.assertEqual(
                    Path(first["path"]).read_bytes(), Path(second["path"]).read_bytes()
                )

    def test_committed_evidence_archive_is_currently_blocked_by_real_taxonomy_gaps(self):
        # Regression against the real, currently committed evidence archive --
        # confirms the reported real-world BLOCKED state stays a clean,
        # deterministic block (never a fabricated/partial universe, never a
        # crash) rather than asserting it must someday "pass".
        raw_root = ROOT / "evidence" / "crypto" / "breadth" / "raw"
        tracked_dates = sorted(
            path.name for path in raw_root.iterdir() if path.is_dir()
        )
        self.assertTrue(tracked_dates)
        with tempfile.TemporaryDirectory() as data:
            data_root = Path(data)
            for source_date in tracked_dates:
                result = POPULATE.populate(source_date, raw_root=raw_root, data_root=data_root)
                self.assertIn(result["outcome"], {"populated", "verified_existing", "blocked"})
                if result["outcome"] == "blocked":
                    self.assertIsNone(result["path"])
                else:
                    self.assertTrue(Path(result["path"]).exists())

    def test_workflow_reuses_existing_cron_and_wires_population_before_commits(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count('cron: "40 0 * * *"'), 1)
        self.assertNotIn("workflow_run:", text)
        self.assertNotIn("repository_dispatch", text)
        self.assertIn("crypto_forward_universe_populate.py", text)
        self.assertIn("Populate P3-04 Crypto source-coverage packet", text)
        self.assertIn("Commit P3-04 source-coverage population", text)
        leadership_step = text.index("P1-CR-07 transient live replay")
        populate_step = text.index("Populate P3-04 Crypto source-coverage packet")
        telemetry_step = text.index("Record Crypto Breadth scheduler telemetry")
        commit_raw = text.index("Commit immutable raw snapshot and run telemetry")
        commit_population = text.index("Commit P3-04 source-coverage population")
        # Population runs after the existing capture/validation steps and
        # before telemetry, so telemetry can observe its outcome; its own
        # commit is a separate step after the raw commit, so a population
        # failure can never block or precede raw evidence being pushed.
        self.assertLess(leadership_step, populate_step)
        self.assertLess(populate_step, telemetry_step)
        self.assertLess(telemetry_step, commit_raw)
        self.assertLess(commit_raw, commit_population)

    def test_telemetry_records_population_outcome_reason_path_and_sha(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        telemetry_start = text.index("Record Crypto Breadth scheduler telemetry")
        commit_start = text.index("Commit immutable raw snapshot and run telemetry")
        telemetry_block = text[telemetry_start:commit_start]
        for token in (
            "ATLAS_P3_04_STEP_OUTCOME",
            "ATLAS_P3_04_RESULT",
            "ATLAS_P3_04_REASON",
            "ATLAS_P3_04_PATH",
            "ATLAS_P3_04_SHA256",
            "steps.p3_04_population",
        ):
            self.assertIn(token, telemetry_block)

    def test_module_never_imports_a_network_client(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for prohibited in ("requests", "urllib.request", "http.client", "socket"):
            self.assertNotIn(prohibited, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
