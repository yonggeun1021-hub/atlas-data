#!/usr/bin/env python3
"""Canonical Atlas WBS readiness metric regressions."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "test" / "fixtures" / "wbs_readiness"
EVALUATED_AT = "2026-09-02T22:08:00Z"

SPEC = importlib.util.spec_from_file_location(
    "wbs_readiness_tested", ROOT / "governance" / "wbs_readiness.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def has_float(value) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(child) for child in value.values())
    if isinstance(value, list):
        return any(has_float(child) for child in value)
    return False


class WbsReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(
            (ROOT / "config" / "wbs_readiness_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.snapshot = load("wbs_snapshot_20260903.json")
        self.paper = load("paper_evidence_20260903.json")

    def report(self):
        return MODULE.build_report(
            self.snapshot, self.paper, self.contract, EVALUATED_AT
        )

    def test_live_snapshot_reproduces_every_headline_without_float_math(self):
        report = self.report()
        inventory = report["inventory"]
        self.assertEqual(inventory["totalRows"], 148)
        self.assertEqual(inventory["formalCompletion"]["ratio"], "36/148")
        self.assertEqual(inventory["formalCompletion"]["percentage"], "24.3")
        self.assertEqual(inventory["weightedProgress"]["ratio"], "7380/14800")
        self.assertEqual(inventory["weightedProgress"]["percentage"], "49.9")
        self.assertEqual(
            inventory["forbiddenExcludedWeightedProgress"]["ratio"],
            "7380/13000",
        )
        self.assertEqual(
            inventory["forbiddenExcludedWeightedProgress"]["percentage"],
            "56.8",
        )
        self.assertEqual(inventory["forbiddenRows"], 18)
        self.assertEqual(inventory["nonForbiddenRows"], 130)
        self.assertEqual(inventory["actionableRows"], 94)
        self.assertEqual(inventory["lateStageEntry"]["ratio"], "96/148")
        self.assertEqual(inventory["lateStageEntry"]["percentage"], "64.9")
        self.assertFalse(has_float(report))

    def test_paper_and_small_cap_denominators_are_independent(self):
        report = self.report()
        paper = report["paperRotation"]
        self.assertEqual(paper["score"]["ratio"], "7/16")
        self.assertEqual(paper["score"]["percentage"], "43.8")
        self.assertEqual(paper["naturalE2E"]["ratio"], "0/3")
        self.assertEqual(paper["naturalE2E"]["percentage"], "0.0")

        small_paper = report["scopes"]["smallPaper"]
        self.assertEqual(small_paper["formalReadiness"]["ratio"], "29/92")
        self.assertEqual(small_paper["formalReadiness"]["percentage"], "31.5")
        self.assertEqual(small_paper["weightedReadiness"]["percentage"], "65.4")

        small_live = report["scopes"]["smallLive"]
        self.assertEqual(small_live["formalReadiness"]["ratio"], "31/107")
        self.assertEqual(small_live["formalReadiness"]["percentage"], "29.0")
        self.assertEqual(small_live["weightedReadiness"]["percentage"], "58.5")
        self.assertIn("P10-13", small_live["rowIds"])
        self.assertNotIn("P10-13", small_paper["rowIds"])

    def test_integer_zero_is_preserved_and_zero_denominator_is_null(self):
        fraction = MODULE._fraction(0, 0)
        self.assertEqual(fraction["numerator"], 0)
        self.assertEqual(fraction["denominator"], 0)
        self.assertEqual(fraction["ratio"], "0/0")
        self.assertIsNone(fraction["percentage"])
        with self.assertRaisesRegex(MODULE.ReadinessError, "NUMERATOR_EXCEEDS_DENOMINATOR"):
            MODULE._fraction(1, 0)

        paper = copy.deepcopy(self.paper)
        for gate in paper["gates"]:
            gate["status"] = "MISSING"
        report = MODULE.build_report(
            self.snapshot, paper, self.contract, EVALUATED_AT
        )
        self.assertEqual(report["paperRotation"]["score"]["earnedUnits"], 0)
        self.assertEqual(report["paperRotation"]["score"]["percentage"], "0.0")

    def test_null_and_unknown_statuses_fail_closed(self):
        for invalid in (None, "", "DONE"):
            with self.subTest(invalid=invalid):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["rows"][0]["status"] = invalid
                with self.assertRaisesRegex(
                    MODULE.ReadinessError, "SNAPSHOT_STATUS_UNKNOWN"
                ):
                    MODULE.build_report(
                        snapshot, self.paper, self.contract, EVALUATED_AT
                    )

    def test_duplicate_url_title_and_canonical_id_fail_closed(self):
        cases = []

        duplicate_url = copy.deepcopy(self.snapshot)
        duplicate_url["rows"][1]["url"] = duplicate_url["rows"][0]["url"]
        cases.append((duplicate_url, "DUPLICATE_ROW_URL"))

        duplicate_title = copy.deepcopy(self.snapshot)
        duplicate_title["rows"][1]["workItem"] = duplicate_title["rows"][0]["workItem"]
        cases.append((duplicate_title, "DUPLICATE_WORK_ITEM"))

        duplicate_id = copy.deepcopy(self.snapshot)
        target = next(row for row in duplicate_id["rows"] if row["workItem"].startswith("P0-02 "))
        target["workItem"] = "P0-01 another canonical claim"
        cases.append((duplicate_id, "DUPLICATE_CANONICAL_ROW_ID:P0-01"))

        for snapshot, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(MODULE.ReadinessError, code):
                    MODULE.build_report(
                        snapshot, self.paper, self.contract, EVALUATED_AT
                    )

    def test_missing_scope_row_and_duplicate_scope_id_fail_closed(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["rows"] = [
            row for row in snapshot["rows"] if not row["workItem"].startswith("P10-13 ")
        ]
        with self.assertRaisesRegex(
            MODULE.ReadinessError, "SCOPE_ROWS_MISSING:P10-13"
        ):
            MODULE.build_report(snapshot, self.paper, self.contract, EVALUATED_AT)

        contract = copy.deepcopy(self.contract)
        contract["scopes"]["smallPaper"].append("P0-01")
        with self.assertRaisesRegex(
            MODULE.ReadinessError, "SMALL_PAPER_DUPLICATE_ID"
        ):
            MODULE.build_report(self.snapshot, self.paper, contract, EVALUATED_AT)

    def test_stale_or_future_sources_fail_closed(self):
        with self.assertRaisesRegex(MODULE.ReadinessError, "SNAPSHOT_STALE"):
            MODULE.build_report(
                self.snapshot, self.paper, self.contract, "2026-09-05T00:00:01Z"
            )
        with self.assertRaisesRegex(MODULE.ReadinessError, "SNAPSHOT_FROM_FUTURE"):
            MODULE.build_report(
                self.snapshot, self.paper, self.contract, "2026-09-02T21:35:59Z"
            )

    def test_paper_gate_shape_and_authority_fail_closed(self):
        evidence = copy.deepcopy(self.paper)
        evidence["gates"] = evidence["gates"][:-1]
        with self.assertRaisesRegex(MODULE.ReadinessError, "PAPER_GATES_INVALID"):
            MODULE.build_report(
                self.snapshot, evidence, self.contract, EVALUATED_AT
            )

        evidence = copy.deepcopy(self.paper)
        evidence["authority"]["trading"] = True
        with self.assertRaisesRegex(
            MODULE.ReadinessError, "PAPER_AUTHORITY_MUST_REMAIN_FALSE"
        ):
            MODULE.build_report(
                self.snapshot, evidence, self.contract, EVALUATED_AT
            )

    def test_query_page_collector_rejects_duplicates_at_report_boundary(self):
        query_page = {
            "results": [
                {
                    "url": "https://app.notion.com/row-a",
                    "Work Item": "P0-01 first",
                    "Status": "✅ 완료",
                    "Phase": "P0 운영기반",
                },
                {
                    "url": "https://app.notion.com/row-a",
                    "Work Item": "P0-02 second",
                    "Status": "🟣 관측중",
                    "Phase": "P0 운영기반",
                },
            ]
        }
        normalized = MODULE.collect_query_pages(
            [query_page], self.contract, "2026-09-02T21:36:00Z"
        )
        self.assertEqual(len(normalized["rows"]), 2)
        with self.assertRaisesRegex(MODULE.ReadinessError, "DUPLICATE_ROW_URL"):
            MODULE.build_report(
                normalized, self.paper, self.contract, EVALUATED_AT
            )


if __name__ == "__main__":
    unittest.main()
