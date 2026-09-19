"""Rotation confirmation (#752) robustness: per-market build, committed packets preferred, push retry,
no post-session bars in record-only features, byte-identical replay of committed packets."""
from __future__ import annotations

import contextlib
import gzip
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


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OL = load_module("rotation_opportunity_ledger_for_robustness", ROOT / "rotation" / "rotation_opportunity_ledger.py")
RC = OL.RC
POLICY = RC.load_policy()
CONFIG = OL.load_config()
US = POLICY["markets"]["US"]["entities"]

# ── Who answers for a replay problem ──────────────────────────────────────────
# `RC.verify_market` / `OL.verify_market_days` report two different kinds of
# thing about the real repository, and they have two different owners:
#
#   MISMATCH:<path>         a day that IS committed replays to different bytes.
#   LATEST_MISMATCH:<path>  the latest pointer disagrees with the newest packet
#                           while that packet is committed.
#     -> code in a pull request can cause both, so a pull request answers for
#        them and they stay exactly as strict as before.
#
#   MISSING:<path>          a rebuilt day has no committed packet at all, and
#                           the latest pointer that necessarily trails it.
#     -> nothing a pull request contains can make this true or false; it asks
#        "did the producer's last run write its packets?". Asked on a
#        pull-request shard it turns every open branch red whenever a producer
#        run aborted -- including the branch that fixes the producer, which is
#        how #831 (the fix that lets the producer write the packet) came to be
#        blocked by the absence of the packet it would write.
#
# The two sibling regressions over the same committed evidence already draw the
# line this way, and neither asserts the missing-day direction:
#   test_rotation_confirmation.py::RetainedEvidenceReplayTests
#     ::test_committed_confirmation_packets_match_replay
#   test_rotation_opportunity_ledger.py::RetainedEvidenceTests
#     ::test_rebuild_is_deterministic_and_committed_days_match
# Both walk the *committed* packets and assert each one replays byte-identically.
#
# So the missing-day direction is not dropped, it moves behind an explicit
# declaration of the producer's own context -- the same shape run_all.py uses
# for its environment-scoped authoritative mode (`ATLAS_DISPOSABLE_CHECKOUT`,
# run_all.py:3180, "Actions workflow 가 이 값을 설정한다").
# `.github/workflows/rotation-confirmation.yml` sets it for its own gate step,
# which runs *after* the build loop, where a missing packet means "this run
# failed to write it" and is actionable by whoever is reading that run.
# `WorkflowTests.test_producer_gate_declares_and_runs_the_missing_day_check`
# below fails on every pull request if the workflow ever stops doing either, so
# the check cannot silently stop checking.
PRODUCER_GATE_ENV = "ATLAS_ROTATION_PRODUCER_GATE"
PRODUCER_GATE = os.environ.get(PRODUCER_GATE_ENV) == "1"


def split_replay_problems(problems: list, newest_day_committed: bool) -> tuple:
    """Split a verify_* problem list into (committed-day drift, uncommitted day)."""
    drift, uncommitted = [], []
    for problem in problems:
        producer_owned = problem.startswith("MISSING:") or (
            problem.startswith("LATEST_MISMATCH:") and not newest_day_committed)
        (uncommitted if producer_owned else drift).append(problem)
    return drift, uncommitted


def copy_contracts(root: Path) -> None:
    for relative in (
        RC.POLICY_RELATIVE_PATH, OL.CONFIG_RELATIVE_PATH, RC.STATE_MAPPING_RELATIVE_PATH,
        POLICY["ratification_record"]["repo_path"], CONFIG["entry_rule"]["repo_path"],
        POLICY["markets"]["KR"]["source"]["sector_policy_path"],
    ):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def us_manifest(root: Path, capture: str, session: str, ranking: list) -> None:
    order = list(ranking) + [s for s in US if s not in ranking]
    etfs = [{"symbol": s, "as_of_session_date": session, "available_session_count": 60,
             "relative_to_spy_pct": {"20_session_pct": str(20 - i)}} for i, s in enumerate(order)]
    write_json(root / "evidence/free_market_data/derived" / capture / "manifest.json",
               {"us_market_reference": {"sector_etfs": etfs, "payload_sha256": "0" * 64}})


