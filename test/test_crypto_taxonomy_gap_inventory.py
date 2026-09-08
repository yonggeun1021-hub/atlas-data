#!/usr/bin/env python3
"""P3-04 deterministic taxonomy gap review inventory regression."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


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


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def revision_dir(data_root: Path, source_date: str) -> Path:
    return Path(data_root) / source_date / INVENTORY.TAXONOMY_REVISION_DIRNAME


def ratify_b(taxonomy: Path) -> Path:
    """Advance the taxonomy exactly like PR608 did: ratify the asset that
    was an unknown-before-cutoff in the already published inventory."""
    return FIXTURES.write_taxonomy(
        taxonomy,
        {
            "BTC": "eligible_crypto",
            "A": "eligible_crypto",
            "B": "eligible_crypto",
            "C": "stablecoin",
        },
    )


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

    def test_real_latest_archive_reports_its_actual_cutoff_state_without_authority(self):
        raw_root = ROOT / "evidence" / "crypto" / "breadth" / "raw"
        source_date = sorted(path.name for path in raw_root.iterdir() if path.is_dir())[-1]
        record = INVENTORY.build_inventory(source_date, raw_root=raw_root)
        self.assertEqual(record["status"], "REVIEW_INVENTORY_ONLY")
        context = record["selection_context"]
        unknown = record["review_population"]["taxonomy_unknown_before_cutoff"]
        ranks = [
            row["rank_before_taxonomy"]
            for row in unknown
        ]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(context["unknown_before_cutoff_count"], len(unknown))
        if unknown:
            self.assertEqual(record["source_outcome"], {
                "status": "UNKNOWN",
                "unknown_reason": "TAXONOMY_COVERAGE_UNKNOWN",
            })
            self.assertIsInstance(context["known_eligible_count_so_far"], int)
            self.assertLessEqual(
                context["known_eligible_count_so_far"],
                context["target_asset_count"],
            )
        else:
            # A newly effective ratified taxonomy slice can legitimately let
            # the cutoff-aware scan reach all 100 members.  That natural
            # success must not make the regression suite demand yesterday's
            # gap forever or reinterpret it as investment authority.
            self.assertEqual(record["source_outcome"], {
                "status": "OBSERVED_UNCLASSIFIED",
                "unknown_reason": None,
            })
            self.assertIsNone(context["known_eligible_count_so_far"])
        self.assertEqual(record["authority"]["records_ratified"], 0)
        self.assertFalse(record["authority"]["investability_authorized"])

    def test_populate_retains_pinned_taxonomy_revision_before_publishing(self):
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
            pinned = record["lineage"]["taxonomy_policy_sha256"]
            retained = revision_dir(Path(data), snapshot.name) / f"{pinned}.json"
            self.assertTrue(retained.is_file())
            self.assertEqual(retained.read_bytes(), taxonomy.read_bytes())
            self.assertEqual(file_sha256(retained), pinned)
            # The revision lives inside the source-date directory the capture
            # workflow already commits, so no workflow change is required.
            self.assertEqual(retained.parent.parent, Path(result["path"]).parent)

    def test_repeat_populate_reuses_retained_revision_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            args = dict(
                source_date=snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            INVENTORY.populate(**args)
            revisions = revision_dir(Path(data), snapshot.name)
            before = {path.name: path.read_bytes() for path in revisions.iterdir()}
            second = INVENTORY.populate(**args)
            self.assertEqual(second["outcome"], "verified_existing")
            after = {path.name: path.read_bytes() for path in revisions.iterdir()}
            self.assertEqual(before, after)
            self.assertEqual(len(after), 1)

    def test_published_record_replays_byte_identically_under_pinned_taxonomy(self):
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
            packet = Path(first["path"])
            before = packet.read_bytes()
            pinned = json.loads(before)["lineage"]["taxonomy_policy_sha256"]

            ratify_b(taxonomy)
            current = file_sha256(taxonomy)
            self.assertNotEqual(current, pinned)

            mode = INVENTORY.validate_inventory(
                json.loads(before),
                raw_root=snapshot.parent,
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
                data_root=Path(data),
            )
            self.assertEqual(mode, "pinned_taxonomy_revision")

            second = INVENTORY.populate(**args)
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(packet.read_bytes(), before)
            self.assertEqual(first["payload_sha256"], second["payload_sha256"])
            # Verifying an existing record must not snapshot today's taxonomy.
            self.assertEqual(
                sorted(path.name for path in revision_dir(Path(data), snapshot.name).iterdir()),
                [f"{pinned}.json"],
            )

    def test_replay_keeps_the_record_review_only_with_no_authority(self):
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
            ratify_b(taxonomy)
            INVENTORY.validate_inventory(
                record,
                raw_root=snapshot.parent,
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
                data_root=Path(data),
            )
            self.assertEqual(record["status"], "REVIEW_INVENTORY_ONLY")
            self.assertEqual(record["authority"]["classifications_created"], 0)
            self.assertEqual(record["authority"]["records_ratified"], 0)
            for key, value in record["authority"].items():
                if key not in {"classifications_created", "records_ratified"}:
                    self.assertFalse(value, key)

    def test_real_2026_09_08_record_replays_under_its_pinned_revision(self):
        source_date = "2026-09-08"
        packet = INVENTORY.DATA_ROOT / source_date / "packet.json"
        before = packet.read_bytes()
        record = json.loads(before)
        pinned = record["lineage"]["taxonomy_policy_sha256"]
        current = file_sha256(INVENTORY.CB.EXCLUSION_TAXONOMY_PATH)
        # The real defect: PR608 advanced the current taxonomy after this
        # record was committed against the earlier one.
        self.assertNotEqual(current, pinned)
        mode = INVENTORY.validate_inventory(record, data_root=INVENTORY.DATA_ROOT)
        self.assertEqual(mode, "pinned_taxonomy_revision")
        self.assertEqual(packet.read_bytes(), before)
        self.assertEqual(
            file_sha256(
                INVENTORY.taxonomy_revision_path(
                    source_date, pinned, INVENTORY.DATA_ROOT
                )
            ),
            pinned,
        )
        self.assertEqual(record["authority"]["records_ratified"], 0)
        self.assertFalse(record["authority"]["investability_authorized"])

    def test_missing_pinned_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            args = dict(
                source_date=snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            result = INVENTORY.populate(**args)
            pinned = json.loads(Path(result["path"]).read_text(encoding="utf-8"))[
                "lineage"
            ]["taxonomy_policy_sha256"]
            (revision_dir(Path(data), snapshot.name) / f"{pinned}.json").unlink()
            ratify_b(taxonomy)
            with self.assertRaisesRegex(
                INVENTORY.InventoryError,
                "HISTORICAL_TAXONOMY_REVISION_UNAVAILABLE:missing",
            ):
                INVENTORY.populate(**args)

    def test_wrong_hash_pinned_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            args = dict(
                source_date=snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            result = INVENTORY.populate(**args)
            pinned = json.loads(Path(result["path"]).read_text(encoding="utf-8"))[
                "lineage"
            ]["taxonomy_policy_sha256"]
            revision = revision_dir(Path(data), snapshot.name) / f"{pinned}.json"
            # Valid taxonomy JSON, but not the bytes this record pinned.
            ratify_b(revision)
            ratify_b(taxonomy)
            with self.assertRaisesRegex(
                INVENTORY.InventoryError,
                "HISTORICAL_TAXONOMY_REVISION_UNAVAILABLE:hash_mismatch",
            ):
                INVENTORY.populate(**args)

    def test_malformed_pinned_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as data:
            raw = b"{not json"
            digest = hashlib.sha256(raw).hexdigest()
            path = INVENTORY.taxonomy_revision_path("2026-09-08", digest, Path(data))
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            with self.assertRaisesRegex(
                INVENTORY.InventoryError,
                "HISTORICAL_TAXONOMY_REVISION_UNAVAILABLE:malformed",
            ):
                INVENTORY.resolve_taxonomy_revision("2026-09-08", digest, Path(data))

    def test_symlinked_pinned_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            args = dict(
                source_date=snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            result = INVENTORY.populate(**args)
            pinned = json.loads(Path(result["path"]).read_text(encoding="utf-8"))[
                "lineage"
            ]["taxonomy_policy_sha256"]
            revision = revision_dir(Path(data), snapshot.name) / f"{pinned}.json"
            # Correct bytes, but reached through a link the packet does not
            # commit: the evidence is no longer immutable in place.
            outside = Path(tmp) / "pinned_taxonomy_source.json"
            outside.write_bytes(revision.read_bytes())
            revision.unlink()
            revision.symlink_to(outside)
            ratify_b(taxonomy)
            with self.assertRaisesRegex(
                INVENTORY.InventoryError,
                "HISTORICAL_TAXONOMY_REVISION_UNAVAILABLE:symlink",
            ):
                INVENTORY.populate(**args)

    def test_replay_still_rejects_drift_in_any_other_recorded_input(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            args = dict(
                source_date=snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            INVENTORY.populate(**args)
            # A resolvable pinned taxonomy revision must not excuse a
            # universe policy that no longer matches the stored lineage.
            ratify_b(taxonomy)
            FIXTURES.write_policy(policy, target=4)
            with self.assertRaisesRegex(
                INVENTORY.InventoryError, "INVENTORY_DRIFT_OR_TAMPER"
            ):
                INVENTORY.populate(**args)

    def test_resolvable_revision_does_not_launder_a_tampered_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            args = dict(
                source_date=snapshot.name,
                raw_root=snapshot.parent,
                data_root=Path(data),
                universe_policy_path=policy,
                taxonomy_path=taxonomy,
            )
            result = INVENTORY.populate(**args)
            target = Path(result["path"])
            ratify_b(taxonomy)
            record = json.loads(target.read_text(encoding="utf-8"))
            record["authority"]["records_ratified"] = 1
            record["payload_sha256"] = INVENTORY.payload_sha256(
                {key: value for key, value in record.items() if key != "payload_sha256"}
            )
            target.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                INVENTORY.InventoryError, "INVENTORY_DRIFT_OR_TAMPER"
            ):
                INVENTORY.populate(**args)

    def test_concurrent_identical_revision_preserves_existing_inode(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            target = INVENTORY.taxonomy_revision_path(
                snapshot.name, file_sha256(taxonomy), Path(data)
            )
            real_link = INVENTORY.os.link
            winner_inodes = []

            def another_writer_wins(temp, destination):
                self.assertEqual(Path(destination), target)
                target.write_bytes(taxonomy.read_bytes())
                winner_inodes.append(target.stat().st_ino)
                return real_link(temp, destination)

            with mock.patch.object(INVENTORY.os, "link", side_effect=another_writer_wins):
                result = INVENTORY.populate(
                    snapshot.name, raw_root=snapshot.parent, data_root=Path(data),
                    universe_policy_path=policy, taxonomy_path=taxonomy,
                )
            self.assertEqual(len(winner_inodes), 1)
            self.assertEqual(target.stat().st_ino, winner_inodes[0])
            self.assertEqual(target.read_bytes(), taxonomy.read_bytes())
            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual(list(target.parent.glob(".*.tmp.*")), [])

    def test_concurrent_conflicting_revision_is_not_overwritten_or_published(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            target = INVENTORY.taxonomy_revision_path(
                snapshot.name, file_sha256(taxonomy), Path(data)
            )
            real_link = INVENTORY.os.link
            winner_inodes = []

            def another_writer_wins(temp, destination):
                self.assertEqual(Path(destination), target)
                target.write_bytes(b"{}\n")
                winner_inodes.append(target.stat().st_ino)
                return real_link(temp, destination)

            with mock.patch.object(INVENTORY.os, "link", side_effect=another_writer_wins):
                with self.assertRaisesRegex(
                    INVENTORY.InventoryError, "existing_content_mismatch"
                ):
                    INVENTORY.populate(
                        snapshot.name, raw_root=snapshot.parent, data_root=Path(data),
                        universe_policy_path=policy, taxonomy_path=taxonomy,
                    )
            self.assertEqual(len(winner_inodes), 1)
            self.assertEqual(target.stat().st_ino, winner_inodes[0])
            self.assertEqual(target.read_bytes(), b"{}\n")
            self.assertFalse(INVENTORY.output_path(snapshot.name, Path(data)).exists())
            self.assertEqual(list(target.parent.glob(".*.tmp.*")), [])

    def test_failed_revision_retention_publishes_no_inventory(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            snapshot, policy, taxonomy = gap_fixture(Path(tmp))
            occupied = (
                revision_dir(Path(data), snapshot.name)
                / f"{file_sha256(taxonomy)}.json"
            )
            occupied.parent.mkdir(parents=True)
            occupied.write_bytes(b"{}\n")
            with self.assertRaisesRegex(
                INVENTORY.InventoryError, "TAXONOMY_REVISION_RETENTION_FAILED"
            ):
                INVENTORY.populate(
                    snapshot.name,
                    raw_root=snapshot.parent,
                    data_root=Path(data),
                    universe_policy_path=policy,
                    taxonomy_path=taxonomy,
                )
            self.assertFalse(
                INVENTORY.output_path(snapshot.name, Path(data)).exists()
            )

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
