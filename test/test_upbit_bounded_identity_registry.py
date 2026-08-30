"""P3-12-ID-01 Upbit Bounded Identity Registry regression."""
from __future__ import annotations

import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "identity" / "upbit_bounded_identity_registry.py"
SPEC = importlib.util.spec_from_file_location("upbit_bounded_identity_registry", MODULE_PATH)
ID01 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ID01)

UNI = ID01.UNI
IDP = ID01.IDP
HARNESS = ID01.HARNESS


def market_row(*, korean_name="코인", english_name="Coin", warning=False):
    return {
        "market_all_available": True, "korean_name": korean_name, "english_name": english_name,
        "market_event_warning": warning, "market_event_caution_any": False, "market_event_caution_flags": {},
        "orderbook_available": True, "best_bid": "99000", "best_ask": "100000",
        "ask_levels": [{"price": "100000", "size": "10"}],
        "candles_available": True, "observed_daily_candle_count": 100,
        "trailing_turnover_finalized_day_count": 30, "trailing_30d_krw_turnover": Decimal("6000000000"),
    }


def base_core(markets: dict, *, available_at="2026-08-29T00:40:00Z", snapshot_date="2026-08-29"):
    return {
        "snapshot_date": snapshot_date, "available_at": available_at, "manifest_sha256": "d" * 64,
        "markets": markets, "duplicate_market_codes": {},
        "component_hashes": {"upbit_market_all.json.gz": "e" * 64},
    }


CAPTURE_CONTRACT = {
    "market_all_raw_file": "upbit_market_all.json.gz",
    "market_all_endpoint": "https://api.upbit.com/v1/market/all?is_details=true",
}


def taxonomy_with_records(*records):
    return {
        "policy_version": "test-taxonomy/v1",
        "approval_status": "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY",
        "eligible_category": "eligible_crypto",
        "excluded_categories": ["stablecoin", "wrapped", "leveraged", "derivative_like", "unverified_identity"],
        "unknown_asset_policy": "fail_closed_unknown",
        "records": list(records),
    }


def taxonomy_record(canonical_id, category="eligible_crypto", *, effective_from="2026-08-29", effective_to=None):
    return {
        "canonical_asset_id": canonical_id, "category": category,
        "effective_from": effective_from, "effective_to": effective_to,
    }


def evidence(*, sources=("https://example.com/project",), asset_type="native_l1", chain=None,
             contract=None, rebrand=None, rebrand_resolved=None, collision=False, confidence="high",
             researched_at="2026-08-30", effective_from=None, valid_until=None, manual_override_verdict=None,
             manual_override_reason=None):
    return {
        "official_project_sources": list(sources), "asset_type": asset_type, "chain_or_platform": chain,
        "contract_address": contract, "rebrand_or_token_swap_history": rebrand, "rebrand_resolved": rebrand_resolved,
        "ticker_collision_risk": collision, "name_match_confidence": confidence, "notes": "test evidence",
        "researched_at": researched_at, "effective_from": effective_from, "valid_until": valid_until,
        "manual_override_verdict": manual_override_verdict, "manual_override_reason": manual_override_reason,
    }


def real_policy(**overrides):
    policy = {
        "policy_version": "test-policy/v1", "approval_status": "PROPOSED_PAPER_BASELINE_UNRATIFIED",
        "min_listing_history_finalized_days": 90, "turnover_lookback_finalized_days": 30,
        "min_30d_avg_krw_turnover": "5000000000", "max_spread_bps": "150",
        "max_estimated_paper_slippage_bps": "150", "paper_slippage_estimate_notional_krw": "1000000",
        "max_capture_age_hours": "30",
    }
    policy.update(overrides)
    return policy


def build_from_markets(markets: dict, *, taxonomy, evidence_by_id=None, evaluation_as_of="2026-08-30",
                        exceptions_doc=None):
    core = base_core(markets)
    proposals = HARNESS.build_identity_proposals(core, CAPTURE_CONTRACT, review_as_of="2026-08-29",
                                                  exceptions_doc=exceptions_doc)
    findings = IDP.identity_review_findings(proposals)
    blocked = IDP.blocked_markets(findings)
    return ID01.build_registry_candidate(
        core=core, capture_contract=CAPTURE_CONTRACT, taxonomy=taxonomy, proposals=proposals,
        blocked_markets=blocked, evidence_by_id=evidence_by_id or {}, evaluation_as_of=evaluation_as_of,
    )


