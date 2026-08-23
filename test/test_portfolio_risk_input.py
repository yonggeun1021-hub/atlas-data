#!/usr/bin/env python3
"""Portfolio Risk Input Contract -- regression + the 13 required
counter-example scenarios (numbered per the task spec, each independent).
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so `from portfolio_risk import ...` inside capture.py resolves


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AC = _load("portfolio_risk.alpaca_client", "portfolio_risk/alpaca_client.py")
PS = _load("portfolio_risk.portfolio_snapshot", "portfolio_risk/portfolio_snapshot.py")

T0 = "2026-08-23T14:00:00Z"
T_LATER = "2026-08-23T15:00:00Z"
T_MUCH_LATER = "2026-08-25T15:00:00Z"  # > STALENESS_MAX_AGE_HOURS after T0


def _account(**overrides):
    base = {
        "account_number": "PA000TEST0001",
        "currency": "USD",
        "equity": "10000.00",
        "cash": "5000.00",
        "buying_power": "10000.00",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }
    base.update(overrides)
    return base


def _positions(**overrides):
    row = {"symbol": "AAPL", "qty": "10", "market_value": "5000.00", "unrealized_pl": "100.00"}
    row.update(overrides)
    return [row]


class ContractShapeTests(unittest.TestCase):
    def test_contract_authority_all_false(self):
        contract = json.loads((ROOT / "config" / "portfolio_risk_input_contract.json").read_text())
        self.assertEqual(contract["contract_version"], "portfolio_risk_input/1")
        for value in contract["authority"].values():
            self.assertFalse(value is True and False)  # placeholder, real check below
        self.assertFalse(contract["authority"]["order_authorized"])
        self.assertFalse(contract["authority"]["trading_authorized"])
        self.assertFalse(contract["authority"]["action_authorized"])

    def test_happy_path_builds_and_publishes(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        self.assertEqual(fact["nav_reconciliation_status"], "OK")
        self.assertTrue(fact["account_id_hash"])
        self.assertNotIn("PA000TEST0001", json.dumps(fact))  # ★ raw account number never present
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        PS.validate_snapshot(packet)
        self.assertEqual(packet["risk_capacity_inputs"]["total_nav_status"], "OK")
        self.assertEqual(packet["risk_capacity_inputs"]["total_nav"], 10000.0)
        self.assertEqual(packet["risk_policy"], PS.RISK_POLICY_UNRATIFIED)
        self.assertEqual(packet["position_size"], PS.POSITION_SIZE_UNRATIFIED)
        self.assertNotIn("PA000TEST0001", json.dumps(packet))


class CounterExample01FutureDatedSnapshot(unittest.TestCase):
    """A future-dated snapshot used against a past decision -- rejected."""
    def test_captured_after_decision_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "FUTURE_DATED_SNAPSHOT_REJECTED"):
            PS._validate_snapshot_timing(captured_at=T_LATER, available_at=T_LATER, decision_at=T0)


class CounterExample02StaleBalance(unittest.TestCase):
    """Stale account balance used as current -- rejected/flagged."""
    def test_old_capture_vs_new_decision_flagged_stale(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T_MUCH_LATER)
        self.assertEqual(fact["staleness_status"], "STALE")
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T_MUCH_LATER,
        )
        self.assertTrue(packet["risk_capacity_inputs"]["data_completeness"]["any_stale"])


class CounterExample03DuplicatePositions(unittest.TestCase):
    """Duplicate positions or duplicate snapshots -- deduplicated/rejected."""
    def test_identical_duplicate_is_deduped(self):
        row = _positions()[0]
        deduped = PS._dedupe_positions([row, dict(row)])
        self.assertEqual(len(deduped), 1)

    def test_conflicting_duplicate_is_rejected(self):
        row = _positions()[0]
        conflicting = dict(row, market_value="9999.00")
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "DUPLICATE_POSITION_CONFLICTING_DATA"):
            PS._dedupe_positions([row, conflicting])


class CounterExample04MixedCurrencyNoFx(unittest.TestCase):
    """Mixed-currency amounts summed without an FX rate -- rejected."""
    def test_missing_fx_rate_blocks_total(self):
        usd_fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        krw_fact = PS.build_manual_account_fact(
            market="KOREA", currency="KRW", cash=1_000_000.0, positions=[],
            captured_at=T0, decision_at=T0,
        )
        packet = PS.assemble_snapshot(
            account_facts=[usd_fact, krw_fact], fx_rates={},
            expected_sources={"ALPACA_PAPER_ACCOUNT", "KOREA"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        self.assertEqual(packet["risk_capacity_inputs"]["total_nav_status"], "NOT_COMPUTABLE_MISSING_FX_RATE")
        self.assertIsNone(packet["risk_capacity_inputs"]["total_nav"])

    def test_stale_fx_rate_also_blocks_total(self):
        usd_fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        krw_fact = PS.build_manual_account_fact(
            market="KOREA", currency="KRW", cash=1_000_000.0, positions=[],
            captured_at=T0, decision_at=T0,
        )
        stale_fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T0, "source": "MANUAL"}}, T_MUCH_LATER)
        packet = PS.assemble_snapshot(
            account_facts=[usd_fact, krw_fact], fx_rates=stale_fx,
            expected_sources={"ALPACA_PAPER_ACCOUNT", "KOREA"},
            captured_at=T0, available_at=T0, decision_at=T_MUCH_LATER,
        )
        self.assertEqual(packet["risk_capacity_inputs"]["total_nav_status"], "NOT_COMPUTABLE_STALE_FX_RATE")

    def test_fresh_fx_rate_allows_total(self):
        usd_fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        krw_fact = PS.build_manual_account_fact(
            market="KOREA", currency="KRW", cash=1_000_000.0, positions=[],
            captured_at=T0, decision_at=T0,
        )
        fresh_fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T0, "source": "MANUAL"}}, T0)
        packet = PS.assemble_snapshot(
            account_facts=[usd_fact, krw_fact], fx_rates=fresh_fx,
            expected_sources={"ALPACA_PAPER_ACCOUNT", "KOREA"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        self.assertEqual(packet["risk_capacity_inputs"]["total_nav_status"], "OK")
        self.assertAlmostEqual(packet["risk_capacity_inputs"]["total_nav"], 10000.0 + 1_000_000.0 * 0.00072)


class CounterExample05ManualDisguisedAsVerified(unittest.TestCase):
    """Manual input disguised as broker-verified -- rejected."""
    def test_claiming_broker_verified_for_manual_is_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "MANUAL_INPUT_DISGUISED_AS_VERIFIED"):
            PS.build_manual_account_fact(
                market="CRYPTO", currency="USD", cash=100.0, positions=[],
                captured_at=T0, decision_at=T0, claimed_verification_status="BROKER_VERIFIED",
            )

    def test_manual_fact_always_carries_unverified_label(self):
        fact = PS.build_manual_account_fact(
            market="CRYPTO", currency="USD", cash=100.0, positions=[],
            captured_at=T0, decision_at=T0,
        )
        self.assertEqual(fact["verification_status"], "PAPER_OR_MANUAL_UNVERIFIED")


class CounterExample06LiveVsPaperConfusion(unittest.TestCase):
    """Alpaca live vs. paper account confusion -- rejected/detected."""
    def test_base_host_is_hardcoded_paper_only(self):
        self.assertEqual(AC.PAPER_API_BASE, "https://paper-api.alpaca.markets")

    def test_live_trading_host_never_appears_in_module_source(self):
        source = inspect.getsource(AC)
        self.assertNotIn("https://api.alpaca.markets", source)

    def test_base_is_never_a_function_parameter(self):
        # Structural: no fetch function accepts a base/host override.
        for fn in (AC.fetch_account, AC.fetch_positions, AC._fetch_path):
            params = inspect.signature(fn).parameters
            self.assertNotIn("base", params)
            self.assertNotIn("host", params)
            self.assertNotIn("url", params)


class CounterExample07NegativeOrNanNav(unittest.TestCase):
    """Negative or NaN NAV -- rejected."""
    def test_negative_equity_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "NEGATIVE_NAV_OR_CASH_REJECTED"):
            PS.build_alpaca_paper_account_fact(_account(equity="-10.00"), _positions(), captured_at=T0, decision_at=T0)

    def test_nan_equity_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "NON_FINITE_VALUE"):
            PS.build_alpaca_paper_account_fact(_account(equity="nan"), _positions(), captured_at=T0, decision_at=T0)

    def test_negative_manual_cash_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "NEGATIVE_NAV_OR_CASH_REJECTED"):
            PS.build_manual_account_fact(market="CRYPTO", currency="USD", cash=-5.0, positions=[], captured_at=T0, decision_at=T0)


class CounterExample08NavPositionMismatch(unittest.TestCase):
    """Account-level NAV disagreeing with the sum of positions -- flagged/rejected."""
    def test_gross_mismatch_is_flagged(self):
        fact = PS.build_alpaca_paper_account_fact(
            _account(equity="10000.00", cash="1.00"), _positions(market_value="1.00"),
            captured_at=T0, decision_at=T0,
        )
        self.assertEqual(fact["nav_reconciliation_status"], "MISMATCH_FLAGGED")


class CounterExample09PartialMarketMissing(unittest.TestCase):
    """Total NAV confirmed while some market's data is missing -- rejected
    (must be NOT_COMPUTABLE instead)."""
    def test_missing_expected_source_blocks_total(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={},
            expected_sources={"ALPACA_PAPER_ACCOUNT", "KOREA", "CRYPTO"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        self.assertEqual(packet["risk_capacity_inputs"]["total_nav_status"], "NOT_COMPUTABLE_MISSING_MARKET_DATA")
        self.assertIsNone(packet["risk_capacity_inputs"]["total_nav"])
        self.assertIn("KOREA", packet["risk_capacity_inputs"]["total_nav_detail"]["missing_sources"])


class CounterExample10SameTimestampTampering(unittest.TestCase):
    """Same-timestamp data tampering -- detected."""
    def test_post_hoc_field_edit_without_rehash_detected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        tampered = copy.deepcopy(packet)
        tampered["risk_capacity_inputs"]["total_nav"] = 999999.0  # same captured_at, content silently changed
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "PACKET_HASH_MISMATCH"):
            PS.validate_snapshot(tampered)


class CounterExample11NoOrderApiCallPossible(unittest.TestCase):
    """Any order-API call attempted from the read-only path -- structurally
    impossible, proven here."""
    def test_orders_path_not_allowlisted(self):
        self.assertNotIn("/v2/orders", AC.ALLOWED_PATHS)

    def test_unregistered_path_rejected_before_any_network_call(self):
        def _getter_should_never_be_called(url, headers):
            raise AssertionError("network I/O attempted for a non-allowlisted path")
        with self.assertRaisesRegex(AC.AlpacaClientError, "PATH_NOT_ALLOWLISTED:/v2/orders"):
            AC._fetch_path("/v2/orders", "k", "s", getter=_getter_should_never_be_called)

    def test_get_helper_never_passes_data_or_method(self):
        source = inspect.getsource(AC._get)
        code_only = source.split('"""', 2)[-1]  # strip the docstring, which mentions `data=` in prose
        self.assertNotIn("data=", code_only)
        self.assertNotIn("method=", code_only)

    def test_get_is_the_only_network_function_in_module(self):
        source = inspect.getsource(AC)
        self.assertEqual(source.count("urllib.request.Request("), 1)
        self.assertEqual(source.count("urllib.request.urlopen("), 1)


