#!/usr/bin/env python3
"""P2-04 real-source pair population and workflow wiring regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_rotation_source_pair_populate.py"
LEADERSHIP_TEST = ROOT / "test" / "test_crypto_leadership.py"
ROTATION_PATH = ROOT / "rotation" / "crypto_rotation.py"
WORKFLOW = ROOT / ".github" / "workflows" / "crypto-breadth-capture.yml"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


POPULATE = load_module("crypto_rotation_source_pair_populate_test", SCRIPT)
FIXTURES = load_module("crypto_leadership_source_pair_fixtures", LEADERSHIP_TEST)
ROTATION = load_module("crypto_rotation_source_pair_consumer", ROTATION_PATH)


def source_paths(tmp: Path) -> dict:
    return FIXTURES.inputs(tmp)


def build_ready(tmp: Path) -> tuple[Path, dict, dict]:
    raw = FIXTURES.write_window(
        tmp / "raw", days=8, end_as_of="2026-08-19"
    )
    paths = source_paths(tmp)
    built = POPULATE.build_source_pair(
        raw_root=raw, as_of_date="2026-08-19", **paths
    )
    return raw, paths, built


def external_ratified_policy(rotation_input: dict) -> dict:
    policies = rotation_input["current_observation"]["policies"]
    return {
        "schema_version": "crypto_rotation_policy/1",
        "policy_id": "CRYPTO_ROTATION.TEST.EXTERNAL.V1",
        "approval_status": "RATIFIED",
        "ratified_by": "test-cio",
        "ratified_at_utc": "2026-01-01T00:00:00Z",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "window_id": "pilot_7d",
        "bucket_ids": ["ALT", "BTC", "ETH"],
        "universe_policy_sha256": policies["universe"]["policy_sha256"],
        "leadership_policy_sha256": policies["leadership"]["policy_sha256"],
        "taxonomy_policy_sha256": policies["taxonomy"]["policy_sha256"],
        "ranking_metric": "BUCKET_RELATIVE_STRENGTH_VS_BTC",
        "ranking_order": "DESCENDING",
        "tie_break": "BUCKET_ID_ASC",
        "top_count": 1,
        "bottom_count": 1,
        "maximum_calendar_gap_days": 3,
    }


class SourcePairPopulationTests(unittest.TestCase):
    def test_two_adjacent_real_leadership_windows_build_exact_rotation_input(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            raw, paths, built = build_ready(Path(tmp_name))
            self.assertEqual(built["status"], "ready")
            packet = built["packet"]
            self.assertEqual(packet["status"], "SOURCE_PAIR_READY_ROTATION_POLICY_ABSENT")
            self.assertEqual(packet["window_id"], "pilot_7d")
            rotation_input = packet["rotation_input"]
            self.assertEqual(rotation_input["schema_version"], "crypto_rotation_input/1")
            self.assertEqual(rotation_input["prior_observation"]["as_of_date"], "2026-08-18")
            self.assertEqual(rotation_input["current_observation"]["as_of_date"], "2026-08-19")
            self.assertEqual(rotation_input["as_of_date"], "2026-08-19")
            self.assertEqual(
                POPULATE.validate_source_pair(packet, raw_root=raw, **paths), packet
            )

    def test_pair_is_consumable_by_existing_rotation_engine_only_with_external_policy(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            _, _, built = build_ready(Path(tmp_name))
            packet = built["packet"]
            result = ROTATION.build_packet(
                packet["rotation_input"],
                external_ratified_policy(packet["rotation_input"]),
            )
            self.assertEqual(result["status"], "ROTATION_BUCKETS_OBSERVED")
            self.assertTrue(result["rotation_policy_effective"])
            self.assertTrue(result["authority"]["bucket_ranking_authorized"])
            self.assertFalse(result["authority"]["production_authorized"])
            self.assertFalse(result["authority"]["trading_authorized"])

    def test_real_producer_precision_tamper_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            _, _, built = build_ready(Path(tmp_name))
            rotation_input = copy.deepcopy(built["packet"]["rotation_input"])
            window = next(
                item for item in rotation_input["current_observation"]["windows"]
                if item["window_id"] == "pilot_7d"
            )
            row = next(
                item for item in window["group_relative_strength"]["bucket"]
                if item["group_id"] == "ALT"
            )
            original = row["relative_strength_vs_btc"]
            row["relative_strength_vs_btc"] = (
                original[:-1] + ("1" if original[-1] != "1" else "2")
            )
            with self.assertRaisesRegex(
                ROTATION.CryptoRotationError,
                "UPSTREAM_BUCKET_RS_INCONSISTENT:current:ALT",
            ):
                ROTATION.build_packet(
                    rotation_input,
                    external_ratified_policy(built["packet"]["rotation_input"]),
                )

    def test_population_never_invokes_rotation_or_creates_policy(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            _, _, built = build_ready(Path(tmp_name))
            packet = built["packet"]
            self.assertFalse(packet["policy_boundary"]["rotation_engine_invoked"])
            self.assertFalse(packet["policy_boundary"]["rotation_policy_authorized"])
            self.assertEqual(
                packet["policy_boundary"]["repository_default_rotation_policy"],
                "ABSENT",
            )
            self.assertFalse(any(
                value for key, value in packet["authority"].items()
                if key != "source_pair_population_only"
            ))
            self.assertTrue(packet["authority"]["source_pair_population_only"])

    def test_insufficient_history_is_blocked_without_packet_or_policy_guess(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            raw = FIXTURES.write_window(
                tmp / "raw", days=5, end_as_of="2026-08-19"
            )
            data_root = tmp / "data"
            result = POPULATE.populate(
                raw_root=raw,
                data_root=data_root,
                as_of_date="2026-08-19",
                **source_paths(tmp),
            )
            self.assertEqual(result["outcome"], "blocked")
            self.assertIn("INSUFFICIENT_CONTIGUOUS_HISTORY", result["reason"])
            self.assertIsNone(result["path"])
            self.assertFalse(data_root.exists())

    def test_append_only_content_addressed_idempotency_and_drift_guard(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            raw, paths, built = build_ready(tmp)
            data_root = tmp / "out"
            first = POPULATE.populate(
                raw_root=raw, data_root=data_root,
                as_of_date="2026-08-19", **paths,
            )
            second = POPULATE.populate(
                raw_root=raw, data_root=data_root,
                as_of_date="2026-08-19", **paths,
            )
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(first["payload_sha256"], second["payload_sha256"])
            target = Path(first["path"])
            self.assertRegex(target.name, r"^pair-[0-9a-f]{16}\.json$")
            target.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                POPULATE.SourcePairPopulationError,
                "CONTENT_ADDRESSED_PACKET_DRIFT",
            ):
                POPULATE.populate(
                    raw_root=raw, data_root=data_root,
                    as_of_date="2026-08-19", **paths,
                )

    def test_self_rehashed_authority_tamper_is_rebuilt_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            raw, paths, built = build_ready(Path(tmp_name))
            packet = copy.deepcopy(built["packet"])
            packet["authority"]["trading_authorized"] = True
            packet["payload_sha256"] = POPULATE.payload_sha256(
                {key: value for key, value in packet.items() if key != "payload_sha256"}
            )
            with self.assertRaisesRegex(
                POPULATE.SourcePairPopulationError, "SOURCE_PAIR_REBUILD_MISMATCH"
            ):
                POPULATE.validate_source_pair(packet, raw_root=raw, **paths)

    def test_current_committed_archive_is_honestly_ready_or_blocked(self):
        result = POPULATE.build_source_pair(raw_root=POPULATE.RAW_ROOT)
        self.assertIn(result["status"], {"ready", "blocked"})
        if result["status"] == "blocked":
            self.assertIn("INSUFFICIENT_CONTIGUOUS_HISTORY", result["reason"])
        else:
            self.assertEqual(
                result["packet"]["status"],
                "SOURCE_PAIR_READY_ROTATION_POLICY_ABSENT",
            )

    def test_module_has_no_network_client_or_embedded_rotation_policy(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "requests", "urllib.request", "http.client", "socket",
            "approval_status\": \"RATIFIED", "top_count\":", "bottom_count\":",
        ):
            self.assertNotIn(forbidden, text)


class WorkflowWiringTests(unittest.TestCase):
    def test_existing_crypto_cron_is_reused_and_pair_runs_after_leadership(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count('cron: "40 0 * * *"'), 1)
        self.assertIn("Populate P2-04 Crypto Rotation source pair", text)
        self.assertIn("crypto_rotation_source_pair_populate.py", text)
        self.assertLess(
            text.index("P1-CR-07 transient live replay"),
            text.index("Populate P2-04 Crypto Rotation source pair"),
        )
        self.assertNotIn("repository_dispatch", text)

    def test_pair_commit_is_after_raw_commit_and_isolated_from_p3_04(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        raw_commit = text.index("Commit immutable raw snapshot and run telemetry")
        pair_commit = text.index("Commit P2-04 Crypto Rotation source pair")
        universe_commit = text.index("Commit P3-04 source-coverage population")
        self.assertLess(raw_commit, pair_commit)
        self.assertLess(raw_commit, universe_commit)
        pair_block = text[pair_commit:universe_commit]
        self.assertIn("data/observations/crypto_rotation_source_pair", pair_block)
        self.assertNotIn("data/observations/crypto_global_universe", pair_block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
