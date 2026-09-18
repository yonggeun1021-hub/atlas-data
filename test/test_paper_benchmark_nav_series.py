#!/usr/bin/env python3
"""Benchmark ("simply bought and held") NAV series regression.

Offline only. Every ledger in this suite is built by the P10-11 simulator's own
builders (``create_ledger`` / ``submit_order`` / ``match_order``) so no hash
chain is hand-written, and every price is a fixture. No network, no credential,
no evidence directory outside a temporary one.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
from decimal import Decimal
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "validation" / "paper_benchmark_nav_series.py"
SPEC = importlib.util.spec_from_file_location("paper_benchmark_nav_series", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

SIM = MODULE.SIMULATOR
POLICY = MODULE.load_policy()
PARAMS = MODULE.resolve_market_parameters("CRYPTO", policy=POLICY)

NAV0 = "200000000"
FILL_AT = "2026-10-08T07:05:00Z"
ANCHOR_PRICE = "160000000"
FEE_RATE = "0.0005"
# A process clock far past every fixture timestamp, so the wall-clock ceiling
# is exercised deliberately rather than depending on when the suite is run.
FAR_FUTURE = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────

def _intent(*, order_id="PAPER.ORDER.1", market="KRW-SOL", quantity="100",
            fee_rate=FEE_RATE, side="BUY"):
    return SIM.build_intent(
        order_id=order_id,
        idempotency_key=f"PAPER.SUBMIT.{order_id}",
        market=market,
        side=side,
        order_type="MARKET",
        quantity=quantity,
        limit_price=None,
        fee_rate=fee_rate,
        queue_fraction="1",
        submitted_at="2026-10-08T07:00:00Z",
        expires_at="2026-10-08T08:00:00Z",
        market_regime_status="PASS",
        source_plan_ref=f"test://plan/{order_id}",
        source_plan_sha256="a" * 64,
        source_evidence_ref=f"test://evidence/{market}",
        source_evidence_sha256="b" * 64,
    )


def _snapshot(*, market="KRW-SOL", captured_at="2026-10-08T07:04:30Z",
              snapshot_id="SNAPSHOT.1"):
    return SIM.build_snapshot(
        snapshot_id=snapshot_id,
        market=market,
        captured_at=captured_at,
        freshness_status="FRESH",
        ask_levels=[{"price": "300000", "quantity": "500"}],
        bid_levels=[{"price": "299000", "quantity": "500"}],
        source_ref=f"test://orderbook/{snapshot_id}",
        source_sha256="c" * 64,
    )


def filled_ledger(*, fill_at=FILL_AT, side="BUY", fee_rate=FEE_RATE,
                  ledger_id="PAPER.UPBIT.KRW.ATLAS_SERVER.20260918",
                  initial_cash=NAV0):
    base = SIM.create_ledger(
        ledger_id=ledger_id,
        initial_cash=initial_cash,
        opened_at="2026-09-18T07:00:00Z",
        idempotency_key="PAPER.ACCOUNT.OPEN",
    )
    submitted = SIM.submit_order(base, _intent(fee_rate=fee_rate, side=side))
    return SIM.match_order(
        submitted,
        order_id="PAPER.ORDER.1",
        snapshot=_snapshot(),
        event_at=fill_at,
        idempotency_key="PAPER.MATCH.1",
    )


def resign(ledger):
    """Rebuild the hash chain of a hand-edited ledger.

    The chain catches a naive edit first, so a guard that lives *after*
    validation can only be exercised on a ledger that is internally
    consistent again. Uses the simulator's own signing so no hash is
    hand-written here either.
    """
    events = []
    prior = None
    for index, raw in enumerate(ledger["events"]):
        event = copy.deepcopy(raw)
        event.pop("event_sha256", None)
        event["sequence"] = index + 1
        event["previous_event_sha256"] = prior
        event["event_sha256"] = SIM.payload_sha256(event)
        prior = event["event_sha256"]
        events.append(event)
    return SIM._ledger_packet(ledger["ledger_id"], events, SIM.load_contract())


def publish(ledger, store):
    """Publish the ledger into its own append-only snapshot store."""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    SIM.publish_ledger_snapshot(store, ledger)
    return store


def genesis_pin(ledger):
    """The pin recorded once when the account was opened, before any fill."""
    genesis = ledger["events"][0]
    return {
        "ledger_id": ledger["ledger_id"],
        "genesis_event_sha256": genesis["event_sha256"],
        "genesis_event_at": genesis["event_at"],
        "initial_cash": genesis["payload"]["initial_cash"],
    }


def authenticated(ledger, store, *, pin=None):
    publish(ledger, store)
    return MODULE.authenticate_ledger(
        Path(store), ledger["ledger_id"],
        genesis_pin=genesis_pin(ledger) if pin is None else pin,
    )


def price_observation(*, market="KRW-BTC", price=ANCHOR_PRICE,
                      observed_at="2026-10-08T07:04:00Z",
                      available_at="2026-10-08T07:04:10Z",
                      source_ref="test://upbit/realtime/KRW-BTC"):
    return {
        "market": market,
        "price": price,
        "observed_at": observed_at,
        "available_at": available_at,
        "source_ref": source_ref,
        "source_sha256": "d" * 64,
    }


def witness(*, observed_at=None, available_at=None, market="KRW-BTC",
            price="160500000", recorded_at="2026-10-08T07:10:00Z"):
    """A post-fill observation that bounds the claimed record time."""
    return price_observation(
        market=market,
        price=price,
        observed_at=observed_at or FILL_AT,
        available_at=available_at or recorded_at,
        source_ref="test://upbit/realtime/witness",
    )


def market_state(*, state="RISK_ON", multiplier=None, observed_at=None,
                 available_at=None):
    """A market-state observation in read_market_state's documented contract.

    No path is bound in the module (RATIFICATION_MARKET_STATE_SOURCE_BINDING),
    so the fixture supplies the shape the real wiring will have to hand over.
    """
    table = PARAMS["state_multipliers"]
    return {
        "state": state,
        "multiplier": table.get(state, "1.00") if multiplier is None else multiplier,
        "observed_at": observed_at or "2026-10-08T07:00:00Z",
        "available_at": available_at or "2026-10-08T07:02:00Z",
        "source_ref": "test://market-state/crypto",
        "source_sha256": "e" * 64,
        "source_schema_version": "test_market_state/1",
    }


_DEFAULT = object()


def anchor(*, recorded_at_utc="2026-10-08T07:10:00Z", observations=None,
           ledger=None, clock_witness=None, pin=None, store=None, now=None,
           state=_DEFAULT):
    """Derive an anchor over an authenticated ledger. Uses its own temp store
    unless one is supplied, so each call is independent."""
    value = filled_ledger() if ledger is None else ledger
    if store is None:
        holder = tempfile.TemporaryDirectory()
        store = Path(holder.name)
    else:
        holder = None
    try:
        verified = authenticated(value, store, pin=pin)
        return MODULE.derive_anchor(
            market="CRYPTO",
            verified_ledger=verified,
            price_observations=observations if observations is not None
            else [price_observation()],
            recorded_at_utc=recorded_at_utc,
            clock_witness=clock_witness if clock_witness is not None
            else witness(recorded_at=recorded_at_utc),
            market_state_observation=(
                market_state() if state is _DEFAULT else state
            ),
            policy=POLICY,
            params=PARAMS,
            now=now if now is not None else FAR_FUTURE,
        )
    finally:
        if holder is not None:
            holder.cleanup()


def nav_rows(values, *, start_day=8, hour="07:05:00"):
    rows = []
    for index, value in enumerate(values):
        stamp = f"2026-10-{start_day + index:02d}T{hour}Z"
        rows.append({
            "observed_at": stamp,
            "available_at": stamp,
            "total_nav": value,
        })
    return rows


def mark_rows(prices, *, start_day=8, hour="07:05:00", market="KRW-BTC"):
    rows = []
    for index, price in enumerate(prices):
        stamp = f"2026-10-{start_day + index:02d}T{hour}Z"
        rows.append({
            "observed_at": stamp,
            "available_at": stamp,
            "market": market,
            "price": price,
        })
    return rows


def recorded(root, value=None):
    value = anchor() if value is None else value
    MODULE.record_anchor(Path(root), value)
    return MODULE.load_anchor(
        Path(root), value["market"], value["ledger_id"],
        trusted_anchor_sha256=value["packet_sha256"],
    )


def series(root, *, navs=("200000000", "210000000"),
           prices=(ANCHOR_PRICE, "176000000"), generated_at="2026-10-09T08:00:00Z",
           value=None):
    verified = recorded(root, value)
    rows = nav_rows(list(navs))
    return MODULE.build_series(
        verified,
        series_id="BENCHMARK.CRYPTO.TEST",
        generated_at_utc=generated_at,
        paper_nav_series=rows,
        paper_nav_series_sha256=MODULE.payload_sha256(rows),
        benchmark_marks=mark_rows(list(prices)),
        policy=POLICY,
        params=PARAMS,
    )


# ─────────────────────────────────────────────────────────────────────────
# Policy, authority, and sourced numbers
# ─────────────────────────────────────────────────────────────────────────

class PolicyTest(unittest.TestCase):
    def test_every_authority_field_except_evidence_is_false(self):
        for key, value in MODULE.AUTHORITY.items():
            if key in ("evidence_only", "benchmark_record_only"):
                self.assertTrue(value, key)
            else:
                self.assertFalse(value, key)
        self.assertEqual(POLICY["authority"], MODULE.AUTHORITY)

    def test_policy_declares_the_two_stop_rules_it_unblocks(self):
        ids = [row["id"] for row in POLICY["stop_rules_unblocked"]]
        self.assertEqual(ids, ["CHECKPOINT_B_STOP_RULE_1", "CHECKPOINT_B_STOP_RULE_5"])

    def test_policy_states_the_rejected_alternative_and_needs_ratification(self):
        definition = POLICY["benchmark_definition"]
        self.assertIn("EQUAL_WEIGHT_SAME_CANDIDATES",
                      definition["rejected_alternative"]["alternative"])
        self.assertEqual(definition["variant_binding"].split()[0], "RATIFIED")
        ids = {row["id"] for row in POLICY["ratification_required"]}
        self.assertIn("RATIFICATION_BENCHMARK_DEFINITION", ids)
        self.assertIn("RATIFICATION_VARIANT_BINDING", ids)

    def test_numbers_come_from_the_registry_and_the_pinned_ratified_policy(self):
        registry = json.loads((ROOT / "config" / "rule_registry_v1.json").read_text())
        allocation = MODULE._registry_parameter(
            registry, "RULE.ALLOCATION.V2", "base_allocation_all_markets_risk_on"
        )
        self.assertEqual(
            PARAMS["base_allocation_fraction"], Decimal(allocation["CRYPTO"])
        )
        gaps = MODULE._registry_parameter(
            registry, "RULE.ROTATION.MAX_OBSERVATION_GAP.V1", "max_gap_days"
        )
        self.assertEqual(
            PARAMS["max_sample_gap_seconds"], gaps["CRYPTO"] * MODULE.SECONDS_PER_DAY
        )
        ratified = json.loads(
            (ROOT / "config" / "upbit_market_evidence_policy_ratified.json").read_text()
        )
        self.assertEqual(
            PARAMS["anchor_price_max_staleness_seconds"],
            ratified["max_orderbook_staleness_seconds"],
        )

    def test_kr_and_us_refuse_until_the_instrument_is_ratified(self):
        for market in ("KR", "US"):
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, f"MARKET_NOT_DEFINED:{market}"
            ):
                MODULE.resolve_market_parameters(market, policy=POLICY)

    def test_a_tampered_pinned_source_sha_refuses(self):
        broken = copy.deepcopy(POLICY)
        broken["source_documents"]["upbit_market_evidence_policy_ratified"][
            "file_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "POLICY_SOURCE_FILE_SHA_MISMATCH"
        ):
            MODULE.resolve_market_parameters("CRYPTO", policy=broken)


# ─────────────────────────────────────────────────────────────────────────
# Anchor derivation
# ─────────────────────────────────────────────────────────────────────────

class AnchorDerivationTest(unittest.TestCase):
    def test_anchor_is_derived_from_the_first_fill_and_is_arithmetically_exact(self):
        value = anchor()
        self.assertEqual(value["anchor_utc"], FILL_AT)
        self.assertEqual(
            value["anchor_basis"], "FIRST_FILL_APPLIED_EVENT_AT_DERIVED_NOT_SUPPLIED"
        )
        self.assertEqual(value["benchmark_asset"], "KRW-BTC")
        self.assertEqual(value["nav0_krw"], NAV0)
        # 200,000,000 x the ratified crypto base share 0.15
        flat = value["notionals"]["FLAT_BASE_SHARE"]
        self.assertEqual(flat["notional_krw"], "30000000")
        self.assertEqual(value["cost_model"]["fee_rate"], FEE_RATE)
        # The fill consumed one ask level at 300000 versus a best price of
        # 300000, so realized slippage is exactly 0 and the effective entry
        # price is the anchor price itself.
        self.assertEqual(value["cost_model"]["entry_slippage_bps"], "0")
        self.assertEqual(value["effective_entry_price"], ANCHOR_PRICE)
        units = Decimal(flat["units"])
        expected_units = (
            Decimal("30000000") / (Decimal(ANCHOR_PRICE) * (Decimal(1) + Decimal(FEE_RATE)))
        )
        self.assertLessEqual(units, expected_units)
        self.assertLessEqual(Decimal(flat["anchor_cash_spent_krw"]), Decimal("30000000"))
        self.assertEqual(
            Decimal(flat["anchor_cash_spent_krw"])
            + Decimal(flat["anchor_residual_cash_krw"]),
            Decimal("30000000"),
        )
        self.assertEqual(value["packet_sha256"], MODULE.payload_sha256(
            {k: v for k, v in value.items() if k != "packet_sha256"}
        ))

    def test_a_supplied_anchor_time_is_only_ever_checked_never_used(self):
        self.assertEqual(anchor()["anchor_utc"], FILL_AT)
        with tempfile.TemporaryDirectory() as tmp:
            verified = authenticated(filled_ledger(), Path(tmp))
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError,
                "ANCHOR_UTC_NOT_DERIVED_FROM_FIRST_FILL",
            ):
                MODULE.derive_anchor(
                    market="CRYPTO",
                    verified_ledger=verified,
                    price_observations=[price_observation()],
                    recorded_at_utc="2026-10-08T07:10:00Z",
                    clock_witness=witness(),
                    market_state_observation=market_state(),
                    anchor_utc="2026-10-01T07:05:00Z",
                    policy=POLICY,
                    params=PARAMS,
                    now=FAR_FUTURE,
                )

    def test_no_fill_yet_refuses_rather_than_anchoring_on_the_order(self):
        base = SIM.create_ledger(
            ledger_id="PAPER.UPBIT.KRW.ATLAS_SERVER.20260918",
            initial_cash=NAV0,
            opened_at="2026-09-18T07:00:00Z",
            idempotency_key="PAPER.ACCOUNT.OPEN",
        )
        submitted = SIM.submit_order(base, _intent())
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_NO_FILL_YET"
        ):
            anchor(ledger=submitted)

    def test_price_observed_after_the_fill_can_never_anchor(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_PRICE_UNAVAILABLE"
        ):
            anchor(observations=[price_observation(
                observed_at="2026-10-08T07:05:01Z", available_at="2026-10-08T07:05:10Z"
            )])

    def test_a_price_older_than_the_ratified_staleness_window_refuses(self):
        stale = PARAMS["anchor_price_max_staleness_seconds"] + 60
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_PRICE_UNAVAILABLE"
        ):
            anchor(observations=[price_observation(
                observed_at="2026-10-08T06:55:00Z", available_at="2026-10-08T06:55:10Z"
            )])
        self.assertGreater(stale, 0)

    def test_two_eligible_observations_refuse_rather_than_pick_the_later_one(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_PRICE_AMBIGUOUS"
        ):
            anchor(observations=[
                price_observation(observed_at="2026-10-08T07:04:00Z"),
                price_observation(
                    observed_at="2026-10-08T07:04:30Z", price="161000000",
                    available_at="2026-10-08T07:04:40Z",
                ),
            ])

    def test_an_anchor_recorded_after_one_decision_cycle_refuses(self):
        # Inside the cycle: fine.
        self.assertEqual(anchor(recorded_at_utc="2026-10-09T07:05:00Z")["record_lag_seconds"],
                         MODULE.DAILY_DECISION_CYCLE_SECONDS)
        # A second later, the anchor can no longer be created at all -- which
        # is exactly what stops it being chosen weeks later.
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_RECORD_LAG_EXCEEDED"
        ):
            anchor(recorded_at_utc="2026-10-09T07:05:01Z")
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_RECORD_LAG_EXCEEDED"
        ):
            anchor(recorded_at_utc="2026-11-06T07:05:00Z")

    def test_recording_before_the_fill_refuses(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_RECORDED_BEFORE_FILL"
        ):
            anchor(recorded_at_utc="2026-10-08T07:04:59Z")

    def test_editing_the_intent_fee_rate_breaks_the_hash_chain_first(self):
        broken = copy.deepcopy(filled_ledger())
        for event in broken["events"]:
            if event["event_type"] == "ORDER_SUBMITTED":
                event["payload"]["intent"]["fee_rate"] = "0.002"
        with self.assertRaises(SIM.CryptoPaperSimulatorError):
            anchor(ledger=broken)

    def test_the_fee_rate_cannot_disagree_with_the_fill_that_charged_it(self):
        # The fee rate is not a constant in this repo (the simulator contract's
        # cost_model is CALLER_SUPPLIED..._NO_DEFAULTS), so it is only usable if
        # the fill itself proves it. Swapping in an intent that claims 0.002
        # while the fill charged 0.0005 survives a rebuilt hash chain, but the
        # simulator's replay re-derives the match and refuses -- so no ledger
        # can even reach this module carrying an unproven fee rate. The
        # reconciliation guard in derive_anchor is defence in depth over that.
        swapped = copy.deepcopy(filled_ledger(fee_rate="0.0005"))
        for event in swapped["events"]:
            if event["event_type"] == "ORDER_SUBMITTED":
                event["payload"]["intent"] = _intent(fee_rate="0.002")
        with self.assertRaisesRegex(
            SIM.CryptoPaperSimulatorError, "MATCH_DERIVATION_MISMATCH"
        ):
            anchor(ledger=resign(swapped))
        value = anchor()
        fill = value["first_fill"]
        self.assertEqual(
            Decimal(fill["fee_amount"]),
            Decimal(fill["gross_value"]) * Decimal(value["cost_model"]["fee_rate"]),
        )

    def test_a_ledger_whose_genesis_is_not_the_ratified_nav0_refuses(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "LEDGER_GENESIS_NOT_RATIFIED_NAV0"
        ):
            anchor(ledger=filled_ledger(initial_cash="10000000"))

    def test_a_malformed_price_observation_refuses_instead_of_being_skipped(self):
        bad = price_observation()
        bad.pop("source_sha256")
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "PRICE_OBSERVATION_FIELDS_MISMATCH"
        ):
            anchor(observations=[price_observation(), bad])


# ─────────────────────────────────────────────────────────────────────────
# Anchor immutability on disk
# ─────────────────────────────────────────────────────────────────────────

class AnchorImmutabilityTest(unittest.TestCase):
    def test_recording_the_same_anchor_twice_is_an_idempotent_no_op(self):
        value = anchor()
        with tempfile.TemporaryDirectory() as tmp:
            first = MODULE.record_anchor(Path(tmp), value)
            second = MODULE.record_anchor(Path(tmp), copy.deepcopy(value))
            self.assertEqual(first, second)

    def test_a_second_different_anchor_for_the_same_account_refuses(self):
        first = anchor()
        later = anchor(
            observations=[price_observation(
                price="170000000", observed_at="2026-10-08T07:04:20Z",
                available_at="2026-10-08T07:04:25Z",
            )],
        )
        self.assertNotEqual(first["packet_sha256"], later["packet_sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            MODULE.record_anchor(Path(tmp), first)
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError,
                "ANCHOR_ALREADY_BOUND_IMMUTABLE",
            ):
                MODULE.record_anchor(Path(tmp), later)

    def test_load_requires_a_separately_trusted_sha_not_the_files_own(self):
        value = anchor()
        with tempfile.TemporaryDirectory() as tmp:
            MODULE.record_anchor(Path(tmp), value)
            loaded = MODULE.load_anchor(
                Path(tmp), "CRYPTO", value["ledger_id"],
                trusted_anchor_sha256=value["packet_sha256"],
            )
            self.assertEqual(loaded.sha256, value["packet_sha256"])
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_BINDING_SHA_MISMATCH"
            ):
                MODULE.load_anchor(
                    Path(tmp), "CRYPTO", value["ledger_id"],
                    trusted_anchor_sha256="f" * 64,
                )

    def test_an_edited_anchor_file_refuses_on_reload(self):
        value = anchor()
        with tempfile.TemporaryDirectory() as tmp:
            path = MODULE.record_anchor(Path(tmp), value)
            tampered = json.loads(path.read_text())
            tampered["anchor_price"] = "100000000"
            path.write_text(MODULE.canonical_json(tampered) + "\n")
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_SHA_MISMATCH"
            ):
                MODULE.load_anchor(
                    Path(tmp), "CRYPTO", value["ledger_id"],
                    trusted_anchor_sha256=value["packet_sha256"],
                )

    def test_a_series_cannot_be_built_from_an_unverified_anchor_dict(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_NOT_VERIFIED"
        ):
            MODULE.build_series(
                anchor(), series_id="BENCHMARK.CRYPTO.TEST",
                generated_at_utc="2026-10-09T08:00:00Z",
                paper_nav_series=nav_rows([NAV0]),
                paper_nav_series_sha256=MODULE.payload_sha256(nav_rows([NAV0])),
                benchmark_marks=mark_rows([ANCHOR_PRICE]),
                policy=POLICY, params=PARAMS,
            )


# ─────────────────────────────────────────────────────────────────────────
# Series arithmetic and the stop-rule inputs
# ─────────────────────────────────────────────────────────────────────────

class SeriesTest(unittest.TestCase):
    def test_both_variants_are_emitted_and_neither_is_a_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = series(tmp)
        self.assertEqual(set(record["variants"]), set(MODULE.SERIES_NAMES))
        for name in MODULE.SERIES_NAMES:
            self.assertEqual(
                record["comparison"][name]["verdict"],
                "NOT_EMITTED_RATIFICATION_REQUIRED",
            )
        self.assertEqual(record["sample_count"], 2)
        self.assertEqual(record["observation_start_utc"], "2026-10-08T07:05:00Z")

    def test_holding_a_ten_percent_rise_shows_up_in_both_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = series(tmp, navs=("200000000", "200000000"),
                            prices=(ANCHOR_PRICE, "176000000"))
        asset = record["variants"]["FLAT_BASE_SHARE__ASSET_ONLY"]
        exposure = record["variants"]["FLAT_BASE_SHARE__EXPOSURE_MATCHED"]
        # BTC +10%: the sleeve is up ~10% less the entry cost drag.
        self.assertLess(Decimal(asset["final_return_fraction"]), Decimal("0.1"))
        self.assertGreater(Decimal(asset["final_return_fraction"]), Decimal("0.099"))
        # 15% of NAV0 in BTC, +10% on BTC -> ~+1.5% on the whole NAV.
        self.assertLess(Decimal(exposure["final_return_fraction"]), Decimal("0.015"))
        self.assertGreater(Decimal(exposure["final_return_fraction"]), Decimal("0.0149"))
        # The account did nothing, so rule 1's input is negative on both.
        for name in MODULE.SERIES_NAMES:
            rule1 = record["comparison"][name]["stop_rule_1_inputs"]
            self.assertEqual(rule1["paper_final_return_fraction"], "0")
            self.assertLess(
                Decimal(rule1["paper_minus_benchmark_return_fraction"]), Decimal("0")
            )

    def test_drawdown_is_observed_only_and_feeds_rule_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = series(
                tmp,
                navs=("200000000", "190000000", "195000000"),
                prices=(ANCHOR_PRICE, "128000000", "144000000"),
                generated_at="2026-10-11T08:00:00Z",
            )
        asset = record["variants"]["FLAT_BASE_SHARE__ASSET_ONLY"]
        # BTC -20% then back to -10%: trough at sample 2, peak at sample 1.
        self.assertEqual(asset["max_drawdown_peak_at"], "2026-10-08T07:05:00Z")
        self.assertEqual(asset["max_drawdown_trough_at"], "2026-10-09T07:05:00Z")
        self.assertLess(Decimal(asset["max_drawdown_fraction"]), Decimal("-0.19"))
        paper = record["paper_account"]
        self.assertEqual(paper["max_drawdown_fraction"], "-0.05")
        rule5 = record["comparison"]["FLAT_BASE_SHARE__ASSET_ONLY"]["stop_rule_5_inputs"]
        self.assertEqual(rule5["paper_max_drawdown_fraction"], "-0.05")
        # The account fell less than holding, so the difference is positive.
        self.assertGreater(
            Decimal(rule5["paper_minus_benchmark_max_drawdown_fraction"]), Decimal("0")
        )

    def test_the_exposure_matched_rows_drop_straight_into_the_existing_metric(self):
        counterfactual_source = ROOT / "validation" / "crypto_paper_counterfactual.py"
        spec = importlib.util.spec_from_file_location(
            "crypto_paper_counterfactual_for_benchmark", counterfactual_source
        )
        counterfactual = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(counterfactual)
        with tempfile.TemporaryDirectory() as tmp:
            record = series(
                tmp,
                navs=("200000000", "190000000", "195000000"),
                prices=(ANCHOR_PRICE, "128000000", "144000000"),
                generated_at="2026-10-11T08:00:00Z",
            )
        rows = record["nav_series_for_counterfactual"]
        self.assertEqual(
            {key for row in rows for key in row},
            {"observed_at", "available_at", "total_nav"},
        )
        reused = counterfactual._drawdown_metrics(rows)
        expected = Decimal(
            record["variants"]["FLAT_BASE_SHARE__EXPOSURE_MATCHED"]["max_drawdown_fraction"]
        ) * Decimal("100")
        self.assertEqual(
            Decimal(reused["max_drawdown_pct"]).quantize(Decimal("0.0001")),
            expected.quantize(Decimal("0.0001")),
        )

    def test_a_missing_mark_at_a_sample_refuses_instead_of_interpolating(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = nav_rows(["200000000", "210000000", "205000000"])
            marks = mark_rows([ANCHOR_PRICE, "176000000"])
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError,
                "BENCHMARK_MARK_MISSING_AT_SAMPLE:2026-10-10T07:05:00Z",
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-11T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256=MODULE.payload_sha256(rows),
                    benchmark_marks=marks, policy=POLICY, params=PARAMS,
                )

    def test_an_off_grid_mark_refuses_so_the_grid_cannot_be_swapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = nav_rows(["200000000", "210000000"])
            marks = mark_rows([ANCHOR_PRICE, "176000000"])
            marks.append({
                "observed_at": "2026-10-09T12:00:00Z",
                "available_at": "2026-10-09T12:00:00Z",
                "market": "KRW-BTC",
                "price": "200000000",
            })
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "BENCHMARK_MARK_OFF_GRID"
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-11T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256=MODULE.payload_sha256(rows),
                    benchmark_marks=marks, policy=POLICY, params=PARAMS,
                )

    def test_a_gap_wider_than_the_market_allows_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = [
                {"observed_at": "2026-10-08T07:05:00Z",
                 "available_at": "2026-10-08T07:05:00Z", "total_nav": "200000000"},
                {"observed_at": "2026-10-11T07:05:00Z",
                 "available_at": "2026-10-11T07:05:00Z", "total_nav": "210000000"},
            ]
            marks = [
                {"observed_at": "2026-10-08T07:05:00Z",
                 "available_at": "2026-10-08T07:05:00Z", "market": "KRW-BTC",
                 "price": ANCHOR_PRICE},
                {"observed_at": "2026-10-11T07:05:00Z",
                 "available_at": "2026-10-11T07:05:00Z", "market": "KRW-BTC",
                 "price": "176000000"},
            ]
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "PAPER_NAV_SAMPLE_GAP_EXCEEDED"
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-12T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256=MODULE.payload_sha256(rows),
                    benchmark_marks=marks, policy=POLICY, params=PARAMS,
                )

    def test_a_null_nav_is_never_valued_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = nav_rows(["200000000", "210000000"])
            rows[1]["total_nav"] = None
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "PAPER_NAV_SERIES_VALUE_UNKNOWN"
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-11T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256=MODULE.payload_sha256(rows),
                    benchmark_marks=mark_rows([ANCHOR_PRICE, "176000000"]),
                    policy=POLICY, params=PARAMS,
                )

    def test_a_rewritten_nav_series_hash_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = nav_rows(["200000000", "210000000"])
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "PAPER_NAV_SERIES_SHA_MISMATCH"
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-11T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256="e" * 64,
                    benchmark_marks=mark_rows([ANCHOR_PRICE, "176000000"]),
                    policy=POLICY, params=PARAMS,
                )

    def test_a_sample_before_the_anchor_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = nav_rows(["200000000", "210000000"], start_day=7)
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "PAPER_NAV_SAMPLE_BEFORE_ANCHOR"
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-11T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256=MODULE.payload_sha256(rows),
                    benchmark_marks=mark_rows([ANCHOR_PRICE, "176000000"], start_day=7),
                    policy=POLICY, params=PARAMS,
                )

    def test_a_mark_available_after_the_query_time_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = nav_rows(["200000000", "210000000"])
            marks = mark_rows([ANCHOR_PRICE, "176000000"])
            marks[1]["available_at"] = "2026-10-09T09:00:00Z"
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "BENCHMARK_MARK_SERIES_LOOKAHEAD"
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-09T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256=MODULE.payload_sha256(rows),
                    benchmark_marks=marks,
                    policy=POLICY, params=PARAMS,
                )

    def test_a_mark_for_another_market_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = recorded(tmp)
            rows = nav_rows(["200000000", "210000000"])
            marks = mark_rows([ANCHOR_PRICE, "176000000"])
            marks[1]["market"] = "KRW-ETH"
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "BENCHMARK_MARK_MARKET_MISMATCH"
            ):
                MODULE.build_series(
                    verified, series_id="BENCHMARK.CRYPTO.TEST",
                    generated_at_utc="2026-10-11T08:00:00Z",
                    paper_nav_series=rows,
                    paper_nav_series_sha256=MODULE.payload_sha256(rows),
                    benchmark_marks=marks, policy=POLICY, params=PARAMS,
                )

    def test_the_series_is_deterministic_and_self_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = anchor()
            first = series(tmp, value=value)
            second = series(tmp, value=copy.deepcopy(value))
        self.assertEqual(first, second)
        self.assertEqual(first["packet_sha256"], MODULE.payload_sha256(
            {k: v for k, v in first.items() if k != "packet_sha256"}
        ))
        self.assertEqual(first["authority"], MODULE.AUTHORITY)
        self.assertEqual(
            first["nav_series_for_counterfactual_variant"],
            "FLAT_BASE_SHARE__EXPOSURE_MATCHED"
        )


# ─────────────────────────────────────────────────────────────────────────
# CLI (dispatch only -- no workflow invokes this)
# ─────────────────────────────────────────────────────────────────────────

class CliTest(unittest.TestCase):
    def test_anchor_then_series_round_trip_through_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = root / "anchor_request.json"
            ledger = filled_ledger()
            store = publish(ledger, root / "ledger_snapshots")
            request.write_text(json.dumps({
                "market": "CRYPTO",
                "ledger_snapshot_root": str(store),
                "ledger_id": ledger["ledger_id"],
                "ledger_genesis_pin": genesis_pin(ledger),
                "price_observations": [price_observation()],
                "clock_witness": witness(),
                "market_state_observation": market_state(),
                "recorded_at_utc": "2026-10-08T07:10:00Z",
            }, ensure_ascii=False))
            anchor_store = root / "store"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    MODULE.main([
                        "anchor", "--request", str(request), "--root", str(anchor_store),
                    ]),
                    0,
                )
            value = anchor()
            rows = nav_rows(["200000000", "210000000"])
            series_request = root / "series_request.json"
            series_request.write_text(json.dumps({
                "market": "CRYPTO",
                "ledger_id": value["ledger_id"],
                "trusted_anchor_sha256": value["packet_sha256"],
                "series_id": "BENCHMARK.CRYPTO.CLI",
                "generated_at_utc": "2026-10-09T08:00:00Z",
                "paper_nav_series": rows,
                "paper_nav_series_sha256": MODULE.payload_sha256(rows),
                "benchmark_marks": mark_rows([ANCHOR_PRICE, "176000000"]),
            }, ensure_ascii=False))
            out = root / "out" / "series.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    MODULE.main([
                        "series", "--request", str(series_request),
                        "--root", str(anchor_store), "--out", str(out),
                    ]),
                    0,
                )
            written = json.loads(out.read_text())
            self.assertEqual(written["schema_version"], "paper_benchmark_nav_series/1")
            self.assertEqual(written["series_id"], "BENCHMARK.CRYPTO.CLI")

    # ─────────────────────────────────────────────────────────────────────
    # Replaces the former test_this_module_is_wired_into_no_workflow.
    #
    # That guard asserted the module had no caller at all, which stopped being
    # true once the private crypto PAPER runtime began deriving the anchor at
    # its first fill (private_evidence/crypto_paper_benchmark_anchor.py). It is
    # replaced rather than deleted, by the three properties that actually have
    # to hold about the intended wiring:
    #
    #   1. no *public* workflow or schedule invokes this module -- the only
    #      caller is the private runtime, which is not in this repo;
    #   2. it cannot run before a fill exists; and
    #   3. it binds once per account and a second, different anchor refuses.
    #
    # Property "called exactly once, at the first fill" has a caller-side half
    # that cannot be tested from this repo. That half is asserted in the
    # private repo by test/test_crypto_paper_benchmark_anchor.py.
    # ─────────────────────────────────────────────────────────────────────

    def test_no_public_workflow_or_schedule_invokes_this_module(self):
        """The only intended caller is the private runtime at the first fill."""
        workflows = ROOT / ".github" / "workflows"
        for path in sorted(workflows.glob("*.yml")):
            self.assertNotIn("paper_benchmark_nav_series", path.read_text(), str(path))
        # And it never runs on its own: the CLI is dispatch-only, so an
        # accidental bare invocation records nothing.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                MODULE.main([])

    def test_the_anchor_cannot_be_derived_before_a_fill_exists(self):
        """Property 2: no fill, no anchor -- there is nothing to anchor to."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unfilled = SIM.create_ledger(
                ledger_id="PAPER.UPBIT.KRW.NOFILL",
                initial_cash=NAV0,
                opened_at="2026-10-01T00:00:00Z",
                idempotency_key="PAPER.ACCOUNT.OPEN.NOFILL",
            )
            unfilled = SIM.submit_order(unfilled, _intent())
            verified = authenticated(unfilled, root / "snapshots")
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_NO_FILL_YET"
            ):
                MODULE.derive_anchor(
                    market="CRYPTO", verified_ledger=verified,
                    price_observations=[price_observation()],
                    recorded_at_utc="2026-10-08T07:10:00Z",
                    clock_witness=witness(),
                    market_state_observation=market_state(),
                    policy=POLICY, params=PARAMS, now=FAR_FUTURE,
                )

    def test_the_anchor_binds_once_and_a_second_different_anchor_refuses(self):
        """Property 3: a later fill can never move or add an anchor."""
        first = anchor()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            MODULE.record_anchor(root, first)
            # Re-recording the identical anchor is a no-op, so a crash between
            # the ledger write and the anchor write is safe to retry.
            MODULE.record_anchor(root, first)
            self.assertEqual(
                MODULE.bound_digests(root, "CRYPTO", first["ledger_id"]),
                {first["packet_sha256"]},
            )
            # A different anchor for the same account -- e.g. derived off a
            # later fill -- refuses instead of binding a second time.
            second = anchor(observations=[price_observation(price="170000000")])
            self.assertNotEqual(second["packet_sha256"], first["packet_sha256"])
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_ALREADY_BOUND_IMMUTABLE"
            ):
                MODULE.record_anchor(root, second)
            self.assertEqual(
                MODULE.bound_digests(root, "CRYPTO", first["ledger_id"]),
                {first["packet_sha256"]},
            )


