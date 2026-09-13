"""TKT-1 acceptance-criteria tests for discovery/market_rotation_discovery.py
(T1 market_rotation_discovery/1, CRYPTO builder).

Uses this repo's own dynamic-module-loading convention
(importlib.util.spec_from_file_location), matching
test/test_crypto_leadership.py / test/test_upbit_tradeable_universe.py,
rather than a package import.

Fixtures below are shaped to match the real ``crypto_leadership.py``
output exactly -- confirmed by reading that file directly:
``status`` is always the literal string ``"OBSERVED_UNCLASSIFIED"``,
never ``"OBSERVED"`` (an earlier draft's mistake, independent-review
finding B2, fixed here); a bucket row inside ``group_relative_strength.bucket``
carries its own independent ``status``/``unknown_reason``, distinct from
the window's own status; asset rows in ``asset_relative_strength`` carry
no per-row status field at all (only ``classification``, always
``"UNDEFINED"``) -- only the window's own status gates whether they are
trustworthy.
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

# Real crypto_leadership packet dates confirmed UNKNOWN by direct inspection
# of this repo's history at the time this test suite was written
# (2026-09-13). A *fixed* list, not "whatever the latest snapshot happens
# to be" -- independent-review finding B1: an earlier draft asserted this
# property against every packet the live directory currently contains,
# which would break itself the day a pilot_7d window is first observed
# (the regime status file's own natural_history_progress already project
# that could happen as early as 2026-09-15). Fixed dates make this a
# historical regression check, not a ticking time bomb.
_CONFIRMED_UNKNOWN_LEADERSHIP_DATES = (
    "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06",
    "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12",
)


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
    data/observations/crypto_leadership/*/packet.json."""
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


def observed_leadership_packet(as_of_date: str = "2026-09-13", *, alt_bucket_status: str = "OBSERVED_UNCLASSIFIED") -> dict:
    """A synthetic packet shaped *exactly* like crypto_leadership.py's own
    build_transform() output (confirmed by reading that file directly):
    status "OBSERVED_UNCLASSIFIED" (never "OBSERVED"), asset rows with no
    per-row status, bucket rows each carrying their own status. This shape
    has never actually occurred in this repo's real history (every real
    packet to date is UNKNOWN) -- used only to test the passthrough
    helpers against the real schema.
    """
    alt_row = (
        {"group_id": "ALT", "status": "OBSERVED_UNCLASSIFIED", "unknown_reason": None,
         "cumulative_gross_return": "1.005918273645", "relative_strength_vs_btc": "-0.034261830192", "classification": "UNDEFINED"}
        if alt_bucket_status == "OBSERVED_UNCLASSIFIED" else
        {"group_id": "ALT", "status": "UNKNOWN", "unknown_reason": "BUCKET_EMPTY_ON_REQUIRED_DATE",
         "cumulative_gross_return": None, "relative_strength_vs_btc": None, "classification": "UNDEFINED"}
    )
    return {
        "schema_version": 2,
        "contract_version": "crypto_leadership_contract/v2",
        "market": "CRYPTO",
        "as_of_date": as_of_date,
        "status": "OBSERVED_UNCLASSIFIED",
        "unknown_reason": None,
        "windows": [
            {
                "window_id": "primary_30d",
                "role": "PRIMARY",
                "status": "OBSERVED_UNCLASSIFIED",
                "unknown_reason": None,
                "asset_relative_strength": [
                    {"canonical_asset_id": "BTC", "cumulative_gross_return": "1.041592837465", "relative_strength_vs_btc": "0.000000000000", "classification": "UNDEFINED"},
                    {"canonical_asset_id": "ETH", "cumulative_gross_return": "1.128374659201", "relative_strength_vs_btc": "0.083374651937", "classification": "UNDEFINED"},
                    {"canonical_asset_id": "SOL", "cumulative_gross_return": "0.981726354819", "relative_strength_vs_btc": "-0.057473827461", "classification": "UNDEFINED"},
                ],
                "group_relative_strength": {
                    "bucket": [
                        {"group_id": "BTC", "status": "OBSERVED_UNCLASSIFIED", "unknown_reason": None, "cumulative_gross_return": "1.041592837465", "relative_strength_vs_btc": "0.000000000000", "classification": "UNDEFINED"},
                        {"group_id": "ETH", "status": "OBSERVED_UNCLASSIFIED", "unknown_reason": None, "cumulative_gross_return": "1.128374659201", "relative_strength_vs_btc": "0.083374651937", "classification": "UNDEFINED"},
                        alt_row,
                    ]
                },
            },
            {"window_id": "pilot_7d", "role": "PILOT", "status": "UNKNOWN", "unknown_reason": "SOURCE_POINT_UNKNOWN", "asset_relative_strength": [], "group_relative_strength": {"bucket": []}},
        ],
    }


