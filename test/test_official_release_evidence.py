#!/usr/bin/env python3
"""P4-04 official-release evidence normalization regression (offline only)."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors"))

import msft_azure_cc as MSFT                                     # noqa: E402
import tsmc_monthly as TSMC                                      # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "official_release_evidence", ROOT / "bridge" / "official_release_evidence.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MANIFEST = json.loads(
    (ROOT / "collectors" / "fixtures" / "azure_cc_MANIFEST.json").read_text(
        encoding="utf-8"
    )
)


def tsmc_capture(kind="LIVE_OFFICIAL_CAPTURE", **changes):
    value = {
        "source_url": "https://investor.tsmc.com/english/monthly-revenue/2026",
        "source_sha256": "a" * 64,
        "retrieved_at_utc": "2026-08-15T01:00:00Z",
        "available_at": "2026-08-15",
        "capture_kind": kind,
    }
    value.update(changes)
    return value


def parsed_msft_observation(entry):
    html = (
        ROOT / "collectors" / "fixtures" / entry["fixture_file"]
    ).read_text(encoding="utf-8")
    parser = MSFT.TableCollector()
    parser.feed(html)
    rows, row_index, period_end, problems = MSFT.select_observation(parser.tables)
    if problems or rows is None:
        raise AssertionError(problems)
    bound, problems = MSFT.bind_columns(MSFT.build_header(rows, row_index), rows[row_index])
    if problems or bound is None:
        raise AssertionError(problems)
    return {
        "accession": entry["accession"],
        "filing_date": entry["filing_date"],
        "period_end": period_end,
        "exhibit": entry["exhibit"],
        "azure_cc_growth_pct": bound["cc"],
    }


def msft_capture(entry, **changes):
    value = {
        "source_url": entry["exhibit_url"],
        "source_sha256": entry["exhibit_sha256"],
        "slice_sha256": entry["slice_sha256"],
        "retrieved_at_utc": "2026-07-29T21:00:00Z",
        "available_at": entry["filing_date"],
        "capture_kind": "VERBATIM_SOURCE_SLICE",
        "verbatim_substring_of_source": entry["verbatim_substring_of_exhibit"],
        "accession": entry["accession"],
        "exhibit_document": entry["exhibit"],
    }
    value.update(changes)
    return value


def rehash_bundle(value):
    changed = copy.deepcopy(value)
    changed.pop("bundle_sha256", None)
    changed["bundle_sha256"] = MODULE.payload_sha256(changed)
    return changed


class OfficialReleaseEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.contract = MODULE.load_contract()

    def test_registry_and_authority_do_not_ratify_hierarchy_or_fallback(self):
        self.assertEqual(self.contract["source_hierarchy_status"], "UNRATIFIED")
        self.assertFalse(self.contract["automatic_fallback_authorized"])
        self.assertEqual(
            set(self.contract["profiles"]),
            {"tsmc_ir_monthly_revenue", "msft_official_earnings_release"},
        )
        self.assertEqual(
            self.contract["authority"],
            {
                "evidence_only": True,
                "source_ranking_authorized": False,
                "interpretation_authorized": False,
                "rule_evaluation_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
            },
        )

    def test_tsmc_live_capture_normalizes_raw_monthly_and_total_yoy(self):
        normalized = TSMC.from_fixture(published_at="2026-08-15")
        envelopes = MODULE.tsmc_monthly_envelopes(
            normalized, tsmc_capture(), self.contract
        )
        self.assertEqual(len(envelopes), 8)
        self.assertTrue(all(x["status"] == MODULE.EVIDENCE_AVAILABLE for x in envelopes))
        july = next(
            x for x in envelopes
            if x["measurement_identity"].endswith("monthly YoY")
            and x["economic_period_end"] == "2026-07-31"
        )
        self.assertEqual(july["observation"]["raw_value"], "44.7%")
        self.assertEqual(july["observation"]["numeric_value"], "44.7")
        self.assertEqual(july["source_identity"]["identity_kind"], "company_ir_web")
        self.assertEqual(
            july["audit_provenance"]["source_locator"]["column"], "YoY Change"
        )
        total = envelopes[-1]
        self.assertEqual(total["observation"]["raw_value"], "37.0%")
        self.assertEqual(total["audit_provenance"]["source_locator"]["row"], "Total")
        serialized = json.dumps(envelopes, ensure_ascii=False)
        self.assertNotIn('"verdict"', serialized)
        self.assertNotIn('"action"', serialized)

    def test_tsmc_tracked_fixture_and_unobserved_date_are_blocked(self):
        normalized = TSMC.from_fixture()
        envelopes = MODULE.tsmc_monthly_envelopes(
            normalized,
            tsmc_capture(
                "SYNTHETIC_FIXTURE", available_at=None, source_sha256="b" * 64
            ),
            self.contract,
        )
        self.assertTrue(all(x["status"] == MODULE.EVIDENCE_BLOCKED for x in envelopes))
        for blocker in (
            MODULE.CAPTURE_NOT_LIVE_OR_VERBATIM,
            MODULE.AVAILABLE_AT_UNOBSERVED,
            MODULE.COLLECTOR_NOT_DECISION_READY,
        ):
            self.assertIn(blocker, envelopes[0]["blocked_by"])
        self.assertIsNone(envelopes[0]["observation"])

    def test_msft_verbatim_official_release_slice_normalizes_actual_fixture(self):
        entry = MANIFEST["captured"][0]
        observation = parsed_msft_observation(entry)
        envelope = MODULE.msft_azure_envelope(
            observation, msft_capture(entry), self.contract
        )
        self.assertEqual(envelope["status"], MODULE.EVIDENCE_AVAILABLE)
        self.assertEqual(envelope["subject"], "MSFT")
        self.assertTrue(envelope["observation"]["raw_value"].endswith("%"))
        self.assertEqual(
            envelope["source_identity"]["identity_kind"],
            "company_official_release_sec_exhibit",
        )
        self.assertEqual(
            envelope["source_identity"]["source_sha256"], entry["exhibit_sha256"]
        )
        self.assertEqual(
            envelope["audit_provenance"]["slice_sha256"], entry["slice_sha256"]
        )
        self.assertTrue(envelope["audit_provenance"]["verbatim_substring_of_source"])

    def test_source_identity_slice_and_availability_fail_closed(self):
        entry = MANIFEST["captured"][0]
        observation = parsed_msft_observation(entry)
        blocked = MODULE.msft_azure_envelope(
            observation,
            msft_capture(
                entry,
                available_at=None,
                verbatim_substring_of_source=False,
                slice_sha256="bad",
            ),
            self.contract,
        )
        self.assertEqual(blocked["status"], MODULE.EVIDENCE_BLOCKED)
        self.assertIn(MODULE.AVAILABLE_AT_UNOBSERVED, blocked["blocked_by"])
        self.assertIn(MODULE.SOURCE_SLICE_NOT_VERBATIM, blocked["blocked_by"])
        self.assertIsNone(blocked["observation"])

        with self.assertRaisesRegex(MODULE.OfficialReleaseEvidenceError, "OUTSIDE_PROFILE"):
            MODULE.msft_azure_envelope(
                observation,
                msft_capture(entry, source_url="https://example.com/release.htm"),
                self.contract,
            )
        with self.assertRaisesRegex(MODULE.OfficialReleaseEvidenceError, "IDENTITY_MISMATCH"):
            MODULE.msft_azure_envelope(
                observation,
                msft_capture(entry, accession="0000000000-00-000000"),
                self.contract,
            )

    def test_revision_conflict_is_blocked_and_identical_duplicate_is_deduplicated(self):
        envelope = MODULE.tsmc_monthly_envelopes(
            TSMC.from_fixture(published_at="2026-08-15"),
            tsmc_capture(),
            self.contract,
        )[0]
        self.assertEqual(len(MODULE.reconcile([envelope, copy.deepcopy(envelope)])), 1)

        revision = copy.deepcopy(envelope)
        revision["source_identity"]["source_sha256"] = "f" * 64
        revision["observation"]["raw_value"] = "99.9%"
        revision["observation"]["numeric_value"] = "99.9"
        result = MODULE.reconcile([envelope, revision])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], MODULE.EVIDENCE_BLOCKED)
        self.assertEqual(result[0]["blocked_by"], [MODULE.REVISION_AUTHORITY_UNRESOLVED])

        empty_revision = copy.deepcopy(result[0])
        empty_revision["audit_provenance"][
            "revision_candidate_source_sha256"
        ] = []
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseEvidenceError,
            "ENVELOPE_REVISION_STATE_MISMATCH",
        ):
            MODULE.validate_envelope(empty_revision, self.contract)
        self.assertIsNone(result[0]["source_identity"])
        self.assertIsNone(result[0]["observation"])

    def test_unresolved_and_bundle_preserve_missing_and_authority_boundaries(self):
        unresolved = MODULE.unresolved_envelope(
            "TSM", "TSMC consolidated net revenue monthly YoY", "2026-08-31"
        )
        self.assertEqual(unresolved["status"], MODULE.EVIDENCE_UNRESOLVED)
        self.assertIsNone(unresolved["observation"])
        value = MODULE.bundle([unresolved], self.contract)
        self.assertEqual(value["summary"][MODULE.EVIDENCE_UNRESOLVED], 1)
        self.assertEqual(value["source_hierarchy_status"], "UNRATIFIED")
        self.assertFalse(value["automatic_fallback_authorized"])
        self.assertEqual(len(value["bundle_sha256"]), 64)
        self.assertEqual(
            value["bundle_sha256"],
            MODULE.payload_sha256({
                key: item for key, item in value.items() if key != "bundle_sha256"
            }),
        )

    def test_standalone_bundle_validator_accepts_all_envelope_states(self):
        entry = MANIFEST["captured"][0]
        envelopes = [
            MODULE.tsmc_monthly_envelopes(
                TSMC.from_fixture(published_at="2026-08-15"),
                tsmc_capture(),
                self.contract,
            )[0],
            MODULE.msft_azure_envelope(
                parsed_msft_observation(entry), msft_capture(entry), self.contract
            ),
            MODULE.unresolved_envelope(
                "TSM", "future official observation", "2026-08-31"
            ),
        ]
        value = MODULE.bundle(envelopes, self.contract)
        checked = MODULE.validate_bundle(copy.deepcopy(value), self.contract)
        self.assertEqual(MODULE.canonical_json(checked), MODULE.canonical_json(value))

    def test_standalone_bundle_validator_rejects_rehashed_semantic_drift(self):
        value = MODULE.bundle(
            MODULE.tsmc_monthly_envelopes(
                TSMC.from_fixture(published_at="2026-08-15"),
                tsmc_capture(),
                self.contract,
            ),
            self.contract,
        )
        changed = copy.deepcopy(value)
        changed["envelopes"][0]["observation"]["numeric_value"] = "99.9"
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseEvidenceError,
            "ENVELOPE_OBSERVATION_VALUE_MISMATCH",
        ):
            MODULE.validate_bundle(rehash_bundle(changed), self.contract)

        changed = copy.deepcopy(value)
        changed["summary"][MODULE.EVIDENCE_AVAILABLE] -= 1
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseEvidenceError,
            "BUNDLE_SUMMARY_OR_AUTHORITY_MISMATCH",
        ):
            MODULE.validate_bundle(rehash_bundle(changed), self.contract)

        changed = copy.deepcopy(value)
        changed["envelopes"][-1]["observation"]["row_label_raw"] = "Total"
        changed["envelopes"][-1]["observation"][
            "decision_column_identity"
        ] = "Total YoY Change"
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseEvidenceError,
            "ENVELOPE_OBSERVATION_IDENTITY_MISMATCH",
        ):
            MODULE.validate_bundle(rehash_bundle(changed), self.contract)

        changed = copy.deepcopy(value)
        changed["authority"]["trading_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseEvidenceError,
            "BUNDLE_SUMMARY_OR_AUTHORITY_MISMATCH",
        ):
            MODULE.validate_bundle(rehash_bundle(changed), self.contract)

    def test_validator_binds_msft_release_url_to_accession_and_exhibit(self):
        entry = MANIFEST["captured"][0]
        envelope = MODULE.msft_azure_envelope(
            parsed_msft_observation(entry), msft_capture(entry), self.contract
        )
        envelope["source_identity"]["source_url"] = (
            "https://www.sec.gov/Archives/edgar/data/789019/"
            "000119312526191457/msft-ex99_1.htm"
        )
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseEvidenceError,
            "ENVELOPE_RELEASE_IDENTITY_INVALID",
        ):
            MODULE.validate_envelope(envelope, self.contract)

    def test_reconcile_rejects_rehashed_envelope_authority_expansion(self):
        envelope = MODULE.tsmc_monthly_envelopes(
            TSMC.from_fixture(published_at="2026-08-15"),
            tsmc_capture(),
            self.contract,
        )[0]
        envelope["consumable"] = False
        with self.assertRaisesRegex(
            MODULE.OfficialReleaseEvidenceError,
            "ENVELOPE_AVAILABLE_STATE_MISMATCH",
        ):
            MODULE.reconcile([envelope], self.contract)


if __name__ == "__main__":
    unittest.main()
