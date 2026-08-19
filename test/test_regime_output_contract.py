#!/usr/bin/env python3
"""P1-COM-01 pre-score Regime UNKNOWN envelope regression.

All payloads and CLI outputs use temporary files.  No market data, network,
tracked output, score, threshold, or trading action is produced.
"""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "output_contract.py"

SPEC = importlib.util.spec_from_file_location("regime_output_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def defined(
    observation_date,
    available_at,
    transform_version,
    sha_character,
    warnings=None,
):
    return {
        "status": "DEFINED",
        "observation_date": observation_date,
        "available_at": available_at,
        "transform_version": transform_version,
        "evidence": {
            "uri": f"evidence/{transform_version}/{observation_date}.json",
            "sha256": sha_character * 64,
        },
        "warnings": [] if warnings is None else warnings,
    }


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class RegimeOutputContractTest(unittest.TestCase):
    def test_contract_records_candidates_but_runtime_only_authorizes_unknown(self):
        self.assertEqual(CONTRACT["contract_version"], "regime_output/v1")
        self.assertEqual(CONTRACT["contract_mode"], "PRE_SCORE_UNKNOWN_ONLY")
        self.assertEqual(
            CONTRACT["regime_vocabulary"],
            ["RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", "UNKNOWN"],
        )
        self.assertEqual(CONTRACT["runtime_authorized_regimes"], ["UNKNOWN"])
        self.assertEqual(CONTRACT["runtime_authorized_directions"], ["UNKNOWN"])
        self.assertEqual(
            CONTRACT["required_axes"],
            ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"],
        )

    def test_no_factors_is_explicit_unknown_not_neutral(self):
        result = MODULE.build_unknown_output(
            "CRYPTO",
            "2026-08-20T01:00:00Z",
        )

        self.assertEqual(result["regime"], "UNKNOWN")
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["coverage"]["ratio"], "0/5")
        self.assertEqual(result["coverage"]["defined_axes"], [])
        self.assertEqual(
            result["coverage"]["missing_axes"],
            CONTRACT["required_axes"],
        )
        self.assertIsNone(result["evidence_as_of"]["oldest_observation_date"])
        self.assertIsNone(result["available_as_of"])
        self.assertIn("AXIS_COVERAGE_INCOMPLETE", result["warnings"])
        self.assertNotIn("NEUTRAL", json.dumps(result))

    def test_partial_factors_keep_per_axis_time_and_top_level_bounds(self):
        result = MODULE.build_unknown_output(
            "CRYPTO",
            "2026-08-20T01:00:00Z",
            {
                "TREND": defined(
                    "2026-08-18",
                    "2026-08-19T00:20:00Z",
                    "btc_trend/v1",
                    "a",
                ),
                "RISK_VOL": defined(
                    "2026-08-19",
                    "2026-08-20T00:20:00Z",
                    "btc_risk/v1",
                    "b",
                    ["STRESS_THRESHOLDS_UNCALIBRATED"],
                ),
            },
        )

        self.assertEqual(
            result["coverage"]["defined_axes"],
            ["TREND", "RISK_VOL"],
        )
        self.assertEqual(result["coverage"]["ratio"], "2/5")
        self.assertEqual(
            result["evidence_as_of"]["oldest_observation_date"],
            "2026-08-18",
        )
        self.assertEqual(
            result["evidence_as_of"]["per_axis"]["RISK_VOL"],
            "2026-08-19",
        )
        self.assertEqual(result["available_as_of"], "2026-08-20T00:20:00Z")
        self.assertEqual(
            result["factor_results"]["RISK_VOL"]["age_seconds"],
            2400,
        )
        self.assertIn("STRESS_THRESHOLDS_UNCALIBRATED", result["warnings"])
        self.assertEqual(result["regime"], "UNKNOWN")

    def test_neutral_and_scored_direction_cannot_masquerade_as_unknown(self):
        result = MODULE.build_unknown_output("US", "2026-08-20T14:00:00Z")
        neutral = copy.deepcopy(result)
        neutral["regime"] = "NEUTRAL"
        improving = copy.deepcopy(result)
        improving["direction"] = "IMPROVING"
        confident = copy.deepcopy(result)
        confident["confidence"] = "0.8"

        cases = (
            (neutral, "REGIME_NOT_AUTHORIZED"),
            (improving, "DIRECTION_NOT_AUTHORIZED"),
            (confident, "CONFIDENCE_NOT_AUTHORIZED"),
        )
        for payload, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.OutputContractError,
                error,
            ):
                MODULE.validate_output(payload, CONTRACT)

    def test_future_evidence_uses_market_local_date_and_utc_availability(self):
        us_future_date = {
            "TREND": defined(
                "2026-08-20",
                "2026-08-20T00:30:00Z",
                "btc_trend/v1",
                "a",
            )
        }
        future_available = {
            "TREND": defined(
                "2026-08-19",
                "2026-08-20T01:00:01Z",
                "btc_trend/v1",
                "a",
            )
        }

        with self.assertRaisesRegex(
            MODULE.OutputContractError,
            "OBSERVATION_FROM_FUTURE",
        ):
            MODULE.build_unknown_output(
                "US",
                "2026-08-20T01:00:00Z",
                us_future_date,
            )
        with self.assertRaisesRegex(
            MODULE.OutputContractError,
            "AVAILABILITY_FROM_FUTURE",
        ):
            MODULE.build_unknown_output(
                "CRYPTO",
                "2026-08-20T01:00:00Z",
                future_available,
            )

    def test_defined_factor_requires_version_evidence_sha_and_known_axis(self):
        bad_version = defined(
            "2026-08-19",
            "2026-08-20T00:20:00Z",
            "unversioned",
            "a",
        )
        bad_sha = defined(
            "2026-08-19",
            "2026-08-20T00:20:00Z",
            "btc_trend/v1",
            "a",
        )
        bad_sha["evidence"]["sha256"] = "short"

        cases = (
            ({"TREND": bad_version}, "TRANSFORM_VERSION_INVALID"),
            ({"TREND": bad_sha}, "EVIDENCE_INVALID"),
            ({"MOMENTUM": bad_sha}, "AXIS_UNKNOWN"),
        )
        for factors, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.OutputContractError,
                error,
            ):
                MODULE.build_unknown_output(
                    "CRYPTO",
                    "2026-08-20T01:00:00Z",
                    factors,
                )

    def test_undefined_axis_cannot_carry_hidden_evidence(self):
        result = MODULE.build_unknown_output("KR", "2026-08-20T01:00:00Z")
        result["factor_results"]["BREADTH"]["available_at"] = (
            "2026-08-20T00:00:00Z"
        )

        with self.assertRaisesRegex(
            MODULE.OutputContractError,
            "UNDEFINED_FACTOR_HAS_EVIDENCE",
        ):
            MODULE.validate_output(result, CONTRACT)

        with self.assertRaisesRegex(
            MODULE.OutputContractError,
            "UNDEFINED_FACTOR_WARNING_REQUIRED",
        ):
            MODULE.build_unknown_output(
                "KR",
                "2026-08-20T01:00:00Z",
                {"BREADTH": {"status": "UNDEFINED", "warnings": []}},
            )

    def test_derived_coverage_age_warnings_and_authority_are_tamper_evident(self):
        result = MODULE.build_unknown_output(
            "CRYPTO",
            "2026-08-20T01:00:00Z",
            {
                "TREND": defined(
                    "2026-08-19",
                    "2026-08-20T00:20:00Z",
                    "btc_trend/v1",
                    "a",
                )
            },
        )
        payloads = []
        coverage = copy.deepcopy(result)
        coverage["coverage"]["defined_count"] = 5
        payloads.append((coverage, "DERIVED_FIELD_MISMATCH"))
        age = copy.deepcopy(result)
        age["factor_results"]["TREND"]["age_seconds"] = 0
        payloads.append((age, "FACTOR_AGE_INVALID"))
        warnings = copy.deepcopy(result)
        warnings["warnings"] = []
        payloads.append((warnings, "DERIVED_FIELD_MISMATCH"))
        authority = copy.deepcopy(result)
        authority["authority"]["regime_score_authorized"] = True
        payloads.append((authority, "AUTHORITY_BOUNDARY_INVALID"))

        for payload, error in payloads:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.OutputContractError,
                error,
            ):
                MODULE.validate_output(payload, CONTRACT)

    def test_output_is_deterministic_ordered_and_contains_no_float(self):
        factors = {
            "LIQUIDITY": defined(
                "2026-08-18",
                "2026-08-19T06:20:00Z",
                "stablecoin_net_issuance/v1",
                "c",
            ),
            "TREND": defined(
                "2026-08-19",
                "2026-08-20T00:20:00Z",
                "btc_trend/v1",
                "a",
            ),
        }
        first = MODULE.build_unknown_output(
            "CRYPTO",
            "2026-08-20T01:00:00Z",
            factors,
        )
        second = MODULE.build_unknown_output(
            "CRYPTO",
            "2026-08-20T01:00:00Z",
            dict(reversed(list(factors.items()))),
        )

        self.assertEqual(first, second)
        self.assertEqual(
            list(first["factor_results"]),
            CONTRACT["required_axes"],
        )
        self.assertFalse(has_float(first))
        reordered = copy.deepcopy(first)
        reordered["factor_results"] = dict(
            reversed(list(reordered["factor_results"].items()))
        )
        self.assertEqual(
            MODULE.validate_output(reordered, CONTRACT),
            reordered,
        )

    def test_cli_build_and_validate_only_write_requested_temp_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            factors_path = root / "factors.json"
            output_path = root / "output" / "regime.json"
            factors_path.write_text(
                json.dumps(
                    {
                        "TREND": defined(
                            "2026-08-19",
                            "2026-08-20T00:20:00Z",
                            "btc_trend/v1",
                            "a",
                        )
                    }
                ),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                build_exit = MODULE.main(
                    [
                        "build-unknown",
                        "--market",
                        "CRYPTO",
                        "--generated-at",
                        "2026-08-20T01:00:00Z",
                        "--factors",
                        str(factors_path),
                        "--out",
                        str(output_path),
                    ]
                )
                validate_exit = MODULE.main(["validate", str(output_path)])

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(build_exit, 0)
            self.assertEqual(validate_exit, 0)
            self.assertEqual(payload["regime"], "UNKNOWN")
            self.assertFalse(list(output_path.parent.glob(".*.tmp.*")))


if __name__ == "__main__":
    unittest.main()
