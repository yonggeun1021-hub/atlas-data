"""
Verify Collect Path — 산출물 요약 (읽기 전용)

verify.yml 에서만 쓴다. 수집기가 만든 data/latest_*.json 을 읽어
"무엇이 실제로 만들어졌는가"를 한눈에 출력한다.

⚠ 이 스크립트는 아무것도 쓰지 않고 아무것도 판정하지 않는다. 출력만 한다.
   실패해도 워크플로를 죽이지 않는다(항상 exit 0).
"""
import json
import os
import sys

TARGETS = [
    ("KRX ", "data/latest_krx.json"),
    ("DART", "data/latest_dart.json"),
    ("SEC ", "data/latest_sec.json"),
]

LINE = "─" * 62


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

    ltd = None
    for s in (d.get("stocks") or {}).values():
        ltd = s.get("latest_trading_day") or ltd
    if ltd:
        print(f"       | latest_trading_day     : {ltd}")

    # 실패한 종목이 있으면 error 원문을 그대로 보여준다 — 요약하지 않는다
    for code, s in (d.get("stocks") or {}).items():
        if s.get("status") == "FAILED":
            print(f"       | ❌ FAILED {code} {s.get('name')} — {s.get('error')}")
        missing = s.get("missing_investors")
        if missing:
            print(f"       | ⚠ missing_investors {code} — {missing}")


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
    print("   · SEC 가 '파일 없음' 이면 SEC_USER_AGENT 시크릿부터 확인")
    print(LINE)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[verify_summary] 요약 실패 — {type(e).__name__}: {e}")
    sys.exit(0)
