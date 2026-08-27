#!/usr/bin/env python3
"""P8-04 live-axis evidence wiring regression."""

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
SCRIPT = ROOT / "briefing" / "daily_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("p804_daily_orchestrator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
GENERATED_AT = "2026-08-26T23:59:59Z"


def crypto_rows() -> dict:
    return {
        "BTC_TREND": MODULE.build_btc_trend("2026-08-26"),
        "BTC_RISK": MODULE.build_btc_risk("2026-08-26"),
        "STABLECOIN_NET_ISSUANCE": MODULE.build_stablecoin("2026-08-26"),
    }


def all_authorities_false(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_authorized") and item is not False:
                return False
            if not all_authorities_false(item):
                return False
    elif isinstance(value, list):
        return all(all_authorities_false(item) for item in value)
    return True


def v4_free_market_row(root: Path) -> tuple[dict, dict]:
    captured = dt.datetime(2026, 8, 27, 1, 2, 3, tzinfo=dt.timezone.utc)
    raw = json.dumps({"observations": [{
        "date": "2026-08-26", "value": "15.50",
        "realtime_start": "2026-08-27", "realtime_end": "2026-08-27",
    }]}, sort_keys=True).encode()
    bundle = MODULE.FRED_VIX_PROVENANCE.build_evidence_bundle(captured, raw)
    MODULE.FRED_VIX_PROVENANCE.publish_evidence_bundle(root, bundle)
    authority = json.loads((ROOT / "config/free_market_data_contract.json").read_text())["authority"]
    packet = {
        "schema_version": "free_market_data_capture/4",
        "contract_version": "free_market_data/2",
        "observed_at_utc": "2026-08-27T01:02:03Z",
        "fred": {
            "series_id": "VIXCLS", "observation_date": "2026-08-26",
            "value": "15.50", "realtime_start": "2026-08-27",
            "realtime_end": "2026-08-27", "status": "READY",
            "source_scope": "FRED_OFFICIAL_SERIES_API",
            "response_sha256": bundle["pointer"]["raw_response_sha256"],
            "raw_retention": "APPEND_ONLY_CONTENT_ADDRESSED",
            "evidence": bundle["pointer"],
        },
        "alpaca": {
            "status": "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL",
            "feed": "iex", "source_scope": "IEX_ONLY_PARTIAL_US_MARKET",
            "bars": [], "raw_sha256": None, "daily_bars": [],
            "daily_raw_sha256": None, "daily_timeframe": "1Day",
            "daily_adjustment": "raw",
        },
        "authority": authority,
    }
    packet["packet_sha256"] = MODULE.payload_sha256(packet)
    with mock.patch.object(MODULE, "ROOT", root):
        row = MODULE._classify_free_market_data(
            {"kind": "ready", "value": packet}, "2026-08-27"
        )
    return row, bundle


class RegimeLiveAxisAdapterTest(unittest.TestCase):
    def test_adapter_contract_is_versioned_and_all_authority_is_false(self):
        contract = MODULE.LIVE_AXIS_ADAPTER.load_contract()
        self.assertEqual(contract["contract_version"], "regime_live_axis_adapter/v2")
        self.assertEqual(contract["mode"], "EVIDENCE_ONLY_NO_INTERPRETATION")
        self.assertTrue(all_authorities_false(contract))
        self.assertEqual(
            contract["bindings"]["US/RISK_VOL"]["source_component"],
            "FREE_MARKET_DATA",
        )

    def test_adapter_contract_drift_fails_closed(self):
        contract = MODULE.LIVE_AXIS_ADAPTER.load_contract()
        contract["authority"]["regime_interpretation_authorized"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.LIVE_AXIS_ADAPTER.LiveAxisAdapterError,
                "CONTRACT_INVALID",
            ):
                MODULE.LIVE_AXIS_ADAPTER.load_contract(path)

    def test_real_crypto_archives_define_three_evidence_axes_only(self):
        outputs = MODULE.build_regime_outputs(GENERATED_AT, crypto_rows())
        crypto = outputs["CRYPTO"]

        self.assertEqual(
            crypto["coverage"]["defined_axes"],
            ["TREND", "RISK_VOL", "LIQUIDITY"],
        )
        self.assertEqual(crypto["coverage"]["ratio"], "3/5")
        self.assertEqual(crypto["regime"], "UNKNOWN")
        self.assertEqual(crypto["direction"], "UNKNOWN")
        self.assertIsNone(crypto["confidence"])
        self.assertEqual(outputs["US"]["coverage"]["ratio"], "0/5")
        self.assertEqual(outputs["KR"]["coverage"]["ratio"], "0/5")
        self.assertTrue(all_authorities_false(outputs))

    def test_transient_vix_pointer_does_not_define_axis_without_provenance(self):
        outputs = MODULE.build_regime_outputs(
            GENERATED_AT,
            {"FREE_MARKET_DATA": {"component_id": "FREE_MARKET_DATA"}},
        )
        us = outputs["US"]

        self.assertEqual(us["coverage"]["defined_axes"], [])
        self.assertEqual(us["coverage"]["ratio"], "0/5")
        self.assertEqual(us["factor_results"]["TREND"]["status"], "UNDEFINED")
        self.assertEqual(
            us["factor_results"]["RISK_VOL"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )
        self.assertEqual(us["regime"], "UNKNOWN")

    def test_append_only_vix_defines_us_risk_axis_without_regime_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row, bundle = v4_free_market_row(root)
            self.assertEqual(row["status"], "DEGRADED")
            self.assertTrue(row["validated"])
            with mock.patch.object(MODULE.LIVE_AXIS_ADAPTER, "ROOT", root):
                outputs = MODULE.build_regime_outputs(
                    "2026-08-27T23:59:59Z", {"FREE_MARKET_DATA": row}
                )
        us = outputs["US"]
        self.assertEqual(us["coverage"]["defined_axes"], ["RISK_VOL"])
        self.assertEqual(us["coverage"]["ratio"], "1/5")
        self.assertEqual(us["regime"], "UNKNOWN")
        self.assertEqual(us["direction"], "UNKNOWN")
        self.assertEqual(
            us["factor_results"]["RISK_VOL"]["evidence"]["sha256"],
            bundle["pointer"]["raw_response_sha256"],
        )
        self.assertTrue(all_authorities_false(us))

    def test_vix_raw_tamper_fails_closed_per_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row, bundle = v4_free_market_row(root)
            (root / bundle["pointer"]["raw_path"]).write_bytes(b"not-gzip")
            with mock.patch.object(MODULE.LIVE_AXIS_ADAPTER, "ROOT", root):
                factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(
                    {"FREE_MARKET_DATA": row}, "2026-08-27T23:59:59Z"
                )
        self.assertEqual(factors["US"]["RISK_VOL"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["US"]["RISK_VOL"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )

    def test_axis_evidence_binds_exact_immutable_raw_response(self):
        rows = crypto_rows()
        first = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)
        second = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        self.assertEqual(first, second)
        for axis in ("TREND", "RISK_VOL", "LIQUIDITY"):
            evidence = first["CRYPTO"][axis]["evidence"]
            self.assertRegex(evidence["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(evidence["uri"].startswith("atlas-raw-response://"))
            self.assertTrue(
                first["CRYPTO"][axis]["transform_version"].startswith(
                    "regime_live_axis_"
                )
            )
        self.assertEqual(
            first["CRYPTO"]["TREND"]["evidence"]["sha256"],
            first["CRYPTO"]["RISK_VOL"]["evidence"]["sha256"],
        )
        self.assertNotEqual(
            first["CRYPTO"]["TREND"]["evidence"]["sha256"],
            first["CRYPTO"]["LIQUIDITY"]["evidence"]["sha256"],
        )

    def test_rehashed_component_tampering_cannot_remain_defined(self):
        rows = crypto_rows()
        rows["BTC_TREND"]["packet"]["direction"] = "BELOW_200DMA"

        factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        self.assertEqual(factors["CRYPTO"]["TREND"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["TREND"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )

    def test_future_or_authorized_component_fails_closed_per_axis(self):
        future = crypto_rows()["BTC_TREND"]
        future["generated_at"] = "2026-08-27T00:00:00Z"
        authorized = crypto_rows()["BTC_TREND"]
        authorized["authority"]["trading_authorized"] = True

        for row in (future, authorized):
            with self.subTest(row=row):
                factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(
                    {"BTC_TREND": row}, GENERATED_AT
                )
                self.assertEqual(
                    factors["CRYPTO"]["TREND"]["status"], "UNDEFINED"
                )

    def test_market_membership_and_watchlist_are_not_promoted_to_axes(self):
        rows = crypto_rows()
        rows["US_BREADTH_MEMBERSHIP"] = MODULE.component_row(
            "US_BREADTH_MEMBERSHIP", "READY", None, validated=True,
            packet={"member_count": 9999},
        )
        rows["KRX_POST_CLOSE"] = MODULE.component_row(
            "KRX_POST_CLOSE", "READY", None, validated=True,
            packet={"symbols": ["005930", "000660"]},
        )

        outputs = MODULE.build_regime_outputs(GENERATED_AT, rows)

        self.assertEqual(outputs["US"]["coverage"]["ratio"], "0/5")
        self.assertEqual(outputs["KR"]["coverage"]["ratio"], "0/5")

    def test_header_reports_axis_wiring_without_claiming_regime_authority(self):
        outputs = MODULE.build_regime_outputs(GENERATED_AT, crypto_rows())
        row = MODULE.build_three_market_header(outputs, "evening", GENERATED_AT)

        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(
            row["reason"],
            "LIVE_AXIS_EVIDENCE_WIRED_REGIME_SCORING_UNRATIFIED",
        )
        self.assertIsNone(row["packet"]["summary"]["ranked_market"])
        self.assertIsNone(row["packet"]["summary"]["favorable_market"])
        self.assertIsNone(row["packet"]["summary"]["action"])
        self.assertTrue(all_authorities_false(row))


if __name__ == "__main__":
    unittest.main()
