#!/usr/bin/env python3
"""P1-CR-03 Stablecoin Net Issuance offline transform regression.

No network calls or tracked output writes are made.  Committed PIT captures are
read-only inputs; synthetic edge cases stay in memory or temporary directories.
"""

import ast
import contextlib
from decimal import Decimal
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "stablecoin_net_issuance.py"
EVIDENCE = ROOT / "evidence" / "stablecoin" / "raw"
SPEC = importlib.util.spec_from_file_location("stablecoin_net_issuance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def epoch(day):
    import datetime as dt

    return str(
        int(
            dt.datetime.combine(
                day,
                dt.time(0, 0),
                tzinfo=dt.timezone.utc,
            ).timestamp()
        )
    )


def row(day, native, valued=None):
    native_value = None if native is None else Decimal(str(native))
    valued_value = (
        native_value if valued is None else Decimal(str(valued))
    )
    return {
        "date": epoch(day),
        "totalCirculating": (
            {} if native_value is None else {"peggedUSD": native_value}
        ),
        "totalCirculatingUSD": (
            {} if valued_value is None else {"peggedUSD": valued_value}
        ),
    }


class StablecoinNetIssuanceTest(unittest.TestCase):
    def test_actual_pit_produces_exact_versioned_native_deltas(self):
        result = MODULE.build_transform(EVIDENCE / "2026-08-18")
        latest = result["rows"][-1]

        self.assertEqual(result["transform_version"], MODULE.TRANSFORM_VERSION)
        self.assertEqual(
            result["measurement"],
            "stablecoin_net_issuance_native_usd_peg",
        )
        self.assertEqual(
            latest["observation_date"],
            "2026-08-18",
        )
        self.assertEqual(
            latest["daily_net_issuance_native_usd_peg"],
            "34253944.22",
        )
        self.assertEqual(
            latest["weekly_net_issuance_native_usd_peg"],
            "1047565634.22",
        )
        self.assertEqual(latest["daily_status"], "AVAILABLE")
        self.assertEqual(latest["weekly_status"], "AVAILABLE")
        self.assertEqual(result["lineage"]["vintage_date"], "2026-08-18")
        self.assertEqual(
            result["lineage"]["available_at"],
            "2026-08-18T08:04:48Z",
        )
        self.assertIs(result["lineage"]["point_in_time_required"], True)
        self.assertEqual(
            result["lineage"]["revision_policy"],
            "RECOMPUTE_WITHIN_EACH_PIT_VINTAGE_NO_OVERWRITE",
        )

    def test_usd_valuation_changes_do_not_masquerade_as_issuance(self):
        import datetime as dt

        first = dt.date(2026, 8, 17)
        payload = [
            row(first, "100.25", "90"),
            row(first + dt.timedelta(days=1), "101.50", "5000"),
        ]

        result = MODULE.transform_rows(payload, "2026-08-18")[-1]

        self.assertEqual(
            result["daily_net_issuance_native_usd_peg"],
            "1.25",
        )
        self.assertEqual(
            result["gross_supply_usd_valued_diagnostic"],
            "5000",
        )

    def test_missing_policy_requires_exact_calendar_dates(self):
        import datetime as dt

        first = dt.date(2026, 8, 10)
        payload = [
            row(first, "100"),
            row(first + dt.timedelta(days=2), "110"),
            row(first + dt.timedelta(days=7), "130"),
        ]

        transformed = MODULE.transform_rows(payload, "2026-08-17")
        middle = transformed[1]
        latest = transformed[2]

        self.assertEqual(middle["daily_status"], "MISSING_EXACT_PRIOR")
        self.assertIsNone(middle["daily_net_issuance_native_usd_peg"])
        self.assertEqual(latest["daily_status"], "MISSING_EXACT_PRIOR")
        self.assertEqual(latest["weekly_status"], "AVAILABLE")
        self.assertEqual(
            latest["weekly_net_issuance_native_usd_peg"],
            "30",
        )

    def test_each_pit_vintage_preserves_its_own_revision_lineage(self):
        first = MODULE.build_transform(EVIDENCE / "2026-08-17")
        second = MODULE.build_transform(EVIDENCE / "2026-08-18")
        first_row = next(
            item for item in first["rows"] if item["observation_date"] == "2026-08-17"
        )
        second_row = next(
            item for item in second["rows"] if item["observation_date"] == "2026-08-17"
        )

        self.assertEqual(
            first_row["gross_supply_native_usd_peg"],
            "306233559738.93",
        )
        self.assertEqual(
            second_row["gross_supply_native_usd_peg"],
            "306233602481",
        )
        self.assertNotEqual(
            first["source"]["response_sha256"],
            second["source"]["response_sha256"],
        )
        self.assertEqual(first["source"]["snapshot_date"], "2026-08-17")
        self.assertEqual(second["source"]["snapshot_date"], "2026-08-18")

    def test_malformed_supply_and_time_fail_closed(self):
        import datetime as dt

        day = dt.date(2026, 8, 18)
        cases = (
            [row(day, "-1")],
            [row(day + dt.timedelta(days=1), "1")],
            [row(day, "1"), row(day, "2")],
        )

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(MODULE.TransformError):
                    MODULE.transform_rows(payload, "2026-08-18")

        bad_type = [row(day, "1")]
        bad_type[0]["totalCirculating"]["peggedUSD"] = "1"
        with self.assertRaisesRegex(
            MODULE.TransformError,
            "SUPPLY_VALUE_INVALID",
        ):
            MODULE.transform_rows(bad_type, "2026-08-18")

    def test_missing_snapshot_fails_as_no_vintage_mechanism(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                MODULE.TransformError,
                "NO_VINTAGE_MECHANISM",
            ):
                MODULE.build_transform(Path(tmp) / "2026-08-19")

    def test_cli_writes_only_to_explicit_temporary_output(self):
        tracked_candidate = ROOT / "data" / "stablecoin_net_issuance.json"
        tracked_before = tracked_candidate.exists()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "net_issuance.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = MODULE.run(
                    [
                        "--snapshot-dir",
                        str(EVIDENCE / "2026-08-18"),
                        "--out",
                        str(target),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(target.is_file())
            self.assertIn(
                '"transform_version": "stablecoin_net_issuance/v1"',
                target.read_text(encoding="utf-8"),
            )

        self.assertEqual(tracked_candidate.exists(), tracked_before)

    def test_output_has_no_regime_or_trading_authority(self):
        result = MODULE.build_transform(EVIDENCE / "2026-08-18")

        for key in (
            "regime_score_authorized",
            "threshold_authorized",
            "production_wiring_authorized",
            "trading_action_authorized",
        ):
            self.assertIs(result[key], False)
        self.assertNotIn("signal", result)
        self.assertNotIn("action", result)
        self.assertNotIn("regime", result)

    def test_transform_has_no_network_or_workflow_side_effect_imports(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        self.assertTrue(
            {"requests", "urllib", "http", "socket", "subprocess", "git"}.isdisjoint(
                imported
            )
        )


if __name__ == "__main__":
    unittest.main()
