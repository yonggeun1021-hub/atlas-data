#!/usr/bin/env python3
"""C2 Transport Reachability + Payload Verification — 수동 실행 전용 (CIO 승인 2026-08-15).

C2 = FSC 증권선물국 / TWSE 공식 개방데이터 「상장사 월별 영업수입 집계표」.

★ 이 run 의 성격 (CIO 판정 그대로)
   이것은 **source 채택 run 이 아니다**. transport reachability + payload
   verification 뿐이다. GitHub-hosted runner 가 현재의 정직한 UA 로 C2 에
   실제로 도달하는지, 그리고 응답 안에 2330 행이 있는지를 본다.

⛔ 이 스크립트가 하지 않는 것 — 전부 CIO 판정 사항이라서 하지 않는다
   · C1(TSMC IR) fallback 연결 — C2 가 실패해도 C1 을 시도하지 않는다
   · C2 실패 시 C3(TWSE OpenAPI) · C4(SEC EDGAR) 자동 확장
   · 재시도 · UA 변경 · 헤더 추가 · 그 밖의 차단 우회 실험 (GET 1회뿐)
   · `config/rules.json` · collector · Source Contract · fixture · Rule 상태 변경
   · 저장소 파일 생성 · 수정 · commit · push
   · **정밀도 normalization** — `44.68755126916978 → 44.7` 변환을 하지 않는다.
     그 변환이 "TSMC 공표값 재현"인지 "C2 고유값을 새 Decision 값으로 채택"인지는
     아직 미판정인 Source Contract 문제다. 여기서는 **원문 그대로** 출력만 한다.
   · fixture 와의 일치/불일치 **판정** — 값을 나란히 놓기만 하고 결론짓지 않는다.
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time

# ── C2 고정 대상 (CIO 승인 범위 밖으로 나가지 않는다) ────────────────
C2_URL = "https://mopsfin.twse.com.tw/opendata/t187ap05_L.csv"
TARGET_CODE = "2330"

# ★ 현행 계약의 정직한 UA 를 그대로 쓴다 — 위장하지 않는다.
#   `collectors/tsmc_monthly.py` 의 FETCH_USER_AGENT 와 동일 문자열이다.
#   (이 branch 에는 그 파일이 없으므로 import 하지 않고 같은 값을 둔다.)
FETCH_USER_AGENT = "Atlas Research (yonggeun1021@gmail.com)"
FETCH_TIMEOUT_SEC = 30

# 승인된 TSV fixture 가 담고 있는 관측값 — **대조 표시용**이며 판정하지 않는다.
FIXTURE_JUL_REV_MN = "467580"
FIXTURE_JUL_YOY = "44.7"
FIXTURE_TOTAL_REV_MN = "2872064"
FIXTURE_TOTAL_YOY = "37.0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch_once(url: str, timeout: int = FETCH_TIMEOUT_SEC):
    """GET 1회. 실패하면 예외를 그대로 올린다 — fallback 없음, 재시도 없음."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": FETCH_USER_AGENT})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return {
            "status": r.status,
            "final_url": r.geturl(),
            "headers": dict(r.headers),
            "body": body,
            "elapsed_sec": round(time.monotonic() - t0, 3),
        }


