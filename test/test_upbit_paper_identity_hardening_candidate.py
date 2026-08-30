"""P3-12 exact-hash PAPER identity candidate regression."""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BUILD = load_module("upbit_paper_identity_hardening_candidate_test", "identity/upbit_paper_identity_hardening_candidate.py")
FP = load_module("upbit_first_party_identity_capture_test_for_candidate", ".github/scripts/upbit_first_party_identity_capture.py")
CLI = load_module("upbit_paper_identity_hardening_candidate_cli_test", ".github/scripts/upbit_paper_identity_hardening_candidate_build.py")


MARKET_SNAPSHOT = ROOT / "evidence" / "crypto" / "upbit" / "raw" / "2026-08-29"
NOW = dt.datetime(2026, 8, 30, 9, 30, 0, tzinfo=dt.timezone.utc)


def fixed_clock():
    return NOW


def fake_fetcher(contract):
    by_url = {row["url"]: row for row in contract["assets"]}

    def fetch(url, timeout_seconds, max_response_bytes):
        del timeout_seconds, max_response_bytes
        row = by_url[url]
        raw = ("identity " + " ".join(row["required_markers"])).encode()
        return FP.FetchResult(raw, url, 200, "text/html")

    return fetch


def first_party_snapshot(root: Path, capture_id="20260830T093000Z") -> Path:
    contract = FP.load_contract()
    return FP.capture_snapshot(
        root, capture_id=capture_id, fetcher=fake_fetcher(contract), clock=fixed_clock,
    )


