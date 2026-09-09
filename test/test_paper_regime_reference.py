#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "paper_regime_reference.py"
SPEC = importlib.util.spec_from_file_location("paper_regime_reference_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


POLICY_PATH = ROOT / "config" / "paper_regime_reference_policy_v1.json"
KR_THRESHOLD_SLOTS = (
    ("BREADTH", "positive_min"),
    ("BREADTH", "negative_max"),
    ("RISK_VOL", "positive_max"),
    ("RISK_VOL", "neutral_max"),
    ("RISK_VOL", "negative_max"),
    ("RISK_VOL", "stress_above"),
    ("LIQUIDITY", "positive_min"),
    ("LIQUIDITY", "negative_max"),
    ("LEADERSHIP", "positive_min"),
    ("LEADERSHIP", "negative_max"),
)


def kr_policy_fixture() -> dict:
    """A fresh mutable copy of the shipped policy bytes.

    Variants built from this are test inputs only; none is a proposed or
    adopted production threshold.
    """
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def kr_packet_fixture(
    *,
    kospi: str = "1.0",
    kosdaq: str = "1.0",
    advance_fraction: str = "0.50",
    move: str = "2.0",
    trading_value_change: str = "0.0",
    sectors: tuple[str, ...] = ("1.0", "-1.0"),
) -> dict:
    """A synthetic 5/5 KR packet with explicit per-axis measurements."""
    return {
        "as_of_date": "2026-09-03",
        "status": "OBSERVED_UNCLASSIFIED",
        "coverage": {"ratio": "5/5"},
        "axes": {
            "TREND": {
                "status": "OBSERVED",
                "measurement": {"benchmarks": {
                    "KOSPI": {"one_session_return_pct": kospi},
                    "KOSDAQ": {"one_session_return_pct": kosdaq},
                }},
            },
            "BREADTH": {
                "status": "OBSERVED",
                "measurement": {"combined": {"advance_fraction": advance_fraction}},
            },
            "RISK_VOL": {
                "status": "OBSERVED",
                "measurement": {"combined_mean_absolute_stock_move_pct": move},
            },
            "LIQUIDITY": {
                "status": "OBSERVED",
                "measurement": {"combined": {"trading_value_change_pct": trading_value_change}},
            },
            "LEADERSHIP": {
                "status": "OBSERVED",
                "measurement": {"observations": [
                    {"sector_return_pct": value} for value in sectors
                ]},
            },
        },
    }


def frozen_render_sources(root: Path) -> None:
    """Small deterministic synthetic inputs; no mutable latest market data."""
    (root / "config").mkdir()
    (root / "data").mkdir()
    (root / "config/paper_regime_reference_policy_v1.json").write_bytes(POLICY_PATH.read_bytes())
    us = {
        "observed_at_utc": "2026-09-03T10:00:00Z",
        "fred": {"value": "20"},
        "fred_liquidity": {"series": [
            {"series_id": "WRESBAL", "change": "1"},
            {"series_id": "TOTBKCR", "change": "-1"},
        ]},
        "us_market_reference": {
            "status": "READY", "as_of_session_date": "2026-09-03",
            "trend_etfs": [{"returns": {"20_session_pct": "1"}} for _ in range(3)],
            "proxy_axes": {
                "BREADTH": {"measurement": {"advance_fraction": "0.50"}},
                "LEADERSHIP": {"measurement": {"ordered_groups": [
                    {"return_pct": "1"} for _ in range(12)
                ]}},
            },
        },
    }
    kr = kr_packet_fixture(kospi="1.637363", kosdaq="2.947318")
    kr["generated_at"] = "2026-09-03T10:00:00Z"
    crypto = {
        "schema_version": "crypto_regime_refresh_status/1",
        "generated_at": "2026-09-03T10:00:00Z",
        "authority": {"read_only_reference": True},
        "current_reference": {"as_of_date": "2026-09-03"},
        "official_decision": {"coverage": {
            "required_count": 5, "defined_count": 0, "ratio": "0/5",
            "defined_axes": [], "missing_axes": list(MODULE.AXES),
        }},
    }
    crypto["payload_sha256"] = MODULE.payload_sha256(crypto)
    for name, value in (("free_market_data", us), ("korea_market_signals", kr),
                        ("crypto_regime_refresh_status", crypto)):
        (root / f"data/latest_{name}.json").write_text(
            MODULE.canonical_json(value), encoding="utf-8"
        )


def kr_direction(axis_name: str, policy: dict, **measurement) -> str:
    rows = {row["axis"]: row for row in MODULE.build_kr(kr_packet_fixture(**measurement), policy)["axes"]}
    return rows[axis_name]["direction"]


class PaperRegimeReferenceTest(unittest.TestCase):
    def test_current_free_inputs_make_plain_paper_reference(self):
        packet = MODULE.build_reference()
        markets = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(packet["status"], "PARTIAL_REFERENCE_AVAILABLE")
        self.assertEqual(markets["US"]["coverage"]["ratio"], "5/5")
        self.assertIn(markets["US"]["paper_reference"]["candidate_regime"], {"RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"})
        self.assertEqual(
            markets["US"]["paper_reference"]["score"],
            sum(row["score"] for row in markets["US"]["axes"]),
        )
        self.assertEqual(markets["KR"]["coverage"]["ratio"], "5/5")
        self.assertIn(markets["KR"]["paper_reference"]["candidate_regime"], {"RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"})
        self.assertEqual(
            markets["KR"]["paper_reference"]["score"],
            sum(row["score"] for row in markets["KR"]["axes"]),
        )
        self.assertEqual(markets["CRYPTO"]["paper_reference"]["candidate_regime"], "UNKNOWN")
        source = json.loads((ROOT / "data/latest_crypto_regime_refresh_status.json").read_text())
        expected_coverage = source.get("current_reference", {}).get(
            "coverage", source["official_decision"]["coverage"]
        )
        self.assertEqual(markets["CRYPTO"]["coverage"], expected_coverage)
        self.assertEqual(
            markets["CRYPTO"]["official_validation"]["coverage"],
            source["official_decision"]["coverage"],
        )
        expected_status = (
            "WAIT_MARKET_NORMALIZATION_POLICY"
            if markets["CRYPTO"]["coverage"]["ratio"] == "5/5"
            else source["official_decision"].get("classification_status")
        )
        if expected_status not in {"WAIT_MARKET_NORMALIZATION_POLICY", "WAIT_OFFICIAL_DECISION_REFRESH"}:
            expected_status = "WAIT_OFFICIAL_INPUT_COVERAGE"
        self.assertEqual(markets["CRYPTO"]["classification_status"], expected_status)
        if expected_coverage["ratio"] == "5/5":
            self.assertEqual(markets["CRYPTO"]["leadership_code"], "MIXED_WINDOW_LEADERSHIP")
        self.assertEqual(packet["schema_version"], "paper_regime_reference/v2")
        self.assertTrue(all(row["runtime_regime"] == "UNKNOWN" for row in markets.values()))

    def test_axis_values_and_korean_explanations_are_visible(self):
        packet = MODULE.build_reference()
        markets = {row["market"]: row for row in packet["markets"]}
        for market in ("US", "KR"):
            self.assertEqual([row["axis"] for row in markets[market]["axes"]], MODULE.AXES)
            for row in markets[market]["axes"]:
                self.assertIn(row["direction"], {"POSITIVE", "NEUTRAL", "NEGATIVE", "STRESS"})
                self.assertTrue(row["summary_ko"])

    def test_authority_boundary_stays_paper_only(self):
        authority = MODULE.build_reference()["authority"]
        self.assertTrue(authority["paper_reference_display_authorized"])
        self.assertTrue(authority["paper_symbol_context_authorized"])
        for key in ("runtime_regime_authorized", "final_regime_authorized", "stage_authorized", "buy_authorized", "action_authorized", "order_authorized", "capital_authorized", "production_authorized", "trading_authorized"):
            self.assertFalse(authority[key], key)

    def test_official_refresh_wait_is_preserved_in_combined_reference(self):
        source = json.loads((ROOT / "data/latest_crypto_regime_refresh_status.json").read_text())
        source["official_decision"]["classification_status"] = "WAIT_OFFICIAL_DECISION_REFRESH"
        source["official_decision"]["captured_at_utc"] = None
        source["official_decision"]["coverage"] = {
            "defined_count": 0,
            "required_count": 5,
            "ratio": "0/5",
            "defined_axes": [],
            "missing_axes": list(MODULE.AXES),
        }
        source["official_decision"]["unavailable_reason"] = "OFFICIAL_DECISION_REFRESH_REQUIRED"
        unsigned = copy.deepcopy(source)
        unsigned.pop("payload_sha256")
        source["payload_sha256"] = MODULE.payload_sha256(unsigned)

        crypto = MODULE.build_crypto(source)
        self.assertEqual(crypto["classification_status"], "WAIT_MARKET_NORMALIZATION_POLICY")
        self.assertEqual(crypto["paper_reference"]["candidate_regime"], "UNKNOWN")
        self.assertEqual(crypto["coverage"]["ratio"], "5/5")
        self.assertEqual(crypto["official_validation"]["coverage"]["ratio"], "0/5")

    def test_resigned_tamper_and_source_tamper_fail_closed(self):
        packet = MODULE.build_reference()
        self.assertEqual(MODULE.validate_reference(packet), packet)
        tampered = copy.deepcopy(packet)
        tampered["markets"][0]["paper_reference"]["candidate_regime"] = "RISK_ON"
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "REFERENCE_REDERIVATION_MISMATCH"):
            MODULE.validate_reference(tampered)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            shutil.copy2(ROOT / "data/latest_free_market_data.json", root / "data/latest_free_market_data.json")
            shutil.copy2(ROOT / "data/latest_korea_market_signals.json", root / "data/latest_korea_market_signals.json")
            shutil.copy2(ROOT / "data/latest_crypto_regime_refresh_status.json", root / "data/latest_crypto_regime_refresh_status.json")
            value = json.loads((root / "data/latest_free_market_data.json").read_text())
            value["fred"]["value"] = "not-a-number"
            (root / "data/latest_free_market_data.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "US_VIX_INVALID"):
                MODULE.build_reference(root)

    def test_write_is_append_only_and_pointer_is_identical(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            shutil.copy2(ROOT / "data/latest_free_market_data.json", root / "data/latest_free_market_data.json")
            shutil.copy2(ROOT / "data/latest_korea_market_signals.json", root / "data/latest_korea_market_signals.json")
            shutil.copy2(ROOT / "data/latest_crypto_regime_refresh_status.json", root / "data/latest_crypto_regime_refresh_status.json")
            packet = MODULE.build_reference()
            evidence, latest = MODULE.write_packet(packet, root)
            self.assertEqual(evidence.read_bytes(), latest.read_bytes())
            self.assertEqual(MODULE.validate_reference(json.loads(latest.read_text())), packet)
            MODULE.write_packet(packet, root)

    def test_kr_trend_summary_matches_positive_negative_mixed_and_zero(self):
        cases = (
            # Reported 2026-09-04 measurements; explicit local fixture only.
            ("1.637363", "2.947318", "POSITIVE", "두 지수가 모두 상승했습니다."),
            ("-1.637363", "-2.947318", "NEGATIVE", "두 지수가 모두 하락했습니다."),
            ("1.0", "-1.0", "NEUTRAL", "혼조 또는 보합을 보였습니다."),
            ("-1.0", "1.0", "NEUTRAL", "혼조 또는 보합을 보였습니다."),
            ("0", "1.0", "NEUTRAL", "혼조 또는 보합을 보였습니다."),
            ("-1.0", "0", "NEUTRAL", "혼조 또는 보합을 보였습니다."),
            ("0", "0", "NEUTRAL", "혼조 또는 보합을 보였습니다."),
        )
        for kospi, kosdaq, direction, explanation in cases:
            with self.subTest(kospi=kospi, kosdaq=kosdaq):
                packet = MODULE.build_kr(
                    kr_packet_fixture(kospi=kospi, kosdaq=kosdaq), kr_policy_fixture(),
                    render_version=MODULE.KR_TREND_RENDER_VERSION,
                )
                trend = next(row for row in packet["axes"] if row["axis"] == "TREND")
                self.assertEqual(trend["direction"], direction)
                self.assertEqual(
                    trend["summary_ko"],
                    f"코스피 {MODULE.Decimal(kospi):+.2f}%, 코스닥 {MODULE.Decimal(kosdaq):+.2f}%로 {explanation}",
                )

    def test_render_legacy_v2_and_frozen_leaf_hashes_are_preserved(self):
        # Frozen using the exact PR618 a87ab66d renderer on these synthetic inputs.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); frozen_render_sources(root)
            legacy = MODULE.build_reference(root, render_version=None)
            self.assertNotIn("render_version", legacy)
            self.assertEqual(legacy["generation_id"], "bf64db06627fdb5ed075410545a6c648bd08bbcd541a4ce6e9836fd9f334896f")
            self.assertEqual(legacy["payload_sha256"], "64e4574db1d72f6a6a70d6c91a388787a5c3b9039b43807a406813f24d0a1a1a")
            self.assertEqual(MODULE.validate_reference(legacy, root), legacy)
            leaf = MODULE.build_kr(
                json.loads((root / "data/latest_korea_market_signals.json").read_text()),
                kr_policy_fixture(),
            )
            self.assertEqual(MODULE.payload_sha256(leaf), "5089784fce9c91ed53d8a90796a54dd270c6f5942f68db98152cafaedd88c5e2")
            self.assertIn("방향이 엇갈렸습니다.", leaf["axes"][0]["summary_ko"])

    def test_render_new_namespace_coexists_without_overwriting_retained_v2(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); frozen_render_sources(root)
            legacy = MODULE.build_reference(root, render_version=None)
            old_path, _ = MODULE.write_packet(legacy, root)
            retained = old_path.read_bytes()
            current = MODULE.build_reference(root)
            self.assertEqual(current["render_version"], MODULE.CURRENT_RENDER_VERSION)
            self.assertNotEqual(current["generation_id"], legacy["generation_id"])
            self.assertNotEqual(current["payload_sha256"], legacy["payload_sha256"])
            new_path, latest = MODULE.write_packet(current, root)
            self.assertNotEqual(old_path, new_path)
            self.assertEqual(old_path.read_bytes(), retained)
            self.assertEqual(new_path.read_bytes(), latest.read_bytes())
            MODULE.write_packet(current, root)
            for packet in (legacy, current):
                self.assertEqual(MODULE.validate_reference(packet, root), packet)
            before, after = copy.deepcopy(legacy["markets"]), copy.deepcopy(current["markets"])
            before[1]["axes"][0].pop("summary_ko")
            after[1]["axes"][0].pop("summary_ko")
            self.assertEqual(before, after)
            self.assertIn("두 지수가 모두 상승했습니다.", current["markets"][1]["axes"][0]["summary_ko"])

    def test_render_dispatch_rejects_unknown_null_and_resigned_downgrade(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); frozen_render_sources(root)
            current = MODULE.build_reference(root)
            for version in (None, "unknown/v9", True):
                packet = copy.deepcopy(current); packet["render_version"] = version
                packet.pop("payload_sha256"); packet["payload_sha256"] = MODULE.payload_sha256(packet)
                with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "REFERENCE_RENDER_VERSION_INVALID"):
                    MODULE.validate_reference(packet, root)
            downgraded = copy.deepcopy(current); downgraded.pop("render_version")
            downgraded["generation_id"] = MODULE.build_reference(root, render_version=None)["generation_id"]
            downgraded.pop("payload_sha256"); downgraded["payload_sha256"] = MODULE.payload_sha256(downgraded)
            with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "REFERENCE_REDERIVATION_MISMATCH"):
                MODULE.validate_reference(downgraded, root)
            with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "REFERENCE_RENDER_VERSION_INVALID"):
                MODULE.build_reference(root, render_version="unknown/v9")

    def test_kr_policy_baseline_boundaries(self):
        policy = kr_policy_fixture()
        # TREND has no threshold; its three declared sign semantics.
        self.assertEqual(kr_direction("TREND", policy, kospi="0.1", kosdaq="0.1"), "POSITIVE")
        self.assertEqual(kr_direction("TREND", policy, kospi="-0.1", kosdaq="-0.1"), "NEGATIVE")
        self.assertEqual(kr_direction("TREND", policy, kospi="0.1", kosdaq="-0.1"), "NEUTRAL")
        self.assertEqual(kr_direction("TREND", policy, kospi="0", kosdaq="0.1"), "NEUTRAL")
        # BREADTH positive_min = 0.55, negative_max = 0.45 (both inclusive).
        self.assertEqual(kr_direction("BREADTH", policy, advance_fraction="0.55"), "POSITIVE")
        self.assertEqual(kr_direction("BREADTH", policy, advance_fraction="0.45"), "NEGATIVE")
        self.assertEqual(kr_direction("BREADTH", policy, advance_fraction="0.50"), "NEUTRAL")
        # RISK_VOL ladder 1.5 / 2.5 / 3.5, each an inclusive ceiling.
        self.assertEqual(kr_direction("RISK_VOL", policy, move="1.5"), "POSITIVE")
        self.assertEqual(kr_direction("RISK_VOL", policy, move="2.5"), "NEUTRAL")
        self.assertEqual(kr_direction("RISK_VOL", policy, move="3.5"), "NEGATIVE")
        self.assertEqual(kr_direction("RISK_VOL", policy, move="3.500001"), "STRESS")
        # LIQUIDITY positive_min = 5, negative_max = -5 (both inclusive).
        self.assertEqual(kr_direction("LIQUIDITY", policy, trading_value_change="5"), "POSITIVE")
        self.assertEqual(kr_direction("LIQUIDITY", policy, trading_value_change="-5"), "NEGATIVE")
        self.assertEqual(kr_direction("LIQUIDITY", policy, trading_value_change="4.999999"), "NEUTRAL")
        # LEADERSHIP positive_min = 0.60, negative_max = 0.40 on the positive
        # sector fraction (3/5, 2/5, 5/10).
        self.assertEqual(
            kr_direction("LEADERSHIP", policy, sectors=("1", "1", "1", "-1", "-1")), "POSITIVE")
        self.assertEqual(
            kr_direction("LEADERSHIP", policy, sectors=("1", "1", "-1", "-1", "-1")), "NEGATIVE")
        self.assertEqual(
            kr_direction("LEADERSHIP", policy, sectors=("1",) * 5 + ("-1",) * 5), "NEUTRAL")

    def test_kr_policy_each_threshold_is_causal(self):
        # Each row: one declared slot, a fixed measurement, the direction the
        # shipped policy produces, and the direction a valid alternate policy
        # produces.  A slot the code ignores cannot pass its row.
        cases = [
            ("BREADTH.positive_min", "BREADTH", {"advance_fraction": "0.50"}, "NEUTRAL",
             {"BREADTH": {"positive_min": "0.50"}}, "POSITIVE"),
            ("BREADTH.negative_max", "BREADTH", {"advance_fraction": "0.50"}, "NEUTRAL",
             {"BREADTH": {"negative_max": "0.50"}}, "NEGATIVE"),
            ("RISK_VOL.positive_max", "RISK_VOL", {"move": "2.0"}, "NEUTRAL",
             {"RISK_VOL": {"positive_max": "2.0"}}, "POSITIVE"),
            ("RISK_VOL.neutral_max", "RISK_VOL", {"move": "2.0"}, "NEUTRAL",
             {"RISK_VOL": {"neutral_max": "1.9"}}, "NEGATIVE"),
            ("RISK_VOL.negative_max", "RISK_VOL", {"move": "3.0"}, "NEGATIVE",
             {"RISK_VOL": {"negative_max": "2.9", "stress_above": "2.9"}}, "STRESS"),
            ("LIQUIDITY.positive_min", "LIQUIDITY", {"trading_value_change": "3.0"}, "NEUTRAL",
             {"LIQUIDITY": {"positive_min": "3"}}, "POSITIVE"),
            ("LIQUIDITY.negative_max", "LIQUIDITY", {"trading_value_change": "-3.0"}, "NEUTRAL",
             {"LIQUIDITY": {"negative_max": "-3"}}, "NEGATIVE"),
            ("LEADERSHIP.positive_min", "LEADERSHIP", {"sectors": ("1",) * 5 + ("-1",) * 5}, "NEUTRAL",
             {"LEADERSHIP": {"positive_min": "0.50"}}, "POSITIVE"),
            ("LEADERSHIP.negative_max", "LEADERSHIP", {"sectors": ("1",) * 5 + ("-1",) * 5}, "NEUTRAL",
             {"LEADERSHIP": {"negative_max": "0.50"}}, "NEGATIVE"),
        ]
        self.assertEqual(len(cases), 9)
        for label, axis_name, measurement, baseline, override, expected in cases:
            with self.subTest(slot=label):
                self.assertEqual(kr_direction(axis_name, kr_policy_fixture(), **measurement), baseline)
                variant = kr_policy_fixture()
                for name, values in override.items():
                    variant["markets"]["KR"][name].update(values)
                self.assertNotEqual(baseline, expected)
                self.assertEqual(kr_direction(axis_name, variant, **measurement), expected)

    def test_kr_policy_method_and_structure_fail_closed(self):
        packet = kr_packet_fixture()

        def assert_closed(policy, label):
            with self.subTest(case=label):
                with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "KR_POLICY_INVALID"):
                    MODULE.build_kr(packet, policy)

        self.assertTrue(MODULE.build_kr(packet, kr_policy_fixture()))

        missing_markets = kr_policy_fixture()
        del missing_markets["markets"]
        assert_closed(missing_markets, "markets missing")
        for bad in ([], "KR", None):
            policy = kr_policy_fixture()
            policy["markets"] = bad
            assert_closed(policy, f"markets {bad!r}")

        missing_kr = kr_policy_fixture()
        del missing_kr["markets"]["KR"]
        assert_closed(missing_kr, "KR missing")
        for bad in ("KR", [], None):
            policy = kr_policy_fixture()
            policy["markets"]["KR"] = bad
            assert_closed(policy, f"KR {bad!r}")

        for axis_name in MODULE.AXES:
            policy = kr_policy_fixture()
            del policy["markets"]["KR"][axis_name]
            assert_closed(policy, f"{axis_name} missing")

            policy = kr_policy_fixture()
            policy["markets"]["KR"][axis_name] = "combined_advance_fraction"
            assert_closed(policy, f"{axis_name} not an object")

            policy = kr_policy_fixture()
            del policy["markets"]["KR"][axis_name]["method"]
            assert_closed(policy, f"{axis_name}.method missing")

            policy = kr_policy_fixture()
            policy["markets"]["KR"][axis_name]["method"] = "unimplemented_rule"
            assert_closed(policy, f"{axis_name}.method unimplemented")

            # A method name that is real but belongs to a different axis is
            # still not the rule this axis executes.
            policy = kr_policy_fixture()
            other = "combined_advance_fraction" if axis_name != "BREADTH" else "positive_sector_return_fraction"
            policy["markets"]["KR"][axis_name]["method"] = other
            assert_closed(policy, f"{axis_name}.method swapped")

        for key in ("positive", "negative", "neutral"):
            policy = kr_policy_fixture()
            del policy["markets"]["KR"]["TREND"][key]
            assert_closed(policy, f"TREND.{key} missing")

            policy = kr_policy_fixture()
            policy["markets"]["KR"]["TREND"][key] = "any_positive"
            assert_closed(policy, f"TREND.{key} unimplemented")

        # The declared TREND semantics are what sign_pair actually implements,
        # so swapping positive and negative is rejected rather than honoured.
        swapped = kr_policy_fixture()
        swapped["markets"]["KR"]["TREND"]["positive"] = "both_negative"
        swapped["markets"]["KR"]["TREND"]["negative"] = "both_positive"
        assert_closed(swapped, "TREND signs swapped")

    def test_kr_policy_threshold_constraints_fail_closed(self):
        packet = kr_packet_fixture()

        def assert_closed(policy, label):
            with self.subTest(case=label):
                with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "KR_POLICY_INVALID"):
                    MODULE.build_kr(packet, policy)

        for axis_name, key in KR_THRESHOLD_SLOTS:
            policy = kr_policy_fixture()
            del policy["markets"]["KR"][axis_name][key]
            assert_closed(policy, f"{axis_name}.{key} missing")
            for bad in (True, False, "NaN", "Infinity", "-Infinity", "abc", "", None, [], {}, "0.5x"):
                policy = kr_policy_fixture()
                policy["markets"]["KR"][axis_name][key] = bad
                assert_closed(policy, f"{axis_name}.{key} = {bad!r}")

        inversions = [
            ("BREADTH inverted", "BREADTH", {"positive_min": "0.40"}),
            ("BREADTH equal edges", "BREADTH", {"negative_max": "0.55"}),
            ("LIQUIDITY inverted", "LIQUIDITY", {"positive_min": "-6"}),
            ("LIQUIDITY equal edges", "LIQUIDITY", {"negative_max": "5"}),
            ("LEADERSHIP inverted", "LEADERSHIP", {"positive_min": "0.30"}),
            ("LEADERSHIP equal edges", "LEADERSHIP", {"negative_max": "0.60"}),
        ]
        for label, axis_name, values in inversions:
            policy = kr_policy_fixture()
            policy["markets"]["KR"][axis_name].update(values)
            assert_closed(policy, label)

        fractions = [
            ("BREADTH", {"positive_min": "1.5"}),
            ("BREADTH", {"negative_max": "-0.01"}),
            ("LEADERSHIP", {"positive_min": "1.01"}),
            ("LEADERSHIP", {"negative_max": "-0.5"}),
        ]
        for axis_name, values in fractions:
            policy = kr_policy_fixture()
            policy["markets"]["KR"][axis_name].update(values)
            assert_closed(policy, f"{axis_name} fraction bound {values}")

        ladders = [
            ("neutral below positive", {"neutral_max": "1.0"}),
            ("neutral equals positive", {"neutral_max": "1.5"}),
            ("negative below neutral", {"negative_max": "2.0", "stress_above": "2.0"}),
            ("negative equals neutral", {"negative_max": "2.5", "stress_above": "2.5"}),
            ("negative risk ladder", {"positive_max": "-0.5"}),
        ]
        for label, values in ladders:
            policy = kr_policy_fixture()
            policy["markets"]["KR"]["RISK_VOL"].update(values)
            assert_closed(policy, f"RISK_VOL {label}")

        # LIQUIDITY is a percent change, so a negative edge is legitimate there
        # and must not be rejected by the fraction rule.
        wide = kr_policy_fixture()
        wide["markets"]["KR"]["LIQUIDITY"].update({"positive_min": "12", "negative_max": "-12"})
        self.assertEqual(kr_direction("LIQUIDITY", wide, trading_value_change="-12"), "NEGATIVE")

    def test_kr_policy_stress_alias_fail_closed(self):
        packet = kr_packet_fixture()
        for bad in ("3.6", "3.4", "0", "999"):
            policy = kr_policy_fixture()
            policy["markets"]["KR"]["RISK_VOL"]["stress_above"] = bad
            with self.subTest(stress_above=bad):
                with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "KR_POLICY_INVALID"):
                    MODULE.build_kr(packet, policy)

        missing = kr_policy_fixture()
        del missing["markets"]["KR"]["RISK_VOL"]["stress_above"]
        with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "KR_POLICY_INVALID"):
            MODULE.build_kr(packet, missing)

        # stress_above is the NEGATIVE ceiling restated, not an independent
        # edge: an equal value written differently is accepted and changes
        # nothing.
        restated = kr_policy_fixture()
        restated["markets"]["KR"]["RISK_VOL"]["stress_above"] = "3.500"
        self.assertEqual(kr_direction("RISK_VOL", restated, move="3.5"), "NEGATIVE")
        self.assertEqual(kr_direction("RISK_VOL", restated, move="3.6"), "STRESS")

        # Moving the ceiling requires moving the alias with it, and then the
        # STRESS edge really does move.
        lowered = kr_policy_fixture()
        lowered["markets"]["KR"]["RISK_VOL"].update({"negative_max": "3.0", "stress_above": "3.0"})
        self.assertEqual(kr_direction("RISK_VOL", lowered, move="3.0"), "NEGATIVE")
        self.assertEqual(kr_direction("RISK_VOL", lowered, move="3.2"), "STRESS")

    def test_kr_policy_hash_and_calculation_binding(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            for name in (
                "latest_free_market_data.json",
                "latest_crypto_regime_refresh_status.json",
            ):
                shutil.copy2(ROOT / "data" / name, root / "data" / name)
            # Pin the KR measurement used by the assertions below. The latest
            # pointer changes with each daily acquisition.
            retained_kr_path = (
                ROOT / "data/observations/korea_market_signals/2026-09-03/packet.json"
            )
            shutil.copy2(retained_kr_path, root / "data/latest_korea_market_signals.json")

            baseline = MODULE.build_reference(root)
            base_kr = {row["market"]: row for row in baseline["markets"]}["KR"]
            self.assertEqual(
                base_kr,
                MODULE.build_kr(
                    json.loads(retained_kr_path.read_text(encoding="utf-8")),
                    kr_policy_fixture(),
                    render_version=baseline.get("render_version"),
                ),
            )
            base_breadth = {row["axis"]: row for row in base_kr["axes"]}["BREADTH"]
            # The retained KR advance_fraction really is inside the NEGATIVE
            # band under the shipped policy.
            self.assertEqual(base_breadth["direction"], "NEGATIVE")
            observed = MODULE.Decimal(base_breadth["observed_value"]["advance_fraction"])
            self.assertLess(observed, MODULE.Decimal("0.45"))
            self.assertGreater(observed, MODULE.Decimal("0.30"))

            policy_path = root / "config" / "paper_regime_reference_policy_v1.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["markets"]["KR"]["BREADTH"]["negative_max"] = "0.30"
            policy_path.write_text(
                json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            variant = MODULE.build_reference(root)
            variant_kr = {row["market"]: row for row in variant["markets"]}["KR"]
            variant_breadth = {row["axis"]: row for row in variant_kr["axes"]}["BREADTH"]

            # The declared policy is causal on the real retained measurement.
            self.assertEqual(variant_breadth["direction"], "NEUTRAL")
            self.assertEqual(variant_breadth["observed_value"], base_breadth["observed_value"])
            self.assertEqual(variant_kr["paper_reference"]["score"], base_kr["paper_reference"]["score"] + 1)

            # The policy bytes are hashed into both identity fields.
            self.assertNotEqual(variant["policy"]["sha256"], baseline["policy"]["sha256"])
            self.assertNotEqual(variant["generation_id"], baseline["generation_id"])
            self.assertNotEqual(variant["payload_sha256"], baseline["payload_sha256"])
            self.assertEqual(MODULE.validate_reference(variant, root), variant)

            # Nothing outside the KR calculation moved.
            self.assertEqual(variant["contract_version"], baseline["contract_version"])
            self.assertEqual(variant["schema_version"], baseline["schema_version"])
            self.assertEqual(variant["mode"], baseline["mode"])
            self.assertEqual(variant["status"], baseline["status"])
            self.assertEqual(variant["sources"], baseline["sources"])
            self.assertEqual(variant["authority"], baseline["authority"])
            variant_markets = {row["market"]: row for row in variant["markets"]}
            for market in ("US", "CRYPTO"):
                self.assertEqual(
                    variant_markets[market],
                    {row["market"]: row for row in baseline["markets"]}[market],
                )
            self.assertEqual(variant_kr["runtime_regime"], "UNKNOWN")
            self.assertEqual(variant_kr["classification_status"], base_kr["classification_status"])
            for axis_name in ("TREND", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"):
                self.assertEqual(
                    {row["axis"]: row for row in variant_kr["axes"]}[axis_name],
                    {row["axis"]: row for row in base_kr["axes"]}[axis_name],
                )


if __name__ == "__main__":
    unittest.main()