def run_cli(module, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.run(argv)
    return code, out.getvalue(), err.getvalue()


class CommittedPacketsPreferredTests(unittest.TestCase):
    def test_selection_rule(self):
        obs = [{"as_of_date": d} for d in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-05")]
        kept, late = RC.select_append_observations(obs, set())
        self.assertEqual((len(kept), late), (4, []))  # fresh root: nothing excluded
        kept, late = RC.select_append_observations(obs, {"2026-09-01", "2026-09-03"})
        self.assertEqual(([o["as_of_date"] for o in kept], late), (["2026-09-01", "2026-09-03", "2026-09-05"], ["2026-09-02"]))

    def test_late_older_evidence_does_not_break_append_only_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_contracts(root)
            us_manifest(root, "2026-09-01", "2026-09-01", ["XLK"])
            us_manifest(root, "2026-09-02", "2026-09-02", ["XLK"])
            us_manifest(root, "2026-09-04", "2026-09-04", ["XLE"])
            first = RC.build_market("US", root, POLICY)
            RC.write_market("US", first, root)
            committed = {p.parent.name: p.read_bytes() for p in (root / RC.EVIDENCE_RELATIVE_ROOT / "US").glob("*/packet.json")}
            # older session evidence committed late (09-03); a full replay would change 09-04 and conflict
            us_manifest(root, "2026-09-05", "2026-09-03", ["XLE"])
            full = RC.build_market_packets(POLICY, "US", RC.EXTRACTORS["US"](POLICY, root), RC.load_state_mapping(root))
            with self.assertRaisesRegex(RC.RotationConfirmationError, "APPEND_ONLY_EVIDENCE_CONFLICT"):
                RC.write_market("US", full, root)
            self.assertEqual(RC.late_older_evidence_dates("US", root, POLICY), ["2026-09-03"])
            preferred = RC.build_market("US", root, POLICY)
            self.assertEqual([p["as_of_date"] for p in preferred], ["2026-09-01", "2026-09-02", "2026-09-04"])
            RC.write_market("US", preferred, root)
            self.assertEqual({p.parent.name: p.read_bytes() for p in (root / RC.EVIDENCE_RELATIVE_ROOT / "US").glob("*/packet.json")}, committed)
            self.assertEqual(RC.verify_market("US", preferred, root), [])
            # the exclusion is persisted as a notice file (no wall-clock field, rewritten identically)
            code, _out, err = run_cli(RC, ["build", "--market", "US", "--write", "--no-portal", "--root", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("::warning::late older US evidence not replayed: 2026-09-03", err)
            notice_path = RC.late_evidence_notice_path(root, "US")
            notice = json.loads(notice_path.read_text(encoding="utf-8"))
            self.assertEqual(notice["schema_version"], "rotation_confirmation_late_older_evidence/1")
            self.assertEqual([(i["as_of_date"], i["sources"][0]["path"]) for i in notice["late_older_evidence_not_replayed"]],
                             [("2026-09-03", "evidence/free_market_data/derived/2026-09-05/manifest.json")])
            first_bytes = notice_path.read_bytes()
            run_cli(RC, ["build", "--market", "US", "--write", "--no-portal", "--root", str(root)])
            self.assertEqual(notice_path.read_bytes(), first_bytes)
            self.assertFalse((root / RC.PORTAL_RELATIVE_PATH).exists())  # --no-portal
            # newer evidence still appends on top of the committed chain
            us_manifest(root, "2026-09-08", "2026-09-08", ["XLE"])
            appended = RC.build_market("US", root, POLICY)
            self.assertEqual([p["as_of_date"] for p in appended], ["2026-09-01", "2026-09-02", "2026-09-04", "2026-09-08"])
            self.assertEqual([RC.render_json(p) for p in appended[:3]], list(committed[d] for d in sorted(committed)))

    def test_cli_build_isolates_a_failing_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_contracts(root)
            us_manifest(root, "2026-09-01", "2026-09-01", ["XLK"])
            us_manifest(root, "2026-09-02", "2026-09-02", ["XLK"])
            # a malformed crypto source makes the CRYPTO build fail closed
            write_json(root / "data/observations/crypto_leadership/2026-09-02/packet.json", {"as_of_date": "2026-09-01"})
            code, out, err = run_cli(RC, ["build", "--market", "CRYPTO", "--market", "US", "--market", "KR", "--write", "--root", str(root)])
            self.assertEqual(code, 3)
            self.assertIn("Rotation confirmation failed for CRYPTO: CRYPTO_SOURCE_DATE_MISMATCH", err)
            self.assertTrue((root / RC.EVIDENCE_RELATIVE_ROOT / "US/2026-09-02/packet.json").exists())
            self.assertTrue((root / RC.PORTAL_RELATIVE_PATH).exists())
            self.assertIn('"late_older_evidence_not_replayed": []', out)
            self.assertEqual(run_cli(RC, ["verify", "--market", "US", "--root", str(root)])[0], 0)
            self.assertEqual(run_cli(RC, ["verify", "--market", "CRYPTO", "--root", str(root)])[0], 3)
            self.assertEqual(run_cli(RC, ["portal", "--root", str(root)])[0], 0)
            code, _out, err = run_cli(OL, ["verify", "--market", "US", "--market", "CRYPTO", "--root", str(root)])
            self.assertEqual(code, 3)
            self.assertIn("Opportunity ledger failed for CRYPTO", err)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/rotation-confirmation.yml").read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)
        self.steps = self.workflow["jobs"]["build"]["steps"]
        self.run = "\n".join(step.get("run", "") for step in self.steps)

    def _step_index(self, needle: str) -> int:
        return next(i for i, step in enumerate(self.steps) if needle in (step.get("run") or ""))

    def test_producer_gate_declares_and_runs_the_missing_day_check(self):
        """The missing-day assertion left the PR shard; this is what keeps it alive in the producer.

        If the workflow ever stops declaring the producer context, or stops
        running this file, nothing would assert that a rebuilt day has a
        committed packet -- so the absence fails here, where every PR sees it.
        """
        gate = next(i for i, step in enumerate(self.steps) if PRODUCER_GATE_ENV in (step.get("env") or {}))
        self.assertEqual(self.steps[gate]["env"][PRODUCER_GATE_ENV], "1")
        self.assertIn("python3 test/test_rotation_confirmation_robustness.py", self.steps[gate]["run"])
        # after the build loop: only a completed build can be asked whether it wrote every day
        self.assertLess(self._step_index("for market in US KR CRYPTO; do"), gate)
        self.assertEqual(self.steps[gate].get("if"), "always()")

    def test_whole_repo_regression_runs_after_the_per_market_loop(self):
        """These files assert over every market, so before the loop one market's drift aborted all three."""
        loop = self._step_index("for market in US KR CRYPTO; do")
        for name in ("test_rotation_confirmation.py", "test_rotation_opportunity_ledger.py",
                     "test_rotation_confirmation_robustness.py"):
            self.assertGreater(self._step_index(f"python3 test/{name}"), loop, name)

    def test_per_market_build_and_push_retry(self):
        self.assertIn("for market in US KR CRYPTO; do", self.run)
        for command in ("rotation/rotation_confirmation.py build --market \"$market\" --write --no-portal",
                        "rotation/rotation_confirmation.py verify --market \"$market\"",
                        "rotation/rotation_opportunity_ledger.py build --market \"$market\" --write",
                        "rotation/rotation_opportunity_ledger.py verify --market \"$market\"",
                        "rotation/rotation_confirmation.py portal --write"):
            self.assertIn(command, self.run)
        self.assertIn("for attempt in 1 2 3 4 5; do", self.run)
        self.assertIn("git pull --rebase origin main && git push origin HEAD:main", self.run)
        self.assertIn("git rebase --abort", self.run)
        self.assertLess(self.run.index("git push origin HEAD:main"), self.run.index('if [ -n "$failed" ]'))

    def test_portal_failure_does_not_block_market_commit_and_notices_are_kept(self):
        portal = self.run.index("if python3 rotation/rotation_confirmation.py portal --write; then")
        self.assertIn("portal_failed=1", self.run)
        self.assertLess(portal, self.run.index('git commit -m "data: rotation confirmation'))
        self.assertLess(self.run.index("git push origin HEAD:main"), self.run.index('if [ "$portal_failed" -ne 0 ]'))
        self.assertIn('notice="data/rotation_confirmation_late_older_evidence_$lower.json"', self.run)
        self.assertLess(self.run.index('git add -- "$notice"'), self.run.index('git commit -m "data: rotation confirmation'))
        # the per-market clean-up of a failed market never touches the notice file
        loop = self.run[self.run.index("for market in US KR CRYPTO; do"):self.run.index("done")]
        self.assertNotIn("late_older_evidence", loop)

    def test_no_cron_no_secret_and_pinned_sources_untouched(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertNotIn("schedule", triggers)
        self.assertNotIn("secrets.", self.text)
        for name in ("free-market-data.yml", "crypto-breadth-capture.yml", "korea-leadership-live-proof.yml"):
            self.assertNotIn("rotation_confirmation", (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


class PostSessionBarLeakTests(unittest.TestCase):
    """Record-only features for session d never read a bar dated after d, even from the same capture file."""

    def _capture(self, root: Path, capture: str, sessions: list, extreme_after: bool):
        responses = {}
        for symbol in US:
            bars = [{"t": f"{d}T04:00:00Z", "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100 + i, "v": 1}
                    for i, d in enumerate(sessions)]
            if extreme_after:
                bars.append({"t": "2026-09-03T04:00:00Z", "o": 1, "h": 100000, "l": 0.5, "c": 99999, "v": 1})
            responses[symbol] = {"symbol": symbol, "bars": bars}
        path = root / "evidence/free_market_data/raw" / capture / "alpaca_iex_daily_bars.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump({"responses": responses}, handle)

    def test_features_ignore_post_session_bars_in_capture(self):
        import datetime as dt
        sessions = [(dt.date(2026, 9, 2) - dt.timedelta(days=29 - i)).isoformat() for i in range(30)]
        results = {}
        for leak in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                copy_contracts(root)
                self._capture(root, "2026-09-03", sessions, extreme_after=leak)
                features = OL.USBars(OL.load_config(root), root).features("XLK", "2026-09-02")
                features.pop("source")
                results[leak] = features
        self.assertEqual(results[False]["status"], "OBSERVED")
        self.assertEqual(results[True], results[False])
        self.assertEqual(results[True]["bars_used"], 30)


class ReplayByteIdentityTests(unittest.TestCase):
    def _replay(self, market: str) -> tuple:
        """(committed-day drift, uncommitted-day) replay problems over the real repository."""
        packets = RC.build_market(market, ROOT, POLICY)
        days = OL.build_market_days(market, ROOT, CONFIG, POLICY)
        newest_committed = not packets or RC.evidence_path(ROOT, market, packets[-1]["as_of_date"]).exists()
        confirmation = split_replay_problems(RC.verify_market(market, packets, ROOT), newest_committed)
        ledger = split_replay_problems(OL.verify_market_days(market, days, ROOT), newest_committed)
        return confirmation[0] + ledger[0], confirmation[1] + ledger[1]

    def test_committed_packets_and_portal_replay_byte_identical(self):
        """A committed packet or ledger day that replays to different bytes fails here, on every PR."""
        for market in RC.MARKETS:
            self.assertEqual(RC.late_older_evidence_dates(market, ROOT, POLICY), [], market)
            self.assertEqual(self._replay(market)[0], [], market)
        self.assertEqual(run_cli(RC, ["portal", "--root", str(ROOT)])[0], 0)

    @unittest.skipUnless(PRODUCER_GATE, f"producer-only: {PRODUCER_GATE_ENV}=1 declares the producer's own run")
    def test_producer_gate_every_rebuilt_day_has_a_committed_packet(self):
        """After the producer's build loop, a rebuilt day with no packet means this run failed to write it."""
        for market in RC.MARKETS:
            self.assertEqual(self._replay(market)[1], [], market)


if __name__ == "__main__":
    unittest.main(verbosity=2)
