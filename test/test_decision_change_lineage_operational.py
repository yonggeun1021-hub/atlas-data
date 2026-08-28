#!/usr/bin/env python3
"""P10-04 committed Daily Decision lineage operational wiring regressions."""
import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "decision_change_lineage_operational.py"


def load_module():
    spec = importlib.util.spec_from_file_location("decision_change_lineage_operational", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module()
REAL_VALIDATE_DAILY_AT_COMMIT = MODULE._validate_daily_at_commit
UNIFIED_FIXTURE_PATH = ROOT / "test" / "test_unified_decision_contract.py"
spec = importlib.util.spec_from_file_location("p10_04_unified_fixture", UNIFIED_FIXTURE_PATH)
UNIFIED_FIXTURE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(UNIFIED_FIXTURE)
PACKETS = sorted((ROOT / "evidence" / "daily_briefing").rglob("packet.json"))
PACKET = PACKETS[-1]
HISTORICAL_RECORDS = sorted(
    (ROOT / "evidence" / "operational" / "decision_change_lineage" / "records")
    .glob("record-*.json")
)


def commit_for(path: Path) -> str:
    relative = path.relative_to(ROOT).as_posix()
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", relative], cwd=ROOT, text=True
    ).strip()


SOURCE_COMMIT = commit_for(PACKET)


def current_unified():
    return UNIFIED_FIXTURE.MODULE.build_packet(
        UNIFIED_FIXTURE.components(),
        UNIFIED_FIXTURE.reasons(),
        "2026-08-27",
        "morning",
        "2026-08-27T00:00:00Z",
        UNIFIED_FIXTURE.CONTRACT,
    )


def synthetic_daily():
    return {
        "decision_date": "2026-08-27",
        "slot": "morning",
        "components": [{
            "component_id": "UNIFIED_DECISION",
            "validated": True,
            "packet": current_unified(),
        }],
    }


