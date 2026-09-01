#!/usr/bin/env python3
"""PAPER 12-5 KRX market-judgement contract regression."""

import ast
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "market_judgement" / "krx_market_judgement.py"
FIXTURE = ROOT / "test" / "fixtures" / "krx_market_judgement" / "expected_natural_hold.json"
OBSERVATION = ROOT / "data" / "latest_korea_market_signals.json"
POLICY = ROOT / "config" / "korea_leadership_policy.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("krx_market_judgement", SOURCE)
CONTRACT = MODULE.load_contract()
EXPECTATION = json.loads(FIXTURE.read_text(encoding="utf-8"))
OBSERVATION_PACKET = json.loads(OBSERVATION.read_text(encoding="utf-8"))
RETAINED = (
    ROOT
    / "data"
    / "observations"
    / "korea_market_signals"
    / OBSERVATION_PACKET["as_of_date"]
    / "packet.json"
)


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


SOURCE_SHA256 = file_sha(OBSERVATION)
SOURCE_TIME = dt.datetime.strptime(
    OBSERVATION_PACKET["available_at"], "%Y-%m-%dT%H:%M:%SZ"
).replace(tzinfo=dt.timezone.utc)
DECISION_TIME = SOURCE_TIME + dt.timedelta(
    seconds=EXPECTATION["decision_offset_seconds"]
)
DECISION_AT = DECISION_TIME.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_natural():
    envelope = MODULE.build_input_envelope(
        decision_at=DECISION_AT,
        observation_path=OBSERVATION,
        observation_sha256=SOURCE_SHA256,
        retained_observation_path=RETAINED,
        leadership_policy_path=POLICY,
        leadership_policy_sha256=EXPECTATION["leadership_policy_sha256"],
        contract=CONTRACT,
    )
    return envelope, MODULE.build_receipt(envelope, CONTRACT)


def gates(envelope):
    return {row["name"]: row for row in envelope["gates"]}


def rehash_receipt(receipt):
    changed = copy.deepcopy(receipt)
    changed.pop("receipt_sha256", None)
    changed["receipt_sha256"] = MODULE.payload_sha256(changed)
    return changed


