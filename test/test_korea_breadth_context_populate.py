#!/usr/bin/env python3
"""P2-03 wiring: korea_breadth_context_populate.py regression."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_breadth_context_populate.py"
SPEC = importlib.util.spec_from_file_location("korea_breadth_context_populate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def market_packet(market: str, as_of_date: str, payload_sha: str, available_at=None) -> dict:
    return {
        "scope": "recent",
        "market": market,
        "as_of_date": as_of_date,
        "available_at": available_at,
        "fetched_at_utc": {"previous": "2026-08-21T09:00:00Z", "current": "2026-08-21T09:05:00Z"},
        "payload_sha256": payload_sha,
    }


class KoreaBreadthContextPopulateTest(unittest.TestCase):
    def test_build_context_summary_extracts_only_lineage_facts(self):
        packets = {
            "KOSPI": market_packet("KOSPI", "20260821", "a" * 64),
            "KOSDAQ": market_packet("KOSDAQ", "20260821", "b" * 64),
        }
        summary = MODULE.build_context_summary(packets, workflow_run_id="123")
        self.assertEqual(summary["as_of_date"], "2026-08-21")
        self.assertEqual(summary["markets"]["KOSPI"]["lineage_sha256"], "a" * 64)
        self.assertEqual(summary["markets"]["KOSDAQ"]["lineage_sha256"], "b" * 64)
        self.assertIsNone(summary["markets"]["KOSPI"]["available_at"])
        self.assertEqual(summary["generated_at"], "2026-08-21T09:05:00Z")
        self.assertEqual(summary["source"]["workflow_run_id"], "123")
        # Never a raw price/symbol/count field.
        for market_summary in summary["markets"].values():
            self.assertEqual(set(market_summary), {"lineage_sha256", "as_of_date", "available_at"})

    def test_market_as_of_date_mismatch_fails_closed(self):
        packets = {
            "KOSPI": market_packet("KOSPI", "20260821", "a" * 64),
            "KOSDAQ": market_packet("KOSDAQ", "20260820", "b" * 64),
        }
        with self.assertRaisesRegex(MODULE.ContextPopulateError, "MARKETS_AS_OF_DATE_MISMATCH"):
            MODULE.build_context_summary(packets, workflow_run_id=None)

    def test_load_recent_market_packet_rejects_wrong_scope_or_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived_dir = Path(tmp)
            bad = market_packet("KOSPI", "20260821", "a" * 64)
            bad["scope"] = "historical"
            (derived_dir / "korea-breadth-recent-kospi.json").write_text(json.dumps(bad))
            with self.assertRaisesRegex(
                MODULE.ContextPopulateError, "RECENT_PACKET_IDENTITY_MISMATCH:KOSPI"
            ):
                MODULE.load_recent_market_packet(derived_dir, "KOSPI")

    def test_load_recent_market_packet_missing_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                MODULE.ContextPopulateError, "RECENT_PACKET_MISSING:KOSPI"
            ):
                MODULE.load_recent_market_packet(Path(tmp), "KOSPI")

    def test_populate_is_idempotent_and_byte_identical_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived_dir = Path(tmp) / "derived"
            derived_dir.mkdir()
            (derived_dir / "korea-breadth-recent-kospi.json").write_text(
                json.dumps(market_packet("KOSPI", "20260821", "c" * 64))
            )
            (derived_dir / "korea-breadth-recent-kosdaq.json").write_text(
                json.dumps(market_packet("KOSDAQ", "20260821", "d" * 64))
            )
            MODULE.ROOT = Path(tmp) / "repo"
            MODULE.output_path_for = lambda as_of: MODULE.ROOT / "data" / "observations" / "korea_breadth_context" / as_of / "packet.json"
            first = MODULE.populate(derived_dir, workflow_run_id="run-1")
            self.assertEqual(first["outcome"], "populated")
            second = MODULE.populate(derived_dir, workflow_run_id="run-1")
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(first["payload_sha256"], second["payload_sha256"])

    def test_populate_fails_closed_on_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived_dir = Path(tmp) / "derived"
            derived_dir.mkdir()
            (derived_dir / "korea-breadth-recent-kospi.json").write_text(
                json.dumps(market_packet("KOSPI", "20260821", "e" * 64))
            )
            (derived_dir / "korea-breadth-recent-kosdaq.json").write_text(
                json.dumps(market_packet("KOSDAQ", "20260821", "f" * 64))
            )
            MODULE.ROOT = Path(tmp) / "repo"
            MODULE.output_path_for = lambda as_of: MODULE.ROOT / "data" / "observations" / "korea_breadth_context" / as_of / "packet.json"
            MODULE.populate(derived_dir, workflow_run_id="run-1")
            # A different, drifted second run for the same date (e.g. a
            # tampered or corrupted second source) must fail closed, never
            # silently overwrite.
            (derived_dir / "korea-breadth-recent-kospi.json").write_text(
                json.dumps(market_packet("KOSPI", "20260821", "9" * 64))
            )
            with self.assertRaisesRegex(MODULE.ContextPopulateError, "EXISTING_PACKET_DRIFT_OR_TAMPER"):
                MODULE.populate(derived_dir, workflow_run_id="run-1")


if __name__ == "__main__":
    unittest.main()
