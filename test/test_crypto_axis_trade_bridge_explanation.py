#!/usr/bin/env python3
"""P5-10 five-axis per-symbol entry/exit explanation read-model regression."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "decision" / "crypto_axis_trade_bridge_explanation.py"
SPEC = importlib.util.spec_from_file_location(
    "crypto_axis_trade_bridge_explanation", MODULE_PATH,
)
EXPLAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(EXPLAIN)

BRIDGE = EXPLAIN.BRIDGE
CONTRACT = EXPLAIN.load_contract()

# The natural committed generation named by the adopted P5-10 contract.
REAL_PACKET_PATH = (
    ROOT / "evidence" / "crypto_axis_trade_bridge" / "2026-09-06" / "2349" /
    "3a35da983bae7f2a6d66e802f279a9264162a43ef99727a8bd2e1c5f574951ae" / "packet.json"
)

# Producer files this task must leave byte-identical, pinned by the adopted
# contract.  A mismatch means the producer moved, not that the read model broke.
PRODUCER_PINS = {
    "decision/crypto_axis_trade_bridge.py":
        "539be870b66475cba52d2da7ded7afb74d940d75142263f206dd7c548d27a1c3",
    "config/crypto_axis_trade_bridge_contract.json":
        "7e3bf99d69b6b19f96ddbff70fb2fd7702f9db112d9f2da30b1e2b2e0ca9d8df",
    "docs/crypto_axis_trade_bridge_contract.md":
        "64cf3dedd75138fe91862ebe969a7d442cec8edf5d19ec84a2fb0516f2af05de",
    "test/test_crypto_axis_trade_bridge.py":
        "1d18f4aa9a3f87748878f432dc13f023ba7c1e765b3d1e9599a2b80f343c5d66",
    "decision/crypto_paper_decision_snapshot.py":
        "41f8e49f51a2a68246fb3c655914e25580f82bc9c3a6462ee31b54ef95fae373",
}

_VALIDATED: list = []


def committed_bridge_packet() -> dict:
    return json.loads(REAL_PACKET_PATH.read_text(encoding="utf-8"))


def validated_bridge_packet() -> dict:
    """Require the nominated committed generation and complete source lineage."""
    if not _VALIDATED:
        _VALIDATED.append(BRIDGE.validate_output(committed_bridge_packet()))
    return copy.deepcopy(_VALIDATED[0])


def real_explanation() -> dict:
    return EXPLAIN.assemble(validated_bridge_packet(), CONTRACT)


def synthetic_bridge_packet(missing_axes=("TREND", "RISK_VOL", "LIQUIDITY", "BREADTH", "LEADERSHIP")):
    """A structurally exact, obviously synthetic bridge packet.

    These fixtures exercise only the private unsigned relabeling helper.
    Public explanation APIs must reject them for invalid source provenance.
    """
    bridge_contract = BRIDGE.load_contract()
    required = bridge_contract["required_axes"]
    ordered_missing = [axis for axis in required if axis in missing_axes]
    axes = {}
    for axis in required:
        undefined = axis in ordered_missing
        axes[axis] = {
            "status": "UNDEFINED" if undefined else "DEFINED",
            "observation_date": None if undefined else "2026-09-06",
            "available_at": None if undefined else "2026-09-06T23:00:00Z",
            "warnings": ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"] if undefined else [],
            "bindings": copy.deepcopy(bridge_contract["axis_bindings"][axis]),
        }
    exit_policy = bridge_contract["exit_policy"]

    def entry_reasons(upstream_state):
        reasons = [f"UPSTREAM_STATE:{upstream_state}"]
        if ordered_missing:
            reasons.append("OFFICIAL_AXES_INCOMPLETE:" + ",".join(ordered_missing))
        reasons.append("AGGREGATE_POLICY_UNRATIFIED")
        return reasons

    def symbol(market, asset, upstream_state, entry_state):
        return {
            "market": market,
            "canonical_asset_id": asset,
            "upstream_state": upstream_state,
            "upstream_reason": "SYNTHETIC_FIXTURE_REASON",
            "entry": {
                "state": entry_state,
                "reasons": entry_reasons(upstream_state),
                "aggregate_regime": "UNKNOWN",
                "order_draft": None,
                "automatic_entry_generated": False,
            },
            "exit": {
                "state": exit_policy["pre_fill_state"],
                "regime_signal": "UNKNOWN",
                "trend_signal": "UNKNOWN",
                "post_fill_manager_contract_version":
                    exit_policy["post_fill_manager_contract_version"],
                "priority_categories": copy.deepcopy(exit_policy["priority_categories"]),
                "hard_exit_priority_preserved": True,
                "automatic_exit_generated": False,
                "reasons": [
                    "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET",
                    "AGGREGATE_POLICY_UNRATIFIED",
                    "P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED",
                ],
            },
        }

    symbol_rules = [
        symbol("KRW-BTC", "BTC", "WATCH", "WAIT"),
        symbol("KRW-ETH", "ETH", "BLOCKED", "BLOCKED"),
    ]
    return {
        "schema_version": BRIDGE.OUTPUT_SCHEMA_VERSION,
        "contract_version": bridge_contract["contract_version"],
        "mode": bridge_contract["mode"],
        "generated_at": "2026-09-06T23:49:32Z",
        "operational_date_kst": "2026-09-07",
        "source_generation_id": "f" * 64,
        "source_decision_sha256": "e" * 64,
        "five_axis": {
            "required_count": len(required),
            "defined_count": len(required) - len(ordered_missing),
            "all_defined": not ordered_missing,
            "missing_axes": ordered_missing,
            "axes": axes,
        },
        "aggregate_policy": {
            "status": bridge_contract["aggregate_policy_status"],
            "regime": "UNKNOWN",
            "authorized_regimes": copy.deepcopy(
                bridge_contract["aggregate_regimes_currently_authorized"]
            ),
        },
        "symbol_rules": symbol_rules,
        "summary": {
            "symbol_count": 2,
            "entry_wait_count": 1,
            "entry_blocked_count": 1,
            "automatic_entry_count": 0,
            "automatic_exit_count": 0,
        },
        "authority": copy.deepcopy(bridge_contract["authority"]),
        "source": {
            "decision_snapshot": {"capture_date": "2026-09-06", "capture_hhmm": "2349"},
            "contract": copy.deepcopy(bridge_contract),
        },
        "packet_sha256": "0" * 64,
    }


def stage(explanation: dict, stage_id: str) -> dict:
    return next(row for row in explanation["stages"] if row["stage_id"] == stage_id)


class ProducerPinTests(unittest.TestCase):
    def test_producer_sources_and_tests_remain_hash_preserved(self):
        for relative, expected in sorted(PRODUCER_PINS.items()):
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"PRODUCER_FILE_CHANGED:{relative}")


class ContractTests(unittest.TestCase):
    def test_vocabulary_is_pinned_to_the_producer_axis_order_and_exit_ladder(self):
        bridge_contract = BRIDGE.load_contract()
        exit_contract = BRIDGE.EXIT_MANAGER.load_contract()
        ladder = [row["category"] for row in CONTRACT["exit_priority_ladder"]]
        self.assertEqual(ladder, exit_contract["priority_categories"])
        self.assertEqual(ladder[0], "HARD_EXIT")
        self.assertEqual(CONTRACT["axis_order"], bridge_contract["required_axes"])
        self.assertEqual(
            CONTRACT["source_contract_version"], bridge_contract["contract_version"],
        )
        self.assertEqual(
            [row["label"] for row in CONTRACT["stages"]],
            ["매수대기", "진입 차단", "진입검토", "보유", "축소", "청산검토"],
        )
        self.assertTrue(all(value is False for value in CONTRACT["authority"].values()))
        for flag in (
            "stage_authorized", "buy_authorized", "action_authorized", "order_authorized",
            "production_authorized", "trading_authorized", "real_capital_authorized",
        ):
            self.assertIs(CONTRACT["authority"][flag], False)

    def test_reordered_ladder_in_the_contract_is_rejected(self):
        mutated = copy.deepcopy(CONTRACT)
        mutated["exit_priority_ladder"].insert(0, mutated["exit_priority_ladder"].pop(3))
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "CONTRACT_FIELD_MISMATCH",
        ):
            EXPLAIN.validate_contract(mutated)

    def test_removing_an_empty_stage_reason_is_rejected(self):
        mutated = copy.deepcopy(CONTRACT)
        for row in mutated["stages"]:
            if row["stage_id"] == "HOLDING":
                row["empty_reason_code"] = None
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "CONTRACT_FIELD_MISMATCH:stages",
        ):
            EXPLAIN.validate_contract(mutated)

    def test_true_authority_in_the_contract_is_rejected(self):
        mutated = copy.deepcopy(CONTRACT)
        mutated["authority"]["buy_authorized"] = True
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "CONTRACT_FIELD_MISMATCH:authority",
        ):
            EXPLAIN.validate_contract(mutated)


class RelabelingTests(unittest.TestCase):
    """Fail-closed relabeling, exercised at the layer that owns each rule."""

    def test_wait_and_blocked_symbols_land_in_their_own_stages(self):
        result = EXPLAIN._assemble_unsigned(synthetic_bridge_packet(), CONTRACT)
        self.assertEqual(stage(result, "ENTRY_WAIT")["members"], ["KRW-BTC"])
        self.assertEqual(stage(result, "ENTRY_BLOCKED")["members"], ["KRW-ETH"])
        by_market = {row["market"]: row for row in result["symbols"]}
        self.assertEqual(by_market["KRW-BTC"]["entry_label"], "매수대기")
        self.assertEqual(by_market["KRW-BTC"]["entry_state"], "WAIT")
        self.assertEqual(by_market["KRW-ETH"]["entry_label"], "진입 차단")
        self.assertEqual(by_market["KRW-ETH"]["entry_state"], "BLOCKED")
        self.assertNotIn("payload_sha256", result)
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "OUTPUT_SCHEMA_MISMATCH",
        ):
            EXPLAIN.validate_output(result, bridge_packet=synthetic_bridge_packet())

    def test_unknown_entry_state_fails_closed_without_a_default_bucket(self):
        packet = synthetic_bridge_packet()
        packet["symbol_rules"][0]["entry"]["state"] = "PAPER_BUY_ELIGIBLE"
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError,
            "ENTRY_STATE_UNMAPPED:KRW-BTC:PAPER_BUY_ELIGIBLE",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_unknown_exit_state_fails_closed(self):
        packet = synthetic_bridge_packet()
        packet["symbol_rules"][0]["exit"]["state"] = "PARTIALLY_REDUCED"
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "EXIT_STATE_UNMAPPED:KRW-BTC",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_unknown_reason_code_fails_closed(self):
        packet = synthetic_bridge_packet()
        packet["symbol_rules"][0]["entry"]["reasons"].append("BRAND_NEW_UPSTREAM_CODE")
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError,
            "REASON_CODE_UNMAPPED:BRAND_NEW_UPSTREAM_CODE",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_reordered_symbol_exit_ladder_fails_closed(self):
        packet = synthetic_bridge_packet()
        categories = packet["symbol_rules"][0]["exit"]["priority_categories"]
        categories.append(categories.pop(0))
        self.assertNotEqual(categories[0], "HARD_EXIT")
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "EXIT_PRIORITY_DRIFT:KRW-BTC",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_defined_axis_is_never_rendered_as_a_hold_cause(self):
        packet = synthetic_bridge_packet(missing_axes=())
        self.assertEqual(packet["five_axis"]["defined_count"], 5)
        packet["symbol_rules"][0]["entry"]["reasons"].insert(
            1, "OFFICIAL_AXES_INCOMPLETE:BREADTH",
        )
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "AXIS_HOLD_STATUS_CONFLICT:BREADTH",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_dropped_axis_hold_reason_fails_closed(self):
        packet = synthetic_bridge_packet()
        packet["symbol_rules"][0]["entry"]["reasons"] = [
            code for code in packet["symbol_rules"][0]["entry"]["reasons"]
            if not code.startswith("OFFICIAL_AXES_INCOMPLETE")
        ]
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "AXIS_HOLD_COVERAGE_MISMATCH",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_undefined_axis_omitted_from_the_missing_list_fails_closed(self):
        packet = synthetic_bridge_packet(missing_axes=("BREADTH", "LEADERSHIP"))
        packet["five_axis"]["missing_axes"] = ["LEADERSHIP"]
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError,
            "AXIS_MISSING_LIST_INCOMPLETE:BREADTH",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_true_authority_in_the_source_packet_is_rejected(self):
        packet = synthetic_bridge_packet()
        packet["authority"]["trading_authorized"] = True
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "SOURCE_AUTHORITY_INVALID",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_empty_stage_reason_must_be_grounded_in_the_source_packet(self):
        packet = synthetic_bridge_packet()
        packet["symbol_rules"][0]["exit"]["reasons"] = [
            code for code in packet["symbol_rules"][0]["exit"]["reasons"]
            if code != "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET"
        ]
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError,
            "EMPTY_STAGE_REASON_UNSUPPORTED:HOLDING",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_source_summary_counts_must_agree_with_the_rendered_stages(self):
        packet = synthetic_bridge_packet()
        packet["summary"]["entry_wait_count"] = 2
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError,
            "SUMMARY_ENTRY_WAIT_COUNT_MISMATCH",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)

    def test_duplicate_symbol_row_is_rejected(self):
        packet = synthetic_bridge_packet()
        packet["symbol_rules"].append(copy.deepcopy(packet["symbol_rules"][0]))
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "SYMBOL_DUPLICATE:KRW-BTC",
        ):
            EXPLAIN._assemble_unsigned(packet, CONTRACT)


class MalformedSourceTests(unittest.TestCase):
    def test_required_committed_packet_absence_is_an_error(self):
        with tempfile.TemporaryDirectory(prefix="axis_explanation_") as tmp:
            with patch.dict(globals(), {"REAL_PACKET_PATH": Path(tmp) / "absent.json", "_VALIDATED": []}):
                with self.assertRaises(FileNotFoundError):
                    validated_bridge_packet()

    def test_missing_required_lineage_fails_instead_of_skipping(self):
        with tempfile.TemporaryDirectory(prefix="axis_explanation_") as tmp:
            # Keep the real packet and validator; only its source directory is absent.
            with patch.object(BRIDGE.DECISION, "ROOT", Path(tmp)), patch.dict(globals(), {"_VALIDATED": []}):
                with self.assertRaisesRegex(
                    BRIDGE.CryptoAxisTradeBridgeError, "SOURCE_DECISION_INVALID:FILE_HASH_FAILED",
                ):
                    validated_bridge_packet()

    def test_packet_that_fails_the_producer_validator_produces_no_output(self):
        with self.assertRaisesRegex(
            EXPLAIN.CryptoAxisTradeBridgeExplanationError, "SOURCE_BRIDGE_INVALID",
        ):
            EXPLAIN.explain({})

    def test_missing_source_file_is_rejected_and_writes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="axis_explanation_") as tmp:
            output_root = Path(tmp) / "out"
            with self.assertRaisesRegex(
                EXPLAIN.CryptoAxisTradeBridgeExplanationError, "JSON_READ_FAILED",
            ):
                EXPLAIN.populate(Path(tmp) / "absent.json", output_root=output_root)
            self.assertFalse(output_root.exists())


class ProvenanceTests(unittest.TestCase):
    def test_all_public_assemblers_reject_the_original_synthetic_bypass(self):
        for produce in (EXPLAIN.assemble, EXPLAIN.explain):
            with self.subTest(api=produce.__name__):
                with self.assertRaisesRegex(
                    EXPLAIN.CryptoAxisTradeBridgeExplanationError, "SOURCE_BRIDGE_INVALID",
                ):
                    produce(synthetic_bridge_packet(), contract=CONTRACT)

    def test_rehashing_unsigned_synthetic_output_does_not_establish_provenance(self):
        source = synthetic_bridge_packet()
        forged = EXPLAIN._assemble_unsigned(source, CONTRACT)
        self.assertNotIn("payload_sha256", forged)
        forged["payload_sha256"] = EXPLAIN.payload_sha256(forged)
        for consume in (EXPLAIN.validate_output, EXPLAIN.render_markdown):
            with self.subTest(api=consume.__name__):
                with self.assertRaisesRegex(
                    EXPLAIN.CryptoAxisTradeBridgeExplanationError, "SOURCE_BRIDGE_INVALID",
                ):
                    consume(forged, bridge_packet=source, contract=CONTRACT)

    def test_rehashed_explanation_tamper_fails_exact_source_derivation(self):
        source = validated_bridge_packet()
        forged = EXPLAIN.explain(source)
        forged["symbols"][0]["entry_label"] = "위조된 승인 라벨"
        forged.pop("payload_sha256")
        forged["payload_sha256"] = EXPLAIN.payload_sha256(forged)
        for consume in (EXPLAIN.validate_output, EXPLAIN.render_markdown):
            with self.subTest(api=consume.__name__):
                with self.assertRaisesRegex(
                    EXPLAIN.CryptoAxisTradeBridgeExplanationError, "OUTPUT_DERIVATION_MISMATCH",
                ):
                    consume(forged, bridge_packet=source, contract=CONTRACT)


class RealEvidenceTests(unittest.TestCase):
    def test_committed_generation_explains_every_symbol_as_entry_wait(self):
        source = validated_bridge_packet()
        result = real_explanation()
        self.assertEqual(result["summary"]["symbol_count"], 7)
        self.assertEqual(stage(result, "ENTRY_WAIT")["member_count"], 7)
        self.assertEqual(stage(result, "ENTRY_BLOCKED")["member_count"], 0)
        self.assertEqual(len(result["five_axis_explained"]), 5)
        self.assertTrue(
            all(row["status"] == "UNDEFINED" for row in result["five_axis_explained"])
        )
        self.assertTrue(all(row["is_hold_cause"] for row in result["five_axis_explained"]))
        self.assertEqual(result["summary"]["axis_defined_count"], 0)
        self.assertEqual(
            result["source_bridge_sha256"], source["packet_sha256"],
        )

    def test_every_symbol_appears_once_and_round_trips_to_its_upstream_codes(self):
        source = validated_bridge_packet()
        result = real_explanation()
        expected = sorted(row["market"] for row in source["symbol_rules"])
        self.assertEqual(sorted(row["market"] for row in result["symbols"]), expected)
        self.assertEqual(len(result["symbols"]), len(expected))
        source_by_market = {row["market"]: row for row in source["symbol_rules"]}
        members: list[str] = []
        for row in result["symbols"]:
            origin = source_by_market[row["market"]]
            self.assertEqual(row["entry_state"], origin["entry"]["state"])
            self.assertEqual(row["exit_state"], origin["exit"]["state"])
            self.assertEqual(row["entry_reason_codes"], origin["entry"]["reasons"])
            self.assertEqual(row["exit_reason_codes"], origin["exit"]["reasons"])
            self.assertEqual(row["upstream_state"], origin["upstream_state"])
            self.assertEqual(
                [entry["category"] for entry in row["exit_ladder"]],
                origin["exit"]["priority_categories"],
            )
            self.assertEqual(row["exit_ladder"][0]["category"], "HARD_EXIT")
            self.assertEqual(
                [hold["axis"] for hold in row["axis_holds"]],
                source["five_axis"]["missing_axes"],
            )
        for row in result["stages"]:
            members.extend(row["members"])
        self.assertEqual(sorted(members), expected)

    def test_position_stages_are_empty_for_a_grounded_no_virtual_fill_reason(self):
        source = validated_bridge_packet()
        result = real_explanation()
        for stage_id in ("HOLDING", "REDUCE", "EXIT_REVIEW"):
            row = stage(result, stage_id)
            self.assertEqual(row["member_count"], 0)
            self.assertEqual(
                row["empty_reason"]["code"],
                "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET",
            )
        entry_review = stage(result, "ENTRY_REVIEW")
        self.assertEqual(entry_review["member_count"], 0)
        self.assertEqual(entry_review["empty_reason"]["code"], "AGGREGATE_POLICY_UNRATIFIED")
        # The emptiness reasons are verbatim upstream codes, not invented text.
        for row in source["symbol_rules"]:
            self.assertIn(
                "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET", row["exit"]["reasons"],
            )
            self.assertIn("AGGREGATE_POLICY_UNRATIFIED", row["entry"]["reasons"])
        self.assertEqual(result["summary"]["position_stage_member_count"], 0)

    def test_all_authority_flags_are_false_and_validate_output_accepts(self):
        result = real_explanation()
        self.assertTrue(all(value is False for value in result["authority"].values()))
        for flag in (
            "stage_authorized", "buy_authorized", "action_authorized", "order_authorized",
            "production_authorized", "trading_authorized", "real_capital_authorized",
        ):
            self.assertIs(result["authority"][flag], False)
        self.assertEqual(
            EXPLAIN.validate_output(
                result, bridge_packet=validated_bridge_packet(), contract=CONTRACT,
            ), result,
        )

    def test_explain_end_to_end_matches_the_revalidated_assembly(self):
        expected = real_explanation()
        self.assertEqual(
            EXPLAIN.canonical_json(EXPLAIN.explain(committed_bridge_packet())),
            EXPLAIN.canonical_json(expected),
        )

    def test_markdown_and_json_report_the_same_counts_labels_and_reasons(self):
        result = real_explanation()
        source = validated_bridge_packet()
        markdown = EXPLAIN.render_markdown(result, bridge_packet=source)
        self.assertEqual(
            markdown, EXPLAIN.render_markdown(real_explanation(), bridge_packet=source),
        )
        for row in result["stages"]:
            heading = f"### {row['label']} ({row['member_count']}종목)"
            self.assertEqual(markdown.count(heading), 1, heading)
            if row["empty_reason"] is not None:
                self.assertIn(f"`{row['empty_reason']['code']}`", markdown)
        for row in result["symbols"]:
            self.assertEqual(markdown.count(f"### {row['market']} ("), 1, row["market"])
            self.assertIn(
                f"- 단계: {row['entry_label']} (`{row['entry_state']}`)", markdown,
            )
            for reason in row["entry_reason_text"]:
                self.assertIn(f"진입 사유 `{reason['code']}`", markdown)
        positions = [
            markdown.index(f"{entry['rank']}. `{entry['category']}`")
            for entry in result["exit_priority_ladder"]
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("1. `HARD_EXIT`", markdown)
        self.assertIn(f"총 {result['summary']['symbol_count']}종목", markdown)

    def test_resigned_source_tamper_is_rejected_with_no_output(self):
        tampered = copy.deepcopy(validated_bridge_packet())
        tampered["five_axis"]["defined_count"] = 5
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("packet_sha256")
        tampered["packet_sha256"] = BRIDGE.payload_sha256(unsigned)
        with tempfile.TemporaryDirectory(prefix="axis_explanation_") as tmp:
            source = Path(tmp) / "tampered.json"
            source.write_text(json.dumps(tampered), encoding="utf-8")
            output_root = Path(tmp) / "out"
            with self.assertRaisesRegex(
                EXPLAIN.CryptoAxisTradeBridgeExplanationError, "SOURCE_BRIDGE_INVALID",
            ):
                EXPLAIN.populate(source, output_root=output_root)
            self.assertFalse(output_root.exists())

    def test_populate_is_byte_stable_and_rejects_drift_instead_of_overwriting(self):
        validated_bridge_packet()
        with tempfile.TemporaryDirectory(prefix="axis_explanation_") as tmp:
            source = Path(tmp) / "bridge.json"
            source.write_text(json.dumps(committed_bridge_packet()), encoding="utf-8")
            output_root = Path(tmp) / "out"

            first = EXPLAIN.populate(source, output_root=output_root)
            self.assertEqual(first["outcome"], "populated")
            packet_path = Path(first["path"])
            markdown_path = Path(first["markdown_path"])
            self.assertTrue(packet_path.exists() and markdown_path.exists())
            self.assertEqual(
                packet_path.parent,
                output_root / "2026-09-06" / "2349" / first["source_generation_id"],
            )
            packet_bytes = packet_path.read_bytes()
            markdown_bytes = markdown_path.read_bytes()

            second = EXPLAIN.populate(source, output_root=output_root)
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(second["payload_sha256"], first["payload_sha256"])
            self.assertEqual(packet_path.read_bytes(), packet_bytes)
            self.assertEqual(markdown_path.read_bytes(), markdown_bytes)

            markdown_path.write_text("drifted\n", encoding="utf-8")
            with self.assertRaisesRegex(
                EXPLAIN.CryptoAxisTradeBridgeExplanationError,
                "EXISTING_OUTPUT_DRIFT_OR_TAMPER",
            ):
                EXPLAIN.populate(source, output_root=output_root)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), "drifted\n")


if __name__ == "__main__":
    unittest.main()
