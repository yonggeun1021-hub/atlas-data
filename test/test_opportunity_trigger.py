#!/usr/bin/env python3
"""P11 Opportunity Capture PIT Replay -- Opportunity Trigger Event schema
regression. Independent of decision/shadow/briefing code by construction."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import opportunity_trigger as ot  # noqa: E402


class OpportunityTriggerSchemaTests(unittest.TestCase):
    def test_module_has_no_decision_or_shadow_imports(self):
        source = (ROOT / "replay" / "opportunity_trigger.py").read_text(encoding="utf-8")
        for forbidden in ("import decision", "from decision", "import shadow", "from shadow",
                           "import briefing", "from briefing"):
            self.assertNotIn(forbidden, source)

    def test_all_seven_trigger_types_accepted(self):
        self.assertEqual(len(ot.TRIGGER_TYPES), 7)
        for t in ot.TRIGGER_TYPES:
            ev = ot.build_trigger_event(
                t, "BTC", "2026-08-13", "2026-08-13", "test/fixture#1", "a" * 64, 0.5,
            )
            self.assertEqual(ev.trigger_type, t)

    def test_invalid_trigger_type_rejected(self):
        with self.assertRaisesRegex(ot.OpportunityTriggerError, "TRIGGER_TYPE_INVALID"):
            ot.build_trigger_event(
                "NOT_A_REAL_TYPE", "BTC", "2026-08-13", "2026-08-13", "src", "a" * 64, 0.5,
            )

    def test_first_seen_at_after_decision_date_rejected(self):
        with self.assertRaisesRegex(ot.OpportunityTriggerError, "FIRST_SEEN_AT_AFTER_DECISION_DATE"):
            ot.build_trigger_event(
                "PRICE_CONFIRMATION", "BTC", "2026-08-14", "2026-08-13", "src", "a" * 64, 0.5,
            )

    def test_confirmed_at_after_decision_date_rejected(self):
        with self.assertRaisesRegex(ot.OpportunityTriggerError, "CONFIRMED_AT_AFTER_DECISION_DATE"):
            ot.build_trigger_event(
                "PRICE_CONFIRMATION", "BTC", "2026-08-10", "2026-08-13", "src", "a" * 64, 0.5,
                confirmed_at="2026-08-14",
            )

    def test_confirmed_at_before_first_seen_at_rejected(self):
        with self.assertRaisesRegex(ot.OpportunityTriggerError, "CONFIRMED_AT_BEFORE_FIRST_SEEN_AT"):
            ot.build_trigger_event(
                "PRICE_CONFIRMATION", "BTC", "2026-08-10", "2026-08-13", "src", "a" * 64, 0.5,
                confirmed_at="2026-08-09",
            )

    def test_strength_out_of_range_rejected(self):
        for bad in (-0.01, 1.01, 2):
            with self.assertRaisesRegex(ot.OpportunityTriggerError, "STRENGTH_OUT_OF_RANGE"):
                ot.build_trigger_event(
                    "PRICE_CONFIRMATION", "BTC", "2026-08-13", "2026-08-13", "src", "a" * 64, bad,
                )

    def test_evidence_sha256_must_be_real_hex_digest_shape(self):
        with self.assertRaisesRegex(ot.OpportunityTriggerError, "EVIDENCE_SHA256_INVALID"):
            ot.build_trigger_event(
                "PRICE_CONFIRMATION", "BTC", "2026-08-13", "2026-08-13", "src", "not-a-sha", 0.5,
            )

    def test_trigger_id_is_deterministic(self):
        ev1 = ot.build_trigger_event("PRICE_CONFIRMATION", "BTC", "2026-08-13", "2026-08-13",
                                      "src", "a" * 64, 0.5, confirmed_at="2026-08-13")
        ev2 = ot.build_trigger_event("PRICE_CONFIRMATION", "BTC", "2026-08-13", "2026-08-13",
                                      "src", "a" * 64, 0.5, confirmed_at="2026-08-13")
        self.assertEqual(ev1.trigger_id(), ev2.trigger_id())

    def test_trigger_id_changes_with_any_field(self):
        ev1 = ot.build_trigger_event("PRICE_CONFIRMATION", "BTC", "2026-08-13", "2026-08-13",
                                      "src", "a" * 64, 0.5)
        ev2 = ot.build_trigger_event("PRICE_CONFIRMATION", "BTC", "2026-08-13", "2026-08-13",
                                      "src", "a" * 64, 0.51)
        self.assertNotEqual(ev1.trigger_id(), ev2.trigger_id())

    def test_json_round_trip_validates(self):
        ev = ot.build_trigger_event("FLOW_REVERSAL", "005930", "2026-08-13", "2026-08-14",
                                     "data/2026-08-14/krx.json#005930", "b" * 64, 0.6,
                                     confirmed_at="2026-08-13")
        rebuilt = ot.validate_trigger_event(ev.to_dict())
        self.assertEqual(rebuilt, ev.to_dict())

    def test_tampered_dict_rejected(self):
        ev = ot.build_trigger_event("FLOW_REVERSAL", "005930", "2026-08-13", "2026-08-14",
                                     "src", "b" * 64, 0.6)
        d = ev.to_dict()
        d["strength"] = 0.99  # tamper without recomputing trigger_id
        with self.assertRaisesRegex(ot.OpportunityTriggerError, "TRIGGER_ID_MISMATCH"):
            ot.validate_trigger_event(d)

    def test_frozen_dataclass_cannot_be_mutated(self):
        ev = ot.build_trigger_event("PRICE_CONFIRMATION", "BTC", "2026-08-13", "2026-08-13",
                                     "src", "a" * 64, 0.5)
        with self.assertRaises(Exception):
            ev.strength = 0.9  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
