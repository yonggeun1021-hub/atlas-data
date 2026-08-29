#!/usr/bin/env python3
"""P8-16 Crypto funnel briefing regression."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "briefing" / "crypto_funnel_briefing.py"
SPEC = importlib.util.spec_from_file_location("crypto_funnel_briefing", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DECISION = MODULE.DECISION
CONTRACT = MODULE.load_contract()
GENERATED_AT = "2026-08-29T06:30:05Z"
SOURCE_COMMIT = "a" * 40


def decision_packet():
    return DECISION.build_snapshot(
        generated_at=GENERATED_AT,
        source_commit=SOURCE_COMMIT,
        universe_entry=None,
        market_evidence_entry=None,
        realtime_entry=None,
        started_at="2026-08-29T06:30:01Z",
    )


def write_source(directory: Path, packet=None) -> Path:
    path = directory / "decision.json"
    path.write_text(json.dumps(packet or decision_packet(), sort_keys=True), encoding="utf-8")
    return path


def build_from_path(path: Path, packet=None):
    packet = packet or json.loads(path.read_text(encoding="utf-8"))
    return MODULE.build_briefing(
        packet,
        source_path=str(path),
        source_file_sha256=MODULE._file_sha256(path),
        contract=CONTRACT,
        allow_external_sources=True,
    )


def rehash(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("packet_sha256", None)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class CryptoFunnelBriefingTests(unittest.TestCase):
    def test_contract_is_read_only_and_all_money_authority_stays_false(self):
        self.assertTrue(CONTRACT["authority"]["briefing_read_model_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "briefing_read_model_only":
                self.assertIs(value, False, key)
        self.assertEqual(CONTRACT["missing_position_policy"], "UNKNOWN_NULL_NOT_ZERO")

    def test_exact_decision_generation_projects_axes_counts_freshness_and_kst(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source(Path(tmp))
            packet = build_from_path(path)
        self.assertEqual(packet["source_ref"]["generation_id"], packet["source_packet"]["generation_id"])
        self.assertEqual(
            [row["axis"] for row in packet["regime"]["axes"]],
            CONTRACT["axis_order"],
        )
        self.assertEqual(packet["regime"]["defined_axis_count"], 0)
        self.assertEqual(packet["regime"]["aggregate"], "UNKNOWN")
        self.assertFalse(packet["regime"]["aggregate_authorized"])
        self.assertEqual(packet["as_of"]["captured_at_utc"], GENERATED_AT)
        self.assertEqual(packet["as_of"]["captured_at_kst"], "2026-08-29T15:30:05+09:00")
        self.assertEqual(packet["freshness"]["overall"], "MISSING")

    def test_missing_private_position_is_unknown_null_never_false_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = build_from_path(write_source(Path(tmp)))
        positions = next(row for row in packet["funnel"]["stages"] if row["stage"] == "PAPER_POSITION")
        self.assertIsNone(positions["count"])
        self.assertEqual(positions["reason"], "P10_P7_REDACTED_POSITION_SUMMARY_NOT_WIRED")
        self.assertIn(positions["reason"], packet["reasons"])
        self.assertIn("PAPER_POSITION: `UNKNOWN`", packet["rendered_markdown"])

    def test_json_and_markdown_share_exact_counts_reasons_and_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = build_from_path(write_source(Path(tmp)))
        rendered = packet["rendered_markdown"]
        for stage in packet["funnel"]["stages"]:
            count = "UNKNOWN" if stage["count"] is None else str(stage["count"])
            self.assertIn(f"{stage['stage']}: `{count}`", rendered)
        for reason in packet["reasons"]:
            self.assertIn(f"`{reason}`", rendered)
        self.assertIn("PAPER 주문 권한: `false`", rendered)
        self.assertIn("실제 주문·출금·REAL·Production·Trading 권한: `false`", rendered)

    def test_candidate_projection_preserves_p5_reason_and_never_derives_a_new_one(self):
        source = decision_packet()
        row = {
            "market": "KRW-BTC",
            "canonical_asset_id": "CRYPTO:BTC",
            "p3_12_state": "TRADEABLE_UNIVERSE",
            "state": "WAIT",
            "reason": "REGIME_UNKNOWN",
            "freshness_capped": False,
            "freshness_cap_reason": None,
            "p5_08": {
                "promotion_state": "FOCUSED_REVIEW",
                "promotion_reason": "ALL_CRITERIA_PASS",
                "criteria": {
                    "TREND": {"status": "PASS", "reason": "SOURCE_TREND"},
                    "RELATIVE_STRENGTH": {"status": "PASS", "reason": "SOURCE_RS"},
                    "VOLUME_LIQUIDITY": {"status": "PASS", "reason": "SOURCE_LIQUIDITY"},
                },
            },
            "p5_09": {
                "eligibility_state": "WAIT",
                "eligibility_reason": "REGIME_UNKNOWN",
                "criteria": {
                    "BREAKOUT_OR_PULLBACK": {"status": "UNKNOWN", "reason": "SOURCE_TRIGGER"},
                },
                "order_draft": None,
            },
            "authority": copy.deepcopy(source["authority"]),
        }
        source["candidates"] = [row]
        source["funnel_counts"]["focused_review_count"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source(Path(tmp), source)
            with mock.patch.object(DECISION, "validate_output", return_value=copy.deepcopy(source)):
                packet = build_from_path(path, source)
        candidate = packet["candidates"][0]
        self.assertEqual(candidate["reason"], "REGIME_UNKNOWN")
        self.assertEqual(candidate["trend"]["reason"], "SOURCE_TREND")
        self.assertEqual(candidate["relative_strength"]["reason"], "SOURCE_RS")
        self.assertEqual(candidate["liquidity"]["reason"], "SOURCE_LIQUIDITY")
        self.assertEqual(candidate["trigger"]["reason"], "SOURCE_TRIGGER")
        self.assertIsNone(candidate["order_draft"])

    def test_source_file_tamper_and_embedded_source_substitution_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source(Path(tmp))
            packet = build_from_path(path)
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.CryptoFunnelBriefingError, "SOURCE_FILE_SHA256_MISMATCH"):
                MODULE.validate_briefing(packet, CONTRACT, allow_external_sources=True)

    def test_self_rehashed_count_or_reason_tamper_fails_full_rederivation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source(Path(tmp))
            packet = build_from_path(path)
            count = copy.deepcopy(packet)
            count["funnel"]["stages"][0]["count"] = 999
            with self.assertRaisesRegex(MODULE.CryptoFunnelBriefingError, "OUTPUT_DERIVATION_MISMATCH"):
                MODULE.validate_briefing(rehash(count), CONTRACT, allow_external_sources=True)
            reason = copy.deepcopy(packet)
            reason["reasons"] = ["BUY_NOW"]
            reason["rendered_markdown"] = MODULE._render_markdown(reason)
            with self.assertRaisesRegex(MODULE.CryptoFunnelBriefingError, "OUTPUT_DERIVATION_MISMATCH"):
                MODULE.validate_briefing(rehash(reason), CONTRACT, allow_external_sources=True)

    def test_legacy_decision_without_explicit_time_basis_is_rejected(self):
        source = decision_packet()
        for key in (
            "captured_at_utc", "captured_at_kst", "operational_date_kst",
            "path_time_basis", "scheduled_for", "started_at", "completed_at",
        ):
            source.pop(key)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source(Path(tmp), source)
            with self.assertRaisesRegex(MODULE.CryptoFunnelBriefingError, "SOURCE_DECISION_INVALID"):
                build_from_path(path, source)

    def test_populate_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".p816-source-") as source_tmp:
            with tempfile.TemporaryDirectory() as output_tmp:
                source_path = write_source(Path(source_tmp))
                first = MODULE.populate(source_path, Path(output_tmp))
                second = MODULE.populate(source_path, Path(output_tmp))
        self.assertEqual(first["outcome"], "populated")
        self.assertEqual(second["outcome"], "verified_existing")
        self.assertEqual(first["record"]["packet_sha256"], second["record"]["packet_sha256"])

    def test_module_has_no_network_order_or_private_client(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "websockets", "http", "subprocess"):
            self.assertNotIn(prohibited, imports)
        source = SOURCE.read_text(encoding="utf-8")
        for token in ("api.upbit.com", "/v1/orders", "myOrder", "myAsset"):
            self.assertNotIn(token, source)

    def test_scheduled_workflow_wires_exact_decision_output_without_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "upbit-realtime-capture.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python3 test/test_crypto_funnel_briefing.py", workflow)
        self.assertIn("if: steps.crypto_paper_decision.outcome == 'success'", workflow)
        self.assertIn(
            "DECISION_PATH: ${{ steps.crypto_paper_decision.outputs.path }}", workflow
        )
        self.assertIn(
            "python3 briefing/crypto_funnel_briefing.py --decision-packet \"$DECISION_PATH\"",
            workflow,
        )
        self.assertIn("git add evidence/crypto_funnel_briefing || true", workflow)
        self.assertNotIn("--allow-realtime-fallback", workflow)


if __name__ == "__main__":
    unittest.main()
