#!/usr/bin/env python3
"""Portfolio Risk Input Contract -- regression + the 13 required
counter-example scenarios, PLUS:
  - Round 1's 6 CIO-review fixes (account-number sanitization utility,
    multi-currency FX-safe totals, stale/mismatch forcing the whole risk
    block NOT_COMPUTABLE, a non-caller-suppliable Account Scope Registry,
    semantic re-derivation in the validator).
  - Round 2's fixes: this repo is PUBLIC, so `capture.py` NEVER writes any
    file (real or redacted) to disk at all -- it builds/verifies a real
    packet purely in memory and returns/prints only an explicitly
    allowlisted, non-sensitive summary. Round 2 also fixed and permanently
    regression-locked 4 real PIT defects (see the
    `CIORound2PitReproduction0*` classes below, named after the CIO's own
    reproduction scenarios).
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import inspect
import json
from pathlib import Path
import sys
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
T_EARLIER = "2026-08-01T00:00:00Z"

REAL_ACCOUNT_NUMBER = "PA000TEST0001"
REAL_EQUITY = "10000.00"
REAL_CASH = "5000.00"


def _account(**overrides):
    base = {
        "id": "11111111-2222-3333-4444-555555555555",
        "account_number": REAL_ACCOUNT_NUMBER,
        "currency": "USD",
        "equity": REAL_EQUITY,
        "cash": REAL_CASH,
        "buying_power": "10000.00",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }
    base.update(overrides)
    return base


def _positions(**overrides):
    row = {
        "asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
        "symbol": "AAPL", "qty": "10", "market_value": "5000.00", "unrealized_pl": "100.00",
    }
    row.update(overrides)
    return [row]


def _build_packet(account_facts, fx_rates=None, captured_at=T0, available_at=T0, decision_at=T0):
    return PS.assemble_snapshot(
        account_facts=account_facts, fx_rates=fx_rates or {},
        captured_at=captured_at, available_at=available_at, decision_at=decision_at,
    )


class ContractShapeTests(unittest.TestCase):
    def test_contract_authority_all_false(self):
        contract = json.loads((ROOT / "config" / "portfolio_risk_input_contract.json").read_text())
        self.assertEqual(contract["contract_version"], "portfolio_risk_input/1")
        self.assertFalse(contract["authority"]["order_authorized"])
        self.assertFalse(contract["authority"]["trading_authorized"])
        self.assertFalse(contract["authority"]["action_authorized"])

    def test_happy_path_builds_and_publishes(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        self.assertEqual(fact["nav_reconciliation_status"], "OK")
        self.assertTrue(fact["account_id_hash"])
        self.assertNotIn(REAL_ACCOUNT_NUMBER, json.dumps(fact))  # ★ raw account number never present
        packet = _build_packet([fact])
        PS.validate_snapshot(packet)
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["status"], "COMPUTABLE")  # single, fully broker-verified account
        self.assertEqual(risk["account_scope_label"], "US_PAPER_ACCOUNT_SCOPE_ONLY")
        self.assertEqual(risk["connected_scope_nav"], 10000.0)
        self.assertEqual(risk["full_portfolio_nav_status"], "NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE")
        self.assertEqual(packet["risk_policy"], PS.RISK_POLICY_UNRATIFIED)
        self.assertEqual(packet["position_size"], PS.POSITION_SIZE_UNRATIFIED)
        self.assertNotIn(REAL_ACCOUNT_NUMBER, json.dumps(packet))


class CounterExample01FutureDatedSnapshot(unittest.TestCase):
    """A future-dated snapshot used against a past decision -- rejected."""
    def test_captured_after_decision_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "FUTURE_DATED_SNAPSHOT_REJECTED"):
            PS._validate_snapshot_timing(captured_at=T_LATER, available_at=T_LATER, decision_at=T0)


class CounterExample02StaleBalance(unittest.TestCase):
    """Stale account balance used as current -- rejected: forces the WHOLE
    risk_capacity_inputs block to NOT_COMPUTABLE, not just a flag."""
    def test_old_capture_vs_new_decision_forces_whole_block_not_computable(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T_MUCH_LATER)
        self.assertEqual(fact["staleness_status"], "STALE")
        packet = _build_packet([fact], decision_at=T_MUCH_LATER)
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["status"], "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT")
        self.assertTrue(risk["data_completeness"]["any_stale"])
        for field in ("connected_scope_nav", "full_portfolio_nav", "total_cash_base_currency",
                      "gross_exposure_base_currency", "net_exposure_base_currency", "existing_position_count"):
            self.assertIsNone(risk[field], field)
        self.assertEqual(risk["exposure_by_ticker"], [])
        self.assertEqual(risk["cash_by_currency"], [])


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
    """Mixed-currency amounts summed without an FX rate -- rejected. Only
    the explicit `*_base_currency` fields are ever cross-currency totals,
    following the exact same missing/stale-FX rule as NAV. NOTE: the KRW
    account here is a manual fact, so `status` is
    `DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT` (not `COMPUTABLE`) once
    both facts are fresh/reconciled -- see CIO round 2 PIT fix 4."""
    def _two_currency_facts(self):
        usd_fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        krw_fact = PS.build_manual_account_fact(
            market="KOREA", currency="KRW", cash=1_000_000.0,
            positions=[{"symbol": "005930", "qty": "1", "market_value": "80000.0"}],
            captured_at=T0, decision_at=T0,
        )
        return usd_fact, krw_fact

    def test_missing_fx_rate_blocks_base_currency_totals_but_never_blends_raw(self):
        usd_fact, krw_fact = self._two_currency_facts()
        packet = _build_packet([usd_fact, krw_fact])
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["status"], "DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT")  # manual fact present
        self.assertEqual(risk["connected_scope_nav_status"], "NOT_COMPUTABLE_MISSING_FX_RATE")
        self.assertIsNone(risk["connected_scope_nav"])
        self.assertEqual(risk["total_cash_base_currency_status"], "NOT_COMPUTABLE_MISSING_FX_RATE")
        self.assertIsNone(risk["total_cash_base_currency"])
        self.assertEqual(risk["gross_exposure_base_currency_status"], "NOT_COMPUTABLE_MISSING_FX_RATE")
        self.assertIsNone(risk["gross_exposure_base_currency"])
        by_currency = {row["currency"]: row["market_value"] for row in risk["exposure_by_currency"]}
        self.assertEqual(by_currency["USD"], 5000.0)
        self.assertEqual(by_currency["KRW"], 80000.0)
        cash_by_currency = {row["currency"]: row["amount"] for row in risk["cash_by_currency"]}
        self.assertEqual(cash_by_currency["USD"], 5000.0)
        self.assertEqual(cash_by_currency["KRW"], 1_000_000.0)

    def test_stale_fx_rate_also_blocks_base_currency_totals(self):
        usd_fact, krw_fact = self._two_currency_facts()
        stale_fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T0, "source": "MANUAL"}}, T_MUCH_LATER)
        packet = _build_packet([usd_fact, krw_fact], fx_rates=stale_fx, decision_at=T_MUCH_LATER)
        # Both facts read STALE relative to the far-future decision_at ->
        # the stale/mismatch gate (priority 1) fires regardless of the
        # unverified-source downgrade (priority 2).
        self.assertEqual(packet["risk_capacity_inputs"]["status"], "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT")

    def test_stale_fx_rate_alone_blocks_totals_when_accounts_are_fresh(self):
        usd_fact, krw_fact = self._two_currency_facts()
        stale_fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T_EARLIER}}, T0)
        packet = _build_packet([usd_fact, krw_fact], fx_rates=stale_fx)
        self.assertEqual(packet["risk_capacity_inputs"]["status"], "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT")

    def test_fresh_fx_rate_allows_base_currency_totals_but_only_as_diagnostic(self):
        usd_fact, krw_fact = self._two_currency_facts()
        fresh_fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T0, "source": "MANUAL"}}, T0)
        packet = _build_packet([usd_fact, krw_fact], fx_rates=fresh_fx)
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["status"], "DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT")
        self.assertEqual(risk["connected_scope_nav_status"], "DIAGNOSTIC_UNVERIFIED")  # downgraded from OK
        self.assertAlmostEqual(risk["connected_scope_nav"], 10000.0 + 1_080_000.0 * 0.00072)
        self.assertAlmostEqual(risk["total_cash_base_currency"], 5000.0 + 1_000_000.0 * 0.00072)
        self.assertEqual(risk["full_portfolio_nav_status"], "NOT_COMPUTABLE_UNVERIFIED_ACCOUNT_SOURCE")


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
        self.assertNotIn("https://api.alpaca.markets", inspect.getsource(AC))

    def test_base_is_never_a_function_parameter(self):
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
    """Account-level NAV disagreeing with the sum of positions -- rejected:
    forces the whole risk_capacity_inputs block to NOT_COMPUTABLE."""
    def test_gross_mismatch_is_flagged_and_forces_block_not_computable(self):
        fact = PS.build_alpaca_paper_account_fact(
            _account(equity="10000.00", cash="1.00"), _positions(market_value="1.00"),
            captured_at=T0, decision_at=T0,
        )
        self.assertEqual(fact["nav_reconciliation_status"], "MISMATCH_FLAGGED")
        packet = _build_packet([fact])
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["status"], "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT")
        self.assertTrue(risk["data_completeness"]["any_nav_reconciliation_mismatch"])
        self.assertIsNone(risk["connected_scope_nav"])


class CounterExample09PartialMarketMissing(unittest.TestCase):
    """Total NAV confirmed while some market's data is missing -- rejected.
    Account scope is a fixed registry (`CANONICAL_ACCOUNT_SCOPE`), never a
    caller-suppliable parameter."""
    def test_alpaca_only_never_presented_as_full_portfolio(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["account_scope_label"], "US_PAPER_ACCOUNT_SCOPE_ONLY")
        self.assertEqual(risk["full_portfolio_nav_status"], "NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE")
        self.assertIsNone(risk["full_portfolio_nav"])
        self.assertEqual(risk["data_completeness"]["missing_sources"], ["CRYPTO", "KOREA"])
        self.assertEqual(risk["connected_scope_nav"], 10000.0)

    def test_assemble_snapshot_has_no_expected_sources_parameter_to_shrink(self):
        params = inspect.signature(PS.assemble_snapshot).parameters
        self.assertNotIn("expected_sources", params)


class CounterExample10SameTimestampTampering(unittest.TestCase):
    """Same-timestamp data tampering -- detected."""
    def test_post_hoc_field_edit_without_rehash_detected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["risk_capacity_inputs"]["connected_scope_nav"] = 999999.0
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)


class CounterExampleReSignedSemanticTamperRejected(unittest.TestCase):
    """A re-signed tamper -- claimed value changed AND the hash regenerated
    to match -- must STILL be rejected, because validate_snapshot()
    independently RE-DERIVES risk_capacity_inputs from portfolio_facts
    (left untouched) and compares field-by-field, rather than only
    checking hash self-consistency."""
    def test_tampered_nav_with_freshly_regenerated_hash_still_rejected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["risk_capacity_inputs"]["connected_scope_nav"] = 999999.0
        tampered["risk_capacity_inputs"]["full_portfolio_nav"] = None
        tampered["packet_sha256"] = PS.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "SEMANTIC_TAMPER_DETECTED"):
            PS.validate_snapshot(tampered)

    def test_tampered_cash_with_freshly_regenerated_hash_still_rejected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["risk_capacity_inputs"]["total_cash_base_currency"] = 0.0
        tampered["packet_sha256"] = PS.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "SEMANTIC_TAMPER_DETECTED"):
            PS.validate_snapshot(tampered)

    def test_tampered_completeness_with_freshly_regenerated_hash_still_rejected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["risk_capacity_inputs"]["data_completeness"]["missing_sources"] = []
        tampered["packet_sha256"] = PS.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "SEMANTIC_TAMPER_DETECTED"):
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
        code_only = source.split('"""', 2)[-1]
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
        packet = _build_packet([fact])
        self.assertEqual(packet["position_size"], {"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED"})
        self.assertEqual(packet["risk_policy"]["approval_status"], "UNRATIFIED")

    def test_tampered_position_size_rejected_by_validator(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["position_size"] = {"status": "COMPUTED", "shares": 10}
        tampered["packet_sha256"] = PS.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED"):
            PS.validate_snapshot(tampered)


class CounterExample13AuthorityFlip(unittest.TestCase):
    """Any existing authority field flipping to true -- rejected/detected."""
    def test_flipped_authority_field_rejected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["authority"]["order_authorized"] = True
        tampered["packet_sha256"] = PS.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE"):
            PS.validate_snapshot(tampered)

    def test_freshly_built_packet_authority_all_false(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        for key, value in packet["authority"].items():
            if key == "review_only":
                self.assertTrue(value)
            else:
                self.assertFalse(value)


class NoPlaintextSecretsTests(unittest.TestCase):
    """Hard security constraint: no secret keys or account numbers
    committed in plaintext anywhere."""
    def test_no_hardcoded_credential_looking_strings_in_alpaca_client(self):
        source = inspect.getsource(AC)
        self.assertNotIn('"APCA-API-KEY-ID": "', source)
        self.assertNotIn('"APCA-API-SECRET-KEY": "', source)

    def test_account_number_never_leaks_into_built_fact_or_packet(self):
        fact = PS.build_alpaca_paper_account_fact(_account(account_number=REAL_ACCOUNT_NUMBER), _positions(), captured_at=T0, decision_at=T0)
        self.assertNotIn(REAL_ACCOUNT_NUMBER, json.dumps(fact))
        packet = _build_packet([fact])
        self.assertNotIn(REAL_ACCOUNT_NUMBER, json.dumps(packet))


class CounterExampleSanitizeUtility(unittest.TestCase):
    """`sanitize_for_raw_evidence()` -- kept as a tested, still-useful
    utility (defense in depth for any future private-storage path), even
    though round 2 established it alone is not sufficient for public-repo
    safety (see `PublicRepoNeverReceivesRealFinancialData` below)."""
    def test_sanitize_strips_forbidden_keys_recursively(self):
        raw_account = _account()
        self.assertIn("account_number", raw_account)
        self.assertIn("id", raw_account)
        sanitized = PS.sanitize_for_raw_evidence(raw_account)
        self.assertNotIn("account_number", sanitized)
        self.assertNotIn("id", sanitized)
        self.assertNotIn(REAL_ACCOUNT_NUMBER, json.dumps(sanitized))

    def test_sanitize_strips_forbidden_keys_from_nested_structures(self):
        nested = {"outer": {"account_number": REAL_ACCOUNT_NUMBER, "list": [{"id": "abc", "keep": "yes"}]}}
        sanitized = PS.sanitize_for_raw_evidence(nested)
        self.assertNotIn(REAL_ACCOUNT_NUMBER, json.dumps(sanitized))
        self.assertEqual(sanitized["outer"]["list"][0], {"keep": "yes"})


# ═══════════════════════════════════════════════════════════════════════
# CIO round 2 (2026-08-23): repo is PUBLIC -- permanently locked regressions
# ═══════════════════════════════════════════════════════════════════════

class CIORound2PitReproduction01FutureSnapshotPassedAsFresh(unittest.TestCase):
    """CIO reproduction 1: "A future-dated account snapshot passes as
    FRESH against a past decision_at." Locked as a permanent regression --
    now REJECTED outright at fact-build time, never silently marked FRESH."""
    def test_alpaca_fact_future_captured_at_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "FUTURE_DATED_VALUE_REJECTED"):
            PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T_LATER, decision_at=T0)

    def test_manual_fact_future_captured_at_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "FUTURE_DATED_VALUE_REJECTED"):
            PS.build_manual_account_fact(market="CRYPTO", currency="USD", cash=100.0, positions=[],
                                          captured_at=T_LATER, decision_at=T0)


