#!/usr/bin/env python3
"""Patcher verified against the LIVE workflow, not a facsimile.

`fixture_daily_briefing_live.yml` is a byte-for-byte copy of
.github/workflows/daily-briefing.yml on main; test_00 asserts its git blob is
4bf0fc9ecd27e3affb7c4f85d9f93df5f888dd75, so if the fixture ever drifts from
what was actually reviewed, every other test here is invalidated first.

Earlier revisions tested against a reconstructed facsimile and passed 9/9 while
the patch could not apply to the real file at all.
"""
from __future__ import annotations

import collections
import hashlib
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ap", ROOT / ".github/scripts/apply_finalization_patch.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)

FIXTURE = ROOT / "test/fixture_daily_briefing_live.yml"
LIVE_BLOB = "4bf0fc9ecd27e3affb7c4f85d9f93df5f888dd75"


class NoDuplicateKeyLoader(yaml.SafeLoader):
    """PyYAML silently lets a later duplicate key win -- which is exactly how a
    second `workflow_dispatch:` erased the new inputs while the file still
    parsed as 'valid YAML'."""


def _no_dup(loader, node, deep=False):
    keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
    dups = [k for k, c in collections.Counter(keys).items() if c > 1]
    if dups:
        raise AssertionError(f"duplicate keys: {dups}")
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


NoDuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup)


class LivePatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = FIXTURE.read_text(encoding="utf-8")
        cls.patched, cls.problems = ap.patch(cls.original)

    def test_00_fixture_is_the_live_file(self):
        self.assertEqual(ap.git_blob_sha(FIXTURE.read_bytes()), LIVE_BLOB)

    def test_01_patch_applies_cleanly(self):
        self.assertEqual(self.problems, [])
        self.assertNotEqual(self.patched, self.original)

    def _doc(self):
        return yaml.load(self.patched, NoDuplicateKeyLoader)

    def test_02_no_duplicate_keys(self):
        self._doc()   # raises on duplicates

    def test_03_exactly_one_workflow_dispatch(self):
        self.assertEqual(self.patched.count("  workflow_dispatch:\n"), 1)

    def test_04_dispatch_inputs_survive(self):
        on = self._doc()[True]
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]),
                         {"slot", "mode", "decision_date"})

    def test_05_all_three_triggers(self):
        on = self._doc()[True]
        self.assertLessEqual({"schedule", "workflow_dispatch", "repository_dispatch"}, set(on))
        self.assertEqual(sorted(on["repository_dispatch"]["types"]),
                         ["atlas-briefing-run", "atlas-finalization-drain"])

    def _steps(self):
        return self._doc()["jobs"]["briefing"]["steps"]

    def _named(self, name):
        return next(s for s in self._steps() if s.get("name") == name)

    def test_06_resolver_precedes_producer(self):
        names = [s.get("name") for s in self._steps()]
        self.assertLess(names.index("Resolve slot, mode and decision date"),
                        names.index("Publish provider-free daily briefing packet"))

    def test_07_producer_is_skipped_in_drain_mode(self):
        self.assertEqual(self._named("Publish provider-free daily briefing packet")["if"],
                         "steps.resolve.outputs.mode == 'brief'")

    def test_08_resolver_is_actually_wired_into_the_producer(self):
        producer = self._named("Publish provider-free daily briefing packet")
        self.assertEqual(producer["env"]["DISPATCH_SLOT"], "${{ steps.resolve.outputs.slot }}")
        self.assertEqual(producer["env"]["RESOLVED_DATE"],
                         "${{ steps.resolve.outputs.decision_date }}")
        self.assertIn('DECISION_DATE="$RESOLVED_DATE"', producer["run"])
        self.assertNotIn("$(TZ=Asia/Seoul date +%F)", producer["run"])

    def test_09_dispatch_type_is_the_mode_authority(self):
        resolver = self._named("Resolve slot, mode and decision date")["run"]
        self.assertIn('atlas-finalization-drain) MODE="drain"', resolver)
        self.assertIn("contradicts dispatch type", resolver)

    def test_10_human_reaching_write_is_gone_from_the_producer(self):
        self.assertNotIn("GITHUB_STEP_SUMMARY", self.patched)
        self.assertIn("consume_ready",
                      self._named("Publish provider-free daily briefing packet")["run"])

    def test_11_producer_logic_is_preserved(self):
        run = self._named("Publish provider-free daily briefing packet")["run"]
        for fragment in ("daily_orchestrator.py publish", "publish-locator", "consume",
                         "publish_scheduled_briefing_authority.py",
                         "decision_change_lineage_operational.py",
                         "three_market_shadow_operational_readiness.py",
                         "CONSUMER_READY_COMMIT"):
            self.assertIn(fragment, run, f"producer lost {fragment!r}")

    def test_12a_full_wiring_order(self):
        """seal -> publish -> reconcile -> validator -> publish -> gate."""
        names = [s.get("name") for s in self._steps()]
        order = ["Resolve slot, mode and decision date",
                 "Publish provider-free daily briefing packet",
                 "Seal briefing for finalization",
                 "Publish sealed draft",
                 "Reconcile verdicts left by a previous run",
                 "Run deterministic validator",
                 "Publish validator verdict",
                 "Ingest verdicts and deliver",
                 "Commit finalization artifacts"]
        positions = [names.index(n) for n in order]
        self.assertEqual(positions, sorted(positions))

    def test_12b_reconciliation_precedes_the_validator(self):
        """A verdict a dead runner never recorded must be on the books before
        the validator decides whether it has a block to withdraw."""
        names = [s.get("name") for s in self._steps()]
        self.assertLess(names.index("Reconcile verdicts left by a previous run"),
                        names.index("Run deterministic validator"))
        step = self._named("Reconcile verdicts left by a previous run")
        self.assertIn("briefing_finalization.py ingest", step["run"])
        # non-fatal: the gate fails closed at drain anyway, and hard-failing here
        # would skip the validator entirely
        self.assertIn("|| echo", step["run"])

    def test_12c_validator_emits_to_the_inbox(self):
        step = self._named("Run deterministic validator")
        self.assertIn("briefing_validator.py", step["run"])
        self.assertIn("--emit-inbox", step["run"])

    def test_12d_every_wiring_step_is_mode_gated(self):
        for name in ("Seal briefing for finalization", "Publish sealed draft",
                     "Reconcile verdicts left by a previous run",
                     "Run deterministic validator", "Publish validator verdict"):
            self.assertTrue(self._named(name)["if"].startswith("steps.resolve.outputs.mode"),
                            f"{name} is not gated on mode")

    def test_12_gate_order_and_exit_propagation(self):
        names = [s.get("name") for s in self._steps()]
        self.assertLess(names.index("Seal briefing for finalization"),
                        names.index("Publish sealed draft"))
        self.assertLess(names.index("Publish sealed draft"),
                        names.index("Ingest verdicts and deliver"))
        self.assertIn('exit "$RC"', self._named("Ingest verdicts and deliver")["run"])

    def test_12e_no_cron_is_added(self):
        """Manufacturing a same-day retrigger with a new cron was deliberately
        rejected upstream (SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED).
        The patch must not quietly reintroduce one."""
        self.assertEqual(self.patched.count("- cron:"), self.original.count("- cron:"))
        on = self._doc()[True]
        self.assertEqual(on["schedule"], yaml.safe_load(self.original)[True]["schedule"])

    def test_12f_dispatch_is_not_claimed_as_a_recovery_guarantee(self):
        self.assertIn("NOT a recovery guarantee", self.patched)
        self.assertIn("SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED", self.patched)

    def test_13_no_new_action_uses(self):
        self.assertEqual(self.patched.count("uses:"), self.original.count("uses:"))

    def test_14_no_nested_heredoc_in_the_gate(self):
        """A python heredoc inside the gate's own run block would terminate it."""
        self.assertNotIn("<<'PYEOF'", self._named("Ingest verdicts and deliver")["run"])

    def test_15_blob_precondition_refuses_a_changed_file(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        path = repo / ap.WORKFLOW
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.original + "\n# drift\n", encoding="utf-8")
        rc = ap.main.__wrapped__ if hasattr(ap.main, "__wrapped__") else None
        import sys
        argv = sys.argv
        sys.argv = ["p", "--repo-root", str(repo), "--dry-run"]
        try:
            self.assertEqual(ap.main(), 2)
        finally:
            sys.argv = argv
        self.assertEqual(path.read_text(encoding="utf-8"), self.original + "\n# drift\n")

    def test_16_refuses_to_patch_twice(self):
        again, problems = ap.patch(self.patched)
        self.assertTrue(any("already patched" in p for p in problems))
        self.assertEqual(again, self.patched)

    def test_17_refuses_when_an_anchor_is_missing(self):
        mutated = self.original.replace('          } >> "$GITHUB_STEP_SUMMARY"\n',
                                        '          } >> "$SOMETHING_ELSE"\n')
        out, problems = ap.patch(mutated)
        self.assertTrue(problems)
        self.assertEqual(out, mutated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
