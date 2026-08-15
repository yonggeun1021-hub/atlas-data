"""KRX 투자자별 수급 + OHLCV 수집 (pykrx)

원천: KRX 정보데이터시스템 (data.krx.co.kr)
인증: 환경변수 KRX_ID / KRX_PW  (pykrx 1.2.8+ 요구사항)

v4.1 (2026-08-14) — **당일 행은 확정하지 않는다. 단일 경로.** (CIO 승인)

  확정 규칙은 두 줄이 전부다.
      · 지난 거래일  → 확정.        `prior_session`
      · 당일        → 확정하지 않음. `deferred_to_next_day`
                       다음 날 아침 수집에서 '지난 거래일'이 되며 자동으로 확정된다.

  ★ 이 파일에는 **판정에 쓰이는 시계 입력이 없다.**
    `collect_one()` 은 `now` 를 인자로 받지 않는다. 받을 이유가 없기 때문이다.
    따라서 "06:05 에 돌렸는가 10:52 에 돌렸는가"는 산출물에 영향을 줄 수 **없다** —
    조건부로 성립하는 성질이 아니라 구조적으로 성립한다.

  ── 왜 이렇게 됐는가 (설계 이력) ─────────────────────────────────────────
  2026-08-14 06:00 정기 수집이 실패해 10:52 KST 에 수동 재수집했더니, `end=today`
  로 조회하면서 **장중 미확정 행**이 그대로 `latest_trading_day` 와 SMA20 에 들어갔다.
  5종목 전부 SMA20 이 이동했다 (삼성전자 243,500 → 244,150 등).
  데이터가 틀린 게 아니라 **복구 버튼을 누른 시각에 따라 Decision 입력이 달라졌다.**

  1차 수정안은 "16:00 KST 이후 + 수급 행 존재 → 확정" 이었고 **CIO 가 반려했다.**
  pykrx 응답에는 최종성(finality) 플래그가 없다. "수급 행이 있다"는 데이터가 왔다는
  사실일 뿐, 그 값이 더 이상 움직이지 않는다는 보장이 아니다. 시각·건수·존재 여부는
  최종성의 필요조건은 될 수 있어도 충분조건이 될 수 없고, 충분조건으로 쓰는 순간
  그것은 추정치다. **Atlas 는 추정치를 만들지 않는다.**

  2차안은 재조회 안정성 게이트(`stability`)를 옵션으로 남기는 것이었고, 이 역시
  **CIO 가 제거를 지시했다** — 실행된 적 없는 경로를 production 에 두지 않는다
  (운영 정본 §13 Execution Evidence Audit). 설계는 `docs/spec_same_day_confirmation.md`
  에 남겨 두었고, 실제로 필요해지면 그때 검증해서 v4.2 로 올린다.

  ── 관측은 버리지 않는다. 판정 입력만 가둔다 ──────────────────────────────
    ① 당일 행도 `daily` 에 그대로 남는다. 행마다 `confirmed` / `confirm_reason`.
    ② `latest_trading_day` 는 **확정된** 마지막 거래일. 당일 관측치는
       `latest_observed_day` 로 분리한다. 두 사실을 같은 필드에 담지 않는다.
    ③ SMA20 은 확정 행만으로 계산하고 `sma20_through` 로 범위를 밝힌다.

  ── v3.x 가 표현하지 못하던 상태 하나 ────────────────────────────────────
    · `missing_investors`     행은 있는데 **컬럼**이 없다
    · `investor_rows_missing` **행 자체가 없다**   ← 신설
      기존 코드는 `if idx in src_df.index:` 로 조용히 건너뛰어, 수급 행이 통째로
      없어도 `missing_investors = []` 였다. **`[]` 를 '수급 정상'으로 읽으면 안 된다.**
      이제 행이 없으면 값 자리에 명시적 `null` 이 들어가고 날짜가 목록에 남는다.
      (키를 지우면 읽는 쪽이 `x or 0` 으로 **0(순매수 없음)과 구분하지 못한다** —
       실제로 이 때문에 "8/14 수급이 전부 0" 오보가 나갔다.)

v3.1 (2026-08-13) — Stage 와 Coverage 를 분리한다 (CIO 확정)
  `atlas_stage: "Coverage"` 는 쓰지 않는다. Coverage 는 단계가 아니라 추적 대상 여부다.
  → `{"atlas_stage": null, "coverage": true}` / `{"atlas_stage": "Candidate", "coverage": true}`

v3 (2026-08-13) — 종목 레벨에 Atlas 단계를 실어 보낸다
  문제: 종목 payload에 `stage: null`만 있어, 브리핑이 latest_krx.json만 읽으면
        효성중공업이 Candidate인지 알 수 없었다. 조용한 누락이다.
  수정: Notion `편입 사유`의 `Atlas Stage:` 태그를 종목마다 실어 보낸다.
        DB select 원본은 db_state 로 참고 보존만 하고 판정에 쓰지 않는다.

v2 (2026-08-13) — 수정 2건
  ① 외국인 컬럼 누락 수정 — 기본 조회 컬럼명은 '외국인합계'이지 '외국인'이 아니다.
  ② 조용한 누락 금지 — 요청한 구분이 응답에 없으면 missing_investors 에 명시한다.
"""
import os
import sys
import datetime as dt

