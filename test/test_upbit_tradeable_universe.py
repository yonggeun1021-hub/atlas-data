"""P3-12 Upbit KRW tradeable-universe / PAPER-eligibility classifier regression."""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "upbit_tradeable_universe.py"
SPEC = importlib.util.spec_from_file_location("upbit_tradeable_universe", MODULE_PATH)
UNI = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(UNI)

CAP = UNI.UPBIT_CAPTURE  # the exact capture module instance the classifier itself loaded


def market_entry(
    *,
    warning=False,
    caution_any=False,
    orderbook_available=True,
    candles_available=True,
    best_bid="99000",
    best_ask="100000",
    ask_size="10",
    candle_count=100,
    turnover_days=30,
    turnover="180000000000",
    market_all_available=True,
):
    entry = {"market_all_available": market_all_available}
    if market_all_available:
        entry.update({
            "korean_name": "코인", "english_name": "Coin",
            "market_event_warning": warning, "market_event_caution_any": caution_any,
            "market_event_caution_flags": {},
        })
    else:
        entry["market_event_warning"] = None
    entry["orderbook_available"] = orderbook_available
    if orderbook_available:
        entry["best_bid"] = best_bid
        entry["best_ask"] = best_ask
        entry["ask_levels"] = [{"price": best_ask, "size": ask_size}]
    entry["candles_available"] = candles_available
    if candles_available:
        entry["observed_daily_candle_count"] = candle_count
        entry["trailing_turnover_finalized_day_count"] = turnover_days
        entry["trailing_30d_krw_turnover"] = Decimal(turnover)
    return entry


def base_core(markets: dict, *, available_at="2026-08-28T00:40:00Z", snapshot_date="2026-08-28"):
    return {
        "snapshot_date": snapshot_date,
        "available_at": available_at,
        "manifest_sha256": "c" * 64,
        "markets": markets,
        "duplicate_market_codes": {},
    }


def ratified_policy(**overrides):
    policy = {
        "policy_version": "test-policy/v1",
        "approval_status": "RATIFIED",
        "min_listing_history_finalized_days": 90,
        "turnover_lookback_finalized_days": 30,
        "min_30d_avg_krw_turnover": "5000000000",
        "max_spread_bps": "150",
        "max_estimated_paper_slippage_bps": "150",
        "paper_slippage_estimate_notional_krw": "1000000",
        "max_capture_age_hours": "30",
    }
    policy.update(overrides)
    return policy


def ratified_taxonomy(**overrides):
    taxonomy = {
        "policy_version": "test-taxonomy/v1",
        "approval_status": "RATIFIED",
        "eligible_category": "eligible_crypto",
        "excluded_categories": ["stablecoin"],
        "unknown_asset_policy": "fail_closed_unknown",
        "records": [
            {"canonical_asset_id": "BTC", "category": "eligible_crypto", "effective_from": "2026-08-01", "effective_to": None},
            {"canonical_asset_id": "ETH", "category": "eligible_crypto", "effective_from": "2026-08-01", "effective_to": None},
            {"canonical_asset_id": "USDT", "category": "stablecoin", "effective_from": "2026-08-01", "effective_to": None},
        ],
    }
    taxonomy.update(overrides)
    return taxonomy