class CIORound2PitReproduction02AvailableAfterDecisionPassedValidation(unittest.TestCase):
    """CIO reproduction 2: "available_at > decision_at still passes
    validation." Locked as a permanent regression -- now REJECTED."""
    def test_available_after_decision_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "AVAILABLE_AFTER_DECISION_REJECTED"):
            PS._validate_snapshot_timing(captured_at=T0, available_at=T_LATER, decision_at=T0)

    def test_assemble_snapshot_rejects_available_after_decision_end_to_end(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "AVAILABLE_AFTER_DECISION_REJECTED"):
            PS.assemble_snapshot(account_facts=[fact], fx_rates={}, captured_at=T0, available_at=T_LATER, decision_at=T0)


class CIORound2PitReproduction03FutureFxPassedAsFresh(unittest.TestCase):
    """CIO reproduction 3: "A future-dated FX rate also passes as FRESH."
    Locked as a permanent regression -- now REJECTED."""
    def test_future_dated_fx_as_of_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "FUTURE_DATED_VALUE_REJECTED"):
            PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T_LATER}}, decision_at=T0)


class CIORound2PitReproduction04ManualInputReachedFullCanonicalComputable(unittest.TestCase):
    """CIO reproduction 4: "Feeding manual Korea/Crypto input (unverified)
    still produces FULL_CANONICAL_ACCOUNT_SCOPE, full_portfolio_nav_status
    =OK, and top-level COMPUTABLE." Locked as a permanent regression --
    unverified manual data is never treated as equivalent to
    broker-verified data for completeness purposes."""
    def test_full_canonical_scope_with_manual_sources_never_reaches_computable_or_ok(self):
        usd_fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        krw_fact = PS.build_manual_account_fact(market="KOREA", currency="USD", cash=100.0, positions=[], captured_at=T0, decision_at=T0)
        crypto_fact = PS.build_manual_account_fact(market="CRYPTO", currency="USD", cash=200.0, positions=[], captured_at=T0, decision_at=T0)
        packet = _build_packet([usd_fact, krw_fact, crypto_fact])
        risk = packet["risk_capacity_inputs"]
        # Scope IS fully connected...
        self.assertEqual(risk["account_scope_label"], "FULL_CANONICAL_ACCOUNT_SCOPE")
        self.assertEqual(risk["data_completeness"]["missing_sources"], [])
        # ...but that must NEVER translate into a verified-looking total.
        self.assertNotEqual(risk["status"], "COMPUTABLE")
        self.assertEqual(risk["status"], "DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT")
        self.assertIsNone(risk["full_portfolio_nav"])
        self.assertEqual(risk["full_portfolio_nav_status"], "NOT_COMPUTABLE_UNVERIFIED_ACCOUNT_SOURCE")
        self.assertNotEqual(risk["connected_scope_nav_status"], "OK")
        self.assertEqual(risk["connected_scope_nav_status"], "DIAGNOSTIC_UNVERIFIED")
        PS.validate_snapshot(packet)  # also survives independent re-derivation


