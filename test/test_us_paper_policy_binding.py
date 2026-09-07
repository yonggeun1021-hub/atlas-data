#!/usr/bin/env python3
"""US PAPER reference thresholds must come from the retained policy file.

``regime/paper_regime_reference.py::build_us`` previously compared every US
observation against a literal written into the function body, so the nine
``markets.US`` numbers published in
``config/paper_regime_reference_policy_v1.json`` were documentation only: a
valid edited policy produced an unchanged classification.  These cases pin the
binding in both directions — the configured numbers are the ones executed, and
a structurally invalid policy blocks the US reference instead of falling back
to a hardcoded default.

The policy values themselves are not proposed, tuned, or rounded here.
0.666667 is asserted exactly as configured, including the consequence that a
2-of-3 TREND (0.6666...) sits *below* it and is therefore NEUTRAL.
"""

from __future__ import annotations

import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "paper_regime_reference.py"
SPEC = importlib.util.spec_from_file_location("us_paper_policy_binding_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

POLICY_PATH = ROOT / "config" / "paper_regime_reference_policy_v1.json"
POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

# The nine executed threshold slots, as published today.  TREND/LEADERSHIP
# share a fraction shape, BREADTH is an advance fraction, RISK_VOL is a VIX
# ladder whose STRESS entry point is republished as the stress_min alias.
CONFIGURED = {
    ("TREND", "positive_min_fraction"): "0.666667",
    ("TREND", "negative_max_fraction"): "0.333333",
    ("BREADTH", "positive_min"): "0.55",
    ("BREADTH", "negative_max"): "0.45",
    ("RISK_VOL", "positive_below"): "15",
    ("RISK_VOL", "neutral_below"): "25",
    ("RISK_VOL", "negative_below"): "30",
    ("LEADERSHIP", "positive_min_fraction"): "0.666667",
    ("LEADERSHIP", "negative_max_fraction"): "0.333333",
}
STRESS_ALIAS = ("RISK_VOL", "stress_min", "30")


def us_packet(
    trend_positive: int = 2,
    advance_fraction: str = "0.50",
    vix: str = "20",
    liquidity: tuple[str, str] = ("1000", "2000"),
    leadership_positive: int = 8,
) -> dict:
    """A fixed synthetic packet in exactly the shape build_us consumes.

    Nothing is read from the mutable ``data/latest_*`` snapshots, so every
    boundary below is a property of the policy and the builder, not of today's
    market.
    """
    return {
        "observed_at_utc": "2026-09-04T21:42:06Z",
        "fred": {"series_id": "VIXCLS", "value": vix},
        "fred_liquidity": {
            "series": [
                {"series_id": "WRESBAL", "change": liquidity[0]},
                {"series_id": "TOTBKCR", "change": liquidity[1]},
            ],
        },
        "us_market_reference": {
            "as_of_session_date": "2026-09-04",
            "status": "READY",
            "trend_etfs": [
                {
                    "symbol": symbol,
                    "returns": {"20_session_pct": "1.0" if index < trend_positive else "-1.0"},
                }
                for index, symbol in enumerate(("SPY", "QQQ", "IWM"))
            ],
            "proxy_axes": {
                "BREADTH": {"measurement": {"advance_fraction": advance_fraction}},
                "LEADERSHIP": {
                    "measurement": {
                        "ordered_groups": [
                            {
                                "symbol": f"G{index:02d}",
                                "return_pct": "1.0" if index < leadership_positive else "-1.0",
                            }
                            for index in range(12)
                        ],
                    },
                },
            },
        },
    }


def edited(*changes: tuple[str, str, object]) -> dict:
    """The retained policy with only the named ``markets.US`` keys replaced."""
    policy = copy.deepcopy(POLICY)
    for axis_name, key, value in changes:
        policy["markets"]["US"][axis_name][key] = value
    return policy


def vix_ladder(positive_below: str, neutral_below: str, negative_below: str) -> dict:
    """The retained policy with a replaced VIX ladder and a matching alias."""
    return edited(
        ("RISK_VOL", "positive_below", positive_below),
        ("RISK_VOL", "neutral_below", neutral_below),
        ("RISK_VOL", "negative_below", negative_below),
        ("RISK_VOL", "stress_min", negative_below),
    )


def dropped(axis_name: str, key: str) -> dict:
    policy = copy.deepcopy(POLICY)
    del policy["markets"]["US"][axis_name][key]
    return policy


def directions(packet: dict, policy: dict) -> dict:
    return {row["axis"]: row["direction"] for row in MODULE.build_us(packet, policy)["axes"]}


class USPaperPolicyBindingTest(unittest.TestCase):
    def assertFailsClosed(self, policy: dict, code: str, packet: dict | None = None) -> None:
        with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, re.escape(code)):
            MODULE.build_us(us_packet() if packet is None else packet, policy)

    # ------------------------------------------------------------------
    def test_existing_policy_boundaries(self):
        """The configured numbers are consumed verbatim and decide the boundary."""
        resolved = MODULE._us_market_policy(POLICY)
        for (axis_name, key), text in CONFIGURED.items():
            self.assertEqual(POLICY["markets"]["US"][axis_name][key], text, f"{axis_name}.{key}")
        alias_axis, alias_key, alias_text = STRESS_ALIAS
        self.assertEqual(POLICY["markets"]["US"][alias_axis][alias_key], alias_text)
        self.assertEqual(
            resolved,
            {
                "TREND": {"positive_min": Decimal("0.666667"), "negative_max": Decimal("0.333333")},
                "BREADTH": {"positive_min": Decimal("0.55"), "negative_max": Decimal("0.45")},
                "LEADERSHIP": {"positive_min": Decimal("0.666667"), "negative_max": Decimal("0.333333")},
                "RISK_VOL": {
                    "positive_below": Decimal("15"),
                    "neutral_below": Decimal("25"),
                    "negative_below": Decimal("30"),
                    "stress_min": Decimal("30"),
                },
            },
        )

        # TREND: the configured 0.666667 is a rounded threshold, so 2-of-3
        # (0.6666...) is below it and 1-of-3 (0.3333...) is above 0.333333.
        # Only a unanimous set clears either side.  This is the published
        # policy, not a substituted rational 2/3.
        for positive, expected in ((3, "POSITIVE"), (2, "NEUTRAL"), (1, "NEUTRAL"), (0, "NEGATIVE")):
            self.assertEqual(directions(us_packet(trend_positive=positive), POLICY)["TREND"], expected, positive)

        for fraction, expected in (
            ("0.550001", "POSITIVE"), ("0.55", "POSITIVE"), ("0.549999", "NEUTRAL"),
            ("0.450001", "NEUTRAL"), ("0.45", "NEGATIVE"), ("0.449999", "NEGATIVE"),
        ):
            self.assertEqual(directions(us_packet(advance_fraction=fraction), POLICY)["BREADTH"], expected, fraction)

        # US keeps strict `<` at every VIX rung: the configured level itself
        # belongs to the worse band.
        for vix, expected in (
            ("0", "POSITIVE"), ("14.999999", "POSITIVE"), ("15", "NEUTRAL"),
            ("24.999999", "NEUTRAL"), ("25", "NEGATIVE"),
            ("29.999999", "NEGATIVE"), ("30", "STRESS"), ("55", "STRESS"),
        ):
            self.assertEqual(directions(us_packet(vix=vix), POLICY)["RISK_VOL"], expected, vix)

        for positive, expected in (
            (12, "POSITIVE"), (9, "POSITIVE"), (8, "NEUTRAL"),
            (4, "NEUTRAL"), (3, "NEGATIVE"), (0, "NEGATIVE"),
        ):
            self.assertEqual(
                directions(us_packet(leadership_positive=positive), POLICY)["LEADERSHIP"], expected, positive,
            )

        # LIQUIDITY is a sign pair with no numeric threshold to bind.
        for changes, expected in (
            (("1", "2"), "POSITIVE"), (("-1", "-2"), "NEGATIVE"),
            (("1", "-2"), "NEUTRAL"), (("0", "2"), "NEUTRAL"), (("0", "0"), "NEUTRAL"),
        ):
            self.assertEqual(directions(us_packet(liquidity=changes), POLICY)["LIQUIDITY"], expected, changes)

    # ------------------------------------------------------------------
    def test_nine_thresholds_are_causal(self):
        """Editing any one of the nine slots moves exactly its own axis."""
        cases = (
            ("TREND", (("TREND", "positive_min_fraction", "0.6"),), {"trend_positive": 2}, "NEUTRAL", "POSITIVE"),
            ("TREND", (("TREND", "negative_max_fraction", "0.34"),), {"trend_positive": 1}, "NEUTRAL", "NEGATIVE"),
            ("BREADTH", (("BREADTH", "positive_min", "0.50"),), {"advance_fraction": "0.50"}, "NEUTRAL", "POSITIVE"),
            ("BREADTH", (("BREADTH", "negative_max", "0.50"),), {"advance_fraction": "0.50"}, "NEUTRAL", "NEGATIVE"),
            ("RISK_VOL", (("RISK_VOL", "positive_below", "21"),), {"vix": "20"}, "NEUTRAL", "POSITIVE"),
            ("RISK_VOL", (("RISK_VOL", "neutral_below", "20"),), {"vix": "20"}, "NEUTRAL", "NEGATIVE"),
            (
                "RISK_VOL",
                (("RISK_VOL", "negative_below", "26"), ("RISK_VOL", "stress_min", "26")),
                {"vix": "27"},
                "NEGATIVE",
                "STRESS",
            ),
            ("LEADERSHIP", (("LEADERSHIP", "positive_min_fraction", "0.6"),), {"leadership_positive": 8}, "NEUTRAL", "POSITIVE"),
            ("LEADERSHIP", (("LEADERSHIP", "negative_max_fraction", "0.34"),), {"leadership_positive": 4}, "NEUTRAL", "NEGATIVE"),
        )
        covered = set()
        for axis_name, changes, packet_kwargs, before, after in cases:
            packet = us_packet(**packet_kwargs)
            baseline = directions(packet, POLICY)
            moved = directions(packet, edited(*changes))
            self.assertEqual(baseline[axis_name], before, changes)
            self.assertEqual(moved[axis_name], after, changes)
            # Only the edited axis may move; the other four are untouched.
            self.assertEqual(
                {name: value for name, value in moved.items() if name != axis_name},
                {name: value for name, value in baseline.items() if name != axis_name},
                changes,
            )
            covered.update((edit[0], edit[1]) for edit in changes if edit[1] != "stress_min")
        self.assertEqual(covered, set(CONFIGURED))
        self.assertEqual(len(covered), 9)

    # ------------------------------------------------------------------
    def test_methods_structures_and_signs_fail_closed(self):
        """A malformed root, block, method, sign, or value blocks the build."""
        for mutation in ({}, {"markets": None}, {"markets": []}, {"markets": {"KR": POLICY["markets"]["KR"]}}):
            policy = copy.deepcopy(POLICY)
            policy.pop("markets", None)
            policy.update(mutation)
            self.assertFailsClosed(policy, "US_POLICY_MISSING")
        for value in (None, [], "US"):
            policy = copy.deepcopy(POLICY)
            policy["markets"]["US"] = value
            self.assertFailsClosed(policy, "US_POLICY_MISSING")

        for axis_name in MODULE.AXES:
            missing = copy.deepcopy(POLICY)
            del missing["markets"]["US"][axis_name]
            self.assertFailsClosed(missing, f"US_POLICY_BLOCK_INVALID:{axis_name}")
            for value in (None, [], "on"):
                replaced = copy.deepcopy(POLICY)
                replaced["markets"]["US"][axis_name] = value
                self.assertFailsClosed(replaced, f"US_POLICY_BLOCK_INVALID:{axis_name}")

            # A missing, retyped, renamed, or borrowed-from-KR method is not
            # the method these comparisons implement.
            for method in (None, "", 1, "positive_20_session_group_return_fractions", POLICY["markets"]["KR"][axis_name]["method"]):
                self.assertFailsClosed(edited((axis_name, "method", method)), f"US_POLICY_METHOD_INVALID:{axis_name}")
            self.assertFailsClosed(dropped(axis_name, "method"), f"US_POLICY_METHOD_INVALID:{axis_name}")

        for key, configured in MODULE.US_LIQUIDITY_SIGNS.items():
            for sign in (None, "", "both_up", "mixed", configured.upper()):
                self.assertFailsClosed(edited(("LIQUIDITY", key, sign)), f"US_POLICY_SIGN_INVALID:{key}")
            self.assertFailsClosed(dropped("LIQUIDITY", key), f"US_POLICY_SIGN_INVALID:{key}")

        for axis_name, key in list(CONFIGURED) + [STRESS_ALIAS[:2]]:
            self.assertFailsClosed(dropped(axis_name, key), f"US_POLICY_VALUE_MISSING:{axis_name}.{key}")
            for value in (None, True, False, [], {}, "", " ", "0.55x", "high", "NaN", "Infinity", "-Infinity"):
                self.assertFailsClosed(
                    edited((axis_name, key, value)), f"US_POLICY_VALUE_INVALID:{axis_name}.{key}",
                )

        # Unchanged policy still builds, so the guards above reject only defects.
        self.assertEqual(len(MODULE.build_us(us_packet(), POLICY)["axes"]), 5)

    # ------------------------------------------------------------------
    def test_numeric_order_and_fraction_guards(self):
        """Fractions stay inside [0,1] and the ladders stay strictly ordered."""
        for axis_name, positive_key, negative_key in MODULE.US_RATIO_AXES:
            for value in ("1.000001", "2", "-0.000001", "-1"):
                self.assertFailsClosed(
                    edited((axis_name, positive_key, value), (axis_name, negative_key, "0")),
                    f"US_POLICY_FRACTION_RANGE_INVALID:{axis_name}",
                )
                self.assertFailsClosed(
                    edited((axis_name, negative_key, value), (axis_name, positive_key, "1")),
                    f"US_POLICY_FRACTION_RANGE_INVALID:{axis_name}",
                )
            for positive_min, negative_max in (("0.5", "0.5"), ("0.4", "0.6"), ("0", "1")):
                self.assertFailsClosed(
                    edited((axis_name, positive_key, positive_min), (axis_name, negative_key, negative_max)),
                    f"US_POLICY_FRACTION_ORDER_INVALID:{axis_name}",
                )
            # The inclusive endpoints and an adjacent-but-ordered pair are legal.
            for positive_min, negative_max in (("1", "0"), ("0.5", "0.499999")):
                self.assertEqual(
                    len(MODULE.build_us(
                        us_packet(), edited((axis_name, positive_key, positive_min), (axis_name, negative_key, negative_max)),
                    )["axes"]),
                    5,
                )

        self.assertEqual(MODULE.US_VIX_LADDER, ("positive_below", "neutral_below", "negative_below"))
        for ladder in (("-0.000001", "25", "30"), ("-30", "-25", "-15")):
            self.assertFailsClosed(vix_ladder(*ladder), "US_POLICY_VIX_RANGE_INVALID")
        for ladder in (("15", "15", "30"), ("15", "25", "25"), ("25", "15", "30"), ("15", "30", "25")):
            self.assertFailsClosed(vix_ladder(*ladder), "US_POLICY_VIX_ORDER_INVALID")
        # Zero is a legal floor; the ladder only has to be nonnegative and rising.
        self.assertEqual(
            directions(us_packet(vix="0"), edited(("RISK_VOL", "positive_below", "0")))["RISK_VOL"], "NEUTRAL",
        )

    # ------------------------------------------------------------------
    def test_stress_alias_boundary(self):
        """stress_min is negative_below republished; it can never drift from it."""
        self.assertEqual(directions(us_packet(vix="30"), POLICY)["RISK_VOL"], "STRESS")
        self.assertEqual(directions(us_packet(vix="29.999999"), POLICY)["RISK_VOL"], "NEGATIVE")

        moved = edited(("RISK_VOL", "negative_below", "26"), ("RISK_VOL", "stress_min", "26"))
        self.assertEqual(directions(us_packet(vix="26"), moved)["RISK_VOL"], "STRESS")
        self.assertEqual(directions(us_packet(vix="25.999999"), moved)["RISK_VOL"], "NEGATIVE")

        for stress_min in ("31", "29", "26", "30.000001", "0"):
            self.assertFailsClosed(edited(("RISK_VOL", "stress_min", stress_min)), "US_POLICY_STRESS_ALIAS_INVALID")
        # Moving only the executed rung is equally rejected: the two published
        # numbers cannot silently disagree in either direction.
        self.assertFailsClosed(edited(("RISK_VOL", "negative_below", "26")), "US_POLICY_STRESS_ALIAS_INVALID")
        self.assertFailsClosed(dropped("RISK_VOL", "stress_min"), "US_POLICY_VALUE_MISSING:RISK_VOL.stress_min")

    # ------------------------------------------------------------------
    def test_policy_hash_generation_and_calculation_binding(self):
        """The published policy bytes, the generation id, and the US numbers move together."""
        packet_source = us_packet(
            trend_positive=2, advance_fraction="0.50", vix="20", liquidity=("1000", "2000"), leadership_positive=8,
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            (root / "data" / "latest_free_market_data.json").write_text(
                json.dumps(packet_source, ensure_ascii=False), encoding="utf-8",
            )
            for name in ("latest_korea_market_signals.json", "latest_crypto_regime_refresh_status.json"):
                shutil.copy2(ROOT / "data" / name, root / "data" / name)
            policy_file = root / "config" / "paper_regime_reference_policy_v1.json"

            baseline = MODULE.build_reference(root)
            us = {row["market"]: row for row in baseline["markets"]}["US"]
            self.assertEqual(
                {row["axis"]: row["direction"] for row in us["axes"]},
                {
                    "TREND": "NEUTRAL", "BREADTH": "NEUTRAL", "RISK_VOL": "NEUTRAL",
                    "LIQUIDITY": "POSITIVE", "LEADERSHIP": "NEUTRAL",
                },
            )
            self.assertEqual(us["coverage"]["ratio"], "5/5")
            self.assertEqual(us["classification_status"], "PAPER_REFERENCE_CLASSIFIED")
            self.assertEqual(us["runtime_regime"], "UNKNOWN")
            self.assertEqual(us["paper_reference"]["score"], sum(row["score"] for row in us["axes"]))

            # Hash chain: published policy bytes -> generation id -> payload.
            self.assertEqual(baseline["policy"]["sha256"], MODULE.file_sha256(policy_file))
            self.assertEqual(
                baseline["generation_id"],
                MODULE.payload_sha256({"policy_sha256": MODULE.file_sha256(policy_file), "sources": baseline["sources"]}),
            )
            self.assertEqual(MODULE.validate_reference(baseline, root), baseline)
            self.assertEqual(baseline["schema_version"], MODULE.SCHEMA_VERSION)
            self.assertEqual(baseline["contract_version"], "paper_regime_reference_policy/v1")
            self.assertEqual(baseline["mode"], "PAPER_DIAGNOSTIC_NOT_RUNTIME")
            self.assertEqual(baseline["status"], "PARTIAL_REFERENCE_AVAILABLE")
            self.assertEqual([row["market"] for row in baseline["markets"]], ["US", "KR", "CRYPTO"])
            self.assertEqual(baseline["authority"], POLICY["authority"])

            # One published US number changes; nothing else in the tree does.
            changed_policy = edited(("BREADTH", "positive_min", "0.50"))
            policy_file.write_text(json.dumps(changed_policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rebuilt = MODULE.build_reference(root)
            rebuilt_us = {row["market"]: row for row in rebuilt["markets"]}["US"]

            self.assertNotEqual(rebuilt["policy"]["sha256"], baseline["policy"]["sha256"])
            self.assertEqual(rebuilt["policy"]["sha256"], MODULE.file_sha256(policy_file))
            self.assertNotEqual(rebuilt["generation_id"], baseline["generation_id"])
            self.assertNotEqual(rebuilt["payload_sha256"], baseline["payload_sha256"])
            self.assertEqual(
                {row["axis"]: row["direction"] for row in rebuilt_us["axes"]},
                {
                    "TREND": "NEUTRAL", "BREADTH": "POSITIVE", "RISK_VOL": "NEUTRAL",
                    "LIQUIDITY": "POSITIVE", "LEADERSHIP": "NEUTRAL",
                },
            )
            self.assertEqual(rebuilt_us["paper_reference"]["score"], us["paper_reference"]["score"] + 1)
            self.assertEqual(rebuilt_us["paper_reference"]["score"], sum(row["score"] for row in rebuilt_us["axes"]))
            self.assertEqual(rebuilt["markets"][1:], baseline["markets"][1:])
            self.assertEqual(rebuilt["authority"], baseline["authority"])
            self.assertEqual(rebuilt["sources"], baseline["sources"])

            # The previously signed packet is not re-derivable under the new
            # policy: the hash binds the calculation, not just the file.
            with self.assertRaisesRegex(MODULE.PaperRegimeReferenceError, "REFERENCE_REDERIVATION_MISMATCH"):
                MODULE.validate_reference(baseline, root)
            self.assertEqual(MODULE.validate_reference(rebuilt, root), rebuilt)


if __name__ == "__main__":
    unittest.main()
