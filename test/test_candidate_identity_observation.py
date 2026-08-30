#!/usr/bin/env python3
"""Candidate canonical-identity observation contract regressions."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE_REPORT_PATH = (
    ROOT / "evidence" / "operational" / "dynamic_clock"
    / "candidate_validity_source_reports"
    / "report-8dce78ebbbd43fb241afd77270ef80e67e8ab6ca2d89184302421707c4271512.json"
)

from identity import canonical_identity as ci  # noqa: E402
from identity.candidate_identity_observation import (  # noqa: E402
    AUTHORITY_ALL_FALSE,
    CandidateIdentityObservationError,
    TRIGGER_MANUAL_WORKFLOW_DISPATCH,
    TRIGGER_UPSTREAM_WORKFLOW_RUN,
    _history_record,
    build_observation,
    validate_history_record,
    validate_observation,
    write_history_record,
)
from replay.opportunity_trigger import payload_sha256  # noqa: E402


def resign(candidate: dict) -> None:
    candidate["record_hash"] = payload_sha256({
        key: value for key, value in candidate.items() if key != "record_hash"
    })


class CandidateIdentityObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        full = json.loads(FIXTURE_REPORT_PATH.read_text(encoding="utf-8"))
        crypto_rows = full["by_market"]["CRYPTO"]["review_queue"]
        cls.unresolved_crypto_subject = next(
            row["subject"] for row in crypto_rows if row["subject"] != "BTC"
        )
        wanted = {"BTC", "005930", cls.unresolved_crypto_subject}
        by_market = {}
        for market, result in full["by_market"].items():
            rows = [row for row in result["review_queue"] if row["subject"] in wanted]
            if rows:
                by_market[market] = {"review_queue": rows}
        cls.report = {
            "decision_date": full["decision_date"],
            "operational_evaluation": full["operational_evaluation"],
            "by_market": by_market,
        }
        cls.authority = ci.load_authority()
        cls.scope_authority = ci.load_scope_authority()
        cls.packet = build_observation(cls.report, cls.authority, cls.scope_authority)

    def row(self, subject: str) -> dict:
        return next(row for row in self.packet["observations"] if row["subject"] == subject)

    def test_real_btc_and_samsung_resolve_to_ratified_instruments(self):
        self.assertEqual(self.row("BTC")["identity"]["canonical_instrument_id"], "CRYPTO:BTC")
        self.assertEqual(self.row("005930")["identity"]["canonical_instrument_id"], "KRX:005930:COMMON")

    def test_unratified_crypto_pair_remains_not_computable(self):
        row = self.row(self.unresolved_crypto_subject)
        self.assertNotEqual(row["identity"]["status"], ci.RESOLVED)
        self.assertIsNone(row["identity"]["canonical_instrument_id"])

    def test_scope_observation_is_separate_from_instrument_identity(self):
        row = self.row(self.unresolved_crypto_subject)
        self.assertEqual(row["account_scope"], {"status": ci.RESOLVED, "account_scope": "CRYPTO"})
        self.assertNotEqual(row["identity"]["status"], ci.RESOLVED)

    def test_exact_operational_timestamp_is_used_without_upgrading_candidate_precision(self):
        source = next(row for result in self.report["by_market"].values() for row in result["review_queue"] if row["subject"] == "BTC")
        observed = self.row("BTC")
        self.assertEqual(observed["operational_evaluated_at"], source["operational_evaluation"]["evaluated_at_utc"])
        self.assertEqual(source["time_precision"], "DATE_ONLY")
        self.assertEqual(observed["candidate_validity_status"], "NOT_EVALUATED_BY_THIS_CONTRACT")

    def test_all_authority_remains_false(self):
        self.assertEqual(self.packet["authority"], AUTHORITY_ALL_FALSE)
        for row in self.packet["observations"]:
            self.assertEqual(row["authority"], AUTHORITY_ALL_FALSE)
            self.assertTrue(all(value is False for value in row["authority"].values()))

    def test_zero_candidate_population_is_a_valid_identity_observation(self):
        report = {
            "decision_date": self.report["decision_date"],
            "operational_evaluation": copy.deepcopy(
                self.report["operational_evaluation"]
            ),
            "by_market": {},
        }
        packet = build_observation(
            report, self.authority, self.scope_authority
        )
        self.assertEqual(packet["observations"], [])
        self.assertEqual(packet["summary"], {
            "candidate_count": 0,
            "identity_resolved_count": 0,
            "scope_resolved_count": 0,
        })
        self.assertEqual(
            validate_observation(
                packet, report, self.authority, self.scope_authority
            ),
            packet,
        )

    def test_packet_is_deterministic_and_validator_rebuilds_independently(self):
        rebuilt = build_observation(self.report, self.authority, self.scope_authority)
        self.assertEqual(self.packet, rebuilt)
        self.assertEqual(validate_observation(self.packet, self.report, self.authority, self.scope_authority), self.packet)

    def test_resigned_output_tamper_is_rejected(self):
        tampered = copy.deepcopy(self.packet)
        tampered["observations"][0]["identity"]["canonical_instrument_id"] = "FAKE"
        tampered["packet_sha256"] = payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(CandidateIdentityObservationError, "OBSERVATION_MISMATCH"):
            validate_observation(tampered, self.report, self.authority, self.scope_authority)

    def test_candidate_market_mismatch_is_rejected(self):
        report = copy.deepcopy(self.report)
        market = next(iter(report["by_market"]))
        report["by_market"][market]["review_queue"][0]["market"] = "WRONG"
        resign(report["by_market"][market]["review_queue"][0])
        with self.assertRaisesRegex(CandidateIdentityObservationError, "CANDIDATE_MARKET_MISMATCH"):
            build_observation(report, self.authority, self.scope_authority)

    def test_missing_exact_operational_time_fails_closed(self):
        report = copy.deepcopy(self.report)
        candidate = next(iter(report["by_market"].values()))["review_queue"][0]
        candidate["operational_evaluation"] = {
            "status": "NOT_AVAILABLE_ARTIFACT_REPRODUCTION",
            "evaluated_at_utc": None,
            "time_precision": "NOT_AVAILABLE",
        }
        candidate["timing_precision"]["operational_evaluated_at"] = "NOT_AVAILABLE"
        resign(candidate)
        with self.assertRaisesRegex(CandidateIdentityObservationError, "OPERATIONAL_EVALUATION_NOT_EXACT"):
            build_observation(report, self.authority, self.scope_authority)

    def test_source_pair_change_cannot_silently_keep_resolved_identity(self):
        report = copy.deepcopy(self.report)
        candidate = next(row for result in report["by_market"].values() for row in result["review_queue"] if row["subject"] == "BTC")
        candidate["source_identity_lineage"]["source_pairs"][0]["source_asset_id"] = "ETH/USD"
        resign(candidate)
        packet = build_observation(report, self.authority, self.scope_authority)
        row = next(row for row in packet["observations"] if row["subject"] == "BTC")
        self.assertNotEqual(row["identity"]["status"], ci.RESOLVED)
        self.assertIsNone(row["identity"]["canonical_instrument_id"])

    def test_summary_is_exactly_reconciled(self):
        summary = self.packet["summary"]
        self.assertEqual(summary["candidate_count"], len(self.packet["observations"]))
        self.assertEqual(
            summary["identity_resolved_count"],
            sum(row["identity"]["status"] == ci.RESOLVED for row in self.packet["observations"]),
        )
        self.assertEqual(
            summary["scope_resolved_count"],
            sum(row["account_scope"]["status"] == ci.RESOLVED for row in self.packet["observations"]),
        )

    def test_history_record_retains_exact_validated_packet_and_false_authority(self):
        record = _history_record(self.packet, self.report, TRIGGER_UPSTREAM_WORKFLOW_RUN)
        self.assertEqual(record["candidate_identity_observation"], self.packet)
        self.assertEqual(record["source_observation_packet_sha256"], self.packet["packet_sha256"])
        self.assertEqual(record["observation_trigger_kind"], TRIGGER_UPSTREAM_WORKFLOW_RUN)
        self.assertEqual(record["boundary"]["candidate_validity"], "NOT_EVALUATED_BY_THIS_CONTRACT")
        self.assertEqual(record["boundary"]["money_action"], "NONE")
        self.assertTrue(all(value is False for value in record["authority"].values()))
        self.assertEqual(
            validate_history_record(record, self.report, self.authority, self.scope_authority),
            record,
        )

    def test_history_is_content_addressed_and_identical_run_is_noop(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = write_history_record(
                self.packet, self.report, self.authority, self.scope_authority,
                history_root=root, trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            before = first.read_bytes()
            second = write_history_record(
                self.packet, self.report, self.authority, self.scope_authority,
                history_root=root, trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), before)
            self.assertEqual(len(list(root.rglob("observation-*.json"))), 1)

    def test_manual_and_natural_runs_are_physically_separated(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            natural = write_history_record(
                self.packet, self.report, self.authority, self.scope_authority,
                history_root=root, trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            manual = write_history_record(
                self.packet, self.report, self.authority, self.scope_authority,
                history_root=root, trigger_kind=TRIGGER_MANUAL_WORKFLOW_DISPATCH,
            )
            self.assertNotEqual(natural, manual)
            self.assertIn("upstream_workflow_run", natural.as_posix())
            self.assertIn("manual_workflow_dispatch", manual.as_posix())

    def test_same_day_different_exact_evaluation_is_not_overwritten(self):
        report = copy.deepcopy(self.report)
        current = datetime.fromisoformat(
            report["operational_evaluation"]["evaluated_at_utc"].replace("Z", "+00:00")
        )
        alternative = current + timedelta(seconds=1)
        if alternative.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat() != report["decision_date"]:
            alternative = current - timedelta(seconds=1)
        alternative_text = alternative.isoformat().replace("+00:00", "Z")
        self.assertNotEqual(alternative_text, report["operational_evaluation"]["evaluated_at_utc"])
        self.assertEqual(
            alternative.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat(),
            report["decision_date"],
        )
        report["operational_evaluation"]["evaluated_at_utc"] = alternative_text
        for market in report["by_market"].values():
            for candidate in market["review_queue"]:
                candidate["operational_evaluation"]["evaluated_at_utc"] = alternative_text
                resign(candidate)
        packet = build_observation(report, self.authority, self.scope_authority)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = write_history_record(
                self.packet, self.report, self.authority, self.scope_authority,
                history_root=root, trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            second = write_history_record(
                packet, report, self.authority, self.scope_authority,
                history_root=root, trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_history_rejects_report_candidate_operational_time_drift(self):
        report = copy.deepcopy(self.report)
        report["operational_evaluation"]["evaluated_at_utc"] = "2026-08-25T17:00:00Z"
        with self.assertRaisesRegex(CandidateIdentityObservationError, "OPERATIONAL_TIME_MISMATCH"):
            _history_record(self.packet, report, TRIGGER_UPSTREAM_WORKFLOW_RUN)

    def test_history_rejects_resigned_authority_or_identity_tamper(self):
        record = _history_record(self.packet, self.report, TRIGGER_UPSTREAM_WORKFLOW_RUN)
        record["authority"]["trading_authority"] = True
        record["record_sha256"] = payload_sha256({
            key: value for key, value in record.items() if key != "record_sha256"
        })
        with self.assertRaisesRegex(CandidateIdentityObservationError, "HISTORY_MISMATCH"):
            validate_history_record(record, self.report, self.authority, self.scope_authority)

    def test_history_rejects_unknown_trigger_kind(self):
        with self.assertRaisesRegex(CandidateIdentityObservationError, "TRIGGER_KIND_INVALID"):
            _history_record(self.packet, self.report, "SCHEDULED_SUCCESS")

    def test_workflow_labels_and_commits_append_only_identity_history(self):
        workflow = (ROOT / ".github/workflows/p8-12-dynamic-clock.yml").read_text()
        self.assertIn(
            'candidate_identity_observation.py --observation-trigger-kind "$OBSERVATION_TRIGGER_KIND"',
            workflow,
        )
        self.assertIn("git add evidence/operational/dynamic_clock", workflow)
        self.assertLess(
            workflow.index("python3 clock/run_dynamic_clock.py"),
            workflow.index("python3 identity/candidate_identity_observation.py"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
