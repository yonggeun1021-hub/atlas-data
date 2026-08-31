#!/usr/bin/env python3
"""PAPER 12-4 fail-closed receipt pipeline regression."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "paper_12_4_three_market_regime"
FIXTURE = PACKAGE / "fixtures" / "current_blocked"

from paper_12_4_three_market_regime import receipt_pipeline as PIPELINE  # noqa: E402
from paper_12_4_three_market_regime import cli as CLI  # noqa: E402


EVALUATION_TIME = "2026-08-31T01:00:00Z"


def envelope_path(market: str) -> Path:
    return FIXTURE / f"{market.lower()}_envelope.json"


def fixture_paths() -> list[Path]:
    return [envelope_path("krx"), envelope_path("us"), envelope_path("crypto")]


def load_envelope(market: str) -> dict:
    return json.loads(envelope_path(market).read_text(encoding="utf-8"))


def rehash(value: dict, field: str) -> dict:
    changed = copy.deepcopy(value)
    changed.pop(field, None)
    changed[field] = PIPELINE.canonical_sha256(changed)
    return changed


class ReceiptPipelineTests(unittest.TestCase):
    def test_current_canonical_blockers_are_preserved_without_paper_authority(self):
        bundle = PIPELINE.build_bundle(fixture_paths(), EVALUATION_TIME)
        receipts = {row["market"]: row for row in bundle["market_receipts"]}

        krx = receipts["KRX"]
        self.assertEqual(krx["receipt_status"], "WAIT")
        self.assertEqual(krx["regime"], "UNKNOWN")
        self.assertEqual(krx["paper_disposition"], "HOLD")
        self.assertEqual(krx["input_readiness"]["AXES"]["coverage"]["ratio"], "5/5")
        self.assertEqual(krx["blocked_reasons"], ["REGIME_SCORING_POLICY_UNRATIFIED"])
        self.assertEqual(krx["rotation_input"]["declared_state"], "PENDING")
        self.assertIn("KRX_ROTATION_PENDING", krx["rotation_input"]["blocked_reasons"])

        us = receipts["US"]
        self.assertEqual(us["input_readiness"]["AXES"]["coverage"]["ratio"], "0/5")
        self.assertIn("LEADERSHIP_POLICY_UNRATIFIED", us["blocked_reasons"])
        self.assertIn("AXES_COVERAGE_INCOMPLETE", us["blocked_reasons"])

        crypto = receipts["CRYPTO"]
        self.assertEqual(crypto["input_readiness"]["AXES"]["coverage"]["ratio"], "0/5")
        self.assertIn("LEADERSHIP_COVERAGE_POLICY_UNRATIFIED", crypto["blocked_reasons"])
        self.assertIn("SECTOR_FLOW_COVERAGE_POLICY_UNRATIFIED", crypto["blocked_reasons"])

        header = bundle["three_market_header"]
        self.assertEqual(header["header_status"], "PENDING")
        self.assertEqual(header["rotation_discovery"]["status"], "DEGRADED")
        self.assertEqual(header["summary"]["wait_count"], 3)
        self.assertIsNone(header["summary"]["market_ranking"])
        self.assertIsNone(header["summary"]["paper_action"])
        for authority in [bundle["authority"], header["authority"]] + [
            row["authority"] for row in bundle["market_receipts"]
        ]:
            self.assertTrue(all(value is False for value in authority.values()))

    def test_missing_market_does_not_delete_independent_market_receipts(self):
        bundle = PIPELINE.build_bundle(fixture_paths()[:2], EVALUATION_TIME)
        self.assertEqual(
            [row["market"] for row in bundle["market_receipts"]], ["KRX", "US"]
        )
        rows = {row["market"]: row for row in bundle["three_market_header"]["markets"]}
        self.assertEqual(rows["KRX"]["blocked_reasons"], ["REGIME_SCORING_POLICY_UNRATIFIED"])
        self.assertIn("LEADERSHIP_POLICY_UNRATIFIED", rows["US"]["blocked_reasons"])
        self.assertEqual(rows["CRYPTO"]["receipt_status"], "WAIT")
        self.assertEqual(rows["CRYPTO"]["regime"], "UNKNOWN")
        self.assertEqual(rows["CRYPTO"]["paper_disposition"], "HOLD")
        self.assertEqual(rows["CRYPTO"]["blocked_reasons"], ["MARKET_INPUT_ENVELOPE_MISSING"])
        self.assertEqual(bundle["three_market_header"]["header_status"], "PENDING")

    def test_unratified_policy_and_unratified_coverage_fail_closed(self):
        us = PIPELINE.build_market_receipt(
            load_envelope("us"), envelope_path("us"), EVALUATION_TIME
        )
        crypto = PIPELINE.build_market_receipt(
            load_envelope("crypto"), envelope_path("crypto"), EVALUATION_TIME
        )
        for receipt in (us, crypto):
            self.assertEqual(receipt["receipt_status"], "WAIT")
            self.assertEqual(receipt["regime"], "UNKNOWN")
            self.assertEqual(receipt["paper_disposition"], "HOLD")
            self.assertFalse(receipt["authority"]["paper_authorized"])
        self.assertIn("LEADERSHIP_POLICY_UNRATIFIED", us["blocked_reasons"])
        self.assertIn("LEADERSHIP_COVERAGE_POLICY_UNRATIFIED", crypto["blocked_reasons"])

    def test_envelope_cannot_lie_about_exact_policy_or_coverage_status(self):
        cases = [
            (
                "us",
                "policy_status",
                "RATIFIED",
                "POLICY_STATUS_MISMATCH:LEADERSHIP",
            ),
            (
                "crypto",
                "coverage_policy_status",
                "RATIFIED",
                "COVERAGE_POLICY_STATUS_MISMATCH:LEADERSHIP",
            ),
        ]
        for market, field, value, error in cases:
            changed = load_envelope(market)
            changed["inputs"]["LEADERSHIP"][field] = value
            with self.subTest(error=error), self.assertRaisesRegex(
                PIPELINE.ReceiptPipelineError, error
            ):
                PIPELINE.build_market_receipt(
                    changed, envelope_path(market), EVALUATION_TIME
                )

    def test_completed_bar_source_time_and_ttl_are_independent_wait_reasons(self):
        base = load_envelope("krx")
        cases = [
            ("completed_bar", False, EVALUATION_TIME, "LEADERSHIP_BAR_INCOMPLETE"),
            ("source_time_utc", "2026-08-31T02:00:00Z", EVALUATION_TIME, "LEADERSHIP_SOURCE_FROM_FUTURE"),
            ("ttl_seconds", 60, "2026-08-31T01:00:00Z", "LEADERSHIP_TTL_EXPIRED"),
        ]
        for field, value, evaluated_at, reason in cases:
            changed = copy.deepcopy(base)
            changed["inputs"]["LEADERSHIP"][field] = value
            with self.subTest(reason=reason):
                receipt = PIPELINE.build_market_receipt(
                    changed, envelope_path("krx"), evaluated_at
                )
                self.assertIn(reason, receipt["blocked_reasons"])
                self.assertEqual(receipt["receipt_status"], "WAIT")
                self.assertEqual(receipt["regime"], "UNKNOWN")

    def test_exact_source_hash_mismatch_rejects_receipt_build(self):
        changed = load_envelope("krx")
        changed["inputs"]["AXES"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(PIPELINE.ReceiptPipelineError, "SOURCE_SHA_MISMATCH:AXES"):
            PIPELINE.build_market_receipt(changed, envelope_path("krx"), EVALUATION_TIME)

    def test_nested_canonical_snapshot_hash_mismatch_rejects_build(self):
        changed = load_envelope("krx")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            canonical = root / "canonical.json"
            canonical.write_text("{}\n", encoding="utf-8")
            source = root / "source.json"
            source.write_text(
                json.dumps(
                    {
                        "evidence_class": "TEST_FIXTURE_ONLY_NON_AUTHORITATIVE",
                        "canonical_snapshot": {
                            "path": "canonical.json",
                            "sha256": "0" * 64,
                        },
                    }
                ),
                encoding="utf-8",
            )
            changed["inputs"]["LEADERSHIP"]["source_path"] = "source.json"
            changed["inputs"]["LEADERSHIP"]["source_sha256"] = PIPELINE.file_sha256(source)
            with self.assertRaisesRegex(
                PIPELINE.ReceiptPipelineError,
                "CANONICAL_SNAPSHOT_SHA_MISMATCH:LEADERSHIP",
            ):
                PIPELINE.build_market_receipt(
                    changed,
                    envelope_path("krx"),
                    EVALUATION_TIME,
                    source_root=root,
                )

    def test_lineage_receipt_header_bundle_and_transition_hashes_are_exact(self):
        bundle = PIPELINE.build_bundle(fixture_paths(), EVALUATION_TIME)
        PIPELINE.validate_bundle(bundle)
        for receipt in bundle["market_receipts"]:
            self.assertEqual(
                receipt["source_lineage_sha256"],
                PIPELINE.canonical_sha256(receipt["source_lineage"]),
            )
            unsigned = copy.deepcopy(receipt)
            digest = unsigned.pop("receipt_sha256")
            self.assertEqual(digest, PIPELINE.canonical_sha256(unsigned))
        for entry in bundle["transition_ledger"]:
            unsigned = copy.deepcopy(entry)
            digest = unsigned.pop("entry_sha256")
            self.assertEqual(digest, PIPELINE.canonical_sha256(unsigned))

    def test_transition_ledger_carries_prior_market_state_without_authority(self):
        previous = PIPELINE.build_bundle(fixture_paths(), EVALUATION_TIME)
        changed = load_envelope("krx")
        changed["regime_scoring_policy_status"] = "RATIFIED"
        changed["declared_rotation_state"] = "READY"
        with tempfile.TemporaryDirectory() as raw:
            next_envelope = Path(raw) / "krx.json"
            next_envelope.write_text(json.dumps(changed), encoding="utf-8")
            current = PIPELINE.build_bundle(
                [next_envelope],
                "2026-08-31T01:01:00Z",
                previous_bundle=previous,
            )
        krx = current["market_receipts"][0]
        self.assertEqual(krx["receipt_status"], "INPUTS_READY")
        self.assertEqual(krx["regime"], "UNKNOWN")
        self.assertEqual(krx["paper_disposition"], "HOLD")
        self.assertFalse(krx["authority"]["paper_authorized"])
        transition = current["transition_ledger"][0]
        self.assertEqual(transition["from_status"], "WAIT")
        self.assertEqual(transition["to_status"], "INPUTS_READY")
        self.assertEqual(
            transition["artifact_sha256"], krx["receipt_sha256"]
        )

    def test_self_rehashed_projection_or_transition_tamper_fails_derivation(self):
        original = PIPELINE.build_bundle(fixture_paths(), EVALUATION_TIME)

        projection = copy.deepcopy(original)
        projection["three_market_header"]["markets"][0]["blocked_reasons"] = []
        projection["three_market_header"] = rehash(
            projection["three_market_header"], "header_sha256"
        )
        projection = rehash(projection, "bundle_sha256")
        with self.assertRaisesRegex(PIPELINE.ReceiptPipelineError, "HEADER_DERIVATION_MISMATCH"):
            PIPELINE.validate_bundle(projection)

        transition = copy.deepcopy(original)
        transition["transition_ledger"][0]["to_status"] = "INPUTS_READY"
        transition["transition_ledger"][0] = rehash(
            transition["transition_ledger"][0], "entry_sha256"
        )
        transition = rehash(transition, "bundle_sha256")
        with self.assertRaisesRegex(PIPELINE.ReceiptPipelineError, "TRANSITION_DERIVATION_MISMATCH"):
            PIPELINE.validate_bundle(transition)

    def test_fixture_is_explicitly_non_authoritative(self):
        for source_path in sorted((FIXTURE / "sources").glob("*.json")):
            value = json.loads(source_path.read_text(encoding="utf-8"))
            self.assertEqual(
                value["evidence_class"], "TEST_FIXTURE_ONLY_NON_AUTHORITATIVE"
            )

    def test_cli_is_offline_and_round_trips_bundle(self):
        imported = set()
        for source_path in (PACKAGE / "cli.py", PACKAGE / "receipt_pipeline.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
        for prohibited in (
            "requests",
            "urllib",
            "socket",
            "http",
            "subprocess",
            "git",
            "ccxt",
        ):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "bundle.json"
            args = ["build"]
            for path in fixture_paths():
                args.extend(["--envelope", str(path)])
            args.extend(
                [
                    "--evaluation-time-utc",
                    EVALUATION_TIME,
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(CLI.main(args), 0)
            self.assertTrue(output.exists())
            self.assertEqual(CLI.main(["verify", "--bundle", str(output)]), 0)


if __name__ == "__main__":
    unittest.main()
