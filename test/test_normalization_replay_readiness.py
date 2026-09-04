#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "regime" / "normalization_replay_readiness.py"
SPEC = importlib.util.spec_from_file_location("normalization_replay_readiness_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

# Import through the same package path the tested module uses internally
# (`from regime import paper_regime_reference as PRR`) so identity checks
# below compare the *same* cached module object, not a second copy.
from regime import paper_regime_reference as PRR  # noqa: E402

REPLAY_STATUSES = {"COMPUTABLE_NOW", "PARTIAL_HISTORY", "NOT_COMPUTABLE"}


def _copy_retained_snapshots(fixture_root: Path, skip_last_date: bool = False) -> dict:
    """Copy only the retained dated snapshot files into an isolated fixture.

    Copies the exact bytes this repository already retained (never regenerating
    or synthesizing a snapshot), so replay behaviour under test is the real
    historical behaviour. ``skip_last_date`` drops each market's most recent
    retained date, which is how the no-lookahead check builds an "as if we ran
    this yesterday" root.
    """
    shutil.copytree(ROOT / "config", fixture_root / "config", dirs_exist_ok=True)
    (fixture_root / "data").mkdir(parents=True, exist_ok=True)
    kept = {}
    for source_root, filename in (
        (MODULE.US_SOURCE_ROOT, MODULE.US_SOURCE_FILENAME),
        (MODULE.KR_SOURCE_ROOT, MODULE.KR_SOURCE_FILENAME),
    ):
        dates = MODULE.discover_dates(ROOT / source_root, filename)
        if skip_last_date:
            dates = dates[:-1]
        (fixture_root / source_root).mkdir(parents=True, exist_ok=True)
        for date in dates:
            destination = fixture_root / source_root / date
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / source_root / date / filename, destination / filename)
        kept[source_root] = dates
    return kept


