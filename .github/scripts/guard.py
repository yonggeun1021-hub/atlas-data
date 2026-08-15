"""Atlas Guard — Common Health → 소스별 Health

collect.yml 의 Guard 스텝과 D1 게이트가 호출한다.
stdout 첫 줄에 `fresh` 또는 `stale` 을 쓰고 exit 0 으로 끝난다.

  fresh   오늘자 decision-qualified 산출물이 이미 있다 → 이 회차는 건너뛴다
  stale   그 외 전부

계약은 공통과 소스별로 나뉜다.
  공통  collected_for_kst_date == today · summary 존재 · failed == 0 · ok > 0
  KRX   decision_readiness 존재(구버전 fail-closed) · confirmed_through non-null
        · not_decision_ready 빔 · 수급 행 결측 없음 · 확정일 == 관측된 최신 거래일
  DART  자체 검증 계약 — relevant ⊆ total · 배열 정합 · rcept_no/url 정합
        · 날짜 형식·미래 공시 차단 · filter_keywords 와 title 정합
  SEC   validation.schema_verified == true
        (sec.py 는 ok>0 이면 schema 실패해도 exit 0 이므로 여기서 막는다)

★ 거래일 캘린더를 쓰지 않는다. KRX 응답은 거래일에만 행을 만들므로
  수집된 daily 키 자체가 거래일 목록이다.

설계 원칙 — 안전한 기본값은 stale. 판단이 안 서면 '수집한다' 로 귀결된다.
계약이 등록되지 않은 산출물도 stale 로 처리한다.
"""
import json, os, sys


# ── 공통 ────────────────────────────────────────────────────────────
def _common(d, today):
    s = d.get("summary")
    if not isinstance(s, dict):   return "summary 없음"
    if s.get("failed", 1) != 0:   return f"failed={s.get('failed')}"
    if s.get("ok", 0) <= 0:       return f"ok={s.get('ok')} (수집 0건인데 실패도 0)"
    return None


# ── KRX ─────────────────────────────────────────────────────────────
def _krx(d, today):
    dr = d.get("decision_readiness")
    if not isinstance(dr, dict):
        return f"decision_readiness 없음 (collector_version={d.get('collector_version')}) — 확정 개념 미지원"
    if dr.get("confirmed_through") in (None, ""):       return "확정 행 0건 (confirmed_through=null)"
    if dr.get("not_decision_ready"):                    return f"decision_ready 아닌 종목 {dr['not_decision_ready']}"
    if dr.get("stocks_with_investor_rows_missing"):     return f"수급 행 결측 종목 {dr['stocks_with_investor_rows_missing']}"
    return _krx_session_consistency(d, today)


def _krx_session_consistency(d, today):
    """★ 캘린더 없이 '요구 최신 거래일'을 판정한다.

    잡는 것 : 확정 지연 · 종목별 수집 절단 · 거래정지 등 종목 간 불일치
    못 잡는 것 : 전 종목이 **동일하게** 절단된 경우 (교차 대조 대상이 사라진다)
                 → 이건 회차 간 단조성 검사(다음 수집의 confirmed_through 가
                   이전보다 뒤로 가지 않는가)로만 잡히며 Guard 범위 밖이다.
    """
    per = {}
    for code, s in (d.get("stocks") or {}).items():
        if s.get("status") != "ok":
            continue
        days = [k for k in (s.get("daily") or {}) if k < today]
        if not days:
            return f"{code}: 오늘 이전 거래일 행이 하나도 없음"
        per[code] = (max(days), s.get("latest_trading_day"))
    if not per:
        return "status=ok 인 종목이 없음"

    lag = {c: v for c, v in per.items() if v[1] != v[0]}
    if lag:
        return ("확정이 관측된 최신 거래일에 미달 — "
                + ", ".join(f"{c}: 확정={v[1]} 관측최신={v[0]}" for c, v in sorted(lag.items())))

    observed = sorted({v[0] for v in per.values()})
    if len(observed) > 1:
        return (f"종목 간 최신 거래일 불일치 {observed} — 거래정지 등 개별 사유일 수 있으나 "
                f"자동 통과시키지 않는다")
    return None


# ── DART ────────────────────────────────────────────────────────────
def _dart(d, today):
    """★ 신설 — payload 안의 정보만으로 검증 가능한 계약.
    외부 조회 없이 필터·추출·범위 정합을 확인한다."""
    kws = d.get("filter_keywords") or []
    for code, s in (d.get("stocks") or {}).items():
        if s.get("status") != "ok":
            continue
        tot, rel = s.get("total_count"), s.get("relevant_count")
        items = s.get("relevant")
        if tot is None or rel is None:      return f"{code}: total/relevant_count 필드 없음"
        if not isinstance(items, list):     return f"{code}: relevant 배열 없음"
        if rel > tot:                       return f"{code}: relevant({rel}) > total({tot})"
        if len(items) != rel:               return f"{code}: relevant 길이({len(items)}) != relevant_count({rel})"
        for it in items:
            no = it.get("rcept_no")
            if not no:                                  return f"{code}: rcept_no 없음"
            if no not in (it.get("url") or ""):         return f"{code}: url 과 rcept_no 불일치 ({no})"
            dtx = (it.get("date") or "")
            if len(dtx) != 8 or not dtx.isdigit():      return f"{code}: date 형식 이상 ({dtx})"
            if dtx > today.replace("-", ""):            return f"{code}: 미래 공시일 ({dtx})"
            if kws and not any(k in (it.get("title") or "") for k in kws):
                return f"{code}: 필터 키워드에 걸리지 않는 항목이 relevant 에 있음 ({it.get('title')!r})"
    return None
    # ⚠ 남는 Undefined — corp_code 매핑이 **틀린** 경우(존재하지만 다른 회사)는
    #   payload 만으로 검증 불가. 외부 대조가 필요하다.


# ── SEC ─────────────────────────────────────────────────────────────
def _sec(d, today):
    v = d.get("validation")
    if not isinstance(v, dict):  return "validation 없음"
    if v.get("schema_verified") is not True:
        return (f"schema_verified={v.get('schema_verified')} "
                f"(method={v.get('verification_method')}, reason={v.get('reason')})")
    return None
    # sec.py 는 ok>0 이면 schema 실패해도 exit 0 이다 (L391) — 여기서 막는다.


VALIDATORS = {
    "latest_krx.json":  _krx,
    "latest_dart.json": _dart,
    "latest_sec.json":  _sec,
}


def why_not_fresh(path, today):
    base = os.path.basename(path)
    check = VALIDATORS.get(base)
    if check is None:
        return f"알 수 없는 산출물 — 계약 미등록 ({base})"      # fail-closed
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return "파일없음/파싱실패"
    if d.get("collected_for_kst_date") != today:
        return f"날짜불일치({d.get('collected_for_kst_date')})"
    return _common(d, today) or check(d, today)


def main():
    today = os.environ.get("TODAY", "").strip()
    if not today:
        print("stale"); print("  사유: TODAY 미지정", file=sys.stderr); return
    paths = sys.argv[1:] or [f"data/{k}" for k in VALIDATORS]
    bad = [(p, r) for p in paths if (r := why_not_fresh(p, today))]
    print("stale" if bad else "fresh")
    for p, r in bad:
        print(f"  stale: {p} — {r}", file=sys.stderr)


if __name__ == "__main__":
    main()
