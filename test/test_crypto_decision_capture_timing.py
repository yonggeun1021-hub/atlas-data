#!/usr/bin/env python3
"""Crypto PAPER decision capture-to-decision timing regressions (2026-09-14).

The decision snapshot re-evaluates the RATIFIED CRYPTO freshness policy
(20s provider age / 3s transport delay) at its own ``generated_at``.  The
scheduler used to run a ~30s decision-isolated validation capture between the
realtime capture and the decision step, so natural packets 2026-09-13
1447/1814/2112 observed every ticker >= 30s old and were STALE 8/8 even when
BTC/ETH/XRP were 0-1s old at capture.

These tests pin the ordering fix without touching any ratified threshold:

* the decision step directly follows the realtime capture step, and the
  validation capture runs after the whole decision chain;
* the capture-to-decision guard measures the gap and fails over its
  engineering budget (a scheduler budget, never a freshness policy);
* the ratified policy file still carries 20s/3s for CRYPTO;
* a natural replay shows the committed 30s gap is over budget while an
  in-budget gap keeps liquid markets FRESH under the unchanged policy.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "upbit-realtime-capture.yml"
GUARD_PATH = ROOT / ".github" / "scripts" / "check_crypto_decision_capture_gap.py"
RATIFIED_POLICY_PATH = ROOT / "config" / "upbit_realtime_freshness_policy_ratified.json"
NATURAL_RUN_PATH = ROOT / "evidence" / "crypto" / "upbit" / "realtime" / "2026-09-13" / "run_008.json"
NATURAL_DECISION_GLOB = "evidence/crypto_paper_decision/2026-09-13/2112/*/packet.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GUARD = _load("check_crypto_decision_capture_gap_under_test", GUARD_PATH)
GATE = _load("upbit_realtime_gate_capture_timing", ROOT / "realtime" / "upbit_realtime_gate.py")


def _steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]["capture"]["steps"]


def _index(steps: list[dict], step_id: str) -> int:
    matches = [i for i, step in enumerate(steps) if step.get("id") == step_id]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one step id={step_id}, got {matches}")
    return matches[0]


def _utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stamp(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class WorkflowOrderingTests(unittest.TestCase):
    def test_decision_step_directly_follows_realtime_capture(self):
        steps = _steps()
        capture = _index(steps, "capture")
        decision = _index(steps, "crypto_paper_decision")
        self.assertEqual(decision, capture + 1)

    def test_validation_capture_runs_after_the_whole_decision_chain(self):
        steps = _steps()
        validation = _index(steps, "validation_capture")
        for consumer in (
            "crypto_paper_decision",
            "crypto_axis_trade_bridge",
            "crypto_axis_trade_bridge_explanation",
            "crypto_funnel_briefing",
            "crypto_candidate_detail",
            "crypto_decision_capture_gap",
        ):
            self.assertLess(_index(steps, consumer), validation, consumer)
        telemetry = [i for i, s in enumerate(steps) if s.get("name") == "Record P9-06 realtime scheduler telemetry"]
        self.assertEqual(len(telemetry), 1)
        self.assertLess(validation, telemetry[0])

    def test_validation_capture_is_independent_of_decision_outcome(self):
        step = _steps()[_index(_steps(), "validation_capture")]
        condition = step.get("if", "")
        self.assertIn("!cancelled()", condition)
        self.assertIn("steps.capture.outcome == 'success'", condition)
        self.assertNotIn("crypto_paper_decision", condition)
        self.assertIn("--evidence-root evidence/crypto/upbit/realtime_validation", step["run"])
        self.assertIn("validation_duration_seconds", step["env"]["DURATION_SECONDS"])

    def test_no_step_between_capture_and_decision_can_consume_wall_clock(self):
        steps = _steps()
        capture = _index(steps, "capture")
        decision = _index(steps, "crypto_paper_decision")
        self.assertEqual(steps[capture + 1 : decision], [])
        run = steps[decision]["run"]
        # generated_at is the step's first clock read, before any Python work.
        self.assertLess(run.index('GENERATED_AT="$(date -u'), run.index("python3 decision/"))
        self.assertIn('echo "generated_at=$GENERATED_AT" >> "$GITHUB_OUTPUT"', run)
        self.assertIn('echo "realtime_run_path=$RUN_PATH" >> "$GITHUB_OUTPUT"', run)

    def test_timing_guard_is_wired_after_consumers_and_recorded(self):
        steps = _steps()
        guard = steps[_index(steps, "crypto_decision_capture_gap")]
        self.assertIn("!cancelled()", guard["if"])
        self.assertIn("steps.crypto_paper_decision.outputs.realtime_run_path", guard["if"])
        self.assertEqual(
            guard["env"]["DECISION_GENERATED_AT"],
            "${{ steps.crypto_paper_decision.outputs.generated_at }}",
        )
        self.assertIn("python3 .github/scripts/check_crypto_decision_capture_gap.py", guard["run"])
        telemetry = next(s for s in steps if s.get("name") == "Record P9-06 realtime scheduler telemetry")
        for key in (
            "ATLAS_CRYPTO_DECISION_CAPTURE_GAP_STEP_OUTCOME",
            "ATLAS_CRYPTO_DECISION_CAPTURE_GAP_SECONDS",
            "ATLAS_CRYPTO_DECISION_CAPTURE_GAP_ENGINEERING_BUDGET_SECONDS",
        ):
            self.assertIn(key, telemetry["env"])


    def test_telemetry_and_evidence_commit_steps_always_run(self):
        steps = _steps()
        for name in (
            "Record P9-06 realtime scheduler telemetry",
            "Commit append-only realtime evidence and run telemetry",
        ):
            matches = [s for s in steps if s.get("name") == name]
            self.assertEqual(len(matches), 1, name)
            self.assertEqual(matches[0].get("if"), "always()", name)
        guard = _index(steps, "crypto_decision_capture_gap")
        validation = _index(steps, "validation_capture")
        telemetry = next(i for i, s in enumerate(steps) if s.get("name") == "Record P9-06 realtime scheduler telemetry")
        commit = next(i for i, s in enumerate(steps) if s.get("name") == "Commit append-only realtime evidence and run telemetry")
        self.assertLess(guard, telemetry)
        self.assertLess(validation, telemetry)
        self.assertLess(telemetry, commit)
        self.assertEqual(commit, len(steps) - 1)


class RatifiedPolicyUntouchedTests(unittest.TestCase):
    def test_engineering_budget_is_pinned_to_exactly_five_seconds(self):
        self.assertIs(type(GUARD.ENGINEERING_BUDGET_SECONDS), int)
        self.assertEqual(GUARD.ENGINEERING_BUDGET_SECONDS, 5)

    def test_ratified_crypto_limits_are_unchanged(self):
        policy = json.loads(RATIFIED_POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(policy["policy_id"], "P9_06_UPBIT_CRYPTO_PAPER_V1")
        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(policy["max_provider_age_seconds_by_market"]["CRYPTO"], 20)
        self.assertEqual(policy["max_transport_delay_seconds_by_market"]["CRYPTO"], 3)
        self.assertNotIn("engineering", json.dumps(policy).lower())

    def test_engineering_budget_is_well_under_policy_and_not_a_policy_input(self):
        policy = json.loads(RATIFIED_POLICY_PATH.read_text(encoding="utf-8"))
        budget = GUARD.ENGINEERING_BUDGET_SECONDS
        self.assertIsInstance(budget, int)
        self.assertGreater(budget, 0)
        self.assertLessEqual(budget * 2, policy["max_provider_age_seconds_by_market"]["CRYPTO"])
        self.assertIn("ENGINEERING BUDGET, NOT POLICY", GUARD.__doc__)
        for relative in (
            "decision/crypto_paper_decision_snapshot.py",
            "realtime/upbit_realtime_gate.py",
            "execution/intraday_freshness.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("check_crypto_decision_capture_gap", source, relative)
            self.assertNotIn("ENGINEERING_BUDGET_SECONDS", source, relative)


class GuardUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="capture_gap_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_path = self.tmp / "run_001.json"
        self.run_path.write_text(
            json.dumps({"run": {"status": {"generated_at": "2026-09-14T00:10:00Z"}}}),
            encoding="utf-8",
        )

    def _main(self, *argv: str) -> tuple[int, dict, str]:
        output = self.tmp / "github_output"
        output.write_text("", encoding="utf-8")
        stdout = io.StringIO()
        previous = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = str(output)
        try:
            with contextlib.redirect_stdout(stdout):
                code = GUARD.main(list(argv))
        finally:
            if previous is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = previous
        return code, json.loads(stdout.getvalue()), output.read_text(encoding="utf-8")

    def test_in_budget_gap_passes_and_is_reported(self):
        code, result, output = self._main(
            "--realtime-run-path", str(self.run_path),
            "--decision-generated-at", "2026-09-14T00:10:01Z",
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["gap_seconds"], 1)
        self.assertEqual(result["status"], GUARD.WITHIN_BUDGET)
        self.assertIn("gap_seconds=1\n", output)
        self.assertIn(f"engineering_budget_seconds={GUARD.ENGINEERING_BUDGET_SECONDS}\n", output)

    def test_budget_boundary_is_inclusive(self):
        at = _utc("2026-09-14T00:10:00Z") + dt.timedelta(seconds=GUARD.ENGINEERING_BUDGET_SECONDS)
        code, result, _ = self._main(
            "--realtime-run-path", str(self.run_path), "--decision-generated-at", _stamp(at),
        )
        self.assertEqual((code, result["status"]), (0, GUARD.WITHIN_BUDGET))
        code, result, _ = self._main(
            "--realtime-run-path", str(self.run_path),
            "--decision-generated-at", _stamp(at + dt.timedelta(seconds=1)),
        )
        self.assertEqual((code, result["status"]), (1, GUARD.OVER_BUDGET))

    def test_old_thirty_second_validation_gap_fails(self):
        code, result, output = self._main(
            "--realtime-run-path", str(self.run_path),
            "--decision-generated-at", "2026-09-14T00:10:30Z",
        )
        self.assertEqual(code, 1)
        self.assertEqual(result["gap_seconds"], 30)
        self.assertIn("status=OVER_BUDGET\n", output)

    def test_invalid_inputs_fail_closed(self):
        cases = [
            ("--decision-generated-at", "2026-09-14T00:09:59Z"),  # precedes capture
            ("--decision-generated-at", "2026-09-14 00:10:01"),  # not canonical
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                code, result, _ = self._main("--realtime-run-path", str(self.run_path), *extra)
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "INVALID")
        code, result, _ = self._main(
            "--realtime-run-path", str(self.tmp / "missing.json"),
            "--decision-generated-at", "2026-09-14T00:10:01Z",
        )
        self.assertEqual((code, result["reason"]), (2, "REALTIME_RUN_UNREADABLE"))

    def test_decision_packet_generated_at_must_match(self):
        packet = self.tmp / "packet.json"
        packet.write_text(json.dumps({"generated_at": "2026-09-14T00:10:02Z"}), encoding="utf-8")
        code, result, _ = self._main(
            "--realtime-run-path", str(self.run_path),
            "--decision-generated-at", "2026-09-14T00:10:01Z",
            "--decision-packet", str(packet),
        )
        self.assertEqual((code, result["reason"]), (2, "DECISION_PACKET_GENERATED_AT_MISMATCH"))
        code, result, _ = self._main(
            "--realtime-run-path", str(self.run_path),
            "--decision-generated-at", "2026-09-14T00:10:02Z",
            "--decision-packet", str(packet),
        )
        self.assertEqual((code, result["gap_seconds"]), (0, 2))


class NaturalReplayTests(unittest.TestCase):
    """2026-09-13 21:12 run_008 and its committed decision packet."""

    def setUp(self):
        packets = sorted(ROOT.glob(NATURAL_DECISION_GLOB))
        self.assertEqual(len(packets), 1)
        self.packet_path = packets[0]
        self.packet = json.loads(self.packet_path.read_text(encoding="utf-8"))
        self.run = json.loads(NATURAL_RUN_PATH.read_text(encoding="utf-8"))["run"]
        self.capture_at = _utc(self.run["status"]["generated_at"])

    def _statuses(self, observed_at: dt.datetime) -> dict:
        rows = []
        for item in self.run["latest_public_messages"].values():
            if item.get("kind") != "ticker":
                continue
            rows.append(GATE.quote_row_from_ticker(
                GATE.parse_message(item["raw"]), received_at=_utc(item["received_at"]),
            ))
        result = GATE.evaluate_with_ratified_freshness_policy(
            rows,
            observed_at=observed_at,
            batch_id=f"P9_06_{observed_at.strftime('%Y%m%dT%H%M%SZ')}",
            contract=GATE.load_contract(),
        )
        self.assertEqual(result["status"], "EVALUATED")
        return {
            row["asset_id"].rsplit(".", 1)[-1]: row["freshness_status"]
            for row in result["result"]["results"]
        }

    def test_committed_packet_gap_was_the_thirty_second_validation_window(self):
        measured = GUARD.measure(
            realtime_run_path=NATURAL_RUN_PATH,
            decision_generated_at=self.packet["generated_at"],
            decision_packet_path=self.packet_path,
        )
        self.assertEqual(measured["gap_seconds"], 30)
        self.assertEqual(measured["status"], GUARD.OVER_BUDGET)
        self.assertEqual(self.packet["freshness_status"]["realtime"], "STALE")

    def test_thirty_second_gap_makes_every_ticker_stale(self):
        statuses = self._statuses(_utc(self.packet["generated_at"]))
        self.assertEqual(len(statuses), 8)
        self.assertEqual(set(statuses.values()), {"STALE"})

    def test_in_budget_gap_keeps_liquid_markets_fresh_under_unchanged_policy(self):
        observed_at = self.capture_at + dt.timedelta(seconds=GUARD.ENGINEERING_BUDGET_SECONDS)
        statuses = self._statuses(observed_at)
        for market in ("KRW-BTC", "KRW-XRP", "KRW-SOL", "KRW-SUI"):
            self.assertEqual(statuses[market], "FRESH", market)
        # Thin-market staleness is a separate, reported (not fixed) issue: the
        # provider time is trade_timestamp, so a market with no recent trade
        # stays STALE regardless of pipeline timing.
        for market in ("KRW-SHIB", "KRW-WLD"):
            self.assertEqual(statuses[market], "STALE", market)


if __name__ == "__main__":
    unittest.main()
