#!/usr/bin/env python3
"""Crypto PAPER decision-packet composition regression (P1-CR-08 + P5-08 +
P5-09 wired onto the tail of the P9-06 30-minute Upbit realtime capture).
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "decision" / "crypto_paper_decision_snapshot.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "upbit-realtime-capture.yml"
SPEC = importlib.util.spec_from_file_location("crypto_paper_decision_snapshot", MODULE_PATH)
CPDS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CPDS)

UNI = CPDS.UNIVERSE
MARKET_EV = CPDS.MARKET_EVIDENCE

EVAL_AS_OF = "2026-08-29"
GENERATED_AT = EVAL_AS_OF + "T09:00:00Z"
SOURCE_COMMIT = "a" * 40


# ---------------------------------------------------------------------------
# Fixture builders -- same shape/technique as test/test_crypto_candidate_promotion.py
# ---------------------------------------------------------------------------

def universe_row(*, market="KRW-BTC", state=None, canonical_asset_id="BTC", caution_any=False):
    state = state or UNI.STATE_PAPER_ELIGIBLE
    return {
        "market": market,
        "state": state,
        "reason": "PAPER_ELIGIBLE_ALL_GATES_PASSED",
        "candidate_canonical_asset_id": canonical_asset_id,
        "market_event_warning": False,
        "market_event_caution_any": caution_any,
        "observed_daily_candle_count": 120,
        "trailing_30d_krw_turnover": "10000000000",
        "kraken_cross_exchange_reference": False,
        "authority": dict(UNI._ROW_AUTHORITY),
    }


def universe_packet(rows, *, available_at=EVAL_AS_OF + "T00:40:00Z", evaluation_as_of=EVAL_AS_OF):
    policy = UNI.load_policy()
    taxonomy = UNI.load_taxonomy()
    packet = {
        "schema_version": UNI.OUTPUT_SCHEMA_VERSION,
        "snapshot_date": evaluation_as_of,
        "evaluation_as_of": evaluation_as_of,
        "available_at": available_at,
        "manifest_sha256": "a" * 64,
        "policy_version": policy["policy_version"],
        "policy_ratified": True,
        "taxonomy_version": taxonomy["policy_version"],
        "taxonomy_ratified": True,
        "duplicate_market_codes": {},
        "summary": {
            "market_count": len(rows),
            "observation_pool_count": sum(r["state"] == UNI.STATE_OBSERVATION_POOL for r in rows),
            "tradeable_universe_count": sum(r["state"] == UNI.STATE_TRADEABLE_UNIVERSE for r in rows),
            "paper_eligible_count": sum(r["state"] == UNI.STATE_PAPER_ELIGIBLE for r in rows),
            "blocked_count": sum(r["state"] == UNI.STATE_BLOCKED for r in rows),
        },
        "markets": rows,
        "authority": dict(UNI._ROW_AUTHORITY),
    }
    packet["payload_sha256"] = UNI.payload_sha256(packet)
    return packet


def write_universe_entry(tmp_dir: Path, packet: dict, *, date=EVAL_AS_OF):
    directory = tmp_dir / "universe" / date
    directory.mkdir(parents=True, exist_ok=True)
    record = {"schema_version": "upbit_universe_population/1", "snapshot_date": date, "packet": packet}
    record["payload_sha256"] = CPDS.payload_sha256(record)
    path = directory / "packet.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return {"date": date, "path": path, "record": record, "packet": packet}


def valid_market_evidence_packet(market="KRW-BTC"):
    as_of = dt.datetime(2026, 8, 29, 1, 0, 0, tzinfo=dt.timezone.utc)
    captured_at = dt.datetime(2026, 8, 29, 1, 5, 0, tzinfo=dt.timezone.utc)
    raw_candle = {
        "candle_date_time_utc": "2026-08-28T00:00:00",
        "opening_price": 1000, "high_price": 1010, "low_price": 990, "trade_price": 1005,
        "candle_acc_trade_price": 123456, "candle_acc_trade_volume": 12.3,
    }
    open_time_by_timeframe = {
        "15m": "2026-08-29T00:45:00",
        "1h": "2026-08-29T00:00:00",
        "4h": "2026-08-28T20:00:00",
        "1d": "2026-08-28T00:00:00",
    }
    candles = {}
    for timeframe, open_time in open_time_by_timeframe.items():
        row = copy.deepcopy(raw_candle)
        row["candle_date_time_utc"] = open_time
        candles[timeframe] = [row]
    timestamp_ms = int(as_of.timestamp() * 1000)
    trades = [{"market": market, "trade_price": 1000, "trade_volume": 1, "timestamp": timestamp_ms, "ask_bid": "BID"}]
    orderbook = {
        "market": market, "timestamp": timestamp_ms,
        "orderbook_units": [{"bid_price": 999, "bid_size": 10000, "ask_price": 1001, "ask_size": 10000}],
    }
    return MARKET_EV.build_market_evidence_packet(
        market, candles_by_timeframe=candles, trades=trades, orderbook_row=orderbook,
        as_of=as_of, captured_at=captured_at, policy=MARKET_EV.load_policy(),
    )


def write_market_evidence_entry(tmp_dir: Path, packets_by_market: dict, *, date=EVAL_AS_OF):
    directory = tmp_dir / "market_evidence" / date
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "upbit_microstructure_population/1", "snapshot_date": date,
        "generated_at": date + "T01:05:00Z",
        "policy_ratified": all(packet.get("policy_ratified") is True for packet in packets_by_market.values()),
        "summary": {
            "market_count": len(packets_by_market),
            "packet_count": len(packets_by_market),
            "error_count": 0,
        },
        "errors": {},
        "packets": packets_by_market,
    }
    record["payload_sha256"] = CPDS.payload_sha256(record)
    path = directory / "packet.json"
    path.write_text(json.dumps(record, default=str), encoding="utf-8")
    return {"date": date, "path": path, "record": json.loads(path.read_text())}


def write_realtime_entry(
    tmp_dir: Path, *, date=EVAL_AS_OF, run_index=1, overall_status="FRESH",
    with_observations=True,
):
    directory = tmp_dir / "realtime" / date
    directory.mkdir(parents=True, exist_ok=True)
    status = {
        "schema_version": "upbit_realtime_gate_status/1",
        "generated_at": date + "T09:00:00Z",
        "connection_state": "CONNECTED" if with_observations else "CONNECTING",
        "reconnect_count": 0,
        "last_disconnect_reason": None,
        "overall_status": overall_status,
        "counts": {"accepted": 1},
        "markets": [{"market": "KRW-BTC", "freshness_by_kind": {}}] if with_observations else [],
        "pending_connection_gap_windows": [],
        "duplicate_guard_size": 1,
        "authority": dict(CPDS.REALTIME_GATE._GATE_AUTHORITY),
    }
    status["payload_sha256"] = CPDS.payload_sha256(status)
    run = {
        "started_at": date + "T08:59:00Z",
        "ended_at": date + "T09:00:00Z",
        "requested_duration_seconds": 60,
        "markets": ["KRW-BTC"] if with_observations else [],
        "message_log": (
            [{"received_at": date + "T08:59:30Z", "result": {"action": "ACCEPTED"}}]
            if with_observations else []
        ),
        "status": status,
        "candle_ledger": {},
    }
    record = {
        "schema_version": "upbit_realtime_capture_run/1",
        "transform_version": "upbit_realtime_gate/1",
        "auth_required": False,
        "order_or_withdrawal_endpoints_called": False,
        "private_channel_subscribed": False,
        "run": run,
    }
    record["source_sha256"] = CPDS.payload_sha256(run)
    path = directory / f"run_{run_index:03d}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return {"date": date, "path": path, "record": record}


def ratified_policy_patches():
    """Simulate a future-ratified P3-12 policy/taxonomy, same technique
    test_crypto_candidate_promotion.py::BuildPromotionPacketTests uses.

    Each of P5-08 (``universe/crypto_candidate_promotion.py``) and P5-09
    (``universe/crypto_paper_buy_eligibility.py``) loads its own,
    independent ``universe/upbit_tradeable_universe.py`` module instance via
    ``importlib.util.spec_from_file_location`` (this repo's own
    established reuse pattern -- not something this test invents), so
    every instance actually consulted during a full P5-08->P5-09 run must
    be patched consistently, not just the one this test module itself
    loaded.
    """
    policy = copy.deepcopy(UNI.load_policy())
    taxonomy = copy.deepcopy(UNI.load_taxonomy())
    policy["approval_status"] = "RATIFIED"
    taxonomy["approval_status"] = "RATIFIED"
    targets = [CPDS.PROMOTION.UPBIT_UNIVERSE, CPDS.ELIGIBILITY.UPBIT_UNIVERSE]
    patchers = []
    for target in targets:
        patchers.append(mock.patch.object(target, "load_policy", return_value=policy))
        patchers.append(mock.patch.object(target, "load_taxonomy", return_value=taxonomy))
    market_policy = copy.deepcopy(CPDS.MARKET_EVIDENCE.load_policy())
    market_policy["approval_status"] = "RATIFIED"
    patchers.append(mock.patch.object(CPDS.MARKET_EVIDENCE, "load_policy", return_value=market_policy))
    patchers.append(mock.patch.object(CPDS.PROMOTION.MARKET_EVIDENCE, "load_policy", return_value=market_policy))
    patchers.append(mock.patch.object(
        CPDS.ELIGIBILITY.PROMOTION.MARKET_EVIDENCE, "load_policy", return_value=market_policy,
    ))
    patchers.append(mock.patch.object(
        CPDS.REALTIME_GATE, "load_freshness_policy_proposal",
        return_value={"approval_status": "RATIFIED"},
    ))
    return patchers


class TempDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cpds_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def start_ratified_patches(self):
        for patcher in ratified_policy_patches():
            patcher.start()
            self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# 1. Normal complete input
# ---------------------------------------------------------------------------

class NormalCompleteInputTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.start_ratified_patches()

    def test_full_evidence_produces_real_funnel_counts_and_reasons(self):
        packet = universe_packet([universe_row(market="KRW-BTC", state=UNI.STATE_PAPER_ELIGIBLE)])
        universe_entry = write_universe_entry(self.tmp, packet)
        market_evidence_entry = write_market_evidence_entry(
            self.tmp, {"KRW-BTC": valid_market_evidence_packet("KRW-BTC")}
        )
        realtime_entry = write_realtime_entry(self.tmp)

        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=universe_entry, market_evidence_entry=market_evidence_entry,
            realtime_entry=realtime_entry,
        )

        self.assertEqual(record["freshness_status"]["overall"], "FRESH")
        self.assertEqual(record["funnel_counts"]["tradeable_universe_count"], 1)
        self.assertEqual(record["funnel_counts"]["observation_pool_count"], 0)
        self.assertEqual(len(record["candidates"]), 1)
        row = record["candidates"][0]
        self.assertEqual(row["market"], "KRW-BTC")
        # REGIME is UNKNOWN by construction today -> caps every real
        # derivation at WATCH; this is the correct, honest current output.
        self.assertEqual(row["state"], "WATCH")
        self.assertEqual(row["p5_08"]["criteria"]["REGIME"]["status"], "UNKNOWN")
        self.assertIn("REGIME", row["p5_08"]["promotion_reason"])
        self.assertFalse(row["freshness_capped"])
        self.assertEqual(record["funnel_counts"]["paper_ready_count"], 0)
        self.assertEqual(len(record["source_refs"]), 3)
        for ref in record["source_refs"]:
            self.assertRegex(ref["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            record["upbit_universe_snapshot_identity"],
            {"date": EVAL_AS_OF, "payload_sha256": packet["payload_sha256"]},
        )
        self.assertIn("KRW-BTC", record["finalized_candle_attestation"]["markets"])
        self.assertTrue(record["finalized_candle_attestation"]["used_in_promotion"])
        self.assertEqual(CPDS.validate_output(record, allow_external_sources=True), record)

    def test_rehashed_candidate_state_tamper_is_rejected_by_full_rederivation(self):
        packet = universe_packet([universe_row(market="KRW-BTC", state=UNI.STATE_PAPER_ELIGIBLE)])
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT,
            source_commit=SOURCE_COMMIT,
            universe_entry=write_universe_entry(self.tmp, packet),
            market_evidence_entry=write_market_evidence_entry(
                self.tmp, {"KRW-BTC": valid_market_evidence_packet("KRW-BTC")},
            ),
            realtime_entry=write_realtime_entry(self.tmp),
        )
        forged = copy.deepcopy(record)
        forged["candidates"][0]["state"] = "PAPER_BUY_ELIGIBLE"
        forged["funnel_counts"]["paper_ready_count"] = 1
        forged["payload_sha256"] = CPDS.payload_sha256(
            {key: value for key, value in forged.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(CPDS.CryptoPaperDecisionSnapshotError, "OUTPUT_DERIVATION_MISMATCH"):
            CPDS.validate_output(forged, allow_external_sources=True)


# ---------------------------------------------------------------------------
# 2. P3-12 packet missing
# ---------------------------------------------------------------------------

class UniverseMissingTests(TempDirMixin, unittest.TestCase):
    def test_missing_universe_yields_missing_freshness_and_no_crash(self):
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=None, realtime_entry=None,
        )
        self.assertEqual(record["freshness_status"]["upbit_universe"], "MISSING")
        self.assertEqual(record["freshness_status"]["overall"], "MISSING")
        self.assertEqual(record["candidates"], [])
        self.assertEqual(record["funnel_counts"], {
            "observation_pool_count": 0, "tradeable_universe_count": 0,
            "focused_review_count": 0, "paper_ready_count": 0,
        })
        self.assertIn("UPBIT_UNIVERSE_PACKET_MISSING", record["derivation_notes"])
        self.assertIsNone(record["upbit_universe_snapshot_identity"]["payload_sha256"])
        # Packet is still generated -- never a crash.
        self.assertEqual(record["schema_version"], CPDS.OUTPUT_SCHEMA_VERSION)


class EmptyAndUnratifiedEvidenceTests(TempDirMixin, unittest.TestCase):
    def test_empty_market_evidence_is_missing_never_fresh(self):
        entry = write_market_evidence_entry(self.tmp, {})
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=entry, realtime_entry=None,
        )
        self.assertEqual(record["freshness_status"]["market_evidence"], CPDS.MISSING)
        self.assertIn("UPBIT_MARKET_EVIDENCE_PACKETS_EMPTY", record["derivation_notes"])

    def test_empty_realtime_capture_is_missing_never_fresh(self):
        entry = write_realtime_entry(self.tmp, with_observations=False, overall_status="UNKNOWN")
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=None, realtime_entry=entry,
        )
        self.assertEqual(record["freshness_status"]["realtime"], CPDS.MISSING)
        self.assertIn("UPBIT_REALTIME_OBSERVATIONS_EMPTY", record["derivation_notes"])

    def test_unratified_market_and_realtime_freshness_are_unknown(self):
        market_entry = write_market_evidence_entry(
            self.tmp, {"KRW-BTC": valid_market_evidence_packet("KRW-BTC")},
        )
        realtime_entry = write_realtime_entry(self.tmp)
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=market_entry,
            realtime_entry=realtime_entry,
        )
        self.assertEqual(record["freshness_status"]["market_evidence"], CPDS.UNKNOWN)
        self.assertEqual(record["freshness_status"]["realtime"], CPDS.UNKNOWN)


class SourceIntegrityTests(TempDirMixin, unittest.TestCase):
    def test_rehashed_outer_market_record_tamper_is_rejected(self):
        entry = write_market_evidence_entry(self.tmp, {})
        entry["record"]["summary"]["packet_count"] = 1
        entry["record"]["payload_sha256"] = CPDS.payload_sha256(
            {key: value for key, value in entry["record"].items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(CPDS.CryptoPaperDecisionSnapshotError, "SUMMARY_INVALID"):
            CPDS.build_snapshot(
                generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
                universe_entry=None, market_evidence_entry=entry, realtime_entry=None,
            )

    def test_realtime_run_tamper_with_stale_source_hash_is_rejected(self):
        entry = write_realtime_entry(self.tmp)
        entry["record"]["run"]["message_log"].append({"result": {"action": "FORGED"}})
        with self.assertRaisesRegex(CPDS.CryptoPaperDecisionSnapshotError, "REALTIME_SOURCE_HASH_MISMATCH"):
            CPDS.build_snapshot(
                generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
                universe_entry=None, market_evidence_entry=None, realtime_entry=entry,
            )

    def test_generation_id_binds_exact_source_file_bytes(self):
        entry = write_market_evidence_entry(self.tmp, {})
        first = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=entry, realtime_entry=None,
        )
        entry["path"].write_text(entry["path"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
        second = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=entry, realtime_entry=None,
        )
        self.assertNotEqual(first["generation_id"], second["generation_id"])

    def test_future_realtime_evidence_is_rejected(self):
        entry = write_realtime_entry(self.tmp)
        entry["record"]["run"]["ended_at"] = "2026-08-29T09:00:01Z"
        entry["record"]["source_sha256"] = CPDS.payload_sha256(entry["record"]["run"])
        with self.assertRaisesRegex(CPDS.CryptoPaperDecisionSnapshotError, "FUTURE_DATED"):
            CPDS.build_snapshot(
                generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
                universe_entry=None, market_evidence_entry=None, realtime_entry=entry,
            )


# ---------------------------------------------------------------------------
# 3. P3-12 packet stale
# ---------------------------------------------------------------------------

class UniverseStaleTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.start_ratified_patches()

    def test_stale_universe_beyond_reused_max_capture_age_hours(self):
        policy = UNI.load_policy()
        max_age = int(policy["max_capture_age_hours"])
        stale_available_at = (
            dt.datetime.strptime(GENERATED_AT, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            - dt.timedelta(hours=max_age + 1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        packet = universe_packet(
            [universe_row(state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None)],
            available_at=stale_available_at, evaluation_as_of=EVAL_AS_OF,
        )
        universe_entry = write_universe_entry(self.tmp, packet)
        # market_evidence/realtime are FRESH (present, matching date) so
        # the assertion isolates the universe's own STALE status as the
        # single worst input -- MISSING would otherwise dominate worst-of.
        market_evidence_entry = write_market_evidence_entry(
            self.tmp, {"KRW-BTC": valid_market_evidence_packet("KRW-BTC")},
        )
        realtime_entry = write_realtime_entry(self.tmp)
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=universe_entry, market_evidence_entry=market_evidence_entry,
            realtime_entry=realtime_entry,
        )
        self.assertEqual(record["freshness_status"]["upbit_universe"], "STALE")
        self.assertEqual(record["freshness_status"]["overall"], "STALE")
        self.assertTrue(any("STALE" in note for note in record["derivation_notes"]))


# ---------------------------------------------------------------------------
# 4. Mixed generation
# ---------------------------------------------------------------------------

class MixedGenerationTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.start_ratified_patches()

    def test_market_evidence_date_mismatch_is_mixed_generation_and_excluded(self):
        packet = universe_packet([universe_row(state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None)])
        universe_entry = write_universe_entry(self.tmp, packet, date=EVAL_AS_OF)
        market_evidence_entry = write_market_evidence_entry(
            self.tmp, {"KRW-BTC": valid_market_evidence_packet("KRW-BTC")}, date="2026-08-27",
        )
        realtime_entry = write_realtime_entry(self.tmp)
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=universe_entry, market_evidence_entry=market_evidence_entry,
            realtime_entry=realtime_entry,
        )
        self.assertEqual(record["freshness_status"]["market_evidence"], "MIXED_GENERATION")
        self.assertEqual(record["freshness_status"]["overall"], "MIXED_GENERATION")
        self.assertFalse(record["finalized_candle_attestation"]["used_in_promotion"])

    def test_realtime_date_mismatch_is_mixed_generation(self):
        packet = universe_packet([universe_row(state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None)])
        universe_entry = write_universe_entry(self.tmp, packet, date=EVAL_AS_OF)
        realtime_entry = write_realtime_entry(self.tmp, date="2026-08-27")
        market_evidence_entry = write_market_evidence_entry(
            self.tmp, {"KRW-BTC": valid_market_evidence_packet("KRW-BTC")},
        )
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=universe_entry, market_evidence_entry=market_evidence_entry,
            realtime_entry=realtime_entry,
        )
        self.assertEqual(record["freshness_status"]["realtime"], "MIXED_GENERATION")
        self.assertEqual(record["freshness_status"]["overall"], "MIXED_GENERATION")

    def test_mixed_generation_caps_would_be_actionable_state_to_wait(self):
        capped = CPDS.cap_state_for_freshness("FOCUSED_REVIEW", "ALL_CRITERIA_PASSED", CPDS.MIXED_GENERATION)
        self.assertEqual(capped["state"], "WAIT")
        self.assertTrue(capped["capped"])


# ---------------------------------------------------------------------------
# 5. Regime UNKNOWN hard invariant
# ---------------------------------------------------------------------------

class RegimeUnknownInvariantTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.start_ratified_patches()

    def test_no_candidate_ever_reaches_paper_buy_eligible_today(self):
        rows = [universe_row(market=f"KRW-{i}", state=UNI.STATE_PAPER_ELIGIBLE, canonical_asset_id=f"C{i}") for i in range(3)]
        packet = universe_packet(rows)
        universe_entry = write_universe_entry(self.tmp, packet)
        market_evidence_entry = write_market_evidence_entry(
            self.tmp, {row["market"]: valid_market_evidence_packet(row["market"]) for row in rows}
        )
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=universe_entry, market_evidence_entry=market_evidence_entry, realtime_entry=None,
        )
        self.assertEqual(record["funnel_counts"]["paper_ready_count"], 0)
        for row in record["candidates"]:
            self.assertNotEqual(row["state"], "PAPER_BUY_ELIGIBLE")
            self.assertEqual(row["p5_08"]["criteria"]["REGIME"]["status"], "UNKNOWN")

    def test_cap_state_for_freshness_never_reports_actionable_when_not_fresh(self):
        """Hard invariant, independent of the real Regime gate: no input
        combination can make the composition layer itself report an
        actionable state while freshness is degraded.
        """
        for state in ("WATCH", "FOCUSED_REVIEW", "BLOCKED", "WAIT", "PAPER_BUY_ELIGIBLE"):
            for freshness in (CPDS.STALE, CPDS.MISSING, CPDS.MIXED_GENERATION):
                capped = CPDS.cap_state_for_freshness(state, "reason", freshness)
                self.assertNotIn(capped["state"], CPDS._ACTIONABLE_STATES)
        for state in ("WATCH", "FOCUSED_REVIEW", "BLOCKED", "WAIT", "PAPER_BUY_ELIGIBLE"):
            capped = CPDS.cap_state_for_freshness(state, "reason", CPDS.FRESH)
            self.assertEqual(capped["state"], state)


# ---------------------------------------------------------------------------
# 6/7. duplicate_guard_key stability / differs on real input change
# ---------------------------------------------------------------------------

class DuplicateGuardKeyTests(TempDirMixin, unittest.TestCase):
    def test_same_slot_same_inputs_same_key_and_byte_identical_packet(self):
        record1 = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=None, realtime_entry=None,
        )
        record2 = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=None, realtime_entry=None,
        )
        self.assertEqual(record1["duplicate_guard_key"], record2["duplicate_guard_key"])
        self.assertEqual(record1["generation_id"], record2["generation_id"])
        self.assertEqual(json.dumps(record1, sort_keys=True), json.dumps(record2, sort_keys=True))

    def test_key_differs_when_source_commit_differs(self):
        record1 = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit="a" * 40,
            universe_entry=None, market_evidence_entry=None, realtime_entry=None,
        )
        record2 = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit="b" * 40,
            universe_entry=None, market_evidence_entry=None, realtime_entry=None,
        )
        self.assertNotEqual(record1["duplicate_guard_key"], record2["duplicate_guard_key"])
        self.assertNotEqual(record1["generation_id"], record2["generation_id"])

    def test_key_differs_when_universe_snapshot_differs(self):
        packet_a = universe_packet([universe_row(state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None)])
        packet_b = universe_packet([
            universe_row(state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None),
            universe_row(market="KRW-ETH", state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None),
        ])
        entry_a = write_universe_entry(self.tmp, packet_a)
        entry_b = write_universe_entry(self.tmp, packet_b)
        record_a = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=entry_a, market_evidence_entry=None, realtime_entry=None,
        )
        record_b = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=entry_b, market_evidence_entry=None, realtime_entry=None,
        )
        self.assertNotEqual(record_a["generation_id"], record_b["generation_id"])
        self.assertNotEqual(record_a["duplicate_guard_key"], record_b["duplicate_guard_key"])

    def test_key_matches_p9_04_token_shape(self):
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=None, realtime_entry=None,
        )
        self.assertRegex(record["duplicate_guard_key"], r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


# ---------------------------------------------------------------------------
# populate() idempotency -- mirrors upbit_universe_populate.py::populate
# ---------------------------------------------------------------------------

class PopulateIdempotencyTests(TempDirMixin, unittest.TestCase):
    def test_rerun_same_slot_verifies_existing_not_duplicate(self):
        output_root = self.tmp / "out"
        first = CPDS.populate(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_data_root=self.tmp / "no_universe",
            market_evidence_data_root=self.tmp / "no_market_evidence",
            realtime_evidence_root=self.tmp / "no_realtime",
            output_root=output_root,
        )
        self.assertEqual(first["outcome"], "populated")
        second = CPDS.populate(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_data_root=self.tmp / "no_universe",
            market_evidence_data_root=self.tmp / "no_market_evidence",
            realtime_evidence_root=self.tmp / "no_realtime",
            output_root=output_root,
        )
        self.assertEqual(second["outcome"], "verified_existing")
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])
        written = list(output_root.rglob("packet.json"))
        self.assertEqual(len(written), 1)

    def test_prior_realtime_run_is_not_reused_without_explicit_opt_in(self):
        write_realtime_entry(self.tmp)
        result = CPDS.populate(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_data_root=self.tmp / "no_universe",
            market_evidence_data_root=self.tmp / "no_market_evidence",
            realtime_evidence_root=self.tmp / "realtime",
            output_root=self.tmp / "out-no-fallback",
        )
        self.assertEqual(result["record"]["freshness_status"]["realtime"], CPDS.MISSING)
        self.assertFalse(any(ref["role"] == "upbit_realtime_capture_run" for ref in result["record"]["source_refs"]))


# ---------------------------------------------------------------------------
# 8. Authority fields all false
# ---------------------------------------------------------------------------

class AuthorityAllFalseTests(TempDirMixin, unittest.TestCase):
    def test_top_level_and_every_candidate_authority_all_false(self):
        self.start_ratified_patches()
        packet = universe_packet([universe_row(state=UNI.STATE_PAPER_ELIGIBLE)])
        universe_entry = write_universe_entry(self.tmp, packet)
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=universe_entry, market_evidence_entry=None, realtime_entry=None,
        )
        self.assertTrue(record["candidates"], "expected at least one candidate row")
        for value in record["authority"].values():
            self.assertIs(value, False)
        for row in record["candidates"]:
            for value in row["authority"].values():
                self.assertIs(value, False)


# ---------------------------------------------------------------------------
# 9. Determinism
# ---------------------------------------------------------------------------

class DeterminismTests(TempDirMixin, unittest.TestCase):
    def test_same_inputs_twice_byte_identical(self):
        packet = universe_packet([universe_row(state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None)])
        universe_entry = write_universe_entry(self.tmp, packet)
        market_evidence_entry = write_market_evidence_entry(self.tmp, {})
        realtime_entry = write_realtime_entry(self.tmp)
        kwargs = dict(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=universe_entry, market_evidence_entry=market_evidence_entry,
            realtime_entry=realtime_entry,
        )
        record1 = CPDS.build_snapshot(**kwargs)
        record2 = CPDS.build_snapshot(**kwargs)
        self.assertEqual(canonical := json.dumps(record1, sort_keys=True), json.dumps(record2, sort_keys=True))
        self.assertEqual(record1["payload_sha256"], record2["payload_sha256"])


# ---------------------------------------------------------------------------
# 10. Zero network/order calls -- source-grep, matching this session's
#     established pattern (e.g. test_upbit_market_capture.py's endpoint-path
#     assertions).
# ---------------------------------------------------------------------------

class ZeroNetworkOrderCallsTests(unittest.TestCase):
    def test_source_never_imports_network_or_order_libraries(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        forbidden_imports = ("requests", "urllib.request", "http.client", "websockets", "socket")
        for name in forbidden_imports:
            self.assertNotIn(f"import {name}", source, f"unexpected network import: {name}")
        forbidden_endpoints = ("api.upbit.com", "/v1/orders", "myOrder", "myAsset")
        for token in forbidden_endpoints:
            self.assertNotIn(token, source, f"unexpected order/private endpoint reference: {token}")

    def test_source_never_calls_subprocess_except_git_rev_parse(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        calls = re.findall(r"subprocess\.run\(\s*\[([^\]]*)\]", source)
        self.assertTrue(calls, "expected at least the git rev-parse HEAD subprocess call")
        for call in calls:
            self.assertIn('"git"', call)
            self.assertIn("rev-parse", call)

    def test_workflow_uses_post_capture_clock_and_no_implicit_realtime_fallback(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        snapshot_step = workflow.split("- name: Crypto PAPER decision snapshot", 1)[1].split(
            "- name: Record P9-06 realtime scheduler telemetry", 1,
        )[0]
        self.assertIn("GENERATED_AT=\"$(date -u", snapshot_step)
        self.assertNotIn("steps.runner_start.outputs.observed_started_at_utc", snapshot_step)
        self.assertNotIn("--allow-realtime-fallback", snapshot_step)


# ---------------------------------------------------------------------------
# 11. previous_state_reference
# ---------------------------------------------------------------------------

class PreviousStateReferenceTests(TempDirMixin, unittest.TestCase):
    def test_first_packet_ever_has_no_previous_reference(self):
        output_root = self.tmp / "out"
        result = CPDS.populate(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_data_root=self.tmp / "no_universe",
            market_evidence_data_root=self.tmp / "no_market_evidence",
            realtime_evidence_root=self.tmp / "no_realtime",
            output_root=output_root,
        )
        self.assertIsNone(result["record"]["previous_state_reference"])

    def test_second_slot_points_at_first_without_fabrication(self):
        output_root = self.tmp / "out"
        no_universe = self.tmp / "no_universe"
        no_market_evidence = self.tmp / "no_market_evidence"
        no_realtime = self.tmp / "no_realtime"
        first = CPDS.populate(
            generated_at="2026-08-29T09:00:00Z", source_commit=SOURCE_COMMIT,
            universe_data_root=no_universe, market_evidence_data_root=no_market_evidence,
            realtime_evidence_root=no_realtime, output_root=output_root,
        )
        second = CPDS.populate(
            generated_at="2026-08-29T09:30:00Z", source_commit="b" * 40,
            universe_data_root=no_universe, market_evidence_data_root=no_market_evidence,
            realtime_evidence_root=no_realtime, output_root=output_root,
        )
        previous_ref = second["record"]["previous_state_reference"]
        self.assertIsNotNone(previous_ref)
        self.assertEqual(previous_ref["generation_id"], first["record"]["generation_id"])
        self.assertEqual(previous_ref["payload_sha256"], first["record"]["payload_sha256"])
        self.assertEqual(previous_ref["funnel_counts"], first["record"]["funnel_counts"])


# ---------------------------------------------------------------------------
# Contract-shape sanity
# ---------------------------------------------------------------------------

class PacketShapeTests(TempDirMixin, unittest.TestCase):
    def test_required_top_level_fields_present(self):
        record = CPDS.build_snapshot(
            generated_at=GENERATED_AT, source_commit=SOURCE_COMMIT,
            universe_entry=None, market_evidence_entry=None, realtime_entry=None,
        )
        required = {
            "schema_version", "generated_at", "capture_date", "capture_hhmm", "source_commit",
            "generation_id", "duplicate_guard_key", "source_refs", "upbit_universe_snapshot_identity",
            "finalized_candle_attestation", "crypto_regime_five_axis", "source_components",
            "funnel_counts", "candidates",
            "freshness_status", "authority", "previous_state_reference", "derivation_notes", "payload_sha256",
        }
        self.assertTrue(required.issubset(record))
        self.assertEqual(set(record["crypto_regime_five_axis"]), {"TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"})
        for axis_result in record["crypto_regime_five_axis"].values():
            self.assertIn(axis_result["status"], ("DEFINED", "UNDEFINED"))


if __name__ == "__main__":
    unittest.main()