def after_generated(seconds: int = 1) -> str:
    generated = dt.datetime.strptime(
        current_unified()["generated_at"], "%Y-%m-%dT%H:%M:%SZ"
    )
    return (generated + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class OperationalDecisionLineageTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            MODULE, "_validate_daily_at_commit", return_value=synthetic_daily()
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, previous=None, path=PACKET, commit=SOURCE_COMMIT, extra_seconds=1):
        return MODULE.build_record(
            path, commit, after_generated(extra_seconds), previous
        )

    def test_exact_source_commit_validator_accepts_its_historical_packet(self):
        relative = PACKET.relative_to(ROOT).as_posix()
        blob_sha = MODULE.hashlib.sha256(PACKET.read_bytes()).hexdigest()
        value = REAL_VALIDATE_DAILY_AT_COMMIT(SOURCE_COMMIT, relative, blob_sha)
        self.assertEqual(value["packet_sha256"], json.loads(PACKET.read_text())["packet_sha256"])

    def test_committed_history_revalidates_at_each_snapshot_source_commit(self):
        self.assertTrue(HISTORICAL_RECORDS)
        record = json.loads(HISTORICAL_RECORDS[-1].read_text(encoding="utf-8"))
        with mock.patch.object(
            MODULE,
            "_validate_daily_at_commit",
            side_effect=REAL_VALIDATE_DAILY_AT_COMMIT,
        ):
            validated = MODULE.validate_record(record)
        self.assertEqual(validated["record_sha256"], record["record_sha256"])

    def test_snapshot_source_ref_is_exact_repo_commit_path_only(self):
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError,
            "SNAPSHOT_SOURCE_REF_INVALID",
        ):
            MODULE._validate_snapshot_at_source(
                current_unified(),
                "https://example.invalid/evidence/daily_briefing/packet.json",
                "test:current",
            )

    def test_exact_source_checkout_retains_git_provenance_and_is_clean(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "repo"
            MODULE._materialize_exact_commit(SOURCE_COMMIT, checkout)
            self.assertTrue((checkout / ".git").is_dir())
            self.assertEqual(
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
                ).strip(),
                SOURCE_COMMIT,
            )
            identity_path = "config/canonical_security_identity.json"
            self.assertEqual(
                subprocess.check_output(
                    ["git", "log", "-1", "--format=%H", "--", identity_path],
                    cwd=checkout,
                    text=True,
                ).strip(),
                subprocess.check_output(
                    ["git", "log", "-1", "--format=%H", "--", identity_path],
                    cwd=ROOT,
                    text=True,
                ).strip(),
            )
            self.assertEqual(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=checkout, text=True
                ).strip(),
                "",
            )

    def test_real_committed_briefing_builds_created_zero_authority_record(self):
        record = self.record()
        self.assertEqual(record["lineage_packet"]["entries"][0]["change_type"], "CREATED")
        self.assertEqual(record["lineage_packet"]["summary"]["decisions_created"], 0)
        self.assertEqual(record["lineage_packet"]["summary"]["decisions_changed"], 0)
        self.assertTrue(record["authority"]["lineage_recording_only"])
        for key, value in record["authority"].items():
            if key != "lineage_recording_only":
                self.assertFalse(value, key)

    def test_exact_full_sha_and_exact_committed_bytes_are_required(self):
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError, "SOURCE_COMMIT_MUST_BE_FULL_SHA"
        ):
            MODULE.build_record(PACKET, "HEAD", after_generated())
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary) / "packet.json"
            outside.write_bytes(PACKET.read_bytes())
            with self.assertRaisesRegex(
                MODULE.OperationalDecisionLineageError, "SOURCE_PATH_OUTSIDE_REPOSITORY"
            ):
                MODULE.build_record(outside, SOURCE_COMMIT, after_generated())

    def test_record_validator_rechecks_git_blob_and_embedded_decision(self):
        record = self.record()
        tampered = copy.deepcopy(record)
        tampered["source_blob_sha256"] = "0" * 64
        tampered["record_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "record_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError, "SOURCE_BLOB_SHA256_MISMATCH"
        ):
            MODULE.validate_record(tampered)

    def test_authority_tamper_with_valid_new_record_hash_is_rejected(self):
        record = self.record()
        record["authority"]["trading_authorized"] = True
        record["record_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in record.items() if key != "record_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError, "RECORD_AUTHORITY_INVALID"
        ):
            MODULE.validate_record(record)

    def test_future_unified_decision_cannot_be_observed_early(self):
        generated = dt.datetime.strptime(
            current_unified()["generated_at"], "%Y-%m-%dT%H:%M:%SZ"
        )
        before = (generated - dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError, "UNIFIED_DECISION_FROM_FUTURE"
        ):
            MODULE.build_record(PACKET, SOURCE_COMMIT, before)

    def test_same_packet_forward_observation_is_unchanged_without_evidence(self):
        first = self.record(extra_seconds=1)
        second = self.record(previous=first, extra_seconds=2)
        entry = second["lineage_packet"]["entries"][0]
        self.assertEqual(entry["change_type"], "UNCHANGED")
        self.assertEqual(entry["reason_codes"], [])
        self.assertEqual(entry["evidence"], [])

    def test_previous_record_requires_strictly_forward_time(self):
        first = self.record(extra_seconds=2)
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError, "NON_FORWARD_RECORDED_AT"
        ):
            self.record(previous=first, extra_seconds=1)

    def test_content_addressed_write_is_idempotent_and_chain_is_validated(self):
        first = self.record(extra_seconds=1)
        second = self.record(previous=first, extra_seconds=2)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, created = MODULE.write_record(first, root)
            self.assertTrue(created)
            self.assertEqual(MODULE.write_record(first, root), (path, False))
            MODULE.write_record(second, root)
            history = MODULE.load_history(root)
            self.assertEqual([row["record_sha256"] for row in history], [
                first["record_sha256"], second["record_sha256"]
            ])

    def test_chain_gap_is_rejected(self):
        first = self.record(extra_seconds=1)
        second = self.record(previous=first, extra_seconds=2)
        second["previous_record_sha256"] = "f" * 64
        second["record_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in second.items() if key != "record_sha256"}
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            MODULE.write_record(first, root)
            raw = root / f"record-{second['record_sha256']}.json"
            raw.write_text(json.dumps(second, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.OperationalDecisionLineageError, "RECORD_CHAIN_BROKEN"
            ):
                MODULE.load_history(root)

    def test_module_has_no_network_or_money_execution_surface(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue({"requests", "urllib", "httpx"}.isdisjoint(imports))
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("/v2/orders", "submit_order", "trade_proposal", "position_size"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
