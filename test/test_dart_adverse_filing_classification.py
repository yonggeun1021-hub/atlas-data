#!/usr/bin/env python3
"""DART adverse-filing collection/classification regression (offline only).

CIO 확정 2026-09-18 (CLAUDE_CIO_ADVERSE_DISCLOSURE_CARD_20260916.md ·
USER_RATIFICATION_DECISION_BUNDLE_20260918.json 항목 2_adverse_disclosure) —
collectors/dart.py 의 KEYWORDS 를 악재성 공시(Group A·B)까지 확대하고,
매칭된 제목에 사실 기반 그룹(A/B/C) + matched_keyword 를 붙였다.

이 회귀가 잠그는 것 — fixture 제목만으로 network 없이 검증한다:
  · Group A/B/C 각각의 제목이 해당 그룹으로 분류된다.
  · 어느 키워드에도 안 걸리는 제목은 배제되고, "C"로 조용히 떨어지지
    않는다 (group is None 로 명시).
  · 두 그룹에 동시에 걸리는 제목은 결정론적으로(A > B > C 우선순위) 정해진다.
  · 기존 Group C 7종 키워드는 그대로 동작한다(하위호환).
  · KEYWORDS(하위호환 평평한 리스트) == GROUP_A + GROUP_B + GROUP_C, 중복 없음.
  · 매도/매수 차단 등 조치는 이 파일에 없다 — is_relevant/classify 는
    분류만 하고 어떤 실행도 트리거하지 않는다(런타임 함수 부재 자체가 증거).

⛔ live DART API 호출 없음 — collectors/dart.py 를 모듈로 불러와 순수 함수
   (classify/is_relevant/KEYWORDS)만 검사한다. 모듈 최상단이 DART_API_KEY
   부재 시 sys.exit(1) 하므로, import 전에 임시 키를 환경변수로 준다
   (실제 API 호출은 발생하지 않음 — fetch()/build_corp_map() 은 호출하지 않는다).
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
os.environ.setdefault("DART_API_KEY", "offline-test-key")  # 모듈 최상단 fail-closed 가드용
import dart as MODULE                                                # noqa: E402


# ── Group 별 대표 제목 (실제 DART report_nm 표기 관례를 따른 fixture) ──────
GROUP_A_TITLES = {
    "상장폐지 사유 발생": "주권상장폐지사유발생",
    "상장폐지 결정": "상장폐지결정",
    "정리매매 개시": "정리매매개시안내",
    "감사의견 거절": "감사보고서제출(감사의견거절)",
    "감사의견 부적정": "감사보고서제출(감사의견부적정)",
    "감사의견 한정": "감사보고서제출(감사의견한정)",
    "회생절차 신청": "회생절차개시신청",
    "파산 신청": "파산신청",
    "횡령 혐의": "임원의횡령ㆍ배임혐의발생",
    "배임 혐의": "주요주주의배임혐의발생",
}

GROUP_B_TITLES = {
    "불성실공시법인 지정": "불성실공시법인지정",
    "최대주주 변경": "최대주주변경",
    "경영권 분쟁 소송": "경영권분쟁소송제기",
}

GROUP_C_TITLES = {
    "단일판매·공급계약": "단일판매ㆍ공급계약체결",
    "신규시설투자": "신규시설투자등",
    "유상증자": "유상증자결정",
    "전환사채": "전환사채권발행결정",
    "신주인수권부사채": "신주인수권부사채권발행결정",
    "영업(잠정)실적": "영업(잠정)실적(공정공시)",
    "매출액또는손익구조": "매출액또는손익구조30%(대규모법인은15%)이상변경",
}

NON_MATCHING_TITLES = [
    "정기주주총회소집공고",
    "분기보고서",
    "임원ㆍ주요주주특정증권등소유현황보고서",
    "타법인주식및출자증권양도결정",  # Group C 와 무관한 일반 공시
]


class GroupATests(unittest.TestCase):
    def test_each_group_a_title_is_classified_as_a(self):
        for label, title in GROUP_A_TITLES.items():
            with self.subTest(label=label, title=title):
                group, matched = MODULE.classify(title)
                self.assertEqual(group, "A")
                self.assertIn(matched, MODULE.GROUP_A_KEYWORDS)
                self.assertTrue(MODULE.is_relevant(title))


class GroupBTests(unittest.TestCase):
    def test_each_group_b_title_is_classified_as_b(self):
        for label, title in GROUP_B_TITLES.items():
            with self.subTest(label=label, title=title):
                group, matched = MODULE.classify(title)
                self.assertEqual(group, "B")
                self.assertIn(matched, MODULE.GROUP_B_KEYWORDS)
                self.assertTrue(MODULE.is_relevant(title))


class GroupCBackwardCompatibilityTests(unittest.TestCase):
    def test_each_existing_group_c_title_still_matches(self):
        for label, title in GROUP_C_TITLES.items():
            with self.subTest(label=label, title=title):
                group, matched = MODULE.classify(title)
                self.assertEqual(group, "C")
                self.assertIn(matched, MODULE.GROUP_C_KEYWORDS)
                self.assertTrue(MODULE.is_relevant(title))

    def test_all_seven_original_keywords_are_still_present_and_unmodified(self):
        original_seven = [
            "단일판매", "공급계약", "신규시설투자",
            "유상증자", "전환사채", "신주인수권부사채",
            "영업(잠정)실적", "매출액또는손익구조",
        ]
        for kw in original_seven:
            self.assertIn(kw, MODULE.GROUP_C_KEYWORDS)
            self.assertIn(kw, MODULE.KEYWORDS)


class NonMatchingTests(unittest.TestCase):
    def test_titles_matching_no_group_are_excluded_and_not_silently_c(self):
        for title in NON_MATCHING_TITLES:
            with self.subTest(title=title):
                group, matched = MODULE.classify(title)
                # ★ 핵심 계약 — 매칭 실패는 "C"로 조용히 떨어지지 않는다.
                self.assertIsNone(group)
                self.assertIsNone(matched)
                self.assertNotEqual(group, "C")
                self.assertFalse(MODULE.is_relevant(title))

    def test_empty_or_missing_title_is_not_relevant(self):
        for title in ("", None):
            group, matched = MODULE.classify(title)
            self.assertIsNone(group)
            self.assertIsNone(matched)


class DeterministicTwoGroupResolutionTests(unittest.TestCase):
    """제목이 두 그룹 키워드에 동시에 걸릴 때 A > B > C 로 결정된다.

    우선순위는 확정 카드의 조치 강도를 반영한다 — 즉시매도+매수차단(A)이
    신규매수차단(B)보다, 신규매수차단(B)이 기록전용(C)보다 우선한다.
    """

    def test_group_a_wins_over_group_c(self):
        title = "상장폐지 사유 발생 및 유상증자 결정"
        group, matched = MODULE.classify(title)
        self.assertEqual(group, "A")
        self.assertEqual(matched, "상장폐지")

    def test_group_a_wins_over_group_b(self):
        title = "상장폐지 결정 및 최대주주변경"
        group, matched = MODULE.classify(title)
        self.assertEqual(group, "A")
        self.assertEqual(matched, "상장폐지")

    def test_group_b_wins_over_group_c(self):
        title = "최대주주변경 및 단일판매ㆍ공급계약체결"
        group, matched = MODULE.classify(title)
        self.assertEqual(group, "B")
        self.assertEqual(matched, "최대주주변경")

    def test_resolution_is_deterministic_across_repeated_calls(self):
        title = "상장폐지 결정 및 최대주주변경, 유상증자 결정"
        first = MODULE.classify(title)
        second = MODULE.classify(title)
        third = MODULE.classify(title)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(first, ("A", "상장폐지"))


class KeywordUnionInvariantTests(unittest.TestCase):
    def test_keywords_is_exact_union_of_the_three_groups_in_priority_order(self):
        self.assertEqual(
            MODULE.KEYWORDS,
            MODULE.GROUP_A_KEYWORDS + MODULE.GROUP_B_KEYWORDS + MODULE.GROUP_C_KEYWORDS,
        )

    def test_no_keyword_string_is_shared_across_groups(self):
        # 그룹 간 동일 키워드 문자열이 중복되면 우선순위 규칙이 무의미해진다.
        a, b, c = set(MODULE.GROUP_A_KEYWORDS), set(MODULE.GROUP_B_KEYWORDS), set(MODULE.GROUP_C_KEYWORDS)
        self.assertEqual(a & b, set())
        self.assertEqual(b & c, set())
        self.assertEqual(a & c, set())


class NoActionAuthorityTests(unittest.TestCase):
    """이 커밋은 수집·분류만 한다 — 매도/매수 차단 실행은 구현하지 않는다."""

    def test_collector_module_defines_no_action_or_order_functions(self):
        names = {name for name, _ in inspect.getmembers(MODULE, inspect.isfunction)}
        forbidden_substrings = ("sell", "buy", "order", "block", "execute", "trade")
        offending = [
            n for n in names
            if any(sub in n.lower() for sub in forbidden_substrings)
        ]
        self.assertEqual(offending, [], f"조치성 함수가 발견됨: {offending}")

    def test_classify_and_is_relevant_return_plain_data_not_actions(self):
        group, matched = MODULE.classify("상장폐지결정")
        self.assertIsInstance(group, str)
        self.assertIsInstance(matched, str)
        self.assertIsInstance(MODULE.is_relevant("상장폐지결정"), bool)


if __name__ == "__main__":
    unittest.main()
