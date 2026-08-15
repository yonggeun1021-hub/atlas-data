"""B1 migration candidate extractor — fail-closed 회귀 테스트

happy path 를 세지 않는다. 이 스위트가 검사하는 것은
**금지된 상태를 만들 수 없다는 구조** 다 — T8-0 · §23-7 과 같은 방식이다.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules"))
import extract as X                                          # noqa: E402

PASS = FAIL = 0
_FAILS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        _FAILS.append(name)
        print(f"  FAIL  {name}" + (f"   — {detail}" if detail else ""))


def row(**kw) -> dict:
    base = {"url": "https://app.notion.com/x", "종목": "TSMC", "티커": "TSM"}
    for c in X.SOURCE_CELLS:
        base[c] = None
    base.update(kw)
    return base


# 실제 원문이다 — §21-13 이 "한 칸에 두 Rule" 사례로 지목한 그 셀.
TSM_TECH = ("종가 기준 $398 이탈 — 7/17·7/31·8/3 삼중지지 붕괴 = 8월 반등 구조 전체 무효. "
            "진입 전 보류선: 종가가 SMA20 $409 아래면 A·B 모두 매수 보류. "
            "(펌더멘털 무효화=탈락 조건과 분리)")


# ── [E1] 원천 fail-closed ────────────────────────────────────────────────
def test_source_fail_closed() -> None:
    print("\n[E1] 원천을 신뢰할 수 없으면 산출물을 만들지 않는다")

    for label, rows in (("0행", []), ("None", None), ("list 아님", {"a": 1})):
        try:
            X.extract(rows, source_ref="t")
            check(f"★★ {label} → 차단", False, "예외가 안 났다")
        except X.SourceUnavailable:
            check(f"★★ {label} → 차단", True)
        except Exception as e:                               # noqa: BLE001
            check(f"★★ {label} → 차단", False, f"엉뚱한 예외 {type(e).__name__}: {e}")

    # ★ 스키마 변경 — 칸이 사라진 것과 값이 빈 것을 구분한다
    r = row(**{"탈락 조건": "x"})
    del r["기술적 무효화"]
    try:
        X.extract([r], source_ref="t")
        check("★★ 후보 칸이 사라지면 차단 (칸 소실 ≠ 값 없음)", False)
    except X.SourceUnavailable:
        check("★★ 후보 칸이 사라지면 차단 (칸 소실 ≠ 값 없음)", True)

    r2 = row(**{"탈락 조건": "x"})
    del r2["티커"]
    try:
        X.extract([r2], source_ref="t")
        check("★ 식별 키가 사라지면 차단", False)
    except X.SourceUnavailable:
        check("★ 식별 키가 사라지면 차단", True)

    # ★ A-1 재발 방지 — 실패했는데 파일이 남으면 안 된다
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "config", "rules.candidates.json")
        try:
            X.save(X.extract([], source_ref="t"), out)
        except X.SourceUnavailable:
            pass
        check("★★ 차단된 회차는 산출물을 남기지 않는다 (A-1 패턴)",
              not os.path.exists(out))


# ── [E2] 원문 무손실 ─────────────────────────────────────────────────────
def test_lossless() -> None:
    print("\n[E2] 추출기는 원문을 바꾸지 않는다")
    p = X.extract([row(**{"기술적 무효화": TSM_TECH})], source_ref="t")
    c = p["candidates"][0]
    check("★★ raw_text 가 원문과 바이트 동일", c["raw_text"] == TSM_TECH)
    check("★ sha256 이 원문과 일치", c["raw_sha256"] == X._sha256(TSM_TECH))
    check("★ 공백·줄바꿈을 정규화하지 않는다",
          X.extract([row(**{"탈락 조건": "  a\n\n b  "})], source_ref="t")
           ["candidates"][0]["raw_text"] == "  a\n\n b  ")


# ── [E3] ★ v1 이 만들지 않는 것 ──────────────────────────────────────────
def test_no_inference() -> None:
    print("\n[E3] 어떤 입력에도 의미를 추론하지 않는다")
    rows = [
        row(**{"기술적 무효화": TSM_TECH}),                      # 한 칸에 두 Rule
        row(티커="298040.KS", 종목="효성중공업",
            **{"탈락 조건": "수주잔고 전분기 대비 감소 / 7월 저점 종가 1,894,000원 재이탈 "
                            "/ 기관 순매수 연속 끊김"}),          # 슬래시 3개 = 세 조건
        row(티커="SNDK", 종목="SanDisk",
            **{"탈락 조건": "미정 — Discovery 단계이므로 탈락 조건 미설정."}),  # 부재 표식
    ]
    p = X.extract(rows, source_ref="t")

    for f in ("rule_id", "rule_kind", "split_index", "downstream_effect",
              "definition_status", "data_status", "evaluator_status"):
        check(f"★★ {f} 는 전부 None — 자동 생성 경로 없음",
              all(c[f] is None for c in p["candidates"]),
              f"{[c[f] for c in p['candidates']]}")

    check("★★ 슬래시 3개짜리 칸도 1건으로 보존한다 (분해는 이관 시점)",
          sum(1 for c in p["candidates"] if c["ticker"] == "298040.KS") == 1)
    check("★★ '한 칸에 두 Rule' 사례도 1건으로 보존한다 (§21-13)",
          sum(1 for c in p["candidates"] if c["ticker"] == "TSM") == 1)
    check("★ '미정 — …' 부재 표식을 규칙 없음으로 판단하지 않는다 (원문 보존)",
          any("미정" in c["raw_text"] for c in p["candidates"]))


# ── [E4] 세는 단위 ───────────────────────────────────────────────────────
def test_counting_discipline() -> None:
    print("\n[E4] 칸을 센다. Rule 을 세지 않는다 (§21-11 · §21-15)")
    p = X.extract([row(**{"탈락 조건": "a", "기술적 무효화": "b"})], source_ref="t")
    check("★★ rule_count 는 언제나 None", p["rule_count"] is None)
    check("★ cell_count 는 칸 수와 같다", p["cell_count"] == 2, str(p["cell_count"]))
    check("★ row_count 와 cell_count 를 분리한다",
          p["row_count"] == 1 and p["cell_count"] == 2)
    check("★★ 'rules' 라는 이름의 집계 키가 없다",
          not any(k.startswith("rule") and k != "rule_count" for k in p))


# ── [E5] 재현성 · 유일성 ─────────────────────────────────────────────────
def test_reproducible() -> None:
    print("\n[E5] 같은 입력 → 같은 산출 (§20)")
    rows = [row(**{"탈락 조건": "a", "기술적 무효화": "b"}),
            row(티커="MU", 종목="Micron", **{"탈락 조건": "c"})]
    a = X.extract(rows, source_ref="t")
    b = X.extract(rows, source_ref="t")
    check("★★ 두 번 돌려도 동일", json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    ids = [c["candidate_id"] for c in a["candidates"]]
    check("★★ candidate_id 충돌 없음", len(ids) == len(set(ids)), str(ids))
    check("★ candidate_id 는 종목×칸 주소다",
          "TSM::탈락 조건" in ids and "MU::탈락 조건" in ids, str(ids))


# ── [E6] 숨기지 않는다 ───────────────────────────────────────────────────
def test_nothing_silent() -> None:
    print("\n[E6] 빼거나 비어 있는 것을 침묵시키지 않는다")
    p = X.extract([row(**{"탈락 조건": "a"})], source_ref="t")
    check("★★ 제외한 칸이 사유와 함께 남는다",
          len(p["excluded_cells"]) == len(X.EXCLUDED_CELLS)
          and all(e["reason"] for e in p["excluded_cells"]))
    check("★★ '편입 사유' 제외가 눈에 보인다",
          any(e["source_cell"] == "편입 사유" for e in p["excluded_cells"]))
    check("★ 빈 칸도 목록으로 남는다 (값 없음을 관측으로 기록)",
          len(p["empty_cells"]) == len(X.SOURCE_CELLS) - 1)


# ── [E7] ★ 권한 경계 ─────────────────────────────────────────────────────
def test_authority_boundary() -> None:
    print("\n[E7] 이 산출물은 authority 가 아니다 — 구조로 막는다")
    p = X.extract([row(**{"탈락 조건": "a"})], source_ref="t")
    check("★★ authority = False", p["authority"] is False)
    check("★★ consumable_by_evaluator = False", p["consumable_by_evaluator"] is False)
    check("★ artifact_role 이 명시된다", "NOT Rule SSOT" in p["artifact_role"])
    check("★★ population_status 는 incomplete", p["population_status"] == "incomplete")
    check("★ 모집단 불완전 사유가 본문에 남는다", "B0 rev.4" in p["population_note"])

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "c.json")
        for label, mut in (("authority=True", {"authority": True}),
                           ("population=complete", {"population_status": "complete"})):
            q = dict(p); q.update(mut)
            try:
                X.save(q, out)
                check(f"★★ {label} 로는 저장할 수 없다", False, "저장돼 버렸다")
            except X.SourceUnavailable:
                check(f"★★ {label} 로는 저장할 수 없다", True)
        check("★★ 승격 시도 회차도 파일을 남기지 않는다", not os.path.exists(out))


SUITES = [test_source_fail_closed, test_lossless, test_no_inference,
          test_counting_discipline, test_reproducible, test_nothing_silent,
          test_authority_boundary]


def main() -> None:
    print("B1 migration candidate extractor — fail-closed 회귀 테스트")
    for fn in SUITES:
        try:
            fn()
        except Exception as e:                               # noqa: BLE001
            check(f"[{fn.__name__}] 그룹이 예외로 중단되지 않는다", False,
                  f"{type(e).__name__}: {e}")
    print(f"\n{'='*60}\n  {PASS} PASS / {FAIL} FAIL")
    if _FAILS:
        for n in _FAILS:
            print(f"    ✗ {n}")
    print("="*60)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
