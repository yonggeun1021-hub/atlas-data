"""TKT-1 acceptance-criteria tests for discovery/market_rotation_discovery.py
(T1 market_rotation_discovery/1, CRYPTO builder).

Uses this repo's own dynamic-module-loading convention
(importlib.util.spec_from_file_location), matching
test/test_crypto_leadership.py / test/test_upbit_tradeable_universe.py,
rather than a package import.
"""
from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MRD = load_module("market_rotation_discovery", ROOT / "discovery" / "market_rotation_discovery.py")


def _market_row(market: str, asset_id: str | None) -> dict:
    return {
        "market": market,
        "candidate_canonical_asset_id": asset_id,
        "state": "PAPER_ELIGIBLE" if asset_id else "OBSERVATION_POOL",
        "reason": "PAPER_ELIGIBLE_ALL_GATES_PASSED" if asset_id else "IDENTITY_UNRATIFIED",
        "authority": {
            "investable_eligible": False, "order_authorized": False, "paper_eligible": False,
            "production_authorized": False, "stage_authorized": False, "trading_authorized": False,
        },
    }


# The 8 identity-ratified assets in config/upbit_asset_identity_registry.json today.
_RATIFIED = ("BTC", "ETH", "LINK", "SHIB", "SOL", "SUI", "WLD", "XRP")


def base_universe_packet(*, available_at: str = "2026-09-13T00:56:39Z", market_count: int = 282) -> dict:
    markets = [_market_row(f"KRW-{asset}", asset) for asset in _RATIFIED]
    markets += [_market_row(f"KRW-OTHER{i}", None) for i in range(market_count - len(_RATIFIED))]
    return {
        "packet": {
            "schema_version": 1,
            "snapshot_date": "2026-09-13",
            "available_at": available_at,
            "evaluation_as_of": available_at,
            "payload_sha256": "0" * 64,
            "summary": {"market_count": len(markets)},
            "markets": markets,
            "authority": {"trading_authorized": False},
        }
    }


def unknown_leadership_packet(as_of_date: str = "2026-09-12") -> dict:
    """Shaped exactly like the real (current) packets under
    data/observations/crypto_leadership/*/packet.json -- every date in
    this repo's history is UNKNOWN today."""
    return {
        "schema_version": 2,
        "contract_version": "crypto_leadership_contract/v2",
        "market": "CRYPTO",
        "as_of_date": as_of_date,
        "status": "UNKNOWN",
        "unknown_reason": "NO_WINDOW_OBSERVED",
        "windows": [
            {"window_id": "pilot_7d", "role": "PILOT", "status": "UNKNOWN", "unknown_reason": "SOURCE_POINT_UNKNOWN", "asset_relative_strength": [], "group_relative_strength": {"bucket": []}},
            {"window_id": "primary_30d", "role": "PRIMARY", "status": "UNKNOWN", "unknown_reason": "INSUFFICIENT_CONTIGUOUS_HISTORY", "asset_relative_strength": [], "group_relative_strength": {"bucket": []}},
        ],
    }


def observed_leadership_packet(as_of_date: str = "2026-09-13") -> dict:
    """A synthetic OBSERVED packet, shaped per crypto_leadership.py's own
    build_transform() output, used only to test the decimal-exact
    passthrough helpers -- this shape has never actually occurred in this
    repo's real history (every real packet to date is UNKNOWN)."""
    return {
        "schema_version": 2,
        "contract_version": "crypto_leadership_contract/v2",
        "market": "CRYPTO",
        "as_of_date": as_of_date,
        "status": "OBSERVED",
        "unknown_reason": None,
        "windows": [
            {
                "window_id": "primary_30d",
                "role": "PRIMARY",
                "status": "OBSERVED",
                "unknown_reason": None,
                "asset_relative_strength": [
                    {"canonical_asset_id": "BTC", "cumulative_gross_return": "1.041592837465", "relative_strength_vs_btc": "0.000000000000", "classification": "UNDEFINED"},
                    {"canonical_asset_id": "ETH", "cumulative_gross_return": "1.128374659201", "relative_strength_vs_btc": "0.083374651937", "classification": "UNDEFINED"},
                    {"canonical_asset_id": "SOL", "cumulative_gross_return": "0.981726354819", "relative_strength_vs_btc": "-0.057473827461", "classification": "UNDEFINED"},
                ],
                "group_relative_strength": {
                    "bucket": [
                        {"group_id": "BTC", "cumulative_gross_return": "1.041592837465", "relative_strength_vs_btc": "0.000000000000", "classification": "UNDEFINED"},
                        {"group_id": "ETH", "cumulative_gross_return": "1.128374659201", "relative_strength_vs_btc": "0.083374651937", "classification": "UNDEFINED"},
                        {"group_id": "ALT", "cumulative_gross_return": "1.005918273645", "relative_strength_vs_btc": "-0.034261830192", "classification": "UNDEFINED"},
                    ]
                },
            },
            {"window_id": "pilot_7d", "role": "PILOT", "status": "UNKNOWN", "unknown_reason": "SOURCE_POINT_UNKNOWN", "asset_relative_strength": [], "group_relative_strength": {"bucket": []}},
        ],
    }