# ─────────────────────────────────────────────────────────────────────────
# Forgery gaps closed after independent review of PR #790. Each of these three
# fails against the pre-review code.
# ─────────────────────────────────────────────────────────────────────────

class LedgerProvenanceTest(unittest.TestCase):
    """Gap 1 -- an internally hash-consistent ledger dict is not provenance."""

    def test_a_bare_ledger_dict_is_refused_however_consistent_it_is(self):
        forged = filled_ledger()
        SIM.validate_ledger(forged)  # perfectly self-consistent
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "LEDGER_NOT_AUTHENTICATED"
        ):
            MODULE.derive_anchor(
                market="CRYPTO", verified_ledger=forged,
                price_observations=[price_observation()],
                recorded_at_utc="2026-10-08T07:10:00Z",
                clock_witness=witness(), market_state_observation=market_state(),
                policy=POLICY, params=PARAMS, now=FAR_FUTURE,
            )

    def test_an_unpublished_ledger_cannot_be_authenticated(self):
        ledger = filled_ledger()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "LEDGER_SNAPSHOT_STORE_MISSING"
            ):
                MODULE.authenticate_ledger(
                    Path(tmp), ledger["ledger_id"], genesis_pin=genesis_pin(ledger)
                )

    def test_a_ledger_that_does_not_match_the_genesis_pin_is_refused(self):
        real = filled_ledger()
        # Same shape, different account open time -> a different genesis hash.
        other = SIM.create_ledger(
            ledger_id=real["ledger_id"], initial_cash=NAV0,
            opened_at="2026-09-19T07:00:00Z", idempotency_key="PAPER.ACCOUNT.OPEN",
        )
        with tempfile.TemporaryDirectory() as tmp:
            publish(real, Path(tmp))
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "LEDGER_PIN_GENESIS_SHA_MISMATCH"
            ):
                MODULE.authenticate_ledger(
                    Path(tmp), real["ledger_id"], genesis_pin=genesis_pin(other)
                )
        with tempfile.TemporaryDirectory() as tmp:
            publish(real, Path(tmp))
            pin = genesis_pin(real)
            pin["initial_cash"] = "10000000"
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "LEDGER_PIN_INITIAL_CASH_MISMATCH"
            ):
                MODULE.authenticate_ledger(
                    Path(tmp), real["ledger_id"], genesis_pin=pin
                )

    def test_a_divergent_published_history_is_refused_by_recovery(self):
        submitted = SIM.submit_order(
            SIM.create_ledger(
                ledger_id="PAPER.UPBIT.KRW.ATLAS_SERVER.20260918", initial_cash=NAV0,
                opened_at="2026-09-18T07:00:00Z", idempotency_key="PAPER.ACCOUNT.OPEN",
            ),
            _intent(),
        )
        real = SIM.match_order(
            submitted, order_id="PAPER.ORDER.1", snapshot=_snapshot(),
            event_at=FILL_AT, idempotency_key="PAPER.MATCH.1",
        )
        rival = SIM.cancel_order(
            submitted, order_id="PAPER.ORDER.1", event_at=FILL_AT,
            idempotency_key="PAPER.CANCEL.ALT", reason="ALTERNATIVE_HISTORY",
        )
        with tempfile.TemporaryDirectory() as tmp:
            publish(real, Path(tmp))
            publish(rival, Path(tmp))
            with self.assertRaises(SIM.CryptoPaperSimulatorError):
                MODULE.authenticate_ledger(
                    Path(tmp), real["ledger_id"], genesis_pin=genesis_pin(real)
                )

    def test_the_anchor_records_what_it_authenticated_against(self):
        value = anchor()
        provenance = value["ledger_provenance"]
        self.assertEqual(
            provenance["authentication"],
            "PUBLISHED_APPEND_ONLY_SNAPSHOT_STORE_PLUS_GENESIS_PIN",
        )
        self.assertEqual(provenance["genesis_initial_cash"], NAV0)
        self.assertEqual(provenance["residual"], "RATIFICATION_LEDGER_ATTESTATION")
        self.assertIn("RATIFICATION_LEDGER_ATTESTATION", value["ratification_required"])


