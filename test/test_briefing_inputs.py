#!/usr/bin/env python3

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / ".github/scripts/build_briefing_inputs.py"
DATA = ROOT / "data"
OUT = DATA / "briefing"


class BriefingInputsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        krx = json.loads((DATA / "latest_krx.json").read_text())
        cls.today = krx["collected_for_kst_date"]

        subprocess.run(
            [sys.executable, str(BUILDER), "--today", cls.today],
            cwd=ROOT,
            check=True,
        )

    def test_step0_summary_matches_sources(self):
        status = json.loads((OUT / "step0_status.json").read_text())

        total_ok = 0
        total_failed = 0

        for name in ("krx", "dart", "sec"):
            src = json.loads((DATA / f"latest_{name}.json").read_text())
            self.assertEqual(
                status["collectors"][name]["ok"],
                src["summary"]["ok"],
            )
            self.assertEqual(
                status["collectors"][name]["failed"],
                src["summary"]["failed"],
            )
            total_ok += src["summary"]["ok"]
            total_failed += src["summary"]["failed"]

        self.assertEqual(status["totals"]["ok"], total_ok)
        self.assertEqual(status["totals"]["failed"], total_failed)
        self.assertEqual(status["overall"], "pass")

    def test_krx_tail_symbols_have_exact_views(self):
        for code in ("000660", "005930"):
            p = OUT / "krx" / f"{code}.json"
            self.assertTrue(p.exists(), code)
            obj = json.loads(p.read_text())
            self.assertEqual(obj["symbol"], code)
            self.assertIsNotNone(obj["latest_confirmed_day"])
            self.assertIsInstance(obj["latest_confirmed_row"], dict)

    def test_source_hash_matches(self):
        krx_hash = hashlib.sha256(
            (DATA / "latest_krx.json").read_bytes()
        ).hexdigest()

        samsung = json.loads(
            (OUT / "krx" / "005930.json").read_text()
        )

        self.assertEqual(
            samsung["source"]["source_sha256"],
            krx_hash,
        )

    def test_scheduled_collector_inventory_has_date_basis(self):
        status = json.loads((OUT / "step0_status.json").read_text())

        inventory = {
            x["name"]: x
            for x in status["scheduled_collectors"]
        }

        self.assertEqual(
            inventory["daily_collect"]["date_basis"],
            "KST",
        )
        self.assertEqual(
            inventory["stablecoin_daily_capture"]["date_basis"],
            "UTC",
        )

    def test_sec_compact_view_is_bounded(self):
        for path in (OUT / "sec").glob("*.json"):
            obj = json.loads(path.read_text())
            filings = obj["stock"].get("filings_recent", [])
            self.assertLessEqual(len(filings), 10)
            self.assertLess(path.stat().st_size, 32768)

    def test_invalid_json_fails_closed(self):
        spec = importlib.util.spec_from_file_location(
            "build_briefing_inputs",
            BUILDER,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "latest_krx.json"
            broken.write_bytes(
                b'{"summary":{"ok":5,"failed":0},"stocks":{"005930":'
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "incomplete_or_invalid_json",
            ):
                module.load_json(broken)


if __name__ == "__main__":
    unittest.main()