class ComputeVerdictTests(unittest.TestCase):
    def test_verified_candidate_native_l1_high_confidence(self):
        v, basis = ID01.compute_verdict("SOL", evidence(chain="Solana mainnet"), evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_VERIFIED)

    def test_re_forced_hold_regardless_of_evidence(self):
        v, _ = ID01.compute_verdict("RE", evidence(confidence="high", collision=False), evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_TICKER_COLLISION)

    def test_missing_evidence_holds(self):
        v, _ = ID01.compute_verdict("FOO", None, evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_MISSING_SECOND_SOURCE)

    def test_no_official_sources_holds(self):
        v, _ = ID01.compute_verdict("FOO", evidence(sources=[]), evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_MISSING_SECOND_SOURCE)

    def test_ticker_collision_risk_holds(self):
        v, _ = ID01.compute_verdict("FOO", evidence(collision=True), evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_TICKER_COLLISION)

    def test_low_confidence_holds(self):
        v, _ = ID01.compute_verdict("FOO", evidence(confidence="low"), evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_MISSING_SECOND_SOURCE)

    def test_unresolved_rebrand_holds(self):
        v, _ = ID01.compute_verdict(
            "FOO", evidence(rebrand="X renamed to Y", rebrand_resolved=False), evaluation_as_of="2026-08-30",
        )
        self.assertEqual(v, ID01.VERDICT_HOLD_REBRAND_UNRESOLVED)

    def test_resolved_rebrand_still_verifies(self):
        v, _ = ID01.compute_verdict(
            "FOO", evidence(rebrand="X renamed to Y", rebrand_resolved=True), evaluation_as_of="2026-08-30",
        )
        self.assertEqual(v, ID01.VERDICT_VERIFIED)

    def test_token_without_chain_holds(self):
        v, _ = ID01.compute_verdict("FOO", evidence(asset_type="token", chain=None), evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_MISSING_SECOND_SOURCE)

    def test_token_with_chain_and_contract_verifies(self):
        v, _ = ID01.compute_verdict(
            "FOO", evidence(asset_type="token", chain="Ethereum", contract="0xABCDEF"), evaluation_as_of="2026-08-30",
        )
        self.assertEqual(v, ID01.VERDICT_VERIFIED)

    def test_manual_override_contract_mismatch(self):
        v, basis = ID01.compute_verdict(
            "FOO", evidence(manual_override_verdict="HOLD_CONTRACT_MISMATCH",
                             manual_override_reason="Reviewer found two different contract addresses cited."),
            evaluation_as_of="2026-08-30",
        )
        self.assertEqual(v, ID01.VERDICT_HOLD_CONTRACT_MISMATCH)
        self.assertIn("two different contract", basis)

    def test_manual_override_invalid_value_raises(self):
        with self.assertRaises(ID01.BoundedIdentityRegistryError):
            ID01.compute_verdict("FOO", evidence(manual_override_verdict="NOT_A_REAL_VERDICT"), evaluation_as_of="2026-08-30")

    def test_research_conducted_after_evaluation_as_of_is_not_stale(self):
        # Identity research is normally conducted AFTER the market/taxonomy
        # snapshot it corroborates -- this is expected workflow, not staleness.
        v, _ = ID01.compute_verdict(
            "FOO", evidence(researched_at="2026-08-30", chain="Solana mainnet"), evaluation_as_of="2026-08-29",
        )
        self.assertEqual(v, ID01.VERDICT_VERIFIED)

    def test_explicit_future_effective_from_holds_stale(self):
        v, _ = ID01.compute_verdict("FOO", evidence(effective_from="2099-01-01"), evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_SOURCE_STALE)

    def test_expired_evidence_holds_stale(self):
        v, _ = ID01.compute_verdict(
            "FOO", evidence(researched_at="2020-01-01", valid_until="2021-01-01"), evaluation_as_of="2026-08-30",
        )
        self.assertEqual(v, ID01.VERDICT_HOLD_SOURCE_STALE)

    def test_missing_researched_at_holds_stale(self):
        ev = evidence()
        ev["researched_at"] = None
        v, _ = ID01.compute_verdict("FOO", ev, evaluation_as_of="2026-08-30")
        self.assertEqual(v, ID01.VERDICT_HOLD_SOURCE_STALE)


class NormalizeContractAddressTests(unittest.TestCase):
    def test_case_and_whitespace_normalized_identically(self):
        a = ID01.normalize_contract_address("  0xABCDEF123  ")
        b = ID01.normalize_contract_address("0xabcdef123")
        self.assertEqual(a, b)

    def test_none_and_empty_both_normalize_to_none(self):
        self.assertIsNone(ID01.normalize_contract_address(None))
        self.assertIsNone(ID01.normalize_contract_address("   "))


