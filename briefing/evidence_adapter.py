#!/usr/bin/env python3
"""아침/저녁 브리핑용 **evidence adapter** (WS4 · 2026-08-16).

★ 이 모듈의 유일한 책임
      확인된 사실을 브리핑 표기로 **옮겨 적는 것**.
   ⛔ 사실을 만들지 않는다 · ⛔ 사실을 해석하지 않는다 · ⛔ 행동을 지시하지 않는다.

★ 절대 섞지 않는 세 층 (CIO 지정)
      ① 확인된 사실        관측 원문 그대로. 출처가 함께 간다.
      ② 투자판정 반영 상태  ①이 판정에 반영됐는가. **입력으로 받는다.**
      ③ 현재 행동          ②의 귀결. ⛔ 이 모듈이 새로 만들지 않는다.

   허용:  "SEC 원문에서 Commercial RPO 84% 확인"
   금지:  "84% 이므로 매수 명분 강화"
   후자는 **별도로 승인된 evaluator 결과가 있을 때만** 가능하며, 그 결과는
   이 모듈이 아니라 상위에서 만들어져 입력으로 들어온다.

⛔ 이 모듈이 하지 않는 것
   ⛔ evaluator 를 호출하지 않는다 · ⛔ Rule 통과 여부를 만들지 않는다
   ⛔ 값의 크기를 평가하지 않는다 (「높다」 「개선됐다」 「강하다」)
   ⛔ 매매 방향 · 가격 목표 · 포지션 비중을 산출하지 않는다
   ⛔ Notion 에 쓰지 않는다 — 네트워크도 파일도 모른다. dict → dict/str 이다.
   ⛔ Daily Briefing 의 행 계약(아침 생성 · 저녁 보강)을 바꾸지 않는다.
      `slot` 은 **표기 라벨일 뿐** 행 구조에 관여하지 않는다.

★ Signal ≠ Order 원칙을 그대로 잇는다. 이 모듈의 출력은 signal 조차 아니다.
"""
from __future__ import annotations

EVIDENCE_BRIEFING_SCHEMA_VERSION = "briefing_evidence/1"
ENVELOPE_SCHEMA_VERSION = "evidence_envelope/1"

# ── 슬롯 — 라벨일 뿐이다 ──────────────────────────────────────────────
MORNING = "morning"
EVENING = "evening"
SLOTS = (MORNING, EVENING)

# ── 판정 반영 상태 — **입력으로 받는다** ──────────────────────────────
#   ⛔ 이 모듈이 이 값을 스스로 정하지 않는다. 거버넌스 상태이지 계산 결과가 아니다.
EVALUATION_NOT_AUTHORIZED = "EVALUATION_NOT_AUTHORIZED"
EVALUATION_AUTHORIZED = "EVALUATION_AUTHORIZED"
EVALUATION_STATES = (EVALUATION_NOT_AUTHORIZED, EVALUATION_AUTHORIZED)

# ── envelope 상태 (WS3 어휘를 **그대로** 쓴다 — 새 이름을 붙이지 않는다) ──
EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"
EVIDENCE_BLOCKED = "EVIDENCE_BLOCKED"
EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"

# ── ③ 현재 행동 — **이 모듈이 만들지 않는다** ─────────────────────────
#   ★★ CIO 판정 2026-08-16 (Q8 반려):
#      「evaluation 미승인」에서 「기존 판단 유지」는 **논리적으로 도출되지 않는다.**
#      기존 보유 포지션에는 evaluation 과 무관한 별도의 risk action 이 존재할 수 있다.
#      formatter 가 행동을 합성하면 그 순간 이 층은 formatter 가 아니게 된다.
#   ★ 따라서 current_action 은 authority 와 함께 **상위 입력으로만** 들어온다.
#     주어지지 않으면 `None` 이고, 표기는 「미정」이다 — 「변화 없음」이 아니다.
#   ⛔ 기본값을 만들지 않는다 · ⛔ 미승인에서 행동을 유추하지 않는다.
ACTION_UNDETERMINED_LABEL = "미정 — 상위에서 제공되지 않음"


class AdapterError(ValueError):
    """adapter 입력 계약 위반."""


class CIODefinitionRequired(NotImplementedError):
    """이 분기를 채우려면 CIO 정의가 필요하다 — 임의로 만들지 않는다."""


