#!/usr/bin/env python3
"""Portfolio Risk Input Contract -- regression + the 13 required
counter-example scenarios, PLUS the 6 CIO review-round-1 P0/P1 fixes
(account-number sanitization, real append-only storage, multi-currency
FX-safe totals, stale/mismatch forcing the whole risk block
NOT_COMPUTABLE, a non-caller-suppliable Account Scope Registry, and
semantic -- not just hash -- re-derivation in the validator).
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
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

REAL_ACCOUNT_NUMBER = "PA000TEST0001"


def _account(**overrides):
    base = {
        "id": "11111111-2222-3333-4444-555555555555",
        "account_number": REAL_ACCOUNT_NUMBER,
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
        self.assertEqual(risk["status"], "COMPUTABLE")
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
    """Stale account balance used as current -- rejected. Fix 4: this now
    forces the WHOLE risk_capacity_inputs block to NOT_COMPUTABLE, not
    just a flag next to numbers that keep getting computed."""
    def test_old_capture_vs_new_decision_forces_whole_block_not_computable(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T_MUCH_LATER)
        self.assertEqual(fact["staleness_status"], "STALE")
        packet = _build_packet([fact], decision_at=T_MUCH_LATER)
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["status"], "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT")
        self.assertTrue(risk["data_completeness"]["any_stale"])
        # ★ Fix 4: not just flagged -- every number in the block is None.
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
    """Mixed-currency amounts summed without an FX rate -- rejected.
    Fix 3: cash/exposure totals are NEVER summed raw across currency --
    only the explicit `*_base_currency` fields are cross-currency totals,
    and those follow the exact same missing/stale-FX NOT_COMPUTABLE rule
    as NAV."""
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
        self.assertEqual(risk["status"], "COMPUTABLE")  # data itself isn't stale/mismatched
        self.assertEqual(risk["connected_scope_nav_status"], "NOT_COMPUTABLE_MISSING_FX_RATE")
        self.assertIsNone(risk["connected_scope_nav"])
        self.assertEqual(risk["total_cash_base_currency_status"], "NOT_COMPUTABLE_MISSING_FX_RATE")
        self.assertIsNone(risk["total_cash_base_currency"])
        self.assertEqual(risk["gross_exposure_base_currency_status"], "NOT_COMPUTABLE_MISSING_FX_RATE")
        self.assertIsNone(risk["gross_exposure_base_currency"])
        # Raw per-currency breakdowns are still reported -- never blended,
        # never silently dropped just because the base-currency total failed.
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
        # Both facts are FRESH relative to T0's staleness window from T0 itself,
        # but decision_at is far in the future -> facts read STALE -> whole
        # block collapses (Fix 4), which subsumes the FX staleness case too.
        self.assertEqual(packet["risk_capacity_inputs"]["status"], "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT")

    def test_stale_fx_rate_alone_blocks_totals_when_accounts_are_fresh(self):
        usd_fact, krw_fact = self._two_currency_facts()
        # Accounts stay fresh (captured_at == decision_at == T0); only the FX
        # rate itself is stale relative to decision_at.
        stale_fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": "2026-08-01T00:00:00Z"}}, T0)
        packet = _build_packet([usd_fact, krw_fact], fx_rates=stale_fx)
        risk = packet["risk_capacity_inputs"]
        # any_stale picks up the stale fx pair -> whole block NOT_COMPUTABLE (Fix 4).
        self.assertEqual(risk["status"], "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT")

    def test_fresh_fx_rate_allows_base_currency_totals(self):
        usd_fact, krw_fact = self._two_currency_facts()
        fresh_fx = PS.assemble_fx_rates({"KRW/USD": {"rate": 0.00072, "as_of": T0, "source": "MANUAL"}}, T0)
        packet = _build_packet([usd_fact, krw_fact], fx_rates=fresh_fx)
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["status"], "COMPUTABLE")
        self.assertEqual(risk["connected_scope_nav_status"], "OK")
        self.assertAlmostEqual(risk["connected_scope_nav"], 10000.0 + 1_080_000.0 * 0.00072)
        self.assertAlmostEqual(risk["total_cash_base_currency"], 5000.0 + 1_000_000.0 * 0.00072)


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
    """Account-level NAV disagreeing with the sum of positions -- flagged.
    Fix 4: this now also forces the whole risk_capacity_inputs block to
    NOT_COMPUTABLE at the snapshot level, not merely a per-account flag."""
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
    """Total NAV confirmed while some market's data is missing -- rejected
    (must be NOT_COMPUTABLE instead). Fix 5/6: account scope is now a fixed
    registry (`CANONICAL_ACCOUNT_SCOPE`), never a caller-suppliable
    parameter -- there is nothing to shrink to make this pass."""
    def test_alpaca_only_never_presented_as_full_portfolio(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["account_scope_label"], "US_PAPER_ACCOUNT_SCOPE_ONLY")
        self.assertEqual(risk["full_portfolio_nav_status"], "NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE")
        self.assertIsNone(risk["full_portfolio_nav"])
        self.assertEqual(risk["data_completeness"]["missing_sources"], ["CRYPTO", "KOREA"])
        # connected_scope_nav is fine to compute (it covers exactly what IS
        # connected) but is never called a "total portfolio" figure.
        self.assertEqual(risk["connected_scope_nav"], 10000.0)

    def test_full_canonical_scope_present_allows_full_portfolio_nav(self):
        usd_fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        krw_fact = PS.build_manual_account_fact(market="KOREA", currency="USD", cash=100.0, positions=[], captured_at=T0, decision_at=T0)
        crypto_fact = PS.build_manual_account_fact(market="CRYPTO", currency="USD", cash=200.0, positions=[], captured_at=T0, decision_at=T0)
        packet = _build_packet([usd_fact, krw_fact, crypto_fact])
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(risk["account_scope_label"], "FULL_CANONICAL_ACCOUNT_SCOPE")
        self.assertEqual(risk["full_portfolio_nav_status"], "OK")
        self.assertAlmostEqual(risk["full_portfolio_nav"], 10000.0 + 100.0 + 200.0)

    def test_assemble_snapshot_has_no_expected_sources_parameter_to_shrink(self):
        # Structural: nothing a caller can pass to shrink account scope.
        params = inspect.signature(PS.assemble_snapshot).parameters
        self.assertNotIn("expected_sources", params)


class CounterExample10SameTimestampTampering(unittest.TestCase):
    """Same-timestamp data tampering -- detected."""
    def test_post_hoc_field_edit_without_rehash_detected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["risk_capacity_inputs"]["connected_scope_nav"] = 999999.0  # same captured_at, content silently changed
        with self.assertRaises(PS.PortfolioSnapshotError):
            PS.validate_snapshot(tampered)


class CounterExampleReSignedSemanticTamperRejected(unittest.TestCase):
    """★ CIO fix 7/8 (P0): a re-signed tamper -- claimed value changed AND
    the hash regenerated to match the tampered packet -- must STILL be
    rejected, because validate_snapshot() independently RE-DERIVES
    risk_capacity_inputs from portfolio_facts (which was left untouched)
    and compares field-by-field, rather than only checking hash
    self-consistency."""
    def test_tampered_nav_with_freshly_regenerated_hash_still_rejected(self):
        fact = PS.build_alpaca_paper_account_fact(_account(), _positions(), captured_at=T0, decision_at=T0)
        packet = _build_packet([fact])
        tampered = copy.deepcopy(packet)
        tampered["risk_capacity_inputs"]["connected_scope_nav"] = 999999.0
        tampered["risk_capacity_inputs"]["full_portfolio_nav"] = None  # keep internally hash-consistent shape
        # Attacker regenerates a VALID hash over the tampered packet.
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


class CounterExampleAccountNumberNeverInRawEvidence(unittest.TestCase):
    """★ CIO fix 1/2 (P0, the most serious defect found): the previous
    version of this module stored the FULL, un-sanitized raw Alpaca
    response as committed evidence -- gzip is not encryption, and the old
    tests only checked the normalized `manifest.json`, never the raw gzip
    itself, so the leak went undetected. This class decompresses the
    ACTUAL stored gzip bytes -- exactly as `capture.py` would write them --
    and scans the decompressed plaintext for the real account number and
    the Alpaca internal account `id`."""
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

    def test_decompressed_stored_raw_gzip_never_contains_the_real_account_number(self):
        """The actual end-to-end proof: build the bytes exactly as
        `capture.py::run()` does, decompress them, and scan the plaintext."""
        capture = _load("portfolio_risk.capture", "portfolio_risk/capture.py")
        raw_account = _account(account_number=REAL_ACCOUNT_NUMBER)
        sanitized = PS.sanitize_for_raw_evidence(raw_account)
        stored_bytes = gzip.compress(capture._canonical_bytes(sanitized), mtime=0)
        decompressed_text = gzip.decompress(stored_bytes).decode("utf-8")
        self.assertNotIn(REAL_ACCOUNT_NUMBER, decompressed_text)
        self.assertNotIn(raw_account["id"], decompressed_text)
        # sanity: the field really was present in the raw input we started from
        self.assertIn(REAL_ACCOUNT_NUMBER, json.dumps(raw_account))


class EvidencePublishTests(unittest.TestCase):
    def _capture_module(self):
        return _load("portfolio_risk.capture", "portfolio_risk/capture.py")

    def _patch_fetchers(self, capture, account_raw, positions_raw):
        capture.alpaca_client.fetch_account = lambda key, secret: account_raw
        capture.alpaca_client.fetch_positions = lambda key, secret: positions_raw

    def test_capture_module_writes_append_only_evidence_without_raw_account_number(self):
        capture = self._capture_module()
        self._patch_fetchers(capture, _account(), _positions())
        now = dt.datetime(2026, 8, 23, 14, 0, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = capture.run(root, "k", "s", now)
            self.assertTrue((root / "data" / "latest_portfolio_risk_input.json").exists())
            raw_dir = root / "evidence" / "operational" / "portfolio_risk_input" / "raw" / "2026-08-23"
            self.assertTrue(raw_dir.exists())
            gz_files = sorted(raw_dir.glob("alpaca_account-*.json.gz"))
            self.assertEqual(len(gz_files), 1)
            decompressed = gzip.decompress(gz_files[0].read_bytes()).decode("utf-8")
            self.assertNotIn(REAL_ACCOUNT_NUMBER, decompressed)  # ★ the exact CIO-flagged leak, now proven absent
            manifest_files = list(raw_dir.glob("manifest-*.json"))
            self.assertEqual(len(manifest_files), 1)
            self.assertNotIn(REAL_ACCOUNT_NUMBER, manifest_files[0].read_text())
            on_disk = json.loads(manifest_files[0].read_text())
            self.assertEqual(on_disk["packet_sha256"], packet["packet_sha256"])

    def test_rerun_with_identical_snapshot_is_a_byte_identical_noop(self):
        """★ CIO fix 3: re-running with an identical snapshot must not
        duplicate or overwrite evidence -- it's a no-op at the same
        content-addressed path."""
        capture = self._capture_module()
        self._patch_fetchers(capture, _account(), _positions())
        now = dt.datetime(2026, 8, 23, 14, 0, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture.run(root, "k", "s", now)
            raw_dir = root / "evidence" / "operational" / "portfolio_risk_input" / "raw" / "2026-08-23"
            before = {p.name: p.read_bytes() for p in raw_dir.iterdir()}
            capture.run(root, "k", "s", now)  # identical inputs, identical timestamp
            after = {p.name: p.read_bytes() for p in raw_dir.iterdir()}
            self.assertEqual(before, after)  # same files, same bytes -- no duplication, no overwrite

    def test_two_genuinely_different_snapshots_same_day_both_preserved(self):
        """★ CIO fix 3 / required test item 10: two DIFFERENT snapshots
        captured on the same day must both survive as distinct evidence
        files, not overwrite each other."""
        capture = self._capture_module()
        now = dt.datetime(2026, 8, 23, 14, 0, 0, tzinfo=dt.timezone.utc)
        later = dt.datetime(2026, 8, 23, 16, 0, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._patch_fetchers(capture, _account(equity="10000.00", cash="5000.00"), _positions())
            first_packet = capture.run(root, "k", "s", now)
            self._patch_fetchers(capture, _account(equity="12000.00", cash="7000.00"), _positions())
            second_packet = capture.run(root, "k", "s", later)
            raw_dir = root / "evidence" / "operational" / "portfolio_risk_input" / "raw" / "2026-08-23"
            manifest_files = sorted(raw_dir.glob("manifest-*.json"))
            self.assertEqual(len(manifest_files), 2)  # both preserved, neither overwritten
            self.assertNotEqual(first_packet["packet_sha256"], second_packet["packet_sha256"])
            account_gz_files = sorted(raw_dir.glob("alpaca_account-*.json.gz"))
            self.assertEqual(len(account_gz_files), 2)
            for f in account_gz_files:
                self.assertNotIn(REAL_ACCOUNT_NUMBER, gzip.decompress(f.read_bytes()).decode("utf-8"))

    def test_collision_with_genuinely_different_content_at_same_path_hard_fails(self):
        capture = self._capture_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collide.json"
            capture._write_append_only_or_noop(path, b"first-content")
            with self.assertRaisesRegex(capture.PortfolioRiskCaptureError, "APPEND_ONLY_EVIDENCE_COLLISION"):
                capture._write_append_only_or_noop(path, b"different-content")


if __name__ == "__main__":
    unittest.main()