class BuildRegistryCandidateTests(unittest.TestCase):
    def test_verified_candidate_included_hold_excluded(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SOL"), taxonomy_record("UNKNOWNCOIN"))
        evidence_by_id = {"SOL": evidence(chain="Solana mainnet")}
        result = build_from_markets(
            {"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana"),
             "KRW-UNKNOWNCOIN": market_row(korean_name="언노운코인", english_name="UnknownCoin")},
            taxonomy=taxonomy, evidence_by_id=evidence_by_id,
        )
        verified_markets = {row["market"] for row in result["registry_candidates"]}
        self.assertEqual(verified_markets, {"KRW-SOL"})
        hold_markets = {row["market"]: row["verdict"] for row in result["hold_list"]}
        self.assertEqual(hold_markets["KRW-UNKNOWNCOIN"], ID01.VERDICT_HOLD_MISSING_SECOND_SOURCE)

    def test_out_of_scope_markets_never_appear(self):
        # CHIP-like: no taxonomy record at all -- never a candidate, never a hold row
        taxonomy = taxonomy_with_records(taxonomy_record("SOL"))
        result = build_from_markets(
            {"KRW-SOL": market_row(), "KRW-CHIP": market_row(korean_name="칩", english_name="CHIP")},
            taxonomy=taxonomy, evidence_by_id={"SOL": evidence()},
        )
        all_markets = {r["market"] for r in result["registry_candidates"]} | {r["market"] for r in result["hold_list"]}
        self.assertNotIn("KRW-CHIP", all_markets)

    def test_re_market_never_becomes_registry_candidate(self):
        taxonomy = taxonomy_with_records(taxonomy_record("RE", category="unverified_identity"))
        result = build_from_markets(
            {"KRW-RE": market_row(korean_name="리", english_name="Re")},
            taxonomy=taxonomy, evidence_by_id={"RE": evidence(confidence="high", collision=False)},
        )
        self.assertEqual(result["registry_candidates"], [])
        hold = {r["market"]: r["verdict"] for r in result["hold_list"]}
        self.assertEqual(hold["KRW-RE"], ID01.VERDICT_HOLD_TICKER_COLLISION)

    def test_identity_collision_excludes_from_registry(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SHARED"))
        exceptions_doc = {
            "records": [
                {"source_asset_id": "BTC", "canonical_asset_id": "SHARED"},
                {"source_asset_id": "ETH", "canonical_asset_id": "SHARED"},
            ]
        }
        result = build_from_markets(
            {"KRW-BTC": market_row(), "KRW-ETH": market_row()},
            taxonomy=taxonomy, evidence_by_id={"SHARED": evidence()}, exceptions_doc=exceptions_doc,
        )
        self.assertEqual(result["registry_candidates"], [])
        hold_markets = {r["market"]: r["verdict"] for r in result["hold_list"]}
        self.assertEqual(hold_markets["KRW-BTC"], ID01.VERDICT_HOLD_IDENTITY_COLLISION)
        self.assertEqual(hold_markets["KRW-ETH"], ID01.VERDICT_HOLD_IDENTITY_COLLISION)

    def test_future_dated_taxonomy_record_out_of_scope(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SOL", effective_from="2099-01-01"))
        result = build_from_markets(
            {"KRW-SOL": market_row()}, taxonomy=taxonomy, evidence_by_id={"SOL": evidence()},
        )
        self.assertEqual(result["registry_candidates"], [])
        self.assertEqual(result["hold_list"], [])  # not even held -- genuinely out of scope

    def test_expired_taxonomy_record_out_of_scope(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SOL", effective_from="2020-01-01", effective_to="2021-01-01"))
        result = build_from_markets(
            {"KRW-SOL": market_row()}, taxonomy=taxonomy, evidence_by_id={"SOL": evidence()},
        )
        self.assertEqual(result["registry_candidates"], [])
        self.assertEqual(result["hold_list"], [])

    def test_duplicate_canonical_target_raises_defensively(self):
        # Two markets both claiming candidate "SHARED" WITHOUT having gone
        # through blocked_markets first -- the builder's own defense.
        core = base_core({"KRW-BTC": market_row(), "KRW-ETH": market_row()})
        proposals = HARNESS.build_identity_proposals(core, CAPTURE_CONTRACT, review_as_of="2026-08-29")
        proposals[1]["claim"]["candidateCanonicalAssetId"] = proposals[0]["claim"]["candidateCanonicalAssetId"]
        taxonomy = taxonomy_with_records(taxonomy_record(proposals[0]["claim"]["candidateCanonicalAssetId"]))
        with self.assertRaises(ID01.BoundedIdentityRegistryError):
            ID01.build_registry_candidate(
                core=core, capture_contract=CAPTURE_CONTRACT, taxonomy=taxonomy, proposals=proposals,
                blocked_markets=set(), evidence_by_id={}, evaluation_as_of="2026-08-30",
            )

    def test_duplicate_market_across_two_taxonomy_records_raises(self):
        # Two DIFFERENT canonical ids both resolving (via handcrafted
        # proposals) to the SAME Upbit market -- a distinct defensive check
        # from the duplicate-canonical-target case above.
        core = base_core({"KRW-SHARED": market_row()})
        proposals = [
            {"claim": {"upbitMarket": "KRW-SHARED", "candidateCanonicalAssetId": "SHARED_A"}},
            {"claim": {"upbitMarket": "KRW-SHARED", "candidateCanonicalAssetId": "SHARED_B"}},
        ]
        taxonomy = taxonomy_with_records(taxonomy_record("SHARED_A"), taxonomy_record("SHARED_B"))
        with self.assertRaises(ID01.BoundedIdentityRegistryError):
            ID01.build_registry_candidate(
                core=core, capture_contract=CAPTURE_CONTRACT, taxonomy=taxonomy, proposals=proposals,
                blocked_markets=set(), evidence_by_id={}, evaluation_as_of="2026-08-30",
            )

    def test_determinism_same_input_twice_identical_output(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SOL"))
        markets = {"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")}
        ev = {"SOL": evidence(chain="Solana mainnet")}
        first = build_from_markets(copy.deepcopy(markets), taxonomy=copy.deepcopy(taxonomy), evidence_by_id=copy.deepcopy(ev))
        second = build_from_markets(copy.deepcopy(markets), taxonomy=copy.deepcopy(taxonomy), evidence_by_id=copy.deepcopy(ev))
        self.assertEqual(ID01.canonical_json(first), ID01.canonical_json(second))

    def test_empty_input_produces_empty_output(self):
        result = build_from_markets({}, taxonomy=taxonomy_with_records())
        self.assertEqual(result["registry_candidates"], [])
        self.assertEqual(result["hold_list"], [])
        self.assertEqual(result["evidence"], [])

    def test_registry_candidate_as_mapping_shape(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SOL"))
        result = build_from_markets(
            {"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")},
            taxonomy=taxonomy, evidence_by_id={"SOL": evidence(chain="Solana mainnet")},
        )
        mapping = ID01.registry_candidate_as_mapping(result["registry_candidates"])
        self.assertEqual(mapping, {"KRW-SOL": "SOL"})


class ShadowApplyFunnelTests(unittest.TestCase):
    def test_only_verified_candidates_promote_in_shadow_funnel(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SOL"), taxonomy_record("UNKNOWNCOIN"))
        markets = {
            "KRW-SOL": market_row(korean_name="솔라나", english_name="Solana"),
            "KRW-UNKNOWNCOIN": market_row(korean_name="언노운코인", english_name="UnknownCoin"),
        }
        result = build_from_markets(markets, taxonomy=taxonomy, evidence_by_id={"SOL": evidence(chain="Solana mainnet")})
        registry_mapping = ID01.registry_candidate_as_mapping(result["registry_candidates"])
        core = base_core(markets)
        # available_at defaults to 2026-08-29T00:40:00Z; evaluation_as_of
        # here is 2026-08-30 (this WBS's own evaluation date, matching when
        # the identity research was performed) -- widen max_capture_age_hours
        # so this fixture isn't accidentally testing STALE_CAPTURE instead.
        after = ID01.shadow_apply_funnel(
            core=core, real_policy=real_policy(max_capture_age_hours="48"), real_taxonomy=taxonomy,
            registry_mapping=registry_mapping, blocked_markets=set(), evaluation_as_of="2026-08-30",
        )
        rows = {r["market"]: r for r in after["markets"]}
        self.assertEqual(rows["KRW-SOL"]["state"], UNI.STATE_PAPER_ELIGIBLE)
        self.assertEqual(rows["KRW-UNKNOWNCOIN"]["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(rows["KRW-UNKNOWNCOIN"]["reason"], "IDENTITY_UNRATIFIED")

    def test_no_registry_mapping_leaves_everything_unrated(self):
        taxonomy = taxonomy_with_records(taxonomy_record("SOL"))
        markets = {"KRW-SOL": market_row()}
        core = base_core(markets)
        after = ID01.shadow_apply_funnel(
            core=core, real_policy=real_policy(), real_taxonomy=taxonomy, registry_mapping={},
            blocked_markets=set(), evaluation_as_of="2026-08-30",
        )
        self.assertEqual(after["summary"]["paper_eligible_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
