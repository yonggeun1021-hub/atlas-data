#!/usr/bin/env python3
"""Focused integration for the opt-in current P2-03 ratified proof path.

These tests use only already-committed historical source evidence.  They do
not claim or fabricate the first post-2026-09-14 natural pair.  The opt-in
usable-seed connection tests below are SYNTHETIC_TEST_ONLY (legal fixtures in
temporary storage behind a fake git commit), never natural readback.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_capital_rotation_ledger_proof.py"
SPEC = importlib.util.spec_from_file_location(
    "korea_capital_rotation_current_ratified_proof", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

PRIOR = "2026-08-13"
CURRENT = "2026-08-14"


class CurrentRatifiedArtifactConsumptionTests(unittest.TestCase):
    def test_exact_committed_ratified_binding_and_policy_are_consumed(self):
        value, policy = MODULE.build_current_ratified_price_side(PRIOR, CURRENT)
        binding = value["taxonomy_binding"]
        self.assertEqual(
            binding["taxonomy_contract_version"],
            "korea_sector_identity_binding/1",
        )
        self.assertEqual(
            binding["taxonomy_decision_sha256"],
            "09e2db653c04592298c0068745c2393ed4ee754282a5289ca08d77869ba8e8e3",
        )
        self.assertEqual(
            binding["taxonomy_packet_sha256"],
            "6027e89b70766599bac4a242aef5bf608f7979628a6e8b5cbaf23219cab287e3",
        )
        self.assertEqual(
            policy["policy_id"],
            "POLICY.P2_03.KOREA_OWN_BENCHMARK_EXTREMES.RATIFIED.V1",
        )
        self.assertEqual(policy["effective_from"], "2026-09-14")
        self.assertTrue(
            all(
                scope["top_count"] == 3 and scope["bottom_count"] == 3
                for scope in policy["benchmark_scopes"]
            )
        )

    def test_committed_artifact_drift_fails_closed(self):
        original = MODULE.RATIFIED.load_committed_policy()
        tampered = copy.deepcopy(original)
        tampered["maximum_calendar_gap_days"] = 99
        with mock.patch.object(
            MODULE.RATIFIED, "load_committed_policy", return_value=tampered
        ):
            with self.assertRaisesRegex(
                RuntimeError, "RATIFIED_ARTIFACT_REDERIVATION_MISMATCH"
            ):
                MODULE.load_current_ratified_artifacts()

    def test_pre_effective_real_pair_remains_inert(self):
        packet = MODULE.build_current_ratified_packet(PRIOR, CURRENT)
        self.assertEqual(packet["schema_version"], "korea_capital_rotation_packet/4")
        self.assertEqual(packet["contract_version"], "korea_capital_rotation/4")
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertEqual(packet["observation_pair"]["prior_date"], PRIOR)
        self.assertEqual(packet["observation_pair"]["current_date"], CURRENT)
        checked = MODULE.KCR.validate_packet(copy.deepcopy(packet))
        self.assertEqual(checked, packet)


class ExternalPacketBoundaryTests(unittest.TestCase):
    def test_external_write_preserves_pointer_and_revalidates(self):
        pointer_path = ROOT / "data" / "latest_korea_rotation.json"
        pointer_before = pointer_path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "korea-rotation.json"
            result = MODULE.run_current_ratified(PRIOR, CURRENT, out)
            self.assertEqual(result["packet_out"], out.resolve())
            persisted = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(
                MODULE.KCR.validate_packet(copy.deepcopy(persisted)), persisted
            )
        self.assertEqual(pointer_path.read_bytes(), pointer_before)

    def test_relative_and_repository_outputs_are_forbidden(self):
        packet = MODULE.build_current_ratified_packet(PRIOR, CURRENT)
        with self.assertRaisesRegex(
            RuntimeError, "RATIFIED_PACKET_OUTPUT_MUST_BE_ABSOLUTE"
        ):
            MODULE.write_external_ratified_packet(
                Path("korea-rotation.json"), packet
            )
        with self.assertRaisesRegex(
            RuntimeError, "RATIFIED_PACKET_TRACKED_OUTPUT_FORBIDDEN"
        ):
            MODULE.write_external_ratified_packet(
                ROOT / "data" / "forbidden-korea-rotation.json", packet
            )

    def test_no_p2_05_ledger_or_state_policy_path_is_added(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("rotation_state_ledger", source)
        self.assertNotIn("rotation_state_policy_ratification", source)
        self.assertNotIn("--ledger", source)


class LegacyProofPreservationTests(unittest.TestCase):
    def test_legacy_default_function_signature_and_policy_are_unchanged(self):
        value, policy = MODULE.build_real_price_side(PRIOR, CURRENT)
        self.assertEqual(
            policy["policy_id"], "POLICY.P2.03.KOREA_OWN_BENCHMARK_EXTREMES_V1"
        )
        self.assertEqual(policy["effective_from"], "2026-08-01")
        self.assertEqual(value["taxonomy_binding"]["taxonomy_packet_sha256"], "0" * 64)
        self.assertTrue(
            all(
                scope["top_count"] == 1 and scope["bottom_count"] == 1
                for scope in policy["benchmark_scopes"]
            )
        )


FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "usable_seed_rotation_fixtures", ROOT / "test" / "test_korea_capital_rotation.py"
)
FIXTURES = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(FIXTURES)
SYNTHETIC_SEED_COMMIT = "d" * 40  # SYNTHETIC_TEST_ONLY commit identity served by a fake git
SEED_PRIOR = "2026-08-18"
SEED_CURRENT = "2026-08-20"
SEED_FETCH_PRIORS = {SEED_PRIOR: "2026-08-14", SEED_CURRENT: "2026-08-19"}


class UsableSeedProofConnectionTests(unittest.TestCase):
    """SYNTHETIC_TEST_ONLY connection through the real live-fetch verifier,
    loader and consumer. Seeds live in temporary storage; nothing here is
    natural evidence, and no provider call or tracked write may occur."""

    def setUp(self):
        self.value, self.policy = FIXTURES.make_bundle()
        self.live = MODULE._live_fetch_module()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.context_root = Path(tmp.name) / "korea_leadership_context"
        self.committed = {}
        self.write_seed(SEED_PRIOR, self.value["prior_observation"])
        self.write_seed(SEED_CURRENT, self.value["current_observation"])
        # The synthetic seed commit also legitimately carries the real,
        # already-ratified config/policy/binding bytes -- these are not
        # part of what this suite tests, and any real source_commit on
        # this repo would carry them unchanged. Whichever real Breadth
        # packet already exists for these dates is pinned the same way;
        # a date with no real Breadth packet is intentionally left
        # unregistered so the F2 pin loop records it as a genuine,
        # non-raising ABSENT_AT_SOURCE_COMMIT gap.
        for path in (
            MODULE.RATIFIED.DECISION_PATH, MODULE.RATIFIED.IDENTITY_DOCUMENT_PATH,
            MODULE.RATIFIED.BINDING_PATH, MODULE.RATIFIED.POLICY_PATH,
            MODULE.RATIFIED.LEADERSHIP_POLICY_PATH, MODULE.RATIFIED.KRX_HOLIDAY_CAPTURE_PATH,
            MODULE.KCR.CONTRACT_PATH, MODULE.KCR.SECTOR_IDENTITY_BINDING_CONTRACT_PATH,
        ):
            self.committed[path.relative_to(ROOT).as_posix()] = path.read_bytes()
        for date in (SEED_PRIOR, SEED_CURRENT):
            breadth_path = ROOT / "data/observations/korea_breadth_context" / date / "packet.json"
            if breadth_path.is_file():
                self.committed[breadth_path.relative_to(ROOT).as_posix()] = breadth_path.read_bytes()

    def relative(self, date):
        return f"data/observations/korea_leadership_context/{date}/packet.json"

    def write_seed(self, date, inner, *, outcome="populated", commit=True):
        summary = {
            "schema_version": self.live.SCHEMA_VERSION,
            "observation_date": date,
            "prior_date": SEED_FETCH_PRIORS[date],
            "outcome": outcome,
            "reason": None if outcome == "populated" else "SYNTHETIC_BLOCKED_FIXTURE",
            "leadership_packet_sha256": None if inner is None else inner["payload_sha256"],
            "leadership_packet": inner,
            "markets": {
                market: {"raw_response_sha256": {"prior": "1" * 64, "current": "2" * 64}}
                for market in ("KOSDAQ", "KOSPI")
            },
            "generated_at": f"{date}T09:05:00Z",
        }
        summary["payload_sha256"] = self.live.payload_sha256(summary)
        path = self.context_root / date / "packet.json"
        self.live.write_json_atomic(path, summary)
        raw = path.read_bytes()
        if commit:
            self.committed[self.relative(date)] = raw
        return summary, path

    def fake_git(self, commit_available):
        real_run = subprocess.run
        committed = self.committed

        def run(args, **kwargs):
            if list(args[:3]) == ["git", "cat-file", "-e"]:
                return subprocess.CompletedProcess(args, 0 if commit_available else 128, stdout=b"", stderr=b"")
            if list(args[:2]) == ["git", "show"]:
                commit, _, relative = args[2].partition(":")
                if commit == SYNTHETIC_SEED_COMMIT and relative in committed:
                    return subprocess.CompletedProcess(args, 0, stdout=committed[relative], stderr=b"")
                # Real subprocess.run(check=True) raises on a non-zero
                # return instead of returning it -- honor that here too,
                # since _pin_source_files() (F2) relies on it exactly like
                # the PAPER path's own original pinned_bytes() closure.
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(
                        128, args, output=b"", stderr=b"fatal: synthetic path absent",
                    )
                return subprocess.CompletedProcess(args, 128, stdout=b"", stderr=b"fatal: synthetic path absent")
            return real_run(args, **kwargs)
        return run

    def patched(self, *, breadth=None, commit_available=True, binding=None, policy=None):
        binding = self.value["taxonomy_binding"] if binding is None else binding
        policy = self.policy if policy is None else policy
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(
            MODULE, "_leadership_context_root", return_value=self.context_root,
        ))
        stack.enter_context(mock.patch.object(self.live, "CONTEXT_ROOT", self.context_root))
        stack.enter_context(mock.patch.object(
            MODULE, "load_current_ratified_artifacts",
            side_effect=lambda: (copy.deepcopy(binding), copy.deepcopy(policy)),
        ))
        stack.enter_context(mock.patch.object(MODULE.WIRE, "load_breadth_context_source", return_value=None))
        if breadth is not None:
            stack.enter_context(mock.patch.object(
                MODULE.WIRE, "build_coverage_context_breadth", return_value=(breadth, "SYNTHETIC_FIXTURE"),
            ))
        self.provider = stack.enter_context(mock.patch.object(
            self.live, "fetch_index_family", side_effect=AssertionError("PROVIDER_CALL_FORBIDDEN"),
        ))
        self.http = stack.enter_context(mock.patch.object(
            self.live.PROBE, "_http_fetch", side_effect=AssertionError("PROVIDER_CALL_FORBIDDEN"),
        ))
        self.verify = stack.enter_context(mock.patch.object(
            self.live, "verify_existing_observation", wraps=self.live.verify_existing_observation,
        ))
        self.build = stack.enter_context(mock.patch.object(
            MODULE, "build_current_ratified_packet", wraps=MODULE.build_current_ratified_packet,
        ))
        stack.enter_context(mock.patch.object(MODULE.subprocess, "run", self.fake_git(commit_available)))
        return stack

    def connect(self, **changes):
        kwargs = {
            "source_commit": SYNTHETIC_SEED_COMMIT,
            "prior_seed_fetch_prior_date": SEED_FETCH_PRIORS[SEED_PRIOR],
            "current_seed_fetch_prior_date": SEED_FETCH_PRIORS[SEED_CURRENT],
        } | changes
        return MODULE.build_usable_seed_rotation_consumption(SEED_PRIOR, SEED_CURRENT, **kwargs)

    def available_breadth(self):
        market = FIXTURES.forward_live_market("a" * 64, SEED_CURRENT, "2026-08-20T18:00:00+09:00")
        return FIXTURES.breadth_context("AVAILABLE", True, kosdaq=market, kospi=market)

    def test_ready_seed_pair_connects_through_real_verifier_and_existing_consumer(self):
        breadth = self.available_breadth()
        pointer = ROOT / "data" / "latest_korea_rotation.json"
        pointer_before = pointer.read_bytes()
        seeds_before = {path: path.read_bytes() for path in self.context_root.rglob("packet.json")}
        with self.patched(breadth=breadth):
            receipt = self.connect()
        self.provider.assert_not_called()
        self.http.assert_not_called()
        self.assertEqual(
            [call.args for call in self.verify.call_args_list],
            [("20260814", "20260818"), ("20260819", "20260820")],
        )
        self.build.assert_called_once_with(SEED_PRIOR, SEED_CURRENT)
        self.assertEqual(receipt["seed_pair_readiness"], "READY")
        self.assertEqual(receipt["rotation"]["status"], "ROTATION_PACKET_AVAILABLE")
        direct_value = copy.deepcopy(self.value)
        direct_value["coverage_context"]["breadth"] = breadth
        direct = MODULE.KCR.build_packet(direct_value, self.policy)
        self.assertEqual(
            MODULE.KCR.canonical_json(receipt["rotation"]["packet"]), MODULE.KCR.canonical_json(direct)
        )
        for label, date, inner in (
            ("prior", SEED_PRIOR, self.value["prior_observation"]),
            ("current", SEED_CURRENT, self.value["current_observation"]),
        ):
            record = receipt["seeds"][label]
            raw = self.committed[self.relative(date)]
            self.assertEqual(record["availability"], "PINNED")
            self.assertEqual(record["source_commit"], SYNTHETIC_SEED_COMMIT)
            self.assertEqual(record["source_path"], self.relative(date))
            self.assertEqual(record["committed_file_sha256_observed"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(record["summary"]["payload_sha256"], json.loads(raw)["payload_sha256"])
            self.assertEqual(record["leadership"]["payload_sha256"], inner["payload_sha256"])
            self.assertEqual(receipt["rotation"]["packet"]["lineage"][f"{label}_upstream_packet_sha256"], inner["payload_sha256"])
        self.assertEqual(
            set(receipt["lineage"]["consumer_code_sha256"]),
            {"rotation/korea_capital_rotation.py", ".github/scripts/korea_capital_rotation_ledger_proof.py",
             ".github/scripts/korea_leadership_live_fetch.py"},
        )
        self.assertEqual(receipt.pop("payload_sha256"), MODULE.KCR.payload_sha256(receipt))
        self.assertEqual({path: path.read_bytes() for path in self.context_root.rglob("packet.json")}, seeds_before)
        self.assertEqual(pointer.read_bytes(), pointer_before)

    def test_blocked_or_unpinned_seed_is_not_consumed(self):
        current_path = self.context_root / SEED_CURRENT / "packet.json"
        current_raw = current_path.read_bytes()
        relative = self.relative(SEED_CURRENT)

        def blocked():
            self.write_seed(SEED_CURRENT, None, outcome="blocked")

        def unmaterialized():
            current_path.unlink()

        def local_only():
            self.committed.pop(relative)

        def absent():
            current_path.unlink()
            self.committed.pop(relative)

        def differs():
            self.committed[relative] = current_raw + b" "

        scenarios = (
            ("blocked", blocked, True, "PINNED", ["SEED_NOT_USABLE:outcome=blocked"]),
            ("unmaterialized", unmaterialized, True, "COMMITTED_NOT_MATERIALIZED", ["NATURAL_READBACK_UNAVAILABLE_MATERIALIZATION"]),
            ("local_only", local_only, True, "UNCOMMITTED_LOCAL_ONLY", ["SEED_OBSERVATION_NOT_COMMITTED_AT_SOURCE_COMMIT"]),
            ("absent", absent, True, "ABSENT_AT_SOURCE_COMMIT", ["SEED_OBSERVATION_ABSENT_AT_SOURCE_COMMIT"]),
            ("differs", differs, True, "LOCAL_BYTES_DIFFER_FROM_SOURCE_COMMIT", ["SEED_LOCAL_BYTES_DIFFER_FROM_SOURCE_COMMIT"]),
            ("commit_unavailable", lambda: None, False, "SOURCE_COMMIT_UNAVAILABLE",
             ["NATURAL_READBACK_UNAVAILABLE_MATERIALIZATION", "SEED_SOURCE_COMMIT_UNAVAILABLE"]),
        )
        for name, prepare, commit_available, availability, reasons in scenarios:
            with self.subTest(scenario=name):
                self.write_seed(SEED_CURRENT, self.value["current_observation"])
                current_raw = current_path.read_bytes()
                prepare()
                with self.patched(breadth=self.available_breadth(), commit_available=commit_available):
                    receipt = self.connect()
                self.build.assert_not_called()
                self.provider.assert_not_called()
                record = receipt["seeds"]["current"]
                self.assertEqual(record["availability"], availability)
                self.assertEqual(record["readiness"], "NOT_READY")
                self.assertEqual(record["reasons"], reasons)
                self.assertEqual(receipt["seed_pair_readiness"], "NOT_READY")
                self.assertEqual(receipt["rotation"]["status"], "WAIT_ROTATION_INPUT")
                self.assertIsNone(receipt["rotation"]["packet"])
                self.assertIn("USABLE_SEED_PAIR_NOT_READY", receipt["rotation"]["reasons"])
        # The unchanged default loader still refuses a blocked seed on its own.
        self.write_seed(SEED_CURRENT, None, outcome="blocked")
        with mock.patch.object(MODULE, "_leadership_context_root", return_value=self.context_root):
            with self.assertRaisesRegex(RuntimeError, "LEADERSHIP_NOT_POPULATED_FOR_DATE"):
                MODULE.load_real_leadership_packet(SEED_CURRENT)
            self.assertEqual(MODULE.load_real_leadership_packet(SEED_PRIOR), self.value["prior_observation"])

    def test_integrity_failures_raise_without_consumption(self):
        summary, path = self.write_seed(SEED_CURRENT, self.value["current_observation"])
        tampered = copy.deepcopy(summary)
        tampered["leadership_packet"]["relative_strength_observations"][2]["relative_strength_vs_benchmark"] = "0.9"
        self.live.write_json_atomic(path, tampered)
        self.committed[self.relative(SEED_CURRENT)] = path.read_bytes()
        with self.patched(breadth=self.available_breadth()):
            with self.assertRaisesRegex(self.live.LeadershipLiveFetchError, "HASH_MISMATCH"):
                self.connect()
        self.build.assert_not_called()
        self.write_seed(SEED_CURRENT, self.value["current_observation"])
        with self.patched(breadth=self.available_breadth()):
            with self.assertRaisesRegex(self.live.LeadershipLiveFetchError, "PRIOR_DATE_MISMATCH"):
                self.connect(current_seed_fetch_prior_date="2026-08-18")
            with self.assertRaisesRegex(RuntimeError, "SOURCE_COMMIT_MUST_BE_FULL_SHA"):
                self.connect(source_commit="main")
        self.build.assert_not_called()
        self.provider.assert_not_called()

    def test_ready_seeds_without_breadth_or_matching_policy_remain_not_ready(self):
        with self.patched():
            receipt = self.connect()
        self.assertEqual(receipt["seed_pair_readiness"], "READY")
        self.assertEqual(receipt["rotation"]["status"], "WAIT_ROTATION_INPUT")
        self.assertEqual(receipt["rotation"]["reasons"], ["ROTATION_BREADTH_NOT_AVAILABLE"])
        self.assertEqual(receipt["rotation"]["packet"]["coverage_context"]["breadth"]["status"], "UNKNOWN")
        foreign_binding = self.value["taxonomy_binding"] | {"upstream_leadership_policy_sha256": "f" * 64}
        foreign_policy = self.policy | {"upstream_leadership_policy_sha256": "f" * 64}
        with self.patched(breadth=self.available_breadth(), binding=foreign_binding, policy=foreign_policy):
            receipt = self.connect()
        self.assertIsNone(receipt["rotation"]["packet"])
        self.assertEqual(
            receipt["rotation"]["reasons"],
            ["ROTATION_PACKET_UNAVAILABLE", "RATIFIED_UPSTREAM_LEADERSHIP_POLICY_MISMATCH"],
        )
        pre_effective = self.policy | {"effective_from": "2026-08-19"}
        with self.patched(breadth=self.available_breadth(), policy=pre_effective):
            receipt = self.connect()
        self.assertFalse(receipt["rotation"]["policy_effective_for_pair"])
        self.assertEqual(receipt["rotation"]["packet"]["status"], "POLICY_NOT_EFFECTIVE")
        self.assertIn("POLICY_NOT_EFFECTIVE_FOR_OBSERVATION_PAIR", receipt["rotation"]["reasons"])

    def test_cli_usable_seed_options_are_opt_in_and_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "must-not-exist.json"
            base = [sys.executable, str(SCRIPT), "--prior-date", SEED_PRIOR, "--current-date", SEED_CURRENT]
            seed = ["--usable-seed-source-commit", SYNTHETIC_SEED_COMMIT,
                    "--prior-seed-fetch-prior-date", SEED_FETCH_PRIORS[SEED_PRIOR],
                    "--current-seed-fetch-prior-date", SEED_FETCH_PRIORS[SEED_CURRENT],
                    "--usable-seed-receipt-out", str(out)]
            for arguments in (
                base + seed,
                base + ["--current-ratified-policy"] + seed[:-2],
                base + ["--current-ratified-policy", "--commit-pointer"] + seed,
                base + ["--current-ratified-policy", "--packet-out", str(Path(tmp) / "packet.json")] + seed,
                base + ["--current-ratified-policy", "--evaluation-at", "2026-09-13T03:00:00Z"] + seed,
            ):
                with self.subTest(arguments=arguments[5:]):
                    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("usable-seed", result.stderr)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
