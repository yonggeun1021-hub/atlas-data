#!/usr/bin/env python3
"""DART adverse-filing collection/classification regression (offline only).

CIO 확정 2026-09-18 (CLAUDE_CIO_ADVERSE_DISCLOSURE_CARD_20260916.md ·
USER_RATIFICATION_DECISION_BUNDLE_20260918.json 항목 2_adverse_disclosure) —
collectors/dart.py 의 KEYWORDS 를 악재성 공시(Group A·B)까지 확대하고,
매칭된 제목에 사실 기반 그룹(A/B/C) + matched_keyword 를 붙였다.

2026-09-18 독립 리뷰(2차) 반영 — 최초 fixture 는 정규식/키워드에 맞춰
만들어낸 제목이었다("A test that only passes because the fixture was shaped
to the pattern is worse than no test"). 아래 fixture 는 실제로 확인한 제목
또는 그 제목을 그대로 인용한 보도자료로 교체했다. 출처는 각 fixture 옆
주석에 남긴다. 확인하지 못한 것(예: "의견한정"의 실제 사례)은 실제
제목으로 위장하지 않고, 그 사실을 그대로 밝힌다.

이 회귀가 잠그는 것 — fixture 제목만으로 network 없이 검증한다:
  · Group A/B/C 각각의 실제(또는 실제로 인용된) 제목이 해당 그룹으로 분류된다.
  · 어느 키워드에도 안 걸리는 제목은 배제되고, "C"로 조용히 떨어지지
    않는다 (group is None 로 명시).
  · "의견"만 있고 거절/부적정/한정이 붙지 않은 제목(의견서, 조회공시
    답변 등)은 매칭되지 않는다 — 오탐 방지.
  · 두 그룹에 동시에 걸리는 제목은 결정론적으로(A > B > C 우선순위) 정해진다.
  · 기존 Group C 7종 키워드는 그대로 동작한다(하위호환).
  · KEYWORDS(하위호환 평평한 리스트) == GROUP_A + GROUP_B + GROUP_C, 중복 없음.
  · 매도/매수 차단 등 조치는 이 파일에 없다 — is_relevant/classify 는
    분류만 하고 어떤 실행도 트리거하지 않는다(런타임 함수 부재 자체가 증거).

⛔ live DART API 호출 없음 — collectors/dart.py 를 모듈로 불러와 순수 함수
   (classify/is_relevant/KEYWORDS)만 검사한다. 모듈 최상단이 DART_API_KEY
   부재 시 sys.exit(1) 하므로, import 전에 임시 키를 환경변수로 준다
   (실제 API 호출은 발생하지 않음 — fetch()/build_corp_map() 은 호출하지 않는다).

★ `승인 회귀 환경(requirements-ci.txt)`은 network 가 필요한 패키지(requests
   포함)를 의도적으로 설치하지 않는다("승인 회귀는 fixture only" — 파일
   상단 주석). collectors/dart.py 자신은 `requests` 를 build_corp_map()/
   fetch() 안에서만 지연 import 하도록 고쳤지만(fetch 를 부르지 않는 이
   회귀엔 필요 없다), `from common import ...` 가 그대로 실행되며
   collectors/common.py 는 여전히 최상단에서 `import requests` 한다 —
   그 경로는 이 파일이 손댈 범위 밖(공유 인프라)이다. 그래서 이 회귀가
   실제로 검사하는 대상이 아닌 `common` 모듈은 import 전에 최소 stub 으로
   sys.modules 에 등록해 그 경로를 완전히 건너뛴다 — 실제 common.py 코드를
   흉내내지 않고, dart.py 가 요구하는 5개 이름만 no-op 으로 채운다.
"""
from __future__ import annotations

import inspect
import os
import sys
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
os.environ.setdefault("DART_API_KEY", "offline-test-key")  # 모듈 최상단 fail-closed 가드용

