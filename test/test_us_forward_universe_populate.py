#!/usr/bin/env python3
"""P3-02 US Forward Universe scheduled population wiring regression."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "us_forward_universe_populate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "p1-us04-forward-breadth.yml"
GLOBAL_UNIVERSE_TEST = ROOT / "test" / "test_us_global_universe.py"

SPEC = importlib.util.spec_from_file_location("us_forward_universe_populate", SCRIPT)
POPULATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(POPULATE)

UGU_TEST_SPEC = importlib.util.spec_from_file_location(
    "us_global_universe_fixtures", GLOBAL_UNIVERSE_TEST
)
UGU_TEST = importlib.util.module_from_spec(UGU_TEST_SPEC)
assert UGU_TEST_SPEC.loader is not None
UGU_TEST_SPEC.loader.exec_module(UGU_TEST)

US_BREADTH = POPULATE.US_BREADTH
UGU = POPULATE.UGU
BREADTH_CONTRACT = US_BREADTH.load_contract()
UNIVERSE_CONTRACT = UGU.load_contract()


def _day_token(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{month}{day}{year}"


def write_snapshot(raw_root: Path, iso_date: str, previous_core: dict | None) -> dict:
    """Build one exact-byte, minimum_records-satisfying, fully validated
    P1-US-04 bundle -- manifest and membership diff included -- reusing
    the real production contract and the same 1000-row body generator the
    P3-02 adapter's own regression already uses."""
    target = raw_root / iso_date
    target.mkdir(parents=True)
    day_token = _day_token(iso_date)
    (target / "_downloaded_at.txt").write_text(f"{iso_date}T23:30:00Z\n", encoding="utf-8")
    checksum_lines = []
    for source in BREADTH_CONTRACT["sources"]:
        raw = UGU_TEST.directory_body(source["name"], day=day_token)
        raw_name = source["raw_file"].removesuffix(".gz")
        checksum_lines.append(f"{hashlib.sha256(raw).hexdigest()}  {raw_name}")
        with gzip.GzipFile(target / source["raw_file"], "wb", mtime=0) as stream:
            stream.write(raw)
    (target / "_sha256.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    US_BREADTH.build_manifest(target, "us-breadth-forward-capture/v1", BREADTH_CONTRACT)
    core = US_BREADTH.validate_manifest(target, BREADTH_CONTRACT)
    US_BREADTH.write_json_append_only(
        US_BREADTH.build_membership_diff(core, previous_core, BREADTH_CONTRACT),
        target / "_membership_diff.json",
    )
    US_BREADTH.validate_snapshot_bundle(
        target,
        previous_dir=(raw_root / previous_core["snapshot_date"]) if previous_core else None,
        contract=BREADTH_CONTRACT,
    )
    return core


class UsForwardUniversePopulateTests(unittest.TestCase):
    def test_captured_raw_produces_a_p3_02_packet(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root = Path(raw)
            core = write_snapshot(raw_root, "2026-09-01", None)
            result = POPULATE.populate(
                "2026-09-01", raw_root=raw_root, data_root=Path(data),
                breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
            )
            self.assertEqual(result["outcome"], "populated")
            record = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], POPULATE.RECORD_SCHEMA_VERSION)
            self.assertEqual(record["source_date"], "2026-09-01")
            self.assertEqual(record["generated_at"], core["fetched_at_utc"])
            self.assertEqual(
                record["raw_bundle"]["path"], "evidence/us_breadth/raw/2026-09-01"
            )
            self.assertEqual(
                record["builder"]["contract_version"], UNIVERSE_CONTRACT["contract_version"]
            )
            packet = record["packet"]
            self.assertEqual(packet["status"], "FORWARD_SOURCE_COVERAGE_UNIVERSE_VALIDATED")
            self.assertIn(
                POPULATE.SCHEDULED_POPULATION_BOUNDARY,
                packet["unresolved_boundaries"],
            )
            execution = record["population_execution"]
            self.assertEqual(
                execution["status"], "SCHEDULED_SOURCE_COVERAGE_POPULATED"
            )
            self.assertEqual(
                execution["resolved_packet_boundaries"],
                [POPULATE.SCHEDULED_POPULATION_BOUNDARY],
            )
            self.assertNotIn(
                POPULATE.SCHEDULED_POPULATION_BOUNDARY,
                execution["effective_unresolved_boundaries"],
            )
            self.assertEqual(
                packet["source_counts"], {"nasdaq_listed": 1000, "other_listed": 1000}
            )
            # source coverage only -- never investable/Stage/Production/trading.
            for field in (
                "investable_universe_authorized", "stage_promotion_authorized",
                "production_authorized", "trading_authorized",
            ):
                self.assertFalse(packet["authority"][field])
            self.assertFalse(record["authority"]["investable_universe_authorized"])
            self.assertFalse(record["authority"]["stage_promotion_authorized"])
            self.assertFalse(record["authority"]["production_authorized"])
            self.assertFalse(record["authority"]["trading_authorized"])
            self.assertTrue(record["authority"]["source_coverage_population_only"])

    def test_skipped_existing_raw_and_packet_present_verifies_and_skips(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            write_snapshot(raw_root, "2026-09-01", None)
            first = POPULATE.populate(
                "2026-09-01", raw_root=raw_root, data_root=data_root,
                breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
            )
            self.assertEqual(first["outcome"], "populated")
            mtime_before = Path(first["path"]).stat().st_mtime_ns
            second = POPULATE.populate(
                "2026-09-01", raw_root=raw_root, data_root=data_root,
                breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
            )
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(second["payload_sha256"], first["payload_sha256"])
            self.assertEqual(Path(second["path"]).stat().st_mtime_ns, mtime_before)

    def test_legacy_v1_packet_is_verified_without_rewrite(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            write_snapshot(raw_root, "2026-09-01", None)
            legacy = POPULATE.rebuild(
                "2026-09-01",
                raw_root=raw_root,
                breadth_contract=BREADTH_CONTRACT,
                universe_contract=UNIVERSE_CONTRACT,
                record_schema_version=POPULATE.LEGACY_RECORD_SCHEMA_VERSION,
            )
            target = POPULATE.output_path("2026-09-01", data_root)
            US_BREADTH.write_json_append_only(legacy, target)
            before = target.read_bytes()

            result = POPULATE.populate(
                "2026-09-01",
                raw_root=raw_root,
                data_root=data_root,
                breadth_contract=BREADTH_CONTRACT,
                universe_contract=UNIVERSE_CONTRACT,
            )

            self.assertEqual(result["outcome"], "verified_existing")
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(
                json.loads(before)["schema_version"],
                POPULATE.LEGACY_RECORD_SCHEMA_VERSION,
            )

    def test_existing_packet_with_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            write_snapshot(raw_root, "2026-09-01", None)
            target = POPULATE.output_path("2026-09-01", data_root)
            target.parent.mkdir(parents=True)
            target.write_text(
                json.dumps({"schema_version": "us_forward_universe_population/999"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                POPULATE.PopulationError, "EXISTING_PACKET_SCHEMA_UNSUPPORTED"
            ):
                POPULATE.populate(
                    "2026-09-01",
                    raw_root=raw_root,
                    data_root=data_root,
                    breadth_contract=BREADTH_CONTRACT,
                    universe_contract=UNIVERSE_CONTRACT,
                )

    def test_skipped_existing_raw_missing_packet_repairs_without_network(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            write_snapshot(raw_root, "2026-09-01", None)
            self.assertFalse(POPULATE.output_path("2026-09-01", data_root).exists())
            result = POPULATE.populate(
                "2026-09-01", raw_root=raw_root, data_root=data_root,
                breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
            )
            self.assertEqual(result["outcome"], "populated")
            self.assertTrue(POPULATE.output_path("2026-09-01", data_root).exists())

    def test_missing_raw_bundle_fails_closed_without_a_packet(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            with self.assertRaisesRegex(
                POPULATE.PopulationError, "RAW_BUNDLE_MISSING"
            ):
                POPULATE.populate(
                    "2026-09-01", raw_root=Path(raw), data_root=Path(data),
                    breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
                )
            self.assertFalse(POPULATE.output_path("2026-09-01", Path(data)).exists())

    def test_partial_raw_bundle_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root = Path(raw)
            write_snapshot(raw_root, "2026-09-01", None)
            (raw_root / "2026-09-01" / "_membership_diff.json").unlink()
            with self.assertRaises(US_BREADTH.ContractError):
                POPULATE.populate(
                    "2026-09-01", raw_root=raw_root, data_root=Path(data),
                    breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
                )
            self.assertFalse(POPULATE.output_path("2026-09-01", Path(data)).exists())

    def test_tampered_raw_source_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root = Path(raw)
            write_snapshot(raw_root, "2026-09-01", None)
            with gzip.GzipFile(
                raw_root / "2026-09-01" / "nasdaqlisted.txt.gz", "wb", mtime=0
            ) as stream:
                stream.write(b"tampered")
            with self.assertRaises(US_BREADTH.ContractError):
                POPULATE.populate(
                    "2026-09-01", raw_root=raw_root, data_root=Path(data),
                    breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
                )
            self.assertFalse(POPULATE.output_path("2026-09-01", Path(data)).exists())

    def test_persisted_packet_self_rehash_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            write_snapshot(raw_root, "2026-09-01", None)
            result = POPULATE.populate(
                "2026-09-01", raw_root=raw_root, data_root=data_root,
                breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
            )
            target = Path(result["path"])
            record = json.loads(target.read_text(encoding="utf-8"))
            record["packet"]["total_count"] = 1
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
                    "2026-09-01", raw_root=raw_root, data_root=data_root,
                    breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
                )

    def test_same_raw_bundle_builds_byte_identical_packets_twice(self):
        with tempfile.TemporaryDirectory() as raw:
            raw_root = Path(raw)
            write_snapshot(raw_root, "2026-09-01", None)
            with tempfile.TemporaryDirectory() as data_a, tempfile.TemporaryDirectory() as data_b:
                first = POPULATE.populate(
                    "2026-09-01", raw_root=raw_root, data_root=Path(data_a),
                    breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
                )
                second = POPULATE.populate(
                    "2026-09-01", raw_root=raw_root, data_root=Path(data_b),
                    breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
                )
                self.assertEqual(first["payload_sha256"], second["payload_sha256"])
                self.assertEqual(
                    Path(first["path"]).read_bytes(), Path(second["path"]).read_bytes()
                )

    def test_downstream_bundle_extends_true_predecessor_chain(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            first_core = write_snapshot(raw_root, "2026-09-01", None)
            write_snapshot(raw_root, "2026-09-02", first_core)
            first = POPULATE.populate(
                "2026-09-01", raw_root=raw_root, data_root=data_root,
                breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
            )
            second = POPULATE.populate(
                "2026-09-02", raw_root=raw_root, data_root=data_root,
                breadth_contract=BREADTH_CONTRACT, universe_contract=UNIVERSE_CONTRACT,
            )
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "populated")
            self.assertNotEqual(first["payload_sha256"], second["payload_sha256"])

    def test_committed_evidence_archive_populates_cleanly(self):
        # Regression against the real, currently committed evidence archive
        # -- not a synthetic fixture -- so this fails the moment the real
        # production adapter/bundle stops agreeing with each other.
        raw_root = ROOT / "evidence" / "us_breadth" / "raw"
        tracked_dates = sorted(
            path.name for path in raw_root.iterdir()
            if path.is_dir() and US_BREADTH.DATE_DIR.fullmatch(path.name)
        )
        self.assertTrue(tracked_dates)
        with tempfile.TemporaryDirectory() as data:
            data_root = Path(data)
            for source_date in tracked_dates:
                result = POPULATE.populate(source_date, raw_root=raw_root, data_root=data_root)
                self.assertIn(result["outcome"], {"populated", "verified_existing"})
                record = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
                self.assertEqual(record["packet"]["as_of_date"], source_date)

    def test_committed_first_scheduled_population_v1_verifies_without_rewrite(self):
        source_date = "2026-08-24"
        target = POPULATE.output_path(source_date)
        self.assertTrue(target.exists())
        before = target.read_bytes()
        result = POPULATE.populate(source_date)
        self.assertEqual(result["outcome"], "verified_existing")
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(
            json.loads(before)["schema_version"],
            POPULATE.LEGACY_RECORD_SCHEMA_VERSION,
        )

    def test_workflow_reuses_existing_cron_and_wires_population_after_raw_commit(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count('cron: "20 1 * * 2-6"'), 1)
        self.assertNotIn("workflow_run:", text)
        self.assertNotIn("repository_dispatch", text)
        self.assertIn("us_forward_universe_populate.py", text)
        self.assertIn(
            "Populate P3-02 US Forward Universe source-coverage packet", text
        )
        commit_raw = text.index("Commit immutable capture")
        populate_step = text.index(
            "Populate P3-02 US Forward Universe source-coverage packet"
        )
        commit_population = text.index("Commit P3-02 source-coverage population")
        self.assertLess(commit_raw, populate_step)
        self.assertLess(populate_step, commit_population)

    def test_module_never_imports_a_network_client(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for prohibited in ("requests", "urllib.request", "http.client", "socket"):
            self.assertNotIn(prohibited, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
