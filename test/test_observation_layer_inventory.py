#!/usr/bin/env python3
"""Observation package implementation-state inventory regression."""

import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "observation"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ObservationLayerInventoryTest(unittest.TestCase):
    def test_implemented_normalization_and_store_modules_exist_and_import(self):
        implemented = (
            "normalize",
            "record",
            "store",
            "observe_rule0022",
            "persist_rule0022",
        )
        for module in implemented:
            self.assertTrue((PACKAGE / f"{module}.py").is_file())
            self.assertIsNotNone(importlib.import_module(f"observation.{module}"))

    def test_pair_and_evaluator_are_not_claimed_as_implemented(self):
        self.assertFalse((PACKAGE / "pair.py").exists())
        self.assertFalse((PACKAGE / "pair_validation.py").exists())
        self.assertFalse((PACKAGE / "evaluator.py").exists())

        package_doc = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("층 ③ Normalization", package_doc)
        self.assertIn("층 ④ Store", package_doc)
        self.assertIn("층 ⑤ Pair Validation · ⑥ Evaluator 는 아직 구현되지 않았다", package_doc)
        self.assertNotIn("층 ④ Store · ⑤ Pair Validation", package_doc)


if __name__ == "__main__":
    unittest.main()