if "common" not in sys.modules:
    _common_stub = types.ModuleType("common")
    _common_stub.save = lambda *a, **k: None
    _common_stub.save_incident = lambda *a, **k: None
    _common_stub.load_universe = lambda *a, **k: []
    _common_stub.today_kst = lambda: None
    _common_stub.now_utc_iso = lambda: ""
    sys.modules["common"] = _common_stub

import dart as MODULE                                                # noqa: E402


# ── Group A — 실제 확인한 제목 (출처는 각 항목 옆) ─────────────────────────
GROUP_A_TITLES = {
    # DART 표준 공시 항목명. 코스닥/코스피 상장폐지 관련 공시에 쓰이는
    # 실제 제목("주권상장폐지사유발생")이라는 것을 2026-09-18 리뷰에서
    # 재확인했다.
    "상장폐지 사유 발생": "주권상장폐지사유발생",
    # 실제 DART 공시(예: rcpNo=20211125900600 계열 종목)의 제목에
    # "기타시장안내(정리매매 보류 관련)" 형태로 "정리매매"가 그대로 등장한다.
    "정리매매": "기타시장안내(정리매매 보류 관련)",
    # 실제 DART 공시 제목: 콘텐트리중앙, "회생절차개시신청(종속회사의주요경영
    # 사항)", rcpNo=20260616800652, 2026-06-16
    # (https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260616800652).
    "회생절차 개시신청": "회생절차개시신청(종속회사의주요경영사항)",
    # 실제 DART 공시 제목(수피아 dart 미러): "[기재정정]회생절차개시결정"
    # (코스닥 비유테크놀러지) — "회생절차"가 그대로 등장하는 또 다른 실제 사례.
    "회생절차 개시결정(정정)": "[기재정정]회생절차개시결정",
    # 실제 DART 공시 제목: 파산신청, rcpNo=20250102900590, 2025-01-02
    # (https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250102900590).
    "파산 신청": "파산신청",
    # 2026-09-18 리뷰에서 지정한 실제 제목 형태(가운데점 ㆍ 포함) —
    # "횡령ㆍ배임혐의발생". 리뷰가 재확인을 요구하지 않은 항목이라 그대로 인용.
    "횡령ㆍ배임 혐의 발생": "횡령ㆍ배임혐의발생",
    # 실제 보도(이데일리 2021-08-17, bizwatch 2021-08-19)가 그대로 인용한
    # 쌍용자동차·코오롱머티리얼의 반기검토보고서 공시 제목:
    # "반기검토 의견 부적정 또는 의견거절". "의견 부적정"·"의견거절"이 그
    # 안에 그대로 들어 있다 — 감사/분기검토 접두어여도 이 두 부분 문자열
    # 구조는 동일하다.
    "반기검토 의견 부적정 또는 의견거절": "반기검토 의견 부적정 또는 의견거절",
}

# ⚠ "의견한정"/"의견 한정" 은 실제 감사·검토보고서 제목에서 독립적으로
#   확인하지 못했다 — "의견거절"·"의견 부적정"과 동일한 구조("의견"+opinion
#   type)를 갖는 셋째 opinion type이라는 것만 안다(2026-09-18 리뷰 지시).
#   실제 사례를 위장한 fixture를 만들지 않기 위해, 이 키워드가 "A로
#   분류된다"는 개별 fixture 테스트는 만들지 않는다 — 목록에 있다는 사실만
#   구조 점검(KeywordUnionInvariantTests)으로 검증한다.
UNVERIFIED_STRUCTURAL_ONLY_KEYWORDS = ("의견한정", "의견 한정")

