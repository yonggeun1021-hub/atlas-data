"""46칸 전체 reviewed decomposition — 빌더

★ 이 단계의 목적은 "Rule Inventory 구축"이 아니라
  **전체 원문의 무손실 구조화 및 미결정 지점 노출** 이다 (CIO 지시 2026-08-15).

분해는 사람이 했다. 이 스크립트는 그 판독 결과를 데이터로 담고,
각 조각이 원문의 부분문자열인지를 **빌드 시점에 assert** 한다.
오타가 나면 조용히 통과하지 않고 여기서 죽는다.

⛔ 확장 중 금지 (CIO 지시)
  · 새 vocabulary · 새 threshold · 새 의미 생성
  · 중복 Rule 병합 — 서로 다른 source occurrence 로 전부 보존한다.
    canonical rule_id 부여와 dedup 은 전체 분해 후 별도 단계다.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
from vocabulary import (UNRESOLVED, CONNECTIVES,                   # noqa: E402
                        PUNCTUATION)

U = UNRESOLVED

# ── 조각 생성 헬퍼 ──────────────────────────────────────────────────────
def R(txt, kind, eff, dfn, dat, cap=U, src=None, note=""):
    """rule_candidate"""
    return dict(raw_fragment=txt, object_role="rule_candidate", rule_kind=kind,
                downstream_effect=eff, definition_status=dfn, data_status=dat,
                data_capability=cap, source_qualification=src, notes=note)


def X(txt, dfn="DEFINED", dat="MISSING", cap=U, src="SOURCE_UNRESOLVED", note=""):
    """execution_reference — 가격 참조값. Rule Inventory 집계 대상 아님 (CIO 판정 ③)"""
    return dict(raw_fragment=txt, object_role="execution_reference", rule_kind=U,
                downstream_effect="execution_reference", definition_status=dfn,
                data_status=dat, data_capability=cap, source_qualification=src, notes=note)


def N(txt, annotates=(), note=""):
    """non_rule_evidence — 주석·부재 표식·폐기 이력"""
    return dict(raw_fragment=txt, object_role="non_rule_evidence", rule_kind=None,
                downstream_effect=None, definition_status=U, data_status=U,
                data_capability=U, source_qualification=None,
                annotates_split_index=list(annotates), notes=note)


def Q(txt, dfn=U, dat=U, cap=U, src=None, note=""):
    """object_role 자체를 정할 수 없는 조각"""
    return dict(raw_fragment=txt, object_role=U, rule_kind=U, downstream_effect=U,
                definition_status=dfn, data_status=dat, data_capability=cap,
                source_qualification=src, notes=note)


CELLS: list = []

# 결합 표기(CONNECTIVES)와 문장 부호(PUNCTUATION)는 vocabulary.py 에서 단일 정의한다.
# ★ CIO 검수 2026-08-15 ③ — builder 와 validator 가 같은 정의를 공유해야
#   builder 가 보존하기로 한 결합 표기를 validator 가 놓치지 않는다.


def C(ticker, cell, frags, scope="full", partial_reason=None, why=""):
    e = {"candidate_id": f"{ticker}::{cell}", "source_cell": cell,
         "decomposition_scope": scope, "fragments": []}
    if partial_reason:
        e["partial_reason"] = partial_reason
    if why:
        e["selected_because"] = why
    for i, f in enumerate(frags, 1):
        f["split_index"] = i
        f["_orig_idx"] = i          # ★ 결합 표기 삽입 후 주석 대상을 다시 잇기 위한 임시 키
        e["fragments"].append(f)
    CELLS.append(e)


# 자주 쓰는 사유
US_PRICE = "미국 확정 가격의 Official Source 미확보 (CIO 확정 2026-08-14)"
# ★ 가격이 아니라 분기 실적·재무 수치가 필요한 조건에는 이쪽을 쓴다 (CIO 승인 2026-08-15).
US_FINANCIALS = ("미국 분기 실적·재무 수치의 Official Source 미확보 — "
                 "sec.py 는 filing index 와 XBRL 만 수집하고 이 수치를 읽지 않는다")
FIN_BODY = "재무 수치는 dart.py/sec.py 가 파싱하지 않는다 — 원천은 있고 구현이 없다 (§21-9②)"
KR_PRICE = "krx.py v4.1 확정 종가·수급으로 평가 가능"

# ══════════════════════════════════════════════════════════════════════
# MU — Micron
# ══════════════════════════════════════════════════════════════════════
C("MU", "탈락 조건", [
    R("FQ4 $49B 미달", "FAL", "강등 검토", "DEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      # ★ note 정정 (CIO 승인 2026-08-15) — 이 조건은 가격이 아니라 **분기 실적 수치**다.
      #   종전 note 가 US_PRICE(미국 확정 가격 원천 미확보)를 사유로 달고 있었으나
      #   조건 성격과 어긋난 provenance 설명이었다.
      #   ⛔ note 만 정정한다. `SOURCE_UNRESOLVED` 를 해제하지 않으며, 실제 source
      #      resolution 은 P2(미국 실적·재무 수치)에서 별도 판정한다.
      "실적 수치 임계값이 명시돼 정의는 완결. " + US_FINANCIALS),
    R("DRAM ASP 하락 전환", "FAL", "강등 검토", "UNDEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "'전환'의 판정 시점 정의 없음. §21-14(2)가 '산업 가격(DRAM ASP) 사면 0건 — 둘 다 UNDEFINED'라 한 사례."),
])
C("MU", "다음 이벤트", [
    R("9/22 실적(예상)", "MON", "monitoring", U, U, U, None,
      "★ B1-Q2 — '(예상)'은 공표 일정이 아니다. confirmed event date 로 승격 금지."),
])
C("MU", "핵심 지지", [X("800~818", note=US_PRICE)])
C("MU", "핵심 저항", [
    X("931 → 1,050 → 1,255",
      note="3단 사다리를 한 조각으로 둔다. 레벨별로 다른 효과가 원문에 없으므로 쪼개면 추론이 된다."),
])

# ══════════════════════════════════════════════════════════════════════
# TSM — TSMC   (기술적 무효화 · 다음 이벤트는 pilot 판독을 그대로 계승)
# ══════════════════════════════════════════════════════════════════════
C("TSM", "탈락 조건", [
    R("월매출 YoY 40% 미달 2개월 연속", "FAL", "강등 검토", "DEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "'2개월'이 명시돼 있어 효성 '연속'과 달리 UNDEFINED 가 아니다. 월매출은 Atlas 미수집."),
    R("capex 하향", "FAL", "강등 검토", "UNDEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "'하향'의 폭·기준 없음."),
])
C("TSM", "기술적 무효화", [
    R("종가 기준 $398 이탈 — 7/17·7/31·8/3 삼중지지 붕괴 = 8월 반등 구조 전체 무효.",
      "FAL", "강등 검토", "DEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "★ CIO 판정 B2-1 — rule_kind=FAL. 이 조각의 본질은 '$398 을 이탈하면 기존 thesis·구조가 "
      "무효화된다'는 failure/invalidation condition 이다. ENT 처럼 진입 eligibility 를 여는 "
      "조건도, MON 처럼 관측 이벤트를 등록하는 것도 아니다. 새 kind 를 만들지 않는다. "
      "§21-13 이 '→ 강등 검토'로 표기했으나 그것이 Stage 변경 명령이라는 뜻은 아니다 — "
      "Rule 은 '기술적 무효화 조건이 성립/불성립했다'까지만 출력하고 이후 조치는 Decision Layer 소관."),
    R("진입 전 보류선: 종가가 SMA20 $409 아래면 A·B 모두 매수 보류.",
      "ENT", "daily_eligibility", "DEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "§21-13 이 '→ daily_eligibility'로 표기한 조각. 소실된 draft 의 TSM-ENT-01 대응 추정."),
    N("(펌더멘털 무효화=탈락 조건과 분리)", (1, 2), "작성자 주석 — 칸 경계 설명. 판정 효과 없음."),
])
C("TSM", "다음 이벤트", [
    R("★ 8/10경 TSMC 7월 월매출 — Ready Action Plan 발동점.", "MON", "monitoring", U, U, U, None,
      "★ B1-Q1 — 이미 발생·완료된 이벤트(8/10 비약화 확정). CIO 판정: Inventory 에는 보존하되 "
      "active monitoring population 에서는 제외. 기록 보존 ≠ 현재 감시 대상."),
    N("판정 기준 사전 잠금(2026-08-08 CIO 확정 · 주문 판정은 2등급만 사용):", (3, 4),
      "뒤따르는 두 조각의 권위 출처와 잠금 시점."),
    R("약화 = 단월 YoY < +35% OR 누계 YoY < +34.6% → C(매수 취소·Ready 해제)",
      "FAL", "강등 검토", "DEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "임계값 사전 잠금으로 정의 완결. ★ '편입 사유' 칸의 C(월매출 약화)→취소 와 중복 후보 — 합치지 않는다."),
    R("비약화 = 단월 YoY ≥ +35% AND 누계 YoY ≥ +34.6% → Price 판정으로 이월(A/B 활성).",
      "ENT", "daily_eligibility", "DEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "게이트를 여는 효과. 조각 3과 보수 관계이나 효과가 반대라 별개 객체."),
    N("※ 종전 3등급(강화·유지·약화) 및 +50% 기준은 폐기 — 2026-08-09 동기화.", (3, 4),
      "폐기 이력. ⛔ 폐기된 기준을 Rule 후보로 만들지 않는다."),
    N("Action Plan 가격조건은 변경 없음.", (),
      "다른 칸(편입 사유)의 Action Plan 을 가리키는 칸 경계 넘는 참조 — 이 칸만으로 대상 특정 불가."),
    R("이후 9/10 8월 월매출(Review Date),", "MON", "monitoring", U, U, U, None,
      "§21-14(1)이 'TSM-MON-01 에 두 개'라 지목한 것 중 첫째. 날짜 확정."),
    R("10/15 3Q 실적(예상)", "MON", "monitoring", U, U, U, None,
      "★ B1-Q2 — '(예상)'. 공식 일정 미확정/미검증으로 취급한다."),
])
C("TSM", "핵심 지지", [
    X("409~410 (SMA20 + 7/16 종가 409.74)",
      note="★ 같은 SMA20 $409 가 '기술적 무효화' 칸에서는 매수 보류 효과를 갖는다. "
           "이 칸에는 효과 문언이 없으므로 참조값으로 남는다 — 숫자가 아니라 효과가 Rule 이다."),
    X("398~400 삼중지지(7/17·7/31·8/3)",
      note="$398 도 마찬가지 — 효과는 '기술적 무효화' 칸에 있고 여기는 레벨뿐이다."),
])
C("TSM", "핵심 저항", [
    X("424~426 (7/21·8/5·8/7 고점 밀집 + SMA50 425.78 겹침, 3회 거부)"),
    X("479 (52주 고점)"),
])
C("TSM", "진입 패턴", [
    R("B 박스권 돌파 후 재확인", "ENT", "daily_eligibility", "UNDEFINED", "MISSING", U,
      "SOURCE_UNRESOLVED",
      "★ CIO 판정 B2-0 #1 — Rule 이 Stage 를 바꾸는 것과, Stage 변경에 필요한 조건을 "
      "판정하는 것은 다르다. 이 문장의 Rule 역할은 '진입 eligibility 조건이 성립했는가'까지이며 "
      "실제 Ready 승격은 계속 Decision Layer 소관이다. "
      "⛔ definition_status=UNDEFINED 유지 — Entry Language(돌파·재확인의 기계적 정의)가 "
      "정의되지 않았으므로 실행 불가다. 임의 수치화 금지."),
])

# ══════════════════════════════════════════════════════════════════════
# CRDO
# ══════════════════════════════════════════════════════════════════════
C("CRDO", "탈락 조건", [
    R("ANET 대비 상대강도 열위 지속", "FAL", "강등 검토", "UNDEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "'지속'의 정의 없음 — 효성 '연속'과 같은 유형."),
    R("고객 집중 심화(10%+ 고객 3개 미만)", "FAL", "강등 검토", "UNDEFINED", "MISSING",
      "NOT_IMPLEMENTED", None,
      "★ 칸 밖 근거 — 같은 행 '편입 사유'에 '고객 집중도 기준이 분기/연간 중 어느 것인지 미정의'가 "
      "적혀 있다. annotates_split_index 는 칸 안에서만 연결되므로 cross-cell annotation 이 필요하다. "
      + FIN_BODY),
])
C("CRDO", "다음 이벤트", [
    R("ANET 실적 발표 완료(8/4)", "MON", "monitoring", U, U, U, None, "★ B1-Q1 — 완료 이벤트."),
    R("상대강도 판정 8/5·8/6·8/7", "MON", "monitoring", U, U, U, None, "★ B1-Q1 — 완료 이벤트."),
    R("8/8 IC 슬롯 통합", "MON", "monitoring", U, U, U, None, "★ B1-Q1 — 완료 이벤트."),
    R("자사 실적 9/2(예상)", "MON", "monitoring", U, U, U, None, "★ B1-Q2 — '(예상)'."),
])
C("CRDO", "핵심 지지", [X("199~200"), X("157~166")])
C("CRDO", "핵심 저항", [X("213~217"), X("236.5")])

# ══════════════════════════════════════════════════════════════════════
# ANET
# ══════════════════════════════════════════════════════════════════════
C("ANET", "탈락 조건", [
    R("호실적+주가 하락(선반영)", "FAL", "강등 검토", "UNDEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "'호실적'·'하락'의 기준 없음. ★ 같은 행 '편입 사유'가 '주가 하락만으로는 제외하지 않는다'고 "
      "제한을 걸어 두었다 — cross-cell annotation 필요."),
    R("AI 매출 목표 하향 시 CRDO에 슬롯 이양", "FAL", "강등 검토", "DEFINED", "MISSING", U,
      "SOURCE_UNRESOLVED",
      "★ CIO 판정 B2-0 #2 — 원문 occurrence 를 분할하지 않고 FAL 조건으로 보존한다. "
      "한 문장에 두 층이 붙어 있다: 'AI 매출 목표 하향' = ANET 의 탈락 조건(Rule SSOT 안), "
      "'CRDO 에 슬롯 이양' = Portfolio Operation(Rule SSOT 밖). "
      "⛔ 'CRDO 에 슬롯 이양' 을 executable Rule effect 로 해석하지 않는다. "
      "legacy '강등 검토' 는 조건 충족 사실 이상을 의미하지 않는다(CIO 판정 2026-08-15 ②). "
      "★ Portfolio Operation 후보 — 이후 Portfolio Operation extraction 단계에서 별도 객체로 "
      "다룬다. 이번 단계에서 새 Portfolio 객체를 생성하지 않는다. "
      "★ 이 occurrence 를 non_rule_evidence 로 보내면 앞 조각과의 '또는' 결합에서 한쪽 항이 "
      "사라져 ANET 탈락 논리 자체가 훼손된다."),
])
C("ANET", "다음 이벤트", [
    R("2026-08-04 2Q26 실적 발표 완료", "MON", "monitoring", U, U, U, None, "★ B1-Q1 — 완료 이벤트."),
    R("다음은 8/8 IC(Network 슬롯 대표 선정)", "MON", "monitoring", U, U, U, None,
      "★ B1-Q1 — 완료 이벤트."),
    R("이후 3Q26 실적 11월 예상", "MON", "monitoring", U, U, U, None,
      "★ B1-Q2 — 월만 있고 일자 없음."),
])
C("ANET", "핵심 지지", [X("170~176"), X("158")])
C("ANET", "핵심 저항", [X("183.5"), X("189.8")])

# ══════════════════════════════════════════════════════════════════════
# NVDA
# ══════════════════════════════════════════════════════════════════════
C("NVDA", "탈락 조건", [
    R("FQ2 매출 가이드 하단 미달", "FAL", "강등 검토", "DEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "가이드 하단이라는 기준점이 명시. 실적 수치 미수집."),
    R("하이퍼스케일러 2곳+ capex 하향", "FAL", "강등 검토", "UNDEFINED", "MISSING",
      "NOT_IMPLEMENTED", None, "'하향' 기준 없음 · 대상 기업 목록도 미정의. " + FIN_BODY),
])
C("NVDA", "다음 이벤트", [
    R("8/26 실적(확정)", "MON", "monitoring", U, U, U, None,
      "§21-13 이 '→ monitoring'으로 표기한 조각. 날짜 확정."),
    R("실적 전 포지션 제한(PM)", "ENT", "daily_eligibility", "UNDEFINED", "MISSING", U,
      "SOURCE_UNRESOLVED",
      "§21-13 이 '→ daily_eligibility'로 표기한 조각. '실적 전' 기간과 제한 폭이 미정의. "
      "소실된 draft 의 NVDA-ENT-01 대응 추정."),
])
C("NVDA", "핵심 지지", [X("195"), X("192~193")])
C("NVDA", "핵심 저항", [X("206~210"), X("236.5")])

# ══════════════════════════════════════════════════════════════════════
# 000660.KS — SK하이닉스
# ══════════════════════════════════════════════════════════════════════
C("000660.KS", "탈락 조건", [
    R("HBM 예약 취소·LTA 축소", "FAL", "강등 검토", "UNDEFINED", "MISSING", "NOT_IMPLEMENTED", None,
      "★ 가운뎃점(·)이 AND 인지 OR 인지 원문으로 판별 불가 — 쪼개면 추론이 되므로 한 조각으로 둔다. "
      "'축소' 기준도 없음. " + FIN_BODY),
    R("DRAM ASP 하락 전환", "FAL", "강등 검토", "UNDEFINED", "MISSING", U, "SOURCE_UNRESOLVED",
      "★ MU 탈락 조건 조각 2와 문자열까지 동일 — cross-cell duplicate 후보. 합치지 않는다."),
])
C("000660.KS", "다음 이벤트", [
    N("3Q 실적 일정 미공표", (), "★ B1-Q2 — 부재 표식이다. '지남'으로 표현하지 않는다. "
                              "이벤트 객체가 아니라 '공식 일정 미확정' 진술."),
])
C("000660.KS", "핵심 지지", [
    N("급등 후 가격 안정 확인 전 레벨 미설정", (), "부재 표식 — 참조값이 아직 없다는 진술."),
])
C("000660.KS", "핵심 저항", [N("미설정", (), "부재 표식.")])

# ══════════════════════════════════════════════════════════════════════
# 005930.KS — 삼성전자
# ══════════════════════════════════════════════════════════════════════
C("005930.KS", "탈락 조건", [
    R("SK 대비 상대강도 지속 열위", "FAL", "강등 검토", "UNDEFINED", "AVAILABLE", "SUPPORTED", None,
      "'지속'의 정의 없음. 두 한국 종목 종가는 krx.py 로 확보돼 데이터는 있다 — 정의만 없다. "
      "★ 이 조각과 조각 2는 원문상 AND 결합이다(같은 행 '편입 사유'가 '탈락 조건은 AND 구조'라 명시)."),
    R("HBM 공급 확대 미확인", "FAL", "강등 검토", "UNDEFINED", "MISSING", "NOT_IMPLEMENTED", None,
      "'확대'의 기준 없음. ★ 같은 행 '편입 사유'가 'HBM 공급 확대가 명시적으로 해소돼 성립하지 "
      "않는다'고 적어 현재 미성립을 선언 — cross-cell annotation 필요. " + FIN_BODY),
])
C("005930.KS", "다음 이벤트", [N("3Q 실적 일정 미공표", (), "★ B1-Q2 — 부재 표식.")])
C("005930.KS", "핵심 지지", [N("급등 후 가격 안정 확인 전 레벨 미설정", (), "부재 표식.")])
C("005930.KS", "핵심 저항", [N("미설정", (), "부재 표식.")])

# ══════════════════════════════════════════════════════════════════════
# MSFT
# ══════════════════════════════════════════════════════════════════════
C("MSFT", "탈락 조건", [
    R("Azure 성장 45%cc 유의미 하회", "FAL", "강등 검토", "UNDEFINED", "MISSING", U,
      "SOURCE_UNRESOLVED", "★ 임계값(45%cc)은 있으나 '유의미'가 미정의 — 숫자가 있다고 정의가 "
      "완결된 것이 아니다."),
    R("RPO 급둔화", "FAL", "강등 검토", "UNDEFINED", "MISSING", "NOT_IMPLEMENTED", None,
      "'급둔화' 기준 없음. " + FIN_BODY),
])
C("MSFT", "다음 이벤트", [
    R("10/27 실적(예상)", "MON", "monitoring", U, U, U, None, "★ B1-Q2 — '(예상)'."),
])
C("MSFT", "핵심 지지", [
    X("451~457", note="CIO 가 직접 예로 든 사례 — 효과 문언이 없으므로 execution_reference 로 남는다."),
])
C("MSFT", "핵심 저항", [X("466.8"), X("555.5")])

# ══════════════════════════════════════════════════════════════════════
# 267260.KS — HD현대일렉트릭
# ══════════════════════════════════════════════════════════════════════
C("267260.KS", "탈락 조건", [
    N("해당 없음 — Coverage 단계", (), "부재 표식 — 단계상 조건을 두지 않았다는 진술."),
])
C("267260.KS", "다음 이벤트", [
    N("재진입 게이트 3항목 재점검", (),
      "★ CIO 판정 B2-0 #3 — non_rule_evidence. '재점검'은 외부에서 발생·관측되는 사건이 아니라 "
      "우리의 작업/거버넌스 행위다. MON 의 대상이 아니다."),
])

# ══════════════════════════════════════════════════════════════════════
# 329180.KS — HD현대중공업
# ══════════════════════════════════════════════════════════════════════
C("329180.KS", "탈락 조건", [
    N("미정 — Discovery 단계이므로 탈락 조건 미설정. Candidate 승격 심사 시 확정한다.", (),
      "부재 표식 + 향후 절차 진술. ⛔ 여기서 조건을 만들지 않는다."),
])
C("329180.KS", "다음 이벤트", [
    R("8/20 ERCOT 이사회 (BTM 가설 직접 반증 이벤트)", "MON", "monitoring", U, U, U, None,
      "날짜 확정 이벤트. 반증 이벤트라는 성격이 병기돼 있으나 조건문은 아니다."),
])

# ══════════════════════════════════════════════════════════════════════
# 298040.KS — 효성중공업   (pilot 판독 계승)
# ══════════════════════════════════════════════════════════════════════
C("298040.KS", "탈락 조건", [
    R("수주잔고 전분기 대비 감소", "FAL", "강등 검토", "DEFINED", "MISSING", "NOT_IMPLEMENTED", None,
      "§21-9② 가 '현재 데이터로 평가 불가'로 지목한 조건. 소실된 draft 의 298040-FAL-03 대응. " + FIN_BODY),
    R("7월 저점 종가 1,894,000원 재이탈", "FAL", "강등 검토", "DEFINED", "AVAILABLE", "SUPPORTED", None,
      "★ 46칸 전체에서 evaluator_status 가 READY 로 파생되는 유일한 조각. " + KR_PRICE +
      ". ⛔ READY 는 '평가 준비 완료'이지 Stage 변경·투자 판단이 아니다."),
    R("기관 순매수 연속 끊김", "FAL", "강등 검토", "UNDEFINED", "AVAILABLE", "SUPPORTED", None,
      "★ '연속'의 정의가 없다. 데이터는 있고 정의가 없다 — 298040-FAL-02 대응. ⛔ 정의를 만들지 않는다."),
    N("⚠ '연속'의 정의가 Undefined(8/15 Review #2 이월). 정의 전까지 이 조건으로 탈락 판정하지 않는다.",
      (3,), "원문 자신이 붙인 사용 금지 표식. 조각 3의 UNDEFINED 근거."),
])
C("298040.KS", "다음 이벤트", [
    N("Expectations Gap 판정 기준 확정 (8/15 Review)", (),
      "★ CIO 판정 B2-0 #4 — non_rule_evidence. 날짜가 붙었다고 시장 이벤트가 되지 않는다. "
      "'(8/15 Review)' 는 작업의 due/review date 이지 MON event date 가 아니다. "
      "판정 기준은 날짜가 아니라 사건의 성격이다."),
])

# ══════════════════════════════════════════════════════════════════════
# SNDK — SanDisk
# ══════════════════════════════════════════════════════════════════════
C("SNDK", "탈락 조건", [
    N("미정 — Discovery 단계이므로 탈락 조건 미설정. Candidate 승격 심사 시 확정한다. "
      "(현장에서 정의 만들지 않음)", (),
      "★ 부재 표식이면서 동시에 '현장에서 정의 만들지 않음'이라는 지시를 담고 있다."),
])
C("SNDK", "기술적 무효화", [
    N("미설정 — 8/13 종가를 2소스로 확정하지 못해 기준점 설정 불가. ❓", (),
      "부재 표식 + 사유. 데이터 미확정이 정의 부재의 원인임을 원문이 밝힌다."),
])
C("SNDK", "다음 이벤트", [
    R("다음 분기 실적 — 확약계약 비율·ASP 조건 공시 여부 (일자 ❓미확인)", "MON", "monitoring",
      U, U, U, None, "★ B1-Q2 — 일자 미확인이 원문에 명시."),
    R("보조: 8/21 한국 8월 1~20일 수출 낸드 항목", "MON", "monitoring", U, U, U, None,
      "날짜 확정. '보조'라는 우선순위 표기가 붙어 있으나 정본에 대응 어휘가 없어 반영하지 않는다."),
])
C("SNDK", "핵심 지지", [N("❓미확인", (), "부재 표식.")])
C("SNDK", "핵심 저항", [N("❓미확인", (), "부재 표식.")])
C("SNDK", "진입 패턴", [N("미지정", (), "부재 표식 — select 의 명시적 '미지정' 값.")])


# ── 빌드 ────────────────────────────────────────────────────────────────
def place_and_merge(cid, raw, fragments, scope):
    """사람이 정한 조각을 원문에 배치하고, 사이에 남은 결합 표기를 객체로 완성한다.

    ★★ CIO 검수 2026-08-15 ① — occurrence 배치는 fail-closed 다.
       `raw.find(t, cur)` 가 실패했을 때 원문 앞부분에서 다시 찾는 fallback 은
       **제거했다.** 그 fallback 이 있으면 사람이 조각 순서를 틀리게 적어도
       이전 occurrence 를 다시 집어 조용히 통과한다. 게다가 `-1` 을 그대로
       span 에 넣으면 무손실 계산 자체가 깨진다.

       두 검사는 역할이 다르다:
         `t not in raw`        → 조각이 원문에 **존재**하는가
         `raw.find(t, cur) < 0` → 그 occurrence 가 **순서를 지키는가**
       후자가 무손실 빌더의 invariant 다.
    """
    errs, placed, cur = [], [], 0
    for fr in fragments:
        t = fr["raw_fragment"]
        if t not in raw:
            errs.append(f"{cid}#{fr['split_index']}: 원문에 없는 조각 → {t[:40]!r}")
            continue
        i = raw.find(t, cur)
        if i < 0:
            errs.append(f"{cid}#{fr['split_index']}: 현재 cursor({cur}) 이후에서 조각을 "
                        f"찾을 수 없음 — 순서/occurrence 불일치 → {t[:40]!r}")
            continue                      # ⛔ fallback 없음. -1 을 배치하지 않는다.
        placed.append((i, i + len(t), fr))
        cur = i + len(t)

    if errs:
        return list(fragments), errs      # 배치 실패 시 병합을 시도하지 않는다

    merged, prev_end = [], 0
    for a, b, fr in placed:
        gap = raw[prev_end:a]
        token = gap.strip()
        if token:
            if token in PUNCTUATION:
                pass                              # 문장 부호 — 보존 대상 아님
            elif token in CONNECTIVES:
                merged.append(N(token, (), "★ 원문의 결합 표기. 좌우 조각이 어떻게 결합되는지를 "
                               "담고 있으므로 버리지 않는다. "
                               "⛔ AND/OR 실행 semantics 는 정의하지 않는다 — 보존만 한다."))
            elif scope != "partial":
                errs.append(f"{cid}: 결합 표기가 아닌 미분해 원문 {token[:40]!r}")
        merged.append(fr)
        prev_end = b

    tail = raw[prev_end:].strip()
    if tail and scope != "partial":
        if tail in PUNCTUATION:
            pass
        elif tail in CONNECTIVES:
            merged.append(N(tail, (), "원문 말미 결합 표기."))
        else:
            errs.append(f"{cid}: 말미 미분해 원문 {tail[:40]!r}")

    # 번호 재부여 + 사람이 적은 주석 대상을 새 번호로 재매핑
    # ★ 이 재매핑이 없으면 결합 표기가 끼어든 만큼 주석이 엉뚱한 조각을 가리킨다
    #   (효성 '연속' 주석이 '7월 저점 재이탈'을 가리키게 되는 사고)
    for k, fr in enumerate(merged):
        fr["split_index"] = k + 1
    remap = {fr["_orig_idx"]: fr["split_index"] for fr in merged if "_orig_idx" in fr}
    for fr in merged:
        if "_orig_idx" in fr and fr.get("annotates_split_index"):
            fr["annotates_split_index"] = [remap[o] for o in fr["annotates_split_index"]
                                           if o in remap]
    for k, fr in enumerate(merged):
        if fr["object_role"] == "non_rule_evidence" and fr["raw_fragment"] in CONNECTIVES:
            fr["annotates_split_index"] = [j + 1 for j in (k - 1, k + 1)
                                           if 0 <= j < len(merged)]
    for fr in merged:
        fr.pop("_orig_idx", None)
    return merged, errs


def main() -> None:
    with open(os.path.join(ROOT, "config", "rules.candidates.json"), encoding="utf-8") as f:
        cand = json.load(f)
    raws = {c["candidate_id"]: c["raw_text"] for c in cand["candidates"]}
    urls = {c["candidate_id"]: c["source_page_url"] for c in cand["candidates"]}

    errs = []
    for cell in CELLS:
        cid = cell["candidate_id"]
        if cid not in raws:
            errs.append(f"{cid}: 후보 파일에 없는 셀")
            continue
        cell["source_page_url"] = urls[cid]
        raw = raws[cid]

        merged, cell_errs = place_and_merge(cid, raw, cell["fragments"],
                                            cell["decomposition_scope"])
        errs.extend(cell_errs)
        cell["fragments"] = merged

    covered = {c["candidate_id"] for c in CELLS}
    missing = sorted(set(raws) - covered)
    if missing:
        errs.append(f"미분해 셀 {len(missing)}건 → {missing}")
    if errs:
        print("BUILD FAILED"); [print("  ✗", e) for e in errs]; sys.exit(1)

    payload = {
        "artifact": "B1 reviewed decomposition — 46칸 전체",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "purpose": ("Rule Inventory 를 만드는 것이 아니라 전체 원문의 무손실 구조화 및 "
                    "미결정 지점 노출 (CIO 지시 2026-08-15)"),
        "dedup_policy": ("중복 Rule 을 합치지 않는다. 서로 다른 source occurrence 로 전부 보존하며 "
                         "canonical rule_id 부여와 dedup 은 전체 분해 후 별도 단계다."),
        "legacy_tokens": {"강등 검토": "CIO 판정 2026-08-15 #2 — Rule Evaluator 최종 effect 로 "
                                     "승인하지 않음. legacy/provisional 보존만 · executable semantics 금지"},
        "inventory_scope": {
            "execution_reference": "Rule Inventory 집계 대상 제외 (CIO 판정 ③)",
            "monitoring": "Rule Inventory 포함 · Evaluator Population 제외 (CIO 판정 ④)",
        },
        "b1_frozen": {
            "sha256": "b8212ffe3e5e88c1ae93097806fe36a541bd12ed4ae33daeba5cac5b8460e153",
            "state": "B1 PASS / CLOSED (2026-08-15)",
            "note": ("★ B1 당시 상태를 증명하는 provenance 다. 덮어쓰지 않는다. "
                     "이 파일의 현재 해시는 B2 adjudicated hash 이며 둘은 다른 것을 증명한다 — "
                     "전자는 '원문 분해가 어땠는가', 후자는 'CIO 가 미결정을 어떻게 판정했는가'."),
            "provenance_copy": "rules/decompose_full.b1_frozen.json",
        },
        "b2_adjudication": {
            "status": "B2-0 CLOSED (CIO 판정 2026-08-15)",
            "decisions": [
                {"id": "TSM::진입 패턴#1", "object_role": "rule_candidate",
                 "rule_kind": "ENT", "downstream_effect": "daily_eligibility",
                 "definition_status": "UNDEFINED 유지 — Entry Language 미정의로 실행 불가"},
                {"id": "ANET::탈락 조건#3", "object_role": "rule_candidate",
                 "rule_kind": "FAL", "downstream_effect": "강등 검토 (legacy/provisional)",
                 "note": "원문 분할 없음 · CRDO 슬롯 이양은 executable effect 아님 · "
                         "Portfolio Operation 후보로 annotation 만"},
                {"id": "267260.KS::다음 이벤트#1", "object_role": "non_rule_evidence",
                 "rule_kind": None, "downstream_effect": None},
                {"id": "298040.KS::다음 이벤트#1", "object_role": "non_rule_evidence",
                 "rule_kind": None, "downstream_effect": None},
            ],
            "b2_1": {"id": "TSM::기술적 무효화#1", "rule_kind": "FAL",
                     "note": "기술적 무효화는 기존 thesis/구조의 failure condition 이며 "
                             "ENT/MON 이 아니다. definition/data/source field 변경 없음."},
            "policy": ("★ MON = 외부에서 발생하거나 관측되는 사건. "
                       "내부 review·재점검·기준 확정·의사결정 작업은 날짜가 있어도 MON 이 아니다. "
                       "— 새 vocabulary 가 아니라 기존 MON/monitoring 의 분류 경계 확정이다."),
        },
        "cells": CELLS,
    }
    out = os.path.join(ROOT, "rules", "decompose_full.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"BUILD OK → {out}")
    print(f"  셀 {len(CELLS)} · 조각 {sum(len(c['fragments']) for c in CELLS)}")


if __name__ == "__main__":
    main()
