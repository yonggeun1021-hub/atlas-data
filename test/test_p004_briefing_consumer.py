#!/usr/bin/env python3
"""P0-04 evening briefing consumer regression.

No KRX, GitHub, or Notion calls are made. Tracked post-close evidence is read
only; mutation and CLI tests use isolated temporary directories.
"""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "briefing" / "krx_post_close.py"
DATA = ROOT / "data"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("krx_post_close_briefing", SOURCE)
CONTRACT = MODULE.load_contract()
DATE = "2026-08-20"
GENERATED = "2026-08-20T18:00:00+09:00"


class P004BriefingConsumerTest(unittest.TestCase):
    def copy_bundle(self, data_root):
        source = DATA / "observations" / "krx_post_close" / DATE
        target = data_root / "observations" / "krx_post_close" / DATE
        target.parent.mkdir(parents=True)
        shutil.copytree(source, target)
        return target

    def test_contract_has_observation_only_authority(self):
        self.assertTrue(CONTRACT["authority"]["briefing_observation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "briefing_observation_only":
                self.assertFalse(value, key)
        self.assertEqual(CONTRACT["display_status"], "Observed / Unconfirmed")

    def test_tracked_live_bundle_builds_visible_unconfirmed_packet(self):
        before = {
            path.relative_to(DATA): path.read_bytes()
            for path in (
                DATA / "observations" / "krx_post_close" / DATE
            ).rglob("*")
            if path.is_file()
        }

        packet = MODULE.build_packet(DATA, DATE, GENERATED, CONTRACT)

        self.assertEqual(packet["status"], "READY_OBSERVED_UNCONFIRMED")
        self.assertFalse(packet["decision_eligible"])
        self.assertEqual(packet["summary"]["observed_symbol_count"], 6)
        self.assertEqual(
            packet["summary"]["decision_eligible_symbol_count"],
            0,
        )
        for row in packet["symbols"]:
            self.assertEqual(row["display_status"], "Observed / Unconfirmed")
            self.assertFalse(row["decision_eligible"])
            self.assertFalse(row["confirmed"])
            self.assertEqual(row["latest_observed_day"], DATE)
            self.assertEqual(
                row["decision_boundary"]["history_basis"],
                "confirmed_only",
            )
            self.assertEqual(
                row["decision_boundary"]["sma20_through"],
                row["latest_trading_day"],
            )
        after = {
            path.relative_to(DATA): path.read_bytes()
            for path in (
                DATA / "observations" / "krx_post_close" / DATE
            ).rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_bundle_inventory_binds_exact_compact_view_bytes(self):
        packet = MODULE.build_packet(DATA, DATE, GENERATED, CONTRACT)
        files = packet["bundle"]["files"]
        paths = {row["path"] for row in files}

        self.assertIn(
            f"data/observations/krx_post_close/{DATE}/index.json",
            paths,
        )
        self.assertIn(
            f"data/observations/krx_post_close/{DATE}/symbols/005930.json",
            paths,
        )
        self.assertEqual(
            packet["bundle"]["bundle_sha256"],
            MODULE.payload_sha256(files),
        )

    def test_missing_bundle_is_unknown_without_zero_or_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = MODULE.build_packet(
                Path(tmp) / "data",
                DATE,
                GENERATED,
                CONTRACT,
            )

        self.assertEqual(packet["status"], "UNKNOWN")
        self.assertEqual(packet["symbols"], [])
        self.assertTrue(packet["reason_codes"])
        self.assertTrue(
            all(value is None for value in packet["summary"].values())
        )
        self.assertNotEqual(packet["status"], "NEUTRAL")
        self.assertNotEqual(packet["display_status"], "NEUTRAL")

    def test_tampered_decision_eligibility_becomes_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            target = self.copy_bundle(data_root)
            path = target / "symbols" / "005930.json"
            view = json.loads(path.read_text(encoding="utf-8"))
            view["observed_row"]["decision_eligible"] = True
            path.write_text(json.dumps(view), encoding="utf-8")

            packet = MODULE.build_packet(
                data_root,
                DATE,
                GENERATED,
                CONTRACT,
            )

        self.assertEqual(packet["status"], "UNKNOWN")
        self.assertEqual(packet["symbols"], [])

    def test_unexpected_bundle_file_becomes_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            target = self.copy_bundle(data_root)
            (target / "unexpected.json").write_text("{}\n", encoding="utf-8")

            packet = MODULE.build_packet(
                data_root,
                DATE,
                GENERATED,
                CONTRACT,
            )

        self.assertEqual(packet["status"], "UNKNOWN")
        self.assertEqual(
            packet["reason_codes"],
            ["POST_CLOSE_BRIEFING_ADAPTER_VALIDATION_FAILED"],
        )
        self.assertEqual(packet["symbols"], [])

    def test_packet_mutations_fail_closed_even_with_recomputed_digest(self):
        original = MODULE.build_packet(DATA, DATE, GENERATED, CONTRACT)
        variants = []

        eligible = copy.deepcopy(original)
        eligible["symbols"][0]["decision_eligible"] = True
        variants.append((eligible, "READY_SYMBOL_INVALID"))

        boundary = copy.deepcopy(original)
        boundary["symbols"][0]["decision_boundary"]["history_basis"] = "all"
        variants.append((boundary, "READY_SYMBOL_INVALID"))

        summary = copy.deepcopy(original)
        summary["summary"]["decision_eligible_symbol_count"] = 1
        variants.append((summary, "READY_SUMMARY_INVALID"))

        for packet, error in variants:
            unsigned = copy.deepcopy(packet)
            unsigned.pop("packet_sha256")
            packet["packet_sha256"] = MODULE.payload_sha256(unsigned)
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.PostCloseBriefingError,
                error,
            ):
                MODULE.validate_packet(packet, CONTRACT)

    def test_evening_time_and_exact_date_are_required(self):
        cases = (
            ("2026-08-20", "2026-08-20T17:59:59+09:00", "TOO_EARLY"),
            ("2026-08-20", "2026-08-21T18:00:00+09:00", "INVALID"),
            ("2026-02-30", "2026-02-30T18:00:00+09:00", "INVALID"),
        )
        for date, generated, error in cases:
            with self.subTest(
                date=date,
                generated=generated,
            ), self.assertRaisesRegex(
                MODULE.PostCloseBriefingError,
                error,
            ):
                MODULE.build_packet(DATA, date, generated, CONTRACT)

    def test_cli_writes_only_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "packet.json"
            self.assertEqual(
                MODULE.run(DATA, DATE, GENERATED, output),
                0,
            )
            packet = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(packet, MODULE.validate_packet(packet, CONTRACT))

        forbidden = ROOT / "data" / "p004_briefing_test.json"
        self.assertEqual(
            MODULE.run(DATA, DATE, GENERATED, forbidden),
            1,
        )
        self.assertFalse(forbidden.exists())

    def test_consumer_has_no_live_network_or_process_dependency(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in (
            "requests",
            "urllib",
            "socket",
            "http",
            "subprocess",
            "git",
            "notion",
        ):
            self.assertNotIn(prohibited, imported)


if __name__ == "__main__":
    unittest.main()