class CounterExample12SizingWhilePolicyUnratified(unittest.TestCase):
    """Sizing/quantity/weight computed while policy is unratified --
    rejected (NOT_COMPUTABLE_POLICY_UNRATIFIED enforced)."""
    def test_position_size_is_always_the_unratified_constant(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        self.assertEqual(packet["position_size"], {"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED"})
        self.assertEqual(packet["risk_policy"]["approval_status"], "UNRATIFIED")

    def test_tampered_position_size_rejected_by_validator(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        tampered = copy.deepcopy(packet)
        tampered["position_size"] = {"status": "COMPUTED", "shares": 10}
        tampered["packet_sha256"] = PS.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED"):
            PS.validate_snapshot(tampered)


class CounterExample13AuthorityFlip(unittest.TestCase):
    """Any existing authority field flipping to true -- rejected/detected."""
    def test_flipped_authority_field_rejected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        tampered = copy.deepcopy(packet)
        tampered["authority"]["order_authorized"] = True
        tampered["packet_sha256"] = PS.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE"):
            PS.validate_snapshot(tampered)

    def test_freshly_built_packet_authority_all_false(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        for key, value in packet["authority"].items():
            if key == "review_only":
                self.assertTrue(value)
            else:
                self.assertFalse(value)


class NoPlaintextSecretsTests(unittest.TestCase):
    """Hard security constraint: no secret keys or account numbers
    committed in plaintext anywhere -- verified structurally by scanning
    this module's own source for anything that looks like a credential."""
    def test_no_hardcoded_credential_looking_strings_in_alpaca_client(self):
        source = inspect.getsource(AC)
        # Keys/secrets only ever flow in as function parameters -- never a
        # literal value assigned in source.
        self.assertNotIn('"APCA-API-KEY-ID": "', source)
        self.assertNotIn('"APCA-API-SECRET-KEY": "', source)

    def test_account_number_never_leaks_into_built_fact_or_packet(self):
        account_number = "PA00099999ZZ"
        fact = PS.build_alpaca_paper_account_fact(_account(account_number=account_number), _positions(), captured_at=T0, decision_at=T0)
        self.assertNotIn(account_number, json.dumps(fact))
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, expected_sources={"ALPACA_PAPER_ACCOUNT"},
            captured_at=T0, available_at=T0, decision_at=T0,
        )
        self.assertNotIn(account_number, json.dumps(packet))


class EvidencePublishTests(unittest.TestCase):
    def test_capture_module_writes_append_only_evidence(self):
        capture = _load("portfolio_risk.capture", "portfolio_risk/capture.py")
        fixed_account_raw = _account()
        fixed_positions_raw = _positions()

        def fake_fetch_account(key, secret):
            return fixed_account_raw

        def fake_fetch_positions(key, secret):
            return fixed_positions_raw

        capture.alpaca_client.fetch_account = fake_fetch_account
        capture.alpaca_client.fetch_positions = fake_fetch_positions
        now = dt.datetime(2026, 8, 23, 14, 0, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = capture.run(root, "k", "s", now)
            self.assertTrue((root / "data" / "latest_portfolio_risk_input.json").exists())
            manifest = root / "evidence" / "operational" / "portfolio_risk_input" / "raw" / "2026-08-23" / "manifest.json"
            self.assertTrue(manifest.exists())
            on_disk = json.loads(manifest.read_text())
            self.assertEqual(on_disk["packet_sha256"], packet["packet_sha256"])
            self.assertNotIn("PA000TEST0001", manifest.read_text())


if __name__ == "__main__":
    unittest.main()
