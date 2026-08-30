"""P3-12 -> P4-07 exact-hash consumer and fail-close regressions.

P3-12-GOV-02B note: the bridge's real, committed
``config/upbit_p3_p4_bridge_contract.json`` is SUSPENDED (its original
anchor, PR #465's ratified P3 universe record, was CIO-revoked -- see
``config/upbit_governance_revocations.json``). Tests that exercise the
bridge's own validation mechanics (duplicate-market rejection, historical
backfill forbidden, capture/evidence fail-closed behavior, population
idempotency) do so against a LOCAL, SYNTHETIC ``ACTIVE`` contract + anchor
record built by ``_active_repo()`` below, so they keep proving the
mechanism works correctly in principle without depending on -- or
resurrecting -- the real revoked anchor. ``SuspendedBridgeTests`` covers the
real, currently-committed (suspended) contract directly.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ANCHOR = ROOT / "data" / "observations" / "upbit_tradeable_universe" / "2026-08-30" / "packet.json"
REAL_CONTRACT_PATH = ROOT / "config" / "upbit_p3_p4_bridge_contract.json"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BRIDGE = _load("upbit_p3_p4_bridge_test", "microstructure/upbit_p3_p4_bridge.py")
CAP = _load("upbit_microstructure_capture_exact_test", ".github/scripts/upbit_microstructure_capture.py")
EV = _load("upbit_market_evidence_exact_test", "microstructure/upbit_market_evidence.py")
POP = _load("upbit_microstructure_populate_exact_test", ".github/scripts/upbit_microstructure_populate.py")


def rehash_packet_and_record(record: dict) -> str:
    packet = record["packet"]
    packet["payload_sha256"] = BRIDGE.payload_sha256(
        {key: value for key, value in packet.items() if key != "payload_sha256"}
    )
    record["payload_sha256"] = BRIDGE.payload_sha256(
        {key: value for key, value in record.items() if key != "payload_sha256"}
    )
    return record["payload_sha256"]


def _synthetic_anchor_record() -> dict:
    """A fresh, self-consistent, ACTIVE-cohort universe record -- same shape
    and same real, byte-identical raw-manifest binding as the real
    (now-revoked) anchor, but staged only inside an isolated tmpdir by
    ``_active_repo()``, never written back to the real repo path -- it
    never revives, or is confused with, the revoked one. The real,
    currently-committed anchor is (correctly, post-revert)
    policy_ratified=False/taxonomy_ratified=False -- this fixture
    hypothesizes a validly-ratified upstream so these tests can keep
    proving the BRIDGE's own mechanics work, independent of whether P3-12
    is currently ratified for real. snapshot_date is intentionally left
    unchanged (matching the real, byte-copied raw manifest's vintage_date)
    -- only the ratification-status fields the revert reset are re-set.
    """
    record = json.loads(ANCHOR.read_text(encoding="utf-8"))
    record["packet"]["policy_ratified"] = True
    record["packet"]["taxonomy_ratified"] = True
    record["ratification"] = {"effective_for_snapshot": True}
    # The real, currently-committed anchor is (correctly, post-revert) all
    # OBSERVATION_POOL/IDENTITY_UNRATIFIED -- hand-promote a small, fixed
    # cohort to PAPER_ELIGIBLE with an identity assigned, purely so these
    # mechanics tests have a nonempty eligible cohort to exercise.
    promoted = {"KRW-BTC": "BTC", "KRW-ETH": "ETH", "KRW-LINK": "LINK", "KRW-SHIB": "SHIB",
                "KRW-SOL": "SOL", "KRW-SUI": "SUI", "KRW-WLD": "WLD", "KRW-XRP": "XRP"}
    for row in record["packet"]["markets"]:
        canonical_id = promoted.get(row["market"])
        if canonical_id is not None:
            row["state"] = "PAPER_ELIGIBLE"
            row["candidate_canonical_asset_id"] = canonical_id
            row["reason"] = None
    rehash_packet_and_record(record)
    return record


def _active_repo(tmp: str, record: dict | None = None) -> tuple[Path, Path, dict]:
    """Stage a temp repo carrying an ACTIVE (not suspended) bridge contract
    anchored exactly to ``record``, plus everything else the real contract
    references (P4 policy, raw manifest) copied verbatim. Returns
    (repo_root, record_path, contract_dict_written).
    """
    record = record if record is not None else _synthetic_anchor_record()
    root = Path(tmp)
    for relative in (
        "config/upbit_market_evidence_policy_ratified.json",
        "evidence/crypto/upbit/raw/2026-08-30/_manifest.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    record_path = root / "data/observations/upbit_tradeable_universe" / record["snapshot_date"] / "packet.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")

    real_contract = json.loads(REAL_CONTRACT_PATH.read_text(encoding="utf-8"))
    packet = record["packet"]
    cohort = sorted(
        row["market"] for row in packet["markets"]
        if row.get("state") in set(real_contract["eligible_states"])
    )
    identity_unratified_count = sum(
        row.get("reason") == real_contract["identity_unratified_reason"] for row in packet["markets"]
    )
    contract = dict(real_contract)
    contract["approval_status"] = "ACTIVE"
    contract.pop("suspended_reason", None)
    contract.pop("suspended_at_utc", None)
    contract["active_post_ratification_anchor"] = {
        "path": str(record_path.relative_to(root)),
        "record_payload_sha256": record["payload_sha256"],
        "paper_markets": cohort,
        "identity_unratified_count": identity_unratified_count,
    }
    contract_path = root / "config/upbit_p3_p4_bridge_contract.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    return root, record_path, contract


class SuspendedBridgeTests(unittest.TestCase):
    """The real, currently-committed bridge contract and anchor -- P3-12-GOV-02B."""

    def test_real_committed_contract_is_suspended(self):
        contract = json.loads(REAL_CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["approval_status"], "SUSPENDED_INVALID_UPSTREAM")
        self.assertIsNone(contract["active_post_ratification_anchor"])
        self.assertTrue(contract["revoked_initial_anchor"]["revoked"])

    def test_load_bridge_contract_fails_closed_suspended(self):
        with self.assertRaisesRegex(BRIDGE.BridgeError, "P3_BRIDGE_SUSPENDED"):
            BRIDGE.load_bridge_contract()

    def test_consume_universe_record_fails_closed_before_any_provider_call(self):
        # Real, currently-committed (post-revert) anchor path -- must never
        # reach policy loading, market-cohort derivation, or a provider call.
        with self.assertRaisesRegex(BRIDGE.BridgeError, "P3_BRIDGE_SUSPENDED"):
            BRIDGE.consume_universe_record(ANCHOR)

    def test_unrecognized_approval_status_also_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = json.loads(REAL_CONTRACT_PATH.read_text(encoding="utf-8"))
            contract["approval_status"] = "SOMETHING_ELSE_UNRECOGNIZED"
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.BridgeError, "P3_BRIDGE_SUSPENDED"):
                BRIDGE.load_bridge_contract(path)

    def test_active_status_with_null_anchor_still_fails_closed(self):
        # Defensive: ACTIVE alone is not enough -- a null anchor must still
        # block, in case the two fields are ever edited independently.
        with tempfile.TemporaryDirectory() as tmp:
            contract = json.loads(REAL_CONTRACT_PATH.read_text(encoding="utf-8"))
            contract["approval_status"] = "ACTIVE"
            contract["active_post_ratification_anchor"] = None
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.BridgeError, "P3_ANCHOR_REVOKED"):
                BRIDGE.load_bridge_contract(path)

    def test_zero_provider_calls_from_the_real_capture_cli_path(self):
        """End-to-end: the actual CLI entry point
        (.github/scripts/upbit_microstructure_capture.py) must fail closed
        at load_universe_lineage() -- before capture_snapshot() ever calls
        the fetcher -- when pointed at the real, suspended contract/anchor.
        """
        calls = []

        def _forbidden_fetcher(url, timeout):
            calls.append(url)
            raise AssertionError("provider must not be called while the bridge is suspended")

        with self.assertRaisesRegex(CAP.CaptureError, "UNIVERSE_CONSUMER_REJECTED"):
            lineage = CAP.load_universe_lineage(ANCHOR)
            CAP.capture_snapshot(
                Path(tempfile.mkdtemp()), markets=lineage.get("markets", []),
                snapshot_date=dt.date(2026, 8, 30), fetcher=_forbidden_fetcher,
                sleeper=lambda _: None,
            )
        self.assertEqual(calls, [])


class ExactHashBridgeTests(unittest.TestCase):
    """Bridge validation mechanics, proven against a local ACTIVE contract +
    synthetic anchor -- never the real, revoked one."""

    def test_authoritative_anchor_exact_hash_and_eight_market_cohort_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = _synthetic_anchor_record()
            root, path, contract = _active_repo(tmp, record)
            lineage = BRIDGE.consume_universe_record(
                path, expected_record_sha256=record["payload_sha256"],
                contract_path=root / "config/upbit_p3_p4_bridge_contract.json", repo_root=root,
            )
            self.assertEqual(lineage["markets"], contract["active_post_ratification_anchor"]["paper_markets"])
            self.assertFalse(lineage["historical_identity_backfill_applied"])
            self.assertEqual(
                lineage["p4_policy"]["packet_sha256"],
                "26d921e4b98f91010b4397d6642c1dc6021d06ef134977cc80a94692e6e1df5e",
            )

    def test_exact_record_hash_mismatch_fails_before_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, path, contract = _active_repo(tmp)
            with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_RECORD_EXACT_HASH_MISMATCH"):
                BRIDGE.consume_universe_record(
                    path, expected_record_sha256="0" * 64,
                    contract_path=root / "config/upbit_p3_p4_bridge_contract.json", repo_root=root,
                )

    def test_missing_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _path, _contract = _active_repo(tmp)
            with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_RECORD_UNREADABLE"):
                BRIDGE.consume_universe_record(
                    Path("/definitely/missing/p3.json"),
                    contract_path=root / "config/upbit_p3_p4_bridge_contract.json", repo_root=root,
                )

    def test_duplicate_market_is_rejected_even_when_forgery_is_rehashed(self):
        record = _synthetic_anchor_record()
        record["packet"]["markets"].append(copy.deepcopy(record["packet"]["markets"][0]))
        record["packet"]["summary"]["market_count"] += 1
        forged_hash = rehash_packet_and_record(record)
        with tempfile.TemporaryDirectory() as tmp:
            root, path, _contract = _active_repo(tmp, record)
            contract_path = root / "config/upbit_p3_p4_bridge_contract.json"
            with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_DUPLICATE_MARKET"):
                BRIDGE.consume_universe_record(
                    path, expected_record_sha256=forged_hash,
                    contract_path=contract_path, repo_root=root,
                )

    def test_historical_identity_backfill_is_forbidden(self):
        record = _synthetic_anchor_record()
        record["snapshot_date"] = "2020-01-01"
        record["packet"]["snapshot_date"] = "2020-01-01"
        forged_hash = rehash_packet_and_record(record)
        with tempfile.TemporaryDirectory() as tmp:
            root, path, _contract = _active_repo(tmp, record)
            contract_path = root / "config/upbit_p3_p4_bridge_contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            # Point the active anchor at a different path so this record is
            # evaluated as a NON-anchor record (still must respect the
            # bridge's effective_from_utc floor).
            contract["active_post_ratification_anchor"]["path"] = "not-this-record.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.BridgeError, "HISTORICAL_IDENTITY_BACKFILL_FORBIDDEN"):
                BRIDGE.consume_universe_record(
                    path, expected_record_sha256=forged_hash,
                    contract_path=contract_path, repo_root=root,
                )

    def test_ratified_policy_exact_self_hash_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _path, contract = _active_repo(tmp)
            policy = BRIDGE.load_ratified_p4_policy(
                contract, policy_path=root / "config/upbit_market_evidence_policy_ratified.json",
            )
            self.assertEqual(policy["approval_status"], "RATIFIED")
            self.assertEqual(
                BRIDGE.payload_sha256({k: v for k, v in policy.items() if k != "packet_sha256"}),
                policy["packet_sha256"],
            )


class CaptureAndEvidenceFailCloseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        record = _synthetic_anchor_record()
        root, path, _contract = _active_repo(self._tmp.name, record)
        self.lineage = BRIDGE.consume_universe_record(
            path, contract_path=root / "config/upbit_p3_p4_bridge_contract.json", repo_root=root,
        )

    def test_partial_universe_is_rejected_before_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CAP.CaptureError, "PARTIAL_UNIVERSE_REJECTED"):
                CAP.capture_snapshot(
                    Path(tmp), markets=self.lineage["markets"][:-1],
                    snapshot_date=dt.date(2026, 8, 30),
                    snapshot_key=BRIDGE.snapshot_key(self.lineage),
                    universe_lineage=self.lineage,
                    fetcher=lambda url, timeout: self.fail("provider must not be called"),
                    sleeper=lambda _: None,
                    clock=lambda: dt.datetime(2026, 8, 30, 1, 20, tzinfo=dt.timezone.utc),
                )

    def test_exact_hash_snapshot_key_replay_is_append_only_rejected(self):
        lineage = copy.deepcopy(self.lineage)
        lineage["markets"] = []
        lineage["market_count"] = 0
        key = BRIDGE.snapshot_key(lineage)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kwargs = dict(
                markets=[], snapshot_date=dt.date(2026, 8, 30), snapshot_key=key,
                universe_lineage=lineage, fetcher=lambda url, timeout: b"[]",
                sleeper=lambda _: None,
                clock=lambda: dt.datetime(2026, 8, 30, 1, 20, tzinfo=dt.timezone.utc),
            )
            CAP.capture_snapshot(root, **kwargs)
            with self.assertRaisesRegex(CAP.CaptureError, "APPEND_ONLY_VIOLATION"):
                CAP.capture_snapshot(root, **kwargs)

    def test_stale_duplicate_and_malformed_evidence_remain_unknown(self):
        policy = {
            "approval_status": "RATIFIED",
            "policy_id": "fixture",
            "policy_version": "fixture/v1",
            "packet_sha256": "f" * 64,
            "orderbook_depth_levels": 1,
            "paper_slippage_estimate_notional_krw": "1000",
            "max_spread_bps_normal": "1000",
            "max_slippage_bps_normal": "1000",
            "max_staleness_seconds_by_timeframe": {"15m": 1, "1h": 1, "4h": 1, "1d": 1},
            "max_trades_staleness_seconds": 1,
            "max_orderbook_staleness_seconds": 1,
        }
        candle = {
            "candle_date_time_utc": "2026-08-28T00:00:00",
            "opening_price": 1, "high_price": 1, "low_price": 1, "trade_price": 1,
            "candle_acc_trade_price": 1, "candle_acc_trade_volume": 1,
        }
        as_of = dt.datetime(2026, 8, 30, 1, 20, tzinfo=dt.timezone.utc)
        trade = {"trade_price": 1, "trade_volume": 1, "timestamp": 1788052799000, "ask_bid": "BID"}
        packet = EV.build_market_evidence_packet(
            "KRW-BTC", candles_by_timeframe={tf: [candle, copy.deepcopy(candle)] for tf in ("15m", "1h", "4h", "1d")},
            trades=[trade, copy.deepcopy(trade)],
            orderbook_row={"timestamp": 1788052799000, "orderbook_units": [{"bid_price": 1, "bid_size": 10000, "ask_price": 1, "ask_size": 10000}]},
            as_of=as_of, captured_at=as_of, policy=policy,
        )
        result = EV.market_evidence_result(
            packet, policy=policy, generated_at=as_of,
            source_identity={"source_id": "fixture"},
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertTrue(any("DUPLICATE" in reason for reason in result["reasons"]))
        self.assertTrue(any("STALE" in reason for reason in result["reasons"]))
        malformed = [{"trade_price": 1}]
        with self.assertRaisesRegex(EV.MarketEvidenceError, "TRADE_FIELD_MISSING"):
            EV.build_trades_evidence("KRW-BTC", malformed, captured_at=as_of, max_staleness_seconds=1)


class PopulationIntegrationTests(unittest.TestCase):
    def test_complete_exact_hash_market_is_pass_and_idempotent(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            record = _synthetic_anchor_record()
            repo_root, record_path, _contract = _active_repo(repo_tmp, record)
            lineage = BRIDGE.consume_universe_record(
                record_path, contract_path=repo_root / "config/upbit_p3_p4_bridge_contract.json",
                repo_root=repo_root,
            )
            lineage = copy.deepcopy(lineage)
            lineage["markets"] = ["KRW-BTC"]
            lineage["market_count"] = 1
            key = BRIDGE.snapshot_key(lineage)
            contract = CAP.load_contract()
            open_by_timeframe = {
                "15m": ["2026-08-30T00:45:00", "2026-08-30T01:00:00"],
                "1h": ["2026-08-29T23:00:00", "2026-08-30T00:00:00"],
                "4h": ["2026-08-29T16:00:00", "2026-08-29T20:00:00"],
                "1d": ["2026-08-28T00:00:00", "2026-08-29T00:00:00"],
            }
            responses = {}
            for timeframe, opens in open_by_timeframe.items():
                unit = contract["candle_upbit_unit_by_timeframe"][timeframe]
                count = contract["candle_lookback_count_by_timeframe"][timeframe]
                url = (
                    contract["candles_minutes_endpoint_template"].format(
                        UNIT=unit, MARKET="KRW-BTC", COUNT=count,
                    ) if unit is not None else
                    contract["candles_days_endpoint_template"].format(MARKET="KRW-BTC", COUNT=count)
                )
                responses[url] = json.dumps([
                    {
                        "market": "KRW-BTC", "candle_date_time_utc": opened,
                        "opening_price": 1000, "high_price": 1010, "low_price": 990,
                        "trade_price": 1005, "candle_acc_trade_price": 1000000,
                        "candle_acc_trade_volume": 1000,
                    }
                    for opened in reversed(opens)
                ]).encode()
            responses[contract["trades_endpoint_template"].format(
                MARKET="KRW-BTC", COUNT=contract["trades_lookback_count"]
            )] = json.dumps([
                {"market": "KRW-BTC", "trade_price": 1000, "trade_volume": 1,
                 "timestamp": 1788052799000, "ask_bid": "BID"}
            ]).encode()
            responses[contract["orderbook_endpoint_template"].format(MARKETS="KRW-BTC")] = json.dumps([
                {
                    "market": "KRW-BTC", "timestamp": 1788052799000,
                    "orderbook_units": [
                        {"bid_price": 999 - level, "bid_size": 10000,
                         "ask_price": 1001 + level, "ask_size": 10000}
                        for level in range(5)
                    ],
                }
            ]).encode()

            def fetcher(url, timeout):
                return responses[url]

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                raw_root = root / "raw"
                data_root = root / "data"
                CAP.capture_snapshot(
                    raw_root, markets=["KRW-BTC"], snapshot_date=dt.date(2026, 8, 30),
                    snapshot_key=key, universe_lineage=lineage,
                    contract=contract, fetcher=fetcher, sleeper=lambda _: None,
                    clock=lambda: dt.datetime(2026, 8, 30, 1, 20, tzinfo=dt.timezone.utc),
                )
                first = POP.populate(key, raw_root=raw_root, data_root=data_root)
                second = POP.populate(key, raw_root=raw_root, data_root=data_root)
                self.assertEqual(first["outcome"], "populated")
                self.assertEqual(second["outcome"], "verified_existing")
                packet = json.loads((data_root / key / "packet.json").read_text(encoding="utf-8"))
                self.assertEqual(packet["summary"]["pass_count"], 1)
                self.assertEqual(packet["summary"]["unknown_count"], 0)
                self.assertEqual(packet["market_results"]["KRW-BTC"]["status"], "PASS")
                self.assertEqual(packet["universe_lineage"]["record_payload_sha256"], lineage["record_payload_sha256"])
                self.assertEqual(packet["policy_packet_sha256"], lineage["p4_policy"]["packet_sha256"])
                market_result = packet["market_results"]["KRW-BTC"]
                self.assertLessEqual(market_result["observed_at"], market_result["available_at"])
                self.assertLessEqual(market_result["available_at"], market_result["generated_at"])
                self.assertEqual(market_result["source_identity"]["source_id"], "upbit_public_api")
                self.assertEqual(len(market_result["source_identity"]["raw_manifest_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