from common import (save, save_incident, load_universe, today_kst, now_utc_iso,
                    record_stage_snapshot, stage_distribution)

if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
    print("FATAL: KRX_ID / KRX_PW 환경변수가 없습니다. GitHub Secrets를 확인하세요.")
    sys.exit(1)

from pykrx import stock  # noqa: E402

# 기본 조회(detail=False) 컬럼 — 판정에 쓰는 공식 집계
BASIC = ["기관합계", "외국인합계", "개인", "기타법인"]

# 상세 조회(detail=True) 컬럼 — 참고용 세부 주체
DETAIL = [
    "금융투자", "보험", "투신", "사모", "은행", "기타금융",
    "연기금", "기타법인", "개인", "외국인", "기타외국인",
]

LOOKBACK_DAYS = 40  # 20일선 계산용 영업일 확보

KST = dt.timezone(dt.timedelta(hours=9))

# 판정에 쓰는 수급 소스 — detail 은 참고용이므로 결손 판정에 넣지 않는다
BASIC_SOURCES = ("net_value", "net_volume")

# ★ 당일 확정 규칙. 운영 경로는 이것 하나뿐이다 (CIO 확정 2026-08-14).
#   다른 값을 쓰는 분기는 이 파일에 존재하지 않는다 — 대안 설계는
#   docs/spec_same_day_confirmation.md 에 문서로만 있다.
SAME_DAY_CONFIRMATION = "next_day"


def now_kst() -> dt.datetime:
    """현재 KST 시각. **기록용이다 — 확정 판정에는 쓰이지 않는다.**"""
    return dt.datetime.now(KST)


def confirm_state(day: str, today: str):
    """이 행을 Decision 입력으로 써도 되는가 — 순수 함수. 시계를 보지 않는다.

    반환 (confirmed: bool, reason: str)
      prior_session         지난 거래일 → 확정
      deferred_to_next_day  당일 → 확정하지 않는다. 다음 날 아침 수집에서 확정된다
      future_date           있을 수 없다. 방어적으로 미확정 처리하고 흔적을 남긴다
    """
    if day < today:
        return True, "prior_session"
    if day > today:
        return False, "future_date"
    return False, "deferred_to_next_day"


def pick(row, columns, wanted):
    """요청한 구분 중 응답에 없는 것을 조용히 버리지 않는다.

    ⚠ 이 함수가 잡는 것은 '행은 있는데 **컬럼**이 없다' 뿐이다.
       '**행 자체가 없다**' 는 여기까지 오지도 않는다 — collect_one() 이 따로 기록한다.
    """
    got = {k: int(row[k]) for k in wanted if k in columns}
    missing = [k for k in wanted if k not in columns]
    return got, missing


