#!/usr/bin/env python3
"""Briefing renderer <-> B5 semantic checklist alignment (CLAUDE_CIO, 2026-09-14).

S8 user approval package section 6: before BINDING_REFRESH the daily briefing
renderer must emit the formats the B5 checklist (atlas_b5_semantic_checklist/1)
reads, so a correct briefing can PASS instead of HOLD. The checks are NOT
loosened: the evaluators run here are a byte-identical copy of the staging
controller module (sha256 pinned below), and the retained sealed payloads are
shown to still HOLD / PASS_WITH_CORRECTION under the same evaluators.

Inputs are real retained slots: 2026-09-13 AM rev-001, 2026-09-14 AM rev-001
and rev-002 (sealed packets + sealed payloads committed under
evidence/daily_briefing and data/briefing/finalization), and every B5 evaluator
input read from the git blob committed at that slot's finalization seal commit
(test/fixtures/briefing_b5_renderer_alignment_20260914/retained_slots.json).
The 2026-09-13 packet predates PR #722's presentation references, so it goes
through that PR's audited replay derivation (_with_references). No wall clock
is read anywhere: every date comes from the retained bytes.

Rules exercised (staging controller/briefing_semantic_checks.py):
  * B5-3 COMPONENT_AS_OF_DATES -- TOKEN ``([A-Za-z_][A-Za-z0-9_]*)=([^\\s,]+)``;
    FORWARD_ALPHA_REVIEW rows need pilot_decision_date= (the pilot evidence
    date; the checklist reads decision_date/pilot_decision_date/as_of), ROTATION_DISCOVERY DART rows
    filing_date=, OFFICIAL_RELEASE_SUMMARY rows published_at= and
    evidence_as_of=, DYNAMIC_CLOCK KOREA rows price_observation_date=,
    US_BREADTH_MEMBERSHIP members= rows snapshot_date=, FREE_MARKET_DATA
    VIXCLS= rows as_of=; an explicit =UNKNOWN is an explicit statement.
  * B5-4 POINTER_FRESHNESS -- POINTER ``full list: ([A-Za-z0-9_./-]+)\\)``; a
    stale pointer line must carry ``상세 목록 미갱신(기준일 YYYY-MM-DD)``.
  * B5-5 DECISION_RELEVANT_OMISSION -- PAPER_LABEL ('PAPER 참고', '런타임 미승인')
    on the same line as market and candidate_regime; each trend ETF line has
    the symbol, ``close=<source value>`` and its as_of_session_date.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "test/fixtures/briefing_b5_renderer_alignment_20260914"
FIXTURE = json.loads((FIXTURE_DIR / "retained_slots.json").read_text(encoding="utf-8"))
# sha256 of the staging controller's controller/briefing_semantic_checks.py at
# the time of this alignment (S8 section 6 names it as the binding checklist).
B5_CHECKS_SHA256 = "fd98a20461305c45f2ae2c495ef34595de56ebf12f33a4470a42536e62a89792"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B5 = _load("briefing_b5_semantic_checks_fixture", FIXTURE_DIR / "briefing_semantic_checks.py")
# PR #722's audited replay helpers (and its orchestrator instance).
RECENCY = _load(
    "briefing_b5_alignment_recency_helpers", ROOT / "test/test_briefing_content_recency_20260914.py"
)
ORCH = RECENCY.ORCH
SLOTS = {(row["decision_date"], row["slot"], row["revision"]): row for row in FIXTURE["slots"]}

PASS, PWC, NV, HOLD = B5.PASS, B5.PWC, B5.NV, B5.HOLD
PORTAL_NOT_PINNED = "KRX_FRESHNESS_LABEL:PORTAL_RENDER_NOT_PINNED:b5_portal_close_cards"
# Explicit non-PASS results on the aligned renders. None of them is a renderer
# format gap and none is a row that lacks a date:
#   * B5-7 needs the atlas-portal close-card render pinned as a manifest input;
#     atlas-data has no such blob, so the evaluator reports NOT_VERIFIABLE.
# The 2026-09-13 AM B5-1 UNSCOPED_CONFIRMED_EVIDENCE_DATE HOLD documented by
# PR #731 is resolved by scheduled_briefing_retrieval_authority/4, which
# replaces the ambiguous weekend line with market-scoped lines
# (test/test_briefing_weekend_evidence_date_contract_20260914.py).
EXPECTED_NON_PASS = {
    ("2026-09-13", "morning", 1): {B5.PORTAL_PARITY: (NV, PORTAL_NOT_PINNED)},
    ("2026-09-14", "morning", 1): {B5.PORTAL_PARITY: (NV, PORTAL_NOT_PINNED)},
    ("2026-09-14", "morning", 2): {B5.PORTAL_PARITY: (NV, PORTAL_NOT_PINNED)},
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(oid: str) -> bytes:
    completed = subprocess.run(
        ["git", "--no-replace-objects", "cat-file", "blob", oid],
        cwd=ROOT, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise AssertionError(f"retained git blob unavailable (full history required): {oid}")
    return completed.stdout


def _sources(slot: dict) -> dict:
    sources = {}
    for input_id, row in slot["b5_inputs_at_seal_commit"].items():
        body = _git_blob(row["git_blob_sha1"])
        if _sha256(body) != row["sha256"]:
            raise AssertionError(f"B5 input bytes changed: {input_id} {row['repo_path']}")
        if not B5.repo_path_matches(input_id, row["repo_path"]):
            raise AssertionError(f"B5 input path not canonical: {input_id} {row['repo_path']}")
        sources[input_id] = {"repo_path": row["repo_path"], "body": body}
    return sources


def _retained(slot: dict) -> tuple[dict, bytes, bytes]:
    packet_raw = (ROOT / slot["packet_path"]).read_bytes()
    briefing = (ROOT / slot["briefing_path"]).read_bytes()
    sealed = (ROOT / slot["sealed_payload_path"]).read_bytes()
    for label, raw, expected in (
        ("packet", packet_raw, slot["packet_file_sha256"]),
        ("briefing", briefing, slot["briefing_sha256"]),
        ("sealed payload", sealed, slot["sealed_payload_sha256"]),
    ):
        if _sha256(raw) != expected:
            raise AssertionError(f"retained {label} rewritten: {slot['packet_path']}")
    return json.loads(packet_raw), briefing, sealed


def _render_packet(slot: dict) -> dict:
    packet, _, _ = _retained(slot)
    if ORCH.presentation_references(packet) is None:
        packet, _ = RECENCY._with_references(slot["decision_date"], slot["slot"])
    return packet


def _payload(slot: dict, rendered: str) -> bytes:
    """The rendered briefing in the exact sealed delivery envelope.

    briefing_finalization.build_delivery_payload() appends the consume markdown
    and the delivery marker after briefing.md; that tail is taken verbatim from
    the retained sealed payload, only the briefing body is re-rendered.
    """
    _, briefing, sealed = _retained(slot)
    body = briefing.rstrip(b"\n")
    if not sealed.startswith(body):
        raise AssertionError("sealed payload does not start with the retained briefing.md")
    return rendered.encode("utf-8").rstrip(b"\n") + sealed[len(body):]


def _evaluate(slot: dict, payload: bytes) -> dict:
    inputs = B5.build_inputs(
        briefing_date=slot["decision_date"], slot=slot["slot"], payload=payload, sources=_sources(slot)
    )
    return {row["check_id"]: row for row in B5.evaluate_all(inputs)}


class B5ChecklistFixtureTests(unittest.TestCase):
    def test_checks_module_is_the_pinned_staging_copy(self):
        raw = (FIXTURE_DIR / "briefing_semantic_checks.py").read_bytes()
        self.assertEqual(_sha256(raw), B5_CHECKS_SHA256)
        self.assertEqual(FIXTURE["b5_checks_module"]["sha256"], B5_CHECKS_SHA256)
        self.assertEqual(B5.CONTRACT, "atlas_b5_semantic_checklist/1")
        # Pure module: no file, process, network, clock or environment access.
        imports = sorted(set(re.findall(r"^(?:import|from) (\S+)", raw.decode("utf-8"), re.M)))
        self.assertEqual(imports, ["datetime", "decimal", "json", "re"])

    def test_cited_rules_are_the_ones_aligned_to(self):
        self.assertEqual(B5.TOKEN.pattern, r"([A-Za-z_][A-Za-z0-9_]*)=([^\s,]+)")
        self.assertEqual(B5.POINTER.pattern, r"full list: ([A-Za-z0-9_./-]+)\)")
        self.assertEqual(B5.PAPER_LABEL, ("PAPER 참고", "런타임 미승인"))
        self.assertEqual(
            B5.RULES[B5.COMPONENT_AS_OF_DATES],
            ("FORWARD_ALPHA", "DART_ROWS", "OFFICIAL_RELEASE_SUMMARY", "DYNAMIC_CLOCK_ROWS", "SENSOR_ROWS"),
        )
        self.assertEqual(B5.RULES[B5.DECISION_RELEVANT_OMISSION], ("PAPER_CANDIDATE_REGIME", "US_ETF_CLOSES"))
        self.assertEqual(ORCH.PAPER_REFERENCE_RUNTIME_LABEL, B5.PAPER_LABEL[1])

    def test_checks_are_not_loosened_sealed_payloads_still_fail(self):
        """The same evaluators on the retained pre-alignment payloads."""
        for key, slot in SLOTS.items():
            with self.subTest(slot=key):
                _, _, sealed = _retained(slot)
                results = _evaluate(slot, sealed)
                self.assertEqual(results[B5.COMPONENT_AS_OF_DATES]["status"], HOLD)
                self.assertIn(
                    "FORWARD_ALPHA:UNDATED_STALE:FORWARD_ALPHA_REVIEW:034020.KS:decision_date",
                    results[B5.COMPONENT_AS_OF_DATES]["reason"],
                )
                self.assertEqual(results[B5.DECISION_RELEVANT_OMISSION]["status"], PWC)
                self.assertIn("PAPER_CANDIDATE_REGIME:OMITTED", results[B5.DECISION_RELEVANT_OMISSION]["reason"])
                self.assertIn(
                    "US_ETF_CLOSES:DATED_CLOSES_AVAILABLE_BUT_NOT_RENDERED:SPY=764.14@2026-09-11",
                    results[B5.DECISION_RELEVANT_OMISSION]["reason"],
                )
        # The pre-#722 2026-09-13 payload pointed at a separately refreshed file.
        results = _evaluate(SLOTS[("2026-09-13", "morning", 1)], _retained(SLOTS[("2026-09-13", "morning", 1)])[2])
        self.assertEqual(results[B5.POINTER_FRESHNESS]["status"], HOLD)


class AlignedRenderB5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered = {}
        for key, slot in SLOTS.items():
            packet = _render_packet(slot)
            cls.rendered[key] = (packet, ORCH.render_markdown(packet))

    def test_every_b5_check_passes_on_aligned_renders(self):
        for key, slot in SLOTS.items():
            _, rendered = self.rendered[key]
            results = _evaluate(slot, _payload(slot, rendered))
            self.assertEqual(set(results), set(B5.CHECK_IDS))
            for check_id in B5.CHECK_IDS:
                with self.subTest(slot=key, check=check_id):
                    expected = EXPECTED_NON_PASS[key].get(check_id)
                    if expected is None:
                        self.assertEqual(results[check_id]["status"], PASS, results[check_id]["reason"])
                    else:
                        self.assertEqual(results[check_id]["status"], expected[0])
                        self.assertEqual(results[check_id]["reason"].split(" | ")[0], expected[1])

    def test_component_rows_carry_their_own_date_tokens(self):
        unknown = []
        for key, slot in SLOTS.items():
            packet, rendered = self.rendered[key]
            parsed = B5.parse_payload(rendered)
            components = parsed["components"]
            sources = _sources(slot)
            with self.subTest(slot=key):
                forward = [line for line in components["FORWARD_ALPHA_REVIEW"] if "opportunity_state=" in line]
                self.assertEqual(len(forward), 4)
                pilot = re.search(
                    r'^PILOT_DECISION_DATE\s*=\s*["\'](\d{4}-\d{2}-\d{2})["\']',
                    sources["b5_pilot_evidence_intake"]["body"].decode("utf-8"), re.M,
                ).group(1)
                for line in forward:
                    self.assertEqual(B5.tokens(line)["pilot_decision_date"], pilot)
                    self.assertNotIn("decision_date", B5.tokens(line))

                records = json.loads(sources["b5_dart_content"]["body"])["records"]
                dart = [line for line in components["ROTATION_DISCOVERY"] if re.match(r"^\s*- DART \d{6} ", line)]
                self.assertTrue(dart)
                for line in dart:
                    ticker = re.match(r"^\s*- DART (\d{6}) ", line).group(1)
                    filed = {B5._date(r["filing_date"]).isoformat() for r in records if r.get("ticker") == ticker}
                    self.assertIn(B5.tokens(line)["filing_date"], filed)

                summary = json.loads(sources["b5_official_release_summary"]["body"])
                official = [line for line in components["OFFICIAL_RELEASE_SUMMARY"] if "published_at=" in line]
                self.assertEqual(len(official), 1)
                tok = B5.tokens(official[0])
                self.assertEqual(tok["published_at"], summary["observations"][0]["published_at"])
                self.assertEqual(tok["evidence_as_of"], summary["evidence_as_of"][:10])

                korea = [row for row in B5.dynamic_clock_rows(parsed) if row["group"] == "KOREA"]
                self.assertTrue(korea)
                members = [line for line in components["US_BREADTH_MEMBERSHIP"] if "members=" in line]
                vix = [line for line in components["FREE_MARKET_DATA"] if "VIXCLS=" in line]
                self.assertEqual((len(members), len(vix)), (1, 1))
                fred = json.loads(sources["b5_free_market_data"]["body"])["fred"]
                self.assertEqual(B5.tokens(vix[0])["as_of"], fred["observation_date"])

                dated = (
                    [(f"FORWARD_ALPHA_REVIEW:{line.split(':')[0].strip()}", B5.tokens(line)["pilot_decision_date"]) for line in forward]
                    + [("DART", B5.tokens(line)["filing_date"]) for line in dart]
                    + [("OFFICIAL:published_at", tok["published_at"]), ("OFFICIAL:evidence_as_of", tok["evidence_as_of"])]
                    + [(f"DYNAMIC_CLOCK:{row['symbol']}", row["tokens"]["price_observation_date"]) for row in korea]
                    + [("US_BREADTH:snapshot_date", B5.tokens(members[0])["snapshot_date"]),
                       ("VIXCLS:as_of", B5.tokens(vix[0])["as_of"])]
                )
                for label, value in dated:
                    if value == "UNKNOWN":
                        unknown.append((key, label))
                    else:
                        self.assertIsNotNone(B5._date(value), (key, label, value))
        # No retained row genuinely lacks its source date, so none renders UNKNOWN.
        self.assertEqual(unknown, [])

    def test_paper_reference_and_trend_etf_lines(self):
        for key, slot in SLOTS.items():
            packet, rendered = self.rendered[key]
            sources = _sources(slot)
            with self.subTest(slot=key):
                # PR #722's non-authority header is unchanged.
                self.assertIn("- PAPER 참고 판정 (런타임 판정 아님 · 매매/주문 권한 없음)", rendered)
                reference = ORCH.paper_regime_context(packet)
                self.assertIsNone(reference["unknown_reason"])
                for market in reference["markets"]:
                    line = next(
                        line for line in rendered.splitlines()
                        if line.startswith(f"  - {market['market']}: PAPER 참고 판정=")
                    )
                    self.assertTrue(all(part in line for part in B5.PAPER_LABEL))
                    self.assertIn(f"PAPER 참고 판정={market['candidate_regime']} ", line)
                    self.assertTrue(line.endswith("runtime_regime=UNKNOWN; 런타임 미승인"))
                fmd = json.loads(sources["b5_free_market_data"]["body"])
                etfs = fmd["us_market_reference"]["trend_etfs"]
                self.assertEqual([etf["symbol"] for etf in etfs], ["SPY", "QQQ", "IWM"])
                for etf in etfs:
                    self.assertIn(
                        f"    - US trend ETF {etf['symbol']}: close={etf['close']} "
                        f"as_of_session_date={etf['as_of_session_date']} "
                        f"(세션 {etf['as_of_session_date']} 종가 · {slot['decision_date']} 종가로 재표기하지 않음)",
                        rendered,
                    )
                self.assertIn(
                    f"US close values withheld as {slot['decision_date']} closes: independent session "
                    f"evidence is dated 2026-09-11, not {slot['decision_date']}",
                    rendered,
                )

    def test_no_unlabelled_path_pointer_is_rendered(self):
        for key, (_, rendered) in self.rendered.items():
            with self.subTest(slot=key):
                for line in rendered.splitlines():
                    for match in B5.POINTER.finditer(line):
                        self.assertRegex(line, r"상세 목록 미갱신\(기준일 \d{4}-\d{2}-\d{2}\)", match.group(0))

    def test_render_leaves_sealed_packets_untouched(self):
        """Revalidation reads packet bytes only; rendering never mutates them."""
        for key, slot in SLOTS.items():
            with self.subTest(slot=key):
                packet, _, _ = _retained(slot)
                before = copy.deepcopy(packet)
                ORCH.render_markdown(packet)
                self.assertEqual(packet, before)
                ORCH._verify_self_hash(packet)


class ExplicitUnknownAndStalePointerTests(unittest.TestCase):
    def _detail(self, row: dict, decision_date: str = "2026-09-14") -> list[str]:
        return ORCH._format_component_detail(row, decision_date)

    def _b5_3(self, lines: list[str], component_id: str) -> dict:
        payload = "\n".join(
            ["Generated at: 2026-09-14T00:03:23Z", "", f"- **{component_id}**: OK"] + lines
        )
        # Minimal pinned sources so an undated row is aged against a real
        # source date (and a present token is accepted without one).
        sources = {
            "b5_pilot_evidence_intake": {
                "repo_path": "decision/pilot_evidence_intake.py",
                "body": b'PILOT_DECISION_DATE = "2026-08-22"\n',
            },
            "b5_dart_content": {"repo_path": "data/latest_dart_content.json", "body": b'{"records": []}'},
            "b5_official_release_summary": {
                "repo_path": "data/observations/official_release_summary_observations/2026-09-13/x.json",
                "body": b"{}",
            },
        }
        inputs = B5.build_inputs(briefing_date="2026-09-14", slot="morning", payload=payload, sources=sources)
        return B5.evaluate(B5.COMPONENT_AS_OF_DATES, inputs)

    def test_missing_source_dates_render_explicit_unknown(self):
        cases = {
            "FORWARD_ALPHA_REVIEW": (
                {"pilot_subjects": {"TSM": {"opportunity_state": "WAIT_FOR_PRICE"}}},
                "pilot_decision_date=UNKNOWN",
            ),
            "OFFICIAL_RELEASE_SUMMARY": (
                {"subject": "SNDK", "observations": [{"subject": "SNDK", "release_title": "Results"}]},
                "published_at=UNKNOWN 기준일(retrieved)=UNKNOWN evidence_as_of=UNKNOWN",
            ),
            "US_BREADTH_MEMBERSHIP": ({"member_count": 13214}, "snapshot_date=UNKNOWN members=13214"),
            "FREE_MARKET_DATA": ({"vixcls": {"value": "17.84"}}, "VIXCLS=17.84 as_of=UNKNOWN"),
        }
        for component_id, (packet, expected) in cases.items():
            with self.subTest(component=component_id):
                lines = self._detail({"component_id": component_id, "packet": packet})
                self.assertTrue(any(expected in line for line in lines), lines)
                # An explicit UNKNOWN is an explicit statement for B5-3, never a silent omission.
                self.assertEqual(self._b5_3(lines, component_id)["status"], PASS)
        dart = self._detail({
            "component_id": "ROTATION_DISCOVERY",
            "packet": {"dart_observations": {"observation_count": 1, "observations": [
                {"subject_id": "329180", "subject_name": "HD현대중공업", "filing_title": "공시", "filing_date": None},
            ]}},
        })
        self.assertTrue(any("filing_date=UNKNOWN evidence=" in line for line in dart), dart)
        self.assertEqual(self._b5_3(dart, "ROTATION_DISCOVERY")["status"], PASS)
        # Removing the tokens is still a HOLD: the check itself is unchanged.
        stripped = [re.sub(r" (pilot_decision_date|decision_date|published_at|evidence_as_of|snapshot_date|as_of|filing_date)=\S+", "", line)
                    for line in self._detail({"component_id": "FORWARD_ALPHA_REVIEW", "packet": cases["FORWARD_ALPHA_REVIEW"][0]})]
        self.assertEqual(self._b5_3(stripped, "FORWARD_ALPHA_REVIEW")["status"], HOLD)

    def test_trend_etf_close_is_the_source_value_verbatim(self):
        reference = {"as_of_session_date": "2026-09-14", "trend_etfs": [
            {"symbol": "SPY", "close": "764.140", "as_of_session_date": "2026-09-14"},
            {"symbol": "QQQ", "close": None, "as_of_session_date": None},
        ]}
        lines = ORCH._us_trend_etf_close_lines(reference, "2026-09-14")
        self.assertEqual(lines, [
            "    - US trend ETF SPY: close=764.140 as_of_session_date=2026-09-14",
            "    - US trend ETF QQQ: close=UNKNOWN as_of_session_date=UNKNOWN (세션 UNKNOWN 종가 · 2026-09-14 종가로 재표기하지 않음)",
        ])
        self.assertEqual(ORCH._us_trend_etf_close_lines({}, "2026-09-14"), [])

    def test_dynamic_clock_overflow_pointer_labels_an_older_list(self):
        candidates = [
            {"subject": f"C{index}/USD", "price_observation_date": None} for index in range(17)
        ]
        packet = {"decision_date": "2026-09-13", "markets": {"CRYPTO": {"watch_review": candidates}}}
        stale = self._detail({"component_id": "DYNAMIC_CLOCK", "packet": packet}, "2026-09-14")
        pointer = [line for line in stale if "full list:" in line]
        self.assertEqual(pointer, [
            "      - ... +2 more WATCH_REVIEW candidates (full list: this revision's packet.json, "
            "DYNAMIC_CLOCK markets.CRYPTO.watch_review; 기준일=2026-09-13; 상세 목록 미갱신(기준일 2026-09-13))"
        ])
        current = self._detail({"component_id": "DYNAMIC_CLOCK", "packet": packet}, "2026-09-13")
        self.assertNotIn("상세 목록 미갱신", "\n".join(current))


if __name__ == "__main__":
    unittest.main()
