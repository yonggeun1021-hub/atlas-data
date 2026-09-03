#!/usr/bin/env python3
"""P5-10 five-axis to per-symbol entry/exit bridge regression."""
from __future__ import annotations

import copy
import datetime as dt
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "decision" / "crypto_axis_trade_bridge.py"
SPEC = importlib.util.spec_from_file_location("crypto_axis_trade_bridge", MODULE_PATH)
BRIDGE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BRIDGE)


def source_observation_ceiling(
    *, universe_entry: dict | None, market_evidence_entry: dict | None,
    realtime_entry: dict | None,
) -> str:
    """Return the newest timestamp the decision builder actually gates on.

    This is an offline-regression as-of, not a replacement for the scheduler's
    wall clock.  It deliberately preserves each committed source timestamp and
    takes their chronological maximum; it never clamps or rewrites a source.
    The decision builder remains responsible for rejecting a caller-supplied
    ``generated_at`` that precedes any of these timestamps.
    """
    timestamps: list[dt.datetime] = []

    def add(value: object, label: str) -> None:
        timestamps.append(BRIDGE.DECISION._parse_utc(value, label))

    if universe_entry is not None:
        packet = universe_entry.get("packet")
        if not isinstance(packet, dict):
            raise AssertionError("UNIVERSE_PACKET_MISSING")
        add(packet.get("available_at"), "universe.available_at")

    if market_evidence_entry is not None:
        record = market_evidence_entry.get("record")
        if not isinstance(record, dict) or not isinstance(record.get("packets"), dict):
            raise AssertionError("MARKET_EVIDENCE_RECORD_INVALID")
        add(record.get("generated_at"), "market_evidence.generated_at")
        for market, packet in record["packets"].items():
            if not isinstance(packet, dict):
                raise AssertionError(f"MARKET_EVIDENCE_PACKET_INVALID:{market}")
            add(packet.get("captured_at"), f"market_evidence.{market}.captured_at")

    if realtime_entry is not None:
        record = realtime_entry.get("record")
        run = record.get("run") if isinstance(record, dict) else None
        if not isinstance(run, dict):
            raise AssertionError("REALTIME_RUN_INVALID")
        add(run.get("ended_at"), "realtime.ended_at")

    if not timestamps:
        raise AssertionError("NO_COMMITTED_CRYPTO_SOURCE_TIMESTAMP")
    return max(timestamps).strftime("%Y-%m-%dT%H:%M:%SZ")


def latest_committed_source_observation_ceiling() -> str:
    return source_observation_ceiling(
        universe_entry=BRIDGE.DECISION.find_latest_universe_packet(),
        market_evidence_entry=BRIDGE.DECISION.find_latest_market_evidence_packet(),
        realtime_entry=BRIDGE.DECISION.find_latest_realtime_run(),
    )


@lru_cache(maxsize=1)
def current_contract_packet() -> dict:
    """Rebuild from real committed inputs under the checked-out contracts.

    A live-axis contract change intentionally makes older decision packets fail
    byte-exact validation.  The scheduled workflow creates a fresh decision
    packet before invoking this bridge, so this fixture mirrors that ordering
    instead of treating a pre-change committed packet as current authority.
    """
    with tempfile.TemporaryDirectory(prefix="axis_bridge_source_") as tmp:
        result = BRIDGE.DECISION.populate(
            generated_at=latest_committed_source_observation_ceiling(),
            allow_realtime_fallback=True,
            output_root=Path(tmp) / "decision",
            wire_regime_components=True,
        )
    return result["record"]


class ContractTests(unittest.TestCase):
    def test_contract_pins_existing_exit_priority_and_all_authority_false(self):
        contract = BRIDGE.load_contract()
        exit_contract = BRIDGE.EXIT_MANAGER.load_contract()
        self.assertEqual(contract["exit_policy"]["priority_categories"], exit_contract["priority_categories"])
        self.assertTrue(contract["authority"])
        self.assertTrue(all(value is False for value in contract["authority"].values()))
        self.assertEqual(contract["aggregate_policy_status"], "UNRATIFIED")
        self.assertEqual(contract["aggregate_regimes_currently_authorized"], ["UNKNOWN"])


