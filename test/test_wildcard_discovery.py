"""P3-11 evidence-linked Wildcard Discovery path regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "discovery" / "wildcard_discovery.py"
SPEC = importlib.util.spec_from_file_location("wildcard_discovery", MODULE_PATH)
WC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(WC)


SOURCE_META = {
    "sec_edgar": "https://www.sec.gov/Archives/edgar/data/1/test.txt",
    "microsoft_sec_issuer_disclosure": "https://www.sec.gov/Archives/edgar/data/789019/test.htm",
    "tsmc_investor_relations": "https://investor.tsmc.com/english/news/1",
    "dart_open_api": "https://opendart.fss.or.kr/api/list.json",
    "krx_open_api_stock_daily": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "kraken_public_api": "https://api.kraken.com/0/public/Assets",
    "defillama_stablecoins_api": "https://stablecoins.llama.fi/stablecoins",
}


def source(source_id="sec_edgar", marker="a") -> dict:
    return {
        "source_id": source_id,
        "source_url": SOURCE_META[source_id],
        "source_sha256": marker * 64,
        "available_at": "2026-08-19",
        "retrieved_at_utc": "2026-08-19T10:00:00Z",
    }


def linked(evidence_id="EVIDENCE.WC.1", source_id="sec_edgar", marker="a") -> dict:
    return {
        "evidence_id": evidence_id,
        "status": "EVIDENCE_LINKED",
        "claim_text": "Primary source contains an observed event outside the current Theme taxonomy.",
        "missing_reasons": [],
        "source_identity": source(source_id, marker),
        "audit_provenance": {"record_locator": "synthetic-test-record-1", "capture_kind": "PRIMARY_SOURCE"},
    }


def unresolved(evidence_id="EVIDENCE.WC.2", status="EVIDENCE_UNRESOLVED") -> dict:
    return {
        "evidence_id": evidence_id,
        "status": status,
        "claim_text": None,
        "missing_reasons": ["SOURCE_RECORD_NOT_YET_LINKED"],
        "source_identity": None,
        "audit_provenance": None,
    }


def submission(
    *,
    submission_id="WILDCARD.SUBMISSION.1",
    market="US",
    asset_id="US:XNAS:TEST",
    evidence=None,
) -> dict:
    return {
        "submission_id": submission_id,
        "market": market,
        "asset_id": asset_id,
        "subject": "TEST",
        "observed_on": "2026-08-19",
        "theme_membership_status": "OUTSIDE_CURRENT_TAXONOMY",
        "theme_ids": [],
        "nominated_by": "research-observer",
        "nominated_at_utc": "2026-08-19T12:00:00Z",
        "nomination_authority": "OBSERVATION_ONLY",
        "submission_reason": "The observation is not represented by the current Theme taxonomy.",
        "hypothesis": "The event may warrant evidence collection without implying investment strength.",
        "evidence": evidence if evidence is not None else [linked()],
    }


def payload(rows=None) -> dict:
    return {
        "schema_version": "wildcard_discovery_input/1",
        "as_of_utc": "2026-08-20T00:00:00Z",
        "submissions": rows if rows is not None else [submission()],
    }


def rehash(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = WC.payload_sha256(value)
    return value


class WildcardDiscoveryTests(unittest.TestCase):
    def test_linked_nomination_records_case_without_strength_or_promotion(self):
        packet = WC.build_packet(payload())
        self.assertEqual(packet["status"], "WILDCARD_INTAKE_RECORDED")
        self.assertEqual(packet["submission_count"], 1)
        self.assertEqual(packet["case_count"], 1)
        self.assertEqual(packet["pending_count"], 0)
        case = packet["cases"][0]
        self.assertTrue(case["case_id"].startswith("RADAR-WC-"))
        self.assertEqual(case["schema_version"], "wildcard_discovery_case/1")
        self.assertEqual(case["discovery_path"], "WILDCARD_OUTSIDE_THEME")
        self.assertEqual(case["nomination"]["text_status"], "UNCONFIRMED_NOMINATION_TEXT")
        self.assertEqual(case["evidence_status"], "EVIDENCE_LINKED")
        self.assertEqual(case["linked_evidence"][0]["source_identity"]["source_sha256"], "a" * 64)
        self.assertEqual(
            case["linked_evidence"][0]["claim_status"],
            "SOURCE_LINKED_OBSERVATION_NOT_INTERPRETED",
        )
        self.assertEqual(case["strength_status"], "UNRATIFIED")
        self.assertEqual(case["importance"], "UNRATIFIED")
        self.assertFalse(case["candidate_eligible"])
        self.assertIsNone(case["candidate_rank"])
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["rule_evaluation"])
        self.assertIsNone(case["action"])

    def test_no_linked_evidence_stays_pending_and_is_not_case(self):
        packet = WC.build_packet(payload([submission(evidence=[unresolved()])]))
        self.assertEqual(packet["case_count"], 0)
        self.assertEqual(packet["pending_count"], 1)
        result = packet["submissions"][0]
        self.assertFalse(result["case_created"])
        self.assertEqual(result["pending_reason"], "NO_SOURCE_LINKED_EVIDENCE")
        self.assertEqual(result["linked_evidence_count"], 0)

    def test_mixed_evidence_is_partial_and_preserves_unresolved_item(self):
        value = submission(evidence=[linked(), unresolved()])
        packet = WC.build_packet(payload([value]))
        case = packet["cases"][0]
        self.assertEqual(case["evidence_status"], "EVIDENCE_PARTIAL")
        self.assertEqual(len(case["linked_evidence"]), 1)
        self.assertEqual(case["unresolved_evidence"], [{
            "evidence_id": "EVIDENCE.WC.2",
            "status": "EVIDENCE_UNRESOLVED",
            "missing_reasons": ["SOURCE_RECORD_NOT_YET_LINKED"],
        }])

    def test_standalone_validator_accepts_linked_pending_and_partial_packets(self):
        packets = (
            WC.build_packet(payload()),
            WC.build_packet(payload([submission(evidence=[unresolved()])])),
            WC.build_packet(payload([submission(evidence=[linked(), unresolved()])])),
        )
        for packet in packets:
            with self.subTest(cases=packet["case_count"]):
                checked = WC.validate_packet(copy.deepcopy(packet))
                self.assertEqual(WC.canonical_json(checked), WC.canonical_json(packet))

    def test_standalone_validator_rejects_rehashed_case_derivation_tamper(self):
        packet = WC.build_packet(payload())
        packet["cases"][0]["case_id"] = "RADAR-WC-0000000000000000"
        with self.assertRaisesRegex(
            WC.WildcardDiscoveryError, "OUTPUT_CASE_DERIVATION_MISMATCH"
        ):
            WC.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_source_lineage_tamper(self):
        packet = WC.build_packet(payload())
        packet["submissions"][0]["evidence"][0]["source_identity"][
            "source_url"
        ] = "https://example.com/not-source"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "SOURCE_URL_INVALID"):
            WC.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_submission_summary_tamper(self):
        packet = WC.build_packet(payload())
        packet["submissions"][0]["linked_evidence_count"] = 0
        with self.assertRaisesRegex(
            WC.WildcardDiscoveryError, "OUTPUT_SUBMISSION_SUMMARY_MISMATCH"
        ):
            WC.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_authority_expansion(self):
        packet = WC.build_packet(payload())
        packet["cases"][0]["action"] = {"action": "BUY"}
        with self.assertRaisesRegex(
            WC.WildcardDiscoveryError, "OUTPUT_CASE_DERIVATION_MISMATCH"
        ):
            WC.validate_packet(rehash(packet))

    def test_theme_membership_is_outside_or_unresolved_with_no_theme_ids(self):
        unresolved_theme = submission()
        unresolved_theme["theme_membership_status"] = "UNRESOLVED"
        self.assertEqual(WC.build_packet(payload([unresolved_theme]))["case_count"], 1)

        in_theme = submission()
        in_theme["theme_membership_status"] = "IN_CURRENT_TAXONOMY"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "THEME_MEMBERSHIP_STATUS_INVALID"):
            WC.build_packet(payload([in_theme]))
        hidden_theme = submission()
        hidden_theme["theme_ids"] = ["AI_INFRA"]
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "WILDCARD_THEME_IDS_FORBIDDEN"):
            WC.build_packet(payload([hidden_theme]))

    def test_all_markets_accept_only_registered_market_sources(self):
        korea = submission(submission_id="WILDCARD.KOREA.1", market="KOREA", asset_id="KRX:005930", evidence=[linked(source_id="dart_open_api")])
        crypto = submission(submission_id="WILDCARD.CRYPTO.1", market="CRYPTO", asset_id="CRYPTO:BTCUSD", evidence=[linked(source_id="kraken_public_api")])
        packet = WC.build_packet(payload([submission(), korea, crypto]))
        self.assertEqual(packet["case_count"], 3)
        self.assertEqual(sorted(case["market"] for case in packet["cases"]), ["CRYPTO", "KOREA", "US"])

        wrong = submission(market="CRYPTO", asset_id="CRYPTO:BTCUSD", evidence=[linked(source_id="sec_edgar")])
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "SOURCE_ID_NOT_ALLOWED"):
            WC.build_packet(payload([wrong]))

    def test_source_host_hash_and_time_fail_closed(self):
        wrong_host = submission()
        wrong_host["evidence"][0]["source_identity"]["source_url"] = "https://example.com/x"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "SOURCE_URL_INVALID"):
            WC.build_packet(payload([wrong_host]))
        bad_hash = submission()
        bad_hash["evidence"][0]["source_identity"]["source_sha256"] = "bad"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "SOURCE_SHA256_INVALID"):
            WC.build_packet(payload([bad_hash]))
        after_nomination = submission()
        after_nomination["evidence"][0]["source_identity"]["retrieved_at_utc"] = "2026-08-19T13:00:00Z"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            WC.build_packet(payload([after_nomination]))
        after_as_of = submission()
        after_as_of["nominated_at_utc"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "NOMINATED_AT_INVALID"):
            WC.build_packet(payload([after_as_of]))
        before_observation = submission()
        before_observation["nominated_at_utc"] = "2026-08-18T12:00:00Z"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "NOMINATED_AT_INVALID"):
            WC.build_packet(payload([before_observation]))

    def test_unlinked_evidence_cannot_hide_claim_source_or_provenance(self):
        for field, replacement in (
            ("claim_text", "hidden claim"),
            ("source_identity", source()),
            ("audit_provenance", {"locator": "hidden"}),
        ):
            item = unresolved()
            item[field] = replacement
            with self.assertRaisesRegex(WC.WildcardDiscoveryError, "UNLINKED_EVIDENCE_INCONSISTENT"):
                WC.build_packet(payload([submission(evidence=[item])]))

    def test_linked_evidence_requires_claim_provenance_and_no_missing_reason(self):
        no_claim = linked()
        no_claim["claim_text"] = None
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "EVIDENCE_CLAIM_INVALID"):
            WC.build_packet(payload([submission(evidence=[no_claim])]))
        no_provenance = linked()
        no_provenance["audit_provenance"] = {}
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "AUDIT_PROVENANCE_INVALID"):
            WC.build_packet(payload([submission(evidence=[no_provenance])]))
        contradictory = linked()
        contradictory["missing_reasons"] = ["NOT_MISSING"]
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "LINKED_EVIDENCE_INCONSISTENT"):
            WC.build_packet(payload([submission(evidence=[contradictory])]))

    def test_nomination_text_is_explicit_but_never_confirmed_fact(self):
        packet = WC.build_packet(payload())
        nomination = packet["cases"][0]["nomination"]
        self.assertEqual(nomination["authority"], "OBSERVATION_ONLY")
        self.assertEqual(nomination["text_status"], "UNCONFIRMED_NOMINATION_TEXT")
        bad_authority = submission()
        bad_authority["nomination_authority"] = "CANDIDATE_APPROVAL"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "NOMINATION_AUTHORITY_INVALID"):
            WC.build_packet(payload([bad_authority]))
        padded = submission()
        padded["hypothesis"] = " padded"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "HYPOTHESIS_INVALID"):
            WC.build_packet(payload([padded]))

    def test_evidence_count_is_bounded_and_nonempty(self):
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "EVIDENCE_COUNT_INVALID"):
            WC.build_packet(payload([submission(evidence=[])]))
        too_many = [linked(f"EVIDENCE.WC.{index}", marker=chr(97 + index)) for index in range(11)]
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "EVIDENCE_COUNT_INVALID"):
            WC.build_packet(payload([submission(evidence=too_many)]))

    def test_duplicate_evidence_and_submission_ids_fail_closed(self):
        duplicate_evidence = submission(evidence=[linked(), copy.deepcopy(linked())])
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "EVIDENCE_ID_DUPLICATE"):
            WC.build_packet(payload([duplicate_evidence]))
        duplicate_submission = submission()
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "SUBMISSION_ID_DUPLICATE"):
            WC.build_packet(payload([duplicate_submission, copy.deepcopy(duplicate_submission)]))

    def test_order_is_byte_deterministic(self):
        second = submission(submission_id="WILDCARD.SUBMISSION.2", asset_id="US:XNAS:OTHER", evidence=[linked("EVIDENCE.WC.3", marker="c"), linked("EVIDENCE.WC.2", marker="b")])
        first_input = payload([submission(), second])
        first = WC.build_packet(first_input)
        first_input["submissions"].reverse()
        for item in first_input["submissions"]:
            item["evidence"].reverse()
        second_packet = WC.build_packet(first_input)
        self.assertEqual(WC.canonical_json(first), WC.canonical_json(second_packet))
        digest = second_packet.pop("payload_sha256")
        self.assertEqual(digest, WC.payload_sha256(second_packet))

    def test_contract_keeps_strength_candidate_stage_production_and_trading_closed(self):
        packet = WC.build_packet(payload())
        self.assertEqual(packet["policy_status"]["strength_threshold"], "UNRATIFIED")
        authority = packet["authority"]
        self.assertTrue(authority["case_recording_only"])
        self.assertFalse(authority["nomination_text_is_confirmed_fact"])
        for field in (
            "strength_claim_authorized", "importance_ranking_authorized",
            "candidate_eligibility_authorized", "stage_promotion_authorized",
            "rule_evaluation_authorized", "production_authorized", "trading_authorized",
        ):
            self.assertFalse(authority[field])

    def test_contract_and_input_tampering_are_rejected(self):
        contract = WC.load_contract()
        contract["authority"]["stage_promotion_authorized"] = True
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "CONTRACT_FIELD_MISMATCH"):
            WC.build_packet(payload(), contract=contract)
        extra = payload()
        extra["strength_threshold"] = "10"
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "INPUT_FIELDS_MISMATCH"):
            WC.build_packet(extra)
        with self.assertRaisesRegex(WC.WildcardDiscoveryError, "SUBMISSIONS_EMPTY"):
            WC.build_packet(payload([]))

    def test_cli_is_temp_only_atomic_and_preserves_output_on_failure(self):
        tracked_before = (ROOT / "data" / "event_records.jsonl").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            output_path = tmp / "output.json"
            input_path.write_text(json.dumps(payload()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["case_count"], 1)
            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            input_path.write_text(json.dumps(payload([])), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual((ROOT / "data" / "event_records.jsonl").read_bytes(), tracked_before)

        with tempfile.TemporaryDirectory() as raw:
            input_path = Path(raw) / "input.json"
            input_path.write_text(json.dumps(payload()), encoding="utf-8")
            tracked_target = ROOT / "wildcard-output.json"
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(tracked_target)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("TRACKED_OUTPUT_FORBIDDEN", result.stdout)
            self.assertFalse(tracked_target.exists())

    def test_module_has_no_network_tracked_output_or_default_strength_policy(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", text)
        self.assertNotIn("urlopen", text)
        self.assertNotIn("data/", text)
        self.assertFalse((ROOT / "config" / "wildcard_strength_policy.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