def base_regime_status(as_of_date: str = "2026-09-13", *, generated_at: str = "2026-09-13T02:40:55Z") -> dict:
    return {
        "schema_version": "crypto_regime_refresh_status/1",
        "status": "CURRENT_REFERENCE_INCOMPLETE",
        "generation_id": "test-generation-id",
        "generated_at": generated_at,
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
    empty, no substitute ranking -- and (independent-review fix B3) the
    rotation_selection_gate never returns anything but UNKNOWN today,
    regardless of leadership status, because the actual selection rule is
    not implemented yet."""

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
        self.assertEqual(result["rotation_selection_status"], "UNKNOWN:ROTATION_SELECTION_RULE_NOT_IMPLEMENTED")
        self.assertEqual(result["rotation_selection"], [])
        self.assertEqual(result["ranked"], [])

    def test_unratified_rotation_policy_yields_unknown(self):
        """The real current state: no rotation selection policy is
        ratified at all."""
        contract = MRD.load_contract()  # unmodified: rotation_selection_policy.approval_status == UNRATIFIED
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=base_universe_packet(), leadership_packet=observed_leadership_packet(),
            regime_status=base_regime_status(), contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(result["rotation_selection_status"], "UNKNOWN:NO_RATIFIED_ROTATION_SELECTION_POLICY")
        self.assertEqual(result["rotation_selection"], [])
        self.assertEqual(result["ranked"], [])

    def test_ratified_policy_with_fully_observed_leadership_is_still_unknown(self):
        """Independent-review fix B3: even with a ratified policy AND a
        fully-observed leadership packet, this module must never emit a
        substitute ranking -- the actual selection algorithm is not
        implemented yet, so the result stays UNKNOWN with a distinct code
        naming exactly that."""
        contract = ratified_rotation_policy_contract()
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=base_universe_packet(), leadership_packet=observed_leadership_packet(),
            regime_status=base_regime_status(), contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(result["rotation_selection_status"], "UNKNOWN:ROTATION_SELECTION_RULE_NOT_IMPLEMENTED")
        self.assertEqual(result["rotation_selection"], [])
        self.assertEqual(result["ranked"], [])
        # Not merely empty by accident -- rotation_selection_gate itself
        # never returns "OBSERVED" from any input combination.
        self.assertEqual(MRD.rotation_selection_gate(contract), ("UNKNOWN", "ROTATION_SELECTION_RULE_NOT_IMPLEMENTED"))

    def test_real_confirmed_historical_leadership_packets_are_unknown(self):
        """Regression check against a *fixed* set of dates already
        confirmed UNKNOWN at the time this suite was written (independent-
        review fix B1) -- not "whatever the latest snapshot in the repo
        happens to be", which would break itself once a pilot_7d window is
        first observed (the regime status file's own natural_history_progress
        projects that as early as 2026-09-15)."""
        leadership_root = ROOT / "data" / "observations" / "crypto_leadership"
        if not leadership_root.is_dir():
            self.skipTest("crypto_leadership observations not present in this checkout")
        import json

        checked = 0
        for date in _CONFIRMED_UNKNOWN_LEADERSHIP_DATES:
            packet_path = leadership_root / date / "packet.json"
            if not packet_path.is_file():
                continue  # this checkout may not have every historical date; skip, don't fail
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            with self.subTest(date=date):
                self.assertEqual(packet.get("status"), "UNKNOWN", msg=date)
            checked += 1
        if checked == 0:
            self.skipTest("none of the confirmed-UNKNOWN dates are present in this checkout")


class RelativeStrengthPassthroughTests(unittest.TestCase):
    """Acceptance criterion 4: 7d/30d values match the existing leadership
    formula to the decimal -- verified as an exact passthrough (never a
    recomputation) of the published crypto_leadership packet, against
    fixtures shaped exactly like the real schema (status
    "OBSERVED_UNCLASSIFIED", per-bucket-row status)."""

    def test_status_constant_matches_the_real_value_not_the_wrong_literal(self):
        self.assertEqual(MRD.LEADERSHIP_OBSERVED_STATUS, "OBSERVED_UNCLASSIFIED")

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

    def test_group_relative_strength_respects_the_bucket_rows_own_unknown_status(self):
        """A bucket can be UNKNOWN (BUCKET_EMPTY_ON_REQUIRED_DATE) even
        while its window is OBSERVED_UNCLASSIFIED overall -- the row's own
        status must gate the passthrough, not just the window's."""
        packet = observed_leadership_packet(alt_bucket_status="UNKNOWN")
        self.assertIsNone(MRD.extract_group_relative_strength(packet, "primary_30d", "ALT"))
        # BTC/ETH buckets in the same window are unaffected.
        self.assertIsNotNone(MRD.extract_group_relative_strength(packet, "primary_30d", "BTC"))

    def test_unobserved_window_returns_none_not_a_fabricated_value(self):
        packet = observed_leadership_packet()
        self.assertIsNone(MRD.extract_asset_relative_strength(packet, "pilot_7d", "ETH"))

    def test_wrong_status_literal_never_matches_real_data(self):
        """Direct regression test for independent-review finding B2: a
        window whose status is the real 'OBSERVED_UNCLASSIFIED' must be
        treated as observed; a window incorrectly checked against the
        literal 'OBSERVED' (this module's earlier bug) would never match
        real data at all."""
        packet = observed_leadership_packet()
        window = packet["windows"][0]
        self.assertEqual(window["status"], "OBSERVED_UNCLASSIFIED")
        self.assertNotEqual(window["status"], "OBSERVED")
        self.assertIsNotNone(MRD.extract_asset_relative_strength(packet, "primary_30d", "BTC"))

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

    def test_build_ranked_row_is_never_called_by_the_main_pipeline_today(self):
        """Independent-review fix B3: build_ranked_row is a tested,
        standalone building block for a later ticket -- confirm the main
        pipeline genuinely never reaches it by checking ranked stays empty
        even with everything else favorable."""
        contract = ratified_rotation_policy_contract()
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=base_universe_packet(), leadership_packet=observed_leadership_packet(),
            regime_status=base_regime_status(), contract=contract, evaluation_as_of="2026-09-13T12:00:00Z",
        )
        self.assertEqual(result["ranked"], [])