def base_regime_status(as_of_date: str = "2026-09-13") -> dict:
    return {
        "schema_version": "crypto_regime_refresh_status/1",
        "status": "CURRENT_REFERENCE_INCOMPLETE",
        "generation_id": "test-generation-id",
        "current_reference": {"as_of_date": as_of_date, "leadership_code": "MIXED_WINDOW_LEADERSHIP"},
        "official_decision": {"runtime_regime": "UNKNOWN"},
    }


def ratified_rotation_policy_contract() -> dict:
    contract = copy.deepcopy(MRD.load_contract())
    contract["rotation_selection_policy"] = {
        "approval_status": "RATIFIED",
        "window_id": "primary_30d",
        "top_n": 3,
        "unknown_code_when_unratified": "NO_RATIFIED_ROTATION_SELECTION_POLICY",
    }
    return contract


class DeterministicBucketTests(unittest.TestCase):
    """Acceptance criterion 2: bucket assignment is deterministic."""

    def test_btc_and_eth_get_their_own_bucket_everything_else_is_alt(self):
        self.assertEqual(MRD.deterministic_bucket("BTC"), "BTC")
        self.assertEqual(MRD.deterministic_bucket("ETH"), "ETH")
        for asset_id in ("SOL", "XRP", "LINK", "SHIB", "SUI", "WLD", "DOGE"):
            self.assertEqual(MRD.deterministic_bucket(asset_id), "ALT")

    def test_repeated_calls_are_identical(self):
        for _ in range(5):
            self.assertEqual(MRD.deterministic_bucket("SOL"), "ALT")


class PopulationCountTests(unittest.TestCase):
    """Acceptance criterion 1: the 282-row population matches the snapshot."""

    def test_population_count_matches_the_universe_snapshot(self):
        contract = MRD.load_contract()
        universe = base_universe_packet(market_count=282)
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=universe, leadership_packet=None, regime_status=None,
            contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(result["counts"]["population_count"], 282)
        self.assertEqual(result["counts"]["population_count"], len(universe["packet"]["markets"]))

    def test_identity_resolved_count_matches_the_ratified_registry_size(self):
        contract = MRD.load_contract()
        universe = base_universe_packet(market_count=282)
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=universe, leadership_packet=None, regime_status=None,
            contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(result["counts"]["identity_resolved_count"], len(_RATIFIED))
        self.assertEqual(
            result["counts"]["identity_unresolved_count"],
            282 - len(_RATIFIED),
        )


class LeadershipUnknownTests(unittest.TestCase):
    """Acceptance criterion 3: leadership UNKNOWN -> selection and ranking
    empty, no substitute ranking -- even with a ratified rotation policy,
    and even with no leadership packet at all."""

    def test_unknown_leadership_packet_yields_empty_selection_and_ranking(self):
        contract = ratified_rotation_policy_contract()
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=base_universe_packet(), leadership_packet=unknown_leadership_packet(),
            regime_status=base_regime_status(), contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertTrue(result["rotation_selection_status"].startswith("UNKNOWN:"))
        self.assertEqual(result["rotation_selection"], [])
        self.assertEqual(result["ranked"], [])
        self.assertEqual(result["counts"]["rotation_selection_count"], 0)
        self.assertEqual(result["counts"]["ranked_count"], 0)

    def test_missing_leadership_packet_yields_empty_selection_and_ranking(self):
        contract = ratified_rotation_policy_contract()
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=base_universe_packet(), leadership_packet=None,
            regime_status=base_regime_status(), contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(result["rotation_selection_status"], "UNKNOWN:LEADERSHIP_PACKET_NOT_AVAILABLE")
        self.assertEqual(result["rotation_selection"], [])
        self.assertEqual(result["ranked"], [])

    def test_unratified_rotation_policy_yields_unknown_even_with_observed_leadership(self):
        """The real current state: no rotation selection policy is
        ratified at all, so the result is UNKNOWN regardless of whether
        the leadership data itself would otherwise be usable."""
        contract = MRD.load_contract()  # unmodified: rotation_selection_policy.approval_status == UNRATIFIED
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=base_universe_packet(), leadership_packet=observed_leadership_packet(),
            regime_status=base_regime_status(), contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(result["rotation_selection_status"], "UNKNOWN:NO_RATIFIED_ROTATION_SELECTION_POLICY")
        self.assertEqual(result["rotation_selection"], [])
        self.assertEqual(result["ranked"], [])

    def test_real_current_repo_leadership_packets_are_all_unknown(self):
        """Sanity check against the actual repo state (not a fixture):
        every data/observations/crypto_leadership/*/packet.json in this
        repo's history is UNKNOWN today, so this module's real-world
        behavior right now is always the empty-selection path."""
        leadership_root = ROOT / "data" / "observations" / "crypto_leadership"
        if not leadership_root.is_dir():
            self.skipTest("crypto_leadership observations not present in this checkout")
        dated = sorted(p for p in leadership_root.iterdir() if p.is_dir())
        self.assertTrue(dated, "expected at least one crypto_leadership snapshot date")
        for entry in dated:
            packet_path = entry / "packet.json"
            if not packet_path.is_file():
                continue
            import json

            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            with self.subTest(date=entry.name):
                self.assertEqual(packet.get("status"), "UNKNOWN", msg=entry.name)