class RecordTimeWitnessTest(unittest.TestCase):
    """Gap 2 -- the anti-hindsight deadline must not trust the caller's clock."""

    def test_a_record_time_earlier_than_evidence_in_hand_is_refused(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_RECORDED_AT_BEFORE_EVIDENCE"
        ):
            anchor(
                recorded_at_utc="2026-10-08T07:10:00Z",
                clock_witness=witness(available_at="2026-10-08T07:20:00Z"),
            )

    def test_a_record_time_far_from_the_newest_observation_is_refused(self):
        # Claiming 07:10 while the newest evidence in hand is 20 minutes old is
        # the shape of a hindsight write; the bound is the ratified staleness
        # window (max_orderbook_staleness_seconds).
        self.assertEqual(PARAMS["anchor_clock_witness_max_skew_seconds"], 300)
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_CLOCK_WITNESS_SKEW_EXCEEDED"
        ):
            anchor(
                recorded_at_utc="2026-10-08T07:30:00Z",
                clock_witness=witness(available_at="2026-10-08T07:10:00Z"),
            )
        # Inside the window it is accepted, and the skew is recorded.
        value = anchor(
            recorded_at_utc="2026-10-08T07:14:00Z",
            clock_witness=witness(available_at="2026-10-08T07:10:00Z"),
        )
        self.assertEqual(value["clock_witness_skew_seconds"], 240)
        self.assertEqual(value["clock_basis"], "WITNESS_BOUNDED_NOT_PROVEN")

    def test_a_pre_fill_witness_proves_nothing_and_is_refused(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_CLOCK_WITNESS_NOT_AFTER_FILL"
        ):
            anchor(clock_witness=witness(
                observed_at="2026-10-08T07:00:00Z", available_at="2026-10-08T07:10:00Z",
            ))

    def test_a_record_time_in_the_future_is_refused_once_the_fill_is_past(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_RECORDED_AT_IN_FUTURE"
        ):
            anchor(now=dt.datetime(2026, 10, 8, 7, 6, tzinfo=dt.timezone.utc))

    def test_a_witness_for_another_market_is_refused(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError,
            "ANCHOR_CLOCK_WITNESS_MARKET_MISMATCH",
        ):
            anchor(clock_witness=witness(market="KRW-ETH"))


