#!/usr/bin/env python3
"""PAPER execution core v1: config trace, allocation envelope, drawdown, reductions, session budget.

Offline only.  Expected numbers are read from the byte-exact authority
records (never restated) or recomputed independently in the test.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
from fractions import Fraction
import itertools
import json
import shutil
from pathlib import Path
import sys
import tempfile
import tokenize
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance import rule_registry as REG  # noqa: E402
from portfolio import paper_allocation_envelope as ENV  # noqa: E402
from portfolio import paper_execution_core as CORE  # noqa: E402
from portfolio import paper_session_budget as BUD  # noqa: E402

CORE_FILES = [
    "portfolio/paper_execution_core.py", "portfolio/paper_session_budget.py", "portfolio/paper_allocation_envelope.py",
    "portfolio/paper_position_episode.py", "portfolio/paper_execution_status.py",
    "validation/paper_mechanical_checklist.py", "validation/paper_scorecard_contract.py",
]
T = "2026-09-20T07:30:00Z"
ZERO = {"US": 0, "KR": 0, "CRYPTO": 0}
F = Fraction


def record(name: str) -> dict:
    return json.loads((ROOT / "evidence/authority" / name).read_text(encoding="utf-8"))


ALLOC = record("paper_market_allocation_v2_user_ratification_20260913.json")
SIZE = record("paper_session_size_wording_correction_user_ratification_20260915.json")


def envelope(core, states, *, streaks=None, stage="NONE", flow=False, mode="NATURAL"):
    return ENV.allocation_envelope(core, decision_at_utc=T, market_states=states, unknown_streaks=streaks or dict(ZERO),
                                   drawdown_stage=stage, cross_market_flow_validated=flow, evidence_mode=mode)


def all_on(core):
    return envelope(core, {"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_ON"})


def snapshot(cash="200000000", holdings=(), reservations=(), fx=None, as_of="2026-09-20T07:10:00Z"):
    return {"as_of_utc": as_of, "virtual_cash_krw": cash, "holdings": list(holdings),
            "open_buy_reservations": list(reservations), "fx_observation": fx}


def holding(market, code, value, *, last=None, inverse=False):
    return {"market": market, "instrument": code, "currency": "USD" if market == "US" else "KRW",
            "valuation": value, "last_verified_valuation": last, "is_inverse_hedge": inverse}


def cand(code, adv="1000000000000", window="30_DAYS", price=None, step=None, fee=None):
    return {"instrument": code, "avg_traded_value": adv, "adv_window": window, "adv_source": "TEST_FIXTURE",
            "limit_price": price, "quantity_step": step, "fee_rate": fee}


def fx(date="2026-09-17", published="2026-09-18T21:15:00Z", rate="1350.25"):
    return {"series": "FRED:DEXKOUS", "observation_date": date, "published_at_utc": published, "krw_per_usd": rate}


class ConfigTraceTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()

    def test_config_pin_and_registry_bound_parameters(self):
        self.assertEqual(self.core.config_sha256, CORE.PINNED_CONFIG_SHA256)
        for alias, ref in self.core.config["registry_parameters"].items():
            row = self.core.context.rules[ref["rule_id"]]
            self.assertEqual(self.core.param(alias), row["key_parameters"][ref["key_parameter"]]["value"], alias)
        # Values trace to the authority records themselves.
        self.assertEqual(self.core.param("base_allocation"), ALLOC["base_allocation_all_markets_risk_on"])
        self.assertEqual(self.core.param("market_max_allocation"), ALLOC["market_max_allocation"])
        self.assertEqual(self.core.param("state_multipliers"), ALLOC["per_market_state_multiplier_of_base"])
        self.assertIn("one-third", SIZE["decision"]["market_session_total"])
        self.assertEqual(self.core.context.sha256, REG.registry_sha256())

    def test_config_tamper_fails_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / CORE.CONFIG_RELATIVE_PATH
            path.parent.mkdir(parents=True)
            path.write_text((ROOT / CORE.CONFIG_RELATIVE_PATH).read_text().replace(
                '"interval_multiple": "2"', '"interval_multiple": "3"'))
            with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "CONFIG_SHA_PIN_MISMATCH"):
                CORE.load_core(Path(tmp))

    def test_no_numeric_literals_restating_ratified_numbers(self):
        # No integer other than 0/1 and no decimal or ratio string at all: ratified
        # numbers such as 2, 3, 5, 10, 14, 20, 21, 30 must come from the registry.
        for rel in CORE_FILES:
            with open(ROOT / rel, "rb") as handle:
                for tok in tokenize.tokenize(handle.readline):
                    if tok.type == tokenize.NUMBER:
                        self.assertIn(tok.string, ("0", "1"), f"{rel}:{tok.start}")
                    if tok.type == tokenize.STRING:
                        self.assertNotRegex(tok.string, r"^[\"']-?\d+(\.\d+|/\d+)?[\"']$", f"{rel}:{tok.start}")

    def test_reduction_tier_order_bound_to_registry(self):
        self.assertEqual(self.core.interpretations["reduction"]["tier_order"], self.core.param("reduction_order"))
        config = copy.deepcopy(self.core.config)
        config["cio_interpretations"]["reduction"]["tier_order"] = ["LOWER_RANK", "STRENGTH_RELEASED", "PRO_RATA"]
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "CONFIG_REDUCTION_ORDER_DIFFERS_FROM_REGISTRY"):
            CORE._validate_config(config, self.core.context)

    def test_registry_snapshots_committed(self):
        current = REG.registry_sha256()
        path = ROOT / CORE.snapshot_relative_path(current)
        self.assertTrue(path.is_file(), "commit the registry snapshot: CORE.write_registry_snapshot()")
        self.assertEqual(gzip.decompress(path.read_bytes()), (ROOT / REG.REGISTRY_RELATIVE_PATH).read_bytes())
        for snapshot in (ROOT / CORE.SNAPSHOT_DIR).glob("rule_registry_v1-*"):
            digest = hashlib.sha256(gzip.decompress(snapshot.read_bytes())).hexdigest()
            self.assertEqual(snapshot.name, f"rule_registry_v1-{digest}.json.gz")

    def test_rule_refs_are_registry_exact_and_in_force(self):
        env = all_on(self.core)
        for ref in env["rule_refs"]:
            self.assertEqual(ref["registry_sha256"], REG.registry_sha256())
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "RULE_NOT_IN_FORCE_AT_DECISION"):
            ENV.allocation_envelope(self.core, decision_at_utc="2026-09-14T00:00:00Z",
                                    market_states={"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_ON"},
                                    unknown_streaks=dict(ZERO), drawdown_stage="NONE",
                                    cross_market_flow_validated=False, evidence_mode="NATURAL")

    def test_not_defined_items_are_listed(self):
        ids = self.core.not_defined_ids()
        for item in ("UNKNOWN_CAP_REDUCTION_PACE", "DRAWDOWN_OVERRIDE_REDUCTION_PACE", "KR_STRESS_CONDITION",
                     "UNKNOWN_MARKET_RELEASED_CAPITAL", "FLOW_PRIORITY_METHOD", "SCORECARD_BADGE_THRESHOLDS",
                     "ALTERNATIVE_INDEPENDENT_PRICE_PATH", "DRAWDOWN_STEP_BEYOND_RISK_OFF_OR_UNKNOWN"):
            self.assertIn(item, ids)
        self.assertTrue(all(v is False for v in self.core.config["authority"].values()))


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()

    def caps(self, env):
        return {m: env["markets"][m]["cap_fraction"] for m in CORE.MARKETS}

    def test_record_examples_reproduced(self):
        examples = ALLOC["examples_nav"]
        cases = {
            "all_risk_on": {"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_ON"},
            "US_on_KR_neutral_crypto_off": {"US": "RISK_ON", "KR": "NEUTRAL", "CRYPTO": "RISK_OFF"},
            "all_neutral": {"US": "NEUTRAL", "KR": "NEUTRAL", "CRYPTO": "NEUTRAL"},
            "all_risk_off": {"US": "RISK_OFF", "KR": "RISK_OFF", "CRYPTO": "RISK_OFF"},
            "all_stress": {"US": "STRESS", "KR": "STRESS", "CRYPTO": "STRESS"},
        }
        for name, states in cases.items():
            mode = "FIXED_INPUT_REPLAY" if name == "all_stress" else "NATURAL"
            caps = {m: F(v) for m, v in self.caps(envelope(self.core, states, mode=mode)).items()}
            self.assertEqual(sum(caps.values()), F(examples[name]["invested"]), name)
            for market in CORE.MARKETS:
                if market in examples[name]:
                    self.assertEqual(caps[market], F(examples[name][market]), f"{name}:{market}")

    def test_multi_receiver_split_by_base_ratio(self):
        env = envelope(self.core, {"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_OFF"})
        base, mult = ALLOC["base_allocation_all_markets_risk_on"], ALLOC["per_market_state_multiplier_of_base"]
        released = F(base["CRYPTO"]) * (1 - F(mult["RISK_OFF"]))
        weight = F(base["US"]) + F(base["KR"])
        self.assertEqual(F(env["markets"]["US"]["reallocated_extra_fraction"]), released * F(base["US"]) / weight)
        self.assertEqual(F(env["markets"]["KR"]["reallocated_extra_fraction"]), released * F(base["KR"]) / weight)
        self.assertEqual(env["reallocation_status"], "APPLIED_MULTI_RECEIVER")
        self.assertIn(("RULE.EXEC.MULTI_MARKET_REALLOCATION.V1", "SIZED_BY"),
                      {(r["rule_id"], r["role"]) for r in env["rule_refs"]})

    def test_state_grid_caps_bounded_and_single_pass_agrees(self):
        base = {m: F(v) for m, v in ALLOC["base_allocation_all_markets_risk_on"].items() if m in CORE.MARKETS}
        top = {m: F(v) for m, v in ALLOC["market_max_allocation"].items()}
        known = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS")
        for combo in itertools.product(known, repeat=3):
            states = dict(zip(CORE.MARKETS, combo))
            env = envelope(self.core, states, mode="FIXED_INPUT_REPLAY")
            caps = {m: F(env["markets"][m]["cap_fraction"]) for m in CORE.MARKETS}
            self.assertLessEqual(sum(caps.values()), 1)
            for m in CORE.MARKETS:
                self.assertLessEqual(caps[m], top[m])
            # Independent single-pass capped proportional split.
            mult = {m: F(ALLOC["per_market_state_multiplier_of_base"][states[m]]) for m in CORE.MARKETS}
            pool = sum(base[m] * (1 - mult[m]) for m in CORE.MARKETS if states[m] != "RISK_ON")
            receivers = [m for m in CORE.MARKETS if states[m] == "RISK_ON"]
            for m in CORE.MARKETS:
                extra = 0
                if m in receivers and pool:
                    extra = min(top[m] - base[m], pool * base[m] / sum(base[r] for r in receivers))
                self.assertEqual(caps[m], base[m] * mult[m] + extra, str(states))

    def test_unknown_streak_and_cap(self):
        self.assertEqual(ENV.unknown_streak(["RISK_ON", "UNKNOWN", "NEUTRAL", "UNKNOWN", "UNKNOWN"]), 2)
        states = {"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "UNKNOWN"}
        first = envelope(self.core, states, streaks={"US": 0, "KR": 0, "CRYPTO": 1})["markets"]["CRYPTO"]
        self.assertIsNone(first["cap_fraction"])
        self.assertEqual(first["new_buys"], "DENY")
        second = envelope(self.core, states, streaks={"US": 0, "KR": 0, "CRYPTO": 2})
        base = F(ALLOC["base_allocation_all_markets_risk_on"]["CRYPTO"])
        self.assertEqual(F(second["markets"]["CRYPTO"]["cap_fraction"]), base / 2)
        self.assertIn("NOT_DEFINED:UNKNOWN_MARKET_RELEASED_CAPITAL", second["flags"])
        self.assertEqual(second["reallocation_status"], "NONE")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "UNKNOWN_STREAK_STATE_MISMATCH"):
            envelope(self.core, states)

    def test_flow_validated_reallocation_not_defined(self):
        env = envelope(self.core, {"US": "RISK_ON", "KR": "RISK_OFF", "CRYPTO": "RISK_ON"}, flow=True)
        self.assertEqual(env["reallocation_status"], "NOT_DEFINED")
        self.assertEqual(env["markets"]["US"]["reallocated_extra_fraction"], "0")

    def test_drawdown_effects_on_state(self):
        on = {"US": "RISK_ON", "KR": "NEUTRAL", "CRYPTO": "RISK_OFF"}
        m5 = envelope(self.core, on, stage="MINUS_5")["markets"]
        self.assertEqual((m5["US"]["effective_state"], m5["KR"]["effective_state"]), ("NEUTRAL", "RISK_OFF"))
        self.assertIn("NOT_DEFINED:DRAWDOWN_STEP_BEYOND_RISK_OFF_OR_UNKNOWN", m5["CRYPTO"]["flags"])
        m10 = envelope(self.core, {"US": "RISK_ON", "KR": "STRESS", "CRYPTO": "UNKNOWN"},
                       streaks={"US": 0, "KR": 0, "CRYPTO": 1}, stage="MINUS_10", mode="FIXED_INPUT_REPLAY")["markets"]
        risk_off = F(ALLOC["per_market_state_multiplier_of_base"]["RISK_OFF"])
        base = {m: F(v) for m, v in ALLOC["base_allocation_all_markets_risk_on"].items() if m in CORE.MARKETS}
        self.assertEqual(F(m10["US"]["cap_fraction"]), base["US"] * risk_off)
        self.assertEqual(m10["KR"]["cap_fraction"], "0")
        self.assertEqual(F(m10["CRYPTO"]["cap_fraction"]), base["CRYPTO"] * risk_off)
        self.assertTrue(all(m10[m]["new_buys"] == "DENY" for m in CORE.MARKETS))

    def test_kr_stress_only_in_fixture_replay_and_kr_hedge_off(self):
        states = {"US": "RISK_ON", "KR": "STRESS", "CRYPTO": "RISK_ON"}
        natural = envelope(self.core, states)
        kr = natural["markets"]["KR"]
        self.assertEqual((kr["effective_state"], kr["cap_fraction"], kr["new_buys"]), ("FAIL_CLOSED", None, "DENY"))
        self.assertIn("KR_STRESS_CONDITION_UNRATIFIED_FIXED_INPUT_REPLAY_ONLY", natural["flags"])
        base = ALLOC["base_allocation_all_markets_risk_on"]
        self.assertEqual(F(natural["markets"]["US"]["cap_fraction"]), F(base["US"]))
        self.assertEqual(F(natural["markets"]["CRYPTO"]["cap_fraction"]), F(base["CRYPTO"]))
        self.assertEqual(natural["reallocation_status"], "NONE")
        env = envelope(self.core, states, mode="FIXED_INPUT_REPLAY")
        self.assertEqual(env["markets"]["KR"]["effective_state"], "STRESS")
        self.assertEqual(env["markets"]["KR"]["inverse_hedge"]["status"], "OFF")
        self.assertIn(("RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1", "BLOCKED_BY"),
                      {(r["rule_id"], r["role"]) for r in env["rule_refs"]})
        self.assertEqual(ENV.validate_envelope(env), env)


class DrawdownTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()

    def run_series(self, navs, verified=None):
        verified = verified or [True] * len(navs)
        series = [{"as_of_utc": f"2026-09-{16 + i:02d}T00:00:00Z", "combined_nav_krw": v, "verified": ok}
                  for i, (v, ok) in enumerate(zip(navs, verified))]
        return ENV.drawdown_state(self.core, series, decision_at_utc="2026-10-15T00:00:00Z")

    def test_trigger_and_lift_boundaries_inclusive(self):
        self.assertEqual(self.run_series(["1000", "951"])["stage"], "NONE")
        self.assertEqual(self.run_series(["1000", "950"])["stage"], "MINUS_5")
        self.assertEqual(self.run_series(["1000", "950", "974"])["stage"], "MINUS_5")
        self.assertEqual(self.run_series(["1000", "950", "975"])["stage"], "NONE")
        self.assertEqual(self.run_series(["1000", "900"])["stage"], "MINUS_10")
        # -10% lifts within -5% but the -5% stage stays until within -2.5%.
        self.assertEqual(self.run_series(["1000", "900", "950"])["stage"], "MINUS_5")
        self.assertEqual(self.run_series(["1000", "900", "949"])["stage"], "MINUS_10")
        self.assertEqual(self.run_series(["1000", "900", "975"])["stage"], "NONE")

    def test_no_peak_reset_and_unverified_ignored(self):
        state = self.run_series(["1000", "900", "975", "940"])
        self.assertEqual(state["peak_nav_krw"], "1000")
        self.assertEqual(state["stage"], "MINUS_5")
        self.assertFalse(state["peak_reset_applied"])
        gap = self.run_series(["1000", "800", "990"], verified=[True, False, True])
        self.assertEqual(gap["stage"], "NONE")
        self.assertIn("NAV_PARTIALLY_UNVERIFIED", gap["flags"])
        self.assertEqual(self.run_series(["1000"], verified=[False])["stage"], "UNKNOWN")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "NAV_OBSERVATION_AFTER_DECISION"):
            ENV.drawdown_state(self.core, [{"as_of_utc": "2026-10-16T00:00:00Z", "combined_nav_krw": "1", "verified": True}],
                               decision_at_utc="2026-10-15T00:00:00Z")


def lh(code, value, *, price="FRESH", strength="HELD", rank=None, inverse=False):
    return {"instrument": code, "value_krw": value, "price_status": price, "strength_state": strength, "rank": rank,
            "is_inverse_hedge": inverse}


class ReductionTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()

    def plan(self, trigger, cap, holdings, **kw):
        kw.setdefault("evidence_mode", "NATURAL")
        return ENV.reduction_plan(self.core, market=kw.pop("market", "CRYPTO"), trigger=trigger, decision_at_utc=T,
                                  cap_krw=cap, long_holdings=holdings, open_buy_orders=kw.pop("orders", []), **kw)

    def test_price_drift_never_sells(self):
        plan = self.plan("PRICE_DRIFT_NO_STATE_CHANGE", "100", [lh("KRW-A", "150")],
                         orders=[{"order_id": "o1", "instrument": "KRW-A", "reserved_krw": "5"}])
        self.assertEqual((plan["status"], plan["reduction_amount_krw"], plan["lines"], plan["open_buy_cancellations"]),
                         ("NO_SELL_PRICE_DRIFT", "0", [], []))

    def test_stress_full_and_stale_is_risk_uncertain(self):
        plan = self.plan("STRESS", "0", [lh("KRW-A", "60"), lh("KRW-B", "40", price="STALE"), lh("KRW-INV", "30", inverse=True)],
                         orders=[{"order_id": "o1", "instrument": "KRW-A", "reserved_krw": "5"}])
        self.assertEqual(plan["reduction_amount_krw"], "100")
        by = {l["instrument"]: l for l in plan["lines"]}
        self.assertNotIn("KRW-INV", by)
        self.assertTrue(all(l["full_position"] for l in plan["lines"]))
        self.assertEqual(by["KRW-B"]["execution"]["status_code"], "RISK_EXECUTION_UNCERTAIN")
        self.assertEqual(by["KRW-A"]["execution"]["action"], "EXECUTE_AT_FIRST_ALLOWED_FILL")
        self.assertEqual(plan["open_buy_cancellations"][0]["action"], "CANCEL_BEFORE_REDUCTION")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "STRESS_CAP_MUST_BE_ZERO"):
            self.plan("STRESS", "1", [lh("KRW-A", "60")])
        closed = self.plan("STRESS", "0", [lh("005930", "60")], market="KR")
        self.assertEqual((closed["status"], closed["lines"], closed["open_buy_cancellations"]), ("FAIL_CLOSED", [], []))
        self.assertEqual(self.plan("STRESS", "0", [lh("005930", "60")], market="KR",
                                   evidence_mode="FIXED_INPUT_REPLAY")["reduction_amount_krw"], "60")

    def test_downgrade_two_sessions(self):
        holdings = [lh("KRW-A", "100"), lh("KRW-B", "100")]
        one = self.plan("DOWNGRADE", "120", holdings,
                        downgrade_progress={"session_index": 1, "excess_at_trigger_krw": "80", "reduced_krw": "0"})
        self.assertEqual(one["reduction_amount_krw"], "40")
        # Session 2: the rest of the trigger excess, never more than today's excess.
        after = [lh("KRW-A", "80"), lh("KRW-B", "80")]
        self.assertEqual(self.plan("DOWNGRADE", "120", after, downgrade_progress={
            "session_index": 2, "excess_at_trigger_krw": "80", "reduced_krw": "40"})["reduction_amount_krw"], "40")
        risen = [lh("KRW-A", "100"), lh("KRW-B", "100")]
        self.assertEqual(self.plan("DOWNGRADE", "120", risen, downgrade_progress={
            "session_index": 2, "excess_at_trigger_krw": "80", "reduced_krw": "40"})["reduction_amount_krw"], "40")
        fallen = [lh("KRW-A", "70"), lh("KRW-B", "60")]
        self.assertEqual(self.plan("DOWNGRADE", "120", fallen, downgrade_progress={
            "session_index": 2, "excess_at_trigger_krw": "80", "reduced_krw": "40"})["reduction_amount_krw"], "10")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "DOWNGRADE_PROGRESS_INVALID"):
            self.plan("DOWNGRADE", "120", holdings, downgrade_progress={
                "session_index": 3, "excess_at_trigger_krw": "80", "reduced_krw": "0"})

    def test_reduction_order_released_then_low_rank_then_pro_rata(self):
        holdings = [lh("KRW-R", "30", strength="RELEASED"), lh("KRW-R1", "50", rank=1), lh("KRW-R3A", "20", rank=3),
                    lh("KRW-R3B", "60", rank=3), lh("KRW-U1", "40"), lh("KRW-U2", "60")]
        total = 260
        plan = self.plan("DOWNGRADE", str(total - 240), holdings,
                         downgrade_progress={"session_index": 1, "excess_at_trigger_krw": "240", "reduced_krw": "0"})
        sells = {l["instrument"]: (l["tier"], F(l["sell_value_krw"])) for l in plan["lines"]}
        self.assertEqual(sells["KRW-R"], ("STRENGTH_RELEASED", F(30)))
        self.assertEqual(sells["KRW-R3A"], ("LOWER_RANK:3", F(20)))
        self.assertEqual(sells["KRW-R3B"], ("LOWER_RANK:3", F(60)))
        self.assertEqual(sells["KRW-R1"], ("LOWER_RANK:1", F(10)))
        self.assertNotIn("KRW-U1", sells)
        tie_holdings = [lh("KRW-R3A", "20", rank=3), lh("KRW-R3B", "60", rank=3), lh("KRW-U", "100")]
        partial = self.plan("DOWNGRADE", "160", tie_holdings, downgrade_progress={
            "session_index": 1, "excess_at_trigger_krw": "20", "reduced_krw": "0"})
        tie = {l["instrument"]: F(l["sell_value_krw"]) for l in partial["lines"]}
        self.assertEqual(tie, {"KRW-R3A": F(5, 2), "KRW-R3B": F(15, 2)})  # pro rata inside the rank-3 tier

    def test_unknown_cap_and_drawdown_pace_follows_the_downgrade_pace_for_crypto(self):
        """RULE.EXEC.REDUCTION_PACE_UNKNOWN_CAP_AND_DRAWDOWN.V1 (crypto PAPER v2 operation record):
        half of the excess in the first session, the remainder in the next (same as D5-b)."""
        holdings = [lh("KRW-A", "100")]
        for trigger in ("UNKNOWN_CAP", "DRAWDOWN_OVERRIDE"):
            with self.subTest(trigger=trigger):
                first = self.plan(trigger, "50", holdings, downgrade_progress={
                    "session_index": 1, "excess_at_trigger_krw": "50", "reduced_krw": "0"})
                downgrade = self.plan("DOWNGRADE", "50", holdings, downgrade_progress={
                    "session_index": 1, "excess_at_trigger_krw": "50", "reduced_krw": "0"})
                self.assertEqual((first["status"], first["reduction_amount_krw"]), ("PLANNED", "25"))
                self.assertEqual(first["reduction_amount_krw"], downgrade["reduction_amount_krw"])
                self.assertIn(("RULE.EXEC.REDUCTION_PACE_UNKNOWN_CAP_AND_DRAWDOWN.V1", "EXITED_BY"),
                              {(r["rule_id"], r["role"]) for r in first["rule_refs"]})
                second = self.plan(trigger, "75", [lh("KRW-A", "100")], downgrade_progress={
                    "session_index": 2, "excess_at_trigger_krw": "50", "reduced_krw": "25"})
                self.assertEqual(second["reduction_amount_krw"], "25")
                with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "DOWNGRADE_PROGRESS_INVALID"):
                    self.plan(trigger, "50", holdings)
        # KR/US are outside the record's crypto scope: still NOT_DEFINED.
        for market in ("KR", "US"):
            plan = self.plan("UNKNOWN_CAP", "50", holdings, market=market)
            self.assertEqual((plan["status"], plan["reduction_amount_krw"], plan["lines"]), ("NOT_DEFINED", None, []))
        self.assertIn("UNKNOWN_CAP_REDUCTION_PACE", self.core.not_defined_ids())

    def test_reduction_pace_rule_must_equal_the_downgrade_pace(self):
        real = self.core.param

        def drifted(alias):
            value = real(alias)
            if alias == "unknown_drawdown_reduction_pace":
                value = dict(value, session_1_fraction_of_excess="1/3")
            return value

        with mock.patch.object(self.core, "param", side_effect=drifted), \
                self.assertRaisesRegex(CORE.PaperExecutionCoreError, "REDUCTION_PACE_DIFFERS_FROM_DOWNGRADE_PACE"):
            self.plan("UNKNOWN_CAP", "50", [lh("KRW-A", "100")], downgrade_progress={
                "session_index": 1, "excess_at_trigger_krw": "50", "reduced_krw": "0"})


class SessionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.core = CORE.load_core()
        self.env = all_on(self.core)
        self.sid = BUD.crypto_session_id(self.core, T)

    def build(self, market="CRYPTO", candidates=(), snap=None, env=None, session_id=None, at=T):
        return BUD.build_session_budget_record(self.core, market=market, session_id=session_id or self.sid,
                                               decision_at_utc=at, nav_snapshot=snap or snapshot(),
                                               envelope=env or self.env, candidates=list(candidates))

    def test_session_ids_and_bounds(self):
        self.assertEqual(BUD.crypto_session_id(self.core, "2026-09-20T06:59:59Z"), "CRYPTO-2026-09-19")
        self.assertEqual(BUD.crypto_session_id(self.core, "2026-09-20T07:00:00Z"), "CRYPTO-2026-09-20")
        self.assertEqual(BUD.session_bounds(self.core, "CRYPTO-2026-09-20")["order_valid_before_utc"], "2026-09-21T07:00:00Z")
        self.assertEqual(BUD.session_bounds(self.core, "KR-2026-09-21")["order_valid_before_utc"], "2026-09-21T06:20:00Z")
        self.assertEqual(BUD.session_bounds(self.core, "US-2026-09-21")["order_valid_before_utc"], "2026-09-21T19:50:00Z")
        self.assertEqual(BUD.session_bounds(self.core, "US-2026-12-01")["order_valid_before_utc"], "2026-12-01T20:50:00Z")

    def test_budget_is_one_third_of_room_and_water_filling(self):
        base = F(ALLOC["base_allocation_all_markets_risk_on"]["CRYPTO"])
        for n in (1, 2, 3, 7):
            rec = self.build(candidates=[cand(f"KRW-C{i}") for i in range(n)])
            budget = F(200000000) * base / 3
            self.assertEqual(F(rec["market_room"]["budget_krw"]), budget)
            self.assertIn("<= 5% NAV", SIZE["decision"]["per_name_cumulative_holding"])
            name_cap = F(200000000) * 5 / 100
            expected = min(budget / n, name_cap)
            for line in rec["allocation"]:
                self.assertEqual(F(line["allocated_krw"]), expected, n)
            self.assertEqual(F(rec["unallocated_krw"]), budget - expected * n)
            self.assertFalse(rec["carry_over"])
            self.assertEqual([l["instrument"] for l in rec["allocation"]], sorted(l["instrument"] for l in rec["allocation"]))

    def test_liquidity_and_name_caps_redistribute(self):
        rec = self.build(candidates=[cand("KRW-A", adv="100000000"), cand("KRW-B"), cand("KRW-C")],
                         snap=snapshot(holdings=[holding("CRYPTO", "KRW-C", "9000000")]))
        lines = {l["instrument"]: l for l in rec["allocation"]}
        self.assertEqual(lines["KRW-A"]["allocated_krw"], "1000000")  # 1% of ADV
        self.assertEqual(lines["KRW-A"]["bound_by"], "LIQUIDITY_ROOM")
        nav0 = F(209000000)
        name_cap = nav0 / 20
        self.assertEqual(F(lines["KRW-C"]["allocated_krw"]), name_cap - 9000000)
        self.assertEqual(lines["KRW-C"]["bound_by"], "NAME_ROOM")
        budget = (nav0 * F(ALLOC["base_allocation_all_markets_risk_on"]["CRYPTO"]) - 9000000) / 3
        self.assertEqual(F(lines["KRW-B"]["allocated_krw"]), budget - 1000000 - (name_cap - 9000000))
        unknown = self.build(candidates=[cand("KRW-A", adv=None), cand("KRW-B")])
        self.assertIn("ADV_UNKNOWN_NO_LIQUIDITY_ROOM", unknown["allocation"][0]["reasons"])
        self.assertEqual(unknown["allocation"][0]["allocated_krw"], "0")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "ADV_WINDOW_MISMATCH"):
            self.build(candidates=[cand("KRW-A", window="20_SESSIONS")])

    def test_inverse_hedge_and_reservations(self):
        plain = self.build(market="KR", session_id="KR-2026-09-21", candidates=[cand("005930", window="20_SESSIONS")])
        inverse = self.build(market="KR", session_id="KR-2026-09-21", candidates=[cand("005930", window="20_SESSIONS")],
                             snap=snapshot(cash="190000000", holdings=[holding("KR", "114800", "10000000", inverse=True)]))
        self.assertEqual(plain["market_room"]["room_krw"], inverse["market_room"]["room_krw"])
        reserved = self.build(candidates=[cand("KRW-A")], snap=snapshot(reservations=[
            {"market": "CRYPTO", "instrument": "KRW-A", "order_id": "o1", "reserved_krw": "3000000"},
            {"market": "KR", "instrument": "005930", "order_id": "o2", "reserved_krw": "1000000"}]))
        self.assertEqual(reserved["market_room"]["load_krw"], "3000000")
        self.assertEqual(reserved["market_room"]["available_cash_krw"], "196000000")
        self.assertEqual(reserved["allocation"][0]["held_krw"], "3000000")

    def test_canon_us_example_with_verified_fx(self):
        us = lambda i: cand(f"US{i}", adv="100000000000", window="20_SESSIONS")  # USD
        rec = self.build(market="US", session_id="US-2026-09-21", candidates=[us(i) for i in range(7)],
                         snap=snapshot(fx=fx()), at="2026-09-21T12:00:00Z",
                         env=ENV.allocation_envelope(self.core, decision_at_utc="2026-09-21T12:00:00Z",
                                                     market_states={"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_ON"},
                                                     unknown_streaks=dict(ZERO), drawdown_stage="NONE",
                                                     cross_market_flow_validated=False, evidence_mode="NATURAL"))
        self.assertEqual(rec["market_room"]["cap_krw"], "80000000")
        self.assertEqual(F(rec["market_room"]["budget_krw"]), F(80000000, 3))
        self.assertEqual(F(rec["allocation"][0]["allocated_krw"]), F(80000000, 21))
        self.assertIn("RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1", {r["rule_id"] for r in rec["rule_refs"]})
        thin = self.build(market="US", session_id="US-2026-09-21", at="2026-09-21T12:00:00Z", snap=snapshot(fx=fx()),
                          env=rec["inputs"]["envelope"], candidates=[cand("THIN", adv="100000", window="20_SESSIONS"), us(0)])
        lines = {l["instrument"]: l for l in thin["allocation"]}
        self.assertEqual(F(lines["THIN"]["liquidity_room_krw"]), F(1000) * F(fx()["krw_per_usd"]))  # 1% of USD ADV in KRW
        self.assertEqual(lines["THIN"]["bound_by"], "LIQUIDITY_ROOM")

    def test_fx_staleness_counts_from_publication_and_never_blocks(self):
        at = "2026-10-05T12:00:00Z"  # 11 weekdays after the 2026-09-18 publication date
        env = ENV.allocation_envelope(self.core, decision_at_utc=at, market_states={"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_ON"},
                                      unknown_streaks=dict(ZERO), drawdown_stage="NONE", cross_market_flow_validated=False,
                                      evidence_mode="NATURAL")
        snap = snapshot(fx=fx(), holdings=[holding("US", "SPY", "1000")], as_of="2026-10-05T11:00:00Z")
        stale = self.build(market="US", session_id="US-2026-10-05", candidates=[cand("SPY", window="20_SESSIONS")],
                           snap=snap, env=env, at=at)
        self.assertEqual(stale["nav0"]["fx"]["status"], "STALE")
        self.assertEqual(stale["nav0"]["fx"]["display_ko"], "NAV 일부 미검증")
        self.assertEqual(stale["nav0"]["nav_verification"], "UNVERIFIED")
        self.assertEqual(stale["status"], "ALLOCATED")  # display only, allocation unaffected
        self.assertFalse(any("FX" in reason for reason in stale["reasons"]))
        # Counted from publication (availability), not observation date.
        self.assertEqual(BUD.evaluate_fx(self.core, fx(), "2026-10-02T12:00:00Z")["status"], "VERIFIED")
        old_observation = fx(date="2026-09-01", published="2026-09-18T21:15:00Z")
        self.assertEqual(BUD.evaluate_fx(self.core, old_observation, "2026-10-02T12:00:00Z")["business_days_since_publication"], 10)
        self.assertEqual(self.core.interpretations["fx_conversion"]["staleness_clock_kind"], "CIO_INTERPRETATION_NOT_USER_RATIFIED")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "FX_NOT_PUBLISHED_BEFORE_DECISION"):
            BUD.evaluate_fx(self.core, fx(published="2026-10-01T12:00:00Z"), "2026-10-01T12:00:00Z")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "FX_SERIES_NOT_RATIFIED"):
            BUD.evaluate_fx(self.core, {**fx(), "series": "ECB:KRW"}, "2026-10-01T12:00:00Z")

    def test_embedded_envelope_must_rederive_and_match_decision_time(self):
        forged = copy.deepcopy(self.env)
        forged["markets"]["CRYPTO"]["cap_fraction"] = "1"
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "ENVELOPE_NOT_REDERIVABLE"):
            self.build(candidates=[cand("KRW-A")], env=CORE.sign(forged, "record_sha256"))
        old_at = "2026-09-16T07:30:00Z"
        stale_env = ENV.allocation_envelope(self.core, decision_at_utc=old_at, market_states={"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_ON"},
                                            unknown_streaks=dict(ZERO), drawdown_stage="NONE",
                                            cross_market_flow_validated=False, evidence_mode="NATURAL")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "ENVELOPE_DECISION_TIME_MISMATCH"):
            self.build(candidates=[cand("KRW-A")], env=stale_env)

    def test_replay_uses_registry_snapshot_after_rows_are_added(self):
        rec = self.build(candidates=[cand("KRW-A")])
        sha = rec["rule_refs"][0]["registry_sha256"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            shutil.copy(ROOT / CORE.CONFIG_RELATIVE_PATH, root / CORE.CONFIG_RELATIVE_PATH)
            registry = json.loads((ROOT / REG.REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8"))
            added = copy.deepcopy(registry["rules"][0])
            added["rule_id"], added["lineage_key"] = "RULE.LATER.ADDED.V1", "RULE.LATER.ADDED"
            registry["rules"].append(added)
            (root / REG.REGISTRY_RELATIVE_PATH).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n")
            with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "REGISTRY_SNAPSHOT_UNAVAILABLE"):
                BUD.validate_session_budget_record(rec, root=root)
            snapshot_path = root / CORE.snapshot_relative_path(sha)
            snapshot_path.parent.mkdir(parents=True)
            shutil.copy(ROOT / CORE.snapshot_relative_path(sha), snapshot_path)
            self.assertEqual(BUD.validate_session_budget_record(rec, root=root), rec)
            snapshot_path.write_bytes(gzip.compress(gzip.decompress(snapshot_path.read_bytes()) + b" "))
            CORE._load_replay_cached.cache_clear()
            with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "REGISTRY_SNAPSHOT_SHA_MISMATCH"):
                BUD.validate_session_budget_record(rec, root=root)
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "CONFIG_VERSION_UNAVAILABLE"):
            BUD.validate_session_budget_record(CORE.sign(dict(rec, config_sha256="0" * 64), "record_sha256"))

    def test_state_denial_unverified_market_and_quantities(self):
        denied = self.build(candidates=[cand("KRW-A")], env=envelope(self.core, {"US": "RISK_ON", "KR": "RISK_ON", "CRYPTO": "RISK_OFF"}))
        self.assertEqual(denied["market_room"]["budget_krw"], "0")
        self.assertIn(("RULE.ALLOCATION.V2", "BLOCKED_BY"), {(r["rule_id"], r["role"]) for r in denied["rule_refs"]})
        # Was MARKET_HOLDING_VALUATION_UNVERIFIED (this market's own holding only);
        # the user ratified the all-markets reading on 2026-09-18, so the reason now
        # names every unverified market (see the dedicated test below).
        unverified = self.build(candidates=[cand("KRW-A")], snap=snapshot(holdings=[holding("CRYPTO", "KRW-X", None, last="1000")]))
        self.assertIn("NEW_BUYS_BLOCKED_HOLDING_VALUATION_UNVERIFIED_IN_CRYPTO", unverified["reasons"])
        qty = self.build(candidates=[cand("KRW-BTC", price="150000000", step="0.0001", fee="0.0005"),
                                     cand("KRW-Z", price="999999999999", step="1", fee="0")])
        lines = {l["instrument"]: l for l in qty["allocation"]}
        self.assertLessEqual(F(lines["KRW-BTC"]["submitted_amount_krw"]), F(lines["KRW-BTC"]["allocated_krw"]))
        self.assertEqual(F(lines["KRW-BTC"]["quantity"]) % F("0.0001"), 0)
        self.assertIn("QUANTITY_ROUNDS_TO_ZERO_SKIPPED", lines["KRW-Z"]["reasons"])
        self.assertIn("RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1", {r["rule_id"] for r in qty["rule_refs"]})
        self.assertEqual(BUD.validate_session_budget_record(qty), qty)
        tampered = copy.deepcopy(qty)
        tampered["market_room"]["budget_krw"] = "99999999"
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "SHA_MISMATCH"):
            BUD.validate_session_budget_record(tampered)
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "NOT_REDERIVABLE"):
            BUD.validate_session_budget_record(CORE.sign(tampered, "record_sha256"))

    def test_unverified_holding_in_another_market_denies_new_buys_everywhere(self):
        # User ratification 2026-09-18 23:10 KST: "보유분 평가가 확인되지 않으면 해당 시장뿐
        # 아니라 모든 시장의 신규 매수를 금지한다."  New buys only: NAV, exits and
        # releases are unchanged.
        verified = [holding("CRYPTO", "KRW-A", "1000"), holding("KR", "005930", "1000")]
        permitted = self.build(candidates=[cand("KRW-A")], snap=snapshot(holdings=verified))
        self.assertEqual(permitted["reasons"], [])
        self.assertEqual(permitted["status"], "ALLOCATED")
        # CRYPTO's own holding is verified; KR's is not, and CRYPTO is denied all the same.
        cross = [holding("CRYPTO", "KRW-A", "1000"), holding("KR", "005930", None, last="1000")]
        denied = self.build(candidates=[cand("KRW-A")], snap=snapshot(holdings=cross))
        self.assertIn("NEW_BUYS_BLOCKED_HOLDING_VALUATION_UNVERIFIED_IN_KR", denied["reasons"])
        self.assertEqual(denied["market_room"]["budget_krw"], "0")
        self.assertEqual(denied["status"], "NO_ALLOCATION")
        self.assertEqual(denied["nav0"]["unverified_markets"], ["KR"])
        # NAV0 itself is untouched: the last verified value still counts, flagged.
        self.assertEqual(denied["nav0"]["nav0_krw"], permitted["nav0"]["nav0_krw"])
        self.assertEqual(denied["nav0"]["status"], "KNOWN")
        self.assertEqual(denied["nav0"]["nav_verification"], "UNVERIFIED")
        self.assertEqual(BUD.validate_session_budget_record(denied), denied)
        both = self.build(candidates=[cand("KRW-A")], snap=snapshot(
            holdings=[holding("CRYPTO", "KRW-A", None, last="1000"), holding("KR", "005930", None, last="1000")]))
        self.assertIn("NEW_BUYS_BLOCKED_HOLDING_VALUATION_UNVERIFIED_IN_CRYPTO_KR", both["reasons"])

    def test_ledger_one_allocation_restart_reuse_and_consumption(self):
        ledger = BUD.SessionBudgetLedger(self.core)
        inputs = dict(market="CRYPTO", session_id=self.sid, decision_at_utc=T, nav_snapshot=snapshot(),
                      envelope=self.env, candidates=[cand("KRW-A"), cand("KRW-B")])
        rec, reused = ledger.allocate_or_reuse(**inputs)
        self.assertFalse(reused)
        # Restart mid-session with smaller room: the recorded budget is reused, never recomputed.
        changed = dict(inputs, nav_snapshot=snapshot(holdings=[holding("CRYPTO", "KRW-A", "10000000")], cash="190000000"))
        again, reused = BUD.SessionBudgetLedger(self.core, ledger.events).allocate_or_reuse(**changed)
        self.assertTrue(reused)
        self.assertEqual(again, rec)
        budget = F(rec["market_room"]["budget_krw"])
        self.assertEqual(ledger.consume(rec, "o1", CORE.fstr(budget / 2)), "APPENDED")
        self.assertEqual(ledger.consume(rec, "o1", CORE.fstr(budget / 2)), "UNCHANGED_SAME_ORDER")
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "ORDER_ID_CONFLICT"):
            ledger.consume(rec, "o1", "1")
        ledger.cancel(rec, "o1")
        self.assertEqual(F(ledger.remaining_krw(rec)), budget / 2)  # cancel never restores
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "SESSION_BUDGET_EXCEEDED"):
            ledger.consume(rec, "o2", CORE.fstr(budget / 2 + 1))
        replay = BUD.SessionBudgetLedger(self.core, ledger.events)
        self.assertEqual(replay.remaining_krw(rec), ledger.remaining_krw(rec))
        conflict = BUD.build_session_budget_record(self.core, **changed)
        with self.assertRaisesRegex(CORE.PaperExecutionCoreError, "SESSION_BUDGET_KEY_CONFLICT"):
            ledger._event({"event_type": "SESSION_BUDGET_RECORDED", "record": conflict})


if __name__ == "__main__":
    unittest.main()
