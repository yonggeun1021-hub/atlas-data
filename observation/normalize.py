#!/usr/bin/env python3
"""Observation Layer 층 ③ — Normalization.

★ 이 층의 유일한 책임: **source 표기법을 아는 것.**
   `(1)%` 가 회계식 음수라는 지식은 여기에만 있다.
   ⛔ evaluator 는 source 표기법을 알아서는 안 되고, observer(층 ②)도 표기법 해석
      책임을 지면 안 된다. 그래서 표기법 지식을 이 한 층에 가둔다.

⛔ 이 층이 하지 않는 것
   ⛔ 취득 · 행/열 지목 (층 ① ②)   ⛔ 저장 (층 ④)
   ⛔ 비교 · pair (층 ⑤)            ⛔ 임계값 판정 (층 ⑥)

★ 값 표현 계약 (CIO 판정 S2)
   `51%`   → Decimal("51")     sign_convention = none
   `(1)%`  → Decimal("-1")     sign_convention = accounting_parentheses
   `-1%`   → Decimal("-1")     sign_convention = explicit_minus
   `1.5%`  → Decimal("1.5")    sign_convention = none
   ⛔ float 금지 — 반올림이 10%p 경계 판정을 오염시킨다. exact decimal 만 쓴다.
   ⛔ `%` 없는 숫자 자동 수용 금지 — 현재 source 계약은 percent 열이다.
   ⛔ malformed 를 임의 보정하지 않는다 — 공백·괄호가 애매하면 **fail-closed**.
   ★ raw 문면은 어떤 경우에도 폐기하지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal, InvalidOperation

UNIT_PCT = "pct"

# ── sign convention — source 표기 복원성이 목적이다 ────────────────────
#   ⛔ taxonomy 를 키우지 않는다. source 에 실제 부호 표기가 없으면 `none` 이다.
SIGN_NONE = "none"
SIGN_PARENS = "accounting_parentheses"
SIGN_MINUS = "explicit_minus"

# ── 입력 형식 — 좁게 못 박는다 ─────────────────────────────────────────
#   ⛔ 앞뒤·내부 공백을 허용하지 않는다 (임의 trim 은 보정이다).
#   ⛔ `+` 는 승인된 표기가 아니다 — 관측되면 CIO 판정으로 연다.
#   ⛔ 괄호와 마이너스의 동시 사용, 이중 부호, 괄호 불균형은 전부 불일치다.
_NUM = r"\d+(?:\.\d+)?"
RE_PLAIN = re.compile(rf"^({_NUM})%$")
RE_MINUS = re.compile(rf"^-({_NUM})%$")
RE_PARENS = re.compile(rf"^\(({_NUM})\)%$")


class NormalizationError(ValueError):
    """정규화 실패. ★ 이 예외가 나면 완성된 ObservationRecord 를 만들지 않는다."""


def normalize_pct(raw) -> dict:
    """percent 문면 → {raw_value, numeric_value, unit, sign_convention}.

    ⛔ 실패를 값으로 삼키지 않는다 — `NormalizationError` 를 던진다.
       (기존 collector 의 `_n()` 은 실패를 「판정 불가」 문자열로 삼키고 계속했다.
        그 fail-open 성질을 이 층으로 승격시키지 않는다.)
    """
    if not isinstance(raw, str):
        raise NormalizationError(f"문자열이 아니다: {type(raw).__name__}")
    if raw == "":
        raise NormalizationError("빈 문자열")

    m = RE_PARENS.match(raw)
    if m:
        sign, digits = SIGN_PARENS, "-" + m.group(1)
    else:
        m = RE_MINUS.match(raw)
        if m:
            sign, digits = SIGN_MINUS, "-" + m.group(1)
        else:
            m = RE_PLAIN.match(raw)
            if not m:
                raise NormalizationError(f"승인된 percent 표기가 아니다: {raw!r}")
            sign, digits = SIGN_NONE, m.group(1)

    try:
        value = Decimal(digits)
    except InvalidOperation as e:                                   # pragma: no cover
        raise NormalizationError(f"decimal 변환 실패: {raw!r}") from e

    return {
        "raw_value": raw,               # ★ 원문 그대로 — 폐기 금지
        "numeric_value": str(value),    # ★ JSON 은 문자열. ⛔ float 로 만들지 않는다
        "unit": UNIT_PCT,
        "sign_convention": sign,
    }


def numeric(field: dict) -> Decimal:
    """정규화된 필드에서 exact decimal 을 되살린다. ⛔ float 로 바꾸지 않는다."""
    return Decimal(field["numeric_value"])


# ── economic period 정규화 ────────────────────────────────────────────
#   ★ 층 ④ Observation Store 의 key 는 `economic_period_end` 를 요구한다.
#     그 정규형을 만드는 것도 source 표기법 지식이므로 이 층의 책임이다.
#   ⛔ 알 수 없는 월 이름·형식은 보정하지 않고 fail-closed.
_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
           "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
           "december": 12}
RE_PERIOD_END = re.compile(r"^([A-Z][a-z]+) (\d{1,2}), (\d{4})$")


def normalize_period_end(raw) -> dict:
    """`September 30, 2025` → `2025-09-30`. raw 는 함께 보존한다.

    ★ 유효성은 **실제 calendar semantics** 로 본다 (CIO 판정 S2.1 ·
      `INVALID_CALENDAR_DATE_ACCEPTED`).
      `1 <= day <= 31` 만 보면 `February 31` · `April 31` · 평년의 `February 29` 같은
      **존재하지 않는 날짜가 canonical key 로 만들어진다.** store key 가
      `economic_period_end` 이므로 이는 형식 문제가 아니라 **series identity 오염**이다.
    ⛔ 임의 rollover(2월 31일 → 3월 3일) 나 보정을 하지 않는다 — fail-closed.
    """
    if not isinstance(raw, str) or not raw:
        raise NormalizationError(f"기간 문면이 비었다: {raw!r}")
    m = RE_PERIOD_END.match(raw)
    if not m:
        raise NormalizationError(f"승인된 기간 표기가 아니다: {raw!r}")
    month = _MONTHS.get(m.group(1).lower())
    if month is None:
        raise NormalizationError(f"알 수 없는 월 이름: {m.group(1)!r}")
    day, year = int(m.group(2)), int(m.group(3))
    try:
        # ★ `date()` 는 존재하지 않는 날짜를 ValueError 로 거부한다 — rollover 하지 않는다.
        d = _dt.date(year, month, day)
    except ValueError as e:
        raise NormalizationError(f"존재하지 않는 날짜다: {raw!r} ({e})") from e
    return {"period_end_raw": raw, "economic_period_end": d.isoformat()}
