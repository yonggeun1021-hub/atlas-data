"""
Verify Collect Path — 산출물 요약 (읽기 전용)

verify.yml 에서만 쓴다. 수집기가 만든 data/latest_*.json 을 읽어
"무엇이 실제로 만들어졌는가"를 한눈에 출력한다.

⚠ 이 스크립트는 아무것도 쓰지 않고 아무것도 판정하지 않는다. 출력만 한다.
   실패해도 워크플로를 죽이지 않는다(항상 exit 0).

★ 2026-08-14 — 키 계약(CONTRACT)을 명시한다.
  사고: 수집기가 payload 키를 `policy_mode` → `same_day_confirmation` 으로 바꿨는데
        이 파일이 옛 이름을 계속 읽어, 검증 요약에 정책값이 조용히 `None` 으로 찍혔다.
        읽는 쪽이 `.get()` 을 쓰면 **키가 사라진 사실 자체가 사라진다.**
  대책: 아래 CONTRACT 에 이 파일이 의존하는 키를 전부 적어 두고,
        test/fault_injection.py T10 이 **실제 payload 와 대조**한다.
        이름이 또 바뀌면 회귀 테스트가 먼저 깨진다 — 로그가 조용히 틀리는 대신.
"""
import json
import os
import sys

TARGETS = [
    ("KRX ", "data/latest_krx.json"),
    ("DART", "data/latest_dart.json"),
    ("SEC ", "data/latest_sec.json"),
]

# ★ 이 스크립트가 산출물에서 실제로 읽는 키 (T10 이 이 목록을 검사한다)
CONTRACT = {
    "top": ["collected_for_kst_date", "collected_at_utc", "source_tier",
            "summary", "stocks", "decision_readiness"],
    "decision_readiness": ["confirmed_through", "same_day_confirmation", "not_decision_ready"],
    "stock_ok": ["status", "name", "latest_trading_day", "latest_observed_day",
                 "missing_investors", "investor_rows_missing"],
}

LINE = "─" * 62


def _need(d: dict, key: str, label: str = ""):
    """계약된 키를 읽는다. **없으면 조용히 None 을 돌려주지 않고 사실을 드러낸다.**"""
    if key not in d:
        return f"⚠ 키 없음({key})"
    return d[key]


def summarize(label: str, path: str) -> None:
    if not os.path.exists(path):
        print(f"  {label} | ❌ 파일 없음 — {path}")
        return

    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  {label} | ⚠ 파싱 실패 — {type(e).__name__}: {e}")
        return

    summary = d.get("summary") or {}
    ok = summary.get("ok", "?")
    failed = summary.get("failed", "?")
    mark = "✅" if failed == 0 else "⚠"

    print(f"  {label} | {mark} ok={ok} failed={failed}")
    print(f"       | collected_for_kst_date : {d.get('collected_for_kst_date')}")
    print(f"       | collected_at_utc       : {d.get('collected_at_utc')}")
    print(f"       | source_tier            : {d.get('source_tier')}")

    ltd = lod = None
    for s in (d.get("stocks") or {}).values():
        ltd = s.get("latest_trading_day") or ltd
        lod = s.get("latest_observed_day") or lod
    if ltd:
        print(f"       | latest_trading_day     : {ltd}   (확정)")
    if lod and lod != ltd:
        print(f"       | latest_observed_day    : {lod}   ⚠ 미확정 — Decision 입력에서 제외됨")

    dr = d.get("decision_readiness")
    if isinstance(dr, dict):
        # ★ 키 이름은 CONTRACT["decision_readiness"] 와 반드시 일치해야 한다 (T10 이 대조)
        print(f"       | confirmed_through      : {_need(dr, 'confirmed_through')}"
              f"   (당일확정규칙={_need(dr, 'same_day_confirmation')})")
        if dr.get("not_decision_ready"):
            print(f"       | ❌ 확정 거래일 없음     : {dr['not_decision_ready']}")

    # 실패한 종목이 있으면 error 원문을 그대로 보여준다 — 요약하지 않는다
    for code, s in (d.get("stocks") or {}).items():
        if s.get("status") == "FAILED":
            print(f"       | ❌ FAILED {code} {s.get('name')} — {s.get('error')}")
        missing = s.get("missing_investors")
        if missing:
            print(f"       | ⚠ 컬럼 누락 {code} — missing_investors={missing}")
        # ★ 행 자체가 없는 경우. missing_investors 로는 절대 드러나지 않는 상태다.
        rows = s.get("investor_rows_missing")
        if rows:
            print(f"       | ⚠ 수급 행 부재 {code} — investor_rows_missing={rows}")


def main() -> None:
    print(LINE)
    print("  산출물 요약 — 저장소에는 커밋되지 않는다 (아티팩트로만 보관)")
    print(LINE)
    for label, path in TARGETS:
        summarize(label, path)
        print(f"  {'-' * 58}")
    print()
    print("  판정 기준")
    print("   · collected_for_kst_date 가 오늘(KST)이면 수집 경로 정상")
    print("   · latest_trading_day 가 직전 거래일이면 정상 (개장 전·휴장일 포함)")
    print("   · 당일 행은 정책상 항상 미확정이다 — latest_observed_day 만 오늘이면 정상")
    print("     (확정은 다음 날 아침 수집에서 일어난다. 시각으로 확정하지 않는다)")
    print("   · investor_rows_missing 은 missing_investors 와 다른 축이다 ([] 이라고 수급 정상이 아니다)")
    print("   · SEC 가 '파일 없음' 이면 SEC_USER_AGENT 시크릿부터 확인")
    print(LINE)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[verify_summary] 요약 실패 — {type(e).__name__}: {e}")
    sys.exit(0)
