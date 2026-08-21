#!/usr/bin/env python3
"""P1-US-04 free forward-only Symbol Directory contract regression."""

import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "us_breadth_forward.py"
WORKFLOW = ROOT / ".github" / "workflows" / "p1-us04-forward-breadth.yml"
SPEC = importlib.util.spec_from_file_location("us_breadth_forward", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()
with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)


def source_text(source, day, rows):
    header = "|".join(source["required_fields"])
    body = []
    for values in rows:
        row = {field: "fixture" for field in source["required_fields"]}
        row.update(values)
        body.append("|".join(row[field] for field in source["required_fields"]))
    creation = dt.datetime.strptime(day, "%Y-%m-%d").strftime("%m%d%Y") + "20:15"
    # The live otherlisted footer currently has one fewer empty pipe column
    # than its header.  Footer identity is the creation marker; trailing empty
    # columns carry no semantics and must not make a valid official file fail.
    empty_columns = len(source["required_fields"]) - (
        2 if source["name"] == "other_listed" else 1
    )
    footer = "File Creation Time: " + creation + "|" * empty_columns
    return ("\r\n".join([header, *body, footer]) + "\r\n").encode("utf-8")


def fixture_rows(source, symbols):
    rows = []
    for symbol in symbols:
        row = {
            source["identity_field"]: symbol,
            "Security Name": f"Security {symbol}",
            "ETF": "N",
            "Test Issue": "N",
        }
        if source["exchange_field"]:
            row[source["exchange_field"]] = "N"
        rows.append(row)
    return rows


def create_snapshot(root, day, per_source, manifest=True):
    target = Path(root) / day
    target.mkdir(parents=True)
    (target / "_downloaded_at.txt").write_text(
        day + "T23:30:00Z\n", encoding="utf-8"
    )
    checksum_lines = []
    for source in CONTRACT["sources"]:
        raw = source_text(source, day, per_source[source["name"]])
        raw_name = source["raw_file"].removesuffix(".gz")
        checksum_lines.append(f"{hashlib.sha256(raw).hexdigest()}  {raw_name}")
        with gzip.GzipFile(target / source["raw_file"], "wb", mtime=0) as stream:
            stream.write(raw)
    (target / "_sha256.txt").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )
    test_contract = json.loads(json.dumps(CONTRACT))
    for source in test_contract["sources"]:
        source["minimum_records"] = 1
    if manifest:
        MODULE.build_manifest(target, "us-breadth-forward-capture/v1", test_contract)
    return target, test_contract