class UpbitPaperIdentityHardeningCandidateTests(unittest.TestCase):
    def test_builds_exact_eight_candidate_with_all_authority_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_party = first_party_snapshot(Path(tmp) / "fp")
            packet = BUILD.build_candidate(
                first_party_snapshot_dir=first_party,
                market_snapshot_dir=MARKET_SNAPSHOT,
            )
            self.assertEqual(len(packet["proposed_registry"]["mappings"]), 8)
            self.assertEqual(len(packet["proposed_taxonomy"]["records"]), 8)
            self.assertEqual(len(packet["evidence"]), 8)
            self.assertEqual(len(packet["registry_candidates"]), 8)
            self.assertEqual(packet["hold_list"], [])
            self.assertEqual(packet["snapshot_date"], packet["evaluation_as_of"])
            self.assertFalse(packet["release_ready"])
            self.assertFalse(packet["exact_hash_cio_approval_present"])
            self.assertTrue(all(value is False for value in packet["authority"].values()))
            self.assertEqual(
                BUILD.CONSUMER_PATH,
                ROOT / "universe" / "upbit_tradeable_universe.py",
            )
            self.assertEqual(
                packet["consumer_file_sha256"],
                BUILD.file_sha256(ROOT / "universe" / "upbit_tradeable_universe.py"),
            )
            self.assertEqual(
                packet["candidate_builder_file_sha256"],
                BUILD.file_sha256(ROOT / "identity" / "upbit_paper_identity_hardening_candidate.py"),
            )
            self.assertEqual(packet["payload_sha256"], BUILD.payload_sha256({
                key: value for key, value in packet.items() if key != "payload_sha256"
            }))
            BUILD.validate_candidate(packet)

    def test_upbit_name_mismatch_fails_closed_even_with_rehashed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_party = first_party_snapshot(root / "fp")
            market_copy = root / "market"
            shutil.copytree(MARKET_SNAPSHOT, market_copy)
            manifest_path = market_copy / "_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            raw_file = "upbit_market_all.json.gz"
            with gzip.open(market_copy / raw_file, "rb") as handle:
                rows = json.load(handle)
            next(row for row in rows if row["market"] == "KRW-BTC")["english_name"] = "Forged Bitcoin"
            raw = json.dumps(rows, ensure_ascii=True).encode()
            with (market_copy / raw_file).open("wb") as output:
                with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
                    stream.write(raw)
            manifest["checksums"][raw_file] = hashlib.sha256(raw).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(BUILD.HardeningError, "UPBIT_MARKET_NAME_MISMATCH"):
                BUILD.build_candidate(
                    first_party_snapshot_dir=first_party,
                    market_snapshot_dir=market_copy,
                )

    def test_nested_candidate_tamper_is_rejected_after_self_rehash(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_party = first_party_snapshot(Path(tmp) / "fp")
            packet = BUILD.build_candidate(
                first_party_snapshot_dir=first_party,
                market_snapshot_dir=MARKET_SNAPSHOT,
            )
            packet["proposed_registry"]["mappings"]["KRW-BTC"] = "FORGED"
            packet["payload_sha256"] = BUILD.payload_sha256({
                key: value for key, value in packet.items() if key != "payload_sha256"
            })
            with self.assertRaisesRegex(BUILD.HardeningError, "REGISTRY_HASH_MISMATCH"):
                BUILD.validate_candidate(packet)

    def test_classifier_compatibility_projection_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_party = first_party_snapshot(Path(tmp) / "fp")
            packet = BUILD.build_candidate(
                first_party_snapshot_dir=first_party,
                market_snapshot_dir=MARKET_SNAPSHOT,
            )
            packet["registry_candidates"][0]["canonical_asset_id"] = "FORGED"
            packet["payload_sha256"] = BUILD.payload_sha256({
                key: value for key, value in packet.items() if key != "payload_sha256"
            })
            with self.assertRaisesRegex(BUILD.HardeningError, "REGISTRY_COMPATIBILITY_PROJECTION_MISMATCH"):
                BUILD.validate_candidate(packet)

    def test_consumer_hash_tamper_is_rejected_after_self_rehash(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_party = first_party_snapshot(Path(tmp) / "fp")
            packet = BUILD.build_candidate(
                first_party_snapshot_dir=first_party,
                market_snapshot_dir=MARKET_SNAPSHOT,
            )
            packet["consumer_file_sha256"] = "0" * 64
            packet["payload_sha256"] = BUILD.payload_sha256({
                key: value for key, value in packet.items() if key != "payload_sha256"
            })
            with self.assertRaisesRegex(BUILD.HardeningError, "CONSUMER_FILE_HASH_MISMATCH"):
                BUILD.validate_candidate(packet)

    def test_candidate_builder_hash_tamper_is_rejected_after_self_rehash(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_party = first_party_snapshot(Path(tmp) / "fp")
            packet = BUILD.build_candidate(
                first_party_snapshot_dir=first_party,
                market_snapshot_dir=MARKET_SNAPSHOT,
            )
            packet["candidate_builder_file_sha256"] = "0" * 64
            packet["payload_sha256"] = BUILD.payload_sha256({
                key: value for key, value in packet.items() if key != "payload_sha256"
            })
            with self.assertRaisesRegex(BUILD.HardeningError, "CANDIDATE_BUILDER_FILE_HASH_MISMATCH"):
                BUILD.validate_candidate(packet)

    def test_latest_selection_uses_internal_time_not_directory_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fp"
            later = first_party_snapshot(root, capture_id="20260830T093000Z")
            earlier = root / "2026-08-30" / "zzzz-lexically-last"
            shutil.copytree(later, earlier)
            manifest_path = earlier / "_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["capture_id"] = "20260830T083000Z"
            manifest["observed_at"] = "2026-08-30T08:30:00Z"
            manifest["available_at"] = "2026-08-30T08:30:00Z"
            for row in manifest["sources"]:
                row["observed_at"] = "2026-08-30T08:30:00Z"
                row["available_at"] = "2026-08-30T08:30:00Z"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            selected, selected_manifest = CLI.find_latest_first_party(root)
            self.assertEqual(selected, later)
            self.assertEqual(selected_manifest["capture_id"], "20260830T093000Z")

    def test_contract_scope_expansion_and_collision_fail_closed(self):
        base = json.loads(BUILD.CONTRACT_PATH.read_text())
        for mutate, expected in (
            (lambda value: value["assets"].pop(), "CONTRACT_FREEZE_SCOPE_MISMATCH"),
            (lambda value: value["assets"][1].__setitem__("canonical_asset_id", "BTC"), "CONTRACT_IDENTITY_COLLISION"),
        ):
            contract = copy.deepcopy(base)
            mutate(contract)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "contract.json"
                path.write_text(json.dumps(contract), encoding="utf-8")
                with self.assertRaisesRegex(BUILD.HardeningError, expected):
                    BUILD.load_contract(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
