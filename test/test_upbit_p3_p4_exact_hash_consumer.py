"""P3-12 -> P4-07 exact-hash consumer and fail-close regressions."""
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


def staged_repo(tmp: str, record: dict) -> tuple[Path, Path]:
    root = Path(tmp)
    for relative in (
        "config/upbit_p3_p4_bridge_contract.json",
        "config/upbit_market_evidence_policy_ratified.json",
        "config/upbit_identity_taxonomy_governance_freeze.json",
        "evidence/crypto/upbit/raw/2026-08-30/_manifest.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    target = root / "data/observations/upbit_tradeable_universe/2026-08-30/packet.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record), encoding="utf-8")
    return root, target


def release_staged_governance(root: Path) -> Path:
    """P3-12-GOV-03A: a synthetic RELEASED fixture, built by mutating only
    this test's isolated STAGED copy of the freeze registry -- the real
    committed ``config/upbit_identity_taxonomy_governance_freeze.json`` is
    never touched. Flipping ``resolution_status`` alone would NOT be enough
    to un-freeze the anchor (a record's presence in ``records`` is itself
    the block, independent of ``resolution_status`` -- see
    ``test_upbit_identity_taxonomy_governance_freeze.py``'s
    ``test_released_fixture_still_blocks_its_own_registered_records``), so
    this also drops the P3 lineage entry from the staged copy's own
    ``records`` list and re-signs the registry's self-hash.
    """
    governance_path = root / "config/upbit_identity_taxonomy_governance_freeze.json"
    governance = json.loads(governance_path.read_text(encoding="utf-8"))
    governance["resolution_status"] = "RATIFIED_BY_EXPLICIT_CIO_DECISION"
    governance["blocked_universe_record_payload_sha256s"] = []
    governance["blocked_universe_packet_payload_sha256s"] = []
    governance["records"] = [
        record for record in governance["records"]
        if record["source_path"] != "data/observations/upbit_tradeable_universe/2026-08-30/packet.json"
    ]
    governance["payload_sha256"] = BRIDGE.FREEZE.payload_sha256(
        {key: value for key, value in governance.items() if key != "payload_sha256"}
    )
    governance_path.write_text(json.dumps(governance), encoding="utf-8")
    contract_path = root / "config/upbit_p3_p4_bridge_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["identity_governance"]["file_sha256"] = BRIDGE.file_sha256(governance_path)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    return contract_path