class USBreadthForwardTest(unittest.TestCase):
    def test_only_free_forward_membership_scope_is_authorized(self):
        self.assertEqual(CONTRACT["approval_status"], "FORWARD_ONLY_RATIFIED")
        self.assertEqual(
            CONTRACT["historical_universe_policy"],
            "as_captured_forward_only_no_current_state_backfill",
        )
        for key in (
            "historical_reconstruction_authorized",
            "price_breadth_authorized",
            "breadth_classification_authorized",
            "threshold_authorized",
            "regime_score_authorized",
            "production_wiring_authorized",
            "trading_action_authorized",
        ):
            self.assertFalse(CONTRACT[key])

    def test_paid_path_always_requires_new_user_confirmation(self):
        checkpoint = CONTRACT["paid_data_checkpoint"]
        self.assertEqual(checkpoint["status"], "USER_RECONFIRMATION_REQUIRED")
        self.assertFalse(checkpoint["approved"])
        self.assertIn("paid_api_or_vendor_integration", checkpoint["stop_before"])
        self.assertIn("license_or_subscription_cost", checkpoint["stop_before"])
        self.assertIn("delisted_security_ohlcv_acquisition", checkpoint["stop_before"])

    def test_manifest_binds_exact_raw_bytes_and_source_date(self):
        rows = {
            source["name"]: fixture_rows(source, ["AAA", "BBB"])
            for source in CONTRACT["sources"]
        }
        with tempfile.TemporaryDirectory() as tmp:
            target, contract = create_snapshot(tmp, "2026-08-19", rows)
            core = MODULE.validate_manifest(target, contract)
            self.assertEqual(core["snapshot_date"], "2026-08-19")
            self.assertEqual(len(core["members"]), 4)
            manifest = json.loads((target / "_manifest.json").read_text())
            self.assertEqual(
                manifest["paid_data_checkpoint"]["status"],
                "USER_RECONFIRMATION_REQUIRED",
            )
            with self.assertRaisesRegex(MODULE.ContractError, "APPEND_ONLY_VIOLATION"):
                MODULE.build_manifest(
                    target, "us-breadth-forward-capture/v1", contract
                )

    def test_tamper_duplicate_bad_header_and_date_mismatch_fail_closed(self):
        rows = {
            source["name"]: fixture_rows(source, ["AAA"])
            for source in CONTRACT["sources"]
        }
        with tempfile.TemporaryDirectory() as tmp:
            target, contract = create_snapshot(tmp, "2026-08-19", rows)
            raw_path = target / CONTRACT["sources"][0]["raw_file"]
            raw_path.write_bytes(raw_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(MODULE.ContractError, "RAW_FILE_INVALID|CHECKSUM"):
                MODULE.validate_manifest(target, contract)

        source = CONTRACT["sources"][0]
        test_contract = json.loads(json.dumps(CONTRACT))
        test_contract["sources"][0]["minimum_records"] = 1
        duplicate = source_text(source, "2026-08-19", fixture_rows(source, ["AAA", "AAA"]))
        with self.assertRaisesRegex(MODULE.ContractError, "IDENTITY_DUPLICATE"):
            MODULE.parse_source(duplicate, test_contract["sources"][0])
        bad_header = duplicate.replace(b"Symbol|", b"Ticker|", 1)
        with self.assertRaisesRegex(MODULE.ContractError, "HEADER_MISMATCH"):
            MODULE.parse_source(bad_header, test_contract["sources"][0])

        with tempfile.TemporaryDirectory() as tmp:
            target, contract = create_snapshot(tmp, "2026-08-19", rows, manifest=False)
            renamed = target.with_name("2026-08-20")
            target.rename(renamed)
            with self.assertRaisesRegex(MODULE.ContractError, "SOURCE_DATE_MISMATCH"):
                MODULE.snapshot_core(renamed, contract)

    def test_membership_diff_never_becomes_price_breadth(self):
        first_rows = {
            source["name"]: fixture_rows(source, ["AAA", "BBB"])
            for source in CONTRACT["sources"]
        }
        second_rows = {
            source["name"]: fixture_rows(source, ["BBB", "CCC"])
            for source in CONTRACT["sources"]
        }
        second_rows["nasdaq_listed"][0]["Security Name"] = "Renamed BBB"
        with tempfile.TemporaryDirectory() as tmp:
            first, contract = create_snapshot(tmp, "2026-08-19", first_rows)
            second, _ = create_snapshot(tmp, "2026-08-20", second_rows)
            before = MODULE.validate_manifest(first, contract)
            after = MODULE.validate_manifest(second, contract)
            result = MODULE.build_membership_diff(after, before, contract)
            MODULE.write_json_append_only(
                result, second / "_membership_diff.json"
            )
            self.assertEqual(
                MODULE.validate_snapshot_bundle(second, first, contract),
                after,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["membership"]["entered_count"], 2)
            self.assertEqual(result["membership"]["exited_count"], 2)
            self.assertEqual(result["membership"]["changed_attributes_count"], 1)
            self.assertEqual(
                result["price_breadth"]["status"],
                "UNKNOWN_PRICE_SOURCE_UNAVAILABLE",
            )
            self.assertIsNone(result["price_breadth"]["advance_fraction"])
            self.assertFalse(result["authority"]["historical_reconstruction_authorized"])

    def test_bundle_validator_rederives_diff_and_rejects_tamper(self):
        first_rows = {
            source["name"]: fixture_rows(source, ["AAA", "BBB"])
            for source in CONTRACT["sources"]
        }
        second_rows = {
            source["name"]: fixture_rows(source, ["BBB", "CCC"])
            for source in CONTRACT["sources"]
        }
        with tempfile.TemporaryDirectory() as tmp:
            first, contract = create_snapshot(tmp, "2026-08-19", first_rows)
            second, _ = create_snapshot(tmp, "2026-08-20", second_rows)
            first_core = MODULE.validate_manifest(first, contract)
            second_core = MODULE.validate_manifest(second, contract)
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(first_core, contract=contract),
                first / "_membership_diff.json",
            )
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(second_core, first_core, contract),
                second / "_membership_diff.json",
            )

            self.assertEqual(
                MODULE.validate_snapshot_bundle(first, contract=contract),
                first_core,
            )
            self.assertEqual(
                MODULE.validate_snapshot_bundle(second, first, contract),
                second_core,
            )

            diff_path = second / "_membership_diff.json"
            tampered = json.loads(diff_path.read_text(encoding="utf-8"))
            tampered["membership"]["entered_count"] += 1
            diff_path.write_text(
                json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MODULE.ContractError, "MEMBERSHIP_DIFF_MISMATCH"
            ):
                MODULE.validate_snapshot_bundle(second, first, contract)

    def test_tracked_bundles_reproduce_exactly_and_inventory_is_closed(self):
        first = ROOT / "evidence" / "us_breadth" / "raw" / "2026-08-19"
        second = ROOT / "evidence" / "us_breadth" / "raw" / "2026-08-20"
        MODULE.validate_snapshot_bundle(first)
        MODULE.validate_snapshot_bundle(second, first)

        rows = {
            source["name"]: fixture_rows(source, ["AAA"])
            for source in CONTRACT["sources"]
        }
        with tempfile.TemporaryDirectory() as tmp:
            target, contract = create_snapshot(tmp, "2026-08-19", rows)
            core = MODULE.validate_manifest(target, contract)
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(core, contract=contract),
                target / "_membership_diff.json",
            )
            (target / "unexpected.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.ContractError, "BUNDLE_INVENTORY_MISMATCH"
            ):
                MODULE.validate_snapshot_bundle(target, contract=contract)

    def test_tracked_archive_replays_exactly_via_true_predecessor_chain(self):
        # This walks whatever the committed evidence/us_breadth/raw/ archive
        # actually contains -- not two hardcoded dates -- so every future
        # scheduled capture is automatically covered by this regression
        # without a code change.
        raw_root = ROOT / "evidence" / "us_breadth" / "raw"
        tracked_dates = sorted(
            path.name
            for path in raw_root.iterdir()
            if path.is_dir() and MODULE.DATE_DIR.fullmatch(path.name)
        )
        self.assertTrue(tracked_dates)

        chain = MODULE.replay_archive(raw_root)
        self.assertEqual(
            [core["snapshot_date"] for core in chain], tracked_dates
        )

        latest = MODULE.universe_as_of(tracked_dates[-1], raw_root)
        self.assertEqual(latest["snapshot_date"], tracked_dates[-1])
        self.assertEqual(latest["members"], chain[-1]["members"])
        self.assertEqual(
            latest["historical_universe_policy"],
            "as_captured_forward_only_no_current_state_backfill",
        )

        before_baseline = (
            dt.date.fromisoformat(tracked_dates[0]) - dt.timedelta(days=1)
        ).isoformat()
        with self.assertRaisesRegex(
            MODULE.ContractError, "AS_OF_BEFORE_ARCHIVE_BASELINE"
        ):
            MODULE.universe_as_of(before_baseline, raw_root)

    def test_replay_archive_rejects_deleted_middle_snapshot(self):
        # A snapshot's persisted _membership_diff.json records the diff
        # against its true immediate predecessor. If that predecessor is
        # later deleted, replay_archive must not silently accept the next
        # remaining snapshot as a fresh baseline -- the committed diff still
        # claims a non-baseline PASS against a predecessor that is gone.
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            raw_root.mkdir()
            rows_a = {
                s["name"]: fixture_rows(s, ["AAA"]) for s in CONTRACT["sources"]
            }
            rows_b = {
                s["name"]: fixture_rows(s, ["AAA", "BBB"])
                for s in CONTRACT["sources"]
            }
            rows_c = {
                s["name"]: fixture_rows(s, ["AAA", "BBB", "CCC"])
                for s in CONTRACT["sources"]
            }
            first, contract = create_snapshot(raw_root, "2026-08-17", rows_a)
            second, _ = create_snapshot(raw_root, "2026-08-18", rows_b)
            third, _ = create_snapshot(raw_root, "2026-08-19", rows_c)
            first_core = MODULE.validate_manifest(first, contract)
            second_core = MODULE.validate_manifest(second, contract)
            third_core = MODULE.validate_manifest(third, contract)
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(first_core, contract=contract),
                first / "_membership_diff.json",
            )
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(
                    second_core, first_core, contract
                ),
                second / "_membership_diff.json",
            )
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(
                    third_core, second_core, contract
                ),
                third / "_membership_diff.json",
            )

            chain = MODULE.replay_archive(raw_root, contract)
            self.assertEqual(
                [core["snapshot_date"] for core in chain],
                ["2026-08-17", "2026-08-18", "2026-08-19"],
            )

            shutil.rmtree(second)
            with self.assertRaisesRegex(
                MODULE.ContractError, "MEMBERSHIP_DIFF_MISMATCH"
            ):
                MODULE.replay_archive(raw_root, contract)

    def test_replay_archive_rejects_unexpected_entries_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            raw_root.mkdir()
            rows = {
                s["name"]: fixture_rows(s, ["AAA"]) for s in CONTRACT["sources"]
            }
            first, contract = create_snapshot(raw_root, "2026-08-19", rows)
            core = MODULE.validate_manifest(first, contract)
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(core, contract=contract),
                first / "_membership_diff.json",
            )

            (raw_root / "not-a-date").mkdir()
            with self.assertRaisesRegex(
                MODULE.ContractError, "ARCHIVE_INVENTORY_INVALID"
            ):
                MODULE.replay_archive(raw_root, contract)
            shutil.rmtree(raw_root / "not-a-date")

            (raw_root / "2026-08-20").symlink_to(first)
            with self.assertRaisesRegex(
                MODULE.ContractError, "ARCHIVE_INVENTORY_INVALID"
            ):
                MODULE.replay_archive(raw_root, contract)

    def test_universe_as_of_forward_fills_across_gaps_and_refuses_pre_archive(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            raw_root.mkdir()
            rows_a = {
                s["name"]: fixture_rows(s, ["AAA"]) for s in CONTRACT["sources"]
            }
            rows_b = {
                s["name"]: fixture_rows(s, ["AAA", "BBB"])
                for s in CONTRACT["sources"]
            }
            # Friday capture, then Monday -- a weekend gap between them.
            first, contract = create_snapshot(raw_root, "2026-08-14", rows_a)
            second, _ = create_snapshot(raw_root, "2026-08-17", rows_b)
            first_core = MODULE.validate_manifest(first, contract)
            second_core = MODULE.validate_manifest(second, contract)
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(first_core, contract=contract),
                first / "_membership_diff.json",
            )
            MODULE.write_json_append_only(
                MODULE.build_membership_diff(
                    second_core, first_core, contract
                ),
                second / "_membership_diff.json",
            )

            weekend = MODULE.universe_as_of("2026-08-15", raw_root, contract)
            self.assertEqual(weekend["snapshot_date"], "2026-08-14")
            self.assertEqual(
                set(weekend["members"]), set(first_core["members"])
            )

            exact = MODULE.universe_as_of("2026-08-17", raw_root, contract)
            self.assertEqual(exact["snapshot_date"], "2026-08-17")
            self.assertEqual(
                set(exact["members"]), set(second_core["members"])
            )

            with self.assertRaisesRegex(
                MODULE.ContractError, "AS_OF_BEFORE_ARCHIVE_BASELINE"
            ):
                MODULE.universe_as_of("2026-08-13", raw_root, contract)

    def test_universe_as_of_empty_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                MODULE.ContractError, "ARCHIVE_EMPTY"
            ):
                MODULE.universe_as_of(
                    "2026-08-19", Path(tmp) / "does-not-exist", CONTRACT
                )

    def test_baseline_does_not_claim_all_members_entered(self):
        current = {
            "snapshot_date": "2026-08-19",
            "members": {"nasdaq_listed:AAA": {}},
            "source_counts": {"nasdaq_listed": 1, "other_listed": 0},
        }
        result = MODULE.build_membership_diff(current, contract=CONTRACT)
        self.assertEqual(result["status"], "BASELINE_ESTABLISHED")
        self.assertIsNone(result["membership"]["entered_count"])
        self.assertIsNone(result["membership"]["previous_count"])

    def test_workflow_is_scheduled_append_only_and_uses_only_free_sources(self):
        triggers = WF.get("on", WF.get(True))
        self.assertEqual(
            {item["cron"] for item in triggers["schedule"]},
            {"20 1 * * 2-6"},
        )
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(WF["permissions"], {"contents": "write"})
        self.assertEqual(WF["concurrency"]["cancel-in-progress"], False)
        capture = next(
            step
            for step in WF["jobs"]["capture"]["steps"]
            if step.get("name") == "Capture official current-day directories append-only"
        )
        command = capture["run"]
        for source in CONTRACT["sources"]:
            self.assertIn(source["endpoint"], command)
        self.assertNotIn("TIINGO", command.upper())
        self.assertNotIn("API_KEY", command.upper())
        self.assertLess(command.index("_sha256.txt"), command.index("gzip -n"))
        self.assertIn("skipped_existing", command)
        self.assertIn("_membership_diff.json", command)
        self.assertLess(command.index("PREVIOUS=$("), command.index("skipped_existing"))
        self.assertLess(command.index("validate-bundle"), command.index("skipped_existing"))
        self.assertGreater(command.rindex("validate-bundle"), command.index(" diff "))
        self.assertIn("replay-archive", command)
        self.assertLess(
            command.index('mv "$DATED" "$DIR"'), command.index("replay-archive")
        )
        self.assertLess(
            command.index("replay-archive"), command.rindex("result=captured")
        )


if __name__ == "__main__":
    unittest.main()
