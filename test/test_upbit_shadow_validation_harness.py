"""P3-12 Shadow Validation Harness regression."""
from __future__ import annotations

import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "upbit_shadow_validation_harness.py"
SPEC = importlib.util.spec_from_file_location("upbit_shadow_validation_harness", MODULE_PATH)
H = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(H)

UNI = H.UNI
IDP = H.IDP
RUN_SCRIPT = ROOT / ".github" / "scripts" / "upbit_shadow_validation_harness_run.py"
NATURAL = ROOT / "data" / "observations" / "upbit_p3_12_shadow_validation" / "2026-08-29" / "packet.json"


def _load_run_module():
    spec = importlib.util.spec_from_file_location("upbit_shadow_validation_harness_run_test", RUN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def market_row(*, warning=False, orderbook_available=True, candles_available=True,
                best_bid="99000", best_ask="100000", ask_size="10",
                candle_count=100, turnover_days=30, turnover="6000000000",
                market_all_available=True, korean_name="코인", english_name="Coin"):
    entry = {"market_all_available": market_all_available}
    if market_all_available:
        entry.update({
            "korean_name": korean_name, "english_name": english_name,
            "market_event_warning": warning, "market_event_caution_any": False,
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
        "manifest_sha256": "d" * 64,
        "markets": markets,
        "duplicate_market_codes": {},
        "component_hashes": {"upbit_market_all.json.gz": "e" * 64},
    }


CAPTURE_CONTRACT = {
    "market_all_raw_file": "upbit_market_all.json.gz",
    "market_all_endpoint": "https://api.upbit.com/v1/market/all?is_details=true",
}


def real_policy(**overrides):
    policy = {
        "policy_version": "test-policy/v1",
        "approval_status": "PROPOSED_PAPER_BASELINE_UNRATIFIED",
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


def real_taxonomy(**overrides):
    taxonomy = {
        "policy_version": "test-taxonomy/v1",
        "approval_status": "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY",
        "eligible_category": "eligible_crypto",
        "excluded_categories": ["stablecoin", "wrapped", "leveraged", "derivative_like", "unverified_identity"],
        "unknown_asset_policy": "fail_closed_unknown",
        "records": [
            {"canonical_asset_id": "USDT", "category": "stablecoin", "effective_from": "2026-08-01", "effective_to": None},
            {"canonical_asset_id": "BTC", "category": "eligible_crypto", "effective_from": "2026-08-01", "effective_to": None},
            {"canonical_asset_id": "ETH", "category": "eligible_crypto", "effective_from": "2026-08-01", "effective_to": None},
        ],
    }
    taxonomy.update(overrides)
    return taxonomy


KRAKEN_POLICY_VERSION = "crypto_breadth_exclusion_taxonomy/v2"
KRAKEN_EXCLUDED_CATEGORIES = ("commodity_linked", "fiat", "stablecoin", "staked", "unverified_identity", "wrapped")


def kraken_record(canonical_id, category, *, effective_from="2026-08-01", effective_to=None, reason="reason"):
    return {
        "canonical_asset_id": canonical_id, "category": category,
        "effective_from": effective_from, "effective_to": effective_to, "reason": reason,
    }


def kraken_records(*rows) -> dict:
    """The ``{canonical_id: row}`` shape ``build_shadow_packet`` /
    ``kraken_cross_reference_signal`` / ``taxonomy_audit`` consume directly --
    i.e. already past ``load_kraken_breadth_taxonomy()``'s own validation.
    """
    if not rows:
        rows = (
            kraken_record("BTC", "eligible_crypto", reason="native crypto asset"),
            kraken_record("ETH", "eligible_crypto", reason="native crypto asset"),
            kraken_record("SOL", "eligible_crypto", reason="native crypto asset"),
            kraken_record("RE", "unverified_identity", reason="ticker collision"),
            kraken_record("XAUT", "commodity_linked", reason="gold-linked token"),
        )
    return {row["canonical_asset_id"]: row for row in rows}


def kraken_taxonomy_doc(*, approval_status="RATIFIED", policy_version=KRAKEN_POLICY_VERSION,
                         eligible_category="eligible_crypto", excluded_categories=KRAKEN_EXCLUDED_CATEGORIES,
                         records=None):
    """The full on-disk JSON shape, for exercising ``load_kraken_breadth_taxonomy()``
    (i.e. ``identity/candidate_identity_gap_inventory.py::_load_taxonomy()``) itself."""
    if records is None:
        records = [kraken_record("BTC", "eligible_crypto"), kraken_record("ETH", "eligible_crypto")]
    return {
        "approval_status": approval_status, "policy_version": policy_version,
        "eligible_category": eligible_category, "excluded_categories": list(excluded_categories),
        "records": records,
    }


def write_kraken_taxonomy_doc(tmp_dir: Path, doc: dict) -> Path:
    path = Path(tmp_dir) / "kraken_taxonomy.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class ShadowIdentityRegistryTests(unittest.TestCase):
    def test_colliding_markets_excluded_from_shadow_registry(self):
        core = base_core({"KRW-BTC": market_row(), "KRW-BTC2": market_row()})
        proposals = H.build_identity_proposals(core, CAPTURE_CONTRACT, review_as_of="2026-08-28")
        # Force both proposals to the same candidate id to simulate a collision.
        proposals[1]["claim"]["candidateCanonicalAssetId"] = proposals[0]["claim"]["candidateCanonicalAssetId"]
        findings = IDP.identity_review_findings(proposals)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["finding"], "DUPLICATE_CANONICAL_TARGET")
        registry = H.shadow_identity_registry(proposals, findings)
        # Neither colliding market is guessed into the shadow registry.
        self.assertNotIn("KRW-BTC", registry)
        self.assertNotIn("KRW-BTC2", registry)


class BuildShadowPacketTests(unittest.TestCase):
    def _run(self, markets: dict, *, policy=None, taxonomy=None, kraken=None,
              evaluation_as_of="2026-08-28", exceptions_doc=None):
        core = base_core(markets)
        return H.build_shadow_packet(
            core=core, capture_contract=CAPTURE_CONTRACT,
            real_policy=policy or real_policy(), real_taxonomy=taxonomy or real_taxonomy(),
            exceptions_doc=exceptions_doc, kraken_records_by_id=kraken if kraken is not None else kraken_records(),
            evaluation_as_of=evaluation_as_of, code_commit_sha="a" * 40,
            file_hashes={"universe_policy_file_sha256": "b" * 64, "taxonomy_file_sha256": "c" * 64},
        )

    def test_shadow_apply_never_touches_disk_and_reports_boundary_flags(self):
        packet = self._run({"KRW-BTC": market_row()})
        self.assertFalse(packet["shadow_apply_boundary"]["mutates_canonical_config_files"])
        self.assertFalse(packet["shadow_apply_boundary"]["thresholds_changed"])
        self.assertFalse(packet["shadow_apply_boundary"]["taxonomy_categories_or_records_changed"])
        for value in packet["authority"].values():
            if value is not True:
                self.assertFalse(value)
        self.assertTrue(packet["authority"]["review_only"])
        self.assertEqual(packet["review_status"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")

    def test_shadow_apply_promotes_a_clean_market_to_paper_eligible(self):
        packet = self._run({"KRW-BTC": market_row()})
        self.assertEqual(
            packet["funnel"]["before_current_production_mechanical_collision_included"]["paper_eligible_count"], 0
        )
        self.assertEqual(packet["funnel"]["after_shadow_if_ratified_as_currently_proposed"]["paper_eligible_count"], 1)
        row = packet["markets_after_shadow_apply"][0]
        self.assertEqual(row["state"], UNI.STATE_PAPER_ELIGIBLE)
        # authority is still hardcoded false even for a shadow PAPER_ELIGIBLE row
        for value in row["authority"].values():
            self.assertFalse(value)

    def test_real_committed_config_never_mutated_on_disk(self):
        before_policy = UNI.POLICY_PATH.read_text(encoding="utf-8")
        before_taxonomy = UNI.TAXONOMY_PATH.read_text(encoding="utf-8")
        self._run({"KRW-BTC": market_row()})
        self.assertEqual(UNI.POLICY_PATH.read_text(encoding="utf-8"), before_policy)
        self.assertEqual(UNI.TAXONOMY_PATH.read_text(encoding="utf-8"), before_taxonomy)

    def test_identity_collision_stays_blocked_in_shadow_apply_and_flagged_for_review(self):
        core = base_core({"KRW-BTC": market_row(), "KRW-BTC2": market_row()})
        proposals = H.build_identity_proposals(core, CAPTURE_CONTRACT, review_as_of="2026-08-28")
        proposals[1]["claim"]["candidateCanonicalAssetId"] = proposals[0]["claim"]["candidateCanonicalAssetId"]
        findings = IDP.identity_review_findings(proposals)
        packet = H.build_shadow_packet(
            core=core, capture_contract=CAPTURE_CONTRACT, real_policy=real_policy(), real_taxonomy=real_taxonomy(),
            exceptions_doc=None, kraken_records_by_id=kraken_records(), evaluation_as_of="2026-08-28",
            code_commit_sha="a" * 40, file_hashes={},
        )
        # (packet above used the harness's own internal proposal-building, so
        # recompute the collision scenario end-to-end via a monkeypatched
        # proposal set is unnecessary -- instead assert the lower-level
        # registry/queue functions directly against the forced collision.)
        registry = H.shadow_identity_registry(proposals, findings)
        self.assertEqual(registry, {})
        cross_ref = H.kraken_cross_reference_signal(proposals, kraken_records(), as_of="2026-08-28")
        queue = H.identity_manual_review_queue(proposals, findings, cross_ref)
        markets_in_queue = {row["market"] for row in queue}
        self.assertIn("KRW-BTC", markets_in_queue)
        self.assertIn("KRW-BTC2", markets_in_queue)
        for row in queue:
            self.assertIn("DUPLICATE_CANONICAL_TARGET_COLLISION", row["reasons"])
        del packet  # only used to exercise build_shadow_packet's own (non-colliding) path above

    def test_missing_field_market_reported_as_unresolved_no_data(self):
        packet = self._run({"KRW-BTC": market_row(orderbook_available=False)})
        rows = packet["markets_after_shadow_apply"]
        self.assertEqual(rows[0]["reason"], "MISSING_FIELD:orderbook")
        items = {row["market"]: row["reason"] for row in packet["unresolved_no_data_items"]}
        self.assertEqual(items.get("KRW-BTC"), "MISSING_FIELD:orderbook")

    def test_stale_capture_market_stays_observation_pool_in_shadow_apply(self):
        packet = self._run({"KRW-BTC": market_row()}, evaluation_as_of="2026-08-28")
        core = base_core({"KRW-BTC": market_row()}, available_at="2026-08-26T00:40:00Z")
        proposals = H.build_identity_proposals(core, CAPTURE_CONTRACT, review_as_of="2026-08-28")
        findings = IDP.identity_review_findings(proposals)
        stale_packet = H.build_shadow_packet(
            core=core, capture_contract=CAPTURE_CONTRACT, real_policy=real_policy(), real_taxonomy=real_taxonomy(),
            exceptions_doc=None, kraken_records_by_id=kraken_records(), evaluation_as_of="2026-08-28",
            code_commit_sha="a" * 40, file_hashes={},
        )
        row = stale_packet["markets_after_shadow_apply"][0]
        self.assertEqual(row["state"], UNI.STATE_OBSERVATION_POOL)
        self.assertEqual(row["reason"], "STALE_CAPTURE")
        del packet

    def test_spread_not_computable_market_reported_as_unresolved_no_data(self):
        entry = market_row()
        entry["ask_levels"] = []  # depth present flag true but no levels -> not computable
        packet = self._run({"KRW-BTC": entry})
        row = packet["markets_after_shadow_apply"][0]
        self.assertIn(row["reason"], ("SPREAD_NOT_COMPUTABLE", "SLIPPAGE_NOT_COMPUTABLE"))
        markets_flagged = {item["market"] for item in packet["unresolved_no_data_items"]}
        self.assertIn("KRW-BTC", markets_flagged)

    def test_taxonomy_candidate_flagged_for_stablecoin_name_pattern_not_yet_recorded(self):
        packet = self._run({"KRW-XYZUSD": market_row(korean_name="엑스와이지유에스디", english_name="XYZ USD")})
        candidates = {row["market"]: row for row in packet["taxonomy_audit"]["candidates"]}
        self.assertIn("KRW-XYZUSD", candidates)
        self.assertIn("stablecoin", candidates["KRW-XYZUSD"]["suggested_categories"])

    def test_taxonomy_already_recorded_market_not_double_flagged_as_candidate(self):
        packet = self._run({"KRW-USDT": market_row(korean_name="테더", english_name="Tether")})
        candidate_markets = {row["market"] for row in packet["taxonomy_audit"]["candidates"]}
        self.assertNotIn("KRW-USDT", candidate_markets)
        already = {row["market"]: row["existing_category"] for row in packet["taxonomy_audit"]["already_recorded"]}
        self.assertEqual(already.get("KRW-USDT"), "stablecoin")

    def test_kraken_ratified_unverified_identity_cross_reference_flags_manual_review(self):
        packet = self._run({"KRW-RE": market_row(korean_name="리", english_name="Re")})
        queue_markets = {row["market"]: row for row in packet["identity_review"]["manual_review_queue"]}
        self.assertIn("KRW-RE", queue_markets)
        self.assertIn("KRAKEN_RATIFIED_UNVERIFIED_IDENTITY_SAME_CANONICAL_ID", queue_markets["KRW-RE"]["reasons"])
        candidates = {row["market"]: row for row in packet["taxonomy_audit"]["candidates"]}
        self.assertIn("unverified_identity", candidates["KRW-RE"]["suggested_categories"])

    def test_eligible_crypto_kraken_match_is_not_flagged_as_taxonomy_candidate(self):
        # SOL: present in the Kraken RATIFIED registry as eligible_crypto, but
        # deliberately absent from the local (unratified) Upbit taxonomy
        # fixture's own records -- proves a positive Kraken match alone never
        # produces a "needs review" candidate row.
        packet = self._run({"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")})
        candidate_markets = {row["market"] for row in packet["taxonomy_audit"]["candidates"]}
        self.assertNotIn("KRW-SOL", candidate_markets)
        self.assertEqual(packet["taxonomy_audit"]["corroborated_eligible_count"], 1)

    def test_schema_gap_surfaced_for_category_absent_from_upbit_taxonomy(self):
        packet = self._run({"KRW-XAUT": market_row(korean_name="테더골드", english_name="Tether Gold")})
        gap_markets = {row["market"] for row in packet["taxonomy_audit"]["schema_gaps"]}
        self.assertIn("KRW-XAUT", gap_markets)

    def test_category_definition_gap_reported_for_undefined_categories(self):
        packet = self._run({"KRW-BTC": market_row()})
        self.assertIn("leveraged", packet["taxonomy_audit"]["category_definition_gaps"])
        self.assertIn("derivative_like", packet["taxonomy_audit"]["category_definition_gaps"])
        self.assertNotIn("stablecoin", packet["taxonomy_audit"]["category_definition_gaps"])

    def test_empty_input_produces_zero_funnel_without_crashing(self):
        packet = self._run({})
        self.assertEqual(
            packet["funnel"]["before_current_production_mechanical_collision_included"]["market_count"], 0
        )
        self.assertEqual(packet["funnel"]["after_shadow_if_ratified_as_currently_proposed"]["market_count"], 0)
        self.assertEqual(packet["taxonomy_audit"]["candidates"], [])
        self.assertEqual(packet["identity_review"]["manual_review_queue"], [])
        self.assertEqual(packet["slippage_curve_sample"], [])
        self.assertEqual(packet["unresolved_no_data_items"], [])
        self.assertEqual(packet["gate_pass_fail_distribution"]["market_count"], 0)

    def test_determinism_same_input_twice_identical_output(self):
        markets = {"KRW-BTC": market_row(), "KRW-ETH": market_row(turnover="1000000000")}
        first = self._run(copy.deepcopy(markets))
        second = self._run(copy.deepcopy(markets))
        self.assertEqual(H.canonical_json(first), H.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, H.payload_sha256(second))

    def test_slippage_curve_uses_exact_classifier_math_at_policy_multiples(self):
        packet = self._run({"KRW-BTC": market_row(best_bid="99000", best_ask="100000", ask_size="1000")})
        self.assertEqual(len(packet["slippage_curve_sample"]), 1)
        row = packet["slippage_curve_sample"][0]
        self.assertEqual(row["market"], "KRW-BTC")
        self.assertEqual(set(row["slippage_bps_by_notional_krw"]), {"500000", "1000000", "3000000", "5000000"})
        for value in row["slippage_bps_by_notional_krw"].values():
            self.assertIsNotNone(value)  # ample uniform depth at one price -> always computable

    def test_supplemental_hypothetical_scenario_never_touches_primary_funnel(self):
        # SOL is Kraken-corroborated eligible_crypto but absent from the local
        # taxonomy fixture -- the primary "as written" scenario must still
        # show it as TAXONOMY_UNKNOWN/OBSERVATION_POOL; only the clearly
        # separate supplemental scenario may promote it.
        packet = self._run({"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")})
        self.assertEqual(packet["funnel"]["after_shadow_if_ratified_as_currently_proposed"]["paper_eligible_count"], 0)
        primary_row = next(r for r in packet["markets_after_shadow_apply"] if r["market"] == "KRW-SOL")
        self.assertEqual(primary_row["reason"], "TAXONOMY_UNKNOWN")
        supplemental = packet["funnel_supplemental_hypothetical"]
        self.assertEqual(supplemental["hypothetical_records_added"], 1)
        self.assertEqual(supplemental["after_with_kraken_corroborated_eligible_records"]["paper_eligible_count"], 1)

    def test_supplemental_scenario_does_not_mutate_real_taxonomy_dict_or_disk(self):
        real_tax = real_taxonomy()
        original_record_count = len(real_tax["records"])
        self._run({"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")}, taxonomy=real_tax)
        self.assertEqual(len(real_tax["records"]), original_record_count)

    def test_before_baseline_includes_todays_mechanical_collision_hold_like_real_production(self):
        # CIO review (PR #459): before_current_production_mechanical_collision_included
        # must inject today's mechanical DUPLICATE_CANONICAL_TARGET collision
        # set, exactly like .github/scripts/upbit_universe_populate.py's real
        # production rebuild() does today -- proven end-to-end through the
        # public build_shadow_packet() API (not by poking internals) using an
        # identity exception that forces two distinct markets to the same
        # candidate canonical id. Today's real snapshot has 0 collisions, so
        # this can't be proven against the natural packet alone.
        exceptions_doc = {
            "records": [
                {"source_asset_id": "BTC", "canonical_asset_id": "SHARED"},
                {"source_asset_id": "ETH", "canonical_asset_id": "SHARED"},
            ]
        }
        packet = self._run(
            {"KRW-BTC": market_row(), "KRW-ETH": market_row()},
            exceptions_doc=exceptions_doc,
        )
        self.assertEqual(packet["identity_review"]["blocked_market_count"], 2)
        before = packet["funnel"]["before_current_production_mechanical_collision_included"]
        self.assertEqual(before["blocked_count"], 2)
        rows = {row["market"]: row for row in packet["markets_after_shadow_apply"]}
        # the shadow ("after") packet is blocked too, but the whole point of
        # this test is that the BEFORE baseline is *also* blocked, not
        # promoted to a falsely-clean 0-collision current-production picture
        self.assertEqual(rows["KRW-BTC"]["state"], UNI.STATE_BLOCKED)
        self.assertEqual(rows["KRW-ETH"]["state"], UNI.STATE_BLOCKED)

    def test_funnel_definitions_document_the_before_baseline_precisely(self):
        packet = self._run({"KRW-BTC": market_row()})
        definitions = packet["funnel_definitions"]
        self.assertIn(
            "before_current_production_mechanical_collision_included", definitions,
        )
        self.assertIn("mechanical", definitions["before_current_production_mechanical_collision_included"])
        self.assertIn(
            "before_current_production_mechanical_collision_included", packet["funnel"],
        )

    def test_gate_pass_fail_distribution_buckets_reasons_correctly(self):
        packet = self._run({
            "KRW-BTC": market_row(),  # passes everything
            "KRW-ETH": market_row(turnover="1000000000"),  # liquidity fail
        })
        dist = packet["gate_pass_fail_distribution"]
        self.assertEqual(dist["market_count"], 2)
        self.assertEqual(dist["liquidity"]["fail"], 1)
        self.assertEqual(dist["spread"]["fail"], 0)
        self.assertEqual(dist["slippage"]["fail"], 0)


class KrakenTaxonomyLoaderTests(unittest.TestCase):
    """load_kraken_breadth_taxonomy() delegates to the shared, already-tested
    identity/candidate_identity_gap_inventory.py::_load_taxonomy() contract --
    these tests prove that delegation actually enforces every fail-closed
    check the CIO review asked for, using a temp file (never the real
    committed config).
    """

    def test_real_committed_kraken_taxonomy_loads_ratified_v2_with_no_duplicates(self):
        doc, records_by_id = H.load_kraken_breadth_taxonomy()
        self.assertEqual(doc["approval_status"], "RATIFIED")
        self.assertEqual(doc["policy_version"], KRAKEN_POLICY_VERSION)
        self.assertEqual(len(records_by_id), len(doc["records"]))  # no duplicates silently dropped/merged

    def test_unratified_kraken_taxonomy_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kraken_taxonomy_doc(tmp, kraken_taxonomy_doc(approval_status="PROPOSED_UNRATIFIED"))
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.load_kraken_breadth_taxonomy(path)

    def test_wrong_policy_version_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kraken_taxonomy_doc(tmp, kraken_taxonomy_doc(policy_version="crypto_breadth_exclusion_taxonomy/v1"))
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.load_kraken_breadth_taxonomy(path)

    def test_invalid_category_vocabulary_shape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kraken_taxonomy_doc(tmp, kraken_taxonomy_doc(excluded_categories=[]))
            doc = json.loads(path.read_text())
            doc["eligible_category"] = None  # not a string
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.load_kraken_breadth_taxonomy(path)

    def test_record_with_category_outside_vocabulary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kraken_taxonomy_doc(tmp, kraken_taxonomy_doc(
                records=[kraken_record("ZZZ", "not_a_real_category")]
            ))
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.load_kraken_breadth_taxonomy(path)

    def test_duplicate_canonical_asset_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kraken_taxonomy_doc(tmp, kraken_taxonomy_doc(records=[
                kraken_record("BTC", "eligible_crypto"),
                kraken_record("BTC", "eligible_crypto", effective_from="2026-08-15"),
            ]))
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.load_kraken_breadth_taxonomy(path)

    def test_malformed_effective_from_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kraken_taxonomy_doc(tmp, kraken_taxonomy_doc(records=[
                kraken_record("BTC", "eligible_crypto", effective_from="not-a-date")
            ]))
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.load_kraken_breadth_taxonomy(path)

    def test_reversed_effective_interval_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_kraken_taxonomy_doc(tmp, kraken_taxonomy_doc(records=[
                kraken_record("BTC", "eligible_crypto", effective_from="2026-08-20", effective_to="2026-08-01")
            ]))
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.load_kraken_breadth_taxonomy(path)

    def test_future_dated_record_is_inactive_not_an_error(self):
        # A record that is merely not yet effective is normal, expected
        # registry shape -- it must resolve to "absent" for consumption
        # purposes, not raise.
        records = kraken_records(kraken_record("ZOOM", "eligible_crypto", effective_from="2099-01-01"))
        self.assertIsNone(H._active_kraken_record("ZOOM", "2026-08-29", records))

    def test_expired_record_is_inactive_not_an_error(self):
        records = kraken_records(
            kraken_record("OLDCOIN", "eligible_crypto", effective_from="2020-01-01", effective_to="2021-01-01")
        )
        self.assertIsNone(H._active_kraken_record("OLDCOIN", "2026-08-29", records))

    def test_active_record_resolves_on_its_effective_date(self):
        records = kraken_records(
            kraken_record("MIDCOIN", "eligible_crypto", effective_from="2026-08-01", effective_to="2026-12-31")
        )
        row = H._active_kraken_record("MIDCOIN", "2026-08-29", records)
        self.assertIsNotNone(row)
        self.assertEqual(row["category"], "eligible_crypto")


class KrakenActiveRecordConsumptionTests(unittest.TestCase):
    """evaluation_as_of-aware consumption of Kraken corroboration, exercised
    through the full build_shadow_packet() pipeline.
    """

    def _run(self, markets: dict, *, kraken=None, evaluation_as_of="2026-08-28"):
        core = base_core(markets)
        return H.build_shadow_packet(
            core=core, capture_contract=CAPTURE_CONTRACT,
            real_policy=real_policy(), real_taxonomy=real_taxonomy(),
            exceptions_doc=None, kraken_records_by_id=kraken if kraken is not None else kraken_records(),
            evaluation_as_of=evaluation_as_of, code_commit_sha="a" * 40,
            file_hashes={},
        )

    def test_not_yet_effective_kraken_record_never_corroborates_supplemental_scenario(self):
        records = kraken_records(
            kraken_record("FUTURECOIN", "eligible_crypto", effective_from="2099-01-01"),
        )
        packet = self._run(
            {"KRW-FUTURECOIN": market_row(korean_name="퓨처코인", english_name="FutureCoin")},
            kraken=records,
        )
        self.assertEqual(packet["taxonomy_audit"]["corroborated_eligible_count"], 0)
        self.assertEqual(packet["funnel_supplemental_hypothetical"]["hypothetical_records_added"], 0)
        self.assertEqual(packet["identity_review"]["cross_reference"]["present_in_registry_count"], 0)

    def test_expired_kraken_record_never_corroborates_supplemental_scenario(self):
        records = kraken_records(
            kraken_record("OLDCOIN", "eligible_crypto", effective_from="2020-01-01", effective_to="2021-01-01"),
        )
        packet = self._run(
            {"KRW-OLDCOIN": market_row(korean_name="올드코인", english_name="OldCoin")},
            kraken=records,
        )
        self.assertEqual(packet["taxonomy_audit"]["corroborated_eligible_count"], 0)
        self.assertEqual(packet["funnel_supplemental_hypothetical"]["hypothetical_records_added"], 0)

    def test_active_kraken_record_does_corroborate_supplemental_scenario(self):
        records = kraken_records(
            kraken_record("ACTIVECOIN", "eligible_crypto", effective_from="2026-01-01"),
        )
        packet = self._run(
            {"KRW-ACTIVECOIN": market_row(korean_name="액티브코인", english_name="ActiveCoin")},
            kraken=records,
        )
        self.assertEqual(packet["taxonomy_audit"]["corroborated_eligible_count"], 1)
        self.assertEqual(packet["funnel_supplemental_hypothetical"]["hypothetical_records_added"], 1)


class GitCommitShaTests(unittest.TestCase):
    def test_real_repo_commit_sha_is_well_formed(self):
        sha = H.git_commit_sha()
        self.assertRegex(sha, r"^[0-9a-f]{40}$")

    def test_commit_sha_lookup_fails_closed_on_bad_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(H.ShadowValidationHarnessError):
                H.git_commit_sha(root=Path(tmp))


class NaturalPacketTests(unittest.TestCase):
    """Regression against the actual committed evidence packet for 2026-08-29."""

    def test_natural_packet_is_hash_bound_review_only_and_never_mutates_config(self):
        packet = json.loads(NATURAL.read_text(encoding="utf-8"))
        expected_hash = H.payload_sha256({k: v for k, v in packet.items() if k != "payload_sha256"})
        self.assertEqual(packet["payload_sha256"], expected_hash)
        self.assertEqual(packet["review_status"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
        self.assertFalse(packet["shadow_apply_boundary"]["mutates_canonical_config_files"])
        self.assertTrue(packet["authority"]["review_only"])
        for field, value in packet["authority"].items():
            if field != "review_only":
                self.assertIs(value, False, field)
        self.assertRegex(packet["code_commit_sha"], r"^[0-9a-f]{40}$")

    def test_natural_packet_surfaces_known_real_findings(self):
        packet = json.loads(NATURAL.read_text(encoding="utf-8"))
        candidate_markets = {row["market"] for row in packet["taxonomy_audit"]["candidates"]}
        for expected in ("KRW-USD1", "KRW-USDG", "KRW-XAUT", "KRW-RE"):
            self.assertIn(expected, candidate_markets)
        queue_markets = {row["market"] for row in packet["identity_review"]["manual_review_queue"]}
        self.assertIn("KRW-RE", queue_markets)

    def test_run_script_is_idempotent_and_tamper_fails_closed(self):
        run_module = _load_run_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            first = run_module.populate("2026-08-29", data_root=output)
            second = run_module.populate("2026-08-29", data_root=output)
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            target = Path(first["path"])
            packet = json.loads(target.read_text(encoding="utf-8"))
            packet["funnel"]["after_shadow_if_ratified_as_currently_proposed"]["paper_eligible_count"] = 999
            target.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaises(run_module.HARNESS.ShadowValidationHarnessError):
                run_module.populate("2026-08-29", data_root=output)

    def test_run_script_rejects_existing_packet_whose_declared_hash_is_self_inconsistent(self):
        # CIO review (PR #459), P1: previously, mutating ONLY payload_sha256
        # in an existing packet (body left byte-identical) passed silently as
        # "verified_existing" because the drift check excluded that field
        # from comparison on both sides. The self-hash check must catch this
        # BEFORE that content-diff check ever runs.
        run_module = _load_run_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            first = run_module.populate("2026-08-29", data_root=output)
            self.assertEqual(first["outcome"], "populated")
            target = Path(first["path"])
            packet = json.loads(target.read_text(encoding="utf-8"))
            packet["payload_sha256"] = "f" * 64  # well-formed hex, but no longer self-consistent
            target.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaisesRegex(run_module.HARNESS.ShadowValidationHarnessError, "EXISTING_PACKET_HASH_INVALID"):
                run_module.populate("2026-08-29", data_root=output)

    def test_run_script_rejects_existing_packet_with_missing_or_malformed_hash_field(self):
        run_module = _load_run_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            first = run_module.populate("2026-08-29", data_root=output)
            target = Path(first["path"])
            packet = json.loads(target.read_text(encoding="utf-8"))
            del packet["payload_sha256"]
            target.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaisesRegex(run_module.HARNESS.ShadowValidationHarnessError, "EXISTING_PACKET_HASH_INVALID"):
                run_module.populate("2026-08-29", data_root=output)

            packet["payload_sha256"] = "not-hex"
            target.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaisesRegex(run_module.HARNESS.ShadowValidationHarnessError, "EXISTING_PACKET_HASH_INVALID"):
                run_module.populate("2026-08-29", data_root=output)

    def test_run_script_never_writes_to_any_canonical_config_path(self):
        source = RUN_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('"approval_status": "RATIFIED"', source)
        harness_source = MODULE_PATH.read_text(encoding="utf-8")
        # No JSON-literal '"approval_status": "RATIFIED"' is ever hardcoded
        # in the harness source itself either -- the only place the string
        # "RATIFIED" appears at all is (a) shadow_ratify()'s in-memory
        # Python assignment (`shadow["approval_status"] = "RATIFIED"`, never
        # written to disk) and (b) a docstring describing the read-only
        # comparison load_kraken_breadth_taxonomy() performs against the
        # already-committed, already-RATIFIED Kraken file.
        self.assertNotIn('"approval_status": "RATIFIED"', harness_source)
        self.assertEqual(harness_source.count('"RATIFIED"'), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
