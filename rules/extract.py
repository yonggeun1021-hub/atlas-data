"""B1 migration candidate extractor v1 — inactive preparation

★ 이 파일이 무엇인가 / 무엇이 아닌가 (CIO 확정 2026-08-15)

    Notion PM Watchlist (B0 source)
            ↓
      candidate extraction          ← 이 파일
            ↓
    config/rules.candidates.json    ← migration evidence · NOT authority
            ↓
    human-reviewed decomposition & mapping
            ↓
    config/rules.json               ← Rule SSOT / authority
            ↓
    machine Rule Inventory

이 산출물은 **Rule authority 가 아니다.** Rule Engine 이 직접 소비하지 않는다.
정본 §21-9① 의 SSOT 판정(Notion 이냐 저장소냐)은 `config/rules.json` 승격
시점에 발생하며, 이 파일은 그 판정과 무관하게 B0 원문을 손실 없이 옮기는
staging 단계다.

⛔ 이 도구가 하지 않는 것 — 전부 CIO 확정 금지사항이다

  · split_index 자동 생성
      §21-13 의 문장 분해는 "`rules.json` 이관 시" 작업으로 명시돼 있다.
      candidate extraction 알고리즘이 자연어 의미를 판단하라는 요구가 아니다.

  · Rule 의미 추론 · 종류(kind) 부여
      §21-13 실증: TSMC `기술적 무효화` 한 칸에 `$398 이탈 = 논리 무효`(→강등 검토)
      와 `SMA20 $409 아래면 매수 보류`(→daily_eligibility) 가 함께 들어 있었다.
      **칸이 Rule 의 종류를 결정하지 않는다.** 따라서 kind 를 붙이지 않는다.

  · rule_id 부여
      §21-12: "통계에 포함되는 모든 객체는 고유 rule_id 를 갖는다."
      아직 통계 대상 객체가 아니므로 rule_id 는 null 로 남기고,
      대신 되돌아갈 수 있는 주소인 candidate_id(= 종목×칸) 만 부여한다.

  · provisional → official 승격
  · 없는 객체 복원·추정 (B0 rev.4 본문 미확보 40여 건)

★ 세는 단위 — 이 도구는 **칸(cell)을 센다. Rule 을 세지 않는다.**
  §21-11 · §21-15: 사람이 센 총계가 31 → 34 → 45 → 47/52 로 네 번 움직였고
  원인은 "한 행이 한 객체가 아니었다" 였다. 분해 전에는 Rule 을 셀 수 없다.
  따라서 산출물에 rule_count 는 항상 None 이며, cell_count 만 값을 갖는다.
"""
from __future__ import annotations

import hashlib
import json
import os

EXTRACTOR_VERSION = "b1_extract_v1"
SCHEMA_VERSION = "DRAFT_UNRATIFIED"

# ── 원천 계약 ────────────────────────────────────────────────────────────
# Watchlist 스키마에서 실제로 확인한 속성명이다. 추정하지 않았다.
# 스키마가 바뀌면 fail-closed 로 멈춘다 — 조용히 빈 결과를 내지 않는다.
IDENTITY_KEYS = ("url", "종목", "티커")

# 후보를 담을 수 있는 칸. 스키마의 description 이 규칙 의미를 명시한 것들.
SOURCE_CELLS = (
    "탈락 조건",        # "증명 실패 시 자동 제외 — 무엇을 증명해야 하는가"
    "기술적 무효화",    # "가격·차트 기준 무효화 구간 … 탈락 조건과 반드시 분리해 기록"
    "다음 이벤트",
    "핵심 지지",        # §21-12(4) execution_reference 후보 — 층 배분은 이관 시 판정
    "핵심 저항",
    "진입 패턴",        # 진입 실행 기준 v1 Gate 4
)

# ★ 제외한 칸은 침묵시키지 않는다. 왜 뺐는지를 산출물에 남긴다.
EXCLUDED_CELLS = {
    "편입 사유": "투자 논거 산문 — 스키마 description 에 규칙 의미 없음. 이관 대상 여부 CIO 판정 필요",
    "상태": "DB 상태 필드(Freeze 대상) — Atlas Stage 와 별개 축",
    "역할 슬롯": "버킷 후보 — Constitution B1_bucket_definition 소관(§21-14(5))",
    "Conviction (PM)": "PM 확신도 — 규칙 아님",
    "Conviction 이력": "PM 확신도 이력 — 규칙 아님",
    "최근 점검일": "운영 메타",
    "편입일": "운영 메타",
}


class SourceUnavailable(RuntimeError):
    """원천을 신뢰할 수 없다. 이 경우 정상 산출물을 만들지 않는다.

    A-1 의 교훈이다 — 실패했는데 정상처럼 보이는 파일이 디스크에 남으면
    그 파일이 다음 단계에서 초록불로 통과한다.
    """


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidate_id(ticker: str, cell: str) -> str:
    """되돌아갈 수 있는 주소다. rule_id 가 아니다.

    같은 입력 → 같은 id (§20 재현성). 종목×칸 이므로 구조적으로 유일하다.
    """
    return f"{ticker}::{cell}"