def released_anchor_lineage() -> dict:
    record = json.loads(ANCHOR.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        root, path = staged_repo(tmp, record)
        contract_path = release_staged_governance(root)
        return BRIDGE.consume_universe_record(
            path,
            expected_record_sha256=record["payload_sha256"],
            contract_path=contract_path,
            repo_root=root,
        )


class ExactHashBridgeTests(unittest.TestCase):
    def test_authoritative_anchor_is_frozen_before_provider_capture(self):
        with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_IDENTITY_AUTHORITY_FROZEN:a9be9c63"):
            BRIDGE.consume_universe_record(
                ANCHOR,
                expected_record_sha256="a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12",
            )

    def test_governance_contract_rejects_tampered_freeze_file(self):
        record = json.loads(ANCHOR.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            root, path = staged_repo(tmp, record)
            governance_path = root / "config/upbit_identity_taxonomy_governance_freeze.json"
            governance = json.loads(governance_path.read_text(encoding="utf-8"))
            governance["reason"] = f"{governance['reason']} tampered"
            governance_path.write_text(json.dumps(governance), encoding="utf-8")
            with self.assertRaisesRegex(
                BRIDGE.BridgeError, "IDENTITY_GOVERNANCE_FILE_HASH_MISMATCH"
            ):
                BRIDGE.consume_universe_record(
                    path,
                    expected_record_sha256=record["payload_sha256"],
                    contract_path=root / "config/upbit_p3_p4_bridge_contract.json",
                    repo_root=root,
                )

    def test_explicitly_released_fixture_retains_eight_market_lineage(self):
        lineage = released_anchor_lineage()
        self.assertEqual(lineage["market_count"], 8)
        self.assertEqual(
            lineage["markets"],
            ["KRW-BTC", "KRW-ETH", "KRW-LINK", "KRW-SHIB", "KRW-SOL", "KRW-SUI", "KRW-WLD", "KRW-XRP"],
        )
        self.assertEqual(lineage["identity_unratified_count"], 220)
        self.assertFalse(lineage["historical_identity_backfill_applied"])
        self.assertEqual(
            lineage["p4_policy"]["packet_sha256"],
            "26d921e4b98f91010b4397d6642c1dc6021d06ef134977cc80a94692e6e1df5e",
        )

    def test_exact_record_hash_mismatch_fails_before_capture(self):
        with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_RECORD_EXACT_HASH_MISMATCH"):
            BRIDGE.consume_universe_record(ANCHOR, expected_record_sha256="0" * 64)

    def test_missing_record_fails_closed(self):
        with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_RECORD_UNREADABLE"):
            BRIDGE.consume_universe_record(Path("/definitely/missing/p3.json"))

    def test_duplicate_market_is_rejected_even_when_forgery_is_rehashed(self):
        record = json.loads(ANCHOR.read_text(encoding="utf-8"))
        record["packet"]["markets"].append(copy.deepcopy(record["packet"]["markets"][0]))
        record["packet"]["summary"]["market_count"] += 1
        forged_hash = rehash_packet_and_record(record)
        with tempfile.TemporaryDirectory() as tmp:
            root, path = staged_repo(tmp, record)
            contract_path = release_staged_governance(root)
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["initial_post_ratification_anchor"]["record_payload_sha256"] = forged_hash
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_DUPLICATE_MARKET"):
                BRIDGE.consume_universe_record(
                    path, expected_record_sha256=forged_hash,
                    contract_path=contract_path, repo_root=root,
                )

    def test_historical_identity_backfill_is_forbidden(self):
        record = json.loads(ANCHOR.read_text(encoding="utf-8"))
        record["snapshot_date"] = "2026-08-29"
        record["packet"]["snapshot_date"] = "2026-08-29"
        forged_hash = rehash_packet_and_record(record)
        with tempfile.TemporaryDirectory() as tmp:
            root, path = staged_repo(tmp, record)
            contract_path = release_staged_governance(root)
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["initial_post_ratification_anchor"]["path"] = "not-this-record.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.BridgeError, "HISTORICAL_IDENTITY_BACKFILL_FORBIDDEN"):
                BRIDGE.consume_universe_record(
                    path, expected_record_sha256=forged_hash,
                    contract_path=contract_path, repo_root=root,
                )

    def test_active_released_contract_copying_the_blocked_anchor_still_blocks(self):
        # P3-12-GOV-03A Section F.2: flipping resolution_status to a
        # released state alone must NOT revive an anchor that is still
        # listed in ``records`` -- only a genuinely re-hardened/absent
        # record does. This mutates a staged (never the real committed)
        # copy of the governance file, re-signing its self-hash, without
        # touching ``records``.
        record = json.loads(ANCHOR.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            root, path = staged_repo(tmp, record)
            governance_path = root / "config/upbit_identity_taxonomy_governance_freeze.json"
            governance = json.loads(governance_path.read_text(encoding="utf-8"))
            governance["resolution_status"] = "RATIFIED_BY_EXPLICIT_CIO_DECISION"
            governance["payload_sha256"] = BRIDGE.FREEZE.payload_sha256(
                {key: value for key, value in governance.items() if key != "payload_sha256"}
            )
            governance_path.write_text(json.dumps(governance), encoding="utf-8")
            contract_path = root / "config/upbit_p3_p4_bridge_contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["identity_governance"]["file_sha256"] = BRIDGE.file_sha256(governance_path)
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.BridgeError, "UNIVERSE_IDENTITY_AUTHORITY_FROZEN:a9be9c63"):
                BRIDGE.consume_universe_record(
                    path,
                    expected_record_sha256=record["payload_sha256"],
                    contract_path=contract_path,
                    repo_root=root,
                )

    def test_ratified_policy_exact_self_hash_is_verified(self):
        policy = BRIDGE.load_ratified_p4_policy()
        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(
            BRIDGE.payload_sha256({k: v for k, v in policy.items() if k != "packet_sha256"}),
            policy["packet_sha256"],
        )


class CaptureAndEvidenceFailCloseTests(unittest.TestCase):
    def setUp(self):
        self.lineage = released_anchor_lineage()

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
        lineage = released_anchor_lineage()
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
    unittest.main(verbosity=2)
