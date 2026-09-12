#!/usr/bin/env python3
"""Retained official-KRX full-set historical population regression."""

import ast
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_retained_historical_population as MODULE  # noqa: E402
from regime import stage1_market_tuple as STAGE1  # noqa: E402
from briefing import rotation_candidate_selection_input as STAGE3  # noqa: E402


SOURCE = ROOT / "regime" / "kr_retained_historical_population.py"
OBSERVATIONS = ROOT / "data" / "observations" / "korea_market_signals"


def copy_packet(temp_root: Path, date: str) -> Path:
    source = OBSERVATIONS / date / "packet.json"
    target = (
        temp_root
        / "data"
        / "observations"
        / "korea_market_signals"
        / date
        / "packet.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def copy_calendars(temp_root: Path, date: str) -> None:
    for source in ROOT.glob(f"evidence/market_calendar/*/*/calendar-{date}.json"):
        target = temp_root / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


class RetainedHistoricalPopulationTests(unittest.TestCase):
    def temp_root(self, *dates, calendars=()):
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        for date in dates:
            copy_packet(root, date)
        for date in calendars:
            copy_calendars(root, date)
        self.addCleanup(holder.cleanup)
        return root

    def test_build_uses_every_retained_packet_in_sorted_order(self):
        root = self.temp_root("2026-09-02", "2026-08-28", "2026-08-31")
        packet = MODULE.build_population(root)
        self.assertEqual(
            packet["requested_dates"],
            ["2026-08-28", "2026-08-31", "2026-09-02"],
        )
        self.assertEqual(len(packet["records"]), 3)
        self.assertEqual(len(packet["retained_source_manifest"]), 3)
        self.assertTrue(all(row["status"] == "OBSERVED" for row in packet["records"]))
        self.assertEqual(packet["source_population_mode"], MODULE.SOURCE_MODE)
        self.assertEqual(MODULE.validate_population(packet, root=root), packet)

    def test_repository_retained_set_is_real_five_axis_and_pit_fail_closed(self):
        packet = MODULE.build_population(ROOT)
        self.assertGreaterEqual(len(packet["records"]), 10)
        for record in packet["records"]:
            self.assertEqual(record["status"], "OBSERVED")
            self.assertEqual(record["five_axis"]["coverage"]["ratio"], "5/5")
            self.assertEqual(len(record["candidate_normalized_result"]["axes"]), 5)
        status = MODULE.evaluate_pit(packet, root=ROOT)
        self.assertIn(status["status"], {"NOT_ACCEPTED", "PIT_ACCEPTED"})
        self.assertEqual(status["evaluated_date_count"], len(packet["records"]))
        self.assertFalse(status["authority"]["runtime_decision_available"])
        self.assertFalse(status["authority"]["trading_authorized"])

    def test_validation_rejects_a_subset_after_another_retained_packet_exists(self):
        root = self.temp_root("2026-08-28", "2026-08-31")
        packet = MODULE.build_population(root)
        copy_packet(root, "2026-09-01")
        with self.assertRaisesRegex(
            MODULE.RetainedHistoricalPopulationError,
            "RETAINED_POPULATION_SOURCE_DERIVATION_MISMATCH",
        ):
            MODULE.validate_population(packet, root=root)

    def test_validation_rejects_changed_retained_bytes(self):
        root = self.temp_root("2026-08-28", "2026-08-31")
        packet = MODULE.build_population(root)
        path = (
            root
            / "data"
            / "observations"
            / "korea_market_signals"
            / "2026-08-28"
            / "packet.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        value["axes"]["TREND"]["status"] = "ALTERED"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(MODULE.RetainedHistoricalPopulationError):
            MODULE.validate_population(packet, root=root)

    def test_rehashed_backdated_availability_is_rejected(self):
        root = self.temp_root("2026-08-28")
        path = (
            root
            / "data"
            / "observations"
            / "korea_market_signals"
            / "2026-08-28"
            / "packet.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        value["available_at"] = "2026-08-28T06:30:00Z"
        unsigned = copy.deepcopy(value)
        unsigned.pop("payload_sha256")
        value["payload_sha256"] = MODULE.KRP.KMS.payload_sha256(unsigned)
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.RetainedHistoricalPopulationError,
            "PACKET_AVAILABILITY_MISMATCH",
        ):
            MODULE.build_population(root)

    def test_calendar_is_bound_when_present_and_missing_is_explicit(self):
        root = self.temp_root(
            "2026-08-28", "2026-09-08", calendars=("2026-09-08",)
        )
        packet = MODULE.build_population(root)
        rows = {
            row["effective_trading_date"]: row
            for row in packet["retained_source_manifest"]
        }
        self.assertEqual(
            rows["2026-09-08"]["calendar_evidence_status"],
            MODULE.CALENDAR_MATCHED,
        )
        self.assertTrue(rows["2026-09-08"]["calendar_evidence"])
        self.assertEqual(
            rows["2026-08-28"]["calendar_evidence_status"],
            MODULE.CALENDAR_MISSING,
        )
        self.assertEqual(rows["2026-08-28"]["calendar_evidence"], [])

    def test_repeated_build_is_byte_identical(self):
        root = self.temp_root("2026-08-28", "2026-08-31")
        first = MODULE.build_population(root)
        second = MODULE.build_population(root)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))

    def test_historical_bundle_is_not_a_stage1_or_stage3_runtime_input(self):
        root = self.temp_root("2026-08-28")
        packet = MODULE.build_population(root)
        path = root / "population.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaisesRegex(
            STAGE1.Stage1MarketTupleError, "INPUT_CONTRACT_INVALID"
        ):
            STAGE1.build(ROOT, input_path=path)
        with self.assertRaisesRegex(
            STAGE3.RotationCandidateSelectionInputError,
            "SOURCE_BRIEFING_SCHEMA_INVALID",
        ):
            STAGE3.build_candidate_selection_input(
                packet,
                {},
                stage2_reference={},
                stage1_reference={},
            )
        self.assertFalse(packet["authority"]["runtime_regime_wiring_authorized"])
        self.assertFalse(packet["authority"]["stage_authorized"])

    def test_module_has_no_network_or_order_capability(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for prohibited in (
            "requests",
            "urllib",
            "socket",
            "http",
            "subprocess",
        ):
            self.assertNotIn(prohibited, imports)
        source = SOURCE.read_text(encoding="utf-8").lower()
        for prohibited in ("submit_order", "place_order", "real_trading=true"):
            self.assertNotIn(prohibited, source)


if __name__ == "__main__":
    unittest.main()
