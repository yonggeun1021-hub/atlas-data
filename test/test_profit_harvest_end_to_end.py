#!/usr/bin/env python3
"""P7-11 Baseline Audit -- end-to-end determinism/authority/isolation
regression (B-8 items 12, 14, 15, 16, 18, plus a real-evidence smoke run)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.opportunity_trigger import canonical_json  # noqa: E402

from harvest_audit.run_profit_harvest_audit import AUTHORITY_ALL_FALSE, run  # noqa: E402


# Free-text documentation/disclaimer fields are exempt from the action-word
# scan below -- they are EXPECTED to say things like "never generates an
# order" in plain English. Only actual generated field VALUES (candidate
# statuses, subjects, dates, numbers) must never carry an action-shaped
# code word.
_PROSE_KEYS = {"note", "not_an_operational_harvest_engine"}


def _walk_non_prose_string_values(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _PROSE_KEYS:
                continue
            yield from _walk_non_prose_string_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_non_prose_string_values(v)
    elif isinstance(obj, str):
        yield obj


class DeterminismTests(unittest.TestCase):
    """Item 12: same real committed evidence -> byte-identical output."""

    def test_two_full_runs_are_byte_identical(self):
        first = canonical_json(run())
        second = canonical_json(run())
        self.assertEqual(first, second)


class NoDownstreamImportTests(unittest.TestCase):
    """Item 14: `harvest_audit` is never imported by any operational
    decision/clock/shadow/briefing path."""

    def test_decision_module_source_never_imports_harvest_audit(self):
        for path in (ROOT / "decision").glob("*.py"):
            self.assertNotIn("harvest_audit", path.read_text(encoding="utf-8"),
                              f"{path} imports harvest_audit")

    def test_clock_module_source_never_imports_harvest_audit(self):
        for path in (ROOT / "clock").glob("*.py"):
            self.assertNotIn("harvest_audit", path.read_text(encoding="utf-8"),
                              f"{path} imports harvest_audit")

    def test_shadow_module_source_never_imports_harvest_audit(self):
        for path in (ROOT / "shadow").glob("*.py"):
            self.assertNotIn("harvest_audit", path.read_text(encoding="utf-8"),
                              f"{path} imports harvest_audit")

    def test_briefing_module_source_never_imports_harvest_audit(self):
        for path in (ROOT / "briefing").glob("*.py"):
            self.assertNotIn("harvest_audit", path.read_text(encoding="utf-8"),
                              f"{path} imports harvest_audit")

    def test_harvest_audit_itself_never_imports_decision_clock_shadow_briefing(self):
        # The dependency direction is one-way: harvest_audit reads replay/
        # only, never decision/clock/shadow/briefing.
        for path in (ROOT / "harvest_audit").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("import decision", "from decision", "import clock",
                               "from clock", "import shadow", "from shadow",
                               "import briefing", "from briefing"):
                self.assertNotIn(forbidden, source, f"{path} contains {forbidden!r}")


class AuthorityInvariantTests(unittest.TestCase):
    """Item 15: authority stays hard-False everywhere in this artifact."""

    def test_top_level_authority_block_is_all_false(self):
        self.assertEqual(AUTHORITY_ALL_FALSE, {
            "review_only": True,
            "action_authorized": False,
            "order_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })

    def test_report_authority_block_matches_the_constant(self):
        report = run()
        self.assertEqual(report["authority"], AUTHORITY_ALL_FALSE)

    def test_no_true_value_appears_under_any_key_named_authorized_anywhere_in_the_report(self):
        report = run()

        def _walk_authorized_keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.endswith("_authorized") and v is True:
                        yield k
                    yield from _walk_authorized_keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from _walk_authorized_keys(v)

        offending = list(_walk_authorized_keys(report))
        self.assertEqual(offending, [])

    def test_no_stage_buy_action_order_production_trading_field_is_ever_true(self):
        report = run()
        forbidden_true_keys = {"stage", "buy", "action", "order", "production", "trading"}

        def _walk_forbidden(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    lowered = k.lower()
                    if v is True and any(f in lowered for f in forbidden_true_keys):
                        yield (k, v)
                    yield from _walk_forbidden(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from _walk_forbidden(v)

        offending = list(_walk_forbidden(report))
        # review_only=True is expected and fine; explicitly exclude it.
        offending = [o for o in offending if o[0] != "review_only"]
        self.assertEqual(offending, [])


class ResearchOnlyScenarioNeverGeneratesActionTests(unittest.TestCase):
    """Item 16: the EARLY_EXIT_OPPORTUNITY_COST_DIAGNOSTIC scenario packet
    never carries an action-shaped field or value anywhere."""

    def test_policy_input_packet_never_contains_a_recommended_action_field(self):
        import re
        report = run()
        packet = report["policy_input_packet"]
        forbidden = re.compile(r"\b(BUY|SELL|ENTRY|ORDER|RECOMMENDED_ACTION)\b")
        for value in _walk_non_prose_string_values(packet):
            match = forbidden.search(value.upper())
            self.assertIsNone(match, f"forbidden action word {match.group() if match else ''!r} in {value!r}")

    def test_every_scenario_comparison_record_is_locked_unratified_and_unauthorized(self):
        report = run()
        packet = report["policy_input_packet"]
        self.assertEqual(packet["approval_status"], "UNRATIFIED")
        self.assertEqual(packet["scenario_type"], "ANALYTICAL_SCENARIO_ONLY")
        self.assertFalse(packet["action_authorized"])
        self.assertFalse(packet["order_authorized"])
        for block in packet["by_early_exit_horizon"].values():
            self.assertEqual(block["approval_status"], "UNRATIFIED")
            self.assertFalse(block["action_authorized"])
            self.assertFalse(block["order_authorized"])
            for comparison in block["comparisons"]:
                self.assertEqual(comparison["approval_status"], "UNRATIFIED")


class NoOptimalRecommendedActionableWordsAnywhereTests(unittest.TestCase):
    """CIO methodology review round 1, defect 3's explicit required proof:
    no analytical-grid output can ever contain the words "optimal",
    "recommended", or "actionable" -- scanned across EVERY string in the
    scenario/policy packet, prose included, so a future doc-string edit
    cannot silently reintroduce a policy-verdict framing."""

    def test_policy_input_packet_never_contains_optimal_recommended_or_actionable(self):
        import re

        def _all_strings(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    yield from _all_strings(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from _all_strings(v)
            elif isinstance(obj, str):
                yield obj

        report = run()
        packet = report["policy_input_packet"]
        forbidden = re.compile(r"\b(OPTIMAL|RECOMMENDED|ACTIONABLE)\b")
        for value in _all_strings(packet):
            match = forbidden.search(value.upper())
            self.assertIsNone(match, f"forbidden word {match.group() if match else ''!r} found in {value!r}")

    def test_aggregate_summary_status_is_always_the_unratified_policy_parameters_string(self):
        from harvest_audit.scenario import AGGREGATE_STATUS
        report = run()
        for block in report["policy_input_packet"]["by_early_exit_horizon"].values():
            self.assertEqual(block["aggregate_summary"]["status"], AGGREGATE_STATUS)
            self.assertNotIn("INSUFFICIENT_SAMPLE", block["aggregate_summary"]["status"])


class NoImportlibReloadAnywhereTests(unittest.TestCase):
    """Item 18: the exact class of bug found (and fixed) in PR #211's own
    test suite -- `importlib.reload()` on a shared module poisons other
    tests' `assertRaises` calls in the same process. Banned here from the
    start, not discovered later."""

    def test_no_harvest_audit_test_file_uses_importlib_reload(self):
        # This file itself legitimately mentions the strings "importlib.
        # reload"/"reload(" as part of the very check below -- excluded by
        # its own path, not by weakening the check for every other file.
        this_file = Path(__file__).resolve()
        test_dir = ROOT / "test"
        offending = []
        for path in sorted(test_dir.glob("test_profit_harvest_*.py")):
            if path.resolve() == this_file:
                continue
            source = path.read_text(encoding="utf-8")
            if "importlib.reload" in source or "reload(" in source:
                offending.append(str(path))
        self.assertEqual(offending, [])

    def test_no_harvest_audit_source_file_uses_importlib_reload(self):
        src_dir = ROOT / "harvest_audit"
        offending = []
        for path in sorted(src_dir.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "importlib.reload" in source or "reload(" in source:
                offending.append(str(path))
        self.assertEqual(offending, [])


class RealEvidenceSmokeTests(unittest.TestCase):
    """A minimal real-evidence sanity check that the whole pipeline
    produces a non-trivial, internally consistent report."""

    def test_market_summary_covers_all_three_markets(self):
        report = run()
        self.assertEqual(set(report["market_summary"]), {"BTC", "KOREA", "CRYPTO"})

    def test_priority_subject_episodes_only_contains_btc_005930_000660(self):
        report = run()
        subjects = {r["subject"] for r in report["priority_subject_episodes"]}
        self.assertTrue(subjects.issubset({"BTC", "005930", "000660"}))

    def test_report_asof_evidence_date_is_a_real_date_string(self):
        report = run()
        self.assertRegex(report["report_asof_evidence_date"], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
