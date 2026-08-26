#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/daily_briefing_delivery.py"
SPEC = importlib.util.spec_from_file_location("daily_briefing_delivery", SCRIPT)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


class DailyBriefingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.slot = "morning"
        self.date = "2026-08-25"
        self.date_root = self.root / "evidence/daily_briefing/morning/2026-08-25"
        packet = {
            "slot": self.slot,
            "decision_date": self.date,
            "packet_sha256": "packet-digest",
            "components": [
                {
                    "component_id": "INVESTMENT_DECISION_REVIEW",
                    "status": "DATA_BLOCKED",
                    "reason": "P8-09_EXPECTATIONS_GAP_UNKNOWN",
                    "packet": {
                        "review_outcome": "BLOCKED",
                        "trade_proposal": None,
                        "money_action": "NONE",
                        "capital": 0,
                    },
                },
                {
                    "component_id": "INVESTMENT_REVIEW_SHADOW",
                    "status": "DATA_BLOCKED",
                    "reason": "DECISION_REVIEW_BLOCKED",
                    "packet": {
                        "review_outcome": "BLOCKED",
                        "ledger_record_created": False,
                        "capital": {"authorized": False, "amount": 0},
                        "action": None,
                        "order": None,
                        "stage_change": None,
                    },
                },
                {
                    "component_id": "SHADOW_ENTRY_REVIEW",
                    "status": "READY",
                    "reason": None,
                    "packet": {
                        "schema_version": "shadow_entry_review_briefing_status/1",
                        "sample_status": "NATURAL_OPERATIONAL_SAMPLE",
                        "summary": {
                            "candidate_count": 69,
                            "zero_capital_review_item_count": 1,
                            "probe_review_count": 1,
                        },
                        "policy_status": {
                            "candidate_validity": "UNRATIFIED",
                            "entry": "UNRATIFIED",
                            "position_management": "UNRATIFIED",
                            "position_size": "UNRATIFIED",
                        },
                        "review_items": [{
                            "subject": "005930",
                            "market": "KOREA",
                            "review_state": "REVERSAL_PROBE_REVIEW",
                            "participation_state": "PROBE_REVIEW",
                            "review_due_status": "REVIEW_OVERDUE",
                            "review_reason": "WEAK_PRICE_STATE_WITH_TWO_INDEPENDENT_TRIGGER_TYPES",
                            "money_boundary": {
                                "capital": 0,
                                "trade_proposal": None,
                                "stage_promotion_authority": False,
                                "buy_authority": False,
                                "action_authority": False,
                                "order_authority": False,
                                "production_authority": False,
                                "trading_authority": False,
                            },
                        }],
                        "why_not_executable": ["ENTRY_POLICY_UNRATIFIED"],
                        "authority": {
                            "capital": 0,
                            "trade_proposal": None,
                            "stage_promotion_authority": False,
                            "buy_authority": False,
                            "action_authority": False,
                            "order_authority": False,
                            "production_authority": False,
                            "trading_authority": False,
                        },
                    },
                },
            ],
        }
        dump(self.date_root / "rev-001/packet.json", packet)
        (self.date_root / "rev-001/briefing.md").write_text("# full briefing\n")
        dump(self.date_root / "index.json", {
            "schema_version": 1,
            "slot": self.slot,
            "decision_date": self.date,
            "latest_revision": 1,
            "revisions": [{
                "revision": 1,
                "path": "rev-001",
                "packet_sha256": "packet-digest",
            }],
        })
        self.validate = mock.patch.object(delivery, "validate_packet")
        self.validate.start()
        locator = delivery.build_locator(self.root, self.slot, self.date)
        delivery.write_locator(self.root, locator)

    def tearDown(self):
        self.validate.stop()
        self.tmp.cleanup()

    def test_exact_locator_delivers_blocked_review_and_no_shadow_record(self):
        result = delivery.consume(self.root, self.slot, self.date)
        review, shadow, zero_capital_review = result["components"]
        self.assertEqual(review["review_outcome"], "BLOCKED")
        self.assertIsNone(review["trade_proposal"])
        self.assertEqual(review["money_action"], "NONE")
        self.assertEqual(review["capital"], 0)
        self.assertFalse(shadow["ledger_record_created"])
        self.assertEqual(zero_capital_review["sample_status"], "NATURAL_OPERATIONAL_SAMPLE")
        self.assertEqual(zero_capital_review["capital"], 0)
        self.assertIsNone(zero_capital_review["trade_proposal"])
        self.assertEqual(zero_capital_review["review_items"][0]["subject"], "005930")
        self.assertFalse(any(result["authority"].values()))

    def test_wrong_date_never_falls_back(self):
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DATE_MISMATCH"):
            delivery.consume(self.root, self.slot, "2026-08-24")

    def test_wrong_slot_never_falls_back(self):
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_SLOT_MISMATCH"):
            delivery.consume(self.root, "evening", self.date)

    def test_index_latest_revision_drift_is_rejected(self):
        index_path = self.date_root / "index.json"
        index = json.loads(index_path.read_text())
        index["latest_revision"] = 2
        dump(index_path, index)
        with self.assertRaisesRegex(delivery.DeliveryError, "INDEX_REVISION_INVALID"):
            delivery.consume(self.root, self.slot, self.date)

    def test_packet_path_tamper_even_with_existing_alternate_is_rejected(self):
        alternate = self.date_root / "rev-999"
        alternate.mkdir()
        (alternate / "packet.json").write_text(
            (self.date_root / "rev-001/packet.json").read_text()
        )
        (alternate / "briefing.md").write_text("# alternate\n")
        locator_path = self.root / delivery.LOCATOR_PATH
        locator = json.loads(locator_path.read_text())
        locator["packet_path"] = locator["packet_path"].replace("rev-001", "rev-999")
        dump(locator_path, locator)
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DRIFT_OR_TAMPER"):
            delivery.consume(self.root, self.slot, self.date)

    def test_packet_file_byte_tamper_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DRIFT_OR_TAMPER"):
            delivery.consume(self.root, self.slot, self.date)

    def test_briefing_byte_tamper_is_rejected(self):
        (self.date_root / "rev-001/briefing.md").write_text("# changed\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "LOCATOR_DRIFT_OR_TAMPER"):
            delivery.consume(self.root, self.slot, self.date)

    def test_missing_component_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"] = packet["components"][:1]
        dump(path, packet)
        locator = delivery.build_locator(self.root, self.slot, self.date)
        delivery.write_locator(self.root, locator)
        with self.assertRaisesRegex(delivery.DeliveryError, "COMPONENT_MISSING"):
            delivery.consume(self.root, self.slot, self.date)

    def test_authority_escalation_is_rejected_even_when_locator_is_resigned(self):
        locator_path = self.root / delivery.LOCATOR_PATH
        locator = json.loads(locator_path.read_text())
        locator["authority"]["buy"] = True
        dump(locator_path, locator)
        with self.assertRaisesRegex(delivery.DeliveryError, "AUTHORITY_ESCALATION"):
            delivery.consume(self.root, self.slot, self.date)

    def test_blocked_review_with_trade_proposal_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][0]["packet"]["trade_proposal"] = {"side": "BUY"}
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(delivery.DeliveryError, "BLOCKED_REVIEW_ACTION_LEAK"):
            delivery.consume(self.root, self.slot, self.date)

    def test_blocked_shadow_with_created_record_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][1]["packet"]["ledger_record_created"] = True
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(delivery.DeliveryError, "BLOCKED_SHADOW_LEAK"):
            delivery.consume(self.root, self.slot, self.date)

    def test_zero_capital_review_authority_escalation_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][2]["packet"]["review_items"][0]["money_boundary"][
            "buy_authority"
        ] = True
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(
            delivery.DeliveryError, "SHADOW_REVIEW_ITEM_AUTHORITY_INVALID"
        ):
            delivery.consume(self.root, self.slot, self.date)

    def test_zero_capital_review_post_hoc_field_is_rejected(self):
        path = self.date_root / "rev-001/packet.json"
        packet = json.loads(path.read_text())
        packet["components"][2]["packet"]["review_items"][0]["forward_return"] = 9.9
        dump(path, packet)
        delivery.write_locator(
            self.root, delivery.build_locator(self.root, self.slot, self.date)
        )
        with self.assertRaisesRegex(
            delivery.DeliveryError, "SHADOW_REVIEW_POST_HOC_FIELD_FORBIDDEN"
        ):
            delivery.consume(self.root, self.slot, self.date)

    def test_locator_write_is_idempotent(self):
        locator = delivery.build_locator(self.root, self.slot, self.date)
        self.assertFalse(delivery.write_locator(self.root, locator))

    def test_render_is_bounded_and_contains_no_full_packet_dump(self):
        text = delivery.render_delivery(delivery.consume(self.root, self.slot, self.date))
        self.assertIn("INVESTMENT_DECISION_REVIEW: DATA_BLOCKED", text)
        self.assertIn("SHADOW_ENTRY_REVIEW: READY", text)
        self.assertIn("005930 (KOREA): REVERSAL_PROBE_REVIEW", text)
        self.assertIn("capital=0 / trade_proposal=null", text)
        self.assertIn("Trading authority: false", text)
        self.assertNotIn("packet_sha256", text)
        self.assertNotIn("components\":", text)


if __name__ == "__main__":
    unittest.main()