class KrxMarketJudgementTests(unittest.TestCase):
    def test_contract_is_paper_only_and_has_no_execution_authority(self):
        self.assertEqual(CONTRACT["market"], "KRX")
        self.assertEqual(CONTRACT["run_mode"], "PAPER")
        self.assertTrue(CONTRACT["authority"]["paper_market_judgement_input_only"])
        for key, value in CONTRACT["authority"].items():
            if key.endswith("_authorized") and key not in {
                "market_observation_authorized", "source_lineage_authorized"
            }:
                self.assertFalse(value, key)

    def test_canonical_source_hashes_and_pointer_are_exact(self):
        self.assertEqual(EXPECTATION["source_selection"], "CURRENT_CANONICAL_LATEST_POINTER")
        self.assertEqual(file_sha(OBSERVATION), SOURCE_SHA256)
        self.assertEqual(file_sha(RETAINED), SOURCE_SHA256)
        self.assertEqual(OBSERVATION.read_bytes(), RETAINED.read_bytes())
        self.assertEqual(file_sha(POLICY), EXPECTATION["leadership_policy_sha256"])

    def test_natural_input_preserves_breadth_turnover_and_leadership(self):
        envelope, _ = build_natural()
        evidence = envelope["market_evidence"]
        self.assertEqual(
            set(evidence["breadth"]["measurement"]["markets"]),
            {"KOSPI", "KOSDAQ"},
        )
        self.assertEqual(
            evidence["breadth"]["measurement"]["markets"]["KOSPI"]["paired_count"],
            OBSERVATION_PACKET["axes"]["BREADTH"]["measurement"]["markets"]["KOSPI"]["paired_count"],
        )
        self.assertEqual(
            evidence["turnover"]["measurement"]["markets"]["KOSDAQ"]["current_turnover_pct"],
            OBSERVATION_PACKET["axes"]["LIQUIDITY"]["measurement"]["markets"]["KOSDAQ"]["current_turnover_pct"],
        )
        coverage = evidence["leadership_policy_coverage"]
        self.assertEqual(coverage["KOSPI"]["observed_sector_count"], 24)
        self.assertEqual(coverage["KOSDAQ"]["observed_sector_count"], 22)
        observations = evidence["sector_relative_strength"]["measurement"]["observations"]
        self.assertEqual(len(observations), 46)
        self.assertFalse(
            evidence["sector_relative_strength"]["measurement"]["investment_ranking_authorized"]
        )

    def test_five_of_five_does_not_spoof_scoring_authority(self):
        envelope, receipt = build_natural()
        status = gates(envelope)
        self.assertEqual(envelope["coverage"]["ratio"], "5/5")
        self.assertEqual(status["AXIS_COVERAGE"]["status"], "PASS")
        self.assertEqual(status["LEADERSHIP_POLICY"]["status"], "PASS")
        self.assertEqual(status["REGIME_SCORING_AUTHORITY"]["status"], "FAIL")
        self.assertEqual(status["TTL_POLICY"]["status"], "FAIL")
        self.assertEqual(status["FRESHNESS"]["status"], "FAIL")
        self.assertEqual(status["SCORING_RESULT"]["status"], "FAIL")
        self.assertEqual(receipt["market_judgement_status"], "UNKNOWN")
        self.assertEqual(receipt["regime"], "UNKNOWN")
        self.assertEqual(receipt["recommendation"], "HOLD")
        self.assertIsNone(receipt["confidence"])
        self.assertIsNone(receipt["action"])

    def test_natural_blocking_reasons_match_non_promotable_fixture(self):
        envelope, receipt = build_natural()
        expected = EXPECTATION["expected"]
        self.assertEqual(envelope["completed_bar"]["status"], expected["completed_bar"])
        self.assertEqual(envelope["freshness"]["ttl_seconds"], None)
        self.assertEqual(
            envelope["freshness"]["source_age_seconds"],
            EXPECTATION["decision_offset_seconds"],
        )
        for reason in expected["required_blocking_reasons"]:
            self.assertIn(reason, receipt["blocking_reasons"])
        self.assertEqual(EXPECTATION["evidence_class"], "TEST_ONLY_NON_PROMOTABLE")
        self.assertFalse(EXPECTATION["authority"]["paper_or_natural_promotion_authorized"])

    def test_source_hash_and_pointer_substitution_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            policy = temp / "policy.json"
            policy.write_bytes(POLICY.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                MODULE.KrxMarketJudgementError, "EXACT_SOURCE_HASH_MISMATCH"
            ):
                MODULE.build_input_envelope(
                    decision_at=DECISION_AT,
                    observation_path=OBSERVATION,
                    observation_sha256=SOURCE_SHA256,
                    retained_observation_path=RETAINED,
                    leadership_policy_path=policy,
                    leadership_policy_sha256=EXPECTATION["leadership_policy_sha256"],
                    contract=CONTRACT,
                )
            retained = temp / "retained.json"
            retained.write_bytes(RETAINED.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                MODULE.KrxMarketJudgementError, "LATEST_POINTER_APPEND_ONLY_MISMATCH"
            ):
                MODULE.build_input_envelope(
                    decision_at=DECISION_AT,
                    observation_path=OBSERVATION,
                    observation_sha256=SOURCE_SHA256,
                    retained_observation_path=retained,
                    leadership_policy_path=POLICY,
                    leadership_policy_sha256=EXPECTATION["leadership_policy_sha256"],
                    contract=CONTRACT,
                )

    def test_source_from_future_and_bad_expected_hash_fail_closed(self):
        with self.assertRaisesRegex(MODULE.KrxMarketJudgementError, "SOURCE_FROM_FUTURE"):
            MODULE.build_input_envelope(
                decision_at=(SOURCE_TIME - dt.timedelta(seconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                observation_path=OBSERVATION,
                observation_sha256=SOURCE_SHA256,
                retained_observation_path=RETAINED,
                leadership_policy_path=POLICY,
                leadership_policy_sha256=EXPECTATION["leadership_policy_sha256"],
                contract=CONTRACT,
            )
        with self.assertRaisesRegex(
            MODULE.KrxMarketJudgementError, "EXACT_SOURCE_HASH_MISMATCH"
        ):
            MODULE.build_input_envelope(
                decision_at=DECISION_AT,
                observation_path=OBSERVATION,
                observation_sha256="0" * 64,
                retained_observation_path=RETAINED,
                leadership_policy_path=POLICY,
                leadership_policy_sha256=EXPECTATION["leadership_policy_sha256"],
                contract=CONTRACT,
            )

    def test_defined_regime_or_self_rehashed_tamper_is_rejected(self):
        envelope, receipt = build_natural()
        all_pass = copy.deepcopy(envelope)
        for gate in all_pass["gates"]:
            gate["status"] = "PASS"
            gate["reasons"] = []
        all_pass.pop("envelope_sha256")
        all_pass["envelope_sha256"] = MODULE.payload_sha256(all_pass)
        with self.assertRaisesRegex(
            MODULE.KrxMarketJudgementError,
            "SCORING_RESULT_REQUIRED_FOR_DEFINED_REGIME",
        ):
            MODULE.build_receipt(all_pass, CONTRACT)

        tampered = copy.deepcopy(receipt)
        tampered["regime"] = "RISK_ON"
        tampered["market_judgement_status"] = "DEFINED"
        tampered["recommendation"] = "BUY"
        with self.assertRaisesRegex(
            MODULE.KrxMarketJudgementError, "RECEIPT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_receipt(rehash_receipt(tampered), CONTRACT)

    def test_build_is_deterministic_and_does_not_mutate_inputs(self):
        first_envelope, first_receipt = build_natural()
        second_envelope, second_receipt = build_natural()
        self.assertEqual(MODULE.canonical_json(first_envelope), MODULE.canonical_json(second_envelope))
        self.assertEqual(MODULE.canonical_json(first_receipt), MODULE.canonical_json(second_receipt))
        self.assertEqual(first_receipt["input_envelope_sha256"], first_envelope["envelope_sha256"])
        self.assertEqual(MODULE.validate_receipt(first_receipt, CONTRACT), first_receipt)

    def test_cli_writes_only_external_immutable_outputs(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            envelope_path = temp / "envelope.json"
            receipt_path = temp / "receipt.json"
            first = MODULE.run(
                decision_at=DECISION_AT,
                observation_path=OBSERVATION,
                observation_sha256=SOURCE_SHA256,
                retained_observation_path=RETAINED,
                leadership_policy_path=POLICY,
                leadership_policy_sha256=EXPECTATION["leadership_policy_sha256"],
                envelope_output=envelope_path,
                receipt_output=receipt_path,
            )
            second = MODULE.run(
                decision_at=DECISION_AT,
                observation_path=OBSERVATION,
                observation_sha256=SOURCE_SHA256,
                retained_observation_path=RETAINED,
                leadership_policy_path=POLICY,
                leadership_policy_sha256=EXPECTATION["leadership_policy_sha256"],
                envelope_output=envelope_path,
                receipt_output=receipt_path,
            )
            self.assertEqual(first[:2], ("CREATED", "CREATED"))
            self.assertEqual(second[:2], ("NO_CHANGE", "NO_CHANGE"))
            self.assertEqual(json.loads(receipt_path.read_text())["regime"], "UNKNOWN")

            forbidden = ROOT / "data" / "krx_market_judgement_test.json"
            with self.assertRaisesRegex(
                MODULE.KrxMarketJudgementError, "OUTPUT_INSIDE_REPOSITORY_FORBIDDEN"
            ):
                MODULE._write_immutable(forbidden, first[2])
            self.assertFalse(forbidden.exists())

    def test_schema_and_contract_json_are_parseable_and_pinned(self):
        schema = json.loads(
            (ROOT / "schemas" / "krx_market_judgement_receipt.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["regime"]["const"], "UNKNOWN")
        self.assertEqual(schema["properties"]["recommendation"]["const"], "HOLD")
        self.assertEqual(schema["properties"]["all_required_gates_literal_pass"]["const"], False)
        self.assertEqual(CONTRACT["source_requirements"]["ttl_source"], "RATIFIED_SCORING_RECEIPT_ONLY_NO_FALLBACK")


if __name__ == "__main__":
    unittest.main()
