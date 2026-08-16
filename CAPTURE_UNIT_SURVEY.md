# capture `unit` metadata — producer → consumer 경로 조사 (CIO 지시 2026-08-16)

⛔ **조사만이다. 코드 변경 0줄 · 수정 미착수 · 수정안 권고 없음.**
⛔ parser 정확성과 섞지 않는다 — production 의 단위 검증은 별개 경로다.

질문: `unit` 은 **downstream identity/provenance 판단에 쓰이는가**,
      아니면 **사람이 보는 진단 metadata 인가**?

---

## 1. 결론 — 소비처가 없다. **capture observability 결함**이다.

| | |
|---|---|
| producer | `collectors/capture_tsmc_fixture.py` (이 파일 하나) |
| 소비처 — production | **없음** |
| 소비처 — Rule 정본 | **없음** |
| 소비처 — 회귀 | 라벨 출력용 1곳(capture 도구 자체 회귀) |
| ⇒ 분류 | **정확성 blocker 아님 · capture observability 결함** |

## 2. producer

```
capture_tsmc_fixture.unit_label(raw, table_start)
   → 표 **앞쪽** 40,000자 안에서만 단위 선언을 찾는다
   → 못 찾으면 None
      · MANIFEST 의 "unit_declaration": null
      · "unit_included_in_slice": false
      · 파일명에 `unit-unknown`
```

★ 실측상 `(Unit:NT$ million)` 은 결정표 **안**(row2/col7)에 있다.
  탐색 범위가 표 앞쪽뿐이라 못 찾은 것이며, **슬라이스에는 온전히 들어 있다.**

## 3. consumer 전수 조사

### 3-1. production 경로 — 완전히 독립이다

```
c4_sec_edgar_check.verify_unit_million(text)     ← 문서 전체 텍스트에서 직접 검사
  · MANIFEST 를 읽지 않는다
  · fixture 파일명을 읽지 않는다
  · capture 도구를 import 하지 않는다
```

⇒ **production 의 단위 검증은 capture metadata 와 무관하다.**
   `unit-unknown` 이 production 판정에 흘러들 경로가 없다.

### 3-2. Rule 정본 — 참조 0건

```
config/rules.json         unit_declaration ✗ · MANIFEST ✗ · unit-unknown ✗
rules/rule_inventory.json unit_declaration ✗ · MANIFEST ✗ · unit-unknown ✗
```

### 3-3. MANIFEST 파일 자체

```
collectors/fixtures/tsmc_6k_MANIFEST.json   읽는 코드 **0곳**
collectors/fixtures/azure_cc_MANIFEST.json  읽는 코드 1곳 (MSFT 회귀 C-0 무결성 확인)
```

★ TSMC MANIFEST 는 **저장만 되고 아무도 읽지 않는다.** (MSFT 쪽은 회귀가 읽는다.)

### 3-4. `unit_declaration` 필드를 읽는 코드

`test/test_capture_tsmc_fixture.py` 6곳 — 전부 **검사 이름 라벨**로 쓴다
(`f"[{t['unit_declaration']}] 원문의 부분 문자열이다"`). 판정 근거가 아니다.
단 A-1 의 `by_unit` 은 million/thousands 표를 **구분하는 키**로 쓴다 — 그러나
그 대상은 합성 문서이고, capture 도구 자신의 회귀다. production 과 무관하다.

## 4. ★ 조사 중 발견한 별건 — 파일명이 선택자로 쓰이고 있다

`unit` 자체와는 다른 문제라 분리해 보고한다.

```
test/test_c4_sec_edgar.py
  545:  if "_t4_" not in name: continue      ← 결정표 fixture 를 **파일명**으로 고른다
  603:  if "_t4_" not in _name: continue
  729:  if "_t4_" not in _name: continue
  492:  _t6 = _fx("…_t6_NT-thousands.html")  ← 천원표를 파일명으로 지목
```

- 회귀가 **어느 fixture 를 쓸지**를 파일명 substring 으로 정한다.
- 관측 identity 자체는 여전히 내용(`find_decision_table`)이 정한다 —
  따라서 **값이 틀릴 경로는 아니다.**
- 그러나 「파일명을 identity 로 쓰지 않는다」는 우리 규율과 **결이 다르다.**
  파일명이 바뀌면 회귀가 조용히 **일부 fixture 를 건너뛴다**(`continue`)
  — 실패하지 않고 검사 수만 줄어든다. 이쪽이 실제 위험이다.

⛔ 이번 조사 범위 밖이라 손대지 않았다. 별건으로 올린다.

## 5. 판정에 필요한 사실만

1. `unit` 의 downstream 소비처는 **없다.** production 단위 검증은 독립 경로다.
2. 따라서 `unit-unknown` 은 **잘못된 값이 흘러가는 결함이 아니라**,
   capture 산출물의 **자기기술(self-description)이 부정확한** 결함이다.
3. 다만 fixture 파일명에 그 값이 들어가 있고, 그 파일명이 회귀의 **선택자**로
   쓰이고 있다 — `unit` 부분은 아니지만(`_t4_`), 같은 계열의 취약점이다.
4. TSMC MANIFEST 는 현재 아무도 읽지 않는다.

⛔ 권고를 적지 않는다.

## 6. 상태

```
C4 row-local uniqueness       CLOSED @ 36aa11b
C4 table-level uniqueness     CLOSED @ ede4ad5
C4 build_header robustness    CLOSED @ 24838c5
P3 · RULE-0003/0007/0008      CLOSED · READY 유지

OPEN
  capture unit 탐색 범위        조사 완료 — 소비처 0 · observability 결함 · 판정 대기
  회귀의 파일명 선택자           신규 발견 · 별건
  공용 helper inventory         후순위
  mutation harness hardening    후순위 (engineering debt)
```