class SourceAvailabilityRegressionTests(unittest.TestCase):
    SOURCE_COMMIT = "0" * 40

    @staticmethod
    def _one_second_before(value: str) -> str:
        parsed = BRIDGE.DECISION._parse_utc(value, "test.source_time")
        return (parsed - dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _rehash(record: dict, field: str = "payload_sha256") -> None:
        unsigned = copy.deepcopy(record)
        unsigned.pop(field, None)
        record[field] = BRIDGE.DECISION.payload_sha256(unsigned)

    def test_normal_ceiling_uses_universe_market_and_realtime_source_times(self):
        universe = {"packet": {"available_at": "2026-08-30T00:01:00Z"}}
        market = {"record": {
            "generated_at": "2026-08-30T00:02:00Z",
            "packets": {"KRW-BTC": {"captured_at": "2026-08-30T00:04:00Z"}},
        }}
        realtime = {"record": {"run": {"ended_at": "2026-08-30T00:03:00Z"}}}
        self.assertEqual(
            source_observation_ceiling(
                universe_entry=universe,
                market_evidence_entry=market,
                realtime_entry=realtime,
            ),
            "2026-08-30T00:04:00Z",
        )

    def test_current_committed_source_ceiling_builds_without_reusing_prior_decision_time(self):
        universe = BRIDGE.DECISION.find_latest_universe_packet()
        market = BRIDGE.DECISION.find_latest_market_evidence_packet()
        realtime = BRIDGE.DECISION.find_latest_realtime_run()
        generated_at = source_observation_ceiling(
            universe_entry=universe,
            market_evidence_entry=market,
            realtime_entry=realtime,
        )
        packet = BRIDGE.DECISION.build_snapshot(
            generated_at=generated_at,
            source_commit=self.SOURCE_COMMIT,
            universe_entry=universe,
            market_evidence_entry=market,
            realtime_entry=realtime,
        )
        self.assertEqual(packet["generated_at"], generated_at)
        self.assertEqual(
            set(packet["freshness_status"]),
            {"upbit_universe", "market_evidence", "realtime", "leadership", "overall"},
        )

    def test_future_dated_universe_still_fails_closed(self):
        universe = BRIDGE.DECISION.find_latest_universe_packet()
        available_at = universe["packet"]["available_at"]
        with self.assertRaisesRegex(
            BRIDGE.DECISION.CryptoPaperDecisionSnapshotError,
            "UNIVERSE_AVAILABLE_AT_FUTURE_DATED",
        ):
            BRIDGE.DECISION.build_snapshot(
                generated_at=self._one_second_before(available_at),
                source_commit=self.SOURCE_COMMIT,
                universe_entry=universe,
                market_evidence_entry=None,
                realtime_entry=None,
            )

    def test_future_dated_market_evidence_still_fails_closed(self):
        market = BRIDGE.DECISION.find_latest_market_evidence_packet()
        ceiling = source_observation_ceiling(
            universe_entry=None, market_evidence_entry=market, realtime_entry=None,
        )
        with self.assertRaisesRegex(
            BRIDGE.DECISION.CryptoPaperDecisionSnapshotError,
            "MARKET_EVIDENCE_(PACKET_)?FUTURE_DATED",
        ):
            BRIDGE.DECISION.build_snapshot(
                generated_at=self._one_second_before(ceiling),
                source_commit=self.SOURCE_COMMIT,
                universe_entry=None,
                market_evidence_entry=market,
                realtime_entry=None,
            )

    def test_future_dated_realtime_still_fails_closed(self):
        realtime = BRIDGE.DECISION.find_latest_realtime_run()
        ended_at = realtime["record"]["run"]["ended_at"]
        with self.assertRaisesRegex(
            BRIDGE.DECISION.CryptoPaperDecisionSnapshotError,
            "REALTIME_EVIDENCE_FUTURE_DATED",
        ):
            BRIDGE.DECISION.build_snapshot(
                generated_at=self._one_second_before(ended_at),
                source_commit=self.SOURCE_COMMIT,
                universe_entry=None,
                market_evidence_entry=None,
                realtime_entry=realtime,
            )

    def test_stale_universe_remains_stale(self):
        universe = copy.deepcopy(BRIDGE.DECISION.find_latest_universe_packet())
        universe["packet"]["policy_ratified"] = True
        universe["packet"]["taxonomy_ratified"] = True
        self._rehash(universe["packet"])
        universe["record"]["packet"] = universe["packet"]
        self._rehash(universe["record"])
        available = BRIDGE.DECISION._parse_utc(
            universe["packet"]["available_at"], "universe.available_at",
        )
        max_age = float(BRIDGE.DECISION.UNIVERSE.load_policy()["max_capture_age_hours"])
        generated_at = (available + dt.timedelta(hours=max_age, seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        taxonomy = copy.deepcopy(BRIDGE.DECISION.UNIVERSE.load_taxonomy())
        registry = copy.deepcopy(BRIDGE.DECISION.UNIVERSE.load_identity_registry())
        taxonomy["approval_status"] = "RATIFIED"
        taxonomy["effective_from"] = universe["packet"]["evaluation_as_of"]
        registry["approval_status"] = "RATIFIED"
        registry["effective_from"] = universe["packet"]["evaluation_as_of"]
        with (
            mock.patch.object(
                BRIDGE.DECISION.UNIVERSE, "load_taxonomy", return_value=taxonomy,
            ),
            mock.patch.object(
                BRIDGE.DECISION.UNIVERSE, "load_identity_registry", return_value=registry,
            ),
            # P3-12-GOV-05: standard test-only mock exempting this
            # hypothetical-future-ratification fixture from the
            # exact-release allowlist binding -- never a production bypass.
            mock.patch.object(
                BRIDGE.DECISION.UNIVERSE.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True,
            ),
        ):
            packet = BRIDGE.DECISION.build_snapshot(
                generated_at=generated_at,
                source_commit=self.SOURCE_COMMIT,
                universe_entry=universe,
                market_evidence_entry=None,
                realtime_entry=None,
            )
        self.assertEqual(packet["freshness_status"]["upbit_universe"], "STALE")

    def test_stale_market_evidence_remains_stale(self):
        stale = "STALE"
        packet = {
            "candles": {
                timeframe: {"freshness": {"status": stale}}
                for timeframe in BRIDGE.DECISION.CANDLE_FINALIZATION.TIMEFRAMES
            },
            "trades": {"freshness": {"status": stale}},
            "orderbook": {"freshness": {"status": stale}},
        }
        status, reason = BRIDGE.DECISION._market_evidence_freshness({
            "packets": {"KRW-BTC": packet},
            "policy_ratified": True,
            "errors": {},
        })
        self.assertEqual(status, stale)
        self.assertEqual(reason, "UPBIT_MARKET_EVIDENCE_COMPONENT_STALE")

    def test_stale_realtime_remains_stale(self):
        record = {
            "run": {
                "markets": ["KRW-BTC"],
                "message_log": [{}],
                "status": {
                    "markets": {"KRW-BTC": {}},
                    "connection_state": "CONNECTED",
                    "overall_status": "STALE",
                },
            }
        }
        with mock.patch.object(
            BRIDGE.DECISION.REALTIME_GATE,
            "load_freshness_policy_proposal",
            return_value={"approval_status": "RATIFIED"},
        ):
            status, reason = BRIDGE.DECISION._realtime_freshness(record)
        self.assertEqual(status, "STALE")
        self.assertEqual(reason, "UPBIT_REALTIME_GATE_STATUS_STALE")

    def test_mixed_market_date_remains_mixed_generation(self):
        universe = BRIDGE.DECISION.find_latest_universe_packet()
        market = copy.deepcopy(BRIDGE.DECISION.find_latest_market_evidence_packet())
        market["date"] = "1999-01-01"
        market["record"]["snapshot_date"] = market["date"]
        if market["record"].get("schema_version") == "upbit_microstructure_population/2":
            record_hash = market["record"]["universe_lineage"]["record_payload_sha256"]
            market["record"]["snapshot_key"] = f"{market['date']}-p3-{record_hash[:16]}"
        self._rehash(market["record"])
        generated_at = source_observation_ceiling(
            universe_entry=universe, market_evidence_entry=market, realtime_entry=None,
        )
        packet = BRIDGE.DECISION.build_snapshot(
            generated_at=generated_at,
            source_commit=self.SOURCE_COMMIT,
            universe_entry=universe,
            market_evidence_entry=market,
            realtime_entry=None,
        )
        self.assertEqual(packet["freshness_status"]["market_evidence"], "MIXED_GENERATION")

    def test_mixed_realtime_date_remains_mixed_generation(self):
        universe = BRIDGE.DECISION.find_latest_universe_packet()
        realtime = copy.deepcopy(BRIDGE.DECISION.find_latest_realtime_run())
        realtime["date"] = "1999-01-01"
        generated_at = source_observation_ceiling(
            universe_entry=universe, market_evidence_entry=None, realtime_entry=realtime,
        )
        packet = BRIDGE.DECISION.build_snapshot(
            generated_at=generated_at,
            source_commit=self.SOURCE_COMMIT,
            universe_entry=universe,
            market_evidence_entry=None,
            realtime_entry=realtime,
        )
        self.assertEqual(packet["freshness_status"]["realtime"], "MIXED_GENERATION")


class RealEvidenceTests(unittest.TestCase):
    def test_current_contract_packet_builds_honest_fail_closed_bridge(self):
        packet = current_contract_packet()
        result = BRIDGE.build_bridge(packet)
        self.assertEqual(BRIDGE.validate_output(result), result)
        self.assertEqual(result["five_axis"]["required_count"], 5)
        expected_defined = sum(
            row["status"] == "DEFINED"
            for row in packet["crypto_regime_five_axis"].values()
        )
        self.assertEqual(result["five_axis"]["defined_count"], expected_defined)
        self.assertEqual(result["five_axis"]["all_defined"], expected_defined == 5)
        self.assertEqual(result["aggregate_policy"]["status"], "UNRATIFIED")
        self.assertEqual(result["aggregate_policy"]["regime"], "UNKNOWN")
        self.assertEqual(result["summary"]["automatic_entry_count"], 0)
        self.assertEqual(result["summary"]["automatic_exit_count"], 0)
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_source_axis_tamper_cannot_be_hidden_by_rehashing(self):
        packet = current_contract_packet()
        tampered = copy.deepcopy(packet)
        tampered["crypto_regime_five_axis"]["BREADTH"]["status"] = "DEFINED"
        tampered["payload_sha256"] = BRIDGE.DECISION.payload_sha256(tampered)
        with self.assertRaisesRegex(BRIDGE.CryptoAxisTradeBridgeError, "SOURCE_DECISION_INVALID"):
            BRIDGE.build_bridge(tampered)

    def test_populate_is_idempotent_and_byte_stable(self):
        packet = current_contract_packet()
        with tempfile.TemporaryDirectory(prefix="axis_bridge_") as tmp:
            source = Path(tmp) / "decision.json"
            source.write_text(json.dumps(packet), encoding="utf-8")
            first = BRIDGE.populate(source, output_root=Path(tmp) / "out")
            second = BRIDGE.populate(source, output_root=Path(tmp) / "out")
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(first["packet_sha256"], second["packet_sha256"])


class SymbolRuleTests(unittest.TestCase):
    def test_candidate_rules_cap_entry_and_preserve_exit_priority(self):
        contract = BRIDGE.load_contract()
        coverage = {
            "required_count": 5,
            "defined_count": 5,
            "all_defined": True,
            "missing_axes": [],
            "axes": {},
        }
        snapshot = {
            "candidates": [
                {
                    "market": "KRW-BTC",
                    "canonical_asset_id": "BTC",
                    "state": "PAPER_BUY_ELIGIBLE",
                    "reason": "SYNTHETIC_REACHABILITY_ONLY",
                },
                {
                    "market": "KRW-ETH",
                    "canonical_asset_id": "ETH",
                    "state": "BLOCKED",
                    "reason": "UPSTREAM_BLOCKER",
                },
            ]
        }
        rows = BRIDGE._build_symbol_rules(snapshot, coverage, contract)
        self.assertEqual(rows[0]["entry"]["state"], "WAIT")
        self.assertIn("AGGREGATE_POLICY_UNRATIFIED", rows[0]["entry"]["reasons"])
        self.assertIsNone(rows[0]["entry"]["order_draft"])
        self.assertFalse(rows[0]["entry"]["automatic_entry_generated"])
        self.assertEqual(rows[1]["entry"]["state"], "BLOCKED")
        for row in rows:
            self.assertEqual(
                row["exit"]["priority_categories"],
                BRIDGE.EXIT_MANAGER.load_contract()["priority_categories"],
            )
            self.assertEqual(row["exit"]["regime_signal"], "UNKNOWN")
            self.assertEqual(row["exit"]["trend_signal"], "UNKNOWN")
            self.assertFalse(row["exit"]["automatic_exit_generated"])

    def test_missing_axes_are_named_in_each_symbol_entry_reason(self):
        contract = BRIDGE.load_contract()
        coverage = {
            "required_count": 5,
            "defined_count": 3,
            "all_defined": False,
            "missing_axes": ["BREADTH", "LEADERSHIP"],
            "axes": {},
        }
        snapshot = {
            "candidates": [{
                "market": "KRW-BTC", "canonical_asset_id": "BTC",
                "state": "WATCH", "reason": "UPSTREAM_WAITING",
            }]
        }
        row = BRIDGE._build_symbol_rules(snapshot, coverage, contract)[0]
        self.assertIn("OFFICIAL_AXES_INCOMPLETE:BREADTH,LEADERSHIP", row["entry"]["reasons"])


if __name__ == "__main__":
    unittest.main()