def _check_source(rows: list) -> None:
    if not isinstance(rows, list):
        raise SourceUnavailable(f"rows 가 list 가 아니다: {type(rows).__name__}")
    if not rows:
        raise SourceUnavailable(
            "원천 행이 0건 — Watchlist 조회 실패와 '규칙이 없음'을 구분할 수 없다")
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise SourceUnavailable(f"{i}번 행이 dict 가 아니다")
        missing = [k for k in IDENTITY_KEYS if k not in r]
        if missing:
            raise SourceUnavailable(
                f"{i}번 행에 식별 키 없음 {missing} — 스키마가 바뀌었을 수 있다")
        unknown_ok = set(IDENTITY_KEYS) | set(SOURCE_CELLS) | set(EXCLUDED_CELLS)
        absent = [c for c in SOURCE_CELLS if c not in r]
        if absent:
            raise SourceUnavailable(
                f"{i}번 행에 후보 칸 누락 {absent} — 칸이 사라진 것과 "
                f"값이 빈 것을 구분할 수 없다")
        _ = unknown_ok  # 미지의 칸은 오류가 아니다. 추가된 칸은 EXCLUDED 로 승인해야 들어온다.


def extract(rows: list, *, source_ref: str, source_fetched_at: str | None = None) -> dict:
    """순수 변환. 네트워크를 타지 않는다 — 그래서 테스트할 수 있다."""
    _check_source(rows)

    candidates = []
    empty_cells = []
    for r in rows:
        ticker = r.get("티커") or r.get("종목")
        for cell in SOURCE_CELLS:
            raw = r.get(cell)
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                empty_cells.append({"ticker": ticker, "source_cell": cell})
                continue
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            candidates.append({
                # ── 되돌아갈 주소 (provenance) ──
                "candidate_id": candidate_id(ticker, cell),
                "symbol_name": r.get("종목"),
                "ticker": ticker,
                "source_cell": cell,
                "source_page_url": r.get("url"),
                # ── 원문 — 손실 없이 그대로 ──
                "raw_text": text,
                "raw_sha256": _sha256(text),
                # ── ⛔ 여기부터는 v1 이 채우지 않는다 ──
                "rule_id": None,        # §21-12 — 분해 후 부여
                "rule_kind": None,      # §21-13 — 칸이 종류를 결정하지 않는다
                "split_index": None,    # §21-13 — 분해는 이관 시점 작업
                "downstream_effect": None,
                "definition_status": None,
                "data_status": None,
                "evaluator_status": None,   # 파생값이므로 원천에서 만들지 않는다
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,

        # ★ 권한 경계를 데이터로 못 박는다
        "authority": False,
        "consumable_by_evaluator": False,
        "artifact_role": "migration evidence — NOT Rule SSOT",

        # ★ 세는 단위
        "cell_count": len(candidates),
        "rule_count": None,          # 분해 전에는 셀 수 없다 (§21-11 · §21-15)
        "row_count": len(rows),

        # ★ 모집단이 불완전하다는 사실을 숨기지 않는다
        "population_status": "incomplete",
        "population_note": (
            "B0 rev.4 본문(45/47/52 객체)이 워크스페이스에서 확인되지 않는다. "
            "이 산출물은 현재 확보된 원천(PM Watchlist)만을 담는다. "
            "없는 객체를 복원·추정하지 않았다."),

        # ★ 뺀 것을 남긴다
        "excluded_cells": [{"source_cell": k, "reason": v}
                           for k, v in EXCLUDED_CELLS.items()],
        "empty_cells": empty_cells,

        "source_ref": source_ref,
        "source_fetched_at": source_fetched_at,
        "candidates": candidates,
    }


def save(payload: dict, path: str) -> str:
    """산출물을 쓴다. 실패한 payload 를 정상 파일로 쓰는 경로는 만들지 않는다."""
    if payload.get("authority") is not False:
        raise SourceUnavailable("authority 가 False 가 아닌 payload 는 쓰지 않는다")
    if payload.get("population_status") != "incomplete":
        raise SourceUnavailable("population_status 를 incomplete 밖으로 바꿀 수 없다")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def main() -> None:
    """어댑터. 원천 행을 파일에서 읽는다 — 네트워크 접근은 호출자 책임이다.

    지금은 Notion 접근 권한을 가진 주체(리서치센터 세션)가 행을 덤프하고
    이 도구가 변환한다. Notion API 토큰이 배선되면 같은 자리에 어댑터를
    하나 더 붙이면 되고, extract() 는 바뀌지 않는다.
    """
    import sys
    src = os.getenv("WATCHLIST_ROWS", "_watchlist_rows.json")
    out = os.getenv("CANDIDATES_OUT", "config/rules.candidates.json")
    try:
        with open(src, encoding="utf-8") as f:
            blob = json.load(f)
    except Exception as e:                                  # noqa: BLE001
        print(f"FATAL: 원천을 읽지 못했다 ({type(e).__name__}: {e}) — 산출물 미생성")
        sys.exit(1)

    rows = blob.get("results") if isinstance(blob, dict) else blob
    try:
        payload = extract(rows,
                          source_ref=(blob.get("source_ref") if isinstance(blob, dict) else None)
                          or "collection://0d145a42-f565-43bc-97ec-cfb474d0f8ea",
                          source_fetched_at=(blob.get("fetched_at")
                                             if isinstance(blob, dict) else None))
    except SourceUnavailable as e:
        print(f"FATAL: 원천 계약 위반 ({e}) — 산출물 미생성")
        sys.exit(1)

    save(payload, out)
    print(f"[b1-extract] {out}")
    print(f"  row_count   = {payload['row_count']}")
    print(f"  cell_count  = {payload['cell_count']}   ← 칸이다. Rule 수가 아니다")
    print(f"  rule_count  = {payload['rule_count']}   ← 분해 전에는 셀 수 없다")
    print(f"  population  = {payload['population_status']}")
    print(f"  authority   = {payload['authority']}")


if __name__ == "__main__":
    main()