def collect_one(code: str, start: str, end: str, today: str = None) -> dict:
    """★ 인자에 시계가 없다. 같은 원천·같은 today 면 언제 돌려도 같은 결과가 나온다."""
    if today is None:
        today = today_kst().isoformat()
    elif isinstance(today, dt.date):
        today = today.isoformat()

    val = stock.get_market_trading_value_by_date(start, end, code)
    vol = stock.get_market_trading_volume_by_date(start, end, code)
    val_d = stock.get_market_trading_value_by_date(start, end, code, detail=True)
    vol_d = stock.get_market_trading_volume_by_date(start, end, code, detail=True)
    ohlcv = stock.get_market_ohlcv_by_date(start, end, code)

    missing_all = set()      # 컬럼 누락 (행은 존재)
    rows_absent = {}         # 행 자체 부재 — {소스명: [날짜, ...]}
    daily = {}

    for idx in ohlcv.index:
        key = idx.strftime("%Y-%m-%d")
        entry = {
            "close": int(ohlcv.loc[idx, "종가"]),
            "open": int(ohlcv.loc[idx, "시가"]),
            "high": int(ohlcv.loc[idx, "고가"]),
            "low": int(ohlcv.loc[idx, "저가"]),
            "volume": int(ohlcv.loc[idx, "거래량"]),
            "change_pct": float(ohlcv.loc[idx, "등락률"]),
        }

        absent = []
        for src_df, wanted, name in (
            (val,   BASIC,  "net_value"),
            (vol,   BASIC,  "net_volume"),
            (val_d, DETAIL, "net_value_detail"),
            (vol_d, DETAIL, "net_volume_detail"),
        ):
            if idx in src_df.index:
                got, missing = pick(src_df.loc[idx], src_df.columns, wanted)
                entry[name] = got
                missing_all.update(missing)
            else:
                # ★ 행 자체가 없다. 키를 빼지 않고 명시적 null 로 남긴다.
                entry[name] = None
                absent.append(name)
                rows_absent.setdefault(name, []).append(key)

        entry["investor_rows_absent"] = absent
        # ★ provenance Part A — 이 행이 '언제 관측됐는가'를 남긴다 (2026-08-15).
        #   확정 판정에는 쓰지 않는다. 아카이브가 스스로를 설명하게 하는 것이 목적이다.
        #   (8/14 아카이브는 장중 수집분인데 그 사실이 파일 어디에도 없었다)
        entry["observed_at_kst"] = now_kst().isoformat(timespec="seconds")
        entry["confirmed"], entry["confirm_reason"] = confirm_state(key, today)
        daily[key] = entry

    ordered = sorted(daily)
    confirmed_days = [d for d in ordered if daily[d]["confirmed"]]
    unconfirmed_days = [d for d in ordered if not daily[d]["confirmed"]]

    # ★ SMA20 은 확정 행만 쓴다. 당일 행이 섞이면 같은 날 두 번 돌릴 때 값이 달라진다.
    closes = [daily[d]["close"] for d in confirmed_days]
    window = closes[-20:]
    sma20 = round(sum(window) / 20, 2) if len(window) == 20 else None

    rows_missing = sorted({d for n in BASIC_SOURCES for d in rows_absent.get(n, [])})

    return {
        "daily": daily,

        # ── 시간축 — '확정'과 '관측'을 절대 같은 필드에 담지 않는다 ──────────
        "latest_trading_day": confirmed_days[-1] if confirmed_days else None,
        "latest_observed_day": ordered[-1] if ordered else None,
        "unconfirmed_days": unconfirmed_days,
        "decision_ready": bool(confirmed_days),

        # ── SMA20 ────────────────────────────────────────────────────────
        "sma20": sma20,                              # 확정 20일 미만이면 None — 추정하지 않는다
        "sma20_basis": len(window),
        "sma20_through": confirmed_days[-1] if confirmed_days else None,
        "sma20_status": "ok" if sma20 is not None else "insufficient_confirmed_history",

        # ── 수급 결손 — 두 축은 서로 다른 상태다 ─────────────────────────────
        "missing_investors": sorted(missing_all),    # 행 O / 컬럼 X
        "investor_rows_missing": rows_missing,       # 행 X  (판정용 BASIC 기준 날짜)
        "investor_rows_missing_by_source": {k: sorted(v) for k, v in sorted(rows_absent.items())},
    }


