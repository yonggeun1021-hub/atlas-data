import base64
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_data import krx_official_holiday_calendar as CAL
from market_data import krx_session_calendar_v2 as SESSION


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "evidence/market_calendar/krx_global_holiday/2026-09-09/"
    "capture-2026.json"
)


def rewrite_provider(capture, mutate):
    raw = base64.b64decode(capture["response"]["raw_base64"])
    provider = json.loads(raw)
    mutate(provider)
    rewritten = json.dumps(
        provider, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    capture["response"]["raw_base64"] = base64.b64encode(rewritten).decode("ascii")
    capture["response"]["raw_sha256"] = CAL.digest(rewritten)
    return CAL.canonical_bytes(capture)


class KrxOfficialHolidayCalendarTests(unittest.TestCase):
    def build(self, day):
        return CAL.build_calendar_packet(
            SOURCE.read_bytes(), SOURCE.relative_to(ROOT).as_posix(), day
        )

    def test_retained_official_response_and_capture_time_are_exact(self):
        checked = CAL.validate_capture(SOURCE.read_bytes())
        self.assertEqual(
            checked["provider_raw_sha256"],
            "e60dc5a3d4f8a02afc842f34544f2edf162836bc124209b75dc7456030858dfe",
        )
        self.assertEqual(len(checked["closures"]), 17)
        self.assertLess(
            checked["response_received_at"],
            dt.datetime(2026, 9, 9, 0, 0, tzinfo=dt.timezone.utc),
        )

    def test_current_d_and_e_are_open_regular_before_e_open(self):
        for day in ("2026-09-08", "2026-09-09"):
            with self.subTest(day=day):
                packet, receipt = self.build(day)
                self.assertEqual(packet["calendar"]["status"], "OPEN_REGULAR")
                self.assertEqual(receipt["status"], "OPEN_REGULAR")
                self.assertLess(
                    dt.datetime.fromisoformat(packet["calendar"]["available_at"]),
                    dt.datetime(2026, 9, 9, 0, 0, tzinfo=dt.timezone.utc),
                )
                validated = SESSION.validate_calendar(
                    packet["calendar"],
                    dt.datetime(2026, 9, 9, 0, 0, tzinfo=dt.timezone.utc),
                    SESSION.load_contract(),
                )
                self.assertEqual(validated["provider_id"], CAL.PROVIDER_ID)

    def test_listed_holiday_and_weekend_are_closed(self):
        listed, listed_receipt = self.build("2026-09-24")
        weekend, weekend_receipt = self.build("2026-09-12")
        self.assertEqual(listed["calendar"]["status"], "CLOSED")
        self.assertEqual(listed_receipt["reason"], "Chuseok (Korean Thanksgiving)")
        self.assertEqual(weekend["calendar"]["status"], "CLOSED")
        self.assertEqual(weekend_receipt["reason"], "WEEKEND_KRX_RULE")
        for packet in (listed, weekend):
            validated = SESSION.validate_calendar(
                packet["calendar"],
                dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc),
                SESSION.load_contract(),
            )
            self.assertEqual(validated["status"], "CLOSED")

    def test_response_byte_tampering_fails(self):
        capture = json.loads(SOURCE.read_text())
        capture["response"]["raw_base64"] += "AA=="
        with self.assertRaisesRegex(
            CAL.KrxOfficialHolidayCalendarError, "RESPONSE_BASE64_INVALID"
        ):
            CAL.validate_capture(CAL.canonical_bytes(capture))

    def test_duplicate_or_wrong_year_rows_fail_after_rehash(self):
        for mutate, code in (
            (
                lambda provider: provider["block1"].append(
                    copy.deepcopy(provider["block1"][-1])
                ),
                "HOLIDAY_ORDER_OR_DUPLICATE_INVALID",
            ),
            (
                lambda provider: provider["block1"][0].update(
                    calnd_dd="2025-01-01", calnd_dd_dy="2025-01-01"
                ),
                "HOLIDAY_YEAR_OR_DATE_INVALID",
            ),
        ):
            with self.subTest(code=code):
                capture = json.loads(SOURCE.read_text())
                with self.assertRaisesRegex(CAL.KrxOfficialHolidayCalendarError, code):
                    CAL.validate_capture(rewrite_provider(capture, mutate))

    def test_unknown_status_cannot_be_claimed_for_official_holiday_provider(self):
        packet, _ = self.build("2026-09-09")
        packet["calendar"].update(status="UNKNOWN", open_at=None, close_at=None)
        with self.assertRaisesRegex(
            SESSION.KrxSessionCalendarV2Error, "KRX_HOLIDAY_STATUS_INVALID"
        ):
            SESSION.validate_calendar(
                packet["calendar"],
                dt.datetime(2026, 9, 9, tzinfo=dt.timezone.utc),
                SESSION.load_contract(),
            )


if __name__ == "__main__":
    unittest.main()