class CIORound3ValidatorPitBypassRejected(unittest.TestCase):
    """★ CIO round 3: the BUILDER already rejected future timestamps, but
    `validate_snapshot()` never called `_validate_snapshot_timing()` at
    all and trusted each account fact's own embedded diagnostic fields
    instead of recomputing them -- so a post-hoc tamper + a freshly
    regenerated hash slipped through. Each of the 6 CIO-specified
    counter-examples below tampers a real, validly-built packet, then
    regenerates `packet_sha256` over the tampered packet (so the hash
    check alone would pass) -- every one must STILL be rejected."""

    def _valid_packet_and_fact(self, decision_at=T0):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=decision_at)
        packet = _build_packet([fact], captured_at=T0, available_at=T0, decision_at=decision_at)
        PS.validate_snapshot(packet)  # sanity: the untampered packet is valid
        return packet

    def _rehash(self, packet):
        packet["packet_sha256"] = PS.payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"})
        return packet

    def test_1_future_top_level_available_at_rejected(self):
        packet = self._valid_packet_and_fact()
        tampered = copy.deepcopy(packet)
        tampered["available_at"] = T_LATER
        self._rehash(tampered)
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)

    def test_2_future_account_captured_at_rejected(self):
        packet = self._valid_packet_and_fact()
        tampered = copy.deepcopy(packet)
        tampered["portfolio_facts"]["accounts"][0]["captured_at"] = T_LATER
        self._rehash(tampered)
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)

    def test_3_future_fx_as_of_rejected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T0, "source": "MANUAL"}}, T0)
        packet = _build_packet([fact], fx_rates=fx, captured_at=T0, available_at=T0, decision_at=T0)
        PS.validate_snapshot(packet)
        tampered = copy.deepcopy(packet)
        tampered["portfolio_facts"]["fx_rates"]["KRW/USD"]["as_of"] = T_LATER
        self._rehash(tampered)
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)

    def test_4_tampered_account_staleness_status_rejected(self):
        # Genuinely STALE (captured T0, decision far later) -- tamper the
        # self-reported staleness_status to FRESH to hide it.
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T_MUCH_LATER)
        self.assertEqual(fact["staleness_status"], "STALE")
        packet = _build_packet([fact], captured_at=T0, available_at=T0, decision_at=T_MUCH_LATER)
        tampered = copy.deepcopy(packet)
        tampered["portfolio_facts"]["accounts"][0]["staleness_status"] = "FRESH"
        self._rehash(tampered)
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)

    def test_5_tampered_position_count_rejected(self):
        packet = self._valid_packet_and_fact()
        tampered = copy.deepcopy(packet)
        tampered["portfolio_facts"]["accounts"][0]["position_count"] = 5
        self._rehash(tampered)
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)

    def test_6_tampered_nav_reconciliation_result_rejected(self):
        packet = self._valid_packet_and_fact()
        tampered = copy.deepcopy(packet)
        tampered["portfolio_facts"]["accounts"][0]["nav_reconciliation_status"] = "MISMATCH_FLAGGED"
        self._rehash(tampered)
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)

    def test_untampered_packets_still_pass(self):
        """Sanity: none of the new checks false-positive on real, honest
        packets (single account, multi-currency w/ fx, stale)."""
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        PS.validate_snapshot(_build_packet([fact]))
        krw_fact = PS.build_manual_account_fact(
            market="KOREA", currency="KRW", cash=1_000_000.0,
            positions=[{"symbol": "005930", "qty": "1", "market_value": "80000.0"}],
            captured_at=T0, decision_at=T0,
        )
        fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T0, "source": "MANUAL"}}, T0)
        PS.validate_snapshot(_build_packet([fact, krw_fact], fx_rates=fx))
        stale_fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T_MUCH_LATER)
        PS.validate_snapshot(_build_packet([stale_fact], decision_at=T_MUCH_LATER))