SOURCE_FACT_LABELS = {
    "company_ir_web": "기업 IR 원문",
    "company_official_release_sec_exhibit": "기업 공식발표 원문",
}


def _fact_source_label(env: dict) -> str:
    src = env.get("source_identity") or {}
    identity_kind = src.get("identity_kind")
    if identity_kind is None:
        return "SEC 원문"
    if identity_kind not in SOURCE_FACT_LABELS:
        raise AdapterError(f"알 수 없는 source identity kind: {identity_kind!r}")
    return SOURCE_FACT_LABELS[identity_kind]


def _fact_line(env: dict) -> str:
    """① 확인된 사실 한 줄. ⛔ 형용사를 붙이지 않는다."""
    subj = env["subject"]
    meas = env["measurement_identity"]
    period = env["economic_period_end"]
    status = env["status"]

    if status == EVIDENCE_AVAILABLE:
        obs = env.get("observation") or {}
        col = obs.get("decision_column_identity") or ""
        return (f"{subj} · {meas} · {period} — {_fact_source_label(env)}에서 "
                f"{obs.get('raw_value')} 확인 ({col})")
    if status == EVIDENCE_BLOCKED:
        # ⛔ 「값은 이런데 막혀 있다」로 쓰지 않는다 — 값을 내보내면 사실로 읽힌다.
        why = ", ".join(env.get("blocked_by") or []) or "사유 미기재"
        return (f"{subj} · {meas} · {period} — 관측은 있으나 전달 자격 미확인 "
                f"({why})")
    if status == EVIDENCE_UNRESOLVED:
        # ⛔ 「변화 없음」 「0」 으로 바꾸지 않는다. 관측 이전이다.
        return f"{subj} · {meas} · {period} — 해당 기간 관측 없음 (판정 이전)"
    raise AdapterError(f"알 수 없는 envelope status: {status!r}")


def _source_line(env: dict):
    """사실에 붙는 출처. 없으면 None — **지어내지 않는다**."""
    src = env.get("source_identity")
    if not src:
        return None
    if src.get("identity_kind") is not None:
        required = ("source_id", "source_url", "source_sha256", "available_at")
        missing = [field for field in required if not src.get(field)]
        if missing:
            raise AdapterError(f"official-release source identity 결손: {missing}")
        return (f"출처: {src['source_id']} · {src['available_at']} · "
                f"{src['source_url']} · sha256 {src['source_sha256'][:12]}")
    return (f"출처: {src.get('accession')} · {src.get('filing_date')} · "
            f"{src.get('exhibit_document')} · sha256 {(src.get('source_sha256') or '')[:12]}")


def _normalize_action(current_action):
    """③ 현재 행동을 **받아 적는다**. ⛔ 만들지 않는다.

    None            → 미정. 표기만 붙고 내용은 없다.
    {"action": str, "authority": str}
                    → 그대로 싣는다. 둘 다 비어 있으면 거부한다 —
                      「누가 그렇게 정했는가」 없는 행동 문장은 적지 않는다.
    그 밖의 형태     → 거부. ⛔ 문자열만 받아 authority 를 비워두지 않는다.
    """
    if current_action is None:
        return {"action": None, "authority": None,
                "note": ACTION_UNDETERMINED_LABEL}
    if not isinstance(current_action, dict):
        raise AdapterError(
            f"current_action 은 None 이거나 dict 여야 한다: {type(current_action).__name__}")
    action = current_action.get("action")
    authority = current_action.get("authority")
    if not isinstance(action, str) or not action.strip():
        raise AdapterError("current_action.action 이 비어 있다 — 빈 행동을 적지 않는다")
    if not isinstance(authority, str) or not authority.strip():
        raise AdapterError(
            "current_action.authority 가 비어 있다 — 출처 없는 행동 문장은 적지 않는다")
    return {"action": action, "authority": authority, "note": None}