def main() -> int:
    print("C2 Transport Reachability + Payload Verification")
    print(f"  url  {C2_URL}")
    print(f"  ua   {FETCH_USER_AGENT} · timeout {FETCH_TIMEOUT_SEC}s · GET 1회")
    print("  ⛔ C1 fallback 없음 · C3/C4 자동 확장 없음 · 재시도 없음")
    print("  ⛔ 저장소를 쓰지 않는다 · 정밀도 normalization 하지 않는다\n")

    # ── ① transport reachability ───────────────────────────────────
    res = fetch_once(C2_URL)          # 실패 시 예외 → non-zero exit
    body = res["body"]
    ctype = res["headers"].get("Content-Type", "(없음)")
    from urllib.parse import urlparse
    final_host = urlparse(res["final_url"]).netloc

    print("① transport")
    print(f"  HTTP status      {res['status']}")
    print(f"  final URL        {res['final_url']}")
    print(f"  final domain     {final_host}")
    print(f"  Content-Type     {ctype}")
    print(f"  응답 크기        {len(body)} bytes")
    print(f"  소요             {res['elapsed_sec']}s")
    print(f"  redirect 여부    {'있음' if res['final_url'] != C2_URL else '없음'}")

    # ── ② 디코딩 — 선언된 charset 을 먼저 보고, 조용히 바꾸지 않는다 ──
    print("\n② 디코딩")
    declared = ""
    if "charset=" in ctype.lower():
        declared = ctype.lower().split("charset=", 1)[1].strip().strip('"; ')
    print(f"  Content-Type 선언 charset  {declared or '(선언 없음)'}")
    try:
        text = body.decode("utf-8")
        print("  utf-8 디코딩              성공")
    except UnicodeDecodeError as e:
        print(f"  utf-8 디코딩              실패 — {e}")
        print("  ⛔ 다른 인코딩을 임의로 시도하지 않는다. 인코딩 계약은 CIO 판정 대상이다.")
        return 1

    # ── ③ header schema ────────────────────────────────────────────
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        print("\n⛔ 본문이 비어 있다 — 행 0건")
        return 1
    header = rows[0]
    print(f"\n③ header schema — {len(header)} 컬럼")
    for i, col in enumerate(header):
        print(f"  [{i:2d}] {col}")
    print(f"  데이터 행 수     {len(rows) - 1}")

    # ── ④ 2330 행 존재 여부 ────────────────────────────────────────
    try:
        code_idx = header.index("公司代號")
    except ValueError:
        print("\n⛔ header 에 `公司代號` 컬럼이 없다 — schema 가 조사 시점과 다르다")
        return 1

    hits = [r for r in rows[1:] if len(r) > code_idx and r[code_idx] == TARGET_CODE]
    print(f"\n④ 公司代號={TARGET_CODE} 행")
    print(f"  일치 행 수       {len(hits)}")
    if len(hits) != 1:
        print("  ⛔ 정확히 1건이 아니다 — 관측값을 읽지 않고 중단한다")
        return 1

    row = dict(zip(header, hits[0]))
    print("  전체 필드 (원문 그대로):")
    for k in header:
        print(f"    {k} = {row.get(k)!r}")

    # ── ⑤ 관측값 출력 — 변환·반올림·판정 없음 ──────────────────────
    print("\n⑤ 관측값 — ⛔ 원문 그대로, 정밀도 변환 없음")
    obs = [
        ("월매출 (당월영수, 千元)", "營業收入-當月營收", FIXTURE_JUL_REV_MN + " (백만, fixture)"),
        ("월 YoY (%)", "營業收入-去年同月增減(%)", FIXTURE_JUL_YOY + " (fixture)"),
        ("누계 매출 (千元)", "累計營業收入-當月累計營收", FIXTURE_TOTAL_REV_MN + " (백만, fixture)"),
        ("누계 YoY (%)", "累計營業收入-前期比較增減(%)", FIXTURE_TOTAL_YOY + " (fixture)"),
    ]
    for label, col, fixture_note in obs:
        print(f"  {label}")
        print(f"    C2 원문   {row.get(col)!r}")
        print(f"    참고      {fixture_note}")
    print(f"  資料年月         {row.get('資料年月')!r}   (民國 연월)")
    print(f"  出表日期         {row.get('出表日期')!r}   ⛔ 표 생성일이며 TSMC 발표일이 아니다")
    print("\n  ⛔ 위 값들의 fixture 일치 여부를 이 스크립트는 판정하지 않는다.")
    print("     단위(千元 ↔ 백만)와 정밀도(미반올림 ↔ 공표값) 매핑은 미판정 Source Contract 사안이다.")

    # ── ⑥ 저장소 무변경 ────────────────────────────────────────────
    print("\n⑥ 저장소")
    made = [p for p in ("data/latest_tsmc_monthly.json", "data/t187ap05_L.csv",
                        "collectors/fixtures/t187ap05_L.csv")
            if os.path.exists(os.path.join(ROOT, p))]
    print(f"  이 스크립트가 만들 수 있었던 산출물 존재 여부  {made or '없음'}")
    if made:
        print("  ⛔ 예기치 않은 파일이 있다")
        return 1

    print("\n✅ C2 transport reachability 확인 + payload 관측 완료")
    print("   ⛔ 이것은 source 채택이 아니다. `Official Fetch/Extraction` 은 OPEN 유지.")
    print("   ⛔ RULE-0003/0007/0008 상태 변경 없음 · Production HOLD 유지 · evaluator 미연결.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