class PointerDeletionTest(unittest.TestCase):
    """Gap 3 -- the pointer file is not the binding."""

    def test_deleting_the_pointer_does_not_allow_a_second_anchor_to_bind(self):
        first = anchor()
        later = anchor(observations=[price_observation(
            price="170000000", observed_at="2026-10-08T07:04:20Z",
            available_at="2026-10-08T07:04:25Z",
        )])
        self.assertNotEqual(first["packet_sha256"], later["packet_sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            MODULE.record_anchor(Path(tmp), first)
            base = Path(tmp) / "CRYPTO" / first["ledger_id"] / "anchor"
            (base / MODULE.POINTER_FILENAME).unlink()
            self.assertFalse((base / MODULE.POINTER_FILENAME).exists())
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_ALREADY_BOUND_IMMUTABLE"
            ):
                MODULE.record_anchor(Path(tmp), later)

    def test_deleting_the_pointer_and_the_bindings_still_leaves_the_record(self):
        first = anchor()
        later = anchor(observations=[price_observation(
            price="170000000", observed_at="2026-10-08T07:04:20Z",
            available_at="2026-10-08T07:04:25Z",
        )])
        with tempfile.TemporaryDirectory() as tmp:
            MODULE.record_anchor(Path(tmp), first)
            base = Path(tmp) / "CRYPTO" / first["ledger_id"] / "anchor"
            (base / MODULE.POINTER_FILENAME).unlink()
            for path in (base / MODULE.BINDINGS_DIRNAME).glob("*.json"):
                path.unlink()
            # The content-addressed record is the second, independent witness.
            self.assertEqual(
                MODULE.bound_digests(Path(tmp), "CRYPTO", first["ledger_id"]),
                {first["packet_sha256"]},
            )
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_ALREADY_BOUND_IMMUTABLE"
            ):
                MODULE.record_anchor(Path(tmp), later)

    def test_a_missing_pointer_is_recoverable_rather_than_fatal(self):
        value = anchor()
        with tempfile.TemporaryDirectory() as tmp:
            MODULE.record_anchor(Path(tmp), value)
            base = Path(tmp) / "CRYPTO" / value["ledger_id"] / "anchor"
            (base / MODULE.POINTER_FILENAME).unlink()
            loaded = MODULE.load_anchor(
                Path(tmp), "CRYPTO", value["ledger_id"],
                trusted_anchor_sha256=value["packet_sha256"],
            )
            self.assertEqual(loaded.sha256, value["packet_sha256"])

    def test_two_bindings_present_refuse_to_load_rather_than_pick_one(self):
        first = anchor()
        later = anchor(observations=[price_observation(
            price="170000000", observed_at="2026-10-08T07:04:20Z",
            available_at="2026-10-08T07:04:25Z",
        )])
        with tempfile.TemporaryDirectory() as tmp:
            MODULE.record_anchor(Path(tmp), first)
            # Simulate a store that somehow already carries two records.
            base = Path(tmp) / "CRYPTO" / first["ledger_id"] / "anchor"
            rogue = base / later["packet_sha256"]
            rogue.mkdir()
            (rogue / MODULE.ANCHOR_FILENAME).write_text(
                MODULE.canonical_json(later) + "\n"
            )
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_MULTIPLE_BINDINGS"
            ):
                MODULE.load_anchor(
                    Path(tmp), "CRYPTO", first["ledger_id"],
                    trusted_anchor_sha256=first["packet_sha256"],
                )


