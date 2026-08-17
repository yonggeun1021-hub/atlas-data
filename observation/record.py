#!/usr/bin/env python3
"""Observation Layer 층 ③ — ObservationRecord schema · 조립 · invariant 검증.

★ 흐름은 **외부에서 명시적으로** 호출된다 (CIO 판정 S2):

    ObservationDraft(raw)  →  build_record()  →  ObservationRecord

   ⛔ observer(층 ②)가 normalization 을 직접 수행하게 만들지 않는다.
      observer 는 draft 만 만들고, 이 모듈이 그것을 받아 record 를 만든다.
      두 방향 어느 쪽으로도 import 하지 않는다 — observer 는 이 모듈을 모른다.

⛔ 이 층이 하지 않는 것
   ⛔ 저장 (층 ④) — record 는 `persisted=False` 로 만들어진다
   ⛔ pair 구성 · 비교가능성 (층 ⑤)   ⛔ 임계값 판정 (층 ⑥)
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from normalize import (NormalizationError, SIGN_MINUS, SIGN_NONE,   # noqa: E402
                       SIGN_PARENS, UNIT_PCT,
                       normalize_pct, normalize_period_end)

SCHEMA_VERSION = "observation/1"

# ── RULE-0022 계약 상수 (CIO 판정 D-1 · D-3) ──────────────────────────
#   ⛔ observer 에서 import 하지 않는다 — 층 간 결합을 만들지 않는다.
#     대신 record 층이 **독립적으로** 계약을 알고, draft 가 그 계약을 만족하는지
#     검증한다. 두 층이 같은 계약을 각자 들고 있으면 한쪽이 조용히 바뀔 때 잡힌다.
EXPECTED_SUBJECT = "MSFT"
EXPECTED_MEASUREMENT = "Commercial remaining performance obligation"
EXPECTED_PERIOD_KIND = "quarter"
EXPECTED_DECISION_COLUMN_IDENTITY = "Percentage Change Y/Y (GAAP)"
EXPECTED_EVIDENCE_KEYS = ("cc_impact", "cc")
REQUIRED_PROVENANCE = ("accession", "filing_date", "source_sha256")
REQUIRED_NARROWING = ("period_candidates", "table_candidates", "row_candidates")
EXPECTED_COLUMN_NARROWING = {"gaap": 1, "cc_impact": 1, "cc": 1}
ALLOWED_SIGN_CONVENTIONS = (SIGN_NONE, SIGN_PARENS, SIGN_MINUS)
RE_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RecordInvariantError(ValueError):
    """record invariant 위반. ★ 완성된 ObservationRecord 를 만들지 않는다."""


def _identity_contains(header_text: str, expected: str) -> bool:
    """결합 헤더가 기대 column identity 를 담고 있는가. 공백만 정규화한다.

    ⛔ 대소문자·괄호를 무르게 하지 않는다 — `(GAAP)` 의 괄호가 사라지면 다른 열과
       구별되지 않는다.
    """
    import re
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    return norm(expected) in norm(header_text)


def build_record(draft: dict) -> dict:
    """ObservationDraft → ObservationRecord.

    ⛔ 어느 단계든 실패하면 예외를 던지고 **부분 완성 record 를 돌려주지 않는다.**
    """
    if not isinstance(draft, dict):
        raise RecordInvariantError(f"draft 가 dict 가 아니다: {type(draft).__name__}")

    # ── 층 순서 invariant ─────────────────────────────────────────────
    if draft.get("normalized") is not False:
        raise RecordInvariantError("draft 가 이미 normalized 로 표시돼 있다 — 층 ② 가 층 ③ 을 침범했다")
    if draft.get("persisted") is not False:
        raise RecordInvariantError("draft 가 이미 persisted 로 표시돼 있다 — 층 ② 가 층 ④ 를 침범했다")

    # ── identity invariant ────────────────────────────────────────────
    if draft.get("subject") != EXPECTED_SUBJECT:
        raise RecordInvariantError(f"subject 가 계약과 다르다: {draft.get('subject')!r}")
    if draft.get("measurement_identity") != EXPECTED_MEASUREMENT:
        raise RecordInvariantError(
            f"measurement identity 가 계약과 다르다: {draft.get('measurement_identity')!r}")
    if draft.get("economic_period_kind") != EXPECTED_PERIOD_KIND:
        raise RecordInvariantError(
            f"economic period 가 quarter 가 아니다: {draft.get('economic_period_kind')!r}")

    # ── narrowing exactly-one evidence ────────────────────────────────
    narrowing = draft.get("narrowing") or {}
    for k in REQUIRED_NARROWING:
        if narrowing.get(k) != 1:
            raise RecordInvariantError(f"narrowing `{k}` 가 정확히 1이 아니다: {narrowing.get(k)!r}")
    cols = narrowing.get("column_candidates") or {}
    if cols != {"gaap": 1, "cc_impact": 1, "cc": 1}:
        raise RecordInvariantError(f"column narrowing 이 각각 1이 아니다: {cols!r}")

    # ── decision column identity (D-3) ────────────────────────────────
    decision = draft.get("decision") or {}
    if decision.get("column_key") != "gaap":
        raise RecordInvariantError(f"Decision 열이 GAAP 계약이 아니다: {decision.get('column_key')!r}")
    if not _identity_contains(decision.get("column_identity") or "",
                              EXPECTED_DECISION_COLUMN_IDENTITY):
        raise RecordInvariantError(
            f"Decision 열 identity 가 기대 문면을 담고 있지 않다: "
            f"{decision.get('column_identity')!r}")

    # ── provenance ────────────────────────────────────────────────────
    prov = draft.get("provenance") or {}
    missing = [k for k in REQUIRED_PROVENANCE if not prov.get(k)]
    if missing:
        raise RecordInvariantError(f"provenance 필수 항목이 없다: {missing}")

    # ── normalization — 실패하면 record 를 만들지 않는다 ───────────────
    period = normalize_period_end(draft.get("period_end_text"))
    dec_norm = normalize_pct(decision.get("raw_value"))

    evidence = []
    got_keys = []
    for e in draft.get("evidence_columns") or []:
        got_keys.append(e.get("column_key"))
        norm = normalize_pct(e.get("raw_value"))
        evidence.append({
            "column_key": e.get("column_key"),
            "column_identity": e.get("column_identity"),
            **norm,
            "is_decision_value": False,     # ★ evidence 는 Decision 값으로 승격되지 않는다
        })
    if tuple(got_keys) != EXPECTED_EVIDENCE_KEYS:
        raise RecordInvariantError(
            f"evidence 열이 계약과 다르다: {got_keys} != {list(EXPECTED_EVIDENCE_KEYS)}")
    for e in evidence:
        if e["unit"] != UNIT_PCT:
            raise RecordInvariantError(f"evidence unit 이 pct 가 아니다: {e['unit']!r}")

    if dec_norm["unit"] != UNIT_PCT:
        raise RecordInvariantError(f"decision unit 이 pct 가 아니다: {dec_norm['unit']!r}")

    record = {
        "schema_version": SCHEMA_VERSION,
        "subject": draft["subject"],
        "measurement_identity": draft["measurement_identity"],
        "economic_period_end": period["economic_period_end"],
        "economic_period_kind": EXPECTED_PERIOD_KIND,
        "period_text_raw": draft.get("period_text_raw"),
        "period_end_raw": period["period_end_raw"],
        "decision": {
            "column_key": decision["column_key"],
            "column_identity": decision["column_identity"],
            **dec_norm,
            "is_decision_value": True,
        },
        "evidence_columns": evidence,
        "row_label_raw": draft.get("row_label_raw"),
        "table_header_raw": draft.get("table_header_raw"),
        "narrowing": narrowing,
        "provenance": prov,
        "observed_by": draft.get("observed_by"),
        "rule_scope": draft.get("rule_scope"),
        "normalized": True,
        "persisted": False,     # ★ 층 ④ 가 아직 처리하지 않았다
    }
    validate_record(record)
    return record


def validate_record(record: dict) -> None:
    """완성된 record 만 보고 **핵심 계약 전체**를 독립 검증한다.

    ★ 이것이 층 ④ Store 의 authoritative invariant gate 다 (CIO 판정 S2.1 ·
      `RECORD_VALIDATION_CONTRACT_INCOMPLETE`).
      **Store 는 `build_record()` 의 내부 구현을 신뢰하면 안 된다.** 직렬화돼 돌아온
      남의 record 를 받아도 이 문 하나로 계약이 증명돼야 한다.
    ★ `build_record()` 의 preflight 검사와 중복되는 항목이 있다 — 그것은 의도된
      이중 방어다. ⛔ 중복이라는 이유로 어느 쪽도 제거하지 않는다.
    """
    if not isinstance(record, dict):
        raise RecordInvariantError(f"record 가 dict 가 아니다: {type(record).__name__}")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise RecordInvariantError(f"schema_version 이 다르다: {record.get('schema_version')!r}")

    # ── identity 계약 — 값까지 검증한다 (존재 여부만 보지 않는다) ──────
    if record.get("subject") != EXPECTED_SUBJECT:
        raise RecordInvariantError(f"subject 가 계약과 다르다: {record.get('subject')!r}")
    if record.get("measurement_identity") != EXPECTED_MEASUREMENT:
        raise RecordInvariantError(
            f"measurement identity 가 계약과 다르다: {record.get('measurement_identity')!r}")
    if record.get("economic_period_kind") != EXPECTED_PERIOD_KIND:
        raise RecordInvariantError("economic period 가 quarter 가 아니다")

    # ── economic_period_end — store key 이므로 실제 달력으로 재확인한다 ──
    pe = record.get("economic_period_end")
    if not pe:
        raise RecordInvariantError("필수 필드가 없다: economic_period_end")
    if not isinstance(pe, str) or not RE_ISO_DATE.match(pe):
        raise RecordInvariantError(f"economic_period_end 가 ISO date 가 아니다: {pe!r}")
    try:
        _dt.date(int(pe[0:4]), int(pe[5:7]), int(pe[8:10]))
    except ValueError as e:
        raise RecordInvariantError(f"economic_period_end 가 존재하지 않는 날짜다: {pe!r} ({e})") from e
    if not record.get("period_end_raw"):
        raise RecordInvariantError("period_end_raw 가 없다 — source 복원성 상실")

    # ── 층 순서 ───────────────────────────────────────────────────────
    if record.get("normalized") is not True:
        raise RecordInvariantError("normalized 가 True 가 아니다")
    if record.get("persisted") is not False:
        raise RecordInvariantError("persisted 가 False 가 아니다 — 층 ④ 침범")

    # ── Decision 계약 (D-3) ───────────────────────────────────────────
    d = record.get("decision")
    if not isinstance(d, dict):
        raise RecordInvariantError("decision 이 없다")
    for k in ("raw_value", "numeric_value", "unit", "sign_convention", "column_identity"):
        if d.get(k) in (None, ""):
            raise RecordInvariantError(f"decision 에 `{k}` 가 없다")
    if d.get("column_key") != "gaap":
        raise RecordInvariantError(f"Decision 열이 GAAP 계약이 아니다: {d.get('column_key')!r}")
    if not _identity_contains(d.get("column_identity") or "", EXPECTED_DECISION_COLUMN_IDENTITY):
        raise RecordInvariantError(
            f"Decision 열 identity 가 기대 문면을 담고 있지 않다: {d.get('column_identity')!r}")
    if d.get("unit") != UNIT_PCT:
        raise RecordInvariantError(f"decision unit 이 pct 가 아니다: {d.get('unit')!r}")
    if d.get("sign_convention") not in ALLOWED_SIGN_CONVENTIONS:
        raise RecordInvariantError(f"승인되지 않은 sign_convention: {d.get('sign_convention')!r}")
    if d.get("is_decision_value") is not True:
        raise RecordInvariantError("decision 이 Decision 값으로 표시돼 있지 않다")
    _check_numeric(d, "decision")

    # ── evidence 계약 — 키 집합과 순서까지 본다 ────────────────────────
    ev = record.get("evidence_columns")
    if not isinstance(ev, list):
        raise RecordInvariantError("evidence_columns 가 목록이 아니다")
    keys = tuple(e.get("column_key") for e in ev)
    if keys != EXPECTED_EVIDENCE_KEYS:
        raise RecordInvariantError(
            f"evidence 열이 계약과 다르다: {list(keys)} != {list(EXPECTED_EVIDENCE_KEYS)}")
    for e in ev:
        for k in ("raw_value", "numeric_value", "unit"):
            if e.get(k) in (None, ""):
                raise RecordInvariantError(f"evidence 에 `{k}` 가 없다")
        if e.get("unit") != UNIT_PCT:
            raise RecordInvariantError(f"evidence unit 이 pct 가 아니다: {e.get('unit')!r}")
        if e.get("is_decision_value") is not False:
            raise RecordInvariantError("evidence 가 Decision 값으로 승격돼 있다")
        _check_numeric(e, "evidence")
    n_decision = sum(1 for x in [d] + ev if x.get("is_decision_value"))
    if n_decision != 1:
        raise RecordInvariantError(f"Decision 값이 정확히 1개가 아니다: {n_decision}개")

    # ── provenance ────────────────────────────────────────────────────
    prov = record.get("provenance") or {}
    missing = [k for k in REQUIRED_PROVENANCE if not prov.get(k)]
    if missing:
        raise RecordInvariantError(f"provenance 필수 항목이 없다: {missing}")

    # ── narrowing exactly-one 증거 ────────────────────────────────────
    narrowing = record.get("narrowing") or {}
    for k in REQUIRED_NARROWING:
        if narrowing.get(k) != 1:
            raise RecordInvariantError(f"narrowing `{k}` 가 1이 아니다: {narrowing.get(k)!r}")
    if narrowing.get("column_candidates") != EXPECTED_COLUMN_NARROWING:
        raise RecordInvariantError(
            f"column narrowing 이 각각 1이 아니다: {narrowing.get('column_candidates')!r}")


def _check_numeric(field: dict, where: str) -> None:
    """numeric 이 문자열이고 exact decimal 로 되살아나는지 본다.

    ⛔ float 유입을 막는다 · ⛔ 값이 raw 와 무관한 문자열이어도 잡는다.
    """
    n = field.get("numeric_value")
    if not isinstance(n, str):
        raise RecordInvariantError(f"{where} numeric_value 가 문자열이 아니다 — float 유입 의심")
    try:
        Decimal(n)
    except InvalidOperation as e:
        raise RecordInvariantError(f"{where} numeric_value 가 decimal 이 아니다: {n!r}") from e


def try_build(draft: dict):
    """(record | None, error | None). ⛔ 실패를 성공값으로 흘려보내지 않는다."""
    try:
        return build_record(draft), None
    except (RecordInvariantError, NormalizationError) as e:
        return None, f"{type(e).__name__}: {e}"
