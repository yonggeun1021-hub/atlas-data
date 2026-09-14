#!/usr/bin/env python3
"""Per-market Crypto realtime freshness, realtime liquidity floor, stale HOLD.

User ratification CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914 (option B)
and CIO companion decision CIO-CRYPTO-REALTIME-SUBSCRIPTION-LIQUIDITY-20260914:

* thresholds stay 20s provider age / 3s transport delay;
* a non-FRESH market caps only its own action state, FRESH markets are
  evaluated normally, the aggregate realtime status is display/telemetry only;
* the realtime subscription and action set use the ratified P3-12 universe
  liquidity floor (30-day average, per CIO addendum correcting a 24h metric);
  unknown turnover is excluded; open PAPER position markets stay subscribed;
* a held position in a non-FRESH market is HOLD (no PAPER exit execution) and
  raises an alert beyond a 30 minute engineering alert budget;
* /1 packets generated before the ratification keep revalidating.

Replays use the committed natural 2026-09-13 21:12 inputs (run_008 and the
2112 decision packet's retained sources).
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CPDS = _load("per_market_test_decision", "decision/crypto_paper_decision_snapshot.py")
POLICY = CPDS.PER_MARKET
HOLD = _load("per_market_test_stale_hold", "portfolio/crypto_paper_stale_hold.py")
BRIEFING = _load("per_market_test_briefing", "briefing/crypto_funnel_briefing.py")
CAPTURE = _load("per_market_test_capture", ".github/scripts/upbit_realtime_capture.py")

V1 = CPDS.LEGACY_OUTPUT_SCHEMA_VERSION
V2 = CPDS.PER_MARKET_OUTPUT_SCHEMA_VERSION
RECORD_PATH = ROOT / POLICY.RATIFICATION_RECORD_RELATIVE_PATH
NATURAL_2112_GLOB = "evidence/crypto_paper_decision/2026-09-13/2112/*/packet.json"
NATURAL_2314_GLOB = "evidence/crypto_paper_decision/2026-09-13/2314/*/packet.json"
UNIVERSE_0913 = ROOT / "data/observations/upbit_tradeable_universe/2026-09-13/packet.json"
REPLAY_AT = "2026-09-13T21:12:24Z"  # run_008 capture end + 1s
MAJORS = ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-SUI", "KRW-XRP")
OPEN_AT_REPLAY = MAJORS + ("KRW-LINK",)  # FRESH at run_008 capture + 1s
STALE_AT_REPLAY = ("KRW-SHIB", "KRW-WLD")
ALL_EIGHT = tuple(sorted(OPEN_AT_REPLAY + STALE_AT_REPLAY))
ADDENDUM_PATH = ROOT / POLICY.ADDENDUM_RELATIVE_PATH


def _natural_packet(pattern: str) -> dict:
    paths = sorted(ROOT.glob(pattern))
    assert len(paths) == 1, paths
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _entries_from(packet: dict) -> dict:
    refs = {row["role"]: row for row in packet["source_refs"]}

    def entry(role):
        ref = refs.get(role)
        if ref is None:
            return None
        path = ROOT / ref["path"]
        return {"date": path.parent.name, "path": path, "record": json.loads(path.read_text(encoding="utf-8"))}

    universe = entry("upbit_tradeable_universe_packet")
    universe["packet"] = universe["record"]["packet"]
    market = entry("upbit_market_evidence_packet")
    market["date"] = market["record"]["snapshot_date"]
    return {
        "universe_entry": universe,
        "market_evidence_entry": market,
        "realtime_entry": entry("upbit_realtime_capture_run"),
        "leadership_entry": entry("crypto_leadership_packet"),
    }


def _replay(schema_version=V2, generated_at=REPLAY_AT, **extra) -> dict:
    packet = _natural_packet(NATURAL_2112_GLOB)
    return CPDS.build_snapshot(
        generated_at=generated_at, source_commit=packet["source_commit"],
        previous_entry=None, component_rows=None, schema_version=schema_version,
        **_entries_from(packet), **extra,
    )


@contextlib.contextmanager
def _actionable_upstream():
    """Every candidate reaches FOCUSED_REVIEW and non-realtime evidence is FRESH.

    Today's Regime UNKNOWN keeps every natural candidate at WATCH, so caps
    never bite on natural data.  Lifting the upstream state (test-only) makes
    the per-market cap observable on the exact natural realtime bytes.
    """
    real_build = CPDS.PROMOTION.build_promotion_packet

    def promoted(*args, **kwargs):
        packet = real_build(*args, **kwargs)
        for row in packet["candidates"]:
            row["promotion_state"] = "FOCUSED_REVIEW"
            row["promotion_reason"] = "TEST_ONLY_ALL_CRITERIA_PASSED"
        return packet

    def eligibility_unavailable(*_args, **_kwargs):
        raise CPDS.ELIGIBILITY.CryptoPaperBuyEligibilityError("TEST_ONLY_PROMOTION_STATE_LIFTED")

    with (
        mock.patch.object(CPDS.PROMOTION, "build_promotion_packet", side_effect=promoted),
        mock.patch.object(CPDS.ELIGIBILITY, "build_eligibility_packet", side_effect=eligibility_unavailable),
        mock.patch.object(CPDS, "_market_evidence_freshness", return_value=(CPDS.FRESH, None)),
    ):
        yield


def assert_per_market_invariants(test: unittest.TestCase, record: dict) -> None:
    rows = {row["market"]: row for row in record["candidates"]}
    test.assertEqual(sorted(rows), list(ALL_EIGHT))
    for market in ALL_EIGHT:
        test.assertEqual(rows[market]["realtime_liquidity_floor"]["status"], POLICY.INCLUDED, market)
    for market in OPEN_AT_REPLAY:
        test.assertEqual(rows[market]["realtime_freshness"]["status"], CPDS.FRESH, market)
        test.assertEqual(rows[market]["state"], "FOCUSED_REVIEW", market)
        test.assertFalse(rows[market]["freshness_capped"], market)
        test.assertIsNone(rows[market]["market_action_cap_reason"], market)
    for market in STALE_AT_REPLAY:
        test.assertEqual(rows[market]["realtime_freshness"]["status"], CPDS.STALE, market)
        test.assertEqual(rows[market]["state"], "WAIT", market)
        test.assertEqual(
            rows[market]["freshness_cap_reason"],
            f"MARKET_REALTIME_FRESHNESS_NOT_FRESH:{market}:STALE",
        )


class RatificationBindingTests(unittest.TestCase):
    def test_record_is_bound_by_exact_file_hash(self):
        self.assertEqual(
            hashlib.sha256(RECORD_PATH.read_bytes()).hexdigest(),
            "043932a4ff13e9bd683c8ff233bad3b5c8e8e2cb62045a1e756e27c7deba5ac4",
        )
        policy = POLICY.load_policy()
        self.assertEqual(policy["ratification_record"]["file_sha256"], POLICY.RATIFICATION_RECORD_SHA256)
        self.assertEqual(policy["effective_from_utc"], "2026-09-13T23:25:00Z")
        record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
        self.assertEqual(record["ratification_id"], POLICY.RATIFICATION_ID)
        self.assertIn("NOT ratified", record["not_adopted"])

    def test_cio_addendum_is_bound_alongside_the_ratification(self):
        self.assertEqual(
            hashlib.sha256(ADDENDUM_PATH.read_bytes()).hexdigest(),
            "bc009c591cd6492c55471a499502381812d54a8f12e50e7334ec04c10a954e1f",
        )
        addendum = json.loads(ADDENDUM_PATH.read_text(encoding="utf-8"))
        self.assertEqual(addendum["ratification"]["sha256"], POLICY.RATIFICATION_RECORD_SHA256)
        policy = POLICY.load_policy()
        self.assertEqual(policy["companion_decision_correction"]["file_sha256"], POLICY.ADDENDUM_SHA256)
        reference = POLICY.policy_reference(policy)
        self.assertEqual(reference["companion_decision_addendum_sha256"], POLICY.ADDENDUM_SHA256)
        self.assertEqual(reference["ratification_record_sha256"], POLICY.RATIFICATION_RECORD_SHA256)

    def test_tampered_record_or_policy_fails_closed(self):
        tmp = Path(tempfile.mkdtemp(prefix="per_market_policy_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for relative in (
            POLICY.RATIFICATION_RECORD_RELATIVE_PATH, POLICY.AMENDED_POLICY_RELATIVE_PATH,
            POLICY.UNIVERSE_POLICY_RELATIVE_PATH, POLICY.POLICY_RELATIVE_PATH,
            POLICY.ADDENDUM_RELATIVE_PATH,
        ):
            (tmp / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, tmp / relative)
        POLICY.load_policy(tmp / POLICY.POLICY_RELATIVE_PATH, root=tmp)
        record = tmp / POLICY.RATIFICATION_RECORD_RELATIVE_PATH
        record.write_bytes(record.read_bytes().replace(b"30 minutes", b"60 minutes"))
        with self.assertRaisesRegex(POLICY.CryptoRealtimePerMarketPolicyError, "RATIFICATION_RECORD_HASH_MISMATCH"):
            POLICY.load_policy(tmp / POLICY.POLICY_RELATIVE_PATH, root=tmp)
        shutil.copyfile(RECORD_PATH, record)
        addendum = tmp / POLICY.ADDENDUM_RELATIVE_PATH
        addendum.write_bytes(addendum.read_bytes().replace(b"30-day average", b"24h traded value"))
        with self.assertRaisesRegex(POLICY.CryptoRealtimePerMarketPolicyError, "ADDENDUM_HASH_MISMATCH"):
            POLICY.load_policy(tmp / POLICY.POLICY_RELATIVE_PATH, root=tmp)
        shutil.copyfile(ADDENDUM_PATH, addendum)
        policy_path = tmp / POLICY.POLICY_RELATIVE_PATH
        value = json.loads(policy_path.read_text(encoding="utf-8"))
        value["stale_held_position"]["alert_after_stale_minutes"] = 60
        unsigned = {key: item for key, item in value.items() if key != "packet_sha256"}
        value["packet_sha256"] = POLICY.payload_sha256(unsigned)
        policy_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(POLICY.CryptoRealtimePerMarketPolicyError, "POLICY_EXACT_HASH_MISMATCH"):
            POLICY.load_policy(policy_path, root=tmp)

    def test_ratified_thresholds_are_unchanged(self):
        amended = json.loads((ROOT / POLICY.AMENDED_POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(amended["max_provider_age_seconds_by_market"]["CRYPTO"], 20)
        self.assertEqual(amended["max_transport_delay_seconds_by_market"]["CRYPTO"], 3)
        self.assertEqual(amended["packet_sha256"], POLICY.AMENDED_POLICY_PACKET_SHA256)
        policy = POLICY.load_policy()
        self.assertIs(policy["amended_freshness_policy"]["thresholds_changed"], False)
        # The floor references the ratified universe policy; no number is copied.
        self.assertNotIn("min_krw", policy["liquidity_floor"])
        self.assertNotIn("5000000000", json.dumps(policy["liquidity_floor"]))
        definition = POLICY.load_universe_floor_definition()
        universe_policy = json.loads((ROOT / POLICY.UNIVERSE_POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(str(definition["min_30d_avg_krw_turnover"]), universe_policy["min_30d_avg_krw_turnover"])
        self.assertEqual(definition["turnover_lookback_finalized_days"], universe_policy["turnover_lookback_finalized_days"])
        self.assertEqual(policy["stale_held_position"]["alert_budget_kind"], "ENGINEERING_ALERT_BUDGET_NOT_POLICY")


class ThresholdBoundaryTests(unittest.TestCase):
    """The per-market view is a projection of the unchanged ratified guard."""

    def _statuses(self, *, provider_age: int, transport: int) -> dict:
        observed = dt.datetime(2026, 9, 14, 1, 0, 0, tzinfo=dt.timezone.utc)
        provider = observed - dt.timedelta(seconds=provider_age)
        received = provider + dt.timedelta(seconds=transport)
        row = {
            "asset_id": "CRYPTO.UPBIT.KRW-BTC", "market": "CRYPTO", "price": "1",
            "volume": "1", "quote_currency": "KRW", "provider_id": "UPBIT.WS.PUBLIC",
            "provider_timestamp": provider.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "received_at": received.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_ref": "wss://api.upbit.com/websocket/v1#ticker", "source_sha256": "0" * 64,
        }
        result = CPDS.REALTIME_GATE.evaluate_with_ratified_freshness_policy(
            [row], observed_at=observed,
            batch_id=f"P9_06_{observed.strftime('%Y%m%dT%H%M%SZ')}",
            contract=CPDS.REALTIME_GATE.load_contract(),
        )
        self.assertEqual(result["status"], "EVALUATED")
        return CPDS._realtime_market_rows(result["result"]["results"])["KRW-BTC"]

    def test_provider_age_boundary_is_twenty_seconds(self):
        self.assertEqual(self._statuses(provider_age=20, transport=0)["status"], CPDS.FRESH)
        stale = self._statuses(provider_age=21, transport=0)
        self.assertEqual(stale["status"], CPDS.STALE)
        self.assertEqual(stale["reasons"], ["PROVIDER_AGE_EXCEEDED"])

    def test_transport_delay_boundary_is_three_seconds(self):
        self.assertEqual(self._statuses(provider_age=5, transport=3)["status"], CPDS.FRESH)
        stale = self._statuses(provider_age=5, transport=4)
        self.assertEqual(stale["status"], CPDS.STALE)
        self.assertEqual(stale["reasons"], ["TRANSPORT_DELAY_EXCEEDED"])


class NaturalReplayTests(unittest.TestCase):
    def test_fresh_majors_are_open_while_thin_markets_are_capped_or_excluded(self):
        record = _replay()
        self.assertEqual(record["schema_version"], V2)
        rows = {row["market"]: row for row in record["candidates"]}
        for market in OPEN_AT_REPLAY:
            self.assertEqual(rows[market]["realtime_freshness"]["status"], CPDS.FRESH, market)
        for market in ALL_EIGHT:
            self.assertEqual(rows[market]["realtime_liquidity_floor"]["status"], POLICY.INCLUDED, market)
        self.assertEqual(rows["KRW-SHIB"]["realtime_freshness"]["status"], CPDS.STALE)
        self.assertEqual(rows["KRW-WLD"]["realtime_freshness"]["status"], CPDS.STALE)
        self.assertEqual(
            rows["KRW-WLD"]["realtime_freshness"]["reasons"],
            ["PROVIDER_AGE_EXCEEDED", "TRANSPORT_DELAY_EXCEEDED"],
        )
        # Aggregate stays recorded, but only for display/telemetry.
        self.assertEqual(record["freshness_status"]["realtime"], CPDS.STALE)
        block = record["realtime_per_market_freshness"]
        self.assertEqual(block["aggregate_realtime_status"], CPDS.STALE)
        self.assertEqual(block["aggregate_realtime_status_role"], "DISPLAY_TELEMETRY_ONLY_NOT_A_GLOBAL_BLOCKER")
        self.assertEqual(block["policy"]["ratification_record_sha256"], POLICY.RATIFICATION_RECORD_SHA256)

    def test_per_market_cap_on_natural_bytes_with_actionable_upstream(self):
        with _actionable_upstream():
            record = _replay()
        assert_per_market_invariants(self, record)
        block = record["realtime_per_market_freshness"]
        self.assertEqual(block["action_open_markets"], sorted(OPEN_AT_REPLAY))
        self.assertEqual(block["action_capped_markets"], sorted(STALE_AT_REPLAY))

    def test_same_inputs_under_v1_cap_every_market_globally(self):
        with _actionable_upstream():
            record = _replay(schema_version=V1)
        self.assertNotIn("realtime_per_market_freshness", record)
        for row in record["candidates"]:
            self.assertEqual(row["state"], "WAIT", row["market"])
            self.assertEqual(row["freshness_cap_reason"], "OVERALL_FRESHNESS_NOT_FRESH:STALE")

    def test_ratified_thirty_day_floor_on_natural_universe_keeps_all_eight(self):
        record = _replay()
        floor = record["realtime_per_market_freshness"]["liquidity_floor"]
        self.assertEqual(floor["metric"], "RATIFIED_UNIVERSE_POLICY_30D_AVG_FINALIZED_DAILY_KRW_TURNOVER")
        self.assertEqual(floor["definition_source"]["threshold_field"], "min_30d_avg_krw_turnover")
        self.assertEqual(floor["universe_snapshot_date"], "2026-09-13")
        self.assertEqual(floor["included_markets"], list(ALL_EIGHT))
        self.assertEqual(floor["excluded_markets"], [])
        self.assertEqual(
            POLICY.evaluate_liquidity_floor(json.loads(UNIVERSE_0913.read_text(encoding="utf-8")))["markets"]["KRW-LINK"]["krw_30d_avg_turnover"],
            "7593513888.50747663",
        )
        self.assertFalse([note for note in record["derivation_notes"] if "LIQUIDITY_FLOOR_EXCLUDED" in note])
        self.assertEqual(record["realtime_per_market_freshness"]["subscribed_outside_floor"], [])

    def test_realtime_subscription_is_floor_plus_held_positions(self):
        subscription = CAPTURE.resolve_eligible_subscription(UNIVERSE_0913, None)
        self.assertEqual(subscription["markets"], list(ALL_EIGHT))
        self.assertEqual(subscription["floor_excluded"], [])
        held_doc = json.dumps({"schema_version": "crypto_paper_held_markets/1", "markets": ["KRW-ADA", "KRW-WLD"]})
        with_held = CAPTURE.resolve_eligible_subscription(UNIVERSE_0913, held_doc)
        self.assertEqual(with_held["markets"], sorted(ALL_EIGHT + ("KRW-ADA",)))
        self.assertEqual(with_held["held_kept_subscribed"], [
            {"market": "KRW-ADA", "reason": POLICY.HELD_KEEP_SUBSCRIBED},
        ])
        for bad in ('{"markets": ["KRW-ADA"]}', json.dumps({"schema_version": "crypto_paper_held_markets/1", "markets": ["KRW-ADA", "KRW-ADA"]})):
            with self.assertRaises(CAPTURE.PER_MARKET.CryptoRealtimePerMarketPolicyError):
                CAPTURE.resolve_eligible_subscription(UNIVERSE_0913, bad)
        source = (ROOT / ".github/scripts/upbit_realtime_capture.py").read_text(encoding="utf-8")
        self.assertIn("resolve_eligible_subscription(", source)
        workflow = (ROOT / ".github/workflows/upbit-realtime-capture.yml").read_text(encoding="utf-8")
        self.assertIn("ATLAS_CRYPTO_PAPER_HELD_MARKETS_JSON: ${{ secrets.ATLAS_CRYPTO_PAPER_HELD_MARKETS_JSON }}", workflow)


def _universe_record(rows, *, policy_version=None) -> dict:
    definition = POLICY.load_universe_floor_definition()
    return {
        "snapshot_date": "2026-09-20",
        "packet": {
            "policy_version": policy_version or definition["policy_version"],
            "markets": [
                {"market": market, "state": state, "trailing_30d_krw_turnover": aggregate}
                for market, state, aggregate in rows
            ],
        },
    }


class LiquidityFloorUnitTests(unittest.TestCase):
    def test_thirty_day_average_boundary_and_fail_closed_unknown(self):
        record = _universe_record([
            ("KRW-AT", "PAPER_ELIGIBLE", "150000000000"),          # avg exactly 5B
            ("KRW-BELOW", "TRADEABLE_UNIVERSE", "149999999999.99"),
            ("KRW-NULL", "PAPER_ELIGIBLE", None),
            ("KRW-TEXT", "PAPER_ELIGIBLE", "lots"),
            ("KRW-NEG", "PAPER_ELIGIBLE", "-1"),
            ("KRW-NUM", "PAPER_ELIGIBLE", 150000000000),           # not the packet's string form
            ("KRW-POOL", "OBSERVATION_POOL", "999999999999999"),
        ])
        floor = POLICY.evaluate_liquidity_floor(record)["markets"]
        self.assertEqual(floor["KRW-AT"], {"status": POLICY.INCLUDED, "reason": None, "krw_30d_avg_turnover": "5000000000.00000000"})
        self.assertEqual(floor["KRW-BELOW"]["status"], POLICY.EXCLUDED)
        self.assertEqual(floor["KRW-BELOW"]["reason"], "TURNOVER_30D_AVG_BELOW_FLOOR")
        for market in ("KRW-NULL", "KRW-TEXT", "KRW-NEG", "KRW-NUM"):
            self.assertEqual(floor[market]["status"], POLICY.EXCLUDED, market)
            self.assertEqual(floor[market]["reason"], "TURNOVER_30D_AVG_UNKNOWN:AGGREGATE_INVALID", market)
        self.assertNotIn("KRW-POOL", floor)

    def test_universe_packet_from_another_policy_version_is_unknown(self):
        record = _universe_record([("KRW-AT", "PAPER_ELIGIBLE", "900000000000")], policy_version="upbit_tradeable_universe_policy/v0")
        row = POLICY.evaluate_liquidity_floor(record)["markets"]["KRW-AT"]
        self.assertEqual((row["status"], row["reason"]), (POLICY.EXCLUDED, "TURNOVER_30D_AVG_UNKNOWN:UNIVERSE_POLICY_VERSION_MISMATCH"))


class CapFunctionTests(unittest.TestCase):
    def test_every_capping_status_caps_only_its_own_market(self):
        for status in POLICY.CAPPING_STATUSES:
            for state in CPDS._ACTIONABLE_STATES:
                capped = POLICY.cap_state_for_market(
                    state, "OK", market="KRW-WLD", non_realtime_freshness=CPDS.FRESH,
                    market_realtime_freshness=status, liquidity_floor_status=POLICY.INCLUDED,
                    liquidity_floor_reason=None,
                )
                self.assertEqual(capped["state"], "WAIT")
                self.assertEqual(capped["cap_reason"], f"MARKET_REALTIME_FRESHNESS_NOT_FRESH:KRW-WLD:{status}")
        open_market = POLICY.cap_state_for_market(
            "PAPER_BUY_ELIGIBLE", "OK", market="KRW-BTC", non_realtime_freshness=CPDS.FRESH,
            market_realtime_freshness=CPDS.FRESH, liquidity_floor_status=POLICY.INCLUDED,
            liquidity_floor_reason=None,
        )
        self.assertEqual(open_market["state"], "PAPER_BUY_ELIGIBLE")
        self.assertIsNone(open_market["market_action_cap_reason"])

    def test_non_realtime_components_stay_global_and_watch_is_never_promoted(self):
        capped = POLICY.cap_state_for_market(
            "FOCUSED_REVIEW", "OK", market="KRW-BTC", non_realtime_freshness="UNKNOWN",
            market_realtime_freshness=CPDS.FRESH, liquidity_floor_status=POLICY.INCLUDED,
            liquidity_floor_reason=None,
        )
        self.assertEqual(capped["cap_reason"], "NON_REALTIME_FRESHNESS_NOT_FRESH:UNKNOWN")
        watch = POLICY.cap_state_for_market(
            "WATCH", "R", market="KRW-WLD", non_realtime_freshness=CPDS.FRESH,
            market_realtime_freshness=CPDS.STALE, liquidity_floor_status=POLICY.INCLUDED,
            liquidity_floor_reason=None,
        )
        self.assertEqual((watch["state"], watch["capped"]), ("WATCH", False))
        self.assertIsNotNone(watch["market_action_cap_reason"])


class MutationProofTests(unittest.TestCase):
    """Each mutation must be caught by the invariants above."""

    def _mutated_record(self, **patches):
        with contextlib.ExitStack() as stack:
            stack.enter_context(_actionable_upstream())
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(POLICY, name, value))
            return _replay()

    def test_restoring_the_global_blocker_is_detected(self):
        real_cap = POLICY.cap_state_for_market

        def global_blocker(state, reason, **kwargs):
            kwargs["market_realtime_freshness"] = CPDS.STALE  # aggregate for every market
            return real_cap(state, reason, **kwargs)

        record = self._mutated_record(cap_state_for_market=global_blocker)
        with self.assertRaises(AssertionError):
            assert_per_market_invariants(self, record)

    def test_removing_the_per_market_cap_is_detected(self):
        real_cap = POLICY.cap_state_for_market

        def no_market_cap(state, reason, **kwargs):
            kwargs["market_realtime_freshness"] = CPDS.FRESH
            return real_cap(state, reason, **kwargs)

        record = self._mutated_record(cap_state_for_market=no_market_cap)
        with self.assertRaises(AssertionError):
            assert_per_market_invariants(self, record)

    def test_including_unknown_turnover_is_detected(self):
        record = _universe_record([("KRW-AT", "PAPER_ELIGIBLE", None), ("KRW-OK", "PAPER_ELIGIBLE", "900000000000")])
        real_evaluate = POLICY.evaluate_liquidity_floor

        def include_unknown(*args, **kwargs):
            block = real_evaluate(*args, **kwargs)
            for row in block["markets"].values():
                if row["reason"] and row["reason"].startswith(POLICY.UNKNOWN_PREFIX):
                    row.update(status=POLICY.INCLUDED, reason=None)
            return block

        def unknown_is_excluded(block):
            return all(
                row["status"] == POLICY.EXCLUDED
                for row in block["markets"].values()
                if row["krw_30d_avg_turnover"] is None
            )

        self.assertTrue(unknown_is_excluded(real_evaluate(record)))
        with mock.patch.object(POLICY, "evaluate_liquidity_floor", include_unknown):
            self.assertFalse(unknown_is_excluded(POLICY.evaluate_liquidity_floor(record)))

    def test_dropping_a_held_market_from_the_subscription_is_detected(self):
        held_doc = json.dumps({"schema_version": "crypto_paper_held_markets/1", "markets": ["KRW-ADA"]})
        real_subscription = POLICY.subscription_markets

        def drop_held(*args, **kwargs):
            result = real_subscription(*args, **kwargs)
            result["markets"] = [market for market in result["markets"] if market != "KRW-ADA"]
            result["held_kept_subscribed"] = []
            return result

        CAPTURE.resolve_eligible_subscription(UNIVERSE_0913, held_doc)
        with mock.patch.object(CAPTURE.PER_MARKET, "subscription_markets", drop_held):
            with self.assertRaisesRegex(CAPTURE.RealtimeCaptureError, "HELD_POSITION_MARKET_DROPPED_FROM_SUBSCRIPTION"):
                CAPTURE.resolve_eligible_subscription(UNIVERSE_0913, held_doc)

    def test_unmutated_invariants_hold(self):
        assert_per_market_invariants(self, self._mutated_record())


class SchemaVersionAndRevalidationTests(unittest.TestCase):
    def test_default_schema_follows_the_ratified_effective_instant(self):
        before = CPDS._parse_utc("2026-09-13T23:24:59Z", "t")
        at = CPDS._parse_utc("2026-09-13T23:25:00Z", "t")
        self.assertEqual(CPDS.schema_version_for(before), V1)
        self.assertEqual(CPDS.schema_version_for(at), V2)
        self.assertEqual(_replay(schema_version=None)["schema_version"], V1)

    def test_committed_v1_packets_still_revalidate(self):
        for pattern in (NATURAL_2112_GLOB, NATURAL_2314_GLOB):
            packet = _natural_packet(pattern)
            self.assertEqual(packet["schema_version"], V1)
            self.assertEqual(CPDS.validate_output(packet), packet)

    def test_v2_packet_before_effective_instant_is_rejected(self):
        record = _replay()
        with self.assertRaisesRegex(CPDS.CryptoPaperDecisionSnapshotError, "NOT_EFFECTIVE"):
            CPDS.validate_output(record)

    def test_v2_packet_tamper_is_rejected(self):
        record = _replay()
        forged = copy.deepcopy(record)
        del forged["candidates"][0]["market_action_cap_reason"]
        with self.assertRaisesRegex(CPDS.CryptoPaperDecisionSnapshotError, "OUTPUT_SCHEMA_MISMATCH|FIELDS_MISSING|NOT_EFFECTIVE"):
            CPDS.validate_output(forged)

    def test_v2_packet_round_trips_through_full_rederivation_and_consumers(self):
        # The natural inputs predate the effective instant; only the window
        # check is lifted (test-only) so the /2 rebuild path itself is proven.
        bridge = _load("per_market_test_bridge", "decision/crypto_axis_trade_bridge.py")
        with (
            mock.patch.object(POLICY, "is_effective", return_value=True),
            mock.patch.object(bridge.DECISION.PER_MARKET, "is_effective", return_value=True),
        ):
            record = _replay()
            self.assertEqual(CPDS.validate_output(copy.deepcopy(record)), record)
            packet = bridge.build_bridge(copy.deepcopy(record))
            self.assertEqual(bridge.validate_output(packet)["source_decision_sha256"], record["payload_sha256"])
            forged = copy.deepcopy(record)
            forged["candidates"][0]["market_action_cap_reason"] = "FORGED"
            unsigned = {key: value for key, value in forged.items() if key != "payload_sha256"}
            forged["payload_sha256"] = CPDS.payload_sha256(unsigned)
            with self.assertRaisesRegex(CPDS.CryptoPaperDecisionSnapshotError, "OUTPUT_DERIVATION_MISMATCH"):
                CPDS.validate_output(forged)

    def test_v1_and_v2_of_same_inputs_never_share_a_generation(self):
        self.assertNotEqual(_replay(schema_version=V1)["generation_id"], _replay()["generation_id"])

    def test_briefing_contract_accepts_both_decision_schemas(self):
        contract = BRIEFING.load_contract()
        self.assertEqual(contract["contract_version"], "crypto_funnel_briefing_contract/2")
        self.assertEqual(contract["source_schema_versions"], [V1, V2])
        legacy = BRIEFING._expected_legacy_contract()
        self.assertEqual(legacy["source_schema_version"], V1)


def _hold_decision(realtime_by_market: dict, *, generated_at: str) -> dict:
    record = {
        "schema_version": V2,
        "generated_at": generated_at,
        "generation_id": "a" * 64,
        "candidates": [
            {"market": market, "realtime_freshness": {"status": status, "reasons": [] if status == "FRESH" else ["PROVIDER_AGE_EXCEEDED"]},
             "realtime_liquidity_floor": {"status": POLICY.INCLUDED}}
            for market, status in sorted(realtime_by_market.items())
        ],
    }
    record["payload_sha256"] = CPDS.payload_sha256(record)
    return record


class StaleHoldTests(unittest.TestCase):
    def test_natural_replay_holds_stale_market_and_evaluates_fresh_market(self):
        record = _replay()
        state = HOLD.evaluate_stale_holds(
            record, held_markets=["KRW-WLD", "KRW-BTC"], revalidate_decision=False,
        )
        rows = {row["market"]: row for row in state["markets"]}
        self.assertEqual(rows["KRW-BTC"]["exit_execution"], HOLD.EVALUATE)
        self.assertEqual(rows["KRW-WLD"]["exit_execution"], HOLD.HOLD)
        self.assertEqual(
            rows["KRW-WLD"]["hold_reason"],
            "REALTIME_STALE:KRW-WLD:PROVIDER_AGE_EXCEEDED,TRANSPORT_DELAY_EXCEEDED",
        )
        self.assertEqual(state["alerts"], [])
        self.assertEqual(HOLD.exit_observation_freshness(state, "KRW-BTC"), "FRESH")
        self.assertEqual(HOLD.exit_observation_freshness(state, "KRW-WLD"), "STALE")
        self.assertTrue(all(value is False for value in state["authority"].values()))

    def test_alert_only_beyond_thirty_minutes_and_fresh_resets(self):
        first = HOLD.evaluate_stale_holds(
            _hold_decision({"KRW-WLD": "STALE", "KRW-XRP": "FRESH"}, generated_at="2026-09-14T01:00:00Z"),
            held_markets=["KRW-WLD", "KRW-XRP"], revalidate_decision=False,
        )
        at_budget = HOLD.evaluate_stale_holds(
            _hold_decision({"KRW-WLD": "STALE", "KRW-XRP": "FRESH"}, generated_at="2026-09-14T01:30:00Z"),
            held_markets=["KRW-WLD", "KRW-XRP"], prior_state=first, revalidate_decision=False,
        )
        self.assertEqual(at_budget["markets"][0]["stale_since"], "2026-09-14T01:00:00Z")
        self.assertEqual(at_budget["markets"][0]["stale_seconds"], 1800)
        self.assertEqual(at_budget["alerts"], [])
        beyond = HOLD.evaluate_stale_holds(
            _hold_decision({"KRW-WLD": "MISSING", "KRW-XRP": "FRESH"}, generated_at="2026-09-14T01:30:01Z"),
            held_markets=["KRW-WLD", "KRW-XRP"], prior_state=at_budget, revalidate_decision=False,
        )
        self.assertEqual([row["market"] for row in beyond["alerts"]], ["KRW-WLD"])
        self.assertEqual(beyond["alerts"][0]["code"], HOLD.ALERT_CODE)
        self.assertEqual(beyond["alerts"][0]["alert_budget_kind"], "ENGINEERING_ALERT_BUDGET_NOT_POLICY")
        self.assertEqual(HOLD.exit_observation_freshness(beyond, "KRW-WLD"), "UNKNOWN")
        fresh = HOLD.evaluate_stale_holds(
            _hold_decision({"KRW-WLD": "FRESH", "KRW-XRP": "FRESH"}, generated_at="2026-09-14T02:00:00Z"),
            held_markets=["KRW-WLD", "KRW-XRP"], prior_state=beyond, revalidate_decision=False,
        )
        self.assertEqual(fresh["alerts"], [])
        restale = HOLD.evaluate_stale_holds(
            _hold_decision({"KRW-WLD": "STALE", "KRW-XRP": "FRESH"}, generated_at="2026-09-14T02:30:00Z"),
            held_markets=["KRW-WLD", "KRW-XRP"], prior_state=fresh, revalidate_decision=False,
        )
        self.assertEqual(restale["markets"][0]["stale_since"], "2026-09-14T02:30:00Z")

    def test_inputs_fail_closed(self):
        decision = _hold_decision({"KRW-WLD": "STALE"}, generated_at="2026-09-14T01:00:00Z")
        state = HOLD.evaluate_stale_holds(decision, held_markets=["KRW-WLD"], revalidate_decision=False)
        legacy = copy.deepcopy(decision)
        legacy["schema_version"] = V1
        with self.assertRaisesRegex(HOLD.CryptoPaperStaleHoldError, "NOT_PER_MARKET_SCHEMA"):
            HOLD.evaluate_stale_holds(legacy, held_markets=["KRW-WLD"], revalidate_decision=False)
        forged = copy.deepcopy(state)
        forged["markets"][0]["stale_since"] = "2026-09-13T00:00:00Z"
        later = _hold_decision({"KRW-WLD": "STALE"}, generated_at="2026-09-14T01:30:00Z")
        with self.assertRaisesRegex(HOLD.CryptoPaperStaleHoldError, "PRIOR_STATE_HASH_MISMATCH"):
            HOLD.evaluate_stale_holds(later, held_markets=["KRW-WLD"], prior_state=forged, revalidate_decision=False)
        with self.assertRaisesRegex(HOLD.CryptoPaperStaleHoldError, "PRIOR_STATE_NOT_BEFORE_DECISION"):
            HOLD.evaluate_stale_holds(decision, held_markets=["KRW-WLD"], prior_state=state, revalidate_decision=False)
        with self.assertRaisesRegex(HOLD.CryptoPaperStaleHoldError, "HELD_MARKET_DUPLICATE"):
            HOLD.evaluate_stale_holds(decision, held_markets=["KRW-WLD", "KRW-WLD"], revalidate_decision=False)
        absent = HOLD.evaluate_stale_holds(decision, held_markets=["KRW-ABC"], revalidate_decision=False)
        self.assertEqual(absent["markets"][0]["exit_execution"], HOLD.HOLD)
        self.assertEqual(absent["markets"][0]["realtime_status"], "MISSING")
        HOLD.validate_state(state, decision_packet=decision, held_markets=["KRW-WLD"], revalidate_decision=False)

    def test_module_is_offline_and_value_free(self):
        source = (ROOT / "portfolio/crypto_paper_stale_hold.py").read_text(encoding="utf-8")
        policy_source = (ROOT / "realtime/crypto_realtime_per_market_policy.py").read_text(encoding="utf-8")
        for text in (source, policy_source):
            for token in ("import requests", "import urllib", "import socket", "websockets", "api.upbit.com", "/v1/orders"):
                self.assertNotIn(token, text)
        for token in ("quantity", "price", "pnl"):
            self.assertNotIn(f'"{token}"', source)


if __name__ == "__main__":
    unittest.main()
