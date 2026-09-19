#!/usr/bin/env python3
"""Natural-schedule-equivalent briefing_core/2 acceptance tests."""

from __future__ import annotations

import argparse
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from briefing_core import chain  # noqa: E402
from briefing_core import major_events  # noqa: E402
from briefing_core import paper_signal  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PORTAL_PRODUCER = _load(
    "briefing_core_v2_portal_producer",
    ROOT / ".github/scripts/validated_briefing_portal_producer.py",
)


class BtcPriorReferenceUnknownClaims(unittest.TestCase):
    def claims(self, packet):
        return {
            row["claim_id"]: row
            for row in chain._delivery_claims(packet, "packet.json")
        }

    def test_actual_20260909_packet_keeps_prior_dates_historical(self):
        relative = "evidence/daily_briefing/morning/2026-09-09/rev-001/packet.json"
        packet = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        claims = self.claims(packet)

        for component in ("trend", "risk"):
            claim = claims[f"freshness.crypto.btc_{component}_finalized_date"]
            self.assertEqual(claim["kind"], "UNKNOWN")
            self.assertEqual(claim["status"], "UNKNOWN")
            self.assertEqual(claim["source_ref_paths"], [])
            self.assertIn("current BTC", claim["statement"])
            self.assertIn("UNKNOWN", claim["statement"])
            self.assertIn("2026-09-07", claim["statement"])
            self.assertIn("historical prior reference", claim["statement"])
            self.assertIn("as_of_date is null", claim["statement"])

    def test_missing_or_malformed_prior_reference_stays_current_unknown(self):
        for prior in (
            None,
            {},
            "",
            "   ",
            "not-a-date",
            "2026-02-30",
            "20260907",
            "2026-09-07",
            ["2026-09-07"],
            {"measurement_date": ""},
            {"measurement_date": "   "},
            {"measurement_date": "not-a-date"},
            {"measurement_date": "2026-02-30"},
            {"measurement_date": "20260907"},
            {"measurement_date": None},
            {"measurement_date": 20260907},
        ):
            with self.subTest(prior=prior):
                frozen = {"kind": "absent"}
                if prior is not None:
                    frozen["prior_confirmed_reference"] = prior
                packet = {
                    "components": [
                        {"component_id": "BTC_TREND", "as_of_date": None, "packet": None},
                        {"component_id": "BTC_RISK", "as_of_date": None, "packet": None},
                    ],
                    "frozen_sources": {"BTC_TREND": frozen, "BTC_RISK": frozen},
                }
                claims = self.claims(packet)
                for component in ("trend", "risk"):
                    claim = claims[f"freshness.crypto.btc_{component}_finalized_date"]
                    self.assertEqual(claim["kind"], "UNKNOWN")
                    self.assertEqual(claim["source_ref_paths"], [])
                    self.assertIn("current BTC", claim["statement"])
                    self.assertIn("UNKNOWN", claim["statement"])
                    self.assertIn("as_of_date is null", claim["statement"])
                    self.assertNotIn("historical prior reference", claim["statement"])
                    self.assertNotIn("capture vintage", claim["statement"])

    def test_finalized_date_fact_statements_are_unchanged(self):
        packet = {
            "components": [
                {
                    "component_id": "BTC_TREND",
                    "as_of_date": "2026-09-09",
                    "packet": {"latest_finalized_day": "2026-09-08"},
                },
                {
                    "component_id": "BTC_RISK",
                    "as_of_date": "2026-09-09",
                    "packet": {"risk_point": {"as_of_date": "2026-09-08"}},
                },
            ],
            "frozen_sources": {
                "BTC_TREND": {"prior_confirmed_reference": {"measurement_date": "2026-09-07"}},
                "BTC_RISK": {"prior_confirmed_reference": {"measurement_date": "2026-09-07"}},
            },
        }
        claims = self.claims(packet)
        trend = claims["freshness.crypto.btc_trend_finalized_date"]
        risk = claims["freshness.crypto.btc_risk_finalized_date"]
        self.assertEqual(trend["kind"], "FACT")
        self.assertEqual(trend["status"], "VERIFIED")
        self.assertEqual(trend["source_ref_paths"], ["packet.json"])
        self.assertEqual(
            trend["statement"],
            "The BTC trend measurement uses finalized daily closes through 2026-09-08.",
        )
        self.assertEqual(risk["kind"], "FACT")
        self.assertEqual(risk["status"], "VERIFIED")
        self.assertEqual(risk["source_ref_paths"], ["packet.json"])
        self.assertEqual(
            risk["statement"],
            "The BTC risk measurement uses finalized daily closes through 2026-09-08.",
        )


