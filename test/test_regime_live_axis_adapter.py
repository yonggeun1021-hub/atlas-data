#!/usr/bin/env python3
"""P8-04 live-axis evidence wiring regression."""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
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
        "CRYPTO_BREADTH": MODULE.build_crypto_breadth("2026-08-26"),
    }


UPBIT_FORBIDDEN_INTERPRETED_VALUES = (
    "POSITIVE", "NEGATIVE", "NEUTRAL", "RISK_ON", "RISK_OFF", "STRESS",
    "IMPROVING", "DETERIORATING", "STABLE",
)


def assert_no_interpreted_axis_values(testcase, factor_results: dict) -> None:
    """P1-CR-08 hard invariant: not one axis result -- for any market, any
    axis, any input combination -- may ever carry an interpreted value.
    Only ``status in {"DEFINED", "UNDEFINED"}`` is permitted; nothing else
    in a factor result (evidence URI/sha, warnings, transform_version) may
    contain any of the Notion "5축 판정"-style interpreted literals either.
    """
    for axis, factor in factor_results.items():
        testcase.assertIn(factor["status"], ("DEFINED", "UNDEFINED"))
        rendered = json.dumps(factor, ensure_ascii=False)
        for forbidden in UPBIT_FORBIDDEN_INTERPRETED_VALUES:
            testcase.assertIsNone(
                re.search(rf"\b{forbidden}\b", rendered),
                f"{axis} leaked interpreted value {forbidden}",
            )


def leadership_row(
    as_of_vintage: str, generated_at: str, *, status: str = "READY", packet=None
) -> dict:
    return MODULE.component_row(
        "CRYPTO_LEADERSHIP",
        status,
        None if status == "READY" else "TEST_FIXTURE_BLOCK",
        as_of_date=as_of_vintage,
        generated_at=generated_at,
        source_packet_path="evidence/crypto/breadth/raw",
        validated=True,
        packet=packet,
    )


def upbit_liquidity_row(
    snapshot_date: str, generated_at: str, *, status: str = "READY", packet=None
) -> dict:
    return MODULE.component_row(
        "UPBIT_MARKET_EVIDENCE",
        status,
        None if status == "READY" else "TEST_FIXTURE_BLOCK",
        as_of_date=snapshot_date,
        generated_at=generated_at,
        source_packet_path=f"evidence/crypto/upbit/microstructure/{snapshot_date}",
        validated=True,
        contract_version="upbit_market_evidence_packet/1",
        packet=packet,
    )


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


