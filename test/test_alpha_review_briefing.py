#!/usr/bin/env python3
"""P8-11 stage 2 — Alpha Review briefing renderer regression."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "briefing" / "alpha_review_briefing.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BRIEFING = load_module("alpha_review_briefing", SOURCE)

FT_FIXTURE = load_module("briefing_ft_fixture", ROOT / "test" / "test_forward_thesis.py")
EG_FIXTURE = load_module("briefing_eg_fixture", ROOT / "test" / "test_expectations_gap.py")
PR_FIXTURE = load_module("briefing_pr_fixture", ROOT / "test" / "test_price_reflection.py")
ALPHA_FIXTURE = load_module("briefing_alpha_fixture", ROOT / "test" / "test_alpha_review.py")
PEI = load_module("briefing_pei", ROOT / "decision" / "pilot_evidence_intake.py")

EXPECTED_SECTION_HEADERS = [
    "## 1. 종목",
    "## 2. 왜 지금 보는가",
    "## 3. 확인된 사실",
    "## 4. Atlas의 미래 가설",
    "## 5. 시장이 놓친 것으로 추정하는 부분",
    "## 6. 실적 전환 예상 구간",
    "## 7. 현재 가격/반영 정도",
    "## 8. 다음 촉매",
    "## 9. 탐색 진입 조건",
    "## 10. 증액 조건",
    "## 11. 무효화 조건",
    "## 12. 현재 행동",
    "## 확신도",
    "## 부족한 데이터",
]


def synthetic_bundle():
    ft = FT_FIXTURE.MODULE.build_packet(FT_FIXTURE.minimal_input())
    eg = EG_FIXTURE.MODULE.build_packet({
        "subject": ft["subject"],
        "decision_date": ft["decision_date"],
        "generated_at": ft["generated_at"],
        "revenue_margin_trend": {"direction": "POSITIVE", "evidence_note": "synthetic"},
    })
    pr = PR_FIXTURE.MODULE.build_packet(
        subject=ft["subject"],
        decision_date=ft["decision_date"],
        generated_at=ft["generated_at"],
    )
    alpha = ALPHA_FIXTURE.MODULE.build_packet(
        forward_thesis_packet=ft,
        expectations_gap_packet=eg,
        price_reflection_packet=pr,
        generated_at=ft["generated_at"],
    )
    shadow_module = load_module("briefing_shadow_fixture", ROOT / "shadow" / "alpha_shadow_ledger.py")
    shadow = shadow_module.build_record(
        alpha_review_packet=alpha, recorded_at=ft["generated_at"], sequence=1,
    )
    return {
        "forward_thesis": ft, "expectations_gap": eg, "price_reflection": pr,
        "alpha_review": alpha, "shadow_ledger_entry": shadow,
    }


class AlphaReviewBriefingTests(unittest.TestCase):
    def _assert_all_sections_present(self, markdown: str):
        for header in EXPECTED_SECTION_HEADERS:
            self.assertIn(header, markdown, f"missing section header: {header}")

    def test_synthetic_fixture_renders_all_sections(self):
        bundle = synthetic_bundle()
        markdown = BRIEFING.render_briefing(
            alpha_review_packet=bundle["alpha_review"],
            forward_thesis_packet=bundle["forward_thesis"],
            expectations_gap_packet=bundle["expectations_gap"],
            price_reflection_packet=bundle["price_reflection"],
            shadow_ledger_entry=bundle["shadow_ledger_entry"],
        )
        self._assert_all_sections_present(markdown)

    def test_real_tsm_packet_renders_all_sections(self):
        results = PEI.run_all_pilots()
        bundle = results["TSM"]
        markdown = BRIEFING.render_briefing(
            alpha_review_packet=bundle["alpha_review"],
            forward_thesis_packet=bundle["forward_thesis"],
            expectations_gap_packet=bundle["expectations_gap"],
            price_reflection_packet=bundle["price_reflection"],
            shadow_ledger_entry=bundle["shadow_ledger_entry"],
        )
        self._assert_all_sections_present(markdown)
        self.assertIn("EXHIBIT_EXTRACTED", markdown)
        self.assertIn("NARRATIVE_SOURCED", markdown)

    def test_facts_and_inferences_never_share_rendered_text(self):
        """Structural separation regression: no non-trivial line of text
        appears in both the §3 확인된 사실 block and the §4 Atlas의 미래 가설
        block for a packet where they carry genuinely distinct content."""
        bundle = synthetic_bundle()
        # Force distinct fact/inference text on the synthetic fixture.
        ft = FT_FIXTURE.MODULE.build_packet(FT_FIXTURE.minimal_input(
            observed_facts=[FT_FIXTURE.observed_fact(statement="Distinct observed fact text.")],
            forward_inferences=[FT_FIXTURE.forward_inference(statement="Distinct forward guess text.")],
        ))
        eg = EG_FIXTURE.MODULE.build_packet({
            "subject": ft["subject"], "decision_date": ft["decision_date"],
            "generated_at": ft["generated_at"],
        })
        pr = PR_FIXTURE.MODULE.build_packet(
            subject=ft["subject"], decision_date=ft["decision_date"], generated_at=ft["generated_at"],
        )
        alpha = ALPHA_FIXTURE.MODULE.build_packet(
            forward_thesis_packet=ft, expectations_gap_packet=eg,
            price_reflection_packet=pr, generated_at=ft["generated_at"],
        )
        markdown = BRIEFING.render_briefing(
            alpha_review_packet=alpha, forward_thesis_packet=ft,
            expectations_gap_packet=eg, price_reflection_packet=pr,
        )
        section_3 = markdown.split("## 3. ")[1].split("## 4. ")[0]
        section_4 = markdown.split("## 4. ")[1].split("## 5. ")[0]
        self.assertIn("Distinct observed fact text.", section_3)
        self.assertNotIn("Distinct observed fact text.", section_4)
        self.assertIn("Distinct forward guess text.", section_4)
        self.assertNotIn("Distinct forward guess text.", section_3)

    def test_every_observed_fact_line_shows_source_class(self):
        bundle = synthetic_bundle()
        markdown = BRIEFING.render_briefing(
            alpha_review_packet=bundle["alpha_review"],
            forward_thesis_packet=bundle["forward_thesis"],
            expectations_gap_packet=bundle["expectations_gap"],
            price_reflection_packet=bundle["price_reflection"],
        )
        section_3 = markdown.split("## 3. ")[1].split("## 4. ")[0]
        for fact in bundle["forward_thesis"]["observed_facts"]:
            self.assertIn(f"[{fact['source_class']}]", section_3)

    def test_price_reflection_section_shows_data_source_scope(self):
        results = PEI.run_all_pilots()
        bundle = results["TSM"]
        markdown = BRIEFING.render_briefing(
            alpha_review_packet=bundle["alpha_review"],
            forward_thesis_packet=bundle["forward_thesis"],
            expectations_gap_packet=bundle["expectations_gap"],
            price_reflection_packet=bundle["price_reflection"],
        )
        section_7 = markdown.split("## 7. ")[1].split("## 8. ")[0]
        self.assertIn(bundle["price_reflection"]["price_reflection"]["data_source_scope"], section_7)


if __name__ == "__main__":
    unittest.main()