class RelativeStrengthPassthroughTests(unittest.TestCase):
    """Acceptance criterion 4: 7d/30d values match the existing leadership
    formula to the decimal -- verified here as an exact passthrough
    (never a recomputation) of the published crypto_leadership packet."""

    def test_asset_relative_strength_is_passed_through_unchanged(self):
        packet = observed_leadership_packet()
        row = MRD.extract_asset_relative_strength(packet, "primary_30d", "ETH")
        self.assertIsNotNone(row)
        source_row = packet["windows"][0]["asset_relative_strength"][1]
        self.assertEqual(row["relative_strength_vs_btc"], source_row["relative_strength_vs_btc"])
        self.assertEqual(row["cumulative_gross_return"], source_row["cumulative_gross_return"])
        # Exact string identity, not just numeric equality -- proves no
        # reformatting/rounding happened.
        self.assertIs(type(row["relative_strength_vs_btc"]), str)
        self.assertEqual(row["relative_strength_vs_btc"], "0.083374651937")

    def test_group_relative_strength_is_passed_through_unchanged(self):
        packet = observed_leadership_packet()
        row = MRD.extract_group_relative_strength(packet, "primary_30d", "ALT")
        self.assertIsNotNone(row)
        self.assertEqual(row["relative_strength_vs_btc"], "-0.034261830192")

    def test_unobserved_window_returns_none_not_a_fabricated_value(self):
        packet = observed_leadership_packet()
        self.assertIsNone(MRD.extract_asset_relative_strength(packet, "pilot_7d", "ETH"))

    def test_build_ranked_row_carries_the_exact_relative_strength_string(self):
        market_row = _market_row("KRW-ETH", "ETH")
        row = MRD.build_ranked_row(market_row, observed_leadership_packet(), "primary_30d", rank_in_sector=1)
        self.assertEqual(row["membership_id"], "ETH")
        self.assertEqual(row["asset_id"], "ETH")
        self.assertEqual(row["symbol"], "ETH")
        self.assertEqual(row["features"]["relative_strength_vs_btc"], "0.083374651937")
        self.assertEqual(row["data_gaps"], [])

    def test_build_ranked_row_flags_a_data_gap_when_the_asset_row_is_absent(self):
        market_row = _market_row("KRW-SUI", "SUI")  # not present in the fixture's asset_relative_strength
        row = MRD.build_ranked_row(market_row, observed_leadership_packet(), "primary_30d", rank_in_sector=1)
        self.assertIn("LEADERSHIP_ASSET_ROW_NOT_OBSERVED", row["data_gaps"])
        self.assertEqual(row["features"], {})


