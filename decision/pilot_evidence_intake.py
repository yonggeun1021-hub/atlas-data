#!/usr/bin/env python3
"""P8-11 stage 2 — real Pilot evidence intake for Forward Alpha MVP.

This module is a **convenience evidence-assembly layer**, not a contract-gated
core packet type. It reads real, already-committed evidence files for four
Pilot subjects (TSM, 298040.KS, 267260.KS, 034020.KS) from this repository
and assembles the exact input shapes that `decision/forward_thesis.py`,
`decision/expectations_gap.py`, and `decision/price_reflection.py` expect.
It never invents a fact, never extracts filing-body content that has not
actually been extracted anywhere in this repo, and never upgrades a
narrative-sourced number to exhibit-extracted status.

★ TSM raw-vs-extracted discipline: of the five real TSM SEC 6-K manifests
  under `data/sec_content/TSM/`, only accession 0001046179-26-000536 (the
  2026-08-11 TSMC board-approval 6-K) has `evidence_status: "OK"` and a
  real, hash-verified `extracted` array with actual quoted text. The other
  four (000459, 000471, 000539, 000541) are `evidence_status: "PENDING"` /
  `interpretation_status: "UNDETERMINED"` / `reasons: ["EXTRACTOR_NOT_REGISTERED"]`
  -- raw, hash-verified, but NOT extracted. Only 000536 backs an
  `EXHIBIT_EXTRACTED` observed_fact here; the other four are cited only as
  `evidence_lineage` entries (establishing that a real, dated, hash-verified
  filing exists) and are never used to source an observed_fact claiming to
  know filing-body content.

★ Hyosung (298040.KS) backlog/guidance discipline: every backlog and
  new-order-guidance figure here is drawn from `_watchlist_rows.json` (a
  CIO-curated Notion-sourced watchlist row), classified `NARRATIVE_SOURCED`.
  DART's own `relevant_count` is 0 for 298040 across every captured day in
  this repo -- there is no DART-exhibit-extracted backing for these figures,
  and this module never claims otherwise.

★ Doosan Enerbility (034020.KS) has ZERO evidence in this repository. Its
  identity (`034020.KS`) is carried as a reference only, sourced to an
  external CIO/Notion Cockpit note, not to `config/universe.json` (which does
  not include this company). `observed_facts` and `evidence_lineage` are
  hard-coded empty lists for this subject; nothing here can accidentally
  populate them.

Decision date choice: `2026-08-22`. The freshest *confirmed* evidence across
all four subjects is dated 2026-08-21 (KRX `latest_confirmed_day` 2026-08-20,
confirmed as of `collected_for_kst_date` 2026-08-21; TSM SEC filing-metadata
snapshot `collected_for_kst_date` 2026-08-21; TSM free-market close price
`provider_timestamp` 2026-08-21T19:59:00Z), but that same TSM price capture's
own `observed_at_utc` is 2026-08-22T00:44:08Z -- i.e. the capture that
produced the freshest cited evidence itself happened on 2026-08-22 UTC.
`forward_thesis.py` requires `generated_at`'s calendar date to be
`<= decision_date`, and `generated_at` must never precede the evidence it
describes (hard rule 2 in that module's own docstring) -- the only date that
satisfies both simultaneously is `decision_date = "2026-08-22"`, with
`generated_at = "2026-08-22T01:00:00Z"` (after the freshest cited timestamp,
`evidence/free_market_data/raw/2026-08-22/manifest.json`'s
`observed_at_utc` of 2026-08-22T00:44:08Z). Every observed_facts.as_of and
evidence_lineage.filing_date cited below is dated on or before 2026-08-21,
so this choice is always `<=` decision_date, never a lookahead.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PILOT_DECISION_DATE = "2026-08-22"
PILOT_GENERATED_AT = "2026-08-22T01:00:00Z"

PILOT_SUBJECTS = ("TSM", "298040.KS", "267260.KS", "034020.KS")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FORWARD_THESIS = _load_module("pilot_forward_thesis", ROOT / "decision" / "forward_thesis.py")
EXPECTATIONS_GAP = _load_module("pilot_expectations_gap", ROOT / "decision" / "expectations_gap.py")
PRICE_REFLECTION = _load_module("pilot_price_reflection", ROOT / "decision" / "price_reflection.py")
PRICE_EVIDENCE = _load_module("pilot_price_evidence", ROOT / "decision" / "price_evidence.py")
ALPHA_REVIEW = _load_module("pilot_alpha_review", ROOT / "decision" / "alpha_review.py")
ALPHA_SHADOW_LEDGER = _load_module("pilot_alpha_shadow_ledger", ROOT / "shadow" / "alpha_shadow_ledger.py")


def _read_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _to_utc_stamp(iso_with_offset: str) -> str:
    """Convert a real, offset-bearing ISO-8601 timestamp (e.g. KRX's KST
    `observed_at_kst`) to the strict `YYYY-MM-DDTHH:MM:SSZ` UTC form the
    downstream modules require. Never a naive string slice -- a real
    timezone conversion."""
    parsed = dt.datetime.fromisoformat(iso_with_offset)
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ═════════════════════════════ TSM ═════════════════════════════════════
def _tsm_manifest(accession: str) -> dict:
    return _read_json(f"data/sec_content/TSM/{accession}/_manifest.json")


TSM_ACCESSIONS_UNEXTRACTED = (
    "0001046179-26-000459",
    "0001046179-26-000471",
    "0001046179-26-000539",
    "0001046179-26-000541",
)
TSM_ACCESSION_EXTRACTED = "0001046179-26-000536"


def build_tsm_forward_thesis_input(decision_date: str, generated_at: str) -> dict:
    contract = FORWARD_THESIS.load_contract()
    board_manifest = _tsm_manifest(TSM_ACCESSION_EXTRACTED)
    board_doc = board_manifest["documents"][0]
    capex_entry = next(
        row for row in board_manifest["extracted"] if row["label"] == "capital_appropriations"
    )
    sec_briefing = _read_json("data/briefing/sec/TSM.json")
    price_manifest = _read_json("evidence/free_market_data/raw/2026-08-22/manifest.json")
    tsm_bar = next(bar for bar in price_manifest["alpaca"]["bars"] if bar["symbol"] == "TSM")

    evidence_lineage = [
        {
            "source_ref": "TSM_SEC_000536",
            "source_type": "SEC_EXHIBIT",
            "accession": board_manifest["filing_identity"]["accession"],
            "filing_date": board_manifest["filing_date"],
            "exhibit_type": board_manifest["form"],
            "exhibit_document": board_doc["document_name"],
            "source_sha256": board_doc["content_sha256"],
        },
    ]
    for accession in TSM_ACCESSIONS_UNEXTRACTED:
        manifest = _tsm_manifest(accession)
        doc = manifest["documents"][0]
        evidence_lineage.append({
            "source_ref": f"TSM_SEC_{accession.replace('-', '_')}",
            "source_type": "SEC_EXHIBIT",
            "accession": manifest["filing_identity"]["accession"],
            "filing_date": manifest["filing_date"],
            "exhibit_type": manifest["form"],
            "exhibit_document": doc["document_name"],
            "source_sha256": doc["content_sha256"],
        })
    evidence_lineage.append({
        "source_ref": "TSM_WATCHLIST_ROW",
        "source_type": "NARRATIVE_SOURCED",
        "accession": None,
        "filing_date": None,
        "exhibit_type": None,
        "exhibit_document": None,
        "source_sha256": None,
    })
    evidence_lineage.append({
        "source_ref": "TSM_SEC_BRIEFING_SNAPSHOT",
        "source_type": "NARRATIVE_SOURCED",
        "accession": None,
        "filing_date": None,
        "exhibit_type": None,
        "exhibit_document": None,
        "source_sha256": None,
    })
    evidence_lineage.append({
        "source_ref": "TSM_FREE_MARKET_PRICE",
        "source_type": "PRICE_FEED",
        "accession": None,
        "filing_date": None,
        "exhibit_type": None,
        "exhibit_document": None,
        "source_sha256": price_manifest["alpaca"]["raw_sha256"],
    })

    observed_facts = [
        {
            "statement": (
                f"TSMC board approved capital appropriations of approximately "
                f"{capex_entry['raw_value']} (6-K filed {board_manifest['filing_date']}, "
                f"accession {board_manifest['filing_identity']['accession']}), primarily for "
                "installation and upgrade of advanced technology capacity. Quote: "
                f"\"{capex_entry['quote']}\""
            ),
            "source_ref": "TSM_SEC_000536",
            "source_class": "EXHIBIT_EXTRACTED",
            "as_of": board_manifest["filing_date"],
        },
        {
            "statement": (
                "CIO watchlist row confirms TSMC July 2026 monthly revenue: 단월(month) YoY "
                "+44.7%, 누계(cumulative) YoY +37.0%, confirmed 2026-08-10 -- above the "
                "CIO-set non-weakening thresholds (단월 YoY >= +35%, 누계 YoY >= +34.6%)."
            ),
            "source_ref": "TSM_WATCHLIST_ROW",
            "source_class": "NARRATIVE_SOURCED",
            "as_of": "2026-08-10",
        },
        {
            "statement": (
                "Atlas-curated SEC filing-metadata snapshot for TSM "
                f"(collected_at_utc={sec_briefing['source']['collected_at_utc']}, "
                f"atlas_stage={sec_briefing['stock']['atlas_stage']}) lists a Form 4 filed "
                "2026-08-20 and multiple 2026 6-K filings; this is Atlas's own curated "
                "metadata layer (dates/forms/existence), not extracted filing-body content."
            ),
            "source_ref": "TSM_SEC_BRIEFING_SNAPSHOT",
            "source_class": "NARRATIVE_SOURCED",
            "as_of": "2026-08-20",
        },
        {
            "statement": (
                f"TSM closed at ${tsm_bar['close']} (IEX-only partial US market feed) as of "
                f"{tsm_bar['provider_timestamp']}, volume {tsm_bar['volume']}."
            ),
            "source_ref": "TSM_FREE_MARKET_PRICE",
            "source_class": "PRICE_FEED",
            "as_of": "2026-08-21",
        },
    ]

    forward_inferences = [
        {
            "statement": (
                "The 2026-08-11 board-approved capital appropriations (~US$29.44B), if "
                "deployed on TSMC's typical capacity lead time, should begin contributing "
                "incremental advanced-node revenue capacity from late 2026 into 2027, "
                "reinforcing the already-confirmed July monthly revenue growth."
            ),
            "basis": (
                "forward_thesis.capital_commitment (TSM_SEC_000536) combined with "
                "forward_thesis.observed_facts[1] (confirmed July YoY revenue growth)."
            ),
            "confidence": "MEDIUM",
        },
    ]

    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": "TSM",
        "decision_date": decision_date,
        "thesis_id": "FT-TSM-PILOT-20260821-001",
        "thesis_version": 1,
        "spend_owner": "TSMC",
        "capital_commitment": {
            "description": (
                "TSMC board approved capital appropriations of approximately "
                f"{capex_entry['raw_value']} on {board_manifest['filing_date']}, primarily "
                "for installation and upgrade of advanced technology capacity "
                "(6-K exhibit-extracted quote, accession 0001046179-26-000536)."
            ),
            "amount_range_or_unknown": capex_entry["value"],
            "currency_or_unknown": capex_entry["currency"],
            "source_ref": "TSM_SEC_000536",
        },
        "revenue_recipient": "Taiwan Semiconductor Manufacturing Co Ltd (TSM)",
        "atlas_linked_ticker": "TSM",
        "observed_facts": observed_facts,
        "forward_inferences": forward_inferences,
        "assumptions": [
            {
                "statement": (
                    "Board-approved capital appropriations are deployed on the announced "
                    "construction/upgrade timeline without material delay."
                ),
                "why_unconfirmed": (
                    "No subsequent capacity-online or construction-completion filing exists "
                    "in this repository yet."
                ),
            },
        ],
        "counter_evidence": [
            {
                "statement": (
                    "config/rules.json carries a formal TSM disqualification trigger for "
                    "lowered CapEx guidance (RULE-0004), meaning the Atlas rule surface "
                    "already treats a CapEx reversal as a live downside risk, not merely "
                    "upside support."
                ),
                "source_ref": "TSM_RULES_JSON_RULE_0004",
            },
        ],
        "unknowns": [
            (
                "Raw SEC 6-K filings exist and are hash-verified for accessions "
                "0001046179-26-000459 / 000471 / 000539 / 000541, but body content is NOT "
                "extracted (evidence_status=PENDING, interpretation_status=UNDETERMINED, "
                "reasons=[EXTRACTOR_NOT_REGISTERED]); filing content from these accessions "
                "is not yet consumable evidence."
            ),
            (
                "Only a single point-in-time TSM close price snapshot exists in this "
                "repository (evidence/free_market_data/raw/2026-08-22/manifest.json, "
                "IEX-only partial US market feed); no historical price time series exists "
                "here, so recent_return_windows and relative-strength history are UNKNOWN."
            ),
            (
                "config/rules.json holds qualitative TSM Rule cards (RULE-0003 through "
                "RULE-0009) including a CapEx-guidance-lowered disqualification trigger "
                "(RULE-0004), but no ratified Rule PASS/FAIL evaluation packet exists on "
                "disk for TSM; p5_rule_status is NOT_EVALUATED."
            ),
        ],
        "earnings_conversion": {
            "status": "REVENUE_CONVERSION_EXPECTED",
            "mechanism": (
                "AI/HPC-driven demand for advanced-node capacity converts TSMC's confirmed "
                "monthly revenue growth (+44.7% MoM YoY / +37.0% cumulative YoY) into "
                "continuing revenue as the 2026-08-11 board-approved capital appropriations "
                "come online."
            ),
            "expected_start_window": "2026Q3-2027Q2",
            "expected_duration": "UNKNOWN",
            "revenue_direction": "UP",
            "margin_direction": "UNKNOWN",
            "confidence": "MEDIUM",
            "blockers": [
                "CapEx guidance could be lowered (RULE-0004 disqualification trigger).",
                (
                    "Extracted filing content for four of the five 2026 6-Ks on file is "
                    "not yet available (EXTRACTOR_NOT_REGISTERED)."
                ),
            ],
        },
        "catalysts": [
            {
                "description": "Next TSMC monthly revenue report (August 2026 monthly revenue).",
                "expected_date_or_window": "2026-09-10",
                "source_ref": "TSM_WATCHLIST_ROW",
            },
            {
                "description": "TSMC Q3 2026 earnings (expected per watchlist row).",
                "expected_date_or_window": "2026-10-15",
                "source_ref": "TSM_WATCHLIST_ROW",
            },
        ],
        "invalidation_conditions": [
            (
                "월매출 YoY 40% 미달 2개월 연속 (RULE-0003, evaluator_status=READY but no "
                "ratified evaluation packet on file)."
            ),
            (
                "CapEx 하향 disqualification trigger (RULE-0004, evaluator_status=BLOCKED "
                "-- definition/data source components unresolved)."
            ),
            (
                "종가 기준 $398 이탈 -- 8월 반등 구조 전체 무효 (RULE-0005, technical "
                "invalidation, evaluator_status=BLOCKED -- DATA_MISSING)."
            ),
        ],
        "review_dates": ["2026-09-10", "2026-10-15"],
        "evidence_lineage": evidence_lineage,
        "as_of_ceiling": None,
    }


def build_tsm_expectations_gap_input(decision_date: str, generated_at: str) -> dict:
    return {
        "subject": "TSM",
        "decision_date": decision_date,
        "generated_at": generated_at,
        "capex_or_expansion": {
            "direction": "POSITIVE",
            "evidence_note": (
                "TSMC board approved ~US$29.44B capital appropriations for advanced "
                "technology capacity on 2026-08-11 (accession 0001046179-26-000536, "
                "exhibit-extracted)."
            ),
        },
        "revenue_margin_trend": {
            "direction": "POSITIVE",
            "evidence_note": (
                "CIO watchlist confirms July 2026 monthly revenue 단월 YoY +44.7%, 누계 YoY "
                "+37.0% (confirmed 2026-08-10), narrative-sourced from watchlist row."
            ),
        },
        "relative_strength_volume": {
            "direction": "UNKNOWN",
            "evidence_note": (
                "Only a single point-in-time IEX close-price snapshot exists in this repo "
                "(2026-08-21); insufficient to derive a relative-strength/volume trend."
            ),
        },
    }


def build_tsm_price_reflection_input(decision_date: str, generated_at: str) -> dict:
    # decision/price_evidence.py (P8-10 real evidence assembly layer) reads
    # every committed evidence/free_market_data/raw/<date>/ snapshot rather
    # than just today's -- as of this build only one day is committed for
    # TSM, so recent_return_windows/relative_strength come back None (never
    # fabricated from a single point); this widens automatically as the
    # existing daily cron commits more days, with no change needed here.
    evidence = PRICE_EVIDENCE.assemble_price_evidence("TSM", decision_date)
    return {
        "subject": "TSM",
        "decision_date": decision_date,
        "generated_at": generated_at,
        **evidence,
    }


# ═════════════════════════════ 298040.KS (Hyosung) ═════════════════════
def build_hyosung_forward_thesis_input(decision_date: str, generated_at: str) -> dict:
    contract = FORWARD_THESIS.load_contract()
    krx = _read_json("data/briefing/krx/298040.json")
    dart = _read_json("data/briefing/dart/298040.json")
    row = krx["latest_confirmed_row"]

    evidence_lineage = [
        {
            "source_ref": "HYOSUNG_WATCHLIST_ROW",
            "source_type": "NARRATIVE_SOURCED",
            "accession": None, "filing_date": None,
            "exhibit_type": None, "exhibit_document": None, "source_sha256": None,
        },
        {
            "source_ref": "HYOSUNG_KRX_SNAPSHOT",
            "source_type": "PRICE_FEED",
            "accession": None, "filing_date": None,
            "exhibit_type": None, "exhibit_document": None,
            "source_sha256": krx["source"]["source_sha256"],
        },
        {
            "source_ref": "HYOSUNG_DART_SNAPSHOT",
            "source_type": "NARRATIVE_SOURCED",
            "accession": None, "filing_date": None,
            "exhibit_type": None, "exhibit_document": None,
            "source_sha256": dart["source"]["source_sha256"],
        },
    ]

    observed_facts = [
        {
            "statement": (
                "CIO watchlist narrative reports 효성중공업 backlog (수주잔고) of "
                "approximately ₩17.5조원 (+63% YoY), citing the company's 2Q2026 "
                "disclosure date (2026-07-31)."
            ),
            "source_ref": "HYOSUNG_WATCHLIST_ROW",
            "source_class": "NARRATIVE_SOURCED",
            "as_of": "2026-08-13",
        },
        {
            "statement": (
                "CIO watchlist narrative reports annual new-order guidance raised from "
                "₩8.4조원 to ₩12조원, citing the company's 2Q2026 earnings disclosure "
                "(2026-07-31)."
            ),
            "source_ref": "HYOSUNG_WATCHLIST_ROW",
            "source_class": "NARRATIVE_SOURCED",
            "as_of": "2026-08-13",
        },
        {
            "statement": (
                f"KRX official close for 298040 on {krx['latest_confirmed_day']} (confirmed) "
                f"was ₩{row['close']:,}, SMA20 ₩{krx['confirmed_metrics']['sma20']:,.0f} "
                f"through {krx['confirmed_metrics']['sma20_through']}."
            ),
            "source_ref": "HYOSUNG_KRX_SNAPSHOT",
            "source_class": "PRICE_FEED",
            "as_of": krx["latest_confirmed_day"],
        },
        {
            "statement": (
                f"DART filing tracking for 298040 shows relevant_count="
                f"{dart['stock']['relevant_count']} of total_count={dart['stock']['total_count']} "
                "as of this snapshot; no DART-extracted content backs the backlog or "
                "new-order guidance figures cited in the watchlist narrative."
            ),
            "source_ref": "HYOSUNG_DART_SNAPSHOT",
            "source_class": "NARRATIVE_SOURCED",
            "as_of": "2026-08-20",
        },
    ]

    forward_inferences = [
        {
            "statement": (
                "If the backlog and raised new-order guidance reported in the CIO "
                "watchlist narrative are accurate, revenue recognition should begin "
                "converting from backlog into reported sales over coming quarters."
            ),
            "basis": (
                "CIO watchlist backlog/guidance narrative (HYOSUNG_WATCHLIST_ROW); "
                "unconfirmed by any DART-exhibit extraction in this repository."
            ),
            "confidence": "LOW",
        },
    ]

    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": "298040.KS",
        "decision_date": decision_date,
        "thesis_id": "FT-298040KS-PILOT-20260821-001",
        "thesis_version": 1,
        "spend_owner": None,
        "capital_commitment": {
            "description": (
                "No exhibit-extracted capital-commitment figure exists for this subject in "
                "this repository. The CIO watchlist narrative cites backlog growth "
                "(수주잔고 +63% YoY) and raised order guidance, but these are "
                "narrative-sourced only (see observed_facts) and are deliberately not "
                "carried as a capital_commitment figure here."
            ),
            "amount_range_or_unknown": "UNKNOWN",
            "currency_or_unknown": "UNKNOWN",
            "source_ref": None,
        },
        "revenue_recipient": "효성중공업 (Hyosung Heavy Industries)",
        "atlas_linked_ticker": "298040.KS",
        "observed_facts": observed_facts,
        "forward_inferences": forward_inferences,
        "assumptions": [
            {
                "statement": (
                    "수주잔고 및 신규 수주 가이던스 수치가 watchlist 서사 그대로 정확하다."
                ),
                "why_unconfirmed": (
                    "저장소 내 DART 공시 원문 추출 근거 없음 (전 기간 relevant_count=0)."
                ),
            },
        ],
        "counter_evidence": [
            {
                "statement": (
                    "DART filing tracking shows 0 filings classified relevant for 298040 "
                    "across every captured day in this repository, meaning no primary "
                    "disclosure content backs the backlog/guidance narrative here."
                ),
                "source_ref": "HYOSUNG_DART_SNAPSHOT",
            },
        ],
        "unknowns": [
            (
                "수주잔고/가이던스 수치는 CIO watchlist 서사이며, 저장소 내 실제 DART "
                "exhibit로 추출된 근거 문서는 없음(전 기간 relevant_count=0)."
            ),
            (
                "'수주잔고 전분기 대비 감소' 등 탈락조건의 '연속' 정의가 Undefined 상태로 "
                "남아있어(watchlist 8/15 Review #2 이월), 규칙 판정 기준 자체가 미확정이다."
            ),
            (
                "config/rules.json에 298040.KS Rule 카드(RULE-0023/0024/0025)가 존재하나, "
                "외부 비준된 Rule 평가 패킷은 저장소에 없음 -> p5_rule_status=NOT_EVALUATED."
            ),
        ],
        "earnings_conversion": {
            "status": "BACKLOG_BUILDING",
            "mechanism": (
                "Watchlist narrative describes backlog growth (+63% YoY) and raised annual "
                "new-order guidance (₩8.4조원->12조원) as a leading indicator for future "
                "revenue recognition; not yet confirmed by DART-extracted filing content."
            ),
            "expected_start_window": "UNKNOWN",
            "expected_duration": "UNKNOWN",
            "revenue_direction": "UP",
            "margin_direction": "UNKNOWN",
            "confidence": "LOW",
            "blockers": [
                "No DART-extracted filing backs the backlog/guidance narrative (relevant_count=0).",
                "'수주잔고 전분기 대비 감소' 탈락조건의 '연속' 정의가 Undefined.",
            ],
        },
        "catalysts": [
            {
                "description": (
                    "Next DART disclosure that could confirm or update the "
                    "watchlist-reported backlog figure."
                ),
                "expected_date_or_window": "UNKNOWN",
                "source_ref": None,
            },
            {
                "description": (
                    "'수주잔고 전분기 대비 감소' 탈락조건의 '연속' 정의 확정 (watchlist상 "
                    "8/15 Review #2 예정이었으나 저장소 내 확정 기록 없음)."
                ),
                "expected_date_or_window": "UNKNOWN",
                "source_ref": "HYOSUNG_WATCHLIST_ROW",
            },
        ],
        "invalidation_conditions": [
            (
                "수주잔고 전분기 대비 감소 (RULE-0023, '연속' 정의 Undefined -- 정의 확정 "
                "전까지 이 조건으로 탈락 판정하지 않음, watchlist 명시)."
            ),
            "7월 저점 종가 1,894,000원 재이탈 (RULE-0024).",
            "기관 순매수 연속 끊김 (RULE-0025, '연속' 정의 Undefined).",
        ],
        "review_dates": ["2026-11-15"],
        "evidence_lineage": evidence_lineage,
        "as_of_ceiling": None,
    }


def build_hyosung_expectations_gap_input(decision_date: str, generated_at: str) -> dict:
    krx = _read_json("data/briefing/krx/298040.json")
    row = krx["latest_confirmed_row"]
    net_institutional = row["net_value"]["기관합계"]
    return {
        "subject": "298040.KS",
        "decision_date": decision_date,
        "generated_at": generated_at,
        "backlog_or_new_orders": {
            "direction": "POSITIVE",
            "evidence_note": (
                "Watchlist narrative reports backlog (수주잔고) +63% YoY, narrative-sourced "
                "only (DART relevant_count=0)."
            ),
        },
        "guidance_changes": {
            "direction": "POSITIVE",
            "evidence_note": (
                "Watchlist narrative reports annual new-order guidance raised from "
                "₩8.4조원 to ₩12조원, narrative-sourced only."
            ),
        },
        "relative_strength_volume": {
            "direction": "NEGATIVE",
            "evidence_note": (
                f"KRX official net institutional value (기관합계) was ₩{net_institutional:,.0f} "
                f"(net sell) on {krx['latest_confirmed_day']} -- official KRX investor-flow "
                "data, not narrative."
            ),
        },
    }


def build_hyosung_price_reflection_input(decision_date: str, generated_at: str) -> dict:
    # decision/price_evidence.py merges every committed data/<date>/krx.json
    # snapshot's embedded daily window (real KRX closes back to 2026-07-06)
    # and chain-links a real KOSPI composite proxy from the committed
    # korea_leadership_context packets (P1-KR-07) -- genuine 1m return,
    # vs_market, position_vs_recent_high, and volume_change figures, not the
    # single-point value data/briefing/krx/298040.json alone could support.
    evidence = PRICE_EVIDENCE.assemble_price_evidence("298040.KS", decision_date)
    return {
        "subject": "298040.KS",
        "decision_date": decision_date,
        "generated_at": generated_at,
        **evidence,
    }


# ═════════════════════════════ 267260.KS (HD Hyundai Electric) ═════════
def build_hd_hyundai_electric_forward_thesis_input(decision_date: str, generated_at: str) -> dict:
    contract = FORWARD_THESIS.load_contract()
    krx = _read_json("data/briefing/krx/267260.json")
    dart = _read_json("data/briefing/dart/267260.json")
    row = krx["latest_confirmed_row"]
    net_institutional = row["net_value"]["기관합계"]

    evidence_lineage = [
        {
            "source_ref": "HD_WATCHLIST_ROW",
            "source_type": "NARRATIVE_SOURCED",
            "accession": None, "filing_date": None,
            "exhibit_type": None, "exhibit_document": None, "source_sha256": None,
        },
        {
            "source_ref": "HD_KRX_SNAPSHOT",
            "source_type": "PRICE_FEED",
            "accession": None, "filing_date": None,
            "exhibit_type": None, "exhibit_document": None,
            "source_sha256": krx["source"]["source_sha256"],
        },
        {
            "source_ref": "HD_DART_SNAPSHOT",
            "source_type": "NARRATIVE_SOURCED",
            "accession": None, "filing_date": None,
            "exhibit_type": None, "exhibit_document": None,
            "source_sha256": dart["source"]["source_sha256"],
        },
    ]

    observed_facts = [
        {
            "statement": (
                f"KRX official close for 267260 on {krx['latest_confirmed_day']} (confirmed) "
                f"was ₩{row['close']:,}, SMA20 ₩{krx['confirmed_metrics']['sma20']:,.0f} "
                f"through {krx['confirmed_metrics']['sma20_through']}."
            ),
            "source_ref": "HD_KRX_SNAPSHOT",
            "source_class": "PRICE_FEED",
            "as_of": krx["latest_confirmed_day"],
        },
        {
            "statement": (
                f"KRX official net institutional value (기관합계) was ₩{net_institutional:,.0f} "
                f"(net sell) on {krx['latest_confirmed_day']}, continuing the institutional "
                "net-selling pattern the CIO watchlist cites as an unmet re-entry gate."
            ),
            "source_ref": "HD_KRX_SNAPSHOT",
            "source_class": "PRICE_FEED",
            "as_of": krx["latest_confirmed_day"],
        },
        {
            "statement": (
                "CIO watchlist row (Coverage stage, 2026-08-13) notes 재진입 게이트 "
                "1/3 미통과 (기관 순매도 지속); this is a Coverage-tier comparison name, "
                "explicitly not promoted from Discovery per CIO note."
            ),
            "source_ref": "HD_WATCHLIST_ROW",
            "source_class": "NARRATIVE_SOURCED",
            "as_of": "2026-08-13",
        },
        {
            "statement": (
                f"DART filing tracking for 267260 shows relevant_count="
                f"{dart['stock']['relevant_count']} of total_count={dart['stock']['total_count']} "
                "as of this snapshot; no backlog/CapEx figures exist for this company "
                "anywhere in this repository."
            ),
            "source_ref": "HD_DART_SNAPSHOT",
            "source_class": "NARRATIVE_SOURCED",
            "as_of": "2026-08-20",
        },
    ]

    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": "267260.KS",
        "decision_date": decision_date,
        "thesis_id": "FT-267260KS-PILOT-20260821-001",
        "thesis_version": 1,
        "spend_owner": None,
        "capital_commitment": {
            "description": (
                "No capital commitment or backlog evidence exists for this subject in this "
                "repository. This is a comparison-group-only name per the CIO watchlist "
                "note (비교군 또는 데이터가 충분할 때만 포함)."
            ),
            "amount_range_or_unknown": "UNKNOWN",
            "currency_or_unknown": "UNKNOWN",
            "source_ref": None,
        },
        "revenue_recipient": "HD현대일렉트릭 (HD Hyundai Electric)",
        "atlas_linked_ticker": "267260.KS",
        "observed_facts": observed_facts,
        "forward_inferences": [],
        "assumptions": [],
        "counter_evidence": [
            {
                "statement": (
                    "Institutional investors remained net sellers of 267260 on the latest "
                    "confirmed KRX day, and the CIO watchlist explicitly records the "
                    "re-entry gate as unmet (1/3 통과)."
                ),
                "source_ref": "HD_KRX_SNAPSHOT",
            },
        ],
        "unknowns": [
            (
                "No backlog, CapEx, or guidance evidence exists anywhere in this "
                "repository for 267260; earnings-conversion status is honestly UNKNOWN."
            ),
            (
                "This is a Coverage-tier comparison name only -- the CIO watchlist "
                "explicitly notes it is not a Discovery-approved case and is not to be "
                "promoted (Discovery 승인 건이 아니므로 승격하지 않는다)."
            ),
            (
                "config/rules.json has no ratified Rule evaluation packet on disk for "
                "267260.KS; p5_rule_status is NOT_EVALUATED."
            ),
        ],
        "earnings_conversion": {
            "status": "UNKNOWN",
            "mechanism": "UNKNOWN",
            "expected_start_window": "UNKNOWN",
            "expected_duration": "UNKNOWN",
            "revenue_direction": "UNKNOWN",
            "margin_direction": "UNKNOWN",
            "confidence": "UNKNOWN",
            "blockers": [
                "No backlog/CapEx/guidance evidence exists for this subject in this repository.",
            ],
        },
        "catalysts": [
            {
                "description": "재진입 게이트 3항목 재점검 (watchlist 다음 이벤트).",
                "expected_date_or_window": "UNKNOWN",
                "source_ref": "HD_WATCHLIST_ROW",
            },
        ],
        "invalidation_conditions": [
            (
                "해당 없음 -- Coverage 단계. 탈락 조건이 정의되어 있지 않으며, 이는 이 "
                "이름이 애초에 Candidate/Ready 승격 대상이 아님을 반영한다 (watchlist 명시)."
            ),
        ],
        "review_dates": [],
        "evidence_lineage": evidence_lineage,
        "as_of_ceiling": None,
    }


def build_hd_hyundai_electric_expectations_gap_input(decision_date: str, generated_at: str) -> dict:
    krx = _read_json("data/briefing/krx/267260.json")
    row = krx["latest_confirmed_row"]
    net_institutional = row["net_value"]["기관합계"]
    return {
        "subject": "267260.KS",
        "decision_date": decision_date,
        "generated_at": generated_at,
        "relative_strength_volume": {
            "direction": "NEGATIVE",
            "evidence_note": (
                f"KRX official net institutional value (기관합계) was ₩{net_institutional:,.0f} "
                f"(net sell) on {krx['latest_confirmed_day']}, continuing the CIO watchlist's "
                "noted institutional net-selling pattern (unmet re-entry gate)."
            ),
        },
    }


def build_hd_hyundai_electric_price_reflection_input(decision_date: str, generated_at: str) -> dict:
    # See build_hyosung_price_reflection_input's comment -- same real
    # KRX + KOSPI-composite evidence assembly, different code.
    evidence = PRICE_EVIDENCE.assemble_price_evidence("267260.KS", decision_date)
    return {
        "subject": "267260.KS",
        "decision_date": decision_date,
        "generated_at": generated_at,
        **evidence,
    }


# ═════════════════════════════ 034020.KS (Doosan Enerbility) ═══════════
def build_doosan_enerbility_forward_thesis_input(decision_date: str, generated_at: str) -> dict:
    contract = FORWARD_THESIS.load_contract()
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": "034020.KS",
        "decision_date": decision_date,
        "thesis_id": "FT-034020KS-PILOT-20260821-001",
        "thesis_version": 1,
        "spend_owner": None,
        "capital_commitment": {
            "description": "No capital commitment evidence available for this subject in this repository.",
            "amount_range_or_unknown": "UNKNOWN",
            "currency_or_unknown": "UNKNOWN",
            "source_ref": None,
        },
        "revenue_recipient": (
            "두산에너빌리티 (Doosan Enerbility) -- identity reference only; not present in "
            "config/universe.json"
        ),
        "atlas_linked_ticker": "034020.KS",
        "observed_facts": [],
        "forward_inferences": [],
        "assumptions": [
            {
                "statement": (
                    "034020 ticker identity used here is sourced from an external "
                    "CIO/Notion Cockpit record, not from this repository's canonical "
                    "config/universe.json asset master."
                ),
                "why_unconfirmed": (
                    "config/universe.json has not been synced to include 034020; only a "
                    "Notion Cockpit note (external to this repo, 2026-08-21/22) records "
                    "the intended identity and Coverage-only PM Watchlist inclusion."
                ),
            },
        ],
        "counter_evidence": [],
        "unknowns": [
            (
                "034020 ticker identity is sourced from an external CIO/Notion record "
                "(Cockpit note, 2026-08-21/22), NOT from this repository's canonical "
                "config/universe.json asset master, which does not yet include this "
                "company. Treat this identity as a reference only, not as "
                "canonical-master-confirmed until synced."
            ),
            (
                "Zero DART filings, zero KRX price observations, and zero backlog/CapEx "
                "evidence exist for 034020 anywhere in this repository "
                "(grep -r \"034020\" returns exactly one hit: config/corp_map.json's "
                "generic ticker->DART-corp-ID lookup table, which is infrastructure, not "
                "company-specific evidence)."
            ),
        ],
        "earnings_conversion": {
            "status": "UNKNOWN",
            "mechanism": "UNKNOWN",
            "expected_start_window": "UNKNOWN",
            "expected_duration": "UNKNOWN",
            "revenue_direction": "UNKNOWN",
            "margin_direction": "UNKNOWN",
            "confidence": "UNKNOWN",
            "blockers": [
                "Zero DART/price/backlog evidence exists for 034020 in this repository.",
            ],
        },
        "catalysts": [],
        "invalidation_conditions": [
            (
                "This subject has zero DART/price/backlog evidence in the Atlas "
                "repository as of this decision_date; no thesis can be evaluated until "
                "real evidence acquisition occurs for 034020."
            ),
        ],
        "review_dates": [],
        "evidence_lineage": [],
        "as_of_ceiling": None,
    }


def build_doosan_enerbility_expectations_gap_input(decision_date: str, generated_at: str) -> dict:
    return {
        "subject": "034020.KS",
        "decision_date": decision_date,
        "generated_at": generated_at,
        # Zero categories supplied -- honest UNKNOWN packet, never invented.
    }


def build_doosan_enerbility_price_reflection_input(decision_date: str, generated_at: str) -> dict:
    # decision/price_evidence.py confirms this honestly: zero KRX snapshots
    # carry code 034020 anywhere, so every field below comes back None --
    # this is the same PRICE_DATA_MISSING outcome as before, now produced by
    # the shared real evidence-assembly layer instead of a hand-written stub.
    evidence = PRICE_EVIDENCE.assemble_price_evidence("034020.KS", decision_date)
    return {
        "subject": "034020.KS",
        "decision_date": decision_date,
        "generated_at": generated_at,
        **evidence,
    }


# ═════════════════════════════ orchestration ════════════════════════════
_BUILDERS = {
    "TSM": (
        build_tsm_forward_thesis_input,
        build_tsm_expectations_gap_input,
        build_tsm_price_reflection_input,
    ),
    "298040.KS": (
        build_hyosung_forward_thesis_input,
        build_hyosung_expectations_gap_input,
        build_hyosung_price_reflection_input,
    ),
    "267260.KS": (
        build_hd_hyundai_electric_forward_thesis_input,
        build_hd_hyundai_electric_expectations_gap_input,
        build_hd_hyundai_electric_price_reflection_input,
    ),
    "034020.KS": (
        build_doosan_enerbility_forward_thesis_input,
        build_doosan_enerbility_expectations_gap_input,
        build_doosan_enerbility_price_reflection_input,
    ),
}


def run_all_pilots(decision_date: str = PILOT_DECISION_DATE, generated_at: str = PILOT_GENERATED_AT) -> dict:
    """Build the full FT -> EG -> PR -> Alpha Review -> Shadow Ledger chain
    for all four Pilot subjects. Returns a dict keyed by subject, each value
    a dict with keys forward_thesis / expectations_gap / price_reflection /
    alpha_review / shadow_ledger_entry (all fully validated packets/records).

    Each subject gets its own independent, single-entry (sequence=1, genesis)
    shadow ledger chain -- this MVP does not merge the four subjects into one
    shared append-only chain.
    """
    results = {}
    for subject in PILOT_SUBJECTS:
        ft_builder, eg_builder, pr_builder = _BUILDERS[subject]

        ft_input = ft_builder(decision_date, generated_at)
        ft_packet = FORWARD_THESIS.build_packet(ft_input)

        eg_input = eg_builder(decision_date, generated_at)
        eg_packet = EXPECTATIONS_GAP.build_packet(eg_input)

        pr_input = pr_builder(decision_date, generated_at)
        pr_packet = PRICE_REFLECTION.build_packet(**pr_input)

        alpha_packet = ALPHA_REVIEW.build_packet(
            forward_thesis_packet=ft_packet,
            expectations_gap_packet=eg_packet,
            price_reflection_packet=pr_packet,
            generated_at=generated_at,
            p5_rule_status="NOT_EVALUATED",
            portfolio_status="NOT_EVALUATED",
        )

        shadow_record = ALPHA_SHADOW_LEDGER.build_record(
            alpha_review_packet=alpha_packet,
            recorded_at=generated_at,
            sequence=1,
            previous_entry_hash=None,
        )

        results[subject] = {
            "forward_thesis": ft_packet,
            "expectations_gap": eg_packet,
            "price_reflection": pr_packet,
            "alpha_review": alpha_packet,
            "shadow_ledger_entry": shadow_record,
        }
    return results


# ═════════════════════════════ comparison / ranking ═════════════════════
# Data-completeness threshold for a TOP_OPPORTUNITY label. Documented here,
# not buried in arithmetic: a subject needs at least this many combined
# observed_facts + evidence_lineage rows, AND fewer than this many combined
# unknowns + missing_inputs rows, to ever qualify for TOP_OPPORTUNITY -- and
# even then only when opportunity_state is already ANTICIPATORY_REVIEW.
TOP_OPPORTUNITY_MIN_EVIDENCE_COUNT = 4
TOP_OPPORTUNITY_MAX_GAP_COUNT = 4


def compare_pilots(bundles: dict) -> dict:
    """Compare the four Pilot subjects' full run_all_pilots() bundles.

    `bundles` is exactly `run_all_pilots()`'s return value: a dict keyed by
    subject, each value holding the five validated packets/records for that
    subject. (This is a deliberate, documented widening of the originally
    sketched `compare_pilots(alpha_review_packets: list[dict])` signature --
    Forward Evidence strength and Data completeness require
    `observed_facts`/`evidence_lineage`/`unknowns` fields that live on the
    Forward Thesis / Expectations Gap / Price Reflection packets, not on the
    Alpha Review packet alone, so this function needs the whole bundle.)

    Returns {subject: {"label": ..., "reasons": [...], "metrics": {...}}}.

    ★ Label-assignment rule (readable, NOT a hidden composite score):
      1. `opportunity_state == "BLOCKED"`   -> label "BLOCKED"
      2. `opportunity_state == "REJECTED"`  -> label "REJECT"
      3. `opportunity_state == "ANTICIPATORY_REVIEW"` AND
         `evidence_strength >= TOP_OPPORTUNITY_MIN_EVIDENCE_COUNT` AND
         `data_gap_count <= TOP_OPPORTUNITY_MAX_GAP_COUNT`
                                              -> label "TOP_OPPORTUNITY"
      4. `opportunity_state` in
         {"ANTICIPATORY_REVIEW", "CONFIRMATION_REVIEW", "EARLY_DISCOVERY"}
         (and rule 3 did not already fire)   -> label "WATCH"
      5. everything else
         (WAIT_FOR_PULLBACK / WAIT_FOR_EVIDENCE / EXPECTATION_EXHAUSTED)
                                              -> label "WAIT"
    Each metric feeding the rule is reported per-subject in `metrics` so the
    rule is auditable, not just asserted.
    """
    out = {}
    for subject, bundle in bundles.items():
        ft = bundle["forward_thesis"]
        eg = bundle["expectations_gap"]["expectations_gap"]
        pr = bundle["price_reflection"]["price_reflection"]
        ar = bundle["alpha_review"]
        shadow = bundle["shadow_ledger_entry"]

        evidence_strength = len(ft["observed_facts"]) + len(ft["evidence_lineage"])
        earnings_conversion_visible = ar["earnings_conversion_status"] != "UNKNOWN"
        data_gap_count = (
            len(ft["unknowns"]) + len(eg["missing_inputs"]) + len(pr["missing_inputs"])
        )
        invalidation_clarity = len(ar["invalidation_conditions"])
        catalysts = ar["catalyst_timing"]["catalysts"]
        nearest_catalyst = min(
            (c["expected_date_or_window"] for c in catalysts), default="UNKNOWN"
        )

        metrics = {
            "opportunity_state": ar["opportunity_state"],
            "forward_evidence_strength": evidence_strength,
            "earnings_conversion_status": ar["earnings_conversion_status"],
            "earnings_conversion_visible": earnings_conversion_visible,
            "expectations_gap_status": eg["status"],
            "expectations_gap_confidence": eg["confidence"],
            "price_reflection_state": pr["price_state"],
            "price_reflection_status": pr["reflection_status"],
            "price_reflection_confidence": pr["confidence"],
            "nearest_catalyst": nearest_catalyst,
            "next_review_date": ar["next_review_date"],
            "invalidation_condition_count": invalidation_clarity,
            "data_gap_count": data_gap_count,
            "shadow_action": shadow["shadow_proposal"]["action"],
        }

        state = ar["opportunity_state"]
        reasons = [f"opportunity_state={state}"]
        if state == "BLOCKED":
            label = "BLOCKED"
            reasons.append(
                f"BLOCKED: forward_evidence_strength={evidence_strength} "
                "(no real evidentiary basis to review)."
            )
        elif state == "REJECTED":
            label = "REJECT"
            reasons.append("REJECTED: usable evidence exists but price/conversion signal says no.")
        elif (
            state == "ANTICIPATORY_REVIEW"
            and evidence_strength >= TOP_OPPORTUNITY_MIN_EVIDENCE_COUNT
            and data_gap_count <= TOP_OPPORTUNITY_MAX_GAP_COUNT
        ):
            label = "TOP_OPPORTUNITY"
            reasons.append(
                f"ANTICIPATORY_REVIEW with forward_evidence_strength={evidence_strength} "
                f">= {TOP_OPPORTUNITY_MIN_EVIDENCE_COUNT} and data_gap_count={data_gap_count} "
                f"<= {TOP_OPPORTUNITY_MAX_GAP_COUNT}."
            )
        elif state in ("ANTICIPATORY_REVIEW", "CONFIRMATION_REVIEW", "EARLY_DISCOVERY"):
            label = "WATCH"
            reasons.append(
                f"{state} without meeting the TOP_OPPORTUNITY evidence/gap thresholds "
                f"(forward_evidence_strength={evidence_strength}, data_gap_count={data_gap_count})."
            )
        else:
            label = "WAIT"
            reasons.append(f"{state}: not a supported entry state right now.")

        out[subject] = {"label": label, "reasons": reasons, "metrics": metrics}
    return out
