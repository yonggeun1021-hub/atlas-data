#!/usr/bin/env python3
"""P8-04 three-market Regime header regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "briefing" / "three_market_regime_header.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("three_market_regime_header", SOURCE)
REGIME = MODULE.regime_output
CONTRACT = MODULE.load_contract()


def source(market, generated="2026-08-21T01:00:00Z"):
    return REGIME.build_unknown_output(market, generated)


def sources():
    return [source("US"), source("KR"), source("CRYPTO")]


class ThreeMarketRegimeHeaderTests(unittest.TestCase):
    def test_contract_is_read_model_only_and_closes_decision_authority(self):
        self.assertEqual(CONTRACT["required_markets"], ["US", "KR", "CRYPTO"])
        self.assertTrue(CONTRACT["authority"]["briefing_read_model_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "briefing_read_model_only":
                self.assertFalse(value, key)

    def test_header_preserves_state_direction_confidence_and_market_order(self):
        packet = MODULE.build_header(
            list(reversed(sources())), "morning", "2026-08-21T01:10:00Z", CONTRACT
        )
        self.assertEqual([row["market"] for row in packet["markets"]], ["US", "KR", "CRYPTO"])
        self.assertEqual([row["label"] for row in packet["markets"]], ["US", "Korea", "Crypto"])
        for row in packet["markets"]:
            self.assertEqual(row["regime"], "UNKNOWN")
            self.assertEqual(row["direction"], "UNKNOWN")
            self.assertIsNone(row["confidence"])
            self.assertEqual(row["coverage"]["ratio"], "0/5")
        self.assertEqual(packet["status"], "HEADER_ASSEMBLED_NO_DECISION_AUTHORITY")

    def test_morning_and_evening_are_labels_not_decision_changes(self):
        morning = MODULE.build_header(sources(), "morning", "2026-08-21T01:10:00Z", CONTRACT)
        evening = MODULE.build_header(sources(), "evening", "2026-08-21T01:10:00Z", CONTRACT)
        self.assertEqual(morning["slot"], "morning")
        self.assertEqual(evening["slot"], "evening")
        self.assertEqual(morning["markets"], evening["markets"])
        self.assertEqual(morning["summary"], evening["summary"])

    def test_missing_duplicate_and_unexpected_market_fail_closed(self):
        cases = [
            (sources()[:2], "SOURCE_MARKET_MISSING"),
            ([source("US"), source("US"), source("CRYPTO")], "SOURCE_MARKET_DUPLICATE"),
        ]
        unexpected = source("US")
        unexpected["market"] = "EU"
        cases.append(([unexpected, source("KR"), source("CRYPTO")], "SOURCE_MARKET_UNEXPECTED"))
        for values, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.ThreeMarketRegimeHeaderError, error
            ):
                MODULE.build_header(values, "morning", "2026-08-21T01:10:00Z", CONTRACT)

    def test_source_contract_tamper_and_future_source_fail_closed(self):
        confident = source("US")
        confident["confidence"] = "0.8"
        with self.assertRaisesRegex(MODULE.ThreeMarketRegimeHeaderError, "SOURCE_REGIME_INVALID"):
            MODULE.build_header(
                [confident, source("KR"), source("CRYPTO")],
                "morning",
                "2026-08-21T01:10:00Z",
                CONTRACT,
            )
        with self.assertRaisesRegex(MODULE.ThreeMarketRegimeHeaderError, "SOURCE_FROM_FUTURE"):
            MODULE.build_header(sources(), "morning", "2026-08-21T00:59:59Z", CONTRACT)

    def test_header_never_ranks_selects_or_creates_action(self):
        packet = MODULE.build_header(sources(), "morning", "2026-08-21T01:10:00Z", CONTRACT)
        self.assertIsNone(packet["summary"]["ranked_market"])
        self.assertIsNone(packet["summary"]["favorable_market"])
        self.assertIsNone(packet["summary"]["action"])
        self.assertFalse(packet["authority"]["strategy_eligibility_authorized"])
        self.assertFalse(packet["authority"]["trading_authorized"])

    def test_build_is_permutation_safe_deterministic_and_inputs_immutable(self):
        values = sources()
        before = MODULE.canonical_json(values)
        first = MODULE.build_header(values, "morning", "2026-08-21T01:10:00Z", CONTRACT)
        second = MODULE.build_header(
            list(reversed(values)), "morning", "2026-08-21T01:10:00Z", CONTRACT
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(values), before)

    def test_output_digest_summary_and_authority_tamper_fail_closed(self):
        original = MODULE.build_header(sources(), "morning", "2026-08-21T01:10:00Z", CONTRACT)
        cases = []
        digest = copy.deepcopy(original)
        digest["packet_sha256"] = "0" * 64
        cases.append((digest, "HEADER_SHA_MISMATCH"))
        ranking = copy.deepcopy(original)
        ranking["summary"]["ranked_market"] = "US"
        cases.append((ranking, "HEADER_SUMMARY_INVALID"))
        authority = copy.deepcopy(original)
        authority["authority"]["action_generation_authorized"] = True
        cases.append((authority, "HEADER_IDENTITY_INVALID"))
        for packet, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.ThreeMarketRegimeHeaderError, error
            ):
                MODULE.validate_header(packet, CONTRACT)

    def test_source_lineage_sha_matches_exact_validated_packet(self):
        values = sources()
        packet = MODULE.build_header(values, "morning", "2026-08-21T01:10:00Z", CONTRACT)
        expected = {item["market"]: MODULE.payload_sha256(item) for item in values}
        self.assertEqual(
            {row["market"]: row["source_sha256"] for row in packet["markets"]},
            expected,
        )

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
            temp = Path(tmp)
            paths = []
            for item in sources():
                path = temp / f"{item['market']}.json"
                path.write_text(json.dumps(item), encoding="utf-8")
                paths.append(path)
            output = temp / "out" / "header.json"
            self.assertEqual(
                MODULE.run(paths, "morning", "2026-08-21T01:10:00Z", output), 0
            )
            self.assertEqual(json.loads(output.read_text())["summary"]["market_count"], 3)
            forbidden = ROOT / "data" / "three_market_regime_header_test.json"
            self.assertEqual(
                MODULE.run(paths, "morning", "2026-08-21T01:10:00Z", forbidden), 1
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
