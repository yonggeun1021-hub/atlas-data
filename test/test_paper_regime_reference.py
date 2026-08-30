#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "paper_regime_reference.py"
SPEC = importlib.util.spec_from_file_location("paper_regime_reference_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PaperRegimeReferenceTest(unittest.TestCase):
    def test_current_free_inputs_make_plain_paper_reference(self):
        packet = MODULE.build_reference()
        markets = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(packet["status"], "PARTIAL_REFERENCE_AVAILABLE")
        self.assertEqual(markets["US"]["coverage"]["ratio"], "5/5")
        self.assertEqual(markets["US"]["paper_reference"]["candidate_regime"], "NEUTRAL")
        self.assertEqual(markets["US"]["paper_reference"]["score"], 2)
        self.assertEqual(markets["KR"]["coverage"]["ratio"], "5/5")
        self.assertEqual(markets["KR"]["paper_reference"]["candidate_regime"], "NEUTRAL")
        self.assertEqual(markets["KR"]["paper_reference"]["score"], 1)
        self.assertEqual(markets["CRYPTO"]["coverage"]["ratio"], "4/5")
        self.assertEqual(markets["CRYPTO"]["paper_reference"]["candidate_regime"], "UNKNOWN")
        self.assertEqual(markets["CRYPTO"]["coverage"]["missing_axes"], ["LEADERSHIP"])
        self.assertTrue(all(row["runtime_regime"] == "UNKNOWN" for row in markets.values()))

    def test_axis_values_and_korean_explanations_are_visible(self):
        packet = MODULE.build_reference()
        markets = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(
            [row["direction"] for row in markets["US"]["axes"]],
            ["POSITIVE", "NEGATIVE", "POSITIVE", "NEUTRAL", "POSITIVE"],
        )
        self.assertEqual(
            [row["direction"] for row in markets["KR"]["axes"]],
            ["NEUTRAL", "POSITIVE", "NEUTRAL", "NEGATIVE", "POSITIVE"],
        )
        for market in ("US", "KR"):
            for row in markets[market]["axes"]:
                self.assertTrue(row["summary_ko"])

    def test_authority_boundary_stays_paper_only(self):
        authority = MODULE.build_reference()["authority"]
        self.assertTrue(authority["paper_reference_display_authorized"])
        self.assertTrue(authority["paper_symbol_context_authorized"])
        for key in ("runtime_regime_authorized", "final_regime_authorized", "stage_authorized", "buy_authorized", "action_authorized", "order_authorized", "capital_authorized", "production_authorized", "trading_authorized"):
            self.assertFalse(authority[key], key)

    def test_resigned_tamper_and_source_tamper_fail_closed(self):
        packet = MODULE.build_reference()
        self.assertEqual(MODULE.validate_reference(packet), packet)
        tampered = copy.deepcopy(packet)
        tampered["markets"][0]["paper_reference"]["candidate_regime"] = "RISK_ON"
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "REFERENCE_REDERIVATION_MISMATCH"):
            MODULE.validate_reference(tampered)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            shutil.copy2(ROOT / "data/latest_free_market_data.json", root / "data/latest_free_market_data.json")
            shutil.copy2(ROOT / "data/latest_korea_market_signals.json", root / "data/latest_korea_market_signals.json")
            value = json.loads((root / "data/latest_free_market_data.json").read_text())
            value["fred"]["value"] = "not-a-number"
            (root / "data/latest_free_market_data.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "US_VIX_INVALID"):
                MODULE.build_reference(root)

    def test_write_is_append_only_and_pointer_is_identical(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            shutil.copy2(ROOT / "data/latest_free_market_data.json", root / "data/latest_free_market_data.json")
            shutil.copy2(ROOT / "data/latest_korea_market_signals.json", root / "data/latest_korea_market_signals.json")
            packet = MODULE.build_reference(root)
            evidence, latest = MODULE.write_packet(packet, root)
            self.assertEqual(evidence.read_bytes(), latest.read_bytes())
            self.assertEqual(MODULE.validate_reference(json.loads(latest.read_text()), root), packet)
            MODULE.write_packet(packet, root)


if __name__ == "__main__":
    unittest.main()