class NormalizationReplayReadinessTest(unittest.TestCase):
    """Evidence-only: never asserts a threshold value, only readiness/coverage
    facts derived from whatever this repo already has retained on disk."""

    def test_schema_and_mode_are_shadow_only(self):
        report = MODULE.build_report()
        self.assertEqual(report["schema_version"], "regime_normalization_replay_readiness/v1")
        self.assertEqual(report["mode"], "SHADOW_EVIDENCE_ONLY_NOT_RATIFICATION")
        self.assertEqual(report["wbs"], "P1-COM-05")

    def test_authority_is_all_false_except_the_one_readonly_flag(self):
        report = MODULE.build_report()
        authority = report["authority"]
        self.assertTrue(authority["replay_readiness_evidence_authorized"])
        for key, value in authority.items():
            if key == "replay_readiness_evidence_authorized":
                continue
            self.assertIs(value, False, f"{key} must stay false")

    def test_base_policy_is_the_untouched_pm_candidate_file(self):
        report = MODULE.build_report()
        policy_path = ROOT / "config" / "paper_regime_reference_policy_v1.json"
        self.assertEqual(report["base_policy"]["path"], "config/paper_regime_reference_policy_v1.json")
        self.assertEqual(report["base_policy"]["sha256"], MODULE.file_sha256(policy_path))
        self.assertEqual(
            report["base_policy"]["status"],
            "PM_BASELINE_CANDIDATE_NOT_CIO_RATIFIED_SENSOR_POLICY",
        )

    def test_reuses_paper_regime_reference_build_functions_unmodified(self):
        # The readiness module must not carry its own copy of build_us/build_kr —
        # it has to import the live functions so a future edit to the candidate
        # rule is automatically replayed, never silently drifts out of sync.
        self.assertIs(MODULE.PRR.build_us, PRR.build_us)
        self.assertIs(MODULE.PRR.build_kr, PRR.build_kr)

    def test_all_ten_axis_instances_report_an_allowed_replay_status(self):
        report = MODULE.build_report()
        for market in ("US", "KR"):
            axes = report["markets"][market]["axes"]
            self.assertEqual(set(axes), set(PRR.AXES))
            for axis_name, summary in axes.items():
                self.assertIn(summary["replay_status"], REPLAY_STATUSES)
                self.assertEqual(summary["axis"], axis_name)

    def test_axis_date_buckets_sum_to_dates_discovered(self):
        report = MODULE.build_report()
        for market in ("US", "KR"):
            axes = report["markets"][market]["axes"]
            for summary in axes.values():
                total = (
                    summary["dates_observed"]
                    + summary["dates_blocked_this_axis"]
                    + summary["dates_not_attempted"]
                )
                self.assertEqual(total, summary["dates_discovered"])
                self.assertEqual(
                    summary["coverage_ratio"],
                    f"{summary['dates_observed']}/{summary['dates_discovered']}",
                )

    def test_no_axis_ever_reports_a_threshold_value(self):
        # This is the hard guardrail: the report may only ever carry evidence
        # (dates, observed_value, direction counts) — never a numeric
        # classification band, weight, or score threshold of its own.
        report = MODULE.build_report()
        forbidden_keys = {
            "positive_min", "negative_max", "positive_below", "neutral_below",
            "negative_below", "stress_min", "positive_min_fraction",
            "negative_max_fraction", "RISK_ON_MIN_SCORE", "RISK_OFF_MAX_SCORE",
        }
        blob = json.dumps(report)
        for key in forbidden_keys:
            self.assertNotIn(key, blob, f"readiness report must never carry {key}")

    def test_deterministic_rerun_is_byte_identical(self):
        first = MODULE.build_report()
        second = MODULE.build_report()
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))

    def test_validate_report_accepts_its_own_output(self):
        report = MODULE.build_report()
        validated = MODULE.validate_report(copy.deepcopy(report))
        self.assertEqual(validated, report)

    def test_validate_report_rejects_tampered_payload(self):
        report = MODULE.build_report()
        tampered = copy.deepcopy(report)
        tampered["markets"]["US"]["axes"]["TREND"]["replay_status"] = "COMPUTABLE_NOW_FAKE"
        with self.assertRaises(MODULE.ReplayReadinessError):
            MODULE.validate_report(tampered)

    def test_discover_dates_is_read_only_and_date_shaped(self):
        us_dir = ROOT / MODULE.US_SOURCE_ROOT
        dates = MODULE.discover_dates(us_dir, MODULE.US_SOURCE_FILENAME)
        for date in dates:
            self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(dates, sorted(dates))

    def test_discover_dates_on_missing_directory_is_empty_not_an_error(self):
        missing = ROOT / "evidence" / "does_not_exist_for_this_test"
        self.assertEqual(MODULE.discover_dates(missing, "manifest.json"), [])

    def test_write_report_is_append_only_and_conflict_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "root"
            shutil.copytree(ROOT / "config", fixture_root / "config")
            (fixture_root / MODULE.US_SOURCE_ROOT).mkdir(parents=True, exist_ok=True)
            (fixture_root / MODULE.KR_SOURCE_ROOT).mkdir(parents=True, exist_ok=True)
            (fixture_root / "data").mkdir(parents=True, exist_ok=True)

            report = MODULE.build_report(fixture_root)
            evidence, latest = MODULE.write_report(report, fixture_root)
            self.assertTrue(evidence.is_file())
            self.assertTrue(latest.is_file())
            first_text = evidence.read_text(encoding="utf-8")

            # Writing the identical report again must be a silent no-op.
            evidence_again, _ = MODULE.write_report(report, fixture_root)
            self.assertEqual(evidence_again, evidence)
            self.assertEqual(evidence.read_text(encoding="utf-8"), first_text)

            # If something else ever wrote different bytes to this exact
            # evidence path, re-writing the same report must fail closed
            # rather than silently overwrite append-only evidence.
            evidence.write_text("some other content at the same path\n", encoding="utf-8")
            with self.assertRaises(MODULE.ReplayReadinessError):
                MODULE.write_report(report, fixture_root)

    def test_unexpected_build_error_blocks_one_date_not_the_whole_report(self):
        # build_us/build_kr only raise PaperRegimeReferenceError for the shapes
        # they explicitly guard; anything else (a raw KeyError from an older
        # snapshot shape) must degrade to one unreplayable date.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packet.json"
            path.write_text("{}", encoding="utf-8")

            def raises_raw_key_error(source, policy):
                raise KeyError("as_of_session_date")

            entry = MODULE._replay_one_date(
                path, "SOURCE_UNREADABLE", raises_raw_key_error, {}, {}, set(),
            )
        self.assertEqual(entry["outcome"], "UNSUPPORTED_SOURCE_SHAPE")
        self.assertEqual(entry["blocking_code"], "SOURCE_SHAPE_UNSUPPORTED_KeyError")
        self.assertEqual(set(entry["axes"]), set(PRR.AXES))
        self.assertTrue(all(row is None for row in entry["axes"].values()))
        # The exception message must never reach public evidence.
        self.assertNotIn("as_of_session_date", entry["blocking_code"])

    def test_unsupported_shape_date_still_sums_into_axis_buckets(self):
        per_date = {
            "2026-01-02": {
                "outcome": "UNSUPPORTED_SOURCE_SHAPE",
                "blocking_code": "SOURCE_SHAPE_UNSUPPORTED_KeyError",
                "axes": {name: None for name in PRR.AXES},
            }
        }
        summary = MODULE._summarize_axis("TREND", per_date, ["2026-01-02"])
        self.assertEqual(summary["replay_status"], "NOT_COMPUTABLE")
        self.assertEqual(summary["dates_observed"], 0)
        self.assertEqual(summary["dates_not_attempted"], 1)
        self.assertEqual(
            summary["dates_observed"]
            + summary["dates_blocked_this_axis"]
            + summary["dates_not_attempted"],
            summary["dates_discovered"],
        )
        self.assertIn("SOURCE_SHAPE_UNSUPPORTED_KeyError", summary["blocking_reason_codes"])

    def test_replay_over_real_retained_history_observes_axes(self):
        # Guards against a report that is only ever exercised on empty
        # fixtures: the OBSERVED path must actually run on retained bytes.
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "root"
            kept = _copy_retained_snapshots(fixture_root)
            if not any(kept.values()):
                self.skipTest("no retained snapshots in this checkout")
            report = MODULE.build_report(fixture_root)

        for market, source_root in (("US", MODULE.US_SOURCE_ROOT), ("KR", MODULE.KR_SOURCE_ROOT)):
            block = report["markets"][market]
            self.assertEqual(block["dates_discovered"], kept[source_root])
            self.assertEqual(set(block["retained_source_sha256"]), set(kept[source_root]))
            for summary in block["axes"].values():
                self.assertIn(summary["replay_status"], REPLAY_STATUSES)
                self.assertLessEqual(summary["dates_observed"], summary["dates_discovered"])
                for observation in summary["observations"]:
                    self.assertRegex(observation["as_of_date"], r"^\d{4}-\d{2}-\d{2}$")
                    self.assertIn(
                        observation["direction"],
                        {"POSITIVE", "NEUTRAL", "NEGATIVE", "STRESS"},
                    )

    def test_no_lookahead_removing_the_latest_date_does_not_change_earlier_days(self):
        # Point-in-time integrity, checked structurally: an earlier date's axis
        # observation must be identical whether or not a later date exists on
        # disk. If any future evidence leaked into an earlier evaluation, the
        # truncated run would differ.
        with tempfile.TemporaryDirectory() as tmp:
            full_root = Path(tmp) / "full"
            past_root = Path(tmp) / "past"
            full_kept = _copy_retained_snapshots(full_root)
            past_kept = _copy_retained_snapshots(past_root, skip_last_date=True)
            if not any(len(dates) >= 2 for dates in full_kept.values()):
                self.skipTest("need at least two retained dates to test lookahead")
            full = MODULE.build_report(full_root)
            past = MODULE.build_report(past_root)

        for market, source_root in (("US", MODULE.US_SOURCE_ROOT), ("KR", MODULE.KR_SOURCE_ROOT)):
            earlier_dates = set(past_kept[source_root])
            self.assertTrue(earlier_dates.issubset(set(full_kept[source_root])))
            for axis_name in PRR.AXES:
                past_rows = [
                    row
                    for row in past["markets"][market]["axes"][axis_name]["observations"]
                    if row["as_of_date"] in earlier_dates
                ]
                full_rows = [
                    row
                    for row in full["markets"][market]["axes"][axis_name]["observations"]
                    if row["as_of_date"] in earlier_dates
                ]
                self.assertEqual(
                    past_rows,
                    full_rows,
                    f"{market}/{axis_name} changed once a later date existed",
                )

    def test_build_report_does_not_mutate_retained_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "root"
            kept = _copy_retained_snapshots(fixture_root)
            if not any(kept.values()):
                self.skipTest("no retained snapshots in this checkout")

            def snapshot_hashes():
                return {
                    f"{source_root}/{date}": MODULE.file_sha256(
                        fixture_root / source_root / date / filename
                    )
                    for source_root, filename in (
                        (MODULE.US_SOURCE_ROOT, MODULE.US_SOURCE_FILENAME),
                        (MODULE.KR_SOURCE_ROOT, MODULE.KR_SOURCE_FILENAME),
                    )
                    for date in kept[source_root]
                }

            before = snapshot_hashes()
            report = MODULE.build_report(fixture_root)
            self.assertEqual(snapshot_hashes(), before)

            # The report must pin the exact retained bytes it replayed.
            for market, source_root in (
                ("US", MODULE.US_SOURCE_ROOT),
                ("KR", MODULE.KR_SOURCE_ROOT),
            ):
                for date, digest in report["markets"][market]["retained_source_sha256"].items():
                    self.assertEqual(digest, before[f"{source_root}/{date}"])

    def test_pit_replay_block_states_only_enforced_facts(self):
        report = MODULE.build_report()
        pit = report["pit_replay"]
        self.assertTrue(pit["each_date_replayed_independently"])
        self.assertIs(pit["future_dates_used_in_any_date_evaluation"], False)
        self.assertIs(pit["retained_sources_mutated_by_this_module"], False)
        self.assertIs(pit["candidate_rule_modified_by_this_module"], False)
        self.assertEqual(
            pit["candidate_rule_source"],
            "regime/paper_regime_reference.py::build_us,build_kr",
        )

    def test_no_retained_evidence_is_not_computable_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "root"
            shutil.copytree(ROOT / "config", fixture_root / "config")
            (fixture_root / MODULE.US_SOURCE_ROOT).mkdir(parents=True, exist_ok=True)
            (fixture_root / MODULE.KR_SOURCE_ROOT).mkdir(parents=True, exist_ok=True)
            (fixture_root / "data").mkdir(parents=True, exist_ok=True)

            report = MODULE.build_report(fixture_root)
            for market in ("US", "KR"):
                for axis_summary in report["markets"][market]["axes"].values():
                    self.assertEqual(axis_summary["replay_status"], "NOT_COMPUTABLE")
                    self.assertEqual(axis_summary["replay_status_reason"], "NO_RETAINED_SOURCE_DATES")
                    self.assertEqual(axis_summary["dates_discovered"], 0)


if __name__ == "__main__":
    unittest.main()