class BriefingCoreV2Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Atlas Test"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "atlas@example.invalid"],
            cwd=self.repo,
            check=True,
        )
        self.generation = "a" * 64
        self.packet_path = "evidence/daily_briefing/morning/2026-09-02/rev-001/packet.json"
        self.briefing_path = "evidence/daily_briefing/morning/2026-09-02/rev-001/briefing.md"
        dart_path = self.repo / "data/latest_dart_content.json"
        sec_path = self.repo / "data/latest_sec_content.json"
        dart_path.parent.mkdir(parents=True)
        dart_path.write_text('{"provider":"dart"}\n', encoding="utf-8")
        sec_path.write_text('{"provider":"sec"}\n', encoding="utf-8")
        packet = {
            "schema_version": 1,
            "contract_version": "daily_orchestrator/6",
            "output_schema_version": "daily_briefing_packet/1",
            "slot": "morning",
            "decision_date": "2026-09-02",
            "generated_at": "2026-09-01T22:05:00Z",
            "capture_mode": "provider_free_aggregation_of_persisted_evidence_only",
            "authority": {
                "aggregation_only": True,
                "component_build_authorized": True,
                "order_generation_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
            },
            "component_status_counts": {"READY": 5, "DATA_BLOCKED": 2},
            "components": [
                {
                    "component_id": "STEP0_READ_MODEL_HEALTH",
                    "status": "READY",
                    "packet": {"generation": {"generation_id": self.generation}},
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "THREE_MARKET_REGIME_HEADER",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "FREE_MARKET_DATA",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "DART_FILING_CONTENT",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": "data/latest_dart_content.json",
                    "source_packet_sha256": chain.digest_bytes(dart_path.read_bytes()),
                },
                {
                    "component_id": "SEC_FILING_CONTENT",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": "data/latest_sec_content.json",
                    # Deliberate optional adapter defect: must isolate to news.
                    "source_packet_sha256": "b" * 64,
                },
                {
                    "component_id": "OFFICIAL_RELEASE_SUMMARY",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "US_BREADTH_MEMBERSHIP",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
            ],
            "frozen_sources": {},
            "unresolved_boundaries": [],
        }
        packet["packet_sha256"] = chain.digest(packet)
        packet_file = self.repo / self.packet_path
        packet_file.parent.mkdir(parents=True)
        packet_file.write_bytes(chain.canonical(packet) + b"\n")
        briefing_file = self.repo / self.briefing_path
        briefing_file.write_text("# Fixture briefing\n\nNo order is authorized.\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "pinned source"], cwd=self.repo, check=True)
        self.source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, source_commit=None):
        return chain.build_input_envelope(
            self.repo,
            source_commit=source_commit or self.source_commit,
            packet_path=self.packet_path,
            briefing_path=self.briefing_path,
            decision_date="2026-09-02",
            slot="morning",
            registry_path=ROOT / "config/briefing_module_registry_v2.json",
        )

    def source_packet(self):
        return json.loads(
            subprocess.check_output(
                ["git", "show", f"{self.source_commit}:{self.packet_path}"],
                cwd=self.repo,
            )
        )

    def write_packet(self, packet):
        packet.pop("packet_sha256", None)
        packet["packet_sha256"] = chain.digest(packet)
        (self.repo / self.packet_path).write_bytes(chain.canonical(packet) + b"\n")

    def write_generation_source(self, path, generation_id):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            chain.canonical({"generation": {"generation_id": generation_id}}) + b"\n"
        )

    def commit_changes(self, message):
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.repo, check=True)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def event_registry(self):
        return json.loads(
            (ROOT / "validation/fixtures/briefing_major_events/2026-09-02-am.json")
            .read_text(encoding="utf-8")
        )

    def source_with_event_registry(self):
        registry = self.event_registry()
        body = chain.canonical(registry) + b"\n"
        root = self.repo / "evidence/briefing_events/2026-09-02/morning"
        revision = root / "rev-001/registry.json"
        revision.parent.mkdir(parents=True)
        revision.write_bytes(body)
        index = {
            "schema_version": "major_event_registry_index/1",
            "latest_revision": 1,
            "revisions": [{
                "revision": 1,
                "path": "rev-001/registry.json",
                "sha256": chain.digest_bytes(body),
            }],
        }
        (root / "index.json").write_bytes(chain.canonical(index) + b"\n")
        subprocess.run(["git", "add", "evidence/briefing_events"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "major event registry"], cwd=self.repo, check=True)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def test_exact_source_commit_and_generation_are_frozen(self):
        envelope = self.envelope()
        old_hash = envelope["source_refs"][0]["sha256"]
        (self.repo / self.packet_path).write_text("{}\n", encoding="utf-8")
        rebuilt = self.envelope()
        self.assertEqual(rebuilt["source_commit"], self.source_commit)
        self.assertEqual(rebuilt["generation_id"], self.generation)
        self.assertEqual(rebuilt["source_refs"][0]["sha256"], old_hash)

    def test_canonical_root_allows_distinct_nested_source_generations(self):
        packet = self.source_packet()
        packet["components"][0]["packet"]["generation"] = None
        packet["nested_source_lineage"] = [
            {"generation_id": "b" * 64},
            {"generation_id": "c" * 64},
        ]
        self.write_packet(packet)
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("canonical generation with nested sources")

        envelope = self.envelope(source_commit)

        self.assertEqual(envelope["generation_id"], self.generation)

    def test_canonical_generation_sources_must_match(self):
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, "b" * 64)
        source_commit = self.commit_changes("mismatched canonical generations")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_CANONICAL_GENERATION_MISMATCH"
        ):
            self.envelope(source_commit)

    def test_canonical_generation_sources_must_be_present_together(self):
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("one canonical generation source")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_CANONICAL_GENERATION_SOURCE_MISSING"
        ):
            self.envelope(source_commit)

    def test_canonical_generation_source_must_be_lowercase_sha256(self):
        self.write_generation_source(chain.STEP0_STATUS_PATH, "A" * 64)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, "A" * 64)
        source_commit = self.commit_changes("malformed canonical generation")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_STEP0_GENERATION_INVALID"
        ):
            self.envelope(source_commit)

    def test_nested_generation_ids_remain_format_validated(self):
        packet = self.source_packet()
        packet["nested_source_lineage"] = {"generation_id": "B" * 64}
        self.write_packet(packet)
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("malformed nested generation")

        with self.assertRaisesRegex(chain.ChainError, "CORE_GENERATION_INVALID"):
            self.envelope(source_commit)

    def test_embedded_step0_generation_must_match_canonical_root(self):
        packet = self.source_packet()
        packet["components"][0]["packet"]["generation"]["generation_id"] = "b" * 64
        self.write_packet(packet)
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("mismatched embedded generation")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_EMBEDDED_STEP0_GENERATION_MISMATCH"
        ):
            self.envelope(source_commit)

    def test_legacy_packet_with_multiple_generations_still_fails_closed(self):
        packet = self.source_packet()
        packet["nested_source_lineage"] = {"generation_id": "b" * 64}
        self.write_packet(packet)
        source_commit = self.commit_changes("legacy multiple generations")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_GENERATION_NOT_SINGLETON"
        ):
            self.envelope(source_commit)

    def test_optional_module_failure_is_item_unknown_not_global_hold(self):
        envelope = self.envelope()
        modules = {row["module_id"]: row for row in envelope["modules"]}
        self.assertEqual(modules["news"]["status"], "PARTIAL")
        sec = next(
            row for row in modules["news"]["components"]
            if row["component_id"] == "SEC_FILING_CONTENT"
        )
        self.assertEqual(sec["effective_status"], "UNKNOWN")
        self.assertEqual(sec["binding_status"], "SOURCE_BINDING_MISMATCH")
        self.assertEqual(modules["crypto"]["status"], "UNAVAILABLE")
        self.assertEqual(envelope["schema_version"], "briefing_input_envelope/2")

    def test_only_core_identity_and_authority_errors_fail_closed(self):
        with self.assertRaisesRegex(chain.ChainError, "CORE_DATE_SLOT_LINEAGE_MISMATCH"):
            chain.build_input_envelope(
                self.repo,
                source_commit=self.source_commit,
                packet_path=self.packet_path,
                briefing_path=self.briefing_path,
                decision_date="2026-09-03",
                slot="morning",
                registry_path=ROOT / "config/briefing_module_registry_v2.json",
            )
        packet = json.loads(
            subprocess.check_output(
                ["git", "show", f"{self.source_commit}:{self.packet_path}"], cwd=self.repo
            )
        )
        packet["authority"]["order_generation_authorized"] = True
        packet.pop("packet_sha256")
        packet["packet_sha256"] = chain.digest(packet)
        (self.repo / self.packet_path).write_bytes(chain.canonical(packet) + b"\n")
        subprocess.run(["git", "add", self.packet_path], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "unsafe source"], cwd=self.repo, check=True)
        unsafe = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        with self.assertRaisesRegex(chain.ChainError, "CORE_EXECUTION_AUTHORITY_VIOLATION"):
            chain.build_input_envelope(
                self.repo,
                source_commit=unsafe,
                packet_path=self.packet_path,
                briefing_path=self.briefing_path,
                decision_date="2026-09-02",
                slot="morning",
                registry_path=ROOT / "config/briefing_module_registry_v2.json",
            )

    def test_handoff_and_claims_are_always_present_and_compatible(self):
        artifacts = chain.build_chain_artifacts(self.envelope())
        self.assertEqual(artifacts["handoff.json"]["schema_version"], "briefing_handoff/2")
        self.assertEqual(
            artifacts["claude-handoff-v1.json"]["schema_version"],
            "claude_briefing_handoff/1",
        )
        self.assertGreater(len(artifacts["claude-handoff-v1.json"]["claims"]), 0)
        self.assertEqual(artifacts["claim-ledger.json"]["schema_version"], "claim_ledger/1")
        self.assertGreater(len(artifacts["claim-ledger.json"]["claims"]), 0)
        self.assertEqual(
            artifacts["handoff.json"]["major_event_coverage"]["user_message_ko"],
            "주요 뉴스 검증 불가",
        )
        self.assertFalse(
            artifacts["display-proposal.json"]["changes"][0]["content"]
            ["complete_market_conclusion_allowed"]
        )

    def rich_delivery_packet(self):
        """The exact packet shape that renders dated, numeric delivery claims."""
        packet = self.source_packet()
        by_id = {row["component_id"]: row for row in packet["components"]}
        by_id["FREE_MARKET_DATA"]["packet"] = {
            "vixcls": {"date": "2026-08-31", "value": "17.25"},
            "us_market_reference": {"as_of_session_date": "2026-09-01"},
            "scope_warning": "IEX_PARTIAL_EVIDENCE_ONLY_NOT_MARKET_WIDE_OR_TRADE_AUTHORITY",
        }
        packet["components"].extend([
            {
                "component_id": "BTC_TREND",
                "status": "READY",
                "reason": None,
                "as_of_date": "2026-09-01",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {
                    "latest_finalized_day": "2026-09-01",
                    "direction": "ABOVE_200DMA",
                    "dma_200": "70000.0",
                },
            },
            {
                "component_id": "BTC_RISK",
                "status": "READY",
                "reason": None,
                "as_of_date": "2026-09-01",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {
                    "risk_point": {
                        "as_of_date": "2026-09-01",
                        "drawdown": {
                            "current_fraction": "-0.01",
                            "maximum_fraction": "-0.10",
                        },
                        "realized_volatility": {"annualized_fraction": "0.45"},
                    }
                },
            },
            {
                "component_id": "STABLECOIN_NET_ISSUANCE",
                "status": "READY",
                "reason": None,
                "as_of_date": "2026-09-02",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {
                    "observation_date": "2026-09-02",
                    "daily_net_issuance_native_usd_peg": "10",
                    "weekly_net_issuance_native_usd_peg": "70",
                },
            },
            {
                "component_id": "KOREA_MARKET_SIGNALS",
                "status": "READY",
                "reason": None,
                "as_of_date": "2026-09-01",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {"as_of_date": "2026-09-01"},
            },
            {
                "component_id": "KRX_POST_CLOSE",
                "status": "READY",
                "reason": None,
                "as_of_date": "2026-09-02",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {
                    "observation_status": "observed_unconfirmed",
                    "summary": {
                        "observed_symbol_count": 2,
                        "decision_eligible_symbol_count": 0,
                        "confirmed_same_day_count": 0,
                    },
                    "symbols": [
                        {
                            "latest_observed_day": "2026-09-02",
                            "latest_trading_day": "2026-09-01",
                        },
                        {
                            "latest_observed_day": "2026-09-02",
                            "latest_trading_day": "2026-09-01",
                        },
                    ],
                },
            },
            {
                "component_id": "DYNAMIC_CLOCK",
                "status": "READY",
                "reason": None,
                "as_of_date": "2026-09-02",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {
                    "decision_date": "2026-09-02",
                    "markets": {
                        "CRYPTO": {
                            "watch_review": [
                                {"subject": "AAA/USD", "next_review_at": "2026-09-01"},
                                {"subject": "BBB/USD", "next_review_at": "2026-09-02"},
                            ]
                        }
                    },
                },
            },
            {
                "component_id": "ROTATION_DISCOVERY",
                "status": "PENDING",
                "reason": "PROMOTION_NOT_AUTHORIZED",
                "as_of_date": "2026-09-02",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {
                    "discovery": {
                        "case_count": 3,
                        "new_candidates": [],
                        "existing_candidate_changes": [],
                    },
                    "signal_observations": {"observation_count": 2},
                },
            },
            {
                "component_id": "BUSINESS_ACCELERATION",
                "status": "PENDING",
                "reason": "RANKING_UNRATIFIED",
                "as_of_date": "2026-09-02",
                "source_packet_path": None,
                "source_packet_sha256": None,
                "packet": {
                    "series": [{
                        "metric": "MONTHLY_REVENUE_YOY",
                        "pattern": "LATEST_STEP_NOT_UP",
                        "values_pct": ["30.1", "67.9", "44.7"],
                        "candidate_eligible": False,
                    }]
                },
            },
        ])
        by_id["OFFICIAL_RELEASE_SUMMARY"]["packet"] = {
            "counts": {
                "observed_registered_releases": 1,
                "observed_summary_items": 1,
            },
            "observations": [{
                "subject": "SNDK",
                "published_at": "2026-08-05",
                "summary_items": [{
                    "text": "Revenue rose because the company reported higher volume and pricing."
                }],
            }],
        }
        return packet

    def test_delivery_claims_cover_dates_numbers_causality_and_review_due(self):
        self.write_packet(self.rich_delivery_packet())
        source_commit = self.commit_changes("rich delivery claims")

        envelope = self.envelope(source_commit=source_commit)
        artifacts = chain.build_chain_artifacts(envelope)
        ledger = artifacts["claim-ledger.json"]
        claims = {row["claim_id"]: row for row in ledger["claims"]}
        for claim_id in (
            "freshness.us.market_session_date",
            "freshness.us.vix_observation_date",
            "numeric.us.vixcls",
            "freshness.crypto.btc_trend_finalized_date",
            "freshness.crypto.btc_risk_finalized_date",
            "numeric.crypto.btc_risk",
            "freshness.krx.latest_confirmed_close_date",
            "freshness.krx.post_close_observed_dates",
            "numeric.krx.post_close_summary",
            "review_due.dynamic_clock.crypto",
            "review_due.dynamic_clock.all",
            "numeric.rotation.discovery_summary",
            "numeric.business_acceleration.series_1",
            "official_release.attributed_summary_1",
            "boundary.official_release.causality",
        ):
            self.assertIn(claim_id, claims)
        self.assertIn("2026-09-01", claims["freshness.us.market_session_date"]["statement"])
        self.assertIn("2026-08-31", claims["freshness.us.vix_observation_date"]["statement"])
        self.assertIn(
            "overdue=1, due_today=1, upcoming=0, unclassified=0, total=2",
            claims["review_due.dynamic_clock.all"]["statement"],
        )
        self.assertEqual(claims["boundary.official_release.causality"]["kind"], "UNKNOWN")
        self.assertEqual(
            set(claims["numeric.crypto.btc_risk"]),
            {"claim_id", "kind", "statement", "status", "source_ref_paths"},
        )
        PORTAL_PRODUCER.validate_claim_ledger(self.repo, ledger)

    def write_evidence(self, path, body: bytes) -> bytes:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return body

    def primary_evidence(self):
        """Write the exact provider documents behind the external claims."""
        fred_response = b'{"observations":[{"date":"2026-08-31","value":"17.25"}]}\n'
        fred_raw_path = (
            "evidence/free_market_data/fred/raw/2026-09-01/rev-001/fred_vixcls.json.gz"
        )
        fred_manifest_path = (
            "evidence/free_market_data/fred/raw/2026-09-01/rev-001/manifest.json"
        )
        fred_raw = self.write_evidence(fred_raw_path, gzip.compress(fred_response))
        fred_manifest = self.write_evidence(
            fred_manifest_path,
            chain.canonical({
                "realtime_start": "2026-09-01", "series_id": "VIXCLS",
                "captured_at_utc": "2026-09-01T21:43:00Z",
                "observation": {"observation_date": "2026-08-31"},
            }) + b"\n",
        )
        alpaca_response = b'{"bars":{"SPY":[{"c":"770.18"}]}}\n'
        alpaca_raw_path = (
            "evidence/free_market_data/raw/alpaca/daily_bars/rev-001/"
            "alpaca_iex_daily_bars.json.gz"
        )
        self.write_evidence(alpaca_raw_path, gzip.compress(alpaca_response))
        capture_path = "data/latest_free_market_data.json"
        capture = self.write_evidence(capture_path, chain.canonical({
            "contract_version": "free_market_data/3",
            "fred": {
                "observation_date": "2026-08-31",
                "response_sha256": chain.digest_bytes(fred_response),
                "series_id": "VIXCLS",
                "value": "17.25",
            },
            "observed_at_utc": "2026-09-01T21:43:00Z",
            "us_market_reference": {"as_of_session_date": "2026-09-01"},
        }) + b"\n")

        btc_directory = "evidence/crypto/btc/raw/2026-09-02"
        btc_response = b'{"result":{"XXBTZUSD":[[1,"70000.0"]]}}\n'
        btc_raw_path = f"{btc_directory}/kraken_ohlc_xbtusd.json.gz"
        btc_manifest_path = f"{btc_directory}/_manifest.json"
        self.write_evidence(btc_raw_path, gzip.compress(btc_response))
        self.write_evidence(btc_manifest_path, chain.canonical({
            "capture_version": "btc-price-capture/v1",
            "fetched_at_utc": "2026-09-02T00:42:02Z",
            "raw": {
                "current_excluded_day": "2026-09-02",
                "file": "kraken_ohlc_xbtusd.json.gz",
                "latest_finalized_day": "2026-09-01",
                "response_sha256": chain.digest_bytes(btc_response),
            },
            "schema_version": 1,
            "snapshot_date": "2026-09-02",
            "source": {"name": "kraken_spot_ohlc"},
        }) + b"\n")
        self.write_evidence(
            f"{btc_directory}/_sha256.txt",
            f"{chain.digest_bytes(btc_response)}  kraken_ohlc_xbtusd.json\n".encode("utf-8"),
        )
        derivation_path = "tools/derivations/btc_trend_v1.py"
        derivation = self.write_evidence(
            derivation_path, b"# pinned 200DMA derivation consumed by the capture\n"
        )

        stablecoin_directory = "evidence/stablecoin/raw/2026-09-02"
        stablecoin_response = b'{"totalCirculatingUSD":{"peggedUSD":1}}\n'
        stablecoin_raw_path = f"{stablecoin_directory}/stablecoincharts_all.json.gz"
        stablecoin_manifest_path = f"{stablecoin_directory}/_manifest.json"
        self.write_evidence(stablecoin_raw_path, gzip.compress(stablecoin_response))
        self.write_evidence(stablecoin_manifest_path, chain.canonical({
            "capture_mode": "direct_fetch_append_only",
            "endpoints": [{
                "endpoint": "https://stablecoins.llama.fi/stablecoincharts/all",
                "fetched_at_utc": "2026-09-02T06:38:27Z",
                "name": "stablecoincharts_all",
                "raw_file": "stablecoincharts_all.json.gz",
                "response_sha256": chain.digest_bytes(stablecoin_response),
                "semantics": "historical_series",
            }],
            "schema_version": 1,
            "snapshot_date": "2026-09-02",
        }) + b"\n")
        self.write_evidence(
            f"{stablecoin_directory}/_sha256.txt",
            f"{chain.digest_bytes(stablecoin_response)}  stablecoincharts_all.json\n"
            .encode("utf-8"),
        )

        release_root = "data/sec_content/SNDK/0001628280-26-053346"
        release_document = b"<html>Sandisk Reports Fiscal Fourth Quarter 2026 Results</html>\n"
        release_document_path = f"{release_root}/sndkq4-26ex991xpressrelease.htm.gz"
        release_manifest_path = f"{release_root}/_manifest.json"
        self.write_evidence(release_document_path, gzip.compress(release_document))
        release_manifest = self.write_evidence(release_manifest_path, chain.canonical({
            "accession": "0001628280-26-053346",
            "filing_date": "2026-08-05",
            "retrieved_at_utc": "2026-08-20T21:59:19Z",
            "documents": [{
                "content_sha256": chain.digest_bytes(release_document),
                "name": "sndkq4-26ex991xpressrelease.htm",
            }],
        }) + b"\n")
        return {
            "alpaca_raw_path": alpaca_raw_path,
            "alpaca_response_sha256": chain.digest_bytes(alpaca_response),
            "btc_directory": btc_directory,
            "btc_manifest_path": btc_manifest_path,
            "btc_raw_path": btc_raw_path,
            "capture_path": capture_path,
            "capture_sha256": chain.digest_bytes(capture),
            "derivation_path": derivation_path,
            "derivation_sha256": chain.digest_bytes(derivation),
            "fred_manifest_file_sha256": chain.digest_bytes(fred_manifest),
            "fred_manifest_path": fred_manifest_path,
            "fred_raw_file_sha256": chain.digest_bytes(fred_raw),
            "fred_raw_path": fred_raw_path,
            "fred_response_sha256": chain.digest_bytes(fred_response),
            "release_content_sha256": chain.digest_bytes(release_document),
            "release_document_path": release_document_path,
            "release_manifest_path": release_manifest_path,
            "release_manifest_sha256": chain.digest_bytes(release_manifest),
            "stablecoin_directory": stablecoin_directory,
            "stablecoin_manifest_path": stablecoin_manifest_path,
            "stablecoin_raw_path": stablecoin_raw_path,
        }

    def source_bound_packet(self):
        """Bind the retained primary evidence into the rich delivery packet."""
        evidence = self.primary_evidence()
        packet = self.rich_delivery_packet()
        by_id = {row["component_id"]: row for row in packet["components"]}
        free = by_id["FREE_MARKET_DATA"]
        free["source_packet_path"] = evidence["capture_path"]
        free["source_packet_sha256"] = evidence["capture_sha256"]
        free["packet"].update({
            "alpaca_daily_evidence": {
                "raw_path": evidence["alpaca_raw_path"],
                "raw_response_sha256": evidence["alpaca_response_sha256"],
            },
            "fred_evidence": {
                "manifest_file_sha256": evidence["fred_manifest_file_sha256"],
                "manifest_path": evidence["fred_manifest_path"],
                "raw_file_sha256": evidence["fred_raw_file_sha256"],
                "raw_path": evidence["fred_raw_path"],
                "raw_response_sha256": evidence["fred_response_sha256"],
            },
        })
        trend = by_id["BTC_TREND"]
        trend["source_packet_path"] = evidence["btc_directory"]
        trend["packet"]["capture_date"] = "2026-09-02"
        trend["packet"]["derivation"] = {
            "code_path": evidence["derivation_path"],
            "code_sha256": evidence["derivation_sha256"],
        }
        # BTC_RISK deliberately pins no derivation code and keeps the older
        # risk_point.as_of_date fallback for its finalized measurement day.
        risk = by_id["BTC_RISK"]
        risk["source_packet_path"] = evidence["btc_directory"]
        risk["packet"]["capture_date"] = "2026-09-02"
        by_id["STABLECOIN_NET_ISSUANCE"]["source_packet_path"] = (
            evidence["stablecoin_directory"]
        )
        by_id["OFFICIAL_RELEASE_SUMMARY"]["packet"]["observations"][0]["lineage"] = {
            "manifest_ref": evidence["release_manifest_path"],
            "manifest_sha256": evidence["release_manifest_sha256"],
            "release_content_sha256": evidence["release_content_sha256"],
            "release_document_ref": evidence["release_document_path"],
            "release_source_uri": (
                "https://www.sec.gov/Archives/edgar/data/2023554/"
                "000162828026053346/sndkq4-26ex991xpressrelease.htm"
            ),
            "retrieved_at_utc": "2026-08-20T21:59:19Z",
        }
        return packet, evidence

    def bound_envelope(self, packet, message):
        self.write_packet(packet)
        return self.envelope(source_commit=self.commit_changes(message))

    def test_external_claims_bind_exact_primary_sources_and_clocks(self):
        packet, evidence = self.source_bound_packet()
        envelope = self.bound_envelope(packet, "exact primary source binding")
        artifacts = chain.build_chain_artifacts(envelope)
        ledger = artifacts["claim-ledger.json"]
        claims = {row["claim_id"]: row for row in ledger["claims"]}
        bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
        compat = {
            row["claim_id"]: row
            for row in artifacts["claude-handoff-v1.json"]["claims"]
        }
        self.assertEqual(
            envelope["claim_source_binding_schema"],
            "briefing_claim_source_binding/1",
        )

        # FRED VIXCLS: the observation date and the capture clock stay separate.
        vix = bindings["numeric.us.vixcls"]
        self.assertEqual(vix["source_grade"], "PRIMARY_DIRECT")
        self.assertEqual(vix["reason_codes"], [])
        self.assertEqual(vix["observation_date"], "2026-08-31")
        self.assertEqual(vix["observed_at"], "2026-09-01T21:43:00Z")
        self.assertIn(evidence["fred_raw_path"], claims["numeric.us.vixcls"]["source_ref_paths"])
        self.assertIn(
            evidence["fred_manifest_path"],
            claims["freshness.us.vix_observation_date"]["source_ref_paths"],
        )
        self.assertEqual(compat["numeric.us.vixcls"]["source_grade"], "PRIMARY_DIRECT")
        self.assertEqual(compat["numeric.us.vixcls"]["observed_at"], "2026-09-01T21:43:00Z")
        self.assertEqual(
            compat["numeric.us.vixcls"]["compared_dates"], ["2026-08-31", "2026-09-01"]
        )

        # The US session clock stays its own date and grants no PIT permission.
        session = bindings["freshness.us.market_session_date"]
        self.assertEqual(session["source_grade"], "PRIMARY_DIRECT")
        self.assertEqual(session["observation_date"], "2026-09-01")
        self.assertIn(evidence["alpaca_raw_path"], session["source_ref_paths"])
        clocks = bindings["boundary.us.independent_evidence_clocks"]
        self.assertEqual(clocks["source_grade"], "UNKNOWN")
        self.assertEqual(clocks["compared_dates"], ["2026-08-31", "2026-09-01"])
        self.assertEqual(claims["boundary.us.independent_evidence_clocks"]["source_ref_paths"], [])

        # BTC: the finalized measurement day and the capture day stay distinct.
        trend_date = bindings["freshness.crypto.btc_trend_finalized_date"]
        self.assertEqual(trend_date["source_grade"], "PRIMARY_DIRECT")
        self.assertEqual(trend_date["observation_date"], "2026-09-01")
        self.assertEqual(trend_date["observed_at"], "2026-09-02T00:42:02Z")
        self.assertEqual(trend_date["compared_dates"], ["2026-09-01", "2026-09-02"])
        self.assertEqual(
            bindings["freshness.crypto.btc_risk_finalized_date"]["observation_date"],
            "2026-09-01",
        )

        # Raw/code binding identifies a calculation, never a direct provider value.
        self.assertEqual(bindings["numeric.crypto.btc_trend"]["source_grade"], "INTERNAL_LOGIC_CHECK")
        self.assertIn(
            evidence["derivation_path"],
            claims["numeric.crypto.btc_trend"]["source_ref_paths"],
        )
        risk = bindings["numeric.crypto.btc_risk"]
        self.assertEqual(risk["source_grade"], "UNKNOWN")
        self.assertEqual(risk["reason_codes"], ["DERIVATION_CODE_NOT_PINNED"])
        self.assertEqual(
            bindings["numeric.crypto.stablecoin_net_issuance"]["reason_codes"],
            ["DERIVATION_CODE_NOT_PINNED"],
        )

        # The retained stablecoin capture is readable compressed primary evidence.
        stablecoin = bindings["freshness.crypto.stablecoin_observation_date"]
        self.assertEqual(stablecoin["source_grade"], "PRIMARY_DIRECT")
        self.assertEqual(stablecoin["observed_at"], "2026-09-02T06:38:27Z")
        self.assertIn(evidence["stablecoin_raw_path"], stablecoin["source_ref_paths"])
        self.assertIn(
            f"{evidence['stablecoin_directory']}/_sha256.txt",
            stablecoin["source_ref_paths"],
        )

        # The official release stays attributed, never independent causality.
        summary = bindings["official_release.attributed_summary_1"]
        self.assertEqual(summary["source_grade"], "OFFICIAL_STATEMENT_RELAY")
        self.assertEqual(summary["observation_date"], "2026-08-05")
        self.assertEqual(summary["observed_at"], "2026-08-20T21:59:19Z")
        self.assertIn(evidence["release_document_path"], summary["source_ref_paths"])
        self.assertEqual(
            bindings["date.official_release.observation_1"]["source_grade"],
            "OFFICIAL_STATEMENT_RELAY",
        )
        self.assertEqual(compat["boundary.official_release.causality"]["source_grade"], "UNKNOWN")

        # Aggregate and internal facts are never relabelled as provider statements.
        for claim_id in (
            "numeric.components.status_counts",
            "numeric.official_release.summary_counts",
            "numeric.krx.post_close_summary",
            "core.lineage",
        ):
            self.assertNotIn(claim_id, bindings)
            self.assertEqual(compat[claim_id]["source_grade"], "INTERNAL_LOGIC_CHECK")
            self.assertEqual(compat[claim_id]["observed_at"], "UNKNOWN")

        # Every added claim reference is an exact Git-byte, generation-bound ref.
        refs = {row["path"]: row for row in ledger["source_refs"]}
        for path in (
            evidence["capture_path"],
            evidence["fred_raw_path"],
            evidence["btc_raw_path"],
            evidence["btc_manifest_path"],
            evidence["derivation_path"],
            evidence["release_document_path"],
        ):
            self.assertIn(path, refs)
            self.assertEqual(refs[path]["generation_id"], envelope["generation_id"])
            self.assertEqual(
                refs[path]["sha256"],
                chain.digest_bytes((self.repo / path).read_bytes()),
            )
        self.assertEqual(ledger["source_refs"][0]["path"], self.packet_path)
        self.assertEqual(ledger["source_refs"][1]["path"], self.briefing_path)
        for claim in ledger["claims"]:
            self.assertEqual(
                set(claim),
                {"claim_id", "kind", "statement", "status", "source_ref_paths"},
            )
            self.assertTrue(set(claim["source_ref_paths"]).issubset(set(refs)))

        # Capture alone never grants point-in-time admissibility.
        for binding in bindings.values():
            self.assertIsNone(binding["source_available_at"])
            self.assertFalse(binding["point_in_time_admissible"])
        PORTAL_PRODUCER.validate_claim_ledger(self.repo, ledger)

    def test_missing_mismatched_and_unreadable_evidence_stays_unknown(self):
        packet, evidence = self.source_bound_packet()
        by_id = {row["component_id"]: row for row in packet["components"]}
        by_id["FREE_MARKET_DATA"]["packet"]["fred_evidence"]["raw_response_sha256"] = "0" * 64
        by_id["BTC_TREND"]["packet"]["derivation"]["code_sha256"] = "0" * 64
        (self.repo / evidence["btc_manifest_path"]).unlink()
        (self.repo / evidence["stablecoin_raw_path"]).write_bytes(b"not gzip bytes\n")
        (self.repo / evidence["release_manifest_path"]).write_bytes(b"{}\n")

        envelope = self.bound_envelope(packet, "degraded primary evidence")
        artifacts = chain.build_chain_artifacts(envelope)
        ledger = artifacts["claim-ledger.json"]
        claims = {row["claim_id"]: row for row in ledger["claims"]}
        bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}

        vix = bindings["numeric.us.vixcls"]
        self.assertEqual(vix["source_grade"], "UNKNOWN")
        self.assertIn("FRED_SOURCE_CONTENT_DIGEST_MISMATCH", vix["reason_codes"])
        self.assertIn("US_CAPTURE_FRED_RESPONSE_DIGEST_MISMATCH", vix["reason_codes"])
        self.assertNotIn(evidence["fred_raw_path"], claims["numeric.us.vixcls"]["source_ref_paths"])

        trend_date = bindings["freshness.crypto.btc_trend_finalized_date"]
        self.assertEqual(trend_date["source_grade"], "UNKNOWN")
        self.assertEqual(trend_date["reason_codes"], ["CAPTURE_CLOCK_MISSING", "RETAINED_MANIFEST_UNAVAILABLE"])
        self.assertEqual(trend_date["observed_at"], "UNKNOWN")
        self.assertEqual(trend_date["observation_date"], "2026-09-01")
        self.assertIn(
            "DERIVATION_SOURCE_FILE_DIGEST_MISMATCH",
            bindings["numeric.crypto.btc_trend"]["reason_codes"],
        )

        stablecoin = bindings["freshness.crypto.stablecoin_observation_date"]
        self.assertEqual(stablecoin["source_grade"], "UNKNOWN")
        self.assertIn(
            "STABLECOIN_RESPONSE_SOURCE_COMPRESSED_UNREADABLE", stablecoin["reason_codes"]
        )

        summary = bindings["official_release.attributed_summary_1"]
        self.assertEqual(summary["source_grade"], "UNKNOWN")
        self.assertIn("RELEASE_SOURCE_FILE_DIGEST_MISMATCH", summary["reason_codes"])

        # A degraded claim keeps its id, kind, statement and packet binding, and
        # the strict ledger still passes the unchanged producer intake.
        self.assertEqual(claims["numeric.us.vixcls"]["kind"], "FACT")
        self.assertIn("17.25", claims["numeric.us.vixcls"]["statement"])
        self.assertEqual(
            claims["freshness.crypto.btc_trend_finalized_date"]["source_ref_paths"],
            [self.packet_path],
        )
        PORTAL_PRODUCER.validate_claim_ledger(self.repo, ledger)

    def test_future_dated_primary_evidence_is_not_admitted(self):
        packet, evidence = self.source_bound_packet()
        by_id = {row["component_id"]: row for row in packet["components"]}
        by_id["STABLECOIN_NET_ISSUANCE"]["packet"]["observation_date"] = "2026-09-05"
        manifest_path = self.repo / evidence["stablecoin_manifest_path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["snapshot_date"] = "2026-09-05"
        manifest_path.write_bytes(chain.canonical(manifest) + b"\n")

        envelope = self.bound_envelope(packet, "future stablecoin observation")
        bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
        stablecoin = bindings["freshness.crypto.stablecoin_observation_date"]
        self.assertEqual(stablecoin["source_grade"], "UNKNOWN")
        self.assertIn("FUTURE_EVIDENCE", stablecoin["reason_codes"])
        self.assertEqual(stablecoin["observation_date"], "2026-09-05")
        self.assertEqual(
            bindings["numeric.crypto.stablecoin_net_issuance"]["source_grade"], "UNKNOWN"
        )
        # An unrelated, correctly dated claim keeps its own primary grade.
        self.assertEqual(
            bindings["freshness.crypto.btc_trend_finalized_date"]["source_grade"],
            "PRIMARY_DIRECT",
        )

    def rewrite_source_manifest(self, packet, evidence, kind, mutate):
        path = self.repo / evidence[f"{kind}_manifest_path"]
        value = json.loads(path.read_bytes())
        mutate(value)
        path.write_bytes(chain.canonical(value) + b"\n")
        components = {row["component_id"]: row for row in packet["components"]}
        if kind == "fred":
            components["FREE_MARKET_DATA"]["packet"]["fred_evidence"]["manifest_file_sha256"] = chain.digest_bytes(path.read_bytes())
        elif kind == "release":
            components["OFFICIAL_RELEASE_SUMMARY"]["packet"]["observations"][0]["lineage"]["manifest_sha256"] = chain.digest_bytes(path.read_bytes())

    def test_all_bound_computations_keep_internal_grade_and_unbound_code_stays_unknown(self):
        packet, evidence = self.source_bound_packet()
        by_id = {row["component_id"]: row for row in packet["components"]}
        for component_id, name in [("BTC_RISK", "btc_risk"), ("STABLECOIN_NET_ISSUANCE", "stablecoin_net_issuance")]:
            path = f"tools/derivations/{name}.py"
            body = self.write_evidence(path, f"# isolated {name} test derivation\n".encode())
            by_id[component_id]["packet"]["derivation"] = {"code_path": path, "code_sha256": chain.digest_bytes(body)}
        envelope = self.bound_envelope(packet, "bound computed values retain internal grade")
        bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
        for name in ("btc_trend", "btc_risk", "stablecoin_net_issuance"):
            self.assertEqual(bindings[f"numeric.crypto.{name}"]["source_grade"], "INTERNAL_LOGIC_CHECK")
            self.assertEqual(bindings[f"numeric.crypto.{name}"]["reason_codes"], [])
        for component_id in ("BTC_TREND", "BTC_RISK", "STABLECOIN_NET_ISSUANCE"):
            by_id[component_id]["packet"]["derivation"]["code_sha256"] = "0" * 64
        rejected = self.bound_envelope(packet, "computed code mismatch remains unknown")
        rejected_bindings = {row["claim_id"]: row for row in rejected["claim_source_bindings"]}
        for name in ("btc_trend", "btc_risk", "stablecoin_net_issuance"):
            self.assertEqual(rejected_bindings[f"numeric.crypto.{name}"]["source_grade"], "UNKNOWN")
            self.assertIn("DERIVATION_SOURCE_FILE_DIGEST_MISMATCH", rejected_bindings[f"numeric.crypto.{name}"]["reason_codes"])

    def test_missing_invalid_or_mismatched_provider_clocks_are_claim_local_unknown(self):
        cases = [
            ("fred", "captured_at_utc", None, "numeric.us.vixcls", "FRED_CAPTURE_CLOCK_MISSING"),
            ("fred", "captured_at_utc", "2026-09-01T21:43:00", "numeric.us.vixcls", "FRED_CAPTURE_CLOCK_INVALID"),
            ("fred", "captured_at_utc", "2026-09-01T21:44:00Z", "numeric.us.vixcls", "FRED_CAPTURE_CLOCK_MISMATCH"),
            ("release", "retrieved_at_utc", None, "official_release.attributed_summary_1", "RELEASE_CAPTURE_CLOCK_MISSING"),
            ("release", "retrieved_at_utc", "2026-02-30T12:00:00Z", "official_release.attributed_summary_1", "RELEASE_CAPTURE_CLOCK_INVALID"),
            ("release", "retrieved_at_utc", "2026-08-20T22:00:00Z", "official_release.attributed_summary_1", "RELEASE_CAPTURE_CLOCK_MISMATCH"),
            ("release", "filing_date", "2026-08-06", "official_release.attributed_summary_1", "RELEASE_MANIFEST_PUBLICATION_DATE_MISMATCH"),
            ("btc", "fetched_at_utc", None, "freshness.crypto.btc_trend_finalized_date", "CAPTURE_CLOCK_MISSING"),
            ("btc", "fetched_at_utc", "not-an-instant", "freshness.crypto.btc_trend_finalized_date", "CAPTURE_CLOCK_INVALID"),
        ]
        for kind, key, value, claim_id, reason in cases:
            with self.subTest(kind=kind, key=key, value=value):
                packet, evidence = self.source_bound_packet()
                self.rewrite_source_manifest(packet, evidence, kind, lambda manifest: manifest.__setitem__(key, value))
                envelope = self.bound_envelope(packet, f"invalid {kind} {key} {value}")
                bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
                self.assertEqual(bindings[claim_id]["source_grade"], "UNKNOWN")
                self.assertIn(reason, bindings[claim_id]["reason_codes"])
                unaffected = "freshness.crypto.btc_trend_finalized_date" if kind != "btc" else "numeric.us.vixcls"
                self.assertEqual(bindings[unaffected]["source_grade"], "PRIMARY_DIRECT")
                self.assertIsNone(bindings[claim_id]["source_available_at"])
                self.assertFalse(bindings[claim_id]["point_in_time_admissible"])
                PORTAL_PRODUCER.validate_claim_ledger(self.repo, chain.build_chain_artifacts(envelope)["claim-ledger.json"])

    def test_missing_or_invalid_measurement_dates_do_not_use_capture_vintage(self):
        for component_id, field, claim_id in [
            ("BTC_TREND", "latest_finalized_day", "numeric.crypto.btc_trend"),
            ("STABLECOIN_NET_ISSUANCE", "observation_date", "numeric.crypto.stablecoin_net_issuance"),
        ]:
            for value in (None, "2026-02-30"):
                with self.subTest(component=component_id, value=value):
                    packet, evidence = self.source_bound_packet()
                    component = next(row for row in packet["components"] if row["component_id"] == component_id)
                    component["packet"][field] = value
                    envelope = self.bound_envelope(packet, f"measurement {component_id} {value}")
                    bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
                    self.assertEqual(bindings[claim_id]["source_grade"], "UNKNOWN")
                    self.assertEqual(bindings[claim_id]["observation_date"], "UNKNOWN")
                    self.assertIn("OBSERVATION_DATE_MISSING" if value is None else "OBSERVATION_DATE_INVALID", bindings[claim_id]["reason_codes"])
        packet, evidence = self.source_bound_packet()
        self.rewrite_source_manifest(packet, evidence, "fred", lambda manifest: manifest["observation"].__setitem__("observation_date", "2026-09-01"))
        envelope = self.bound_envelope(packet, "FRED measurement must match provider manifest")
        bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
        self.assertIn("FRED_MANIFEST_OBSERVATION_DATE_MISMATCH", bindings["numeric.us.vixcls"]["reason_codes"])
        self.assertEqual(bindings["numeric.us.vixcls"]["source_grade"], "UNKNOWN")

    def test_equivalent_zoned_clocks_match_and_one_missing_endpoint_still_rejects(self):
        packet, evidence = self.source_bound_packet()
        self.rewrite_source_manifest(packet, evidence, "fred", lambda manifest: manifest.__setitem__("captured_at_utc", "2026-09-02T06:43:00+09:00"))
        self.rewrite_source_manifest(packet, evidence, "release", lambda manifest: manifest.__setitem__("retrieved_at_utc", "2026-08-21T06:59:19+09:00"))
        envelope = self.bound_envelope(packet, "equivalent explicit timezone clocks")
        bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
        self.assertEqual(bindings["numeric.us.vixcls"]["source_grade"], "PRIMARY_DIRECT")
        self.assertEqual(bindings["official_release.attributed_summary_1"]["source_grade"], "OFFICIAL_STATEMENT_RELAY")

        # A valid second endpoint cannot hide an absent capture time on the first.
        manifest_path = self.repo / evidence["stablecoin_manifest_path"]
        manifest = json.loads(manifest_path.read_bytes())
        other = copy.deepcopy(manifest["endpoints"][0])
        other.update(name="other", raw_file="other.json.gz")
        body = (self.repo / evidence["stablecoin_raw_path"]).read_bytes()
        self.write_evidence(f"{evidence['stablecoin_directory']}/other.json.gz", body)
        index = self.repo / evidence["stablecoin_directory"] / "_sha256.txt"
        index.write_bytes(index.read_bytes() + f"{other['response_sha256']}  other.json\n".encode())
        manifest["endpoints"].append(other)
        del manifest["endpoints"][0]["fetched_at_utc"]
        manifest_path.write_bytes(chain.canonical(manifest) + b"\n")
        envelope = self.bound_envelope(packet, "one absent endpoint capture cannot be hidden")
        bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
        stable = bindings["freshness.crypto.stablecoin_observation_date"]
        self.assertEqual(stable["source_grade"], "UNKNOWN")
        self.assertIn("ENDPOINT_CAPTURE_CLOCK_MISSING", stable["reason_codes"])

    def test_retained_digest_index_and_each_exact_response_entry_are_required(self):
        for kind, claim_id in [
            ("btc", "freshness.crypto.btc_trend_finalized_date"),
            ("stablecoin", "freshness.crypto.stablecoin_observation_date"),
        ]:
            for mode in ("missing", "unreadable", "malformed", "empty", "absent_entry", "duplicate"):
                with self.subTest(kind=kind, mode=mode):
                    packet, evidence = self.source_bound_packet()
                    path = self.repo / evidence[f"{kind}_directory"] / "_sha256.txt"
                    original = path.read_bytes()
                    if mode == "missing":
                        path.unlink()
                    else:
                        body = {"unreadable": b"\xff", "malformed": original + b"not a digest line\n", "empty": b"\n", "absent_entry": b"0" * 64 + b"  unrelated.json\n", "duplicate": original + original}[mode]
                        path.write_bytes(body)
                    envelope = self.bound_envelope(packet, f"required retained index {kind} {mode}")
                    bindings = {row["claim_id"]: row for row in envelope["claim_source_bindings"]}
                    binding = bindings[claim_id]
                    self.assertEqual(binding["source_grade"], "UNKNOWN")
                    self.assertTrue(any("RETAINED_DIGEST" in reason for reason in binding["reason_codes"]))
                    self.assertNotIn(evidence[f"{kind}_raw_path"], binding["source_ref_paths"])
                    unaffected = "freshness.crypto.stablecoin_observation_date" if kind == "btc" else "freshness.crypto.btc_trend_finalized_date"
                    self.assertEqual(bindings[unaffected]["source_grade"], "PRIMARY_DIRECT")

    def test_retained_20260907_packet_claims_exact_24_of_93_overdue_watch_reviews(self):
        relative = (
            "evidence/daily_briefing/evening/2026-09-07/rev-001/packet.json"
        )
        packet = json.loads(
            subprocess.check_output(
                ["git", "show", f"HEAD:{relative}"], cwd=ROOT
            )
        )
        claims = {
            row["claim_id"]: row
            for row in chain._delivery_claims(packet, relative)
        }
        self.assertIn(
            "overdue=24, due_today=16, upcoming=53, unclassified=0, total=93",
            claims["review_due.dynamic_clock.all"]["statement"],
        )
        self.assertEqual(
            claims["freshness.crypto.btc_trend_finalized_date"]["kind"],
            "UNKNOWN",
        )
        self.assertNotIn(
            "2026-09-07",
            claims["freshness.crypto.btc_trend_finalized_date"]["statement"],
        )

    def test_20260902_major_event_omission_enters_correction_loop_then_passes(self):
        registry = major_events.validate_registry(
            self.event_registry(), briefing_date="2026-09-02", slot="AM"
        )
        missing_draft = {"major_event_coverage": major_events.unavailable_coverage()}
        missing = major_events.validate_coverage(missing_draft, registry)
        self.assertEqual(missing["status"], "CORRECTION_REQUIRED")
        self.assertFalse(missing["portal_allowed"])
        self.assertIn("MAJOR_EVENT_COVERAGE_MISSING", missing["reason_codes"])
        corrected = major_events.correct_handoff(missing_draft, registry)
        passed = major_events.validate_coverage(corrected, registry)
        self.assertEqual(passed["status"], "PASS")
        self.assertTrue(passed["portal_allowed"])
        coverage = corrected["major_event_coverage"]
        self.assertEqual(
            coverage["user_message_ko"],
            "미국의 이란 군사시설 타격, 이란 보복으로 중동 위험 재확대",
        )
        event = coverage["events"][0]
        self.assertEqual(len(event["facts"]), 2)
        self.assertTrue(event["inferences"])
        self.assertTrue(event["unknowns"])
        self.assertTrue(all(not row["price_causality_confirmed"] for row in event["transmission_channels"]))

    def test_20260902_operational_event_registry_matches_regression_fixture(self):
        fixture = self.event_registry()
        registry_path = (
            ROOT / "evidence/briefing_events/2026-09-02/morning/rev-001/registry.json"
        )
        registry_bytes = registry_path.read_bytes()
        operational = json.loads(registry_bytes)
        self.assertEqual(operational, fixture)
        index = json.loads(
            (ROOT / "evidence/briefing_events/2026-09-02/morning/index.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(index["latest_revision"], 1)
        self.assertEqual(index["revisions"][0]["sha256"], chain.digest_bytes(registry_bytes))
        grades = {source["grade"] for source in operational["events"][0]["sources"]}
        self.assertEqual(grades, {"PRIMARY_OFFICIAL", "INDEPENDENT_MAJOR_MEDIA"})

    def test_chain_publication_is_append_only_and_idempotent(self):
        artifacts = chain.build_chain_artifacts(self.envelope())
        first = chain.publish_chain(self.repo, artifacts)
        second = chain.publish_chain(self.repo, artifacts)
        self.assertEqual((first["result"], second["result"]), ("APPLIED", "NO_CHANGE"))
        self.assertEqual(second["duplicate_count"], 0)
        changed = copy.deepcopy(artifacts)
        changed["handoff.json"]["analyst_adapter"]["reason"] = "DIFFERENT"
        with self.assertRaisesRegex(chain.ChainError, "CORE_DUPLICATE_ID_CONFLICT"):
            chain.publish_chain(self.repo, changed)

    def test_natural_equivalent_e2e_reaches_portal_and_notion_receipt(self):
        self.source_commit = self.source_with_event_registry()
        envelope = self.envelope()
        source_briefing = subprocess.check_output(
            ["git", "show", f"{self.source_commit}:{self.briefing_path}"], cwd=self.repo
        )
        artifacts = chain.build_chain_artifacts(envelope, briefing_bytes=source_briefing)
        event_gate = artifacts["major-event-validation.json"]
        self.assertEqual(event_gate["pre_correction"]["status"], "CORRECTION_REQUIRED")
        self.assertEqual(event_gate["post_correction"]["status"], "PASS")
        self.assertEqual(event_gate["correction_count"], 1)
        self.assertFalse(event_gate["overwrite_performed"])
        self.assertEqual(
            artifacts["display-proposal.json"]["changes"][0]["content"]
            ["today_key_events"][0]["headline_ko"],
            "미국의 이란 군사시설 타격, 이란 보복으로 중동 위험 재확대",
        )
        corrected_briefing = artifacts["corrected-briefing.md"]
        corrected_text = corrected_briefing.decode("utf-8")
        self.assertLess(corrected_text.index("오늘의 핵심 사건"), corrected_text.index("No order"))
        self.assertIn("전면전의 완전한 재개로 단정할 수 있는지는 아직 확인되지 않았습니다", corrected_text)
        self.assertFalse(artifacts["correction-manifest.json"]["overwrites_source"])
        self.assertEqual(
            artifacts["correction-manifest.json"]["corrected_briefing_sha256"],
            chain.digest_bytes(corrected_briefing),
        )
        input_dir = self.repo / "e2e-input"
        input_dir.mkdir()
        briefing = input_dir / "briefing.md"
        briefing.write_bytes(corrected_briefing)
        paths = {}
        for name in ("claim-ledger.json", "display-proposal.json"):
            path = input_dir / name
            path.write_bytes(chain.canonical(artifacts[name]) + b"\n")
            paths[name] = path
        report = chain.fixture_validation_report(
            artifacts["claim-ledger.json"],
            corrected_briefing,
            artifacts["display-proposal.json"],
            validated_at_kst="2026-09-02T08:00:00+09:00",
        )
        report_path = input_dir / "validation-report.json"
        report_path.write_bytes(chain.canonical(report) + b"\n")
        args = argparse.Namespace(
            repo_root=str(self.repo),
            briefing=str(briefing),
            claim_ledger=str(paths["claim-ledger.json"]),
            validation_report=str(report_path),
            display_proposal=str(paths["display-proposal.json"]),
            out_root="evidence/validated_briefing_portal",
        )
        portal_first = PORTAL_PRODUCER.build(args)
        portal_second = PORTAL_PRODUCER.build(args)
        self.assertEqual((portal_first["result"], portal_second["result"]), ("APPLIED", "NO_CHANGE"))
        portal_envelope = json.loads(
            (self.repo / portal_first["envelope_path"]).read_text(encoding="utf-8")
        )
        receipt = chain.notion_receipt(
            portal_envelope,
            portal_state="APPLIED",
            portal_url="https://atlas.example.invalid/briefing",
        )
        notion_first = chain.publish_notion_receipt(self.repo, receipt)
        notion_second = chain.publish_notion_receipt(self.repo, receipt)
        self.assertEqual((notion_first["result"], notion_second["result"]), ("APPLIED", "NO_CHANGE"))
        self.assertEqual(notion_second["duplicate_count"], 0)
        self.assertTrue(receipt["readback_verified"])

    def test_paper_runtime_can_only_publish_append_only_standard_signals(self):
        signal = {
            "schema_version": "atlas_paper_signal/1",
            "signal_id": "paper-20260902-btc-001",
            "event_at": "2026-09-02T00:00:00Z",
            "market": "CRYPTO",
            "symbol": "BTC/KRW",
            "signal_type": "OBSERVATION",
            "payload": {"status": "PAPER_ONLY"},
            "lineage": {"source_commit": "a" * 40, "generation_id": "b" * 64},
            "authority": {
                "account_mode": "PAPER",
                "real_capital": False,
                "order_authority": False,
                "production_authority": False,
                "trading_authority": False,
            },
        }
        path = "runtime/paper/signals/v1/2026-09-02/paper-20260902-btc-001.json"
        first = paper_signal.publish(self.repo, path, signal)
        second = paper_signal.publish(self.repo, path, signal)
        self.assertEqual((first["result"], second["result"]), ("APPLIED", "NO_CHANGE"))
        with self.assertRaisesRegex(paper_signal.PaperBoundaryError, "PAPER_CORE_PATH_FORBIDDEN"):
            paper_signal.publish(self.repo, "data/briefing/finalization/attack.json", signal)
        unsafe = copy.deepcopy(signal)
        unsafe["authority"]["order_authority"] = True
        with self.assertRaisesRegex(paper_signal.PaperBoundaryError, "PAPER_SIGNAL_AUTHORITY_INVALID"):
            paper_signal.publish(
                self.repo,
                "runtime/paper/signals/v1/2026-09-02/unsafe.json",
                unsafe,
            )
        result = {
            "schema_version": "atlas_paper_result/1",
            "result_id": "result-20260902-btc-001",
            "signal_id": signal["signal_id"],
            "observed_at": "2026-09-02T00:05:00Z",
            "outcome": "OBSERVED_NO_ORDER",
            "payload": {"status": "PAPER_ONLY"},
            "lineage": signal["lineage"],
            "authority": signal["authority"],
        }
        result_path = "runtime/paper/results/v1/2026-09-02/result-20260902-btc-001.json"
        self.assertEqual(
            paper_signal.publish(self.repo, result_path, result)["result"], "APPLIED"
        )

    def test_workflow_and_path_ownership_are_enforced(self):
        workflow = (ROOT / ".github/workflows/daily-briefing.yml").read_text(encoding="utf-8")
        seal = workflow.index("- name: Seal briefing for finalization")
        core = workflow.index("- name: Build pinned briefing core handoff")
        publish = workflow.index("- name: Publish sealed draft")
        self.assertLess(seal, core)
        self.assertLess(core, publish)
        core_step = workflow[core:publish]
        self.assertIn('CAPTURE_PATH="${CAPTURE_PATH#"$GITHUB_WORKSPACE"/}"', core_step)
        self.assertIn('--packet-path "$CAPTURE_PATH/packet.json"', core_step)
        self.assertIn('--briefing-path "$CAPTURE_PATH/briefing.md"', core_step)
        self.assertNotIn(
            '--packet-path "${{ steps.briefing.outputs.capture_path }}/packet.json"',
            core_step,
        )
        self.assertIn("--source-commit \"${{ steps.briefing.outputs.source_commit }}\"", workflow)
        self.assertIn("git add data/briefing/chain_v2", workflow)
        actions = (ROOT / ".github/workflows/actions-pass.yml").read_text(encoding="utf-8")
        self.assertIn("python3 validation/tests/test_briefing_core_v2.py", actions)
        ownership = json.loads(
            (ROOT / "config/briefing_path_ownership_v1.json").read_text(encoding="utf-8")
        )
        paper = ownership["paper_runtime_owner"]
        self.assertTrue(paper["append_only"])
        self.assertFalse(paper["direct_portal_or_notion_write"])
        self.assertTrue(
            set(paper["write_roots"]).isdisjoint(set(paper["forbidden_roots"]))
        )


if __name__ == "__main__":
    unittest.main()