GROUP_B_TITLES = {
    # 실제 KIND 공시 제목: "[3S] 최대주주변경"(acptNo=20240125000750),
    # "[AP위성] 최대주주변경"(acptno=20240726000693),
    # "[드림어스컴퍼니] 최대주주변경"(acptno=20251128000536) — 셋 다 붙여 쓴
    # "최대주주변경"이었다. 띄어 쓴 형태는 확인하지 못해 키워드에서 뺐다.
    "최대주주 변경": "[AP위성] 최대주주변경",
    # 실제 더 긴 제목 형태 — "최대주주변경을 수반하는 주식양수도 계약 체결"도
    # "최대주주변경"을 그대로 포함하는지 별도로 확인한다.
    "최대주주변경을 수반하는 주식양수도 계약 체결": "최대주주변경을 수반하는 주식양수도 계약 체결",
    # DART 공시규정 제6조제1항제3호다목(4) 소송 공시의 실제 항목 표기 —
    # "소송등의제기·신청(경영권분쟁소송)". "경영권분쟁"이 그대로 들어 있다.
    "경영권분쟁 소송": "소송등의제기·신청(경영권분쟁소송)",
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

# 실제 DART 정기/일반 공시 제목 — 어느 그룹 키워드에도 걸리지 않아야 한다.
NON_MATCHING_TITLES = [
    "정기주주총회소집공고",
    "분기보고서",
    "임원ㆍ주요주주특정증권등소유현황보고서",
    "타법인주식및출자증권양도결정",  # Group C 와 무관한 일반 공시
]

# ★ 오탐 방지 — "의견"이라는 글자만 있고 거절/부적정/한정이 붙지 않은
#   실제 DART 공시 제목. 리뷰가 예시로 든 두 가지("의견서", "조회공시 요구에
#   대한 답변")를 그대로 쓴다 — 실제 DART 공시 항목명 표기 관례와 일치한다
#   ("조회공시요구(풍문또는보도)에대한답변" 계열, "의견서" 첨부 표기).
BENIGN_TITLES_CONTAINING_UIGYEON = [
    "의견서",
    "조회공시 요구에 대한 답변",
    "조회공시요구(풍문또는보도)에대한답변(의견없음)",
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


class FalsePositiveGuardTests(unittest.TestCase):
    """'의견'이라는 글자만으로는 매칭되지 않는다 — 거절/부적정/한정이 붙어야 A다."""

    def test_benign_titles_with_uigyeon_do_not_match_any_group(self):
        for title in BENIGN_TITLES_CONTAINING_UIGYEON:
            with self.subTest(title=title):
                group, matched = MODULE.classify(title)
                self.assertIsNone(group)
                self.assertIsNone(matched)
                self.assertFalse(MODULE.is_relevant(title))

    def test_at_least_one_benign_fixture_actually_contains_the_trap_substring(self):
        # ★ 함정 자체가 성립하는지 확인한다 — "의견"이라는 글자가 실제로
        #   들어 있는 fixture가 최소 하나는 있어야, 이 회귀가 오탐 방지를
        #   실제로 검사하고 있다고 말할 수 있다.
        self.assertTrue(any("의견" in title for title in BENIGN_TITLES_CONTAINING_UIGYEON))


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

    ★ 아래 제목은 실제 공시 인용이 아니라, 여러 그룹의 확인된 키워드를
      한 문자열에 합쳐 우선순위 알고리즘 자체를 검증하기 위한 합성
      입력이다(경계 조건 테스트) — GROUP_A/B_TITLES 의 "실제 제목" 주장과는
      다른 종류의 근거다.
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

    def test_unverified_structural_variants_are_declared_but_not_claimed_real(self):
        # "의견한정"류는 목록에는 있지만(리뷰 지시 반영), 실제 사례로 확인하지
        # 못했다는 사실 자체를 테스트로 고정한다 — 조용히 "확인됨"으로 넘어가지
        # 않는다.
        for kw in UNVERIFIED_STRUCTURAL_ONLY_KEYWORDS:
            self.assertIn(kw, MODULE.GROUP_A_KEYWORDS)


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
        title = "주권상장폐지사유발생"  # 실제 DART 표준 공시 제목 (GROUP_A_TITLES 와 동일 근거)
        group, matched = MODULE.classify(title)
        self.assertIsInstance(group, str)
        self.assertIsInstance(matched, str)
        self.assertIsInstance(MODULE.is_relevant(title), bool)


if __name__ == "__main__":
    unittest.main()
