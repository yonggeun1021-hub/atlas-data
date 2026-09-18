#!/usr/bin/env python3
"""Daily scheduled run for the KR/US population symbol observation (2026-09-18).

Before this change the two producers had no ``.github/workflows`` trigger at
all, so every assertion here failed.  Offline structural + behavioural checks
only: no provider call, no tracked-file mutation, no clock dependency.

What is actually being protected:

1. the schedule exists, at the intended UTC slots, with a backup slot;
2. a ``workflow_dispatch`` run enforces the SAME guards as a scheduled run --
   there is no dispatch input to vary and no step keyed on ``github.event_name``,
   so the server-side dispatcher may be registered against it;
3. a repeat run for an already-captured date is skipped (``verified_existing``)
   rather than rewriting the committed packet or failing -- the real hazard,
   because ``persist_packet`` silently supersedes a packet whose rolling inputs
   moved;
4. no pass rule was introduced: ``passed_count`` stays 0 with unchanged
   semantics and every authority flag stays false.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "population-symbol-observation-daily.yml"
SCRIPT = ROOT / ".github" / "scripts" / "population_symbol_observation_daily.py"

PRIMARY_CRON = "20 15 * * *"
BACKUP_CRON = "20 20 * * *"


def _load_script():
    spec = importlib.util.spec_from_file_location("population_symbol_observation_daily", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_script()
CORE = MODULE.CORE


class ScheduleTest(unittest.TestCase):
    """(1) The schedule exists and is the intended UTC time."""

    def setUp(self):
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        # PyYAML parses a bare `on:` key as the boolean True.
        self.triggers = self.workflow.get("on", self.workflow.get(True))

    def test_daily_schedule_with_a_backup_slot_exists(self):
        crons = [entry["cron"] for entry in self.triggers["schedule"]]
        self.assertEqual(crons, [PRIMARY_CRON, BACKUP_CRON])
        # Every day, not weekday-restricted: the two markets' sources land on
        # different weekday offsets, and a day with no new source is a cheap
        # no-op rather than a missed capture.
        for cron in crons:
            self.assertTrue(cron.endswith("* * *"), cron)

    def test_primary_slot_is_after_the_latest_observed_source_landing(self):
        """14:18Z is the latest observed krx_global_universe landing; the
        primary slot must be after it, and the backup must clear the observed
        ~4.5h scheduler lateness of that primary."""
        def minutes(cron: str) -> int:
            minute, hour = cron.split()[0], cron.split()[1]
            return int(hour) * 60 + int(minute)

        latest_source_landing = 14 * 60 + 18
        self.assertGreater(minutes(PRIMARY_CRON), latest_source_landing)
        self.assertGreaterEqual(minutes(BACKUP_CRON) - minutes(PRIMARY_CRON), 4 * 60 + 30)

    def test_schedule_does_not_collide_with_another_workflow_slot(self):
        mine = {PRIMARY_CRON, BACKUP_CRON}
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            if path == WORKFLOW:
                continue
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            triggers = document.get("on", document.get(True)) if isinstance(document, dict) else None
            if not isinstance(triggers, dict):
                continue
            schedule = triggers.get("schedule") or []
            for entry in schedule:
                self.assertNotIn(entry.get("cron"), mine, f"slot collision with {path.name}")


class DispatchGuardEquivalenceTest(unittest.TestCase):
    """(2) A dispatched run enforces the same guard as a scheduled one.

    This is the precondition for registering the workflow with the server-side
    dispatcher that catches missed slots.  ``stablecoin-capture.yml`` is
    excluded from that dispatcher precisely because its dispatch path is not
    guard-equivalent, so the property is asserted structurally here rather than
    trusted.
    """

    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)
        self.triggers = self.workflow.get("on", self.workflow.get(True))
        self.jobs = self.workflow["jobs"]

    def test_workflow_dispatch_exists_alongside_the_schedule(self):
        self.assertIn("workflow_dispatch", self.triggers)
        self.assertIn("schedule", self.triggers)

    def test_dispatch_declares_no_inputs_so_nothing_can_be_varied(self):
        # `workflow_dispatch:` with an empty body parses as None. Any inputs
        # block would be a knob a dispatched run could turn that a scheduled
        # run cannot -- exactly the asymmetry we must not ship.
        self.assertIsNone(self.triggers["workflow_dispatch"])

    def test_no_step_or_job_is_conditioned_on_the_event_name(self):
        # Scan executable YAML only: the file's comments discuss event_name in
        # order to explain why nothing branches on it.
        executable = "\n".join(
            line for line in self.text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("github.event_name", executable)
        self.assertNotIn("github.event.schedule", executable)
        self.assertNotIn("inputs.", executable)
        for name, job in self.jobs.items():
            self.assertNotIn("if", job, f"job {name} is conditional")
            for step in job["steps"]:
                self.assertNotIn("if", step, f"step {step.get('name')} is conditional")

    def test_single_job_runs_every_guard_in_one_ordered_path(self):
        self.assertEqual(list(self.jobs), ["observe"])
        steps = self.jobs["observe"]["steps"]
        names = [step.get("name") for step in steps if step.get("name")]
        # The regression gate, the producer, the outside-scope refusal and the
        # commit are all on the one path every trigger takes.
        self.assertEqual(names, [
            "Offline contract regression for the producer and this schedule",
            "Observe KR and US population (committed evidence only)",
            "Refuse any change outside the two observation roots",
            "Commit append-only observation (no-op when already captured)",
        ])

    def test_checkout_uses_the_run_time_branch_not_the_stale_event_sha(self):
        checkout = self.jobs["observe"]["steps"][0]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.repository.default_branch }}")


class NoCollectionAddedTest(unittest.TestCase):
    """Collection targets and sources are unchanged: this path adds none."""

    def test_workflow_makes_no_provider_call_and_uploads_nothing(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for forbidden in ("secrets.", "curl", "wget", "upload-artifact", "KRX_API_KEY", "requests"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_producer_script_has_no_network_import(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("import requests", "import urllib", "http.client", "socket"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_work_dir_must_be_outside_the_repository(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('--work-dir "$RUNNER_TEMP/population-observation"', text)
        with self.assertRaises(SystemExit):
            MODULE.main(["--generated-at", "2026-09-18T15:20:00Z",
                         "--work-dir", str(ROOT / "data" / "nope")])


class AlreadyCapturedSkipTest(unittest.TestCase):
    """(3) A repeat run for an already-captured date neither rewrites nor fails.

    Uses the repository's own committed packets, copied into a temp tree, so the
    test never touches tracked files.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pop_obs_sched_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _committed_dirs(self):
        found = {}
        for market in MODULE.MARKETS:
            base = CORE.DEFAULT_OUTPUT_ROOTS[market]
            dated = [d for d in sorted(base.iterdir()) if (d / "packet.json.gz").is_file() or (d / "packet.json").is_file()] if base.is_dir() else []
            if dated:
                found[market] = dated[-1]
        return found

    def test_repository_has_a_committed_packet_to_reverify(self):
        self.assertTrue(self._committed_dirs(), "no committed observation packet on disk")

    def test_already_captured_date_reports_verified_existing_and_rewrites_nothing(self):
        """Pin the resolved session onto an already-committed date.

        The resolved session is whatever the committed sources currently say
        (US is at 2026-09-16 while the newest committed observation is
        2026-09-11, i.e. genuinely not captured yet), so the skip path is
        exercised by pinning the date rather than by waiting for the two to
        coincide.  Pinning also keeps this test from ever building -- a test
        must not write a real observation into the repository.
        """
        original = MODULE.resolve_session_date
        self.addCleanup(setattr, MODULE, "resolve_session_date", original)

        for market, dated in self._committed_dirs().items():
            with self.subTest(market=market):
                self.assertTrue(MODULE.already_captured(dated))
                target = CORE._packet_target(dated)
                before = target.read_bytes()
                sidecar = (dated / "summary.json").read_bytes()

                MODULE.resolve_session_date = lambda m, root=None, _d=dated.name: _d
                record = MODULE.observe(market, generated_at="2026-09-18T15:20:00Z")

                self.assertEqual(record["outcome"], "verified_existing")
                self.assertTrue(record["already_captured"])
                self.assertFalse(record["wrote_anything"])
                self.assertEqual(record["session_date"], dated.name)
                # The committed bytes are untouched -- the whole point.
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual((dated / "summary.json").read_bytes(), sidecar)

    def test_guard_prevents_the_supersede_that_would_rewrite_committed_evidence(self):
        """persist_packet() overwrites a packet whose generation_id changed.

        The guard is what stands between that behaviour and committed evidence,
        so assert the hazard is real: persist into a dir that already holds a
        different generation and it is superseded, whereas observe() never
        reaches persist for an already-captured date.
        """
        dirs = self._committed_dirs()
        self.assertTrue(dirs)
        market, dated = next(iter(dirs.items()))
        packet = CORE.read_packet_file(CORE._packet_target(dated))

        staged = self.root / dated.name
        staged.mkdir(parents=True)
        shutil.copy2(CORE._packet_target(dated), staged / "packet.json.gz")
        shutil.copy2(dated / "summary.json", staged / "summary.json")

        mutated = json.loads(json.dumps(packet))
        mutated["generation_id"] = "0" * 64
        outcome = CORE.persist_packet(mutated, staged, compress=True)["outcome"]
        self.assertEqual(outcome, "superseded_generation")
        # And the committed copy was never a candidate for that, because the
        # guard short-circuits before build()/persist_packet().
        self.assertTrue(MODULE.already_captured(dated))