class PublicRepoNeverReceivesRealFinancialData(unittest.TestCase):
    """★ CIO round 2 P0 (the most serious defect found): this repo
    (`yonggeun1021-hub/atlas-data`) is PUBLIC. Real NAV/cash/positions/P&L/
    account_id_hash must never reach ANY path that writes to it -- proven
    here at the code level, not just "we removed the commit step"."""

    def _capture_module(self):
        return _load("portfolio_risk.capture", "portfolio_risk/capture.py")

    def test_capture_module_contains_no_filesystem_write_capability_at_all(self):
        """The strongest structural proof: this module cannot write a file
        even if something in it tried to -- none of the primitives that
        could open/replace/compress a file for writing appear anywhere in
        its ACTUAL CODE (docstrings are stripped first, since this file's
        own documentation discusses these forbidden primitives by name)."""
        import re
        source = inspect.getsource(self._capture_module())
        code_only = re.sub(r'"""(?:.|\n)*?"""', "", source)  # strip module/function docstrings
        for forbidden in ("open(", "os.replace", "gzip", "tempfile", ".write_text(", ".write_bytes(", "NamedTemporaryFile"):
            self.assertNotIn(forbidden, code_only, f"capture.py must never gain filesystem-write capability ({forbidden!r} found)")

    def test_redact_for_public_repo_keys_are_a_subset_of_the_public_safe_allowlist(self):
        capture = self._capture_module()
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        redacted = capture._redact_for_public_repo(packet)
        self.assertLessEqual(set(redacted.keys()), capture.PUBLIC_SAFE_CAPTURE_RESULT_KEYS)

    def test_redact_for_public_repo_never_contains_any_real_numeric_or_identifying_value(self):
        capture = self._capture_module()
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        redacted_text = json.dumps(capture._redact_for_public_repo(packet))
        for forbidden_value in (REAL_ACCOUNT_NUMBER, REAL_EQUITY, REAL_CASH, "5000.0", "10000.0",
                                 fact["account_id_hash"], "AAPL", "100.0"):
            self.assertNotIn(forbidden_value, redacted_text, forbidden_value)
        # And no forbidden KEY names either, recursively.
        redacted_obj = json.loads(redacted_text)
        self._assert_no_forbidden_keys(redacted_obj)

    def _assert_no_forbidden_keys(self, value):
        forbidden_key_fragments = ("equity", "cash", "nav", "market_value", "unrealized_pl",
                                    "buying_power", "account_id_hash", "position", "exposure", "quantity", "qty")
        if isinstance(value, dict):
            for k, v in value.items():
                lowered = k.lower()
                for fragment in forbidden_key_fragments:
                    self.assertNotIn(fragment, lowered, f"forbidden key fragment {fragment!r} found in key {k!r}")
                self._assert_no_forbidden_keys(v)
        elif isinstance(value, list):
            for item in value:
                self._assert_no_forbidden_keys(item)

    def test_failure_path_also_never_leaks_a_real_value(self):
        """Even an exception message could embed a real number (e.g.
        `NEGATIVE_NAV_OR_CASH_REJECTED:equity=-10.0`) -- the failure-path
        redaction uses only the exception CLASS name, never `str(exc)`."""
        capture = self._capture_module()
        try:
            PS.build_alpaca_paper_account_fact(_account(equity="-10.00"), _positions(), captured_at=T0, decision_at=T0)
            self.fail("expected PortfolioSnapshotError")
        except PS.PortfolioSnapshotError as exc:
            redacted = capture._redacted_failure_result(type(exc).__name__)
        self.assertLessEqual(set(redacted.keys()), capture.PUBLIC_SAFE_CAPTURE_RESULT_KEYS)
        self.assertNotIn("-10", json.dumps(redacted))
        self.assertEqual(redacted["capture_status"], "FAILURE")
        self.assertEqual(redacted["error_code"], "PortfolioSnapshotError")

    def test_run_returns_only_the_redacted_result_end_to_end_with_mocked_network(self):
        capture = self._capture_module()
        capture.alpaca_client.fetch_account = lambda key, secret: _account()
        capture.alpaca_client.fetch_positions = lambda key, secret: _positions()
        now = dt.datetime(2026, 8, 23, 14, 0, 0, tzinfo=dt.timezone.utc)
        result = capture.run("k", "s", now)
        self.assertLessEqual(set(result.keys()), capture.PUBLIC_SAFE_CAPTURE_RESULT_KEYS)
        result_text = json.dumps(result)
        for forbidden_value in (REAL_ACCOUNT_NUMBER, REAL_EQUITY, REAL_CASH):
            self.assertNotIn(forbidden_value, result_text)
        self.assertEqual(result["capture_status"], "SUCCESS")
        self.assertEqual(result["source"], "ALPACA_PAPER")
        self.assertEqual(result["real_data_persistence_status"], "PRIVATE_STORAGE_REQUIRED_BEFORE_LIVE_PERSISTENCE")

    def test_public_repo_has_no_live_capture_workflow_at_all(self):
        """★ 2026-08-23 cutover: `.github/workflows/portfolio-risk-input.yml`
        was decommissioned entirely (not merely shrunk to read-only) once
        the private, pull-based `atlas-private-evidence` repo took over
        all real Alpaca capture. `ALPACA_API_KEY`/`ALPACA_API_SECRET` were
        removed from this repo's GitHub secrets; nothing in this repo
        should reference them, and no workflow file here should exist
        that ever attempted a live Alpaca capture -- a stronger guarantee
        than "read-only": there is no such workflow at all."""
        self.assertFalse((ROOT / ".github" / "workflows" / "portfolio-risk-input.yml").exists())
        for workflow_path in (ROOT / ".github" / "workflows").glob("*.yml"):
            text = workflow_path.read_text()
            self.assertNotIn("secrets.ALPACA_API_KEY", text, workflow_path.name)
            self.assertNotIn("secrets.ALPACA_API_SECRET", text, workflow_path.name)


if __name__ == "__main__":
    unittest.main()
