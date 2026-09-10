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


def crypto_coverage(defined_axes: tuple[str, ...]) -> dict:
    defined = list(defined_axes)
    return {
        "required_count": 5,
        "defined_count": len(defined),
        "ratio": f"{len(defined)}/5",
        "defined_axes": defined,
        "missing_axes": [axis for axis in MODULE.AXES if axis not in defined],
    }


def crypto_status_fixture(
    *,
    current_axes: tuple[str, ...] = tuple(MODULE.AXES),
    official_axes: tuple[str, ...] = ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY"),
) -> dict:
    packet = {
        "schema_version": "crypto_regime_refresh_status/1",
        "generated_at": "2026-09-09T07:30:14Z",
        "authority": {
            "read_only_reference": True,
            "runtime_regime_authorized": False,
            "trading_authorized": False,
        },
        "current_reference": {
            "as_of_date": "2026-09-09",
            "price_as_of_date": "2026-09-08",
            "coverage": crypto_coverage(current_axes),
            "leadership_code": "MIXED_WINDOW_LEADERSHIP",
            "mode": MODULE.CURRENT_REFERENCE_MODE,
        },
        "official_decision": {
            "classification_status": "WAIT_PIT_LEADERSHIP_HISTORY",
            "coverage": crypto_coverage(official_axes),
        },
    }
    packet["payload_sha256"] = MODULE.payload_sha256(packet)
    return packet


def resign_crypto(packet: dict) -> dict:
    packet.pop("payload_sha256", None)
    packet["payload_sha256"] = MODULE.payload_sha256(packet)
    return packet


def kr_direction(axis_name: str, policy: dict, **measurement) -> str:
    rows = {row["axis"]: row for row in MODULE.build_kr(kr_packet_fixture(**measurement), policy)["axes"]}
    return rows[axis_name]["direction"]