class PersistPacketSupersedeHazardTest(unittest.TestCase):
    """The supersede hazard itself, pinned -- not merely this caller's avoidance.

    ``persist_packet()`` overwrites an existing packet whenever the incoming
    ``generation_id`` differs, and the generation hashes rolling inputs (stage
    history, the bounded review pointer, the KRX watchlist).  For an output
    directory that is committed evidence, that is an append-only violation
    waiting for a scheduler to arm it.

    ``.github/scripts/population_symbol_observation_daily.py`` refuses to reach
    ``build()``/``persist_packet()`` for an already-captured date, so the
    scheduled path is safe.  But the hazard lives in the shared producer, so
    every *other* caller inherits it.  These tests pin it two ways:

    1. the behaviour is characterised explicitly, so it cannot change silently;
    2. the set of production callers is pinned, so a new unguarded caller fails
       loudly instead of quietly rewriting committed packets.

    If ``persist_packet()`` is later fixed (see the PR body's named follow-up),
    test 1 will fail -- that is the intended signal.  Update it to assert the
    refusal; do not delete it.
    """

    #: Non-test files allowed to reach persist_packet()/build(write=True).
    #: decision/population_symbol_observation.py defines both; the daily script
    #: is guarded by its already-captured check.
    GUARDED_PRODUCTION_CALLERS = {
        "decision/population_symbol_observation.py",
        ".github/scripts/population_symbol_observation_daily.py",
    }

    def test_hazard_is_live_persist_packet_overwrites_a_same_date_packet(self):
        """Characterise the hazard: same directory, different generation, overwritten.

        Uses a temp directory -- the hazard is demonstrated, never inflicted on
        committed evidence.
        """
        with tempfile.TemporaryDirectory(prefix="pop_obs_hazard_") as tmp:
            staged = Path(tmp) / "2026-09-11"
            staged.mkdir(parents=True)

            market = "US"
            base = CORE.DEFAULT_OUTPUT_ROOTS[market]
            committed = [d for d in sorted(base.iterdir())
                         if CORE._packet_target(d) is not None] if base.is_dir() else []
            if not committed:
                self.skipTest("no committed packet on disk to characterise the hazard with")
            packet = CORE.read_packet_file(CORE._packet_target(committed[-1]))

            first = CORE.persist_packet(packet, staged, compress=True)
            self.assertEqual(first["outcome"], "populated")
            original_bytes = (staged / "packet.json.gz").read_bytes()

            # Same date, same directory, only the generation differs -- exactly
            # what a rolling-pointer move produces.
            mutated = json.loads(json.dumps(packet))
            mutated["generation_id"] = "1" * 64
            second = CORE.persist_packet(mutated, staged, compress=True)

            self.assertEqual(
                second["outcome"], "superseded_generation",
                "persist_packet() no longer supersedes -- if it now refuses, the hazard is "
                "fixed: update this test to assert the refusal rather than deleting it")
            # The overwrite is real, not just a label.
            self.assertNotEqual((staged / "packet.json.gz").read_bytes(), original_bytes)

    def test_production_callers_of_the_supersede_path_are_pinned(self):
        """A new unguarded caller of persist_packet()/build() must fail loudly."""
        callers = set()
        for path in sorted(ROOT.rglob("*.py")):
            relative = path.relative_to(ROOT)
            if relative.parts[0] in {"test", "evidence", "data", ".git", "outputs"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            # Only count real call sites, not prose in docstrings/comments.
            executable = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#"))
            if "persist_packet(" in executable or "CORE.build(" in executable:
                callers.add(relative.as_posix())
        self.assertEqual(
            callers, self.GUARDED_PRODUCTION_CALLERS,
            "a production caller of the supersede path changed; every caller writing into a "
            "committed observation root must first check already_captured()")

    def test_the_daily_script_checks_already_captured_before_building(self):
        """The guard is ordered: the check precedes the build, in source order."""
        text = SCRIPT.read_text(encoding="utf-8")
        body = text.split("def observe(", 1)[1]
        self.assertLess(body.index("already_captured("), body.index("CORE.build("),
                        "observe() must test already_captured() before it builds")


class WatchdogSpecTruthfulnessTest(unittest.TestCase):
    """A producer spec must not claim "no trigger exists" once one does.

    ``watchdog/daily_producer_freshness.py`` hardcodes, per producer, a
    ``workflow`` description and a ``calendar``.  Two of its specs say
    ``"-- no .github/workflows trigger exists"`` and carry ``NO_SCHEDULE``,
    which was true when it was written and becomes false the moment this
    workflow lands.  Nothing else in the repository would catch that, so the
    watchdog would quietly report a producer as unscheduled while a cron drives
    it daily.

    The watchdog currently lives on an unmerged branch, so this test skips with
    a named reason rather than passing vacuously -- whichever change lands
    second then breaks loudly instead of lying.
    """

    WATCHDOG = ROOT / "watchdog" / "daily_producer_freshness.py"
    NO_TRIGGER_CLAIM = "no .github/workflows trigger exists"

    def setUp(self):
        if not self.WATCHDOG.is_file():
            self.skipTest(
                "watchdog/daily_producer_freshness.py is not present on this branch "
                "(it is unmerged work on claude/daily-producer-watchdog); this check "
                "activates automatically once that file lands")

    def _specs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("daily_producer_freshness", self.WATCHDOG)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.default_watchlist()

    @staticmethod
    def _workflow_bodies():
        """Executable YAML of every workflow, comments stripped."""
        bodies = {}
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            bodies[path.name] = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#"))
        return bodies

    def test_no_spec_claims_no_trigger_while_a_workflow_drives_it(self):
        bodies = self._workflow_bodies()
        for spec in self._specs():
            claim = str(spec.get("workflow") or "")
            if self.NO_TRIGGER_CLAIM not in claim:
                continue
            # The producer's own committed output root: a workflow that stages
            # that root is, by construction, running this producer.
            target = spec.get("glob") or spec.get("path") or ""
            root_path = str(target).split("/*", 1)[0]
            self.assertTrue(root_path, f"spec {spec['id']} has no glob/path to check")
            driving = sorted(name for name, body in bodies.items() if root_path in body)
            self.assertEqual(
                driving, [],
                f"watchdog spec {spec['id']!r} still claims {self.NO_TRIGGER_CLAIM!r}, but "
                f"{driving} write its output root {root_path!r}. Update that spec's "
                f"'workflow' and 'calendar' fields to name the real trigger.")

    def test_no_spec_is_marked_no_schedule_while_a_workflow_schedules_it(self):
        bodies = self._workflow_bodies()
        for spec in self._specs():
            calendar = spec.get("calendar") or {}
            if calendar.get("type") != "NO_SCHEDULE":
                continue
            target = spec.get("glob") or spec.get("path") or ""
            root_path = str(target).split("/*", 1)[0]
            if not root_path:
                continue
            for name, body in bodies.items():
                if root_path in body and "schedule:" in body:
                    self.fail(
                        f"watchdog spec {spec['id']!r} is NO_SCHEDULE but {name} both "
                        f"schedules and writes {root_path!r}")


class NoPassRuleTest(unittest.TestCase):
    """(4) No pass rule introduced; authority stays closed."""

    def test_committed_summaries_keep_zero_passed_with_unchanged_semantics(self):
        checked = 0
        for market in MODULE.MARKETS:
            base = CORE.DEFAULT_OUTPUT_ROOTS[market]
            for sidecar in sorted(base.glob("*/summary.json")) if base.is_dir() else []:
                record = json.loads(sidecar.read_text(encoding="utf-8"))
                summary = record["summary"]
                self.assertEqual(summary["passed_count"], MODULE.REQUIRED_PASSED_COUNT)
                self.assertEqual(summary["passed_semantics"], MODULE.REQUIRED_PASSED_SEMANTICS)
                self.assertTrue(record["authority"]["observation_only"])
                for flag, value in record["authority"].items():
                    if flag != "observation_only":
                        self.assertFalse(value, flag)
                checked += 1
        self.assertGreater(checked, 0)

    def test_script_refuses_a_packet_that_carries_a_pass_rule(self):
        authority = {"observation_only": True, "order_authorized": False}
        with self.assertRaises(SystemExit):
            MODULE._assert_observation_only(
                {"passed_count": 3, "passed_semantics": MODULE.REQUIRED_PASSED_SEMANTICS}, authority, "probe")
        with self.assertRaises(SystemExit):
            MODULE._assert_observation_only(
                {"passed_count": 0, "passed_semantics": "A_RATIFIED_PASS_RULE"}, authority, "probe")

    def test_script_refuses_any_granted_authority(self):
        summary = {"passed_count": 0, "passed_semantics": MODULE.REQUIRED_PASSED_SEMANTICS}
        with self.assertRaises(SystemExit):
            MODULE._assert_observation_only(summary, {"observation_only": True, "order_authorized": True}, "probe")
        with self.assertRaises(SystemExit):
            MODULE._assert_observation_only(summary, {"observation_only": False}, "probe")


class SessionResolutionTest(unittest.TestCase):
    """The session date comes from committed evidence, with no network."""

    def test_each_market_resolves_a_session_date_from_committed_evidence(self):
        for market in MODULE.MARKETS:
            with self.subTest(market=market):
                session = MODULE.resolve_session_date(market)
                self.assertRegex(session, r"^\d{4}-\d{2}-\d{2}$")
                # And the resolved date is the directory the producer would write.
                self.assertEqual(MODULE.output_dir_for(market, session).name, session)


if __name__ == "__main__":
    unittest.main()
