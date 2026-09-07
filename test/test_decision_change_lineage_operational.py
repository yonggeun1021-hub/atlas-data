#!/usr/bin/env python3
"""P10-04 committed Daily Decision lineage operational wiring regressions."""
import ast
import copy
import datetime as dt
import hashlib
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


def validated_unified_generated_at(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = [
        row for row in value.get("components", [])
        if isinstance(row, dict) and row.get("component_id") == "UNIFIED_DECISION"
    ]
    if (
        len(rows) != 1
        or rows[0].get("validated") is not True
        or not isinstance(rows[0].get("packet"), dict)
    ):
        return None
    generated_at = value.get("generated_at")
    if rows[0]["packet"].get("generated_at") != generated_at:
        return None
    try:
        return dt.datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except (TypeError, ValueError):
        return None


def latest_validated_packet(paths):
    candidates = [
        (generated_at, path)
        for path in paths
        if (generated_at := validated_unified_generated_at(path)) is not None
    ]
    if not candidates:
        raise RuntimeError("VALIDATED_UNIFIED_DECISION_DAILY_FIXTURE_MISSING")
    return max(candidates, key=lambda item: (item[0], item[1].as_posix()))[1]


PACKET = latest_validated_packet(PACKETS)
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


def rehash_daily(packet):
    value = copy.deepcopy(packet)
    value.pop("packet_sha256", None)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


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

    def test_latest_validated_fixture_is_selected_by_generated_at(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lexical_last_but_old = root / "z-old.json"
            chronological_latest = root / "a-new.json"
            for path, generated_at in (
                (lexical_last_but_old, "2026-08-28T13:41:45Z"),
                (chronological_latest, "2026-08-29T00:39:03Z"),
            ):
                path.write_text(json.dumps({
                    "generated_at": generated_at,
                    "components": [{
                        "component_id": "UNIFIED_DECISION",
                        "validated": True,
                        "packet": {"generated_at": generated_at},
                    }],
                }), encoding="utf-8")
            self.assertEqual(
                latest_validated_packet(sorted(root.glob("*.json"))),
                chronological_latest,
            )

    def test_exact_source_commit_validator_accepts_its_historical_packet(self):
        relative = PACKET.relative_to(ROOT).as_posix()
        blob_sha = MODULE.hashlib.sha256(PACKET.read_bytes()).hexdigest()
        value = REAL_VALIDATE_DAILY_AT_COMMIT(SOURCE_COMMIT, relative, blob_sha)
        self.assertEqual(value["packet_sha256"], json.loads(PACKET.read_text())["packet_sha256"])

    def test_exact_source_commit_validator_binds_present_dynamic_clock_source(self):
        legacy = rehash_daily(synthetic_daily())
        report = {"decision_date": legacy["decision_date"], "candidates": []}
        valid_source = {
            "kind": "report",
            "report_sha256": MODULE.payload_sha256(report),
            "report": report,
        }
        relative = "evidence/daily_briefing/morning/2026-08-27/rev-001/packet.json"
        completed = subprocess.CompletedProcess([], 0, stdout="")

        def validate(packet):
            blob = (json.dumps(packet, sort_keys=True) + "\n").encode()
            REAL_VALIDATE_DAILY_AT_COMMIT.cache_clear()
            with mock.patch.object(MODULE, "_git_blob", return_value=blob), mock.patch.object(
                MODULE, "_materialize_exact_commit"
            ), mock.patch.object(MODULE.subprocess, "run", return_value=completed):
                return REAL_VALIDATE_DAILY_AT_COMMIT(
                    "a" * 40, relative, hashlib.sha256(blob).hexdigest()
                )

        # Historical records without the field remain readable, while each
        # approved present variant retains exact native JSON types and shape.
        self.assertEqual(validate(legacy), legacy)
        for source in (
            {"kind": "unavailable"},
            {"kind": "error", "value": "DynamicClockError:blocked"},
            valid_source,
        ):
            with self.subTest(valid=source):
                packet = copy.deepcopy(legacy)
                packet["frozen_sources"] = {"DYNAMIC_CLOCK": source}
                checked = validate(rehash_daily(packet))
                self.assertEqual(
                    checked["frozen_sources"]["DYNAMIC_CLOCK"], source
                )

        wrong_date_report = {"decision_date": "2026-08-26", "candidates": []}
        invalid_sources = (
            ({**valid_source, "kind": True}, "SOURCE_INVALID"),
            ({**valid_source, "report_sha256": True}, "SOURCE_INVALID"),
            ({**valid_source, "report_sha256": "A" * 64}, "SOURCE_INVALID"),
            ({**valid_source, "report_sha256": "0" * 64}, "SOURCE_SHA256_MISMATCH"),
            ({**valid_source, "extra": None}, "SOURCE_INVALID"),
            ({"kind": "unavailable", "value": "alias"}, "SOURCE_INVALID"),
            ({"kind": "error", "value": True}, "SOURCE_INVALID"),
            ({
                "kind": "report",
                "report_sha256": MODULE.payload_sha256(wrong_date_report),
                "report": wrong_date_report,
            }, "SOURCE_DECISION_DATE_MISMATCH"),
        )
        for source, code in invalid_sources:
            with self.subTest(code=code, source=source):
                packet = copy.deepcopy(legacy)
                packet["frozen_sources"] = {"DYNAMIC_CLOCK": source}
                with self.assertRaisesRegex(
                    MODULE.OperationalDecisionLineageError, code
                ):
                    validate(rehash_daily(packet))

        aliased_date_report = {"decision_date": True, "candidates": []}
        aliased_date_packet = copy.deepcopy(legacy)
        aliased_date_packet["decision_date"] = True
        aliased_date_packet["frozen_sources"] = {"DYNAMIC_CLOCK": {
            "kind": "report",
            "report_sha256": MODULE.payload_sha256(aliased_date_report),
            "report": aliased_date_report,
        }}
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError,
            "SOURCE_DECISION_DATE_MISMATCH",
        ):
            validate(rehash_daily(aliased_date_packet))

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
        with MODULE._exact_commit_checkout(SOURCE_COMMIT, ()) as checkout:
            self.assertTrue((checkout / ".git").is_file())
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
                    [
                        "git", "rev-list", "-1", SOURCE_COMMIT, "--", identity_path,
                    ],
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
            self.assertFalse((checkout / "data").exists())
            self.assertFalse((checkout / "evidence").exists())

    def test_exact_source_checkout_uses_local_sparse_worktree_without_network(self):
        commands = []

        def record(command, **kwargs):
            commands.append(command)
            if "rev-parse" in command:
                return subprocess.CompletedProcess(command, 0, stdout=SOURCE_COMMIT + "\n")
            return subprocess.CompletedProcess(command, 0, stdout="")

        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE.subprocess, "run", side_effect=record
        ):
            checkout = Path(temporary) / "repo"
            with mock.patch.object(Path, "exists", return_value=False):
                MODULE._materialize_exact_commit(SOURCE_COMMIT, checkout)

        worktree = next(command for command in commands if "worktree" in command)
        sparse = next(command for command in commands if "sparse-checkout" in command)
        self.assertIn("add", worktree)
        self.assertIn("--no-checkout", worktree)
        self.assertIn(str(checkout), worktree)
        self.assertEqual(sparse[-3:], ["/*", "!/data/", "!/evidence/"])
        self.assertTrue(all("https://" not in part for command in commands for part in command))

    def test_exact_validator_payload_patterns_are_finite_and_safe(self):
        unified = current_unified()
        rotation = next(
            row for row in unified["components"]
            if row["component"] == "ROTATION_DISCOVERY"
        )["source_packet"]
        rotation["wildcard_observations"]["source_envelopes"] = [{
            "submission_lineage": [{"path": "data/intake/wildcard/submission.json"}],
            "packet": {"submissions": [{"evidence": [{
                "audit_provenance": {
                    "record_locator": "evidence/source/example.json"
                }
            }]}]},
        }]
        rotation["dart_observations"]["source_packet"] = {
            "lineage": {
                "source_path": "data/latest_dart.json",
                "content_run_path": "data/latest_dart_content.json",
            },
            "observations": [{
                "subject_id": "012450",
                "rcept_no": "20260831800137",
                "evidence": {
                    "status": "RAW_BYTES_VERIFIED_ITEM_EXTRACTION_UNRATIFIED"
                },
            }],
        }
        self.assertEqual(
            MODULE._exact_validator_payload_patterns(unified),
            (
                "/data/dart_content/012450/20260831800137/",
                "/data/intake/wildcard/submission.json",
                "/data/latest_dart.json",
                "/data/latest_dart_content.json",
                "/evidence/source/example.json",
            ),
        )
        rotation["wildcard_observations"]["source_envelopes"][0][
            "submission_lineage"
        ][0]["path"] = "../../outside"
        with self.assertRaisesRegex(
            MODULE.OperationalDecisionLineageError,
            "EXACT_VALIDATOR_SOURCE_PATH_INVALID",
        ):
            MODULE._exact_validator_payload_patterns(unified)

    def test_exact_validator_payload_patterns_allow_unavailable_rotation(self):
        unified = current_unified()
        rotation = next(
            row for row in unified["components"]
            if row["component"] == "ROTATION_DISCOVERY"
        )
        rotation["source_packet"] = None
        self.assertEqual(MODULE._exact_validator_payload_patterns(unified), ())

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