def briefing_block(envelopes, evaluation: dict, slot: str = MORNING,
                   current_action=None) -> dict:
    """세 층으로 나뉜 브리핑 블록을 만든다. **순수 함수**다.

    envelopes      WS3 evidence envelope 리스트 (schema_version 로 검증)
    evaluation     {"status": EVALUATION_*, "authority": "<판정 출처 문자열>",
                    "result": None | <상위가 만든 evaluator 결과>}
                   ⛔ 이 모듈이 status 를 추론하지 않는다 — 받아 적는다.
    slot           라벨일 뿐이다. 행 계약에 관여하지 않는다.
    current_action None | {"action": str, "authority": str}
                   ⛔ 이 모듈이 행동을 합성하지 않는다 (CIO 판정 2026-08-16 · Q8).
    """
    if slot not in SLOTS:
        raise AdapterError(f"slot 은 {SLOTS} 중 하나여야 한다: {slot!r}")
    if not isinstance(evaluation, dict):
        raise AdapterError("evaluation 은 dict 여야 한다")
    ev_status = evaluation.get("status")
    if ev_status not in EVALUATION_STATES:
        raise AdapterError(
            f"evaluation.status 는 {EVALUATION_STATES} 중 하나여야 한다: {ev_status!r}")
    if not evaluation.get("authority"):
        # ★ 「누가 그렇게 정했는가」 없이 판정 상태를 적지 않는다.
        raise AdapterError("evaluation.authority 가 비어 있다 — 판정 출처 없이 적지 않는다")
    if not isinstance(envelopes, (list, tuple)):
        raise AdapterError("envelopes 는 리스트여야 한다")
    action = _normalize_action(current_action)

    facts = []
    for env in envelopes:
        if not isinstance(env, dict) or env.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
            # ⛔ 거부 메시지를 만들다가 다른 예외로 새지 않게 한다 —
            #    dict 가 아닌 입력에 `.get` 을 부르면 AdapterError 가 아니라
            #    AttributeError 가 나가고, 호출자의 fail-closed 처리가 빗나간다.
            seen = env.get("schema_version") if isinstance(env, dict) else type(env).__name__
            raise AdapterError(f"evidence envelope 가 아니다: {seen!r}")
        facts.append({
            "status": env["status"],
            "line": _fact_line(env),
            "source": _source_line(env),
        })

    if ev_status == EVALUATION_AUTHORIZED:
        # ⛔ 승인 이후의 표기 형태는 **투자 정책 정의**다. 임의로 만들지 않는다.
        #    evaluator 결과가 실제로 있어도, 그것을 브리핑에 어떻게 옮길지는
        #    이 모듈이 결정할 문제가 아니다.
        raise CIODefinitionRequired(
            "BLOCKED — CIO DEFINITION REQUIRED: evaluation 이 승인된 뒤의 "
            "② 반영 상태 · ③ 현재 행동 표기 형태가 정의되지 않았다. "
            "⛔ 임의로 문장을 만들지 않는다.")

    return {
        "schema_version": EVIDENCE_BRIEFING_SCHEMA_VERSION,
        "slot": slot,
        # ① 확인된 사실
        "confirmed_facts": facts,
        # ② 투자판정 반영 상태 — 받아 적은 것이다
        "evaluation_reflection": {
            "status": ev_status,
            "authority": evaluation["authority"],
            "reflected": False,
            "note": "아직 미승인 — 위 사실은 판정에 반영되지 않았다",
        },
        # ③ 현재 행동 — **상위에서 받은 것**을 그대로 싣는다.
        #    ⛔ 미승인에서 행동을 유추하지 않는다 (Q8 반려).
        "current_action": action,
    }


def render_text(block: dict) -> str:
    """세 층을 그대로 보이는 평문. ⛔ 층을 합치지 않는다."""
    if not isinstance(block, dict) or \
            block.get("schema_version") != EVIDENCE_BRIEFING_SCHEMA_VERSION:
        # ⛔ 위와 같은 이유로 메시지 생성에서 다른 예외가 새지 않게 한다.
        seen = block.get("schema_version") if isinstance(block, dict) else type(block).__name__
        raise AdapterError(f"briefing block 이 아니다: {seen!r}")
    lines = []
    lines.append("확인된 사실:")
    if not block["confirmed_facts"]:
        lines.append("  (없음)")
    for f in block["confirmed_facts"]:
        lines.append(f"  · {f['line']}")
        if f["source"]:
            lines.append(f"    {f['source']}")
    refl = block["evaluation_reflection"]
    lines.append(f"투자판정 반영: {refl['note']} [{refl['authority']}]")
    act = block["current_action"]
    if act.get("action"):
        lines.append(f"현재 행동: {act['action']} [{act['authority']}]")
    else:
        # ⛔ 「기존 판단 유지」로 바꾸지 않는다 — 그것도 행동 문장이다.
        lines.append(f"현재 행동: {act['note']}")
    return "\n".join(lines)