class PitTests(unittest.TestCase):
    """Acceptance criterion 5: PIT -- a future-dated or missing-timestamp
    input fails the build (independent-review fix: missing as_of_date/
    generated_at now fails closed instead of being silently skipped)."""

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

    def test_future_dated_regime_generated_at_fails_closed(self):
        regime_status = base_regime_status(generated_at="2099-01-01T00:00:00Z")
        with self.assertRaises(MRD.MarketRotationDiscoveryError) as ctx:
            MRD.build_crypto_rotation_discovery(
                universe_packet=base_universe_packet(), leadership_packet=None, regime_status=regime_status,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )
        self.assertIn("REGIME_GENERATED_AT_FUTURE_DATED", str(ctx.exception))

    def test_missing_available_at_fails_closed_not_silently_accepted(self):
        universe = base_universe_packet()
        del universe["packet"]["available_at"]
        with self.assertRaises(MRD.MarketRotationDiscoveryError):
            MRD.build_crypto_rotation_discovery(
                universe_packet=universe, leadership_packet=None, regime_status=None,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )

    def test_missing_leadership_as_of_date_fails_closed(self):
        leadership = unknown_leadership_packet()
        del leadership["as_of_date"]
        with self.assertRaises(MRD.MarketRotationDiscoveryError) as ctx:
            MRD.build_crypto_rotation_discovery(
                universe_packet=base_universe_packet(), leadership_packet=leadership, regime_status=None,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )
        self.assertIn("LEADERSHIP_AS_OF_DATE_MISSING", str(ctx.exception))

    def test_missing_regime_generated_at_fails_closed(self):
        regime_status = base_regime_status()
        del regime_status["generated_at"]
        with self.assertRaises(MRD.MarketRotationDiscoveryError) as ctx:
            MRD.build_crypto_rotation_discovery(
                universe_packet=base_universe_packet(), leadership_packet=None, regime_status=regime_status,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )
        self.assertIn("REGIME_GENERATED_AT_MISSING", str(ctx.exception))

    def test_missing_regime_as_of_date_fails_closed(self):
        regime_status = base_regime_status()
        del regime_status["current_reference"]["as_of_date"]
        with self.assertRaises(MRD.MarketRotationDiscoveryError) as ctx:
            MRD.build_crypto_rotation_discovery(
                universe_packet=base_universe_packet(), leadership_packet=None, regime_status=regime_status,
                contract=MRD.load_contract(), evaluation_as_of="2026-09-13T12:00:00Z",
            )
        self.assertIn("REGIME_AS_OF_DATE_MISSING", str(ctx.exception))

    def test_evaluation_at_exactly_available_at_succeeds(self):
        universe = base_universe_packet(available_at="2026-09-13T00:56:39Z")
        result = MRD.build_crypto_rotation_discovery(
            universe_packet=universe, leadership_packet=None, regime_status=None,
            contract=MRD.load_contract(), evaluation_as_of="2026-09-13T00:56:39Z",
        )
        self.assertIsInstance(result, dict)

    def test_latest_dated_packet_never_looks_ahead_of_a_historical_evaluation_date(self):
        """Independent-review fix: a backfill/replay evaluation_as_of must
        only ever see packets dated at or before it, never a packet
        published later."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for date in ("2026-09-10", "2026-09-11", "2026-09-12"):
                (root / date).mkdir()
                (root / date / "packet.json").write_text(json.dumps({"date": date}), encoding="utf-8")

            latest_unbounded = MRD._latest_dated_packet(root)
            self.assertEqual(latest_unbounded.parent.name, "2026-09-12")

            latest_as_of_11 = MRD._latest_dated_packet(root, not_after_date="2026-09-11")
            self.assertEqual(latest_as_of_11.parent.name, "2026-09-11")

            latest_as_of_09 = MRD._latest_dated_packet(root, not_after_date="2026-09-09")
            self.assertIsNone(latest_as_of_09)


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
    """Acceptance criterion 7: rerunning produces byte-identical output,
    including on the same UTC day without an explicit --evaluation-as-of
    (independent-review fix: the CLI now defaults to a fixed end-of-day
    timestamp so two same-day runs share one evaluation_as_of, rather than
    each capturing the current instant and permanently diverging)."""

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

    def test_populate_cli_same_day_rerun_without_explicit_evaluation_as_of_is_idempotent(self):
        """Independent-review fix: two runs on the same UTC day with no
        --evaluation-as-of flag (the real workflow's own invocation shape)
        must not fail -- both must resolve to the same fixed end-of-day
        timestamp, not each capture a different current instant."""
        import datetime as dt
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_root = tmp_path / "market_rotation_discovery"
            universe_root = tmp_path / "universe"
            leadership_root = tmp_path / "leadership"
            regime_path = tmp_path / "regime.json"
            contract_path = tmp_path / "contract.json"

            today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
            (universe_root / today).mkdir(parents=True)
            (universe_root / today / "packet.json").write_text(
                MRD.canonical_json(base_universe_packet(available_at=f"{today}T00:00:01Z")), encoding="utf-8"
            )
            regime_path.write_text(
                MRD.canonical_json(base_regime_status(as_of_date=today, generated_at=f"{today}T00:00:01Z")),
                encoding="utf-8",
            )
            contract_path.write_text(MRD.canonical_json(MRD.load_contract()), encoding="utf-8")

            argv = [
                "--universe-root", str(universe_root),
                "--leadership-root", str(leadership_root),
                "--regime-status-path", str(regime_path),
                "--contract-path", str(contract_path),
                "--output-root", str(output_root),
            ]
            self.assertEqual(MRD.run(argv), 0)  # first run: populated
            self.assertEqual(MRD.run(argv), 0)  # second run, same day, no explicit timestamp: verified_existing


if __name__ == "__main__":
    unittest.main()
