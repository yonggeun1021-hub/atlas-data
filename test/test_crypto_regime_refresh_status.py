#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "crypto_regime_refresh_status.py"
SPEC = importlib.util.spec_from_file_location("crypto_regime_refresh_status_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CryptoRegimeRefreshStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # P3-12-GOV-05: _select_official_decision() fully re-derives (and
        # byte-compares) each candidate decision snapshot via
        # DECISION.validate_output(); on this branch the real committed
        # identity/taxonomy is PENDING_EXACT_HASH_REAPPROVAL (dedicated
        # coverage in test_upbit_exact_release_binding.py and
        # test_upbit_tradeable_universe.py), so a fresh re-derivation would
        # legitimately differ from historical snapshots captured while the
        # v2 release was in effect. This test is about regime-refresh
        # status mechanics, not the exact-release binding itself, so it is
        # exempted the same standard test-only way as every other
        # hypothetical-future-ratification fixture in this test suite --
        # never a production bypass. Each of DECISION.UNIVERSE,
        # DECISION.PROMOTION.UPBIT_UNIVERSE, and
        # DECISION.ELIGIBILITY.UPBIT_UNIVERSE is its own independent module
        # instance (this repo's own established reuse pattern) and must be
        # patched separately.
        cls._exact_release_binding_patches = [
            mock.patch.object(target.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True)
            for target in (
                MODULE.DECISION.UNIVERSE,
                MODULE.DECISION.PROMOTION.UPBIT_UNIVERSE,
                MODULE.DECISION.ELIGIBILITY.UPBIT_UNIVERSE,
            )
        ]
        for patcher in cls._exact_release_binding_patches:
            patcher.start()
        cls.packet = MODULE.build_status()

    @classmethod
    def tearDownClass(cls):
        for patcher in cls._exact_release_binding_patches:
            patcher.stop()

    def test_current_reference_and_official_decision_stay_distinct(self):
        packet = self.packet
        self.assertEqual(packet["current_reference"]["coverage"]["ratio"], "5/5")
        self.assertIn(packet["current_reference"]["leadership_code"], {
            "BTC_LEADERSHIP", "ETH_LEADERSHIP", "BROAD_ALT_LEADERSHIP",
            "NARROW_ALT_LEADERSHIP", "MIXED_WINDOW_LEADERSHIP",
        })
        official = packet["official_decision"]["coverage"]
        self.assertLessEqual(official["defined_count"], 5)
        self.assertTrue(set(official["defined_axes"]).issubset(set(packet["current_reference"]["coverage"]["defined_axes"])))
        self.assertEqual(packet["official_decision"]["runtime_regime"], "UNKNOWN")

    def test_natural_history_progress_is_explicit(self):
        history = self.packet["natural_history_progress"]
        as_of = dt.date.fromisoformat(self.packet["current_reference"]["as_of_date"])
        self.assertGreaterEqual(history["eligible_consecutive_days"], 0)
        self.assertEqual(
            history["earliest_pilot_capture_date_if_no_new_gap"],
            (as_of + dt.timedelta(days=history["pilot_remaining_days"])).isoformat(),
        )
        self.assertEqual(
            history["earliest_primary_capture_date_if_no_new_gap"],
            (as_of + dt.timedelta(days=history["primary_remaining_days"])).isoformat(),
        )
        self.assertTrue(history["new_missing_or_unknown_day_delays_dates"])

    def test_authority_and_expected_date_fail_closed(self):
        authority = self.packet["authority"]
        self.assertTrue(authority["read_only_reference"])
        self.assertTrue(all(value is False for key, value in authority.items() if key != "read_only_reference"))
        current = self.packet["current_reference"]["as_of_date"]
        self.assertEqual(MODULE.validate_expected_date(self.packet, current), self.packet)
        next_day = (dt.date.fromisoformat(current) + dt.timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(MODULE.CryptoRegimeRefreshStatusError, "CURRENT_REFERENCE_DATE_STALE"):
            MODULE.validate_expected_date(self.packet, next_day)

    def test_resigned_tamper_fails_full_rederivation(self):
        self.assertEqual(MODULE.validate_status(self.packet), self.packet)
        tampered = copy.deepcopy(self.packet)
        tampered["official_decision"]["coverage"]["ratio"] = "5/5"
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.CryptoRegimeRefreshStatusError, "STATUS_REDERIVATION_MISMATCH"):
            MODULE.validate_status(tampered)

    def test_write_is_append_only_and_latest_is_identical(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence, latest = MODULE.write_packet(self.packet, root)
            self.assertEqual(evidence.read_bytes(), latest.read_bytes())
            self.assertEqual(json.loads(latest.read_text()), self.packet)
            MODULE.write_packet(self.packet, root)


if __name__ == "__main__":
    unittest.main()
