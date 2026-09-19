import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data import krx_post_close_session_calendar as CAL
from market_data import krx_session_calendar_v2 as BARS


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/observations/krx_post_close/2026-09-08/source.json"


class KrxPostCloseSessionCalendarTest(unittest.TestCase):
    def build(self, day="2026-09-08", raw=None):
        return CAL.build_calendar_packet(
            SOURCE.read_bytes() if raw is None else raw,
            "data/observations/krx_post_close/2026-09-08/source.json",
            day,
        )

    def test_exact_committed_source_builds_both_open_days(self):
        for day in ("2026-09-07", "2026-09-08"):
            packet, receipt = self.build(day)
            checked = BARS.validate_calendar(
                packet["calendar"],
                dt.datetime(2026, 9, 8, 8, 0, tzinfo=dt.timezone.utc),
                BARS.load_contract(),
            )
            self.assertEqual(checked["status"], "OPEN_REGULAR")
            self.assertEqual(receipt["required_symbols"], ["000660", "005930"])
            self.assertFalse(receipt["price_or_flow_finality_promoted"])
            self.assertEqual(
                packet["official_response_sha256"],
                hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            )

    def test_same_day_unconfirmed_is_allowed_only_for_open_fact(self):
        _, receipt = self.build("2026-09-08")
        self.assertTrue(all(row["confirmed"] is False for row in receipt["observations"]))
        self.assertFalse(receipt["authority"]["entry_authorized"])
        self.assertFalse(receipt["authority"]["real_capital_authorized"])

    def test_weekday_or_missing_exact_rows_cannot_be_inferred(self):
        value = json.loads(SOURCE.read_text())
        del value["stocks"]["005930"]["daily"]["2026-09-08"]
        with self.assertRaisesRegex(CAL.KrxPostCloseCalendarError, "EXACT_DATE_ROW_MISSING"):
            self.build(raw=CAL.canonical_bytes(value))

    def test_partial_or_nonofficial_source_is_rejected(self):
        for mutate, code in (
            (lambda value: value["summary"].__setitem__("failed", 1), "SOURCE_PARTIAL_RESPONSE"),
            (lambda value: value.__setitem__("source_tier", "Derived"), "SOURCE_IDENTITY_INVALID"),
        ):
            value = json.loads(SOURCE.read_text())
            mutate(value)
            with self.assertRaisesRegex(CAL.KrxPostCloseCalendarError, code):
                self.build(raw=CAL.canonical_bytes(value))

    def test_tampered_ohlcv_and_wrong_confirmation_are_rejected(self):
        cases = []
        bad_price = json.loads(SOURCE.read_text())
        bad_price["stocks"]["000660"]["daily"]["2026-09-08"]["volume"] = 0
        cases.append((bad_price, "ROW_VOLUME_INVALID"))
        bad_confirmation = json.loads(SOURCE.read_text())
        bad_confirmation["stocks"]["000660"]["daily"]["2026-09-08"]["confirmed"] = True
        cases.append((bad_confirmation, "ROW_CONFIRMATION_STATE_INVALID"))
        for value, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(
                CAL.KrxPostCloseCalendarError, code
            ):
                self.build(raw=CAL.canonical_bytes(value))

    def test_noncanonical_source_bytes_are_rejected(self):
        value = json.loads(SOURCE.read_text())
        raw = json.dumps(value, ensure_ascii=False).encode()
        with self.assertRaisesRegex(CAL.KrxPostCloseCalendarError, "SOURCE_BYTES_NOT_CANONICAL"):
            self.build(raw=raw)

    def test_v2_validator_rejects_unapproved_or_closed_krx_claims(self):
        packet, _ = self.build()
        decision = dt.datetime(2026, 9, 8, 8, 0, tzinfo=dt.timezone.utc)
        for provider, status, code in (
            ("MANUAL_CALENDAR", "OPEN_REGULAR", "CALENDAR_PROVIDER_INVALID"),
            (CAL.PROVIDER_ID, "CLOSED", "KRX_OBSERVATION_CLOSED_DERIVATION_FORBIDDEN"),
        ):
            calendar = dict(packet["calendar"])
            calendar["provider_id"] = provider
            calendar["status"] = status
            if status == "CLOSED":
                calendar["open_at"] = None
                calendar["close_at"] = None
            with self.subTest(provider=provider, status=status), self.assertRaisesRegex(
                BARS.KrxSessionCalendarV2Error, code
            ):
                BARS.validate_calendar(calendar, decision, BARS.load_contract())


if __name__ == "__main__":
    unittest.main()