def korea_market_signal_row(root: Path) -> tuple[dict, dict]:
    source = MODULE.LIVE_AXIS_ADAPTER.KOREA_MARKET_SIGNALS
    contract = source.load_contract()
    packet = {
        "schema_version": source.SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "status": "OBSERVED_UNCLASSIFIED",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "previous_date": "2026-08-25",
        "as_of_date": "2026-08-26",
        "generated_at": "2026-08-26T09:20:00Z",
        "available_at": "2026-08-26T09:20:00Z",
        "source": {"name": "KRX", "raw_persistence": 0, "per_symbol_persistence": 0},
        "axes": {
            axis: {"status": "OBSERVED", "measurement": {"test_fixture": axis}}
            for axis in contract["required_axes"]
        },
        "coverage": {
            "required_axes": list(contract["required_axes"]),
            "observed_axes": list(contract["required_axes"]),
            "observed_count": 5,
            "required_count": 5,
            "ratio": "5/5",
        },
        "authority": contract["authority"],
    }
    packet["payload_sha256"] = source.payload_sha256(packet)
    relative = Path("data/observations/korea_market_signals/2026-08-26")
    path = root / relative / "packet.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
    row = MODULE.component_row(
        "KOREA_MARKET_SIGNALS",
        "READY",
        None,
        as_of_date=packet["as_of_date"],
        generated_at=packet["generated_at"],
        available_at=packet["available_at"],
        source_packet_path=relative.as_posix(),
        source_packet_sha256=packet["payload_sha256"],
        validated=True,
        authority=packet["authority"],
        contract_version=packet["contract_version"],
        packet=packet,
    )
    return row, packet


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
        self.assertEqual(contract["contract_version"], "regime_live_axis_adapter/v6")
        self.assertEqual(contract["mode"], "EVIDENCE_ONLY_NO_INTERPRETATION")
        self.assertTrue(all_authorities_false(contract))
        self.assertEqual(
            contract["bindings"]["US/RISK_VOL"]["source_component"],
            "FREE_MARKET_DATA",
        )
        self.assertEqual(
            contract["bindings"]["CRYPTO/BREADTH"]["source_component"],
            "CRYPTO_BREADTH",
        )
        self.assertEqual(
            contract["bindings"]["CRYPTO/LEADERSHIP"]["source_component"],
            "CRYPTO_LEADERSHIP",
        )
        self.assertEqual(
            set(contract["bindings"]["CRYPTO/LIQUIDITY"]["source_components"]),
            {"STABLECOIN_NET_ISSUANCE", "UPBIT_MARKET_EVIDENCE"},
        )
        self.assertEqual(contract["deferred_axes"], {})
        self.assertEqual(
            contract["bindings"]["KR/BREADTH"]["source_component"],
            "KOREA_MARKET_SIGNALS",
        )
        self.assertIn(
            "KOREA_BREADTH_LINEAGE_RECEIPT_WITHOUT_PARTICIPATION_COUNTS",
            contract["non_promotable_evidence"],
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

    def test_missing_combined_korea_packet_fails_closed_without_defining_axes(self):
        outputs = MODULE.build_regime_outputs(GENERATED_AT, crypto_rows())
        breadth = outputs["KR"]["factor_results"]["BREADTH"]
        self.assertEqual(breadth["status"], "UNDEFINED")
        self.assertEqual(breadth["warnings"], ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"])
        self.assertEqual(outputs["KR"]["coverage"]["ratio"], "0/5")
        self.assertTrue(all_authorities_false(outputs["KR"]))

    def test_combined_korea_packet_defines_all_five_axes_without_regime_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row, packet = korea_market_signal_row(root)
            with mock.patch.object(MODULE.LIVE_AXIS_ADAPTER, "ROOT", root):
                outputs = MODULE.build_regime_outputs(
                    GENERATED_AT, {"KOREA_MARKET_SIGNALS": row}
                )
        korea = outputs["KR"]
        self.assertEqual(korea["coverage"]["ratio"], "5/5")
        self.assertEqual(korea["regime"], "UNKNOWN")
        self.assertEqual(korea["direction"], "UNKNOWN")
        self.assertIsNone(korea["confidence"])
        for axis in packet["axes"]:
            factor = korea["factor_results"][axis]
            self.assertEqual(factor["status"], "DEFINED")
            self.assertEqual(factor["evidence"]["sha256"], packet["payload_sha256"])
            self.assertEqual(factor["warnings"], ["REGIME_INTERPRETATION_UNAUTHORIZED"])
        self.assertTrue(all_authorities_false(korea))

    def test_real_crypto_archives_define_three_evidence_axes_with_breadth_correctly_blocked(
        self,
    ):
        """P1-CR-08: CRYPTO/BREADTH is now a real binding (bound since this
        PR; ``CRYPTO_BREADTH`` rows are already produced by
        daily_orchestrator.py since P1-CR-06). On this specific committed
        real evidence date (2026-08-26), the real CRYPTO_BREADTH row is
        genuinely ``POLICY_BLOCKED``/``TAXONOMY_COVERAGE_UNKNOWN`` -- Kraken's
        real captured universe on that day has assets ranked below the
        Top-N cutoff whose taxonomy category is not yet ratified (see
        crypto_breadth.py's ``qualified_members()``). This is a genuine,
        real fail-closed result, not a test gap: it demonstrates BREADTH's
        binding correctly reporting UNDEFINED for a real
        taxonomy-incomplete day. A real DEFINED demonstration (2026-08-28,
        the one date in current committed evidence where taxonomy coverage
        is complete) is covered separately below.

        CRYPTO/LEADERSHIP stays UNDEFINED here for a different, independent
        reason: no ``CRYPTO_LEADERSHIP`` component row exists in
        ``crypto_rows()`` because daily_orchestrator.py does not produce one
        yet (see docs/regime_live_axis_adapter_contract.md) -- this, too, is
        the real, current, honest production state.
        """
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
        self.assertEqual(
            crypto["factor_results"]["BREADTH"]["status"], "UNDEFINED"
        )
        self.assertEqual(
            crypto["factor_results"]["BREADTH"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )
        self.assertEqual(
            crypto["factor_results"]["LEADERSHIP"]["status"], "UNDEFINED"
        )
        self.assertEqual(
            crypto["factor_results"]["LEADERSHIP"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )
        self.assertEqual(outputs["US"]["coverage"]["ratio"], "0/5")
        self.assertEqual(outputs["KR"]["coverage"]["ratio"], "0/5")
        self.assertEqual(
            {
                axis: outputs["KR"]["factor_results"][axis]["warnings"]
                for axis in ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP")
            },
            {axis: ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"] for axis in (
                "TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"
            )},
        )
        self.assertTrue(all_authorities_false(outputs))
        assert_no_interpreted_axis_values(self, crypto["factor_results"])

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
        assert_no_interpreted_axis_values(self, first["CRYPTO"])

    def test_rehashed_component_tampering_cannot_remain_defined(self):
        rows = crypto_rows()
        rows["BTC_TREND"]["packet"]["direction"] = "BELOW_200DMA"

        factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        self.assertEqual(factors["CRYPTO"]["TREND"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["TREND"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )

    def test_crypto_breadth_defined_with_real_evidence_on_taxonomy_complete_day(
        self,
    ):
        """2026-08-28 is the one committed evidence date where the real
        Kraken-captured universe clears the taxonomy gate (see the
        UNDEFINED/taxonomy-incomplete demonstration above, which uses
        2026-08-26 -- a genuinely different real day). Isolated to just the
        BREADTH axis (no BTC_TREND/RISK/STABLECOIN rows needed) since
        stablecoin evidence does not extend to 2026-08-28 in this repo.
        """
        generated_at = "2026-08-28T23:59:59Z"
        row = MODULE.build_crypto_breadth("2026-08-28")
        self.assertEqual(row["status"], "READY")
        rows = {"CRYPTO_BREADTH": row}

        first = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, generated_at)
        second = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, generated_at)

        self.assertEqual(first, second)
        breadth = first["CRYPTO"]["BREADTH"]
        self.assertEqual(breadth["status"], "DEFINED")
        self.assertRegex(breadth["evidence"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(breadth["evidence"]["uri"].startswith("atlas-raw-response://"))
        self.assertTrue(breadth["evidence"]["uri"].endswith("_manifest.json"))
        self.assertEqual(
            breadth["transform_version"], "regime_live_axis_crypto_breadth/v1"
        )
        self.assertTrue(all_authorities_false(first))
        assert_no_interpreted_axis_values(self, first["CRYPTO"])

    def test_rehashed_crypto_breadth_tampering_cannot_remain_defined(self):
        row = MODULE.build_crypto_breadth("2026-08-28")
        row["packet"]["selected_asset_count"] = -9999
        rows = {"CRYPTO_BREADTH": row}

        factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(
            rows, "2026-08-28T23:59:59Z"
        )

        self.assertEqual(factors["CRYPTO"]["BREADTH"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["BREADTH"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )

    def test_crypto_breadth_undefined_when_row_not_ready(self):
        rows = crypto_rows()
        rows["CRYPTO_BREADTH"] = MODULE.component_row(
            "CRYPTO_BREADTH", "POLICY_BLOCKED", "TAXONOMY_COVERAGE_UNKNOWN",
        )

        factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        self.assertEqual(factors["CRYPTO"]["BREADTH"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["BREADTH"]["warnings"],
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

    def test_rehashed_korea_breadth_lineage_receipt_cannot_define_axis(self):
        receipt = json.loads(
            (
                ROOT
                / "data/observations/korea_breadth_context/2026-08-21/packet.json"
            ).read_text(encoding="utf-8")
        )
        receipt["markets"]["KOSPI"]["advancing_count"] = 9999
        receipt["markets"]["KOSDAQ"]["advancing_count"] = 9999
        unsigned = {
            key: value for key, value in receipt.items() if key != "payload_sha256"
        }
        receipt["payload_sha256"] = MODULE.payload_sha256(unsigned)
        rows = {
            "KOREA_BREADTH_CONTEXT": MODULE.component_row(
                "KOREA_BREADTH_CONTEXT",
                "READY",
                None,
                validated=True,
                packet=receipt,
            )
        }

        outputs = MODULE.build_regime_outputs(GENERATED_AT, rows)

        breadth = outputs["KR"]["factor_results"]["BREADTH"]
        self.assertEqual(breadth["status"], "UNDEFINED")
        self.assertEqual(breadth["warnings"], ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"])
        self.assertIsNone(breadth["evidence"])
        self.assertEqual(outputs["KR"]["coverage"]["ratio"], "0/5")
        self.assertTrue(all_authorities_false(outputs["KR"]))

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

    # -- P1-CR-08: CRYPTO/LEADERSHIP (newly bound) --------------------------
    #
    # crypto_leadership.py's own dual-window correctness (7-day pilot,
    # 30-day primary, continuity, taxonomy layering) is already covered
    # exhaustively by test/test_crypto_leadership.py -- these tests instead
    # prove only the ADAPTER's binding/rederivation-check/PIT logic, against
    # a hand-built but schema-faithful packet. Real evidence/crypto/breadth/
    # raw/ spans only ~9 committed days as of this PR, short of even the
    # 7-day pilot window's continuity requirement, so a real end-to-end
    # OBSERVED_UNCLASSIFIED packet cannot yet be produced from committed
    # repository evidence -- see docs/regime_live_axis_adapter_contract.md.

    def test_crypto_leadership_defined_when_dual_window_observed_and_pit_valid(self):
        vintage = "2026-08-27"
        end_date = "2026-08-26"
        manifest_sha = "a" * 64
        fake_packet = {
            "contract_version": "crypto_leadership_contract/v2",
            "status": "OBSERVED_UNCLASSIFIED",
            "as_of_date": end_date,
            "windows": [
                {
                    "window_id": "pilot_7d",
                    "status": "OBSERVED_UNCLASSIFIED",
                    "daily_points": [
                        {
                            "as_of_date": end_date,
                            "lineage": {"available_at": "2026-08-27T00:10:00Z"},
                        },
                    ],
                },
                {
                    "window_id": "primary_30d",
                    "status": "OBSERVED_UNCLASSIFIED",
                    "daily_points": [
                        {
                            "as_of_date": end_date,
                            "lineage": {"available_at": "2026-08-27T00:05:00Z"},
                        },
                    ],
                },
            ],
            "lineage": {
                "manifest_sha256_by_date": [
                    {"as_of_date": end_date, "manifest_sha256": manifest_sha},
                ],
            },
        }
        row = leadership_row(
            vintage, GENERATED_AT, packet={"status": "OBSERVED_UNCLASSIFIED"}
        )
        rows = {"CRYPTO_LEADERSHIP": row}

        with mock.patch.object(
            MODULE.LIVE_AXIS_ADAPTER.CRYPTO_LEADERSHIP,
            "build_transform",
            return_value=fake_packet,
        ) as stub:
            first = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)
            second = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        stub.assert_called_with(mock.ANY, end_date=end_date)
        self.assertEqual(first, second)
        leadership = first["CRYPTO"]["LEADERSHIP"]
        self.assertEqual(leadership["status"], "DEFINED")
        self.assertEqual(leadership["observation_date"], end_date)
        self.assertEqual(leadership["available_at"], "2026-08-27T00:10:00Z")
        self.assertEqual(leadership["evidence"]["sha256"], manifest_sha)
        self.assertTrue(leadership["evidence"]["uri"].startswith("atlas-raw-response://"))
        self.assertIn(vintage, leadership["evidence"]["uri"])
        self.assertEqual(
            leadership["transform_version"], "regime_live_axis_crypto_leadership/v1"
        )
        self.assertTrue(all_authorities_false(first))
        assert_no_interpreted_axis_values(self, first["CRYPTO"])

    def test_crypto_leadership_undefined_when_window_not_fully_observed(self):
        vintage = "2026-08-27"
        end_date = "2026-08-26"
        fake_packet = {
            "contract_version": "crypto_leadership_contract/v2",
            "status": "PARTIAL",
            "as_of_date": end_date,
            "windows": [],
            "lineage": {"manifest_sha256_by_date": []},
        }
        row = leadership_row(
            vintage, GENERATED_AT, packet={"status": "OBSERVED_UNCLASSIFIED"}
        )
        rows = {"CRYPTO_LEADERSHIP": row}

        with mock.patch.object(
            MODULE.LIVE_AXIS_ADAPTER.CRYPTO_LEADERSHIP,
            "build_transform",
            return_value=fake_packet,
        ):
            factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        self.assertEqual(factors["CRYPTO"]["LEADERSHIP"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["LEADERSHIP"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )

    def test_crypto_leadership_undefined_when_row_missing(self):
        factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors({}, GENERATED_AT)

        self.assertEqual(factors["CRYPTO"]["LEADERSHIP"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["LEADERSHIP"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )

    # -- P1-CR-08: CRYPTO/LIQUIDITY extended with a second qualifying input -

    def test_crypto_liquidity_defined_from_upbit_alone_when_stablecoin_absent(self):
        snapshot_date = "2026-08-26"
        record = {
            "snapshot_date": snapshot_date,
            "generated_at": GENERATED_AT,
            "builder": {"output_schema_version": "upbit_market_evidence_packet/1"},
            "policy_ratified": True,
            "summary": {"market_count": 5, "packet_count": 5, "error_count": 0},
            "payload_sha256": "c" * 64,
        }
        row = upbit_liquidity_row(
            snapshot_date, GENERATED_AT,
            packet={"market_count": 5, "packet_count": 5, "error_count": 0},
        )
        rows = {"UPBIT_MARKET_EVIDENCE": row}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence" / "crypto" / "upbit" / "microstructure" / snapshot_date).mkdir(
                parents=True
            )
            with mock.patch.object(MODULE.LIVE_AXIS_ADAPTER, "ROOT", root), \
                mock.patch.object(
                    MODULE.LIVE_AXIS_ADAPTER.UPBIT_MARKET_EVIDENCE,
                    "rebuild",
                    return_value=record,
                ) as stub:
                factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        stub.assert_called_with(snapshot_date, mock.ANY)
        liquidity = factors["CRYPTO"]["LIQUIDITY"]
        self.assertEqual(liquidity["status"], "DEFINED")
        self.assertEqual(liquidity["evidence"]["sha256"], "c" * 64)
        self.assertIn(
            "CRYPTO_LIQUIDITY_STABLECOIN_INPUT_UNAVAILABLE", liquidity["warnings"]
        )
        self.assertTrue(all_authorities_false(factors))
        assert_no_interpreted_axis_values(self, factors["CRYPTO"])

    def test_crypto_liquidity_prefers_stablecoin_when_both_qualify(self):
        """Both inputs are synthetic/mocked here (rather than reusing the
        real evidence-backed ``crypto_rows()`` stablecoin row) so this test
        can patch ``LIVE_AXIS_ADAPTER.ROOT`` to one self-contained temp
        directory for both the stablecoin and Upbit source-directory checks,
        without needing to relocate real committed evidence.
        """
        snapshot_date = "2026-08-26"
        upbit_record = {
            "snapshot_date": snapshot_date,
            "generated_at": GENERATED_AT,
            "builder": {"output_schema_version": "upbit_market_evidence_packet/1"},
            "policy_ratified": True,
            "summary": {"market_count": 5, "packet_count": 5, "error_count": 0},
            "payload_sha256": "d" * 64,
        }
        stablecoin_available_at = "2026-08-26T00:00:00Z"
        stablecoin_latest = {
            "observation_date": "2026-08-25",
            "daily_net_issuance_native_usd_peg": "1000",
            "daily_status": "AVAILABLE",
            "weekly_net_issuance_native_usd_peg": "5000",
            "weekly_status": "AVAILABLE",
        }
        stablecoin_packet = {
            "transform_version": "stablecoin_net_issuance/v1",
            "rows": [stablecoin_latest],
            "lineage": {
                "available_at": stablecoin_available_at,
                "vintage_date": snapshot_date,
            },
            "source": {"response_sha256": "f" * 64},
        }
        stablecoin_row = MODULE.component_row(
            "STABLECOIN_NET_ISSUANCE", "READY", None,
            as_of_date=snapshot_date,
            generated_at=stablecoin_available_at,
            source_packet_path=f"evidence/stablecoin/raw/{snapshot_date}",
            validated=True,
            packet=stablecoin_latest,
        )
        rows = {
            "STABLECOIN_NET_ISSUANCE": stablecoin_row,
            "UPBIT_MARKET_EVIDENCE": upbit_liquidity_row(
                snapshot_date, GENERATED_AT,
                packet={"market_count": 5, "packet_count": 5, "error_count": 0},
            ),
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence" / "crypto" / "upbit" / "microstructure" / snapshot_date).mkdir(
                parents=True
            )
            (root / "evidence" / "stablecoin" / "raw" / snapshot_date).mkdir(parents=True)
            with mock.patch.object(MODULE.LIVE_AXIS_ADAPTER, "ROOT", root), \
                mock.patch.object(
                    MODULE.LIVE_AXIS_ADAPTER.UPBIT_MARKET_EVIDENCE,
                    "rebuild",
                    return_value=upbit_record,
                ), mock.patch.object(
                    MODULE.LIVE_AXIS_ADAPTER.STABLECOIN,
                    "build_transform",
                    return_value=stablecoin_packet,
                ):
                factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        liquidity = factors["CRYPTO"]["LIQUIDITY"]
        self.assertEqual(liquidity["status"], "DEFINED")
        self.assertEqual(liquidity["evidence"]["sha256"], "f" * 64)
        self.assertTrue(
            liquidity["evidence"]["uri"].endswith("stablecoincharts_all.json.gz")
        )
        self.assertNotIn(
            "CRYPTO_LIQUIDITY_STABLECOIN_INPUT_UNAVAILABLE", liquidity["warnings"]
        )
        self.assertNotIn(
            "CRYPTO_LIQUIDITY_UPBIT_MICROSTRUCTURE_INPUT_UNAVAILABLE",
            liquidity["warnings"],
        )
        self.assertTrue(all_authorities_false(factors))

    def test_crypto_liquidity_undefined_when_neither_input_present(self):
        factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors({}, GENERATED_AT)

        liquidity = factors["CRYPTO"]["LIQUIDITY"]
        self.assertEqual(liquidity["status"], "UNDEFINED")
        self.assertEqual(liquidity["warnings"], ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"])

    def test_crypto_liquidity_undefined_when_upbit_policy_unratified(self):
        snapshot_date = "2026-08-26"
        record = {
            "snapshot_date": snapshot_date,
            "generated_at": GENERATED_AT,
            "builder": {"output_schema_version": "upbit_market_evidence_packet/1"},
            "policy_ratified": False,
            "summary": {"market_count": 5, "packet_count": 5, "error_count": 0},
            "payload_sha256": "e" * 64,
        }
        row = upbit_liquidity_row(
            snapshot_date, GENERATED_AT,
            packet={"market_count": 5, "packet_count": 5, "error_count": 0},
        )
        rows = {"UPBIT_MARKET_EVIDENCE": row}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence" / "crypto" / "upbit" / "microstructure" / snapshot_date).mkdir(
                parents=True
            )
            with mock.patch.object(MODULE.LIVE_AXIS_ADAPTER, "ROOT", root), \
                mock.patch.object(
                    MODULE.LIVE_AXIS_ADAPTER.UPBIT_MARKET_EVIDENCE,
                    "rebuild",
                    return_value=record,
                ):
                factors = MODULE.LIVE_AXIS_ADAPTER.build_axis_factors(rows, GENERATED_AT)

        liquidity = factors["CRYPTO"]["LIQUIDITY"]
        self.assertEqual(liquidity["status"], "UNDEFINED")
        self.assertEqual(liquidity["warnings"], ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"])


if __name__ == "__main__":
    unittest.main()
