#!/usr/bin/env python3
"""P6-03 Bear / Hedge risk-budget registry regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "bear_hedge_risk_budget.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("bear_hedge_risk_budget", SOURCE)
CONTRACT = MODULE.load_contract()


def record(
    budget_id="BEAR_HEDGE_GLOBAL",
    scope_type="PORTFOLIO_TOTAL",
    scope_id="GLOBAL",
    start="2026-08-20",
    end=None,
    marker="a",
):
    return {
        "risk_budget_id": budget_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "valid_from": start,
        "valid_to": end,
        "max_loss": 0.02,
        "max_gross_exposure": 0.10,
        "holding_horizon_days": 10,
        "unit": "NAV_FRACTION",
        "separate_from_long_budget": True,
        "eligible_instrument_registry_sha256": marker * 64,
        "budget_basis_ref": f"test://budget/{budget_id}/{start}",
        "budget_basis_sha256": "b" * 64,
        "notes": [],
    }


def budget_set(records=None):
    value = {
        "schema_version": "bear_hedge_budget_set/1",
        "contract_version": "bear_hedge_risk_budget/1",
        "budget_set_id": "TEST-BEAR-HEDGE-BUDGET-2026-08-20",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "portfolio_loss_budget_ref": "test://portfolio/loss-budget",
        "portfolio_loss_budget_sha256": "c" * 64,
        "long_budget_ref": "test://portfolio/long-budget",
        "long_budget_sha256": "d" * 64,
        "records": [record()] if records is None else records,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["records"] = sorted(
        normalized["records"],
        key=lambda row: (row["risk_budget_id"], row["valid_from"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class BearHedgeRiskBudgetTests(unittest.TestCase):
    def test_contract_has_no_default_allocation_sizing_or_order_authority(self):
        self.assertEqual(CONTRACT["approval_mode"], "EXPLICIT_CIO_RATIFIED_ONLY")
        self.assertEqual(
            CONTRACT["long_budget_separation"],
            "EXACT_DISTINCT_SHA_REQUIRED",
        )
        self.assertTrue(CONTRACT["authority"]["budget_registry_validation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "budget_registry_validation_only":
                self.assertFalse(value, key)

    def test_portfolio_and_market_budgets_are_reproduced_without_usage(self):
        rows = [
            record(),
            record(
                budget_id="BEAR_HEDGE_US",
                scope_type="MARKET",
                scope_id="US",
                marker="e",
            ),
        ]
        packet = MODULE.build_packet(budget_set(rows), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "BEAR_HEDGE_BUDGET_SET_VALIDATED")
        self.assertEqual(packet["summary"], {
            "active_count": 2,
            "portfolio_total_count": 1,
            "market_count": 1,
            "scope_ids": ["GLOBAL", "US"],
        })
        self.assertIsNone(packet["budget_usage"])
        self.assertIsNone(packet["hedge_size"])
        self.assertEqual(packet["order_intents"], [])

    def test_effective_history_is_deterministic_and_non_overlapping(self):
        rows = [record(end="2026-08-21"), record(start="2026-08-21", marker="e")]
        value = budget_set(rows)
        packet = MODULE.build_packet(value, "2026-08-21", CONTRACT)
        self.assertEqual(len(packet["active_budgets"]), 1)
        self.assertEqual(
            packet["active_budgets"][0]["eligible_instrument_registry_sha256"],
            "e" * 64,
        )
        permuted = copy.deepcopy(value)
        permuted["records"].reverse()
        self.assertEqual(
            MODULE.canonical_json(packet),
            MODULE.canonical_json(MODULE.build_packet(permuted, "2026-08-21", CONTRACT)),
        )

        overlapping = [record(end="2026-08-22"), record(start="2026-08-21", marker="e")]
        with self.assertRaisesRegex(
            MODULE.BearHedgeBudgetError,
            "BUDGET_INTERVAL_OVERLAP",
        ):
            MODULE.build_packet(budget_set(overlapping), "2026-08-21", CONTRACT)

    def test_long_budget_must_be_explicitly_separate(self):
        same = budget_set()
        same["long_budget_sha256"] = same["portfolio_loss_budget_sha256"]
        with self.assertRaisesRegex(
            MODULE.BearHedgeBudgetError,
            "LONG_BUDGET_SHA_MUST_BE_DISTINCT",
        ):
            MODULE.build_packet(same, "2026-08-21", CONTRACT)

        row = record()
        row["separate_from_long_budget"] = False
        with self.assertRaisesRegex(
            MODULE.BearHedgeBudgetError,
            "LONG_BUDGET_SEPARATION_REQUIRED",
        ):
            MODULE.build_packet(budget_set([row]), "2026-08-21", CONTRACT)

    def test_loss_exposure_horizon_scope_and_lineage_fail_closed(self):
        cases = []
        loss = record()
        loss["max_loss"] = -0.1
        cases.append((loss, "MAX_LOSS_INVALID"))
        exposure = record()
        exposure["max_gross_exposure"] = float("inf")
        cases.append((exposure, "MAX_GROSS_EXPOSURE_INVALID"))
        horizon = record()
        horizon["holding_horizon_days"] = 0
        cases.append((horizon, "HOLDING_HORIZON_INVALID"))
        scope = record(scope_type="MARKET", scope_id="GLOBAL")
        cases.append((scope, "SCOPE_PAIR_INVALID"))
        lineage = record()
        lineage["eligible_instrument_registry_sha256"] = "bad"
        cases.append((lineage, "ELIGIBILITY_REGISTRY_SHA_INVALID"))
        for row, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.BearHedgeBudgetError, error
            ):
                MODULE.build_packet(budget_set([row]), "2026-08-21", CONTRACT)

    def test_non_cio_approval_authority_expansion_and_hash_tamper_fail(self):
        approval = budget_set()
        approval["ratified_by"] = "SYSTEM"
        with self.assertRaisesRegex(MODULE.BearHedgeBudgetError, "BUDGET_SET_IDENTITY_INVALID"):
            MODULE.build_packet(approval, "2026-08-21", CONTRACT)

        authority = budget_set()
        authority["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(MODULE.BearHedgeBudgetError, "BUDGET_SET_IDENTITY_INVALID"):
            MODULE.build_packet(authority, "2026-08-21", CONTRACT)

        tampered = budget_set()
        tampered["records"][0]["max_loss"] = 0.9
        with self.assertRaisesRegex(
            MODULE.BearHedgeBudgetError,
            "BUDGET_SET_PACKET_SHA_MISMATCH",
        ):
            MODULE.build_packet(tampered, "2026-08-21", CONTRACT)

    def test_output_is_deterministic_and_lineage_bound(self):
        value = budget_set()
        first = MODULE.build_packet(value, "2026-08-21", CONTRACT)
        second = MODULE.build_packet(value, "2026-08-21", CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(first["lineage"], {
            "budget_set_packet_sha256": value["packet_sha256"],
            "portfolio_loss_budget_sha256": "c" * 64,
            "long_budget_sha256": "d" * 64,
        })
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_self_rehashed_output_semantic_tamper_fails_closed(self):
        packet = MODULE.build_packet(budget_set(), "2026-08-21", CONTRACT)
        packet["summary"]["active_count"] += 1
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.BearHedgeBudgetError,
            "OUTPUT_SUMMARY_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = write_json(tmp / "budget.json", budget_set())
            output = tmp / "nested" / "budget-packet.json"
            self.assertEqual(MODULE.run(source, "2026-08-21", output), 0)
            self.assertEqual(json.loads(output.read_text())["summary"]["active_count"], 1)
            forbidden = ROOT / "data" / "bear_hedge_budget_test.json"
            self.assertEqual(MODULE.run(source, "2026-08-21", forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