def meta(s: dict) -> dict:
    """★ Atlas 단계는 Notion `편입 사유`의 Atlas Stage 태그에서 온다 (CIO 확정 2026-08-13).
    Stage 와 Coverage 는 서로 다른 축이다 — Coverage 는 Stage 값이 아니다.
      atlas_stage : Discovery / Candidate / Ready / Buy / Holding / Closed / None
      coverage    : true / false / None(Unknown)
    DB select 원본(db_state)은 참고 보존만 하고 판정에 쓰지 않는다."""
    return {
        "atlas_stage": s.get("atlas_stage"),
        "coverage": s.get("coverage"),
        "db_state": s.get("db_state"),
        "in_notion": s.get("in_notion"),
    }


def main() -> None:
    today = today_kst()
    start = (today - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    payload = {
        "collected_at_utc": now_utc_iso(),
        # 언제 돌았는지는 기록한다. 다만 이 값은 **판정에 쓰이지 않는다.**
        "collected_at_kst": now_kst().isoformat(timespec="seconds"),
        "collected_for_kst_date": today.isoformat(),
        "source": "KRX 정보데이터시스템 (pykrx)",
        "source_tier": "Official",
        "collector_version": "v4.1",
        "range": {"start": start, "end": end},

        # ★ 판정 규칙을 데이터에 함께 실어 보낸다 — 읽는 쪽이 규칙을 추측하지 않게 한다
        "same_day_confirmation": SAME_DAY_CONFIRMATION,
        "confirmation_note": (
            "지난 거래일은 실행 시각과 무관하게 확정이다. 당일 행은 항상 unconfirmed 이며 "
            "다음 날 아침 수집에서 확정된다. 미확정 행은 daily 에 관측으로 보존되지만 "
            "latest_trading_day / SMA20 에서는 제외된다."
        ),
        "stocks": {},
    }

    universe = load_universe()
    record_stage_snapshot(universe, today)   # 단계 이력은 수집 성패와 무관하게 먼저 남긴다
    payload["stage_distribution"] = stage_distribution(universe, today)

    ok = failed = 0
    for s in universe:
        code, name = s["code"], s["name"]
        try:
            payload["stocks"][code] = {
                "name": name,
                **meta(s),
                "status": "ok",
                **collect_one(code, start, end, today.isoformat()),
            }
            ok += 1
            item = payload["stocks"][code]
            warn = []
            if item.get("missing_investors"):
                warn.append(f"컬럼누락={item['missing_investors']}")
            if item.get("investor_rows_missing"):
                warn.append(f"수급행없음={item['investor_rows_missing']}")
            if item.get("unconfirmed_days"):
                warn.append(f"미확정={item['unconfirmed_days']}")
            print(f"[ok]     {code} {name} "
                  f"[stage={s.get('atlas_stage')} coverage={s.get('coverage')}]"
                  f" 확정={item.get('latest_trading_day')}"
                  + (f"  ⚠ {' / '.join(warn)}" if warn else ""))
        except Exception as e:                      # noqa: BLE001
            payload["stocks"][code] = {
                "name": name,
                **meta(s),
                "status": "FAILED",
                "error": f"{type(e).__name__}: {e}",
            }
            failed += 1
            print(f"[FAILED] {code} {name} — {type(e).__name__}: {e}")

    # ★ summary 는 {ok, failed} 스키마를 유지한다 — guard.py 와 기존 테스트가 이 모양에 의존한다.
    payload["summary"] = {"ok": ok, "failed": failed}

    # 확정 상태 요약은 별도 키로 분리한다 (Step 0 이 한눈에 읽는 곳)
    live = {c: v for c, v in payload["stocks"].items() if v.get("status") == "ok"}
    confirmed = [v["latest_trading_day"] for v in live.values() if v.get("latest_trading_day")]
    payload["decision_readiness"] = {
        "confirmed_through": max(confirmed) if confirmed else None,
        "same_day_confirmation": SAME_DAY_CONFIRMATION,
        "not_decision_ready": sorted(c for c, v in live.items() if not v.get("decision_ready")),
        "stocks_with_unconfirmed_rows": sorted(c for c, v in live.items() if v.get("unconfirmed_days")),
        "stocks_with_investor_rows_missing": sorted(c for c, v in live.items()
                                                    if v.get("investor_rows_missing")),
    }

    if ok == 0:
        save_incident(payload, "krx.json", today)
        print("FATAL: 모든 종목 수집 실패 — 정본 미갱신")
        sys.exit(1)

    save(payload, "krx.json", today)


if __name__ == "__main__":
    main()
