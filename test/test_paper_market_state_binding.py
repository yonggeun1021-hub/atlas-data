#!/usr/bin/env python3
"""The PAPER market-state source binding: per-market, fail closed, sizing only.

Every test here is cause-based: the binding contract is mutated and the
consequence is measured, rather than pinning today's US/KR/CRYPTO verdicts.  The
three properties under test are the ones the ratification asked for:

1. ``runtime_regime`` is *derived*, not a literal -- so a market the binding
   opens carries its own reference judgement, and the same code with that market
   closed carries UNKNOWN over the identical input bytes.
2. Markets are independent -- closing one never closes another, opening one
   never opens another.
3. The default is closed -- missing file, unratified status, missing market,
   non-boolean flag, unmaterialized authority record, and a tampered authority
   record all end in UNKNOWN (or a hard failure), never in an open market.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio import paper_allocation_envelope as ENV  # noqa: E402
from portfolio import paper_execution_core as CORE  # noqa: E402
from regime import paper_market_state_binding as MODULE  # noqa: E402
from regime import paper_regime_reference as PRR  # noqa: E402


ALL_MARKETS = ("CRYPTO", "KR", "US")
DECISION_AT = "2026-09-19T07:00:00Z"


def reference_packet() -> dict:
    return json.loads(
        (ROOT / MODULE.REFERENCE_RELATIVE).read_text(encoding="utf-8")
    )


class BindingContractTest(unittest.TestCase):
    def setUp(self):
        self.binding = MODULE.load_binding(ROOT)

    def test_repository_binding_loads_and_authorizes_only_a_sizing_input(self):
        authority = self.binding["authority"]
        self.assertIs(
            authority["paper_market_state_sizing_input_authorized"], True
        )
        for key in MODULE.FORBIDDEN_AUTHORITY:
            self.assertIs(authority[key], False, key)

    def test_every_market_row_is_explicit_and_a_closed_row_says_why(self):
        markets = self.binding["markets"]
        self.assertEqual(sorted(markets), sorted(ALL_MARKETS))
        for market, row in sorted(markets.items()):
            self.assertIn(row["adopted"], (True, False), market)
            if row["adopted"] is False:
                self.assertIsInstance(row["blocked_reason"], str, market)
                self.assertTrue(row["blocked_reason"], market)

    def test_crypto_stays_closed_and_names_the_input_it_is_waiting_for(self):
        """Crypto is blocked on elapsed evidence days, which code cannot supply."""
        crypto = self.binding["markets"]["CRYPTO"]
        self.assertIs(crypto["adopted"], False)
        self.assertIs(crypto["must_not_be_forced"], True)
        self.assertIn("2026-09-23", crypto["unblocks_when"])
        decision = json.loads(
            (ROOT / "data" / "latest_crypto_paper_runtime_decision.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(decision["decision_status"], "BLOCKED")
        self.assertEqual(decision["acceptance"]["status"], "NOT_ACCEPTED")
        self.assertNotIn("CRYPTO", MODULE.load_adopted_markets(ROOT))


class FailClosedTest(unittest.TestCase):
    """A synthetic root is mutated one field at a time; closed must win."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "evidence" / "authority").mkdir(parents=True)
        shutil.copy2(
            ROOT / MODULE.BINDING_RELATIVE, self.root / MODULE.BINDING_RELATIVE
        )
        record = json.loads(
            (self.root / MODULE.BINDING_RELATIVE).read_text(encoding="utf-8")
        )["authority_record"]
        shutil.copy2(ROOT / record["path"], self.root / record["path"])
        self.addCleanup(self.tmp.cleanup)

    def binding(self) -> dict:
        return json.loads(
            (self.root / MODULE.BINDING_RELATIVE).read_text(encoding="utf-8")
        )

    def write(self, binding: dict) -> None:
        (self.root / MODULE.BINDING_RELATIVE).write_text(
            json.dumps(binding, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_the_unmutated_synthetic_root_opens_the_same_markets(self):
        self.assertEqual(
            MODULE.load_adopted_markets(self.root), MODULE.load_adopted_markets(ROOT)
        )

    def test_missing_binding_file_closes_every_market(self):
        (self.root / MODULE.BINDING_RELATIVE).unlink()
        self.assertEqual(MODULE.load_adopted_markets(self.root), frozenset())

    def test_authority_record_absent_from_the_root_closes_every_market(self):
        record = self.binding()["authority_record"]
        (self.root / record["path"]).unlink()
        self.assertEqual(MODULE.load_adopted_markets(self.root), frozenset())
        forced = MODULE.load_binding(self.root)
        self.assertEqual(
            forced["forced_closed_reason"], "AUTHORITY_RECORD_NOT_MATERIALIZED_IN_ROOT"
        )

    def test_tampered_authority_record_is_a_hard_failure_not_a_default(self):
        record = self.binding()["authority_record"]
        path = self.root / record["path"]
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.PaperMarketStateBindingError,
            "BINDING_AUTHORITY_RECORD_SHA_MISMATCH",
        ):
            MODULE.load_binding(self.root)

    def test_unratified_status_fails_closed(self):
        binding = self.binding()
        binding["status"] = "DRAFT_NOT_RATIFIED"
        self.write(binding)
        with self.assertRaisesRegex(
            MODULE.PaperMarketStateBindingError, "BINDING_NOT_RATIFIED"
        ):
            MODULE.load_binding(self.root)

    def test_a_market_removed_from_the_contract_is_a_closed_market(self):
        binding = self.binding()
        del binding["markets"]["US"]
        self.write(binding)
        self.assertNotIn("US", MODULE.load_adopted_markets(self.root))
        self.assertIn("KR", MODULE.load_adopted_markets(self.root))

    def test_non_boolean_adopted_is_refused_rather_than_read_as_truthy(self):
        for value in ("true", 1, [], {}, None):
            with self.subTest(value=value):
                binding = self.binding()
                binding["markets"]["US"]["adopted"] = value
                self.write(binding)
                with self.assertRaisesRegex(
                    MODULE.PaperMarketStateBindingError,
                    "BINDING_MARKET_ADOPTED_INVALID",
                ):
                    MODULE.load_binding(self.root)

    def test_any_capital_flag_turned_on_refuses_the_whole_binding(self):
        for key in MODULE.FORBIDDEN_AUTHORITY:
            with self.subTest(flag=key):
                binding = self.binding()
                binding["authority"][key] = True
                self.write(binding)
                with self.assertRaisesRegex(
                    MODULE.PaperMarketStateBindingError, "BINDING_AUTHORITY_ESCALATION"
                ):
                    MODULE.load_binding(self.root)

    def test_an_unknown_market_name_is_refused(self):
        binding = self.binding()
        binding["markets"]["FX"] = {"adopted": True}
        self.write(binding)
        with self.assertRaisesRegex(
            MODULE.PaperMarketStateBindingError, "BINDING_MARKET_UNKNOWN"
        ):
            MODULE.load_binding(self.root)


class DerivationTest(unittest.TestCase):
    def test_closed_market_keeps_unknown_whatever_it_classified(self):
        for state in MODULE.STATES:
            for market in ALL_MARKETS:
                with self.subTest(market=market, candidate=state):
                    self.assertEqual(
                        MODULE.runtime_regime(market, state, frozenset()), "UNKNOWN"
                    )

    def test_open_market_carries_exactly_its_own_judgement(self):
        for state in MODULE.STATES:
            self.assertEqual(
                MODULE.runtime_regime("US", state, frozenset({"US"})), state
            )

    def test_markets_are_independent(self):
        adopted = frozenset({"KR"})
        self.assertEqual(MODULE.runtime_regime("KR", "RISK_ON", adopted), "RISK_ON")
        self.assertEqual(MODULE.runtime_regime("US", "RISK_ON", adopted), "UNKNOWN")
        self.assertEqual(MODULE.runtime_regime("CRYPTO", "RISK_ON", adopted), "UNKNOWN")

    def test_a_state_outside_the_ratified_vocabulary_closes_the_market(self):
        for bogus in ("RISK_ONN", "", None, 3, "risk_on"):
            with self.subTest(candidate=bogus):
                self.assertEqual(
                    MODULE.runtime_regime("US", bogus, frozenset({"US"})), "UNKNOWN"
                )

    def test_producer_derives_the_field_instead_of_writing_a_literal(self):
        """The same bytes produce UNKNOWN or the judgement, depending only on the
        binding -- which is what the removed hardcoded literal made impossible."""
        policy = PRR.read_json(PRR.POLICY_PATH, "POLICY_INVALID")
        us_source = PRR.read_json(PRR.US_PATH, "US_SOURCE_INVALID")
        closed = PRR.build_us(us_source, policy)
        opened = PRR.build_us(us_source, policy, adopted=frozenset({"US"}))
        self.assertEqual(closed["runtime_regime"], "UNKNOWN")
        self.assertEqual(
            opened["runtime_regime"], opened["paper_reference"]["candidate_regime"]
        )
        self.assertEqual(
            closed["paper_reference"], opened["paper_reference"],
            "the judgement itself must not change with the binding",
        )
        # Opening KR must not open US over the same bytes.
        other = PRR.build_us(us_source, policy, adopted=frozenset({"KR"}))
        self.assertEqual(other["runtime_regime"], "UNKNOWN")

    def test_default_caller_of_the_builders_stays_closed(self):
        """Historical and replay callers pass no adopted set and must not move."""
        policy = PRR.read_json(PRR.POLICY_PATH, "POLICY_INVALID")
        kr = PRR.build_kr(PRR.read_json(PRR.KR_PATH, "KR_SOURCE_INVALID"), policy)
        self.assertEqual(kr["runtime_regime"], "UNKNOWN")


class EnvelopeWiringTest(unittest.TestCase):
    """The production call that did not exist before this module."""

    def setUp(self):
        self.core = CORE.load_core()
        self.packet = reference_packet()

    def envelope(self, adopted: frozenset, states=None, streaks=None) -> dict:
        states = states or MODULE.market_states(self.packet, adopted)
        streaks = streaks or {m: (20 if states[m] == "UNKNOWN" else 0) for m in ALL_MARKETS}
        return ENV.allocation_envelope(
            self.core,
            decision_at_utc=DECISION_AT,
            market_states=states,
            unknown_streaks=streaks,
            drawdown_stage="NONE",
            cross_market_flow_validated=False,
            evidence_mode="NATURAL",
        )

    def test_market_states_covers_exactly_the_envelope_markets(self):
        states = MODULE.market_states(self.packet, MODULE.load_adopted_markets(ROOT))
        self.assertEqual(sorted(states), sorted(CORE.MARKETS))

    def test_closed_binding_reproduces_the_pre_change_envelope(self):
        """Everything closed is the 0.4500 / 60-cells-DENY baseline the gate had."""
        record = self.envelope(frozenset())
        total = sum(
            Fraction(record["markets"][m]["cap_fraction"]) for m in ALL_MARKETS
        )
        self.assertEqual(total, Fraction(45, 100))
        for m in ALL_MARKETS:
            self.assertEqual(record["markets"][m]["new_buys"], "DENY", m)

    def test_opening_a_market_only_moves_that_market(self):
        closed = self.envelope(frozenset())
        states = MODULE.market_states(self.packet, frozenset({"KR"}))
        opened = self.envelope(frozenset({"KR"}), states=states)
        self.assertEqual(
            closed["markets"]["US"]["cap_fraction"],
            opened["markets"]["US"]["cap_fraction"],
        )
        self.assertEqual(
            closed["markets"]["CRYPTO"]["cap_fraction"],
            opened["markets"]["CRYPTO"]["cap_fraction"],
        )
        if states["KR"] != "UNKNOWN":
            self.assertNotEqual(
                closed["markets"]["KR"]["cap_fraction"],
                opened["markets"]["KR"]["cap_fraction"],
            )

    def test_envelope_from_reference_is_rederivable_and_grants_no_capital(self):
        bound = MODULE.envelope_from_reference(decision_at_utc=DECISION_AT, root=ROOT)
        ENV.validate_envelope(copy.deepcopy(bound["envelope"]), root=ROOT)
        self.assertEqual(
            bound["market_states"],
            MODULE.market_states(reference_packet(), MODULE.load_adopted_markets(ROOT)),
        )
        self.assertEqual(
            bound["binding"]["adopted_markets"],
            sorted(MODULE.load_adopted_markets(ROOT)),
        )
        for key, value in bound["authority"].items():
            if key != "paper_market_state_sizing_input_authorized":
                self.assertIs(value, False, key)

    def test_unknown_streaks_agree_with_the_envelope_precondition(self):
        """``effective_market_state`` fails unless streak > 0 exactly on UNKNOWN."""
        adopted = MODULE.load_adopted_markets(ROOT)
        history = MODULE.state_history(ROOT, adopted)
        states = MODULE.market_states(self.packet, adopted)
        streaks = MODULE.unknown_streaks(history, states)
        for market in ALL_MARKETS:
            self.assertEqual(
                streaks[market] > 0, states[market] == "UNKNOWN", market
            )

    def test_observation_shape_matches_the_benchmark_consumer_contract(self):
        from validation import paper_benchmark_nav_series as NAV

        observations = MODULE.market_state_observations(
            root=ROOT,
            observed_at="2026-09-19T06:00:00Z",
            available_at="2026-09-19T06:30:00Z",
        )
        for market, row in sorted(observations.items()):
            self.assertEqual(
                sorted(row), sorted(NAV.MARKET_STATE_OBSERVATION_FIELDS), market
            )
            self.assertEqual(row["source_ref"], MODULE.REFERENCE_RELATIVE, market)


class WiringIsNotADispatchTest(unittest.TestCase):
    def test_this_module_is_wired_into_no_workflow(self):
        """Sizing input only: nothing schedules this module, so nothing it does
        can reach a workflow that publishes or trades."""
        hits = [
            path.name
            for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
            if "paper_market_state_binding" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(hits, [])

    def test_no_capital_authority_flag_is_ever_true_in_the_binding_or_record(self):
        forbidden = (
            "real_trading", "capital_authorized", "order_authorized",
            "buy_authorized", "trading_authorized", "position_size_authorized",
            "target_weight_authorized", "exchange_order_authorized",
            "real_capital_authorized",
        )
        for relative in (
            MODULE.BINDING_RELATIVE,
            "evidence/authority/USER_RATIFICATION_MARKET_STATE_SOURCE_BINDING_20260919.json",
        ):
            value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            found = []

            def walk(node):
                if isinstance(node, dict):
                    for key, child in node.items():
                        if key in forbidden and child is True:
                            found.append(f"{relative}:{key}")
                        walk(child)
                elif isinstance(node, list):
                    for child in node:
                        walk(child)

            walk(value)
            self.assertEqual(found, [], relative)


if __name__ == "__main__":
    unittest.main(verbosity=1)