# ─────────────────────────────────────────────────────────────────────────
# Option (c): both notional bases from one anchor, the declared-but-unratified
# binding, and the market-state gates. Each of these fails before the change.
# ─────────────────────────────────────────────────────────────────────────

class MarketStateAtAnchorTest(unittest.TestCase):
    def test_unknown_state_refuses_to_anchor_and_never_takes_0_50(self):
        # The ratified UNKNOWN multiplier is a sentence, not a number.
        self.assertEqual(
            PARAMS["state_multipliers"]["UNKNOWN"],
            "hold_current_up_to_0.50_no_new_buys",
        )
        self.assertEqual(PARAMS["new_buys_by_state"]["UNKNOWN"], "DENY")
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError,
            "ANCHOR_MARKET_STATE_UNKNOWN_REFUSED",
        ):
            anchor(state=market_state(state="UNKNOWN", multiplier="0.50"))

    def test_states_that_deny_new_buys_cannot_be_the_state_of_a_first_fill(self):
        for state in ("RISK_OFF", "STRESS"):
            self.assertEqual(PARAMS["new_buys_by_state"][state], "DENY")
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError,
                f"ANCHOR_MARKET_STATE_FORBIDS_NEW_BUYS:{state}",
            ):
                anchor(state=market_state(state=state))

    def test_neutral_anchors_and_is_sized_at_the_ratified_multiplier(self):
        value = anchor(state=market_state(state="NEUTRAL"))
        self.assertEqual(value["market_state"]["state"], "NEUTRAL")
        self.assertEqual(value["market_state"]["multiplier"], "0.7")
        self.assertEqual(
            value["notionals"]["FLAT_BASE_SHARE"]["notional_krw"], "30000000"
        )
        # 0.15 x 0.70 = 0.105 of NAV0
        self.assertEqual(
            value["notionals"]["MULTIPLIER_MATCHED"]["notional_krw"], "21000000"
        )
        self.assertFalse(value["notional_bases_identical"])

    def test_risk_on_makes_the_two_bases_identical_and_says_so(self):
        value = anchor(state=market_state(state="RISK_ON"))
        self.assertEqual(
            value["notionals"]["FLAT_BASE_SHARE"]["units"],
            value["notionals"]["MULTIPLIER_MATCHED"]["units"],
        )
        self.assertTrue(value["notional_bases_identical"])

    def test_an_absent_market_state_refuses_rather_than_assuming_risk_on(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_MARKET_STATE_UNAVAILABLE"
        ):
            anchor(state=None)
        # Explicitly: passing None does not fall back to RISK_ON.
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_MARKET_STATE_FIELDS_MISMATCH"
        ):
            partial = market_state()
            partial.pop("source_sha256")
            anchor(state=partial)

    def test_a_state_staler_than_the_ratified_crypto_gap_refuses(self):
        # The bound is the ratified rotation observation gap: crypto 2 days.
        self.assertEqual(PARAMS["market_state_max_staleness_seconds"], 2 * 86400)
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_MARKET_STATE_STALE"
        ):
            anchor(state=market_state(
                observed_at="2026-10-05T07:00:00Z",
                available_at="2026-10-05T07:02:00Z",
            ))

    def test_a_state_readable_only_after_the_record_time_refuses(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_MARKET_STATE_FROM_FUTURE"
        ):
            anchor(state=market_state(
                observed_at="2026-10-08T07:06:00Z",
                available_at="2026-10-08T07:40:00Z",
            ))

    def test_a_multiplier_that_is_not_the_ratified_one_refuses(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError,
            "ANCHOR_MARKET_STATE_MULTIPLIER_NOT_RATIFIED:NEUTRAL",
        ):
            anchor(state=market_state(state="NEUTRAL", multiplier="1.00"))

    def test_an_unratified_state_name_refuses(self):
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_MARKET_STATE_NOT_RATIFIED"
        ):
            anchor(state=market_state(state="BULLISH", multiplier="1.00"))

    def test_the_anchor_records_the_state_source_as_unbound(self):
        value = anchor()
        self.assertEqual(
            value["market_state"]["source_binding"],
            "NOT_BOUND_RATIFICATION_MARKET_STATE_SOURCE_BINDING",
        )
        self.assertEqual(
            value["market_state"]["observation"]["source_schema_version"],
            "test_market_state/1",
        )
        self.assertIn(
            "RATIFICATION_MARKET_STATE_SOURCE_BINDING", value["ratification_required"]
        )

    def test_the_state_source_is_read_through_exactly_one_function(self):
        # No path of this module's own: the only way state enters is the
        # documented contract of read_market_state.
        source = SOURCE.read_text()
        self.assertEqual(source.count("def read_market_state("), 1)
        # The candidate artifact is named once, in that function's documented
        # contract, and nowhere else -- and the module builds no data path.
        self.assertEqual(source.count("latest_paper_regime_reference"), 1)
        for forbidden in ('ROOT / "data"', "Path(\"data", "read_text()"):
            self.assertNotIn(forbidden, source, forbidden)


