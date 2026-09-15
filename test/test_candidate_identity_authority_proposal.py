from __future__ import annotations

import copy
import hashlib
import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.candidate_identity_authority_proposal import (
    AUTHORITY_ALL_FALSE, COMPLETE, INCOMPLETE, KOREA_EVIDENCE_DATE_BASIS,
    CandidateIdentityAuthorityProposalError, _korea_evidence_date, _load_korea_evidence,
    _proposal, build_packet, validate_packet,
)


from identity import canonical_identity as ci
from identity.candidate_identity_gap_inventory import _load_taxonomy, build_inventory
from identity.candidate_identity_observation import DEFAULT_OUTPUT, DEFAULT_REPORT, build_observation


KRX_SOURCE = "KRX 정보데이터시스템 (pykrx)"
DART_SOURCE = "OpenDART (금융감독원)"


def _write_korea_collection(root: Path, collected_for: str, symbol: str = "005930", *,
                            collected_at_utc: str | None = None, include=("krx", "dart")) -> Path:
    """Write a minimal official-collector pair for one KST collection date."""
    day = root / collected_for
    day.mkdir(parents=True, exist_ok=True)
    if collected_at_utc is None:
        prior = dt.date.fromisoformat(collected_for) - dt.timedelta(days=1)
        collected_at_utc = f"{prior.isoformat()}T21:00:33+00:00"
    docs = {
        "krx": {"source": KRX_SOURCE, "stocks": {symbol: {"status": "ok", "name": "삼성전자"}}},
        "dart": {"source": DART_SOURCE, "stocks": {symbol: {"status": "ok", "name": "삼성전자", "corp_code": "00126380"}}},
    }
    for name in include:
        doc = dict(docs[name], collected_for_kst_date=collected_for,
                   collected_at_utc=collected_at_utc, source_tier="Official")
        (day / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return day


class CandidateIdentityAuthorityProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taxonomy = ROOT / "config/crypto_breadth_exclusion_taxonomy.json"
        # The rolling inventory pins its generation-time taxonomy bytes. A
        # legitimate taxonomy change must be tested with an inventory rebuilt
        # from the same current inputs the production consumer revalidates.
        # Keep the committed historical inventory untouched.
        taxonomy, records = _load_taxonomy(cls.taxonomy)
        cls.gaps = build_inventory(
            json.loads(DEFAULT_OUTPUT.read_text()),
            json.loads(DEFAULT_REPORT.read_text()),
            ci.load_authority(), ci.load_scope_authority(), taxonomy, records,
            taxonomy_bytes_sha256=hashlib.sha256(cls.taxonomy.read_bytes()).hexdigest(),
        )
        cls.raw = ROOT / "evidence/crypto/breadth/raw"
        cls.packet = build_packet(cls.gaps, cls.taxonomy, cls.raw)
        # Korea identity evidence binds to the Korea collection date the
        # Dynamic Clock admitted, which differs from the calendar
        # decision_date on weekend/KRX-holiday refreshes.
        cls.korea_evidence_date = json.loads(DEFAULT_REPORT.read_text())["by_market"]["KOREA"]["evidence_as_of"]

    def test_real_gap_population_reconciles(self):
        expected_ids = {
            row["candidate_id"] for row in self.gaps["identity_gaps"]
        }
        proposal_ids = {row["candidate_id"] for row in self.packet["proposals"]}
        expected_count = len(expected_ids)
        self.assertEqual(self.packet["summary"]["gap_count"], expected_count)
        self.assertEqual(self.packet["summary"]["proposal_count"], expected_count)
        self.assertEqual(proposal_ids, expected_ids)
        self.assertEqual(
            sum(self.packet["summary"]["review_status_counts"].values()),
            expected_count,
        )
        self.assertEqual(
            set(self.packet["summary"]["review_status_counts"]),
            {row["review_status"] for row in self.packet["proposals"]},
        )
        # The live gap population is expected to change as identity rows are
        # resolved.  Reconcile every currently-present Crypto proposal to its
        # own exact provider pair instead of requiring DOGE/USD to remain a
        # gap forever.
        completed_crypto = [
            proposal
            for proposal in self.packet["proposals"]
            if proposal["market"] == "CRYPTO"
            and proposal["review_status"] == COMPLETE
        ]
        self.assertTrue(
            completed_crypto,
            "expected at least one COMPLETE Crypto proposal in the live gap population",
        )
        for proposal in completed_crypto:
            self.assertEqual(
                proposal["proposed_rows"]["source_alias"]["source_asset_id"],
                proposal["subject"],
            )

    def test_mechanical_proposals_remain_unratified_and_create_no_authority(self):
        self.assertEqual(self.packet["summary"]["canonical_authority_rows_created"], 0)
        self.assertFalse(self.packet["policy_boundary"]["proposal_is_identity_authority"])
        self.assertEqual(self.packet["authority"], AUTHORITY_ALL_FALSE)
        for row in self.packet["proposals"]:
            self.assertEqual(row["authority"], AUTHORITY_ALL_FALSE)
            self.assertNotEqual(row.get("proposal_status"), "RATIFIED")

    def test_exact_kraken_pair_and_taxonomy_are_both_required(self):
        gap = copy.deepcopy(self.gaps["identity_gaps"][0])
        gap["provider_pair_diagnostics"][0]["diagnostic_status"] = "TAXONOMY_RECORD_NOT_FOUND"
        row = _proposal(gap, {})
        self.assertEqual(row["review_status"], INCOMPLETE)

    def test_provider_display_alias_may_differ_when_exact_key_and_structured_identity_match(self):
        gap = {
            "candidate_id": "doge",
            "market": "CRYPTO",
            "subject": "DOGE/USD",
            "provider_pair_diagnostics": [{
                "source_asset_id": "DOGE/USD",
                "taxonomy_canonical_asset_id": "DOGE",
                "diagnostic_status": "MECHANICAL_TAXONOMY_SYMBOL_MATCH_DIAGNOSTIC",
                "source_name": "kraken_asset_pairs",
                "taxonomy_effective_from": "2026-08-22",
            }],
        }
        pairs = {
            "DOGE/USD": {
                "wsname": "XDG/USD",
                "base": "DOGE",
                "quote": "USD",
                "status": "online",
            }
        }
        self.assertEqual(_proposal(gap, pairs)["review_status"], COMPLETE)
        pairs["DOGE/USD"]["base"] = "NOT_DOGE"
        self.assertEqual(_proposal(gap, pairs)["review_status"], INCOMPLETE)

    def test_resigned_source_gap_tamper_is_independently_rejected(self):
        gaps = copy.deepcopy(self.gaps)
        gaps["identity_gaps"][0]["subject"] = "TAMPERED"
        unsigned = dict(gaps)
        unsigned.pop("packet_sha256", None)
        import hashlib
        gaps["packet_sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        with self.assertRaisesRegex(
            CandidateIdentityAuthorityProposalError,
            "SOURCE_GAP_INVENTORY_INDEPENDENT_VALIDATION_FAILED",
        ):
            build_packet(gaps, self.taxonomy, self.raw)

    def test_korea_direct_review_uses_two_official_sources_and_stays_unclassified(self):
        korea_gap = next(x for x in self.gaps["identity_gaps"] if x["market"] == "KOREA")
        symbol = korea_gap["subject"]
        row = next(
            x for x in self.packet["proposals"]
            if x["market"] == "KOREA" and x["subject"] == symbol
        )
        self.assertEqual(row["subject"], symbol)
        self.assertEqual(row["review_status"], COMPLETE)
        evidence = self.packet["source_korea_identity_evidence"][korea_gap["candidate_id"]]
        self.assertEqual(
            row["proposed_rows"]["issuer"]["canonical_issuer_id"],
            f"DART:{evidence['corp_code']}",
        )
        self.assertEqual(row["proposed_rows"]["instrument"]["instrument_type"], "OTHER_UNCLASSIFIED")
        self.assertEqual(row["proposed_rows"]["listing"]["listing_id"], f"XKRX:{symbol}")
        self.assertEqual(row["source_evidence"]["krx"]["source"], "KRX 정보데이터시스템 (pykrx)")
        self.assertEqual(row["source_evidence"]["dart"]["source"], "OpenDART (금융감독원)")

    def test_korea_cross_source_name_mismatch_fails_closed(self):
        korea_gap = next(x for x in self.gaps["identity_gaps"] if x["market"] == "KOREA")
        symbol = korea_gap["subject"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day = root / self.korea_evidence_date
            day.mkdir()
            for name in ("krx.json", "dart.json"):
                shutil.copy2(ROOT / "data" / self.korea_evidence_date / name, day / name)
            dart = json.loads((day / "dart.json").read_text())
            dart["stocks"][symbol]["name"] = "다른회사"
            (day / "dart.json").write_text(json.dumps(dart, ensure_ascii=False))
            with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "KOREA_CROSS_SOURCE_NAME_MISMATCH"):
                build_packet(self.gaps, self.taxonomy, self.raw, market_data_root=root)

    def test_korea_future_collector_timestamp_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day = root / self.korea_evidence_date
            day.mkdir()
            for name in ("krx.json", "dart.json"):
                shutil.copy2(ROOT / "data" / self.korea_evidence_date / name, day / name)
            krx = json.loads((day / "krx.json").read_text())
            future_date = dt.date.fromisoformat(self.gaps["decision_date"]) + dt.timedelta(days=1)
            krx["collected_at_utc"] = future_date.isoformat() + "T00:00:00Z"
            (day / "krx.json").write_text(json.dumps(krx, ensure_ascii=False))
            with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "KOREA_KRX_EVIDENCE_INVALID"):
                build_packet(self.gaps, self.taxonomy, self.raw, market_data_root=root)

    # -- Non-trading-day refresh (P8-12 runs 2026-09-12/13 regression) -----

    def _refresh_fixture(self, td: Path, decision_date: str, korea_evidence_as_of: str | None = None):
        """Rebuild report -> observation -> gap inventory for another refresh date."""
        report = json.loads(DEFAULT_REPORT.read_text())
        report["decision_date"] = decision_date
        if korea_evidence_as_of is not None:
            report["by_market"]["KOREA"]["evidence_as_of"] = korea_evidence_as_of
        authority, scope = ci.load_authority(), ci.load_scope_authority()
        observation = build_observation(report, authority, scope)
        taxonomy, records = _load_taxonomy(self.taxonomy)
        gaps = build_inventory(
            observation, report, authority, scope, taxonomy, records,
            taxonomy_bytes_sha256=hashlib.sha256(self.taxonomy.read_bytes()).hexdigest(),
        )
        report_path, observation_path = td / "report.json", td / "observation.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False))
        observation_path.write_text(json.dumps(observation, ensure_ascii=False))
        return gaps, {"report_path": report_path, "observation_path": observation_path}

    def test_weekend_refresh_binds_korea_identity_to_the_dynamic_clock_korea_evidence_date(self):
        if not any(x["market"] == "KOREA" for x in self.gaps["identity_gaps"]):
            self.skipTest("no Korea identity gap in the live population")
        evidence = dt.date.fromisoformat(self.korea_evidence_date)
        saturday = evidence + dt.timedelta(days=1)
        while saturday.weekday() != 5:
            saturday += dt.timedelta(days=1)
        with tempfile.TemporaryDirectory() as td:
            gaps, paths = self._refresh_fixture(Path(td), saturday.isoformat())
            self.assertEqual(gaps["decision_date"], saturday.isoformat())
            packet = build_packet(gaps, self.taxonomy, self.raw, **paths)
            korea = packet["source_korea_identity_evidence"]
            self.assertTrue(korea)
            for row in korea.values():
                self.assertEqual(row["evidence_date"], self.korea_evidence_date)
                self.assertEqual(row["evidence_date_basis"], KOREA_EVIDENCE_DATE_BASIS)
                self.assertEqual(row["krx"]["path"], f"data/{self.korea_evidence_date}/krx.json")
                self.assertEqual(row["dart"]["path"], f"data/{self.korea_evidence_date}/dart.json")
            self.assertEqual(validate_packet(packet, gaps, self.taxonomy, self.raw, **paths), packet)

            # The same weekend refresh with no Korea collection available
            # still fails closed; there is no hidden fallback to data/.
            with tempfile.TemporaryDirectory() as empty:
                with self.assertRaisesRegex(
                    CandidateIdentityAuthorityProposalError,
                    "KOREA_IDENTITY_EVIDENCE_UNAVAILABLE_OR_INVALID",
                ):
                    build_packet(gaps, self.taxonomy, self.raw, market_data_root=Path(empty), **paths)

    def test_korea_evidence_date_resolves_saturday_sunday_and_krx_holiday_refreshes(self):
        # (refresh KST decision_date, Korea collection admitted by the clock)
        cases = [
            ("2026-09-12", "2026-09-11"),  # Saturday -> Friday collection
            ("2026-09-13", "2026-09-11"),  # Sunday -> Friday collection
            ("2026-09-24", "2026-09-23"),  # KRX Chuseok holiday (Thu) -> Wednesday
            ("2026-09-27", "2026-09-23"),  # Sunday after Chuseok -> Wednesday
        ]
        for decision, evidence in cases:
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _write_korea_collection(root, evidence)
                report = {"decision_date": decision, "by_market": {"KOREA": {"evidence_as_of": evidence}}}
                resolved = _korea_evidence_date(report, decision)
                self.assertEqual(resolved, evidence)
                row = _load_korea_evidence(root, decision, "005930", evidence_date=resolved)
                self.assertEqual(row["evidence_date"], evidence)
                self.assertEqual(row["evidence_date_basis"], KOREA_EVIDENCE_DATE_BASIS)
                self.assertEqual(row["krx"]["path"], "external_fixture/krx.json")
                self.assertEqual(row["corp_code"], "00126380")
                self.assertFalse((root / decision).exists())
                # Without the resolved date the calendar refresh date is used
                # and, having no collection, fails closed exactly as before.
                with self.assertRaisesRegex(
                    CandidateIdentityAuthorityProposalError,
                    "KOREA_IDENTITY_EVIDENCE_UNAVAILABLE_OR_INVALID",
                ):
                    _load_korea_evidence(root, decision, "005930")

    def test_korea_evidence_date_is_never_absent_malformed_or_future(self):
        E = CandidateIdentityAuthorityProposalError
        cases = [
            ({"by_market": {}}, "KOREA_IDENTITY_EVIDENCE_UNAVAILABLE_OR_INVALID"),
            ({"by_market": {"KOREA": {"evidence_as_of": None}}}, "KOREA_IDENTITY_EVIDENCE_UNAVAILABLE_OR_INVALID"),
            ({"by_market": {"KOREA": {"evidence_as_of": "20260911"}}}, "KOREA_EVIDENCE_DATE_INVALID"),
            ({"by_market": {"KOREA": {"evidence_as_of": 20260911}}}, "KOREA_EVIDENCE_DATE_INVALID"),
            ({"by_market": {"KOREA": {"evidence_as_of": "2026-09-14"}}}, "KOREA_EVIDENCE_DATE_FUTURE_DATED"),
        ]
        for report, code in cases:
            with self.subTest(report=report):
                with self.assertRaisesRegex(E, code):
                    _korea_evidence_date(report, "2026-09-13")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_korea_collection(root, "2026-09-14")
            with self.assertRaisesRegex(E, "KOREA_EVIDENCE_DATE_FUTURE_DATED"):
                _load_korea_evidence(root, "2026-09-13", "005930", evidence_date="2026-09-14")

    def test_missing_korea_evidence_on_a_trading_day_still_fails_closed_without_fallback(self):
        E = CandidateIdentityAuthorityProposalError
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_korea_collection(root, "2026-09-11")  # prior Friday collection exists
            report = {"by_market": {"KOREA": {"evidence_as_of": "2026-09-14"}}}
            resolved = _korea_evidence_date(report, "2026-09-14")  # Monday trading day
            with self.assertRaisesRegex(E, "KOREA_IDENTITY_EVIDENCE_UNAVAILABLE_OR_INVALID"):
                _load_korea_evidence(root, "2026-09-14", "005930", evidence_date=resolved)
            with self.assertRaisesRegex(E, "KOREA_IDENTITY_EVIDENCE_UNAVAILABLE_OR_INVALID"):
                _load_korea_evidence(root, "2026-09-14", "005930")
            # One of the two independent official sources missing is still missing.
            _write_korea_collection(root, "2026-09-14", include=("krx",))
            with self.assertRaisesRegex(E, "KOREA_IDENTITY_EVIDENCE_UNAVAILABLE_OR_INVALID"):
                _load_korea_evidence(root, "2026-09-14", "005930", evidence_date=resolved)

    def test_resolved_korea_evidence_must_match_its_date_and_be_available_by_refresh_end(self):
        E = CandidateIdentityAuthorityProposalError
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day = _write_korea_collection(root, "2026-09-11")
            krx = json.loads((day / "krx.json").read_text(encoding="utf-8"))
            krx["collected_for_kst_date"] = "2026-09-12"
            (day / "krx.json").write_text(json.dumps(krx, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(E, "KOREA_KRX_EVIDENCE_INVALID"):
                _load_korea_evidence(root, "2026-09-13", "005930", evidence_date="2026-09-11")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Collected after the end of the KST refresh date (2026-09-13T15:00Z).
            _write_korea_collection(root, "2026-09-11", collected_at_utc="2026-09-13T15:00:01+00:00")
            with self.assertRaisesRegex(E, "KOREA_KRX_EVIDENCE_INVALID"):
                _load_korea_evidence(root, "2026-09-13", "005930", evidence_date="2026-09-11")

    def test_korea_subject_must_equal_the_exact_provider_symbol(self):
        gap = copy.deepcopy(next(x for x in self.gaps["identity_gaps"] if x["market"] == "KOREA"))
        source_id = gap["provider_pair_diagnostics"][0]["source_asset_id"]
        evidence = self.packet["source_korea_identity_evidence"][gap["candidate_id"]]
        self.assertEqual(evidence["symbol"], source_id)
        gap["subject"] = f"NOT-{source_id}"
        row = _proposal(gap, {}, evidence)
        self.assertEqual(row["review_status"], INCOMPLETE)
        self.assertEqual(row["reason_codes"], ["KOREA_SUBJECT_SOURCE_ID_MISMATCH"])

    def test_no_canonical_authority_configuration_is_modified_or_embedded(self):
        self.assertFalse(self.packet["policy_boundary"]["canonical_config_modified"])
        proposal_text = json.dumps(self.packet["proposals"])
        self.assertNotIn('"approval_status": "RATIFIED"', proposal_text)
        self.assertNotIn('"ratified_at"', proposal_text)

    def test_validator_rebuilds_and_rejects_resigned_tamper(self):
        packet = copy.deepcopy(self.packet)
        packet["proposals"][0]["proposal_status"] = "RATIFIED"
        packet["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "PROPOSAL_PACKET_MISMATCH"):
            validate_packet(packet, self.gaps, self.taxonomy, self.raw)

    def test_validator_replays_the_advertised_capture_not_a_later_capture(self):
        decision = dt.date.fromisoformat(self.gaps["decision_date"])
        captures = sorted(
            path.name for path in self.raw.iterdir()
            if path.is_dir()
            and (path / "_manifest.json").is_file()
            and dt.date.fromisoformat(path.name) <= decision
        )
        self.assertGreaterEqual(len(captures), 2)
        packet = build_packet(
            self.gaps,
            self.taxonomy,
            self.raw,
            kraken_capture_date=captures[-2],
        )
        self.assertEqual(packet["source_kraken_capture"]["capture_date"], captures[-2])
        self.assertEqual(validate_packet(packet, self.gaps, self.taxonomy, self.raw), packet)

    def test_missing_eligible_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td)
            with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "KRAKEN_CAPTURE_NOT_AVAILABLE"):
                build_packet(self.gaps, self.taxonomy, empty)

    def test_output_is_deterministic(self):
        self.assertEqual(self.packet, build_packet(copy.deepcopy(self.gaps), self.taxonomy, self.raw))

    def test_run_all_registers_proposal_contract(self):
        self.assertIn('"test/test_candidate_identity_authority_proposal.py"', (ROOT / "run_all.py").read_text())


if __name__ == "__main__":
    unittest.main()
