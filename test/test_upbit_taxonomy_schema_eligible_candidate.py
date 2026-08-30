"""P3-12-TAX-01 Upbit taxonomy schema & eligible-content candidate builder regression."""
from __future__ import annotations

import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "upbit_taxonomy_schema_eligible_candidate.py"
SPEC = importlib.util.spec_from_file_location("upbit_taxonomy_schema_eligible_candidate", MODULE_PATH)
TAX = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(TAX)

UNI = TAX.UNI
IDP = TAX.IDP
HARNESS = TAX.HARNESS
BUILD_SCRIPT = ROOT / ".github" / "scripts" / "upbit_taxonomy_schema_eligible_candidate_build.py"
NATURAL = ROOT / "data" / "observations" / "upbit_taxonomy_schema_eligible_candidate" / "2026-08-29" / "packet.json"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("upbit_taxonomy_schema_eligible_candidate_build_test", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def market_row(*, korean_name="코인", english_name="Coin", warning=False):
    return {
        "market_all_available": True, "korean_name": korean_name, "english_name": english_name,
        "market_event_warning": warning, "market_event_caution_any": False, "market_event_caution_flags": {},
        "orderbook_available": True, "best_bid": "99000", "best_ask": "100000",
        "ask_levels": [{"price": "100000", "size": "10"}],
        "candles_available": True, "observed_daily_candle_count": 100,
        "trailing_turnover_finalized_day_count": 30, "trailing_30d_krw_turnover": Decimal("180000000000"),
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


def real_taxonomy(**overrides):
    taxonomy = {
        "policy_version": "test-taxonomy/v1",
        "approval_status": "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY",
        "eligible_category": "eligible_crypto",
        "excluded_categories": ["stablecoin", "wrapped", "leveraged", "derivative_like", "unverified_identity"],
        "unknown_asset_policy": "fail_closed_unknown",
        "records": [
            {"canonical_asset_id": "USDT", "category": "stablecoin", "effective_from": "2026-08-01", "effective_to": None},
        ],
    }
    taxonomy.update(overrides)
    return taxonomy


def kraken_record(canonical_id, category, *, effective_from="2026-08-01", effective_to=None, reason="reason"):
    return {
        "canonical_asset_id": canonical_id, "category": category,
        "effective_from": effective_from, "effective_to": effective_to, "reason": reason,
    }


def kraken_records(*rows) -> dict:
    return {row["canonical_asset_id"]: row for row in rows}


def build_from_markets(markets: dict, *, taxonomy=None, kraken=None, evaluation_as_of="2026-08-29",
                        exceptions_doc=None):
    core = base_core(markets)
    proposals = HARNESS.build_identity_proposals(core, CAPTURE_CONTRACT, review_as_of="2026-08-29",
                                                  exceptions_doc=exceptions_doc)
    findings = IDP.identity_review_findings(proposals)
    blocked = IDP.blocked_markets(findings)
    return TAX.build_candidate(
        core=core, capture_contract=CAPTURE_CONTRACT, real_taxonomy=taxonomy or real_taxonomy(),
        kraken_records_by_id=kraken if kraken is not None else kraken_records(), proposals=proposals,
        blocked_markets=blocked, evaluation_as_of=evaluation_as_of,
    )


class BuildCandidateTests(unittest.TestCase):
    def test_approval_status_never_changed(self):
        result = build_from_markets({"KRW-BTC": market_row()},
                                     kraken=kraken_records(kraken_record("BTC", "eligible_crypto")))
        self.assertEqual(result["candidate_taxonomy"]["approval_status"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")

    def test_kraken_corroborated_eligible_crypto_drafted(self):
        result = build_from_markets({"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")},
                                     kraken=kraken_records(kraken_record("SOL", "eligible_crypto", reason="native asset")))
        self.assertEqual(len(result["new_records"]), 1)
        record = result["new_records"][0]
        self.assertEqual(record["canonical_asset_id"], "SOL")
        self.assertEqual(record["category"], "eligible_crypto")
        self.assertEqual(record["effective_from"], "2026-08-29")
        self.assertIsNone(record["effective_to"])
        self.assertEqual(len(result["evidence"]), 1)
        ev = result["evidence"][0]
        self.assertEqual(ev["upbit_market"], "KRW-SOL")
        self.assertEqual(ev["kraken_corroboration"]["canonical_asset_id"], "SOL")
        self.assertEqual(ev["generation_rule"], TAX.GENERATION_RULE)

    def test_re_gets_unverified_identity_never_eligible_and_stays_on_hold_list(self):
        result = build_from_markets(
            {"KRW-RE": market_row(korean_name="리", english_name="Re")},
            kraken=kraken_records(kraken_record("RE", "unverified_identity", reason="ticker collision")),
        )
        self.assertEqual(len(result["new_records"]), 1)
        self.assertEqual(result["new_records"][0]["category"], "unverified_identity")
        hold_markets = {row["market"]: row for row in result["hold_list"]}
        self.assertIn("KRW-RE", hold_markets)
        self.assertEqual(
            hold_markets["KRW-RE"]["reason"], "IDENTITY_TICKER_COLLISION_PRECEDENT_KRAKEN_UNVERIFIED_IDENTITY",
        )

    def test_chip_never_becomes_stablecoin_from_name_pattern_alone(self):
        # Even with a Kraken record that WOULD corroborate stablecoin, CHIP
        # is an explicit CIO-directed no-auto-classify exception.
        result = build_from_markets(
            {"KRW-CHIP": market_row(korean_name="유에스디에이아이", english_name="USD.AI")},
            kraken=kraken_records(kraken_record("CHIP", "stablecoin", reason="looks stable")),
        )
        self.assertEqual(result["new_records"], [])
        hold = {row["market"]: row for row in result["hold_list"]}
        self.assertIn("KRW-CHIP", hold)
        self.assertEqual(hold["KRW-CHIP"]["reason"], "NO_INDEPENDENT_STABLECOIN_ISSUER_CORROBORATION")

    def test_chip_without_any_kraken_record_also_held_not_drafted(self):
        result = build_from_markets(
            {"KRW-CHIP": market_row(korean_name="유에스디에이아이", english_name="USD.AI")},
        )
        self.assertEqual(result["new_records"], [])
        hold = {row["market"]: row for row in result["hold_list"]}
        self.assertEqual(hold["KRW-CHIP"]["reason"], "NO_INDEPENDENT_STABLECOIN_ISSUER_CORROBORATION")

    def test_xaut_classified_as_commodity_linked_and_category_added_to_schema(self):
        result = build_from_markets(
            {"KRW-XAUT": market_row(korean_name="테더골드", english_name="Tether Gold")},
            kraken=kraken_records(kraken_record("XAUT", "commodity_linked", reason="gold-linked token")),
        )
        self.assertEqual(len(result["new_records"]), 1)
        self.assertEqual(result["new_records"][0]["category"], "commodity_linked")
        self.assertIn("commodity_linked", result["candidate_taxonomy"]["excluded_categories"])

    def test_commodity_linked_not_duplicated_if_already_present(self):
        taxonomy = real_taxonomy(excluded_categories=["stablecoin", "commodity_linked"])
        result = build_from_markets(
            {"KRW-XAUT": market_row(english_name="Tether Gold")}, taxonomy=taxonomy,
            kraken=kraken_records(kraken_record("XAUT", "commodity_linked", reason="gold-linked token")),
        )
        self.assertEqual(result["candidate_taxonomy"]["excluded_categories"].count("commodity_linked"), 1)

    def test_upbit_only_asset_with_no_corroboration_fails_closed(self):
        result = build_from_markets({"KRW-OBSCURE": market_row(korean_name="오브스큐어", english_name="Obscure")})
        self.assertEqual(result["new_records"], [])
        hold = {row["market"]: row for row in result["hold_list"]}
        self.assertEqual(hold["KRW-OBSCURE"]["reason"], "NO_INDEPENDENT_CORROBORATION_UPBIT_ONLY")

    def test_identity_collision_excludes_market_from_any_draft_record(self):
        exceptions_doc = {
            "records": [
                {"source_asset_id": "BTC", "canonical_asset_id": "SHARED"},
                {"source_asset_id": "ETH", "canonical_asset_id": "SHARED"},
            ]
        }
        result = build_from_markets(
            {"KRW-BTC": market_row(), "KRW-ETH": market_row()},
            kraken=kraken_records(kraken_record("SHARED", "eligible_crypto")),
            exceptions_doc=exceptions_doc,
        )
        self.assertEqual(result["new_records"], [])
        hold = {row["market"]: row for row in result["hold_list"]}
        self.assertEqual(hold["KRW-BTC"]["reason"], "IDENTITY_COLLISION_UNRESOLVED")
        self.assertEqual(hold["KRW-ETH"]["reason"], "IDENTITY_COLLISION_UNRESOLVED")

    def test_duplicate_canonical_id_within_run_raises(self):
        # Two distinct (non-colliding-by-identity-review) markets whose
        # candidate ids happen to coincide would be a builder-level bug --
        # defended here directly since it can never happen via the real
        # default-rule + collision-detection path.
        core = base_core({"KRW-AAA": market_row(), "KRW-BBB": market_row()})
        proposals = HARNESS.build_identity_proposals(core, CAPTURE_CONTRACT, review_as_of="2026-08-29")
        proposals[1]["claim"]["candidateCanonicalAssetId"] = proposals[0]["claim"]["candidateCanonicalAssetId"]
        with self.assertRaises(TAX.TaxonomyCandidateError):
            TAX.build_candidate(
                core=core, capture_contract=CAPTURE_CONTRACT, real_taxonomy=real_taxonomy(),
                kraken_records_by_id=kraken_records(
                    kraken_record(proposals[0]["claim"]["candidateCanonicalAssetId"], "eligible_crypto")
                ),
                proposals=proposals, blocked_markets=set(), evaluation_as_of="2026-08-29",
            )

    def test_future_dated_kraken_record_held_not_drafted(self):
        result = build_from_markets(
            {"KRW-FUTURECOIN": market_row(korean_name="퓨처코인", english_name="FutureCoin")},
            kraken=kraken_records(kraken_record("FUTURECOIN", "eligible_crypto", effective_from="2099-01-01")),
        )
        self.assertEqual(result["new_records"], [])
        hold = {row["market"]: row for row in result["hold_list"]}
        self.assertEqual(hold["KRW-FUTURECOIN"]["reason"], "CONFLICTING_OR_STALE_KRAKEN_RECORD")

    def test_expired_kraken_record_held_not_drafted(self):
        result = build_from_markets(
            {"KRW-OLDCOIN": market_row(korean_name="올드코인", english_name="OldCoin")},
            kraken=kraken_records(
                kraken_record("OLDCOIN", "eligible_crypto", effective_from="2020-01-01", effective_to="2021-01-01")
            ),
        )
        self.assertEqual(result["new_records"], [])
        hold = {row["market"]: row for row in result["hold_list"]}
        self.assertEqual(hold["KRW-OLDCOIN"]["reason"], "CONFLICTING_OR_STALE_KRAKEN_RECORD")

    def test_kraken_category_outside_upbit_vocabulary_is_schema_gap_and_held(self):
        result = build_from_markets(
            {"KRW-FIATCOIN": market_row(korean_name="피아트코인", english_name="FiatCoin")},
            kraken=kraken_records(kraken_record("FIATCOIN", "fiat", reason="fiat currency")),
        )
        self.assertEqual(result["new_records"], [])
        gap_markets = {row["market"] for row in result["schema_gaps"]}
        self.assertIn("KRW-FIATCOIN", gap_markets)
        hold = {row["market"]: row for row in result["hold_list"]}
        self.assertEqual(hold["KRW-FIATCOIN"]["reason"], "TAXONOMY_SCHEMA_GAP")

    def test_already_recorded_canonical_id_never_duplicated(self):
        result = build_from_markets(
            {"KRW-USDT": market_row(korean_name="테더", english_name="Tether")},
            kraken=kraken_records(kraken_record("USDT", "stablecoin", reason="tether")),
        )
        self.assertEqual(result["new_records"], [])
        usdt_records = [r for r in result["candidate_taxonomy"]["records"] if r["canonical_asset_id"] == "USDT"]
        self.assertEqual(len(usdt_records), 1)
        # unchanged, byte-for-byte, from the real taxonomy's own record
        self.assertEqual(usdt_records[0], real_taxonomy()["records"][0])

    def test_empty_input_produces_empty_candidate_without_crashing(self):
        result = build_from_markets({})
        self.assertEqual(result["new_records"], [])
        self.assertEqual(result["hold_list"], [])
        self.assertEqual(result["schema_gaps"], [])
        self.assertEqual(result["candidate_taxonomy"]["records"], real_taxonomy()["records"])

    def test_determinism_same_input_twice_identical_output(self):
        markets = {"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")}
        kraken = kraken_records(kraken_record("SOL", "eligible_crypto"))
        first = build_from_markets(copy.deepcopy(markets), kraken=kraken)
        second = build_from_markets(copy.deepcopy(markets), kraken=kraken)
        self.assertEqual(TAX.canonical_json(first), TAX.canonical_json(second))


class WriteCandidateTaxonomyTests(unittest.TestCase):
    def test_write_preserves_top_level_key_order_and_approval_status(self):
        build_module = _load_build_module()
        result = build_from_markets(
            {"KRW-SOL": market_row(korean_name="솔라나", english_name="Solana")},
            kraken=kraken_records(kraken_record("SOL", "eligible_crypto")),
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "taxonomy.json"
            build_module.write_candidate_taxonomy(result["candidate_taxonomy"], target=target)
            written = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(list(written.keys()), list(result["candidate_taxonomy"].keys()))
            self.assertEqual(written["approval_status"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
            self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))


class NaturalPacketTests(unittest.TestCase):
    """Regression against the actual committed candidate packet for 2026-08-29."""

    def test_natural_packet_is_hash_bound_review_only_and_never_ratifies(self):
        packet = json.loads(NATURAL.read_text(encoding="utf-8"))
        expected_hash = HARNESS.payload_sha256({k: v for k, v in packet.items() if k != "payload_sha256"})
        self.assertEqual(packet["payload_sha256"], expected_hash)
        self.assertEqual(packet["review_status"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
        self.assertFalse(packet["candidate_boundary"]["approval_status_changed"])
        self.assertNotEqual(packet["candidate_boundary"]["approval_status"], "RATIFIED")
        for field, value in packet["authority"].items():
            if field != "review_only":
                self.assertIs(value, False, field)

    def test_natural_packet_matches_cio_ratified_classification_principles(self):
        packet = json.loads(NATURAL.read_text(encoding="utf-8"))
        new_by_id = {row["canonical_asset_id"]: row for row in packet["new_records"]}
        self.assertEqual(new_by_id["RE"]["category"], "unverified_identity")
        self.assertEqual(new_by_id["XAUT"]["category"], "commodity_linked")
        self.assertEqual(new_by_id["USDG"]["category"], "stablecoin")
        self.assertNotIn("CHIP", new_by_id)
        hold_markets = {row["market"]: row for row in packet["hold_list"]}
        self.assertIn("KRW-CHIP", hold_markets)
        self.assertIn("KRW-RE", hold_markets)
        self.assertEqual(packet["summary"]["new_records_by_category"].get("eligible_crypto"), 72)

    def test_real_committed_taxonomy_file_reflects_exact_approved_paper_slice(self):
        taxonomy = UNI.load_taxonomy()
        self.assertEqual(taxonomy["approval_status"], "RATIFIED")
        self.assertEqual(taxonomy["previous_approval_status"], "PENDING_GOVERNANCE_RESOLUTION")
        self.assertEqual(taxonomy["effective_from"], "2026-08-30")
        self.assertEqual(taxonomy["excluded_categories"], [])
        ids = {row["canonical_asset_id"] for row in taxonomy["records"]}
        self.assertEqual(ids, {"BTC", "ETH", "LINK", "SHIB", "SOL", "SUI", "WLD", "XRP"})
        for excluded in ("USDG", "RE", "XAUT", "USDT", "USDC", "USDE", "USDS", "RLUSD", "EURC"):
            self.assertNotIn(excluded, ids)
        self.assertTrue(all(row["category"] == "eligible_crypto" for row in taxonomy["records"]))
        self.assertTrue(all(value is False for value in taxonomy["authority"].values()))

    def test_build_script_is_idempotent_and_tamper_fails_closed(self):
        build_module = _load_build_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            first = build_module.populate("2026-08-29", data_root=output, write_taxonomy=False)
            second = build_module.populate("2026-08-29", data_root=output, write_taxonomy=False)
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            target = Path(first["path"])
            packet = json.loads(target.read_text(encoding="utf-8"))
            packet["summary"]["new_record_count"] = 999
            target.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaises(build_module.TaxonomyCandidateBuildError):
                build_module.populate("2026-08-29", data_root=output, write_taxonomy=False)

    def test_build_script_rejects_existing_packet_with_tampered_self_hash(self):
        build_module = _load_build_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            first = build_module.populate("2026-08-29", data_root=output, write_taxonomy=False)
            target = Path(first["path"])
            packet = json.loads(target.read_text(encoding="utf-8"))
            packet["payload_sha256"] = "f" * 64
            target.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaisesRegex(build_module.TaxonomyCandidateBuildError, "EXISTING_PACKET_HASH_INVALID"):
                build_module.populate("2026-08-29", data_root=output, write_taxonomy=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