class PaperRegimeReferenceTest(unittest.TestCase):
    def test_current_free_inputs_make_plain_paper_reference(self):
        packet = MODULE.build_reference()
        markets = {row["market"]: row for row in packet["markets"]}
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
        source = json.loads((ROOT / "data/latest_crypto_regime_refresh_status.json").read_text())
        expected_coverage = source.get("current_reference", {}).get(
            "coverage", source["official_decision"]["coverage"]
        )
        self.assertEqual(markets["CRYPTO"]["coverage"]["ratio"], expected_coverage["ratio"])
        self.assertEqual(
            markets["CRYPTO"]["coverage"]["defined_count"],
            expected_coverage["defined_count"],
        )
        self.assertEqual(
            markets["CRYPTO"]["coverage"]["missing_axes"],
            expected_coverage["missing_axes"],
        )
        self.assertEqual(
            markets["CRYPTO"]["official_validation"]["coverage"],
            source["official_decision"]["coverage"],
        )
        if expected_coverage["ratio"] == "5/5":
            self.assertEqual(packet["status"], "REFERENCE_AVAILABLE")
            self.assertIn(
                markets["CRYPTO"]["paper_reference"]["candidate_regime"],
                {"RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"},
            )
            self.assertEqual(
                markets["CRYPTO"]["paper_reference"]["score"],
                sum(row["score"] for row in markets["CRYPTO"]["axes"]),
            )
            self.assertEqual(
                [row["axis"] for row in markets["CRYPTO"]["axes"]],
                list(MODULE.AXES),
            )
            for row in markets["CRYPTO"]["axes"]:
                self.assertIn(
                    row["direction"],
                    {"POSITIVE", "NEUTRAL", "NEGATIVE", "STRESS"},
                )
            self.assertEqual(
                markets["CRYPTO"]["classification_status"],
                "PAPER_REFERENCE_CLASSIFIED",
            )
            self.assertEqual(markets["CRYPTO"]["leadership_code"], "MIXED_WINDOW_LEADERSHIP")
            self.assertEqual(
                markets["CRYPTO"]["mode"],
                "CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY",
            )
            self.assertEqual(
                markets["CRYPTO"]["price_as_of_date"],
                source["current_reference"]["price_as_of_date"],
            )
            self.assertEqual(
                markets["CRYPTO"]["caveats"],
                [
                    "PROVISIONAL_CRYPTO_PAPER_POLICY",
                    "RISK_DIRECTION_ONLY",
                    "ABSOLUTE_STRESS_NOT_ASSESSED",
                    "CONFIDENCE_MATCHING_AXIS_FRACTION_NOT_PROBABILITY",
                    "CURRENT_REFERENCE_NOT_PIT_REPLAY",
                ],
            )
        else:
            self.assertEqual(packet["status"], "PARTIAL_REFERENCE_AVAILABLE")
            self.assertEqual(
                markets["CRYPTO"]["paper_reference"]["candidate_regime"],
                "UNKNOWN",
            )
            self.assertIsNone(markets["CRYPTO"]["paper_reference"]["score"])
            self.assertEqual(markets["CRYPTO"]["axes"], [])
            expected_status = (
                "WAIT_OFFICIAL_DECISION_REFRESH"
                if source["official_decision"]["classification_status"]
                == "WAIT_OFFICIAL_DECISION_REFRESH"
                else "WAIT_OFFICIAL_INPUT_COVERAGE"
            )
            self.assertEqual(
                markets["CRYPTO"]["classification_status"],
                expected_status,
            )
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
        source = crypto_status_fixture()
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

    def test_current_crypto_rejects_three_demonstrated_validation_gaps(self):
        cases = []

        missing_mode = crypto_status_fixture()
        missing_mode["current_reference"].pop("mode")
        cases.append(("current_mode_missing", resign_crypto(missing_mode), "CRYPTO_CURRENT_MODE_INVALID"))

        stale_date = crypto_status_fixture()
        stale_date["current_reference"]["as_of_date"] = "2020-01-01"
        cases.append(("current_date_stale_vs_packet", resign_crypto(stale_date), "CRYPTO_CURRENT_DATE_CONTEXT_INVALID"))

        inconsistent = crypto_status_fixture()
        inconsistent["current_reference"]["coverage"] = {
            "defined_axes": ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY"],
            "defined_count": 5,
            "missing_axes": ["LEADERSHIP"],
            "ratio": "5/5",
            "required_count": 5,
        }
        cases.append(("current_coverage5_with_missing_leadership", resign_crypto(inconsistent), "CRYPTO_CURRENT_COVERAGE_INVALID"))

        for label, packet, code in cases:
            with self.subTest(case=label):
                with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, code):
                    MODULE.build_crypto(packet)

    def test_current_crypto_mode_dates_and_axis_partition_fail_closed(self):
        mutations = (
            ("wrong mode", lambda p: p["current_reference"].update(mode="PIT_REPLAY"), "CRYPTO_CURRENT_MODE_INVALID"),
            ("malformed decision date", lambda p: p["current_reference"].update(as_of_date="2026-09-XX"), "CRYPTO_CURRENT_DATE_INVALID"),
            ("malformed price date", lambda p: p["current_reference"].update(price_as_of_date="2026-09-XX"), "CRYPTO_CURRENT_PRICE_DATE_INVALID"),
            ("future decision date", lambda p: p["current_reference"].update(as_of_date="2026-09-10"), "CRYPTO_CURRENT_DATE_CONTEXT_INVALID"),
            ("same-day price", lambda p: p["current_reference"].update(price_as_of_date="2026-09-09"), "CRYPTO_CURRENT_PRICE_DATE_CONTEXT_INVALID"),
            ("future price", lambda p: p["current_reference"].update(price_as_of_date="2026-09-10"), "CRYPTO_CURRENT_PRICE_DATE_CONTEXT_INVALID"),
            ("malformed generation time", lambda p: p.update(generated_at="2026-09-09T99:30:14Z"), "CRYPTO_CURRENT_GENERATION_TIME_INVALID"),
            ("boolean count", lambda p: p["current_reference"]["coverage"].update(defined_count=True), "CRYPTO_CURRENT_COVERAGE_INVALID"),
            ("boolean required count", lambda p: p["current_reference"]["coverage"].update(required_count=True), "CRYPTO_CURRENT_COVERAGE_INVALID"),
            ("duplicate axis", lambda p: p["current_reference"]["coverage"].update(defined_axes=["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LIQUIDITY"]), "CRYPTO_CURRENT_COVERAGE_INVALID"),
            ("unordered axes", lambda p: p["current_reference"]["coverage"].update(defined_axes=["BREADTH", "TREND", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]), "CRYPTO_CURRENT_COVERAGE_INVALID"),
            ("unknown axis", lambda p: p["current_reference"]["coverage"].update(defined_axes=["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "UNKNOWN"]), "CRYPTO_CURRENT_COVERAGE_INVALID"),
        )
        for label, mutate, code in mutations:
            with self.subTest(case=label):
                packet = crypto_status_fixture()
                mutate(packet)
                with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, code):
                    MODULE.build_crypto(resign_crypto(packet))

        malformed = crypto_status_fixture()
        malformed["current_reference"] = []
        with self.assertRaisesRegex(
            MODULE.PaperRegimeReferenceError, "CRYPTO_CURRENT_REFERENCE_INVALID"
        ):
            MODULE.build_crypto(resign_crypto(malformed))

        malformed_coverage = crypto_status_fixture()
        malformed_coverage["current_reference"]["coverage"] = None
        with self.assertRaisesRegex(
            MODULE.PaperRegimeReferenceError, "CRYPTO_CURRENT_COVERAGE_INVALID"
        ):
            MODULE.build_crypto(resign_crypto(malformed_coverage))

    def test_current_crypto_valid_partial_is_truthful_and_metadata_is_minimal(self):
        complete = MODULE.build_crypto(crypto_status_fixture())
        self.assertEqual(complete["mode"], MODULE.CURRENT_REFERENCE_MODE)
        self.assertEqual(complete["price_as_of_date"], "2026-09-08")
        self.assertEqual(complete["paper_reference"], {
            "candidate_regime": "UNKNOWN",
            "score": None,
            "confidence": None,
            "explanation_ko": "오늘 리더십을 포함한 필수 신호 5개는 모두 확인됐습니다. 코인 전용 방향·점수 규칙이 확정될 때까지 Risk On/Off 판정만 보류합니다.",
        })
        self.assertEqual(complete["runtime_regime"], "UNKNOWN")
        self.assertEqual(complete["axes"], [])
        self.assertEqual(complete["official_validation"]["coverage"]["ratio"], "4/5")

        partial_packet = crypto_status_fixture(
            current_axes=("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY"),
            official_axes=("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY"),
        )
        partial = MODULE.build_crypto(partial_packet)
        self.assertEqual(partial["coverage"]["ratio"], "4/5")
        self.assertEqual(partial["classification_status"], "WAIT_OFFICIAL_INPUT_COVERAGE")
        self.assertIn("4/5개 확인", partial["paper_reference"]["explanation_ko"])
        self.assertNotIn("5개 모두 확인", partial["paper_reference"]["explanation_ko"])

        legacy = crypto_status_fixture()
        legacy["current_reference"] = {"as_of_date": "2026-09-09"}
        legacy_result = MODULE.build_crypto(resign_crypto(legacy))
        self.assertEqual(legacy_result["coverage"]["ratio"], "4/5")
        self.assertNotIn("mode", legacy_result)
        self.assertNotIn("price_as_of_date", legacy_result)

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

    def test_retained_current_crypto_v2_rederives_with_exact_identity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); frozen_render_sources(root)
            kr_v1 = MODULE.build_reference(
                root, render_version=MODULE.KR_TREND_RENDER_VERSION
            )
            self.assertEqual(kr_v1["generation_id"], "a07afeea2b92f425bc7b19ae1e9bd9cb8b28e6f411f84737f7ac7f0994e3ea66")
            self.assertEqual(kr_v1["payload_sha256"], "a47059dcb9e126652039e04a42e8298b460e43d9458a77a2dea323e2239ef7e4")
            self.assertEqual(MODULE.validate_reference(kr_v1, root), kr_v1)

            retained = MODULE.build_reference(
                root, render_version=MODULE.LEGACY_CURRENT_RENDER_VERSION
            )
            self.assertEqual(retained["render_version"], "paper_reference_current_crypto/v2")
            self.assertEqual(retained["generation_id"], "4fec8868fb69f5c6ef218f118a97eda3de9b741578d084279fd97f52ac4f30ba")
            self.assertEqual(retained["payload_sha256"], "fa3b5613e7a08f3595b22cf141711562c10238fcfebc4415dca61d0080777ee5")
            crypto = next(row for row in retained["markets"] if row["market"] == "CRYPTO")
            self.assertEqual(MODULE.payload_sha256(crypto), "53b80f99a8754aeee2d32252c89f30428c07525d07c740ce3b12052720102799")
            self.assertEqual(MODULE.validate_reference(retained, root), retained)

            crypto_path = root / "data/latest_crypto_regime_refresh_status.json"
            crypto_path.write_text(
                MODULE.canonical_json(crypto_status_fixture()), encoding="utf-8"
            )
            retained_current = MODULE.build_reference(
                root, render_version=MODULE.LEGACY_CURRENT_RENDER_VERSION
            )
            self.assertEqual(retained_current["generation_id"], "4c0cf92a2c166b82b019682b21787f51436326ea52488f4d102be39fc9874036")
            self.assertEqual(retained_current["payload_sha256"], "d50c704e26e41028d31bda3cefc250e892ae0c3f14054fe97c570501326b5fb5")
            current_crypto = next(
                row for row in retained_current["markets"] if row["market"] == "CRYPTO"
            )
            self.assertEqual(MODULE.payload_sha256(current_crypto), "45cada76ee53b2b20802bca547a9375fe7bf60037c37206e11b80cf394a5241c")
            self.assertNotIn("mode", current_crypto)
            self.assertNotIn("price_as_of_date", current_crypto)
            self.assertEqual(current_crypto["coverage"]["ratio"], "5/5")
            self.assertEqual(current_crypto["official_validation"]["coverage"]["ratio"], "4/5")
            self.assertEqual(
                MODULE.validate_reference(retained_current, root), retained_current
            )

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
            # Only the new renderer corrects the legacy partial-coverage text.
            # Pin both exact explanations before comparing every other field.
            self.assertEqual(
                before[2]["paper_reference"].pop("explanation_ko"),
                "오늘 참고 신호는 5개 모두 확인됐지만, 자동 판정용 주도 코인 이력은 아직 검증 중입니다.",
            )
            self.assertEqual(
                after[2]["paper_reference"].pop("explanation_ko"),
                "오늘 참고 신호는 0/5개 확인됐습니다. 확인되지 않은 신호가 있어 코인 판정을 보류합니다.",
            )
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
