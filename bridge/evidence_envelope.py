#!/usr/bin/env python3
"""Observation → Decision **입력 자격 확인 계층** (WS3 · 2026-08-16).

★ 이 모듈이 답하는 단 하나의 질문
      「이 observation 은 출처 · 무결성 · 저장상태가 확인되어
        판정 엔진에 **전달할 자격**이 있는가?」
   여기까지다. 그 다음은 이 모듈의 책임이 아니다.

⛔ 이 모듈이 하지 않는 것 — 하나라도 하면 층이 무너진다
   ⛔ RULE-0022 를 **평가하지 않는다** — 임계값 · 통과 여부 · 추세 판단 없음
   ⛔ 투자 의미를 만들지 않는다 — 매수/매도/보유/축소 어느 것도 산출하지 않는다
   ⛔ 가격 목표 · 포지션 비중 · 신규 PASS/FAIL 기준을 만들지 않는다
   ⛔ 값을 해석하지 않는다 — 「84% 는 높다」 같은 문장을 만들지 않는다
   ⛔ authority 를 선택하지 않는다 — revision 이 여러 건이면 **차단**이지 선택이 아니다
   ⛔ 네트워크 · 파일 · Git · Notion 을 모른다. state dict 를 받아 dict 를 낸다.

★ Signal ≠ Order (기존 Daily Decision 원칙 유지)
   이 모듈의 출력은 signal 도 아니다. **evidence envelope** — 자격 확인서다.

★ 세 상태 (CIO 요구: available / blocked / unresolved)
   EVIDENCE_AVAILABLE    소비 가능 · 필요한 축이 모두 확인됨
   EVIDENCE_BLOCKED      관측은 있으나 지금 전달할 수 없다 — 사유가 명시된다
   EVIDENCE_UNRESOLVED   해당 key 의 관측 자체가 없다 — 판정 이전이다
                         ⛔ 「없음」을 「0」이나 「통과」로 바꾸지 않는다
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "observation"))

import store as ST                                                  # noqa: E402

EVIDENCE_ENVELOPE_SCHEMA_VERSION = "evidence_envelope/1"

# ── 세 상태 ───────────────────────────────────────────────────────────
EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"
EVIDENCE_BLOCKED = "EVIDENCE_BLOCKED"
EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"
STATUSES = (EVIDENCE_AVAILABLE, EVIDENCE_BLOCKED, EVIDENCE_UNRESOLVED)

# ── 차단/미해소 사유 ──────────────────────────────────────────────────
#   ★ store 가 이미 정의한 사유는 **그대로 통과시킨다** — 새 이름을 붙이지 않는다.
#       ST.OBSERVATION_CONFLICT_UNRESOLVED · ST.REVISION_AUTHORITY_UNRESOLVED
#   아래 둘만 이 층에서 추가한다. 둘 다 「무결성 확인 실패」이지 투자 판단이 아니다.
ACQUISITION_PROVENANCE_MISSING = "ACQUISITION_PROVENANCE_MISSING"
SOURCE_IDENTITY_INCOMPLETE = "SOURCE_IDENTITY_INCOMPLETE"
OBSERVATION_ABSENT = "OBSERVATION_ABSENT"
# ★★ Store state 자체가 자기 불변식을 어긴 경우 (CIO 판정 2026-08-16).
#    ⛔ 「Store 가 원래 막는다」를 이유로 이 층이 검사를 생략하지 않는다.
#       상위 층은 하위 층의 정상 동작을 **가정하지 않고 확인**한다.
STORE_STATE_INCONSISTENT = "STORE_STATE_INCONSISTENT"

# ★ envelope 이 반드시 답해야 하는 축 (CIO 지정 최소 확인축).
REQUIRED_AXES = (
    "subject",
    "measurement_identity",
    "economic_period_end",
    "source_identity",
    "consumable",
    "blocked_by",
    "acquisition_provenance_present",
)

# ★ source identity 로 인정하는 축 — 「어느 SEC 원문인가」를 특정하는 축만이다.
#   ⛔ `slice_sha256` 은 여기 없다. 그것은 fixture 제작 산물이며 acquisition
#      representation 에 따라 유무가 갈린다 — 출처 동일성 판정에 쓰면
#      같은 원문이 두 출처로 갈라진다 (H2 · CIO 확정 2026-08-16).
#   ⛔ 독자적인 출처 정의를 만들지 않는다 — 아래 불변식이 Store 계약과의
#      정합성을 import 시점에 강제한다.
SOURCE_IDENTITY_FIELDS = (
    "accession",
    "filing_date",
    "exhibit_type",
    "exhibit_document",
    "source_sha256",
)

_not_material = set(SOURCE_IDENTITY_FIELDS) - set(ST.MATERIAL_PROVENANCE_FIELDS)
if _not_material:
    raise AssertionError(
        f"source identity 축이 Store 의 material provenance 밖에 있다: "
        f"{sorted(_not_material)} — 두 층의 출처 정의가 어긋났다")
del _not_material


class EnvelopeError(ValueError):
    """envelope 계약 위반."""


def _revision_record(entry: dict):
    """소비 후보 record — revision 이 **정확히 1건일 때만** 존재한다.

    ⛔ 여러 건 중에서 고르지 않는다. 고르는 순간 authority 자동 선택이 된다.
    """
    revs = entry.get("revisions") or []
    return revs[0]["record"] if len(revs) == 1 else None


def _source_identity(record: dict) -> dict:
    """material provenance 중 provenance 축만 — 값 축(row/period/column)은 뺀다."""
    mp = ST.material_provenance(record)
    return {k: mp.get(k) for k in SOURCE_IDENTITY_FIELDS}


def _require_state(state):
    """store state 인지 확인한다. **모든 공개 진입점의 첫 동작이다.**

    ⛔ 거부 메시지를 만들다가 다른 예외로 새지 않게 한다 — dict 가 아닌 입력에
       `.get` 을 부르면 EnvelopeError 가 아니라 AttributeError 가 나가고,
       호출자의 fail-closed 처리가 빗나간다 (CIO 판정 2026-08-16).
    """
    if not isinstance(state, dict) or state.get("schema_version") != ST.STORE_SCHEMA_VERSION:
        seen = state.get("schema_version") if isinstance(state, dict) else type(state).__name__
        raise EnvelopeError(f"store state schema 가 다르다: {seen!r}")
    series = state.get("series")
    if not isinstance(series, dict):
        raise EnvelopeError(f"store state 에 series dict 가 없다: {type(series).__name__}")
    return series


def envelope_for(state: dict, key: tuple) -> dict:
    """key 하나에 대한 evidence envelope. **순수 함수**다.

    key = (subject, measurement_identity, economic_period_end)
    """
    series = _require_state(state)
    if not (isinstance(key, tuple) and len(key) == 3 and all(
            isinstance(p, str) and p for p in key)):
        raise EnvelopeError(f"key 축이 3개의 비어있지 않은 문자열이 아니다: {key!r}")

    env = {
        "schema_version": EVIDENCE_ENVELOPE_SCHEMA_VERSION,
        "subject": key[0],
        "measurement_identity": key[1],
        "economic_period_end": key[2],
        "status": EVIDENCE_UNRESOLVED,
        "reasons": [],
        "consumable": False,
        "blocked_by": [],
        "acquisition_provenance_present": False,
        "source_identity": None,
        "audit_provenance": None,
        "observation": None,
    }

    entry = series.get(ST.key_str(key))
    if entry is None:
        # ⛔ 관측이 없다 ≠ 값이 0 ≠ 통과. 판정 이전 상태로 명시한다.
        env["reasons"] = [OBSERVATION_ABSENT]
        return env

    blocked = list(entry.get("blocked_by") or [])
    env["blocked_by"] = blocked
    env["consumable"] = bool(entry.get("consumable"))

    # ★★ Store state 불변식을 **이 층에서 다시 확인한다** (CIO 판정 2026-08-16).
    #    Store 계약상 consumable == (차단 사유 없음 AND revision 정확히 1건) 이다.
    #    손상된 state · 다른 버전이 쓴 state · 수기 편집된 state 는 이 등식을 깰 수 있고,
    #    그때 「blocked_by 가 비어 있으니 available」로 흘러가면 **차단 의도가 뒤집힌다.**
    #    ⛔ 어긋나면 어느 쪽이 옳은지 추정하지 않는다 — 차단한다.
    expected_consumable = (not blocked) and len(entry.get("revisions") or []) == 1
    if bool(entry.get("consumable")) is not expected_consumable:
        blocked = blocked + [STORE_STATE_INCONSISTENT]
        env["reasons"].append(
            f"{STORE_STATE_INCONSISTENT}: consumable={entry.get('consumable')!r} "
            f"인데 blocked_by={entry.get('blocked_by')!r} · "
            f"revisions={len(entry.get('revisions') or [])}건이다")

    record = _revision_record(entry)
    if record is not None:
        src = _source_identity(record)
        env["source_identity"] = src
        # ★ 감사 증거는 Store 가 정의한 대로만 노출한다.
        #   ⛔ 여기서 audit 축 목록을 다시 정의하지 않는다.
        #   H2 적용 전 Store 에는 `audit_provenance` 가 없다 — 그 경우 None 으로
        #   두고 **없는 것을 있는 척하지 않는다**.
        _audit = getattr(ST, "audit_provenance", None)
        env["audit_provenance"] = _audit(record) if _audit else None
        prov = record.get("provenance") or {}
        env["acquisition_provenance_present"] = bool(prov.get("source_kind")) and bool(
            prov.get("exhibit_identity"))
        missing = [f for f in SOURCE_IDENTITY_FIELDS if not src.get(f)]
        if missing:
            blocked = blocked + [SOURCE_IDENTITY_INCOMPLETE]
            env["reasons"].append(f"{SOURCE_IDENTITY_INCOMPLETE}: {sorted(missing)}")
        if not env["acquisition_provenance_present"]:
            blocked = blocked + [ACQUISITION_PROVENANCE_MISSING]

    if blocked:
        env["status"] = EVIDENCE_BLOCKED
        env["blocked_by"] = blocked
        env["reasons"] = blocked + [r for r in env["reasons"] if r not in blocked]
        return env

    if record is None:
        # revision 이 0건이거나 2건 이상인데 blocked 가 비어 있는 경우는
        # store 계약상 나올 수 없다. 그래도 조용히 available 로 넘기지 않는다.
        env["reasons"] = [ST.REVISION_AUTHORITY_UNRESOLVED]
        env["status"] = EVIDENCE_BLOCKED
        env["blocked_by"] = [ST.REVISION_AUTHORITY_UNRESOLVED]
        return env

    # ★★ 마지막 관문 — `consumable` 이 아니면 **어떤 경로로도** available 이 되지 않는다.
    #    위 불변식 검사와 중복이다. ⛔ 중복이라는 이유로 제거하지 않는다:
    #    available 은 이 층이 내리는 유일한 긍정 판정이고, 그 앞에는 문이 둘 있어야 한다.
    if env["consumable"] is not True:
        env["status"] = EVIDENCE_BLOCKED
        env["blocked_by"] = [STORE_STATE_INCONSISTENT]
        env["reasons"] = [f"{STORE_STATE_INCONSISTENT}: consumable 이 아닌 series 를 "
                          f"available 로 승격시키지 않는다"] + env["reasons"]
        return env

    dec = record.get("decision") or {}
    env["status"] = EVIDENCE_AVAILABLE
    env["observation"] = {
        # ★ 관측된 그대로다. ⛔ 반올림 · 부호 재해석 · 단위 변환 없음.
        "raw_value": dec.get("raw_value"),
        "numeric_value": dec.get("numeric_value"),
        "unit": dec.get("unit"),
        "sign_convention": dec.get("sign_convention"),
        "decision_column_identity": dec.get("column_identity"),
        "row_label_raw": record.get("row_label_raw"),
        "period_end_raw": record.get("period_end_raw"),
        "observed_by": record.get("observed_by"),
    }
    return env


def envelopes_from_state(state: dict) -> list:
    """store 안의 모든 key 에 대한 envelope. key 문자열 정렬 순서로 낸다."""
    series = _require_state(state)
    out = []
    for ks in sorted(series):
        parts = ks.split("|")
        if len(parts) != 3:
            raise EnvelopeError(f"store key 를 세 축으로 나눌 수 없다: {ks!r}")
        out.append(envelope_for(state, tuple(parts)))
    return out


def partition(envelopes: list) -> dict:
    """세 상태로 나눈 요약. ⛔ 어떤 상태도 다른 상태로 승격/강등하지 않는다."""
    out = {s: [] for s in STATUSES}
    for e in envelopes:
        st = e.get("status")
        if st not in out:
            raise EnvelopeError(f"알 수 없는 status: {st!r}")
        out[st].append(e)
    return out


def deliverable(envelopes: list) -> list:
    """판정 엔진에 전달할 자격이 확인된 envelope 만.

    ⛔ 이것이 「전달하라」는 뜻은 아니다. 전달 여부는 상위 층의 결정이다.
    """
    return [e for e in envelopes if e["status"] == EVIDENCE_AVAILABLE]