class DeclaredBindingTest(unittest.TestCase):
    def test_the_policy_binds_the_rules_without_authorizing_a_verdict(self):
        binding = POLICY["declared_stop_rule_binding"]
        self.assertEqual(binding["status"], "RATIFIED")
        # Ratifying WHICH curve judges WHICH rule is not authority to publish a
        # verdict off it. That distinction is the whole point of this test.
        self.assertIs(binding["verdict_authorized"], False)
        self.assertEqual(
            binding["CHECKPOINT_B_STOP_RULE_1"]["series"],
            "FLAT_BASE_SHARE__EXPOSURE_MATCHED",
        )
        self.assertEqual(
            binding["CHECKPOINT_B_STOP_RULE_5"]["series"],
            "MULTIPLIER_MATCHED__EXPOSURE_MATCHED",
        )
        ids = {row["id"] for row in POLICY["ratification_required"]}
        self.assertIn("RATIFICATION_NOTIONAL_STATE_MULTIPLIER", ids)

    def test_the_ratified_status_is_backed_by_a_hash_verified_user_record(self):
        """A RATIFIED status that nothing can check is just a string."""
        binding = POLICY["declared_stop_rule_binding"]
        record = binding["ratification_record"]
        path = ROOT / record["repo_path"]
        self.assertTrue(path.is_file(), str(path))
        self.assertEqual(MODULE.file_sha256(path), record["sha256"])
        # The policy may not claim a binding the user's own record does not say.
        ratified = json.loads(path.read_text(encoding="utf-8"))
        bound = ratified["items"][record["item"]]["stop_rule_binding"]
        self.assertEqual(
            bound["CHECKPOINT_B_STOP_RULE_1"]["series"],
            "FLAT_BASE_SHARE__EXPOSURE_MATCHED",
        )
        self.assertEqual(
            bound["CHECKPOINT_B_STOP_RULE_5"]["series"],
            "MULTIPLIER_MATCHED__EXPOSURE_MATCHED",
        )
        self.assertEqual(
            ratified["items"]["anchor_refused_on_unknown_state"]["failure_code"],
            "ANCHOR_MARKET_STATE_UNKNOWN_REFUSED",
        )

    def test_a_ratified_status_with_a_tampered_record_hash_is_refused(self):
        broken = copy.deepcopy(POLICY)
        broken["source_documents"]["benchmark_notional_basis_ratification"][
            "file_sha256"
        ] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError,
                "POLICY_DECLARED_BINDING_RATIFICATION_SHA_MISMATCH",
            ):
                MODULE.load_policy(path)

    def test_the_two_disclosed_residuals_may_not_be_marked_resolved(self):
        binding = POLICY["declared_stop_rule_binding"]
        residuals = binding["residual_weaknesses_still_open"]
        for name in (
            "RATIFICATION_LEDGER_ATTESTATION", "RATIFICATION_CLOCK_ATTESTATION",
        ):
            self.assertTrue(
                residuals[name].startswith("DISCLOSED_NOT_RESOLVED"), name
            )
            self.assertIn(name, binding["verdict_authorized_blocked_on"])
            self.assertIn(
                name, {row["id"] for row in POLICY["ratification_required"]}
            )
        broken = copy.deepcopy(POLICY)
        broken["declared_stop_rule_binding"]["residual_weaknesses_still_open"][
            "RATIFICATION_LEDGER_ATTESTATION"
        ] = "RESOLVED by the 2026-09-18 record"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.PaperBenchmarkNavSeriesError,
                "POLICY_DECLARED_BINDING_RESIDUAL_CLOSED:"
                "RATIFICATION_LEDGER_ATTESTATION",
            ):
                MODULE.load_policy(path)

    def test_no_verdict_is_emitted_even_though_the_binding_is_ratified(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = series(tmp)
        for name in MODULE.SERIES_NAMES:
            self.assertEqual(
                record["comparison"][name]["verdict"], MODULE.VERDICT_NOT_EMITTED,
            )

    def test_the_binding_is_recorded_from_the_anchor_onward(self):
        value = anchor()
        self.assertEqual(
            value["declared_stop_rule_binding"], POLICY["declared_stop_rule_binding"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            record = series(tmp, value=value)
        self.assertEqual(
            record["declared_stop_rule_binding"], POLICY["declared_stop_rule_binding"]
        )
        self.assertEqual(
            record["comparison"]["FLAT_BASE_SHARE__EXPOSURE_MATCHED"][
                "declared_binding_for"
            ],
            ["CHECKPOINT_B_STOP_RULE_1"],
        )
        self.assertEqual(
            record["comparison"]["MULTIPLIER_MATCHED__EXPOSURE_MATCHED"][
                "declared_binding_for"
            ],
            ["CHECKPOINT_B_STOP_RULE_5"],
        )
        self.assertEqual(
            record["comparison"]["FLAT_BASE_SHARE__ASSET_ONLY"][
                "declared_binding_for"
            ],
            [],
        )

    def test_an_anchor_missing_the_binding_fails_validation(self):
        broken = copy.deepcopy(anchor())
        broken.pop("declared_stop_rule_binding")
        broken = MODULE._with_packet_sha(broken)
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError, "ANCHOR_DECLARED_BINDING_INVALID"
        ):
            MODULE.validate_anchor(broken)

    def test_a_rewritten_binding_fails_validation(self):
        broken = copy.deepcopy(anchor())
        broken["declared_stop_rule_binding"]["CHECKPOINT_B_STOP_RULE_5"]["series"] = (
            "FLAT_BASE_SHARE__EXPOSURE_MATCHED"
        )
        broken = MODULE._with_packet_sha(broken)
        with self.assertRaisesRegex(
            MODULE.PaperBenchmarkNavSeriesError,
            "ANCHOR_DECLARED_BINDING_SERIES_MISMATCH:CHECKPOINT_B_STOP_RULE_5",
        ):
            MODULE.validate_anchor(broken)


class OneAnchorTwoBasesTest(unittest.TestCase):
    def test_all_four_series_come_from_one_anchor_and_one_genesis_pin(self):
        value = anchor(state=market_state(state="NEUTRAL"))
        with tempfile.TemporaryDirectory() as tmp:
            record = series(tmp, value=value)
        self.assertEqual(set(record["variants"]), set(MODULE.SERIES_NAMES))
        # One anchor digest, one anchor instant, one cost model for all four.
        self.assertEqual(record["anchor_sha256"], value["packet_sha256"])
        self.assertEqual(record["anchor_utc"], value["anchor_utc"])
        for name, row in record["variants"].items():
            self.assertEqual(
                row["notional_basis_detail"],
                value["notionals"][row["notional_basis"]],
            )
            self.assertEqual(name, f"{row['notional_basis']}__{row['marking']}")
        self.assertEqual(
            record["market_state_at_anchor"]["state"], "NEUTRAL"
        )

    def test_the_two_bases_differ_in_exposure_but_agree_on_the_sleeve(self):
        value = anchor(state=market_state(state="NEUTRAL"))
        with tempfile.TemporaryDirectory() as tmp:
            record = series(
                tmp, value=value, navs=("200000000", "200000000"),
                prices=(ANCHOR_PRICE, "176000000"),
            )
        flat = record["variants"]["FLAT_BASE_SHARE__EXPOSURE_MATCHED"]
        matched = record["variants"]["MULTIPLIER_MATCHED__EXPOSURE_MATCHED"]
        # BTC +10%: 15% of NAV0 gains ~1.5%, 10.5% of NAV0 gains ~1.05%.
        self.assertGreater(
            Decimal(flat["final_return_fraction"]),
            Decimal(matched["final_return_fraction"]),
        )
        # The sleeve return is the same asset either way, so the two ASSET_ONLY
        # curves agree to within floor rounding.
        a = Decimal(record["variants"]["FLAT_BASE_SHARE__ASSET_ONLY"][
            "final_return_fraction"])
        b = Decimal(record["variants"]["MULTIPLIER_MATCHED__ASSET_ONLY"][
            "final_return_fraction"])
        self.assertLess(abs(a - b), Decimal("0.000001"))

    def test_rule_five_is_read_off_the_matched_curve_and_differs_from_flat(self):
        value = anchor(state=market_state(state="NEUTRAL"))
        with tempfile.TemporaryDirectory() as tmp:
            record = series(
                tmp, value=value,
                navs=("200000000", "190000000", "195000000"),
                prices=(ANCHOR_PRICE, "128000000", "144000000"),
                generated_at="2026-10-11T08:00:00Z",
            )
        flat = record["comparison"]["FLAT_BASE_SHARE__EXPOSURE_MATCHED"][
            "stop_rule_5_inputs"]
        matched = record["comparison"]["MULTIPLIER_MATCHED__EXPOSURE_MATCHED"][
            "stop_rule_5_inputs"]
        # BTC -20%: the flat benchmark loses 3% of NAV, the matched one 2.1%.
        # Reading rule 5 off the flat curve flatters the account by 0.9 points,
        # which is exactly why the binding matters.
        self.assertLess(
            Decimal(flat["benchmark_max_drawdown_fraction"]),
            Decimal(matched["benchmark_max_drawdown_fraction"]),
        )
        self.assertNotEqual(
            flat["paper_minus_benchmark_max_drawdown_fraction"],
            matched["paper_minus_benchmark_max_drawdown_fraction"],
        )
        self.assertEqual(
            record["comparison"]["MULTIPLIER_MATCHED__EXPOSURE_MATCHED"]["verdict"],
            "NOT_EMITTED_RATIFICATION_REQUIRED",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