class BuildClassificationTests(unittest.TestCase):
    """P3-12-GOV-05: these are pure ``build_classification()`` state-machine
    fixtures (turnover/spread/candle-count/identity-collision gating) --
    they are not testing the exact-release-binding mechanism itself (that
    has its own dedicated coverage in test_upbit_exact_release_binding.py
    and GovernanceExactReleaseBindingTests below), so the synthetic
    ``ratified_taxonomy()`` fixture here is exempted from needing a full
    exact-hash provenance chain via a standard test-only mock -- this is a
    test fixture swap, never a bypass flag in the production code path
    (``_approval_effective``/``effective_identity_mapping`` have no such
    flag; nothing in ``universe/upbit_tradeable_universe.py`` itself can
    disable this check)."""

    def setUp(self):
        self.exact_release_binding_patch = mock.patch.object(
            UNI.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True,
        )
        self.exact_release_binding_patch.start()
        self.addCleanup(self.exact_release_binding_patch.stop)

    def test_normal_complete_input_produces_correct_split(self):
        core = base_core({
            "KRW-BTC": market_entry(),                                           # passes everything -> PAPER_ELIGIBLE
            "KRW-ETH": market_entry(turnover="30000000000"),                     # 1B/day -> OBSERVATION_POOL
            "KRW-USDT": market_entry(),                                          # excluded taxonomy category
        })
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC", "KRW-ETH": "ETH", "KRW-USDT": "USDT"},
        )
        rows = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(rows["KRW-BTC"]["state"], UNI.STATE_PAPER_ELIGIBLE)
        self.assertEqual(rows["KRW-ETH"]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(rows["KRW-ETH"]["reason"], "TURNOVER_BELOW_THRESHOLD")
        self.assertEqual(rows["KRW-USDT"]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(rows["KRW-USDT"]["reason"], "TAXONOMY_EXCLUDED:stablecoin")
        self.assertEqual(packet["summary"]["paper_eligible_count"], 1)
        self.assertEqual(packet["summary"]["observation_pool_count"], 2)

    def test_missing_required_field_fails_closed(self):
        core = base_core({"KRW-BTC": market_entry(orderbook_available=False)})
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        row = packet["markets"][0]
        self.assertEqual(row["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(row["reason"], "MISSING_FIELD:orderbook")

    def test_stale_capture_excluded_from_tradeable_universe(self):
        core = base_core({"KRW-BTC": market_entry()}, available_at="2026-08-26T00:40:00Z")
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        row = packet["markets"][0]
        self.assertEqual(row["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(row["reason"], "STALE_CAPTURE")

    def test_partial_data_one_market_fails_closed_others_unaffected(self):
        core = base_core({
            "KRW-BTC": market_entry(),
            "KRW-ETH": market_entry(candles_available=False),
        })
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC", "KRW-ETH": "ETH"},
        )
        rows = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(rows["KRW-BTC"]["state"], UNI.STATE_PAPER_ELIGIBLE)
        self.assertEqual(rows["KRW-ETH"]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(rows["KRW-ETH"]["reason"], "MISSING_FIELD:candles")

    def test_future_dated_evidence_is_rejected(self):
        core = base_core({"KRW-BTC": market_entry()}, available_at="2026-08-29T00:40:00Z")
        with self.assertRaisesRegex(UNI.UpbitUniverseError, "AVAILABLE_AT_FUTURE_DATED"):
            UNI.build_classification(
                core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
                ratified_identity_registry={"KRW-BTC": "BTC"},
            )

    def test_identity_collision_is_blocked_and_never_dropped(self):
        core = base_core({
            "KRW-BTC": market_entry(),
            "KRW-ETH": market_entry(),
        })
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC", "KRW-ETH": "ETH"},
            blocked_markets={"KRW-BTC"},
        )
        rows = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(rows["KRW-BTC"]["state"], UNI.STATE_BLOCKED)
        self.assertEqual(rows["KRW-BTC"]["reason"], "IDENTITY_COLLISION")
        self.assertEqual(rows["KRW-ETH"]["state"], UNI.STATE_PAPER_ELIGIBLE)
        # never silently dropped -- still present with full row shape
        self.assertIn("KRW-BTC", {row["market"] for row in packet["markets"]})
        self.assertEqual(packet["summary"]["blocked_count"], 1)

    def test_investment_warning_force_excluded_even_if_liquidity_passes(self):
        core = base_core({"KRW-BTC": market_entry(warning=True)})
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        row = packet["markets"][0]
        self.assertEqual(row["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(row["reason"], "INVESTMENT_WARNING_ACTIVE")

    def test_abnormal_spread_excludes_from_tradeable_universe_with_reason(self):
        core = base_core({"KRW-BTC": market_entry(best_bid="90000", best_ask="100000")})  # ~1053 bps spread
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28",
            policy=ratified_policy(max_spread_bps="20"), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        row = packet["markets"][0]
        self.assertEqual(row["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(row["reason"], "SPREAD_ABOVE_THRESHOLD")

    def test_abnormal_slippage_excludes_from_paper_eligible_with_reason(self):
        # Thin depth right at the best ask, then a big block far higher --
        # enough total depth to fill the policy notional, but only at a
        # volume-weighted average price well above best ask.
        entry = market_entry(best_bid="99000", best_ask="100000")
        entry["ask_levels"] = [
            {"price": "100000", "size": "0.001"},   # 100 KRW at best
            {"price": "150000", "size": "100"},      # ample depth, far worse price
        ]
        core = base_core({"KRW-BTC": entry})
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28",
            policy=ratified_policy(max_spread_bps="150", max_estimated_paper_slippage_bps="5",
                                    paper_slippage_estimate_notional_krw="1000000"),
            taxonomy=ratified_taxonomy(), ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        row = packet["markets"][0]
        self.assertEqual(row["state"], UNI.STATE_TRADEABLE_UNIVERSE)
        self.assertEqual(row["reason"], "SLIPPAGE_ABOVE_THRESHOLD")

    def test_kraken_presence_never_promotes(self):
        core = base_core({"KRW-BTC": market_entry(turnover="30000000000")})  # 1B/day, deliberately fails
        without_kraken = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        with_kraken = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"}, kraken_known_canonical_ids={"BTC"},
        )
        self.assertEqual(without_kraken["markets"][0]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(with_kraken["markets"][0]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(without_kraken["markets"][0]["reason"], with_kraken["markets"][0]["reason"])
        self.assertFalse(without_kraken["markets"][0]["kraken_cross_exchange_reference"])
        self.assertTrue(with_kraken["markets"][0]["kraken_cross_exchange_reference"])
        # SAFETY INVARIANT source-level check: the only read of the Kraken
        # reference set is the display field itself, never a gating branch.
        source = MODULE_PATH.read_text(encoding="utf-8")
        gating_section = source.split("kraken_known_canonical_ids = kraken_known_canonical_ids or set()")[1]
        gating_section = gating_section.split('"kraken_cross_exchange_reference"')[0]
        self.assertNotIn("kraken_known_canonical_ids", gating_section)

    def test_determinism_same_input_twice_identical_output(self):
        core = base_core({
            "KRW-BTC": market_entry(),
            "KRW-ETH": market_entry(turnover="30000000000"),
        })
        first = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC", "KRW-ETH": "ETH"},
        )
        second = UNI.build_classification(
            copy.deepcopy(core), evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC", "KRW-ETH": "ETH"},
        )
        self.assertEqual(UNI.canonical_json(first), UNI.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, UNI.payload_sha256(second))

    def test_authority_fields_always_false(self):
        core = base_core({"KRW-BTC": market_entry()})
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        self.assertEqual(packet["markets"][0]["state"], UNI.STATE_PAPER_ELIGIBLE)
        for value in packet["markets"][0]["authority"].values():
            self.assertFalse(value)
        for value in packet["authority"].values():
            self.assertFalse(value)

    def test_unratified_policy_caps_at_observation_pool(self):
        core = base_core({"KRW-BTC": market_entry()})
        policy = ratified_policy(approval_status="PROPOSED_PAPER_BASELINE_UNRATIFIED")
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=policy, taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        self.assertEqual(packet["markets"][0]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(packet["markets"][0]["reason"], "POLICY_UNRATIFIED")
        self.assertFalse(packet["policy_ratified"])

    def test_unratified_taxonomy_caps_at_observation_pool(self):
        core = base_core({"KRW-BTC": market_entry()})
        taxonomy = ratified_taxonomy(approval_status="PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=taxonomy,
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        self.assertEqual(packet["markets"][0]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(packet["markets"][0]["reason"], "TAXONOMY_UNRATIFIED")

    def test_unratified_identity_caps_at_observation_pool(self):
        core = base_core({"KRW-BTC": market_entry()})
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={},
        )
        self.assertEqual(packet["markets"][0]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(packet["markets"][0]["reason"], "IDENTITY_UNRATIFIED")

    def test_shipped_repo_config_releases_only_exact_eight_after_effective_date(self):
        """The exact approved slice is effective without historical backfill."""
        policy = UNI.load_policy()
        taxonomy = UNI.load_taxonomy()
        registry = UNI.load_identity_registry()
        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(taxonomy["approval_status"], "RATIFIED")
        self.assertEqual(registry["approval_status"], "RATIFIED")
        self.assertEqual(len(registry["mappings"]), 8)
        self.assertEqual(UNI.effective_identity_mapping(registry, "2026-08-29"), {})
        core = base_core({"KRW-BTC": market_entry()})
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=policy, taxonomy=taxonomy,
            ratified_identity_registry=UNI.effective_identity_mapping(registry, "2026-08-28"),
        )
        self.assertEqual(packet["summary"]["tradeable_universe_count"], 0)
        self.assertEqual(packet["summary"]["paper_eligible_count"], 0)
        self.assertEqual(packet["markets"][0]["state"], UNI.STATE_OBSERVATION_POOL)

    def test_turnover_threshold_uses_30_day_daily_average(self):
        core = base_core({"KRW-BTC": market_entry(turnover="149999999999.99")})
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            ratified_identity_registry={"KRW-BTC": "BTC"},
        )
        self.assertEqual(packet["markets"][0]["reason"], "TURNOVER_BELOW_THRESHOLD")


class ShippedRepoConfigExactReleaseBindingTests(unittest.TestCase):
    """P3-12-GOV-05: unlike BuildClassificationTests above, these
    deliberately run WITHOUT any exact-release-binding mock -- they assert
    what the REAL shipped repo config actually does now: the exact code
    chain is effective only on and after its own ratification date, never
    retroactively for the historical 2026-08-30 decision date."""

    def test_shipped_release_is_exact_bound_only_after_code_approval(self):
        policy = UNI.load_policy()
        taxonomy = UNI.load_taxonomy()
        registry = UNI.load_identity_registry()
        self.assertEqual(taxonomy["approval_status"], "RATIFIED")
        self.assertEqual(registry["approval_status"], "RATIFIED")
        self.assertEqual(len(registry["mappings"]), 8)
        self.assertEqual(
            registry["code_approval_evidence_ref"],
            "evidence/crypto/upbit/identity/approvals/2026-09-03/p3-12-runtime-exact-code-hash.json",
        )
        self.assertEqual(
            taxonomy["code_approval_evidence_ref"],
            registry["code_approval_evidence_ref"],
        )
        # The approval was ratified on 2026-09-02 UTC, so the historical
        # 2026-08-30 classification remains fail-closed while current
        # evaluation resolves only the already-ratified exact eight.
        self.assertEqual(UNI.effective_identity_mapping(registry, "2026-08-30"), {})
        self.assertFalse(UNI._identity_taxonomy_exact_bound_effective(
            taxonomy, "2026-08-30", date_field="effective_from", content_field="records",
        ))
        self.assertEqual(
            UNI.effective_identity_mapping(registry, "2026-09-02"),
            {
                "KRW-BTC": "BTC", "KRW-ETH": "ETH", "KRW-LINK": "LINK", "KRW-SHIB": "SHIB",
                "KRW-SOL": "SOL", "KRW-SUI": "SUI", "KRW-WLD": "WLD", "KRW-XRP": "XRP",
            },
        )
        self.assertTrue(UNI._identity_taxonomy_exact_bound_effective(
            taxonomy, "2026-09-02", date_field="effective_from", content_field="records",
        ))
        core = base_core(
            {"KRW-BTC": market_entry(best_bid="99900", best_ask="100000")},
            available_at="2026-08-30T00:40:00Z", snapshot_date="2026-08-30",
        )
        packet = UNI.build_classification(
            core, evaluation_as_of="2026-08-30", policy=policy, taxonomy=taxonomy,
            ratified_identity_registry=UNI.effective_identity_mapping(registry, "2026-08-30"),
        )
        self.assertTrue(packet["policy_ratified"])
        self.assertFalse(packet["taxonomy_ratified"])
        self.assertEqual(packet["markets"][0]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertTrue(all(value is False for value in packet["authority"].values()))


class RatifiedIdentityRegistryTests(unittest.TestCase):
    def test_registry_is_exactly_evidence_bound_and_held_markets_stay_absent(self):
        registry = UNI.load_identity_registry()
        self.assertEqual(len(registry["mappings"]), 8)
        self.assertEqual(
            sorted(registry["mappings"]),
            ["KRW-BTC", "KRW-ETH", "KRW-LINK", "KRW-SHIB", "KRW-SOL", "KRW-SUI", "KRW-WLD", "KRW-XRP"],
        )
        self.assertNotIn("KRW-RE", registry["mappings"])
        self.assertNotIn("KRW-LIT", registry["mappings"])

    def test_registry_source_hash_tamper_fails_closed(self):
        registry = UNI.load_identity_registry()
        registry["source_candidate_packet"]["file_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(UNI.UpbitUniverseError, "SOURCE_PACKET_FILE_HASH_MISMATCH"):
                UNI.load_identity_registry(path)

    def test_evaluation_as_of_and_available_at_shape_validated(self):
        core = base_core({"KRW-BTC": market_entry()})
        with self.assertRaises(UNI.UpbitUniverseError):
            UNI.build_classification(
                core, evaluation_as_of="not-a-date", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            )
        bad_core = base_core({"KRW-BTC": market_entry()}, available_at="not-a-timestamp")
        with self.assertRaises(UNI.UpbitUniverseError):
            UNI.build_classification(
                bad_core, evaluation_as_of="2026-08-28", policy=ratified_policy(), taxonomy=ratified_taxonomy(),
            )


class SnapshotPipelineTests(unittest.TestCase):
    """load_snapshot_core against real captured (or tampered) files."""

    def _capture(self, tmp_root, markets, *, warning_for=None, tamper=False, downloaded_at=None):
        from test_upbit_market_capture import build_fetcher  # local sibling test module

        contract = CAP.load_contract()
        fetcher = build_fetcher(contract, markets, warning_for=warning_for)
        clock = (lambda: downloaded_at) if downloaded_at else (
            lambda: dt.datetime(2026, 8, 28, 0, 40, 0, tzinfo=dt.timezone.utc)
        )
        target = CAP.capture_snapshot(
            tmp_root, snapshot_date=dt.date(2026, 8, 28), contract=contract, fetcher=fetcher,
            sleeper=lambda s: None, clock=clock,
        )
        if tamper:
            import gzip
            (target / contract["market_all_raw_file"]).write_bytes(gzip.compress(b"[]"))
        return target, contract

    def test_load_snapshot_core_parses_real_captured_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, contract = self._capture(Path(tmp), ["KRW-BTC", "KRW-ETH"])
            core = UNI.load_snapshot_core(target, contract)
            self.assertEqual(set(core["markets"]), {"KRW-BTC", "KRW-ETH"})
            self.assertEqual(core["available_at"], "2026-08-28T00:40:00Z")
            self.assertTrue(core["markets"]["KRW-BTC"]["orderbook_available"])
            self.assertTrue(core["markets"]["KRW-BTC"]["candles_available"])
            self.assertEqual(core["markets"]["KRW-BTC"]["observed_daily_candle_count"], 100)

    def test_tampered_evidence_is_blocked_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, contract = self._capture(Path(tmp), ["KRW-BTC"], tamper=True)
            with self.assertRaisesRegex(CAP.CaptureError, "RAW_FILE_HASH_MISMATCH"):
                UNI.load_snapshot_core(target, contract)

    def test_out_of_order_late_arriving_capture_does_not_overwrite_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, contract = self._capture(root, ["KRW-BTC"])
            original_core = UNI.load_snapshot_core(target, contract)
            with self.assertRaisesRegex(CAP.CaptureError, "APPEND_ONLY_VIOLATION"):
                self._capture(root, ["KRW-BTC", "KRW-ETH"])
            replayed_core = UNI.load_snapshot_core(target, contract)
            self.assertEqual(set(replayed_core["markets"]), set(original_core["markets"]))
            self.assertEqual(replayed_core["manifest_sha256"], original_core["manifest_sha256"])

    def test_duplicate_market_entries_deduped_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, contract = self._capture(Path(tmp), ["KRW-BTC"])
            # Inject a duplicate row into the raw market_all file directly
            # (post-capture, simulating an upstream anomaly the manifest
            # hash still covers because we recompute it below).
            import gzip
            raw = json.loads(gzip.open(target / contract["market_all_raw_file"], "rb").read())
            raw.append(dict(raw[0]))
            new_raw_bytes = json.dumps(raw).encode()
            new_gz = gzip.compress(new_raw_bytes)
            (target / contract["market_all_raw_file"]).write_bytes(new_gz)
            manifest_path = target / "_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            import hashlib
            manifest["checksums"][contract["market_all_raw_file"]] = hashlib.sha256(new_raw_bytes).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

            core = UNI.load_snapshot_core(target, contract)
            self.assertEqual(len(core["markets"]), 1)
            self.assertEqual(core["duplicate_market_codes"]["market_all"].get("KRW-BTC"), 2)

    def test_non_krw_quoted_market_in_raw_market_all_is_excluded_not_crashed(self):
        # Reproduces a real production incident: Upbit's GET /v1/market/all
        # legitimately returns BTC-/USDT-quoted pairs (e.g. a real market
        # like "BTC-0G") alongside KRW-* ones. The capture script's own
        # markets list (manifest["markets"]) is already KRW-only, but the
        # raw market_all archive is deliberately unfiltered for audit
        # completeness. Before this fix, that raw row reached
        # classification/identity review unfiltered and crashed the entire
        # run (MARKET_CODE_INVALID) instead of being excluded as
        # out-of-scope.
        with tempfile.TemporaryDirectory() as tmp:
            target, contract = self._capture(Path(tmp), ["KRW-BTC"])
            import gzip
            raw = json.loads(gzip.open(target / contract["market_all_raw_file"], "rb").read())
            raw.append({
                "market": "BTC-0G",
                "korean_name": "제로지",
                "english_name": "0G",
            })
            new_raw_bytes = json.dumps(raw).encode()
            (target / contract["market_all_raw_file"]).write_bytes(gzip.compress(new_raw_bytes))
            manifest_path = target / "_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            import hashlib
            manifest["checksums"][contract["market_all_raw_file"]] = hashlib.sha256(new_raw_bytes).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

            core = UNI.load_snapshot_core(target, contract)  # must not raise
            self.assertEqual(set(core["markets"]), {"KRW-BTC"})
            self.assertEqual(core["non_krw_market_codes_excluded"], ["BTC-0G"])

            # The exact downstream crash site: identity proposal building
            # must never see the excluded market either.
            identity_spec = importlib.util.spec_from_file_location(
                "upbit_market_identity_proposal_regression",
                ROOT / "identity" / "upbit_market_identity_proposal.py",
            )
            identity_module = importlib.util.module_from_spec(identity_spec)
            identity_spec.loader.exec_module(identity_module)
            for market in core["markets"]:
                identity_module.default_candidate_canonical_asset_id(market)  # must not raise
            with self.assertRaises(identity_module.UpbitMarketIdentityProposalError):
                identity_module.default_candidate_canonical_asset_id("BTC-0G")


_EXACT_RELEASE_BINDING_TEST_SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_fixtures_for_universe_test",
    ROOT / "test" / "test_upbit_exact_release_binding.py",
)
_EXACT_RELEASE_BINDING_TEST = importlib.util.module_from_spec(_EXACT_RELEASE_BINDING_TEST_SPEC)
_EXACT_RELEASE_BINDING_TEST_SPEC.loader.exec_module(_EXACT_RELEASE_BINDING_TEST)


class ExactReleaseBindingIntegrationTests(unittest.TestCase):
    """P3-12-GOV-05 (v2, two-chain design): proves the WIRING at the
    ``universe/`` module level, not the validator's own internals (fully
    covered by test_upbit_exact_release_binding.py's 17 no-mock tests) --
    the SAME fully self-consistent content-chain + code-chain fixture
    routed through ``EXACT_RELEASE_BINDING`` with its path constants
    pointed at an isolated tempdir, exercised via
    ``UNI.effective_identity_mapping()``/``UNI._identity_taxonomy_exact_bound_effective()``
    directly (there is no other, indirect entry point to "bypass")."""

    def _patched(self, tmp: Path):
        erb = UNI.EXACT_RELEASE_BINDING
        return (
            mock.patch.object(erb, "ROOT", tmp),
            mock.patch.object(erb, "POLICY_CONTRACT_PATH", tmp / "config" / "upbit_exact_release_binding_policy_contract.json"),
            mock.patch.object(erb, "CONSUMER_PATH", tmp / "universe" / "upbit_tradeable_universe.py"),
            mock.patch.object(erb, "VALIDATOR_PATH", tmp / "governance" / "upbit_exact_release_binding.py"),
            mock.patch.object(erb, "FREEZE_PATH", tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json"),
            mock.patch.object(erb, "RELEASE_BUILDER_PATH", tmp / "identity" / "upbit_exact_release_binding_release.py"),
        )

    def test_activated_synthetic_v3_chain_is_effective_through_the_real_universe_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = _EXACT_RELEASE_BINDING_TEST.build_full_chain(tmp)
            activated = artifacts["activate"](registry)
            patches = self._patched(tmp)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                self.assertEqual(
                    UNI.effective_identity_mapping(activated, "2026-08-30"),
                    _EXACT_RELEASE_BINDING_TEST.MARKETS_TO_IDS,
                )

    def test_shipped_branch_without_code_approval_ref_stays_pending_through_universe_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = _EXACT_RELEASE_BINDING_TEST.build_full_chain(tmp)
            patches = self._patched(tmp)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                # content chain alone, no code_approval_evidence_ref yet
                self.assertEqual(UNI.effective_identity_mapping(registry, "2026-08-30"), {})

    def test_ninth_market_tamper_stays_empty_via_direct_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = _EXACT_RELEASE_BINDING_TEST.build_full_chain(tmp)
            activated = artifacts["activate"](registry)
            patches = self._patched(tmp)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                self.assertEqual(
                    UNI.effective_identity_mapping(activated, "2026-08-30"),
                    _EXACT_RELEASE_BINDING_TEST.MARKETS_TO_IDS,
                )
                tampered = copy.deepcopy(activated)
                tampered["mappings"]["KRW-DOGE"] = "DOGE"
                # direct call, no other indirection to bypass -- must
                # still fail closed for tampered content.
                self.assertEqual(UNI.effective_identity_mapping(tampered, "2026-08-30"), {})

    def test_consumer_or_validator_file_tamper_stays_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, artifacts = _EXACT_RELEASE_BINDING_TEST.build_full_chain(tmp)
            activated = artifacts["activate"](registry)
            patches = self._patched(tmp)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                self.assertEqual(
                    UNI.effective_identity_mapping(activated, "2026-08-30"),
                    _EXACT_RELEASE_BINDING_TEST.MARKETS_TO_IDS,
                )
                artifacts["consumer_path"].write_text("# tampered\n", encoding="utf-8")
                self.assertEqual(UNI.effective_identity_mapping(activated, "2026-08-30"), {})

    def test_approval_status_tamper_does_not_restore_a_broken_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, _, _ = _EXACT_RELEASE_BINDING_TEST.build_full_chain(tmp)
            patches = self._patched(tmp)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                tampered = copy.deepcopy(registry)
                tampered["approval_status"] = "RATIFIED"  # already was; no code_approval_evidence_ref exists
                self.assertEqual(UNI.effective_identity_mapping(tampered, "2026-08-30"), {})
                self.assertFalse(UNI._identity_taxonomy_exact_bound_effective(
                    tampered, "2026-08-30", date_field="effective_from", content_field="mappings",
                ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