class PitTests(unittest.TestCase):
    """Acceptance criterion 5: PIT -- a future-dated input fails the build."""

    def test_future_dated_universe_available_at_fails_closed(self):
        universe = base_universe_packet(available_at="2099-01-01T00:00:00Z")
        with self.assertRaises(MRD.MarketRotationDiscoveryError) as ctx:
            MRD.build_crypto_rotation_discovery(
                universe_packet=universe, leadership_packet=None, regime_status=None,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )
        self.assertIn("FUTURE_DATED", str(ctx.exception))

    def test_future_dated_leadership_as_of_date_fails_closed(self):
        leadership = unknown_leadership_packet(as_of_date="2099-01-01")
        with self.assertRaises(MRD.MarketRotationDiscoveryError):
            MRD.build_crypto_rotation_discovery(
                universe_packet=base_universe_packet(), leadership_packet=leadership, regime_status=None,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )

    def test_future_dated_regime_status_fails_closed(self):
        regime_status = base_regime_status(as_of_date="2099-01-01")
        with self.assertRaises(MRD.MarketRotationDiscoveryError):
            MRD.build_crypto_rotation_discovery(
                universe_packet=base_universe_packet(), leadership_packet=None, regime_status=regime_status,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )

    def test_missing_available_at_fails_closed_not_silently_accepted(self):
        universe = base_universe_packet()
        del universe["packet"]["available_at"]
        with self.assertRaises(MRD.MarketRotationDiscoveryError):
            MRD.build_crypto_rotation_discovery(
                universe_packet=universe, leadership_packet=None, regime_status=None,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )

    def test_evaluation_at_exactly_available_at_succeeds(self):
        universe = base_universe_packet(available_at="2026-09-13T00:56:39Z")
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=universe, leadership_packet=None, regime_status=None,
            contract=MRD.load_contract(), evaluation_as_of="2026-09-13T00:56:39Z",
        )
        self.assertIsInstance(result, dict)


class AuthorityTests(unittest.TestCase):
    """Acceptance criterion 6: authority is all false."""

    def test_every_authority_field_is_false(self):
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=base_universe_packet(), leadership_packet=unknown_leadership_packet(),
            regime_status=base_regime_status(), contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(set(result["authority"].keys()), set(MRD.AUTHORITY_FIELDS))
        for field, value in result["authority"].items():
            self.assertFalse(value, msg=field)

    def test_contract_authority_block_is_also_all_false(self):
        contract = MRD.load_contract()
        for field, value in contract["authority"].items():
            self.assertFalse(value, msg=field)


class DeterminismTests(unittest.TestCase):
    """Acceptance criterion 7: rerunning produces byte-identical output."""

    def test_same_inputs_twice_produce_canonically_identical_output(self):
        universe = base_universe_packet()
        leadership = unknown_leadership_packet()
        regime = base_regime_status()
        contract = MRD.load_contract()
        first = MRD.build_crypto_rotation_discovery(
            universe_packet=copy.deepcopy(universe), leadership_packet=copy.deepcopy(leadership),
            regime_status=copy.deepcopy(regime), contract=copy.deepcopy(contract),
            evaluation_as_of="2026-09-13T12:00:00Z",
        )
        second = MRD.build_crypto_rotation_discovery(
            universe_packet=copy.deepcopy(universe), leadership_packet=copy.deepcopy(leadership),
            regime_status=copy.deepcopy(regime), contract=copy.deepcopy(contract),
            evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(MRD.canonical_json(first), MRD.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, MRD.payload_sha256(second))

    def test_populate_cli_rerun_is_idempotent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_root = tmp_path / "market_rotation_discovery"
            universe_root = tmp_path / "universe"
            leadership_root = tmp_path / "leadership"
            regime_path = tmp_path / "regime.json"
            contract_path = tmp_path / "contract.json"

            (universe_root / "2026-09-13").mkdir(parents=True)
            (universe_root / "2026-09-13" / "packet.json").write_text(
                MRD.canonical_json(base_universe_packet()), encoding="utf-8"
            )
            (leadership_root / "2026-09-12").mkdir(parents=True)
            (leadership_root / "2026-09-12" / "packet.json").write_text(
                MRD.canonical_json(unknown_leadership_packet()), encoding="utf-8"
            )
            regime_path.write_text(MRD.canonical_json(base_regime_status()), encoding="utf-8")
            contract_path.write_text(MRD.canonical_json(MRD.load_contract()), encoding="utf-8")

            argv = [
                "--evaluation-as-of", "2026-09-13T12:00:00Z",
                "--universe-root", str(universe_root),
                "--leadership-root", str(leadership_root),
                "--regime-status-path", str(regime_path),
                "--contract-path", str(contract_path),
                "--output-root", str(output_root),
            ]
            self.assertEqual(MRD.run(argv), 0)
            first_payload = (output_root / "2026-09-13" / "packet.json").read_text(encoding="utf-8")
            self.assertEqual(MRD.run(argv), 0)  # idempotent rerun -- verified_existing, no error
            second_payload = (output_root / "2026-09-13" / "packet.json").read_text(encoding="utf-8")
            self.assertEqual(first_payload, second_payload)


if __name__ == "__main__":
    unittest.main()
