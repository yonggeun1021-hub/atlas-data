#!/usr/bin/env python3
"""P5-08 contract/3 regression: ratified P4-07 reader + CRYPTO_PAPER_RUNTIME_V1
regime wiring. Contract/2 stays the default and byte-identical."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PROMO = _load("crypto_candidate_promotion_v3_under_test", "universe/crypto_candidate_promotion.py")
RT_FIXTURES = _load("crypto_candidate_promotion_v3_runtime_fixtures", "test/test_crypto_paper_runtime.py")
RUNTIME = RT_FIXTURES.RUNTIME
UNI = PROMO.UPBIT_UNIVERSE
REGIME_OC = PROMO.REGIME_OUTPUT_CONTRACT
MARKET_EV = PROMO.MARKET_EVIDENCE

D = dt.date
DAY = "2026-09-20"
REFERENCE_AT = "2026-09-20T07:40:00Z"
RUNTIME_EVALUATED_AT = "2026-09-20T07:30:00Z"
ALT_MARKET = "KRW-ETH"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def universe_row(market="KRW-BTC", canonical_asset_id="BTC", caution_any=False):
    return {
        "market": market,
        "state": UNI.STATE_PAPER_ELIGIBLE,
        "reason": "PAPER_ELIGIBLE_ALL_GATES_PASSED",
        "candidate_canonical_asset_id": canonical_asset_id,
        "market_event_warning": False,
        "market_event_caution_any": caution_any,
        "observed_daily_candle_count": 120,
        "trailing_30d_krw_turnover": "10000000000",
        "kraken_cross_exchange_reference": False,
        "authority": dict(UNI._ROW_AUTHORITY),
    }


def universe_packet(rows, day=DAY):
    policy = UNI.load_policy()
    taxonomy = UNI.load_taxonomy()
    packet = {
        "schema_version": UNI.OUTPUT_SCHEMA_VERSION,
        "snapshot_date": day,
        "evaluation_as_of": day,
        "available_at": f"{day}T00:40:00Z",
        "manifest_sha256": "a" * 64,
        "policy_version": policy["policy_version"],
        "policy_ratified": True,
        "taxonomy_version": taxonomy["policy_version"],
        "taxonomy_ratified": True,
        "duplicate_market_codes": {},
        "summary": {
            "market_count": len(rows),
            "observation_pool_count": 0,
            "tradeable_universe_count": 0,
            "paper_eligible_count": len(rows),
            "blocked_count": 0,
        },
        "markets": rows,
        "authority": dict(UNI._ROW_AUTHORITY),
    }
    packet["payload_sha256"] = UNI.payload_sha256(packet)
    return packet


def market_evidence(market="KRW-BTC", *, policy=None, captured="2026-09-20T07:05:00Z",
                    bid=999, ask=1001):
    captured_at = dt.datetime.strptime(captured, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    as_of = captured_at - dt.timedelta(minutes=5)
    seconds = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
    candles = {}
    for timeframe in MARKET_EV.finalization.TIMEFRAMES:
        step = seconds[timeframe]
        last_close = int(as_of.timestamp()) // step * step
        rows = []
        for index, close in enumerate((last_close - step, last_close)):
            opened = dt.datetime.fromtimestamp(close - step, tz=dt.timezone.utc)
            rows.append({
                "candle_date_time_utc": opened.strftime("%Y-%m-%dT%H:%M:%S"),
                "opening_price": 1000, "high_price": 1010, "low_price": 990,
                "trade_price": 1005 + index, "candle_acc_trade_price": 123456,
                "candle_acc_trade_volume": 12.3,
            })
        candles[timeframe] = list(reversed(rows))
    timestamp_ms = int(as_of.timestamp() * 1000)
    trades = [{"market": market, "trade_price": 1000, "trade_volume": 1,
               "timestamp": timestamp_ms, "ask_bid": "BID"}]
    orderbook = {
        "market": market, "timestamp": timestamp_ms,
        "orderbook_units": [
            {"bid_price": bid, "bid_size": 10000, "ask_price": ask, "ask_size": 10000}
            for _ in range(5)
        ],
    }
    return MARKET_EV.build_market_evidence_packet(
        market, candles_by_timeframe=candles, trades=trades, orderbook_row=orderbook,
        as_of=as_of, captured_at=captured_at,
        policy=MARKET_EV.load_ratified_policy() if policy is None else policy,
    )


def regime_envelope(generated_at=REFERENCE_AT):
    return REGIME_OC.build_unknown_output("CRYPTO", generated_at)


def _risk_off_records(start, end):
    records = RT_FIXTURES.chain(start, end, vol="0.75", dd="-0.05", breadth="0.40",
                                daily="-1", weekly="-2")
    for row in records.values():
        row["btc"]["trend_category"] = "BELOW_200DMA"
    return records


def runtime_decision(state: str, evaluation_at=RUNTIME_EVALUATED_AT) -> dict:
    """Genuine CRYPTO_PAPER_RUNTIME_V1 packets from the runtime's own
    calculation (not hand-built dicts)."""
    start, current = D(2026, 9, 14), D(2026, 9, 20)
    if state == "RISK_ON":
        records = RT_FIXTURES.chain(start, current)
    elif state == "NEUTRAL":
        records = RT_FIXTURES.chain(start, current, breadth="0.50", daily="100", weekly="-1")
    elif state == "RISK_OFF":
        records = _risk_off_records(start, current)
    elif state == "STRESS":
        records = RT_FIXTURES.chain(start, current)
        records["2026-09-20"] = RT_FIXTURES.record(current, dd="-0.30")
    elif state == "UNKNOWN":
        records = RT_FIXTURES.chain(D(2026, 9, 16), current)  # 4 prior complete days only
    else:
        raise AssertionError(state)
    packet = RT_FIXTURES.evaluate(records, evaluation_at=evaluation_at)
    assert packet["runtime_regime"] == state, (state, packet["reasons"])
    return packet


def rehash_runtime(packet: dict) -> dict:
    packet = copy.deepcopy(packet)
    packet.pop("decision_id")
    packet["decision_id"] = "crypto-paper-regime:" + RUNTIME.payload_sha256(packet)
    return packet


class _RatifiedUniverseMixin:
    """Same test-only P3-12 ratification fixture swap as
    test_crypto_candidate_promotion.BuildPromotionPacketTests."""

    def setUp(self):
        policy = UNI.load_policy()
        taxonomy = UNI.load_taxonomy()
        registry = UNI.load_identity_registry()
        for doc, field in ((policy, "effective_date"), (taxonomy, "effective_from"), (registry, "effective_from")):
            doc["approval_status"] = "RATIFIED"
            doc[field] = "2026-08-28"
        self._patches = [
            mock.patch.object(UNI, "load_policy", return_value=policy),
            mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy),
            mock.patch.object(UNI, "load_identity_registry", return_value=registry),
            mock.patch.object(UNI.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()

    def build(self, *, runtime=None, reference_at=REFERENCE_AT, evidence=None, rows=None,
              contract_version=3, leadership=None):
        day = reference_at[:10]
        rows = rows or [universe_row(), universe_row(ALT_MARKET, "ETH")]
        if evidence is None:
            evidence = {row["market"]: market_evidence(row["market"]) for row in rows}
        kwargs = {"evaluation_as_of": day}
        if contract_version != 2:
            kwargs.update(contract_version=contract_version, crypto_runtime_decision=runtime)
        return PROMO.build_promotion_packet(
            universe_packet(rows, day), regime_envelope(reference_at), evidence, leadership, **kwargs,
        )


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class ContractV3Tests(unittest.TestCase):
    def test_contract_v3_is_hash_pinned_and_loads(self):
        raw = PROMO.CONTRACT_V3_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), PROMO.CONTRACT_V3_SHA256)
        contract = PROMO.load_contract_v3()
        self.assertEqual(contract["contract_version"], "crypto_candidate_promotion_contract/3")
        self.assertTrue(all(value is False for value in contract["authority"].values()))

    def test_contract_v3_tamper_fails_closed(self):
        value = json.loads(PROMO.CONTRACT_V3_PATH.read_text(encoding="utf-8"))
        value["regime_gate"]["states"]["RISK_OFF"]["criterion_status"] = "PASS"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError, "CONTRACT_V3_HASH_MISMATCH"):
                PROMO.load_contract_v3(path)

    def test_regime_gate_is_allocation_v2_verbatim(self):
        # USER_RATIFICATION_PAPER_MARKET_ALLOCATION_V2_20260913.json
        # (sha256 345801ab...): new_buys_by_market_state and
        # per_market_state_multiplier_of_base.
        new_buys = {"RISK_ON": "PERMIT", "NEUTRAL": "PERMIT_SELECTIVE", "RISK_OFF": "DENY",
                    "STRESS": "DENY", "UNKNOWN": "DENY"}
        multipliers = {"RISK_ON": "1.00", "NEUTRAL": "0.70", "RISK_OFF": "0.25", "STRESS": "0.00"}
        for state, (status, buys, multiplier, hold_cap) in PROMO.REGIME_GATE_V3.items():
            with self.subTest(state=state):
                self.assertEqual(buys, new_buys[state])
                self.assertEqual(multiplier, multipliers.get(state))
                self.assertEqual(status, {"PERMIT": "PASS", "PERMIT_SELECTIVE": "PASS"}.get(
                    buys, "UNKNOWN" if state == "UNKNOWN" else "FAIL"))
                self.assertEqual(hold_cap, "0.50" if state == "UNKNOWN" else None)
        self.assertEqual(PROMO.ALLOCATION_V2_RECORD_SHA256,
                         "345801ab907f75c4761097670430fb097e5e8d3b1e595217850fe20fd240a4c8")

    def test_every_runtime_authorized_regime_has_a_gate_row(self):
        self.assertEqual(set(RUNTIME.load_runtime_authorized_regimes()), set(PROMO.REGIME_GATE_V3))

    def test_contract_v2_file_untouched(self):
        self.assertEqual(PROMO.load_contract()["contract_version"], "crypto_candidate_promotion_contract/2")

    def test_regime_source_binds_ratified_runtime_policy(self):
        source = PROMO.load_contract_v3()["regime_source"]
        self.assertEqual(source["policy_sha256"], RUNTIME.POLICY_SHA256)
        self.assertEqual(source["ratification_record_sha256"], RUNTIME.RATIFICATION_RECORD_SHA256)


# ---------------------------------------------------------------------------
# REGIME wiring: every state, no exception for KNOWN values
# ---------------------------------------------------------------------------

class RegimeStateWiringTests(_RatifiedUniverseMixin, unittest.TestCase):
    EXPECTED = {
        "RISK_ON": ("PASS", "PERMIT", "1.00", None, "WATCH"),
        "NEUTRAL": ("PASS", "PERMIT_SELECTIVE", "0.70", None, "WATCH"),
        "RISK_OFF": ("FAIL", "DENY", "0.25", None, "BLOCKED"),
        "STRESS": ("FAIL", "DENY", "0.00", None, "BLOCKED"),
        "UNKNOWN": ("UNKNOWN", "DENY", None, "0.50", "WATCH"),
    }

    def test_each_state_flows_into_promotion_without_exception(self):
        for state, (status, new_buys, multiplier, hold_cap, row_state) in self.EXPECTED.items():
            with self.subTest(state=state):
                decision = runtime_decision(state)
                packet = self.build(runtime=decision)
                self.assertEqual(packet["schema_version"], "crypto_candidate_promotion_packet/3")
                self.assertEqual(PROMO.validate_output(packet), packet)
                for row in packet["candidates"]:
                    regime = row["criteria"]["REGIME"]
                    self.assertEqual(regime["status"], status)
                    self.assertEqual(regime["effective_regime"], state)
                    self.assertEqual(regime["new_buys"], new_buys)
                    self.assertEqual(regime["state_multiplier_of_base"], multiplier)
                    self.assertEqual(regime["hold_current_max_multiplier_of_base"], hold_cap)
                    self.assertEqual(regime["runtime_decision_id"], decision["decision_id"])
                    self.assertEqual(row["promotion_state"], row_state)
                    self.assertTrue(all(v is False for v in row["authority"].values()))
                    if row_state == "BLOCKED":
                        self.assertEqual(row["promotion_reason"], "T2_REQUIRED_FAILED:T2_REGIME_PERMITS_NEW_BUYS")
                    self.assertNotEqual(row["promotion_state"], "FOCUSED_REVIEW")

    def test_stress_multiplier_is_zero_and_unknown_opens_no_new_buys(self):
        stress = self.build(runtime=runtime_decision("STRESS"))["candidates"][0]["criteria"]["REGIME"]
        self.assertEqual(stress["state_multiplier_of_base"], "0.00")
        unknown = self.build(runtime=runtime_decision("UNKNOWN"))["candidates"][0]
        self.assertEqual(unknown["criteria"]["REGIME"]["new_buys"], "DENY")
        self.assertEqual(unknown["criteria"]["REGIME"]["hold_current_max_multiplier_of_base"], "0.50")
        self.assertIn("PROVISIONAL_FORWARD_ACCEPTANCE_NOT_PASSED",
                      unknown["criteria"]["REGIME"]["runtime_reasons"])

    def test_missing_runtime_decision_is_unknown_not_error(self):
        row = self.build(runtime=None)["candidates"][0]
        self.assertEqual(row["criteria"]["REGIME"]["status"], "UNKNOWN")
        self.assertEqual(row["criteria"]["REGIME"]["reason"], "CRYPTO_RUNTIME_DECISION_MISSING")

    def test_contract_v2_path_never_reads_the_runtime(self):
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError,
                                    "CRYPTO_RUNTIME_DECISION_REQUIRES_CONTRACT_V3"):
            PROMO.build_promotion_packet(
                universe_packet([universe_row()]), regime_envelope(), {}, None,
                evaluation_as_of=DAY, crypto_runtime_decision=runtime_decision("RISK_ON"),
            )
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError, "CONTRACT_VERSION_UNSUPPORTED"):
            self.build(runtime=None, contract_version=4)


class RegimeTransitionDayTests(_RatifiedUniverseMixin, unittest.TestCase):
    """2026-09-20 07:00Z: first KNOWN after the 5-complete-UTC-day acceptance."""

    def setUp(self):
        super().setUp()
        records = RT_FIXTURES.chain(D(2026, 9, 15), D(2026, 9, 20))
        prior = {key: value for key, value in records.items() if key <= "2026-09-19"}
        self.day_before = RT_FIXTURES.evaluate(prior, evaluation_at="2026-09-19T07:30:00Z")
        self.first_known = RT_FIXTURES.evaluate(records, evaluation_at="2026-09-20T07:30:00Z")
        assert self.day_before["runtime_regime"] == "UNKNOWN"
        assert self.first_known["runtime_regime"] == "RISK_ON"

    def regime(self, runtime, reference_at):
        return self.build(runtime=runtime, reference_at=reference_at)["candidates"][0]["criteria"]["REGIME"]

    def test_before_07z_previous_decision_in_force_is_unknown(self):
        regime = self.regime(self.day_before, "2026-09-20T06:59:59Z")
        self.assertEqual(regime["status"], "UNKNOWN")
        self.assertEqual(regime["reason"], "CRYPTO_RUNTIME_REGIME:UNKNOWN:NEW_BUYS_DENY")
        self.assertEqual(regime["expected_decision_date"], "2026-09-19")

    def test_after_07z_without_refreshed_decision_is_not_carried(self):
        regime = self.regime(self.day_before, "2026-09-20T07:10:00Z")
        self.assertEqual(regime["status"], "UNKNOWN")
        self.assertEqual(regime["reason"], "CRYPTO_RUNTIME_DECISION_NOT_CURRENT:2026-09-19")
        self.assertEqual(regime["expected_decision_date"], "2026-09-20")

    def test_after_07z_first_known_decision_passes(self):
        regime = self.regime(self.first_known, "2026-09-20T07:40:00Z")
        self.assertEqual(regime["status"], "PASS")
        self.assertEqual(regime["reason"], "CRYPTO_RUNTIME_REGIME:RISK_ON:NEW_BUYS_PERMIT")
        self.assertEqual(regime["runtime_decision_date"], "2026-09-20")

    def test_decision_evaluated_after_reference_is_lookahead(self):
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError, "CRYPTO_RUNTIME_DECISION_LOOKAHEAD"):
            self.build(runtime=self.first_known, reference_at="2026-09-20T07:20:00Z")

    def test_known_decision_goes_stale_next_day(self):
        regime = self.regime(self.first_known, "2026-09-21T07:05:00Z")
        self.assertEqual(regime["status"], "UNKNOWN")
        self.assertEqual(regime["reason"], "CRYPTO_RUNTIME_DECISION_NOT_CURRENT:2026-09-20")

    def test_risk_on_to_stress_transition_day_blocks_immediately(self):
        records = RT_FIXTURES.chain(D(2026, 9, 14), D(2026, 9, 20))
        before = RT_FIXTURES.evaluate(
            {k: v for k, v in records.items() if k <= "2026-09-19"}, evaluation_at="2026-09-19T07:30:00Z",
        )
        records["2026-09-20"] = RT_FIXTURES.record(D(2026, 9, 20), dd="-0.30")
        after = RT_FIXTURES.evaluate(records, evaluation_at="2026-09-20T07:30:00Z")
        self.assertEqual((before["runtime_regime"], after["runtime_regime"]), ("RISK_ON", "STRESS"))
        early = self.build(runtime=before, reference_at="2026-09-20T06:30:00Z")["candidates"][0]
        self.assertEqual(early["criteria"]["REGIME"]["status"], "PASS")
        late = self.build(runtime=after, reference_at="2026-09-20T07:40:00Z")["candidates"][0]
        self.assertEqual(late["criteria"]["REGIME"]["status"], "FAIL")
        self.assertEqual(late["promotion_state"], "BLOCKED")


class RuntimeDecisionIntegrityTests(_RatifiedUniverseMixin, unittest.TestCase):
    def test_regime_flip_without_rehash_rejected(self):
        tampered = copy.deepcopy(runtime_decision("RISK_OFF"))
        tampered["runtime_regime"] = tampered["paper_regime"] = "RISK_ON"
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError, "CRYPTO_RUNTIME_DECISION_ID_MISMATCH"):
            self.build(runtime=tampered)

    def test_rehashed_known_regime_on_unaccepted_decision_rejected(self):
        tampered = copy.deepcopy(runtime_decision("UNKNOWN"))
        tampered.update(runtime_regime="RISK_ON", paper_regime="RISK_ON", runtime_decision_available=True,
                        decision_status="PAPER_RUNTIME_CLASSIFIED")
        tampered["authority"]["paper_runtime_display_authorized"] = True
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError,
                                    "CRYPTO_RUNTIME_KNOWN_DECISION_INCONSISTENT"):
            self.build(runtime=rehash_runtime(tampered))

    def test_authority_escalation_rejected(self):
        tampered = copy.deepcopy(runtime_decision("RISK_ON"))
        tampered["authority"]["buy_authorized"] = True
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError,
                                    "CRYPTO_RUNTIME_DECISION_AUTHORITY_INVALID"):
            self.build(runtime=rehash_runtime(tampered))

    def test_foreign_policy_identity_rejected(self):
        tampered = copy.deepcopy(runtime_decision("RISK_ON"))
        tampered["policy_sha256"] = "f" * 64
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError,
                                    "CRYPTO_RUNTIME_DECISION_IDENTITY_MISMATCH"):
            self.build(runtime=rehash_runtime(tampered))

    def test_runtime_policy_unavailable_locally_fails_closed_to_unknown(self):
        decision = runtime_decision("RISK_ON")
        runtime_module = PROMO._crypto_runtime()
        with mock.patch.object(runtime_module, "load_runtime_authorized_regimes", return_value=["UNKNOWN"]):
            regime = self.build(runtime=decision)["candidates"][0]["criteria"]["REGIME"]
        self.assertEqual(regime["status"], "UNKNOWN")
        self.assertEqual(regime["reason"], "CRYPTO_RUNTIME_POLICY_UNAVAILABLE")

    def test_embedded_regime_criterion_tamper_rejected_by_validate_output(self):
        packet = self.build(runtime=runtime_decision("RISK_OFF"))
        tampered = copy.deepcopy(packet)
        tampered["candidates"][0]["criteria"]["REGIME"]["status"] = "PASS"
        tampered["candidates"][0]["promotion_state"] = "WATCH"
        tampered.pop("payload_sha256")
        tampered["payload_sha256"] = PROMO.payload_sha256(tampered)
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.validate_output(tampered)


# ---------------------------------------------------------------------------
# VOLUME_LIQUIDITY: ratified P4-07 reader
# ---------------------------------------------------------------------------

class VolumeLiquidityRatifiedTests(_RatifiedUniverseMixin, unittest.TestCase):
    def volume(self, packet):
        return packet["candidates"][0]["criteria"]["VOLUME_LIQUIDITY"]

    def test_ratified_thresholds_met_passes(self):
        criterion = self.volume(self.build(runtime=runtime_decision("RISK_ON")))
        self.assertEqual(criterion["status"], "PASS")
        self.assertEqual(criterion["reason"], "P4_07_RATIFIED_THRESHOLDS_MET")
        self.assertEqual(criterion["policy_packet_sha256"],
                         "26d921e4b98f91010b4397d6642c1dc6021d06ef134977cc80a94692e6e1df5e")

    def test_proposal_policy_file_is_never_read_on_v3(self):
        evidence = {"KRW-BTC": market_evidence(), ALT_MARKET: market_evidence(ALT_MARKET)}
        real_load_policy = MARKET_EV.load_policy
        read_paths = []

        def spy(path=MARKET_EV.POLICY_PATH):
            read_paths.append(Path(path))
            return real_load_policy(path)

        with mock.patch.object(MARKET_EV, "load_policy", side_effect=spy):
            packet = self.build(runtime=None, evidence=evidence)
        self.assertTrue(read_paths)
        self.assertNotIn(MARKET_EV.POLICY_PATH, read_paths)
        self.assertEqual(self.volume(packet)["status"], "PASS")

    def test_ratified_policy_absent_or_invalid_fails_closed_to_unknown(self):
        evidence = {"KRW-BTC": market_evidence(), ALT_MARKET: market_evidence(ALT_MARKET)}
        for code in ("JSON_READ_FAILED:missing", "POLICY_PACKET_HASH_MISMATCH", "POLICY_EXACT_PIN_MISMATCH"):
            with self.subTest(code=code), mock.patch.object(
                MARKET_EV, "load_ratified_policy", side_effect=MARKET_EV.MarketEvidenceError(code),
            ):
                criterion = self.volume(self.build(runtime=None, evidence=evidence))
                self.assertEqual(criterion["status"], "UNKNOWN")
                self.assertEqual(criterion["reason"], "P4_07_RATIFIED_POLICY_UNAVAILABLE:" + code.split(":")[0])

    def test_packet_built_under_proposal_policy_is_unknown(self):
        proposal = MARKET_EV.load_policy()
        evidence = {"KRW-BTC": market_evidence(policy=proposal), ALT_MARKET: market_evidence(ALT_MARKET)}
        criterion = self.volume(self.build(runtime=None, evidence=evidence))
        self.assertEqual(criterion["status"], "UNKNOWN")
        self.assertEqual(criterion["reason"], "MARKET_EVIDENCE_PACKET_NOT_BOUND_TO_RATIFIED_POLICY")

    def test_spread_breach_is_unknown_not_fail(self):
        evidence = {"KRW-BTC": market_evidence(bid=900, ask=1100), ALT_MARKET: market_evidence(ALT_MARKET)}
        packet = self.build(runtime=runtime_decision("RISK_ON"), evidence=evidence)
        criterion = self.volume(packet)
        self.assertEqual(criterion["status"], "UNKNOWN")
        self.assertEqual(criterion["reason"], "P4_07_RATIFIED_EVIDENCE_NOT_PASSED")
        self.assertIn("SPREAD_ABOVE_RATIFIED_MAX", criterion["ratified_evidence_reasons"])
        self.assertEqual(packet["candidates"][0]["promotion_state"], "WATCH")

    def test_capture_before_ratified_effective_window_is_unknown(self):
        early = market_evidence(captured="2026-08-29T23:30:00Z")
        criterion = PROMO.evaluate_volume_liquidity_ratified(
            "KRW-BTC", early, ratified_policy=MARKET_EV.load_ratified_policy(),
            policy_unavailable_reason=None,
        )
        self.assertEqual(criterion["status"], "UNKNOWN")
        self.assertEqual(criterion["reason"], "MARKET_EVIDENCE_CAPTURED_OUTSIDE_RATIFIED_POLICY_WINDOW")

    def test_missing_packet_and_incomplete_family_stay_unknown(self):
        policy = MARKET_EV.load_ratified_policy()
        self.assertEqual(
            PROMO.evaluate_volume_liquidity_ratified("KRW-BTC", None, ratified_policy=policy,
                                                     policy_unavailable_reason=None)["reason"],
            "MARKET_EVIDENCE_PACKET_MISSING",
        )
        packet = market_evidence()
        packet["trades"]["trade_count"] = 0
        self.assertEqual(
            PROMO.evaluate_volume_liquidity_ratified("KRW-BTC", packet, ratified_policy=policy,
                                                     policy_unavailable_reason=None)["reason"],
            "EVIDENCE_FAMILY_INCOMPLETE",
        )


# ---------------------------------------------------------------------------
# Unchanged gates and contract/2 compatibility
# ---------------------------------------------------------------------------

class UnchangedGatesTests(_RatifiedUniverseMixin, unittest.TestCase):
    UNCHANGED = ("IDENTITY", "TRADABILITY", "TREND", "RELATIVE_STRENGTH", "OVEREXTENSION", "MATERIAL_BLOCKER")

    def test_other_criteria_identical_between_contract_2_and_3(self):
        v2 = self.build(contract_version=2)
        v3 = self.build(runtime=runtime_decision("RISK_ON"))
        for row2, row3 in zip(v2["candidates"], v3["candidates"]):
            for name in self.UNCHANGED:
                with self.subTest(market=row2["market"], criterion=name):
                    self.assertEqual(row2["criteria"][name], row3["criteria"][name])
        btc = v3["candidates"][0]["criteria"]
        self.assertEqual(btc["TREND"]["reason"], "NO_RATIFIED_CANDIDATE_TREND_RULE")
        self.assertEqual(btc["RELATIVE_STRENGTH"]["reason"], "LEADERSHIP_OUTPUT_MISSING")
        self.assertEqual(btc["OVEREXTENSION"]["reason"], "NO_RATIFIED_OVEREXTENSION_THRESHOLD")
        self.assertEqual(btc["MATERIAL_BLOCKER"]["reason"], "SECURITY_AND_NETWORK_OUTAGE_COVERAGE_MISSING")

    def test_known_regime_still_cannot_reach_focused_review_without_rotation_source(self):
        for state in ("RISK_ON", "NEUTRAL"):
            with self.subTest(state=state):
                packet = self.build(runtime=runtime_decision(state))
                self.assertEqual(packet["summary"]["focused_review_count"], 0)
                self.assertEqual(
                    packet["candidates"][0]["promotion_reason"],
                    "T2_REQUIRED_UNKNOWN:T2_ROTATION_MEMBERSHIP",
                )

    def test_contract_2_default_is_unchanged(self):
        default = self.build(contract_version=2)
        explicit = PROMO.build_promotion_packet(
            universe_packet([universe_row(), universe_row(ALT_MARKET, "ETH")]), regime_envelope(),
            {m: market_evidence(m) for m in ("KRW-BTC", ALT_MARKET)}, None,
            evaluation_as_of=DAY, contract_version=2,
        )
        self.assertEqual(PROMO.canonical_json(default), PROMO.canonical_json(explicit))
        self.assertEqual(default["schema_version"], "crypto_candidate_promotion_packet/2")
        self.assertEqual(set(default["source_packets"]),
                         {"universe", "regime", "market_evidence_by_market", "leadership"})
        row = default["candidates"][0]["criteria"]
        self.assertEqual(row["REGIME"]["reason"], "REGIME_AGGREGATE_UNAUTHORIZED_PENDING_P1_COM_05")
        self.assertEqual(row["VOLUME_LIQUIDITY"]["reason"], "VOLUME_LIQUIDITY_THRESHOLDS_UNRATIFIED")
        self.assertEqual(PROMO.validate_output(default), default)

    def test_production_callers_still_request_contract_2(self):
        """Crypto PAPER wiring v2 (build plan PR3) requests contract/3 only for
        decision snapshot /4, which is emitted from the user-ratified cutover
        T_cut 2026-09-18T07:00Z; every /1-/3 packet before it still requests
        contract/2."""
        decision = _load("crypto_candidate_promotion_v3_caller_check", "decision/crypto_paper_decision_snapshot.py")
        self.assertEqual(decision.v4_cutover_at().strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-09-18T07:00:00Z")
        for instant in ("2026-09-14T23:43:41Z", "2026-09-18T06:59:59Z"):
            with self.subTest(instant=instant):
                self.assertNotEqual(
                    decision.schema_version_for(decision._parse_utc(instant, "t")),
                    decision.V4_OUTPUT_SCHEMA_VERSION,
                )
        source = (ROOT / "decision/crypto_paper_decision_snapshot.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("v4_promotion_kwargs(runtime_decision_entry, rotation_entry) if v4_mode else {}"), 1)

    def test_v3_decision_and_bridge_paths_request_contract_2_at_runtime(self):
        """Behavioural /3 coverage: re-deriving a committed /3 decision packet and
        the /3 bridge rebuild call P5-08 without any contract/3 argument and never
        call P5-09 contract/3."""
        path = ROOT / T2CurrentEvidenceTests.DECISION
        if not path.exists():
            self.skipTest("retained decision evidence not present in this checkout")
        packet = json.loads(path.read_text(encoding="utf-8"))
        bridge = _load("crypto_candidate_promotion_v3_bridge_caller_check", "shadow/crypto_paper_runtime_bridge.py")
        decision = bridge.DECISION
        calls = []
        real_build = decision.PROMOTION.build_promotion_packet

        def spy(*args, **kwargs):
            calls.append(kwargs)
            return real_build(*args, **kwargs)

        forbidden = {"contract_version", "crypto_runtime_decision", "rotation_confirmation"}
        with mock.patch.object(decision.PROMOTION, "build_promotion_packet", side_effect=spy), \
                mock.patch.object(decision.ELIGIBILITY, "build_eligibility_packet_v3",
                                  side_effect=AssertionError("contract/3 eligibility on the /3 path")):
            self.assertEqual(decision.validate_output(copy.deepcopy(packet)), packet)
            decision_calls = len(calls)
            bridge._promotion_packet(packet)
        self.assertGreaterEqual(decision_calls, 1)
        self.assertGreater(len(calls), decision_calls)
        for kwargs in calls:
            self.assertFalse(forbidden & set(kwargs), kwargs)


# ---------------------------------------------------------------------------
# RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1 (user-ratified B2)
# ---------------------------------------------------------------------------

def _pass(reason="TEST"):
    return {"status": "PASS", "reason": reason}


class T2RuleContractTests(unittest.TestCase):
    def test_rule_is_bound_to_committed_ratification_records(self):
        rule = PROMO.load_contract_v3()["promotion_rule"]
        self.assertEqual(rule["rule_id"], "RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1")
        for key, expected in (
            ("source_record", "0e2691e072f4193b6fcd07c14cf2c87be469c4acb9167eca0cbd5d72a390e1c5"),
            ("t2_definition_record", "6870b4572fe46901e9e2ce14e07e01d89c54ba2860ab42a0087f16a4c4703625"),
            ("entry_baseline_record", "b2a905c4eaf23d44749d3e5bcd59b2efe34ff0b0ab5c955a8ce1e0870163154f"),
        ):
            with self.subTest(record=key):
                raw = (ROOT / rule[key]["repo_path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        b2 = json.loads((ROOT / rule["source_record"]["repo_path"]).read_text(encoding="utf-8"))
        self.assertEqual(b2["decisions"]["B2"]["status"], "RATIFIED")
        definition = json.loads((ROOT / rule["t2_definition_record"]["repo_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(definition["ratified"]["t2_minimum_conditions"]), 6)

    def test_missing_or_altered_ratification_record_fails_closed(self):
        real_read_bytes = Path.read_bytes

        def altered(path):
            raw = real_read_bytes(path)
            return raw + b" " if path.name == "paper_b2_b3_size_assembly_user_ratification_20260915.json" else raw

        with mock.patch.object(Path, "read_bytes", altered):
            with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError,
                                        "CONTRACT_V3_RATIFICATION_RECORD_HASH_MISMATCH"):
                PROMO.load_contract_v3()

    def test_state_rule_uses_only_the_six_required_conditions(self):
        t2 = {name: _pass() for name in PROMO.T2_REQUIRED_CONDITIONS}
        self.assertEqual(PROMO.aggregate_t2_state(t2), ("FOCUSED_REVIEW", "T2_REQUIRED_ALL_PASSED"))
        unknown = dict(t2, T2_PRICE_DATA={"status": "UNKNOWN", "reason": "X"})
        self.assertEqual(PROMO.aggregate_t2_state(unknown)[0], "WATCH")
        failed = dict(unknown, T2_REGIME_PERMITS_NEW_BUYS={"status": "FAIL", "reason": "X"})
        self.assertEqual(PROMO.aggregate_t2_state(failed),
                         ("BLOCKED", "T2_REQUIRED_FAILED:T2_REGIME_PERMITS_NEW_BUYS"))
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.aggregate_t2_state(dict(t2, TREND=_pass()))
        self.assertEqual(set(PROMO.NON_BLOCKING_CRITERIA_V3),
                         {"TREND", "OVEREXTENSION", "RELATIVE_STRENGTH", "VOLUME_LIQUIDITY", "MATERIAL_BLOCKER"})


class T2RuleBehaviourTests(_RatifiedUniverseMixin, unittest.TestCase):
    def _rotation_confirmed(self):
        return mock.patch.object(
            PROMO, "evaluate_t2_rotation_membership",
            side_effect=lambda asset: {"status": "PASS", "reason": "TEST_ROTATION_STRONG_CONFIRMED"},
        )

    def test_non_blocking_criteria_never_block_when_all_six_pass(self):
        rs_negative = {
            "market": "CRYPTO", "as_of_date": DAY,
            "windows": [{"window_id": "primary_30d", "role": "PRIMARY", "status": "OBSERVED_UNCLASSIFIED",
                         "unknown_reason": None, "partial_window_assets": [],
                         "asset_relative_strength": [{"canonical_asset_id": "ETH",
                                                      "relative_strength_vs_btc": "-0.05"}]}],
        }
        rows = [universe_row(), universe_row(ALT_MARKET, "ETH", caution_any=True)]
        evidence = {"KRW-BTC": market_evidence(), ALT_MARKET: market_evidence(ALT_MARKET, bid=900, ask=1100)}
        with self._rotation_confirmed(), mock.patch.object(
            PROMO, "_validate_leadership_output", side_effect=lambda output, as_of: copy.deepcopy(output),
        ):
            packet = self.build(runtime=runtime_decision("RISK_ON"), rows=rows, evidence=evidence,
                                leadership=rs_negative)
        eth = packet["candidates"][1]
        self.assertEqual(eth["criteria"]["MATERIAL_BLOCKER"]["status"], "FAIL")
        self.assertEqual(eth["criteria"]["RELATIVE_STRENGTH"]["status"], "FAIL")
        self.assertEqual(eth["criteria"]["VOLUME_LIQUIDITY"]["status"], "UNKNOWN")
        self.assertEqual(eth["criteria"]["TREND"]["status"], "UNKNOWN")
        self.assertEqual(eth["promotion_state"], "FOCUSED_REVIEW")
        self.assertIn("MATERIAL_BLOCKER:WARNING:FAIL:UPBIT_MARKET_EVENT_CAUTION_ACTIVE", eth["warnings"])
        self.assertIn("TREND:RECORD_ONLY_ENTRY_STAGE:UNKNOWN:NO_RATIFIED_CANDIDATE_TREND_RULE", eth["warnings"])
        self.assertTrue(all(value is False for value in eth["authority"].values()))
        self.assertEqual(packet["candidates"][0]["promotion_state"], "FOCUSED_REVIEW")

    def test_missing_required_inputs_fail_closed(self):
        with self._rotation_confirmed():
            no_runtime = self.build(runtime=None)["candidates"][0]
            no_evidence = self.build(runtime=runtime_decision("RISK_ON"), evidence={})["candidates"][0]
        self.assertEqual(no_runtime["promotion_state"], "WATCH")
        self.assertEqual(no_runtime["promotion_reason"], "T2_REQUIRED_UNKNOWN:T2_REGIME_PERMITS_NEW_BUYS")
        self.assertEqual(no_evidence["promotion_state"], "WATCH")
        self.assertEqual(no_evidence["t2_required_conditions"]["T2_PRICE_DATA"]["reason"],
                         "MARKET_EVIDENCE_PACKET_MISSING")
        rotation = self.build(runtime=runtime_decision("RISK_ON"))["candidates"][0]
        self.assertEqual(rotation["t2_required_conditions"]["T2_ROTATION_MEMBERSHIP"]["reason"],
                         "CRYPTO_ROTATION_CONFIRMATION_NOT_WIRED")
        self.assertEqual(rotation["unapplied_rules"], [
            {"rule_id": "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "reason_code": "CRYPTO_ROTATION_CONFIRMATION_NOT_WIRED"},
        ])

    def test_price_data_requires_latest_completed_utc_day(self):
        stale = market_evidence(captured="2026-09-19T07:05:00Z")
        criterion = PROMO.evaluate_t2_price_data("KRW-BTC", stale, reference_at="2026-09-20T07:40:00Z")
        self.assertEqual(criterion["status"], "UNKNOWN")
        self.assertEqual(criterion["reason"], "PRICE_LATEST_COMPLETED_SESSION_MISSING")
        fresh = market_evidence()
        self.assertEqual(
            PROMO.evaluate_t2_price_data("KRW-BTC", fresh, reference_at="2026-09-20T07:40:00Z")["status"], "PASS",
        )
        self.assertEqual(
            PROMO.evaluate_t2_price_data("KRW-BTC", fresh, reference_at="2026-09-20T07:01:00Z")["reason"],
            "PRICE_AVAILABLE_AFTER_REFERENCE",
        )

    def test_rule_refs_are_additive_and_name_the_blocking_rule(self):
        blocked = self.build(runtime=runtime_decision("STRESS"))["candidates"][0]
        refs = {(ref["rule_id"], ref["role"]) for ref in blocked["rule_refs"]}
        self.assertEqual(refs, {
            ("RULE.ALLOCATION.V2", "BLOCKED_BY"),
            ("RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1", "BLOCKED_BY"),
            ("RULE.CRYPTO.RUNTIME.V1", "BLOCKED_BY"),
            ("RULE.ENTRY.PAPER_BASELINE_B.V1", "APPLIED"),
        })
        for ref in blocked["rule_refs"]:
            self.assertEqual(set(ref), {"rule_id", "version", "registry_sha256", "source_record_sha256", "role"})
            self.assertEqual(ref["source_record_sha256"], PROMO.RULE_SOURCES_V3[ref["rule_id"]])
        watch = self.build(runtime=runtime_decision("RISK_ON"))["candidates"][0]
        self.assertTrue(all(ref["role"] == "APPLIED" for ref in watch["rule_refs"]))


class T2CurrentEvidenceTests(unittest.TestCase):
    """Facts on the committed 2026-09-14/2113 decision inputs (retained,
    append-only evidence): contract/2 and contract/3 states per candidate."""

    DECISION = "evidence/crypto_paper_decision/2026-09-14/2113/340453e260e4cb5247e9f64eb73e036d77662f7c239b1bfd4394d44f705cf85e/packet.json"
    RUNTIME = "evidence/regime/crypto_paper_runtime/2026-09-14/40e533d35f66e22adb1157d31d88a7694ed70d9812f299e943e949acafb4993a.json"

    def test_current_candidates_state_and_reason_changes(self):
        path = ROOT / self.DECISION
        if not path.exists():
            self.skipTest("retained decision evidence not present in this checkout")
        decision_module = _load("crypto_candidate_promotion_v3_decision", "decision/crypto_paper_decision_snapshot.py")
        promotion = decision_module.PROMOTION
        captured = []
        real_build = promotion.build_promotion_packet

        def spy(*args, **kwargs):
            captured.append((args, kwargs))
            return real_build(*args, **kwargs)

        with mock.patch.object(promotion, "build_promotion_packet", side_effect=spy):
            decision_module.validate_output(json.loads(path.read_text(encoding="utf-8")))
        args, kwargs = captured[-1]
        v2 = real_build(*args, **kwargs)
        runtime = json.loads((ROOT / self.RUNTIME).read_text(encoding="utf-8"))
        v3 = real_build(*args, **kwargs, contract_version=3, crypto_runtime_decision=runtime)
        self.assertEqual(promotion.validate_output(v3), v3)
        markets = ["KRW-BTC", "KRW-ETH", "KRW-LINK", "KRW-SHIB", "KRW-SOL", "KRW-SUI", "KRW-WLD", "KRW-XRP"]
        self.assertEqual([row["market"] for row in v3["candidates"]], markets)
        for row2, row3 in zip(v2["candidates"], v3["candidates"]):
            with self.subTest(market=row3["market"]):
                self.assertEqual(row2["promotion_state"], "WATCH")
                self.assertEqual(
                    row2["promotion_reason"],
                    "CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY",
                )
                self.assertEqual(row3["promotion_state"], "WATCH")
                self.assertEqual(row3["promotion_reason"],
                                 "T2_REQUIRED_UNKNOWN:T2_REGIME_PERMITS_NEW_BUYS,T2_ROTATION_MEMBERSHIP")
                t2 = row3["t2_required_conditions"]
                for name in ("T2_IDENTITY", "T2_POPULATION_MEMBERSHIP", "T2_LIQUIDITY", "T2_PRICE_DATA"):
                    self.assertEqual(t2[name]["status"], "PASS")
                self.assertEqual(row3["criteria"]["VOLUME_LIQUIDITY"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
