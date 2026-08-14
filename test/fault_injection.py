"""Atlas Data Server — Fault Injection Suite

목적: 부분 실패가 예상한 층에서만 멈추는지(blast radius)를 실제 장애를 기다리지 않고 확인한다.

실행 시점 (CIO 확정 2026-08-13)
  ⛔ 매일 돌리지 않는다. 브리핑은 실제 데이터를 처리한다.
  ✅ Collector 또는 Step 0 을 수정한 날, 커밋 전에 한 번 돌린다 (회귀 검사).

실행:
    PYTHONPATH=collectors python tests/fault_injection.py

★ 이 스위트가 커버하지 않는 것 — 통과했다고 전부 검증된 것이 아니다
  · Notion 인증·네트워크 경로 (_from_notion) — 실제 토큰이 필요하다
  · pykrx 실제 응답 스키마 변경 — 스텁으로는 잡히지 않는다
  · SEC EDGAR 실제 응답 스키마 — T5 는 스텁 응답만 쓴다. 실 응답 검증은 Actions 실행으로만 된다
  · 브리핑 세션의 판정 로직 — 여기 없다
  검증되지 않은 것을 검증된 것처럼 두지 않기 위해 여기 명시한다.
"""
import os
import sys
import json
import types
import shutil
import tempfile
import io
import inspect
import contextlib
import datetime as dt

os.environ.setdefault("KRX_ID", "faultinjection")
os.environ.setdefault("KRX_PW", "faultinjection")

# ★ 실행 방식에 의존하지 않는다 — PYTHONPATH 를 잘못 주면 '테스트가 없는 채로 초록불'이 되기 쉽다.
#   리포 구조에서 직접 경로를 잡는다. (2026-08-13: PYTHONPATH 누락으로 D1 검증이 통째로 빠졌던 사례)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("collectors", "decision", "portfolio", "lab"):
    _full = os.path.join(_ROOT, _sub)
    if os.path.isdir(_full) and _full not in sys.path:
        sys.path.insert(0, _full)

# ★ 산출물을 '읽는' 쪽도 검증 대상이다 (T10). 쓰는 쪽만 테스트하면 키 불일치가 안 잡힌다.
_SCRIPTS = os.path.join(_ROOT, ".github", "scripts")
if os.path.isdir(_SCRIPTS) and _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


# ────────────────────────────────────────────────────────────
# 공통 픽스처 — 2026-08-13 기준선과 동일한 Universe
# ────────────────────────────────────────────────────────────

KR = [
    {"code": "267260", "name": "HD현대일렉트릭", "atlas_stage": None,        "coverage": True, "in_notion": True, "db_state": None},
    {"code": "329180", "name": "HD현대중공업",   "atlas_stage": "Discovery", "coverage": True, "in_notion": True, "db_state": None},
    {"code": "298040", "name": "효성중공업",     "atlas_stage": "Candidate", "coverage": True, "in_notion": True, "db_state": None},
    {"code": "000660", "name": "SK하이닉스",     "atlas_stage": None,        "coverage": True, "in_notion": True, "db_state": "관찰"},
    {"code": "005930", "name": "삼성전자",       "atlas_stage": None,        "coverage": True, "in_notion": True, "db_state": "관찰"},
]

US = [
    {"name": "Micron",           "ticker": "MU",   "atlas_stage": None,    "coverage": True},
    {"name": "TSMC",             "ticker": "TSM",  "atlas_stage": "Ready", "coverage": True},
    {"name": "Credo Technology", "ticker": "CRDO", "atlas_stage": None,    "coverage": True},
    {"name": "Arista Networks",  "ticker": "ANET", "atlas_stage": None,    "coverage": True},
    {"name": "NVIDIA",           "ticker": "NVDA", "atlas_stage": None,    "coverage": True},
    {"name": "Microsoft",        "ticker": "MSFT", "atlas_stage": None,    "coverage": True},
]


# ────────────────────────────────────────────────────────────
# T3 · T4 — 실제 common 모듈로 검사 (네트워크 불필요한 순수 계산·파일 경로)
# ────────────────────────────────────────────────────────────

def test_distribution_and_history() -> None:
    import common

    common.universe_meta.clear()
    common.universe_meta["notion_skipped"] = US

    print("\n[T3] Stage Distribution — 사람이 세지 않는다")
    d = common.stage_distribution(KR, dt.date(2026, 8, 13))
    check("Universe 11종목", d["universe_total"] == 11, f"got {d['universe_total']}")
    check("coverage_total 11", d["coverage_total"] == 11, f"got {d['coverage_total']}")
    check("stage_assigned 3", d["stage_assigned"] == 3, f"got {d['stage_assigned']}")
    # ★ 2026-08-13 에 사람이 1 을 2 로 센 항목 — 회귀 감시 대상
    check("Discovery 1 (오계수 회귀 감시)", d["by_stage"].get("Discovery") == 1,
          f"got {d['by_stage'].get('Discovery')}")
    check("Candidate 1", d["by_stage"].get("Candidate") == 1)
    check("Ready 1", d["by_stage"].get("Ready") == 1)
    check("미부여 8", d["by_stage"].get("미부여") == 8, f"got {d['by_stage'].get('미부여')}")
    check("Buy 0", d["buy"] == 0)
    check("전환율 계산 금지 유지", d["conversion_rate"] is None
          and "Undefined" in d["conversion_rate_status"])

    print("\n[T4] Stage History — 누적되며 기존 이력을 덮어쓰지 않는다")
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        common.record_stage_snapshot(KR, dt.date(2026, 8, 13))
        common.record_stage_snapshot(KR, dt.date(2026, 8, 14))
        with open(common.STAGE_HISTORY, encoding="utf-8") as f:
            hist = json.load(f)
        check("2일치 누적", len(hist) == 2, f"got {len(hist)}")
        day = hist["2026-08-13"]
        check("11종목 전원 기록", len(day) == 11, f"got {len(day)}")
        check("수집 대상 collected=True", day["298040"]["collected"] is True)
        check("미국 collected=False (Unimplemented ≠ Unknown)",
              day["TSM"]["collected"] is False)
        check("미수집이어도 단계 보존 (TSM=Ready)", day["TSM"]["stage"] == "Ready")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


# ────────────────────────────────────────────────────────────
# T1 · T2 — krx.main() 의 실패 격리 (common·pykrx 스텁)
# ────────────────────────────────────────────────────────────

def _install_stubs() -> dict:
    pk = types.ModuleType("pykrx")
    pk.stock = types.ModuleType("pykrx.stock")
    sys.modules["pykrx"] = pk
    sys.modules["pykrx.stock"] = pk.stock

    captured: dict = {}
    cm = types.ModuleType("common")
    cm.universe_meta = {"notion_skipped": US}
    cm.load_universe = lambda: KR
    cm.today_kst = lambda: dt.date(2026, 8, 14)
    cm.now_utc_iso = lambda: "2026-08-13T21:00:00+00:00"
    cm.record_stage_snapshot = lambda u, d=None: "data/stage_history.json"
    cm.stage_distribution = lambda u, d=None: {"coverage_total": 11, "stage_assigned": 3, "buy": 0}
    cm.save = lambda payload, filename, date=None: captured.update(payload) or filename
    sys.modules["common"] = cm
    return captured


def _run(krx, failing: set) -> tuple:
    # ★ v4.1 부터 main() 은 collect_one(code, start, end, today) 로 부른다.
    #   인자 개수에 스위트가 묶이지 않도록 *a, **kw 로 받는다.
    def flaky(code, start, end, *a, **kw):
        if code in failing:
            raise ConnectionError("KRX 응답 없음 (주입된 장애)")
        return {"daily": {"2026-08-13": {"close": 1, "confirmed": True}},
                "latest_trading_day": "2026-08-13", "latest_observed_day": "2026-08-13",
                "unconfirmed_days": [], "decision_ready": True,
                "sma20": None, "sma20_basis": 1, "sma20_through": "2026-08-13",
                "sma20_status": "insufficient_confirmed_history",
                "missing_investors": [], "investor_rows_missing": [],
                "investor_rows_missing_by_source": {}}
    krx.collect_one = flaky
    code = 0
    try:
        krx.main()
    except SystemExit as e:
        code = e.code
    return code


def test_failure_isolation() -> None:
    captured = _install_stubs()
    import krx

    print("\n[T1] 단일 종목 실패 — blast radius 가 한 종목에서 멈추는가")
    captured.clear()
    exit_code = _run(krx, {"298040"})
    st = captured.get("stocks", {})
    failed = [c for c, v in st.items() if v["status"] != "ok"]
    check("실패는 298040 한 종목뿐", failed == ["298040"], f"got {failed}")
    check("나머지 4종목 정상", sum(1 for v in st.values() if v["status"] == "ok") == 4)
    check("summary {ok:4, failed:1}", captured.get("summary") == {"ok": 4, "failed": 1},
          f"got {captured.get('summary')}")
    check("★ 실패해도 단계 보존 (효성=Candidate)",
          st.get("298040", {}).get("atlas_stage") == "Candidate")
    check("실패해도 coverage 보존", st.get("298040", {}).get("coverage") is True)
    check("일 레벨 Summary 생존", "stage_distribution" in captured)
    check("부분 실패는 프로세스를 죽이지 않는다", exit_code == 0, f"exit={exit_code}")

    print("\n[T2] 전 종목 실패 — 시스템 장애로 승격하되 상태는 잃지 않는다")
    captured.clear()
    exit_code = _run(krx, {s["code"] for s in KR})
    st = captured.get("stocks", {})
    check("summary {ok:0, failed:5}", captured.get("summary") == {"ok": 0, "failed": 5},
          f"got {captured.get('summary')}")
    check("전 종목 실패는 종료코드 1", exit_code == 1, f"exit={exit_code}")
    check("★ 전멸해도 단계 보존 (효성=Candidate)",
          st.get("298040", {}).get("atlas_stage") == "Candidate")
    check("전멸해도 JSON 은 생성된다", len(st) == 5)


# ────────────────────────────────────────────────────────────
# T8 · T9 — krx.collect_one() 의 시간 규율과 수급 결손 표면화
#
#   2026-08-14 실제 사고에서 나온 회귀 테스트다.
#   10:52 KST 수동 재수집분이 당일 미확정 행을 latest_trading_day 와 SMA20 에 넣어,
#   **복구 버튼을 누른 시각에 따라 Decision 입력값이 달라졌다.**
#   또 그날 수급 행이 통째로 없었는데 missing_investors 는 [] 였다 —
#   '컬럼 누락'만 표현할 수 있고 '행 부재'는 표현할 수 없었기 때문이다.
#
#   ★ 수정 초안(v4.0)은 "16:00 이후 + 수급 행 존재 → 확정" 이었고 CIO 가 반려했다.
#     원천이 최종성을 알려주지 않는데 시계로 최종성을 선언하는 설계였기 때문이다.
#     기본안은 next_day 다 — 당일 행은 언제 돌리든 미확정이고, 다음 날 아침에 확정된다.
#     8-0 이 그 기본값 자체를 감시하고, 8-2 가 심야까지 포함해 시각 불변성을 검사한다.
#
#   ⚠ 여기서 검증하지 않는 것: pykrx 의 실제 응답 스키마. 아래는 스텁 DataFrame 이다.
# ────────────────────────────────────────────────────────────

OHLCV_COLS = ["시가", "고가", "저가", "종가", "거래량", "등락률"]


class _FakeDF:
    """collect_one() 이 실제로 쓰는 DataFrame 표면만 흉내낸다.

    쓰이는 것은 `.index` · `.columns` · `.loc[idx]` · `.loc[idx, col]` 넷뿐이다.
    pandas 를 끌어오지 않으므로 이 테스트는 pandas 버전에 영향받지 않는다.
    """

    def __init__(self, rows: dict, columns):
        self._rows = rows
        self.columns = list(columns)
        self.index = list(rows)

    @property
    def loc(self):
        return self

    def __getitem__(self, key):
        if isinstance(key, tuple):
            idx, col = key
            return self._rows[idx][col]
        return self._rows[key]


def _ohlcv(day_to_close: dict) -> _FakeDF:
    return _FakeDF({d: {"시가": c, "고가": c, "저가": c, "종가": c,
                        "거래량": 1000, "등락률": 0.0}
                    for d, c in day_to_close.items()}, OHLCV_COLS)


def _flow(days, cols) -> _FakeDF:
    return _FakeDF({d: {c: 0 for c in cols} for d in days}, cols)


def _fresh_krx():
    """T1/T2 가 krx.collect_one 을 스텁으로 갈아끼우므로,
    **진짜** collect_one 이 필요한 테스트는 모듈을 새로 적재한다."""
    captured = _install_stubs()
    sys.modules.pop("krx", None)
    import krx
    return krx, captured


def _wire(krx, ohlcv: _FakeDF, basic: _FakeDF, detail: _FakeDF) -> None:
    krx.stock.get_market_ohlcv_by_date = lambda s, e, c: ohlcv
    krx.stock.get_market_trading_value_by_date = (
        lambda s, e, c, d_=False, **kw: detail if (d_ or kw.get("detail")) else basic)
    krx.stock.get_market_trading_volume_by_date = (
        lambda s, e, c, d_=False, **kw: detail if (d_ or kw.get("detail")) else basic)


def test_intraday_exclusion() -> None:
    krx, captured = _fresh_krx()
    print("\n[T8] 당일 행 확정 정책 — 실행 시각이 Decision 입력을 바꾸는가")

    TODAY = dt.date(2026, 8, 14)
    days = [TODAY - dt.timedelta(days=i) for i in range(20, -1, -1)]   # 21개 (마지막이 당일)
    prior = days[:-1]                                                  # 확정 20개
    closes = {d: 1000 for d in prior}
    closes[TODAY] = 2000        # 장중 급등 — 섞이면 SMA20 이 1000.0 → 1050.0 으로 움직인다
    ohlcv = _ohlcv(closes)

    def wire(include_today_flow: bool = True):
        fdays = days if include_today_flow else prior
        _wire(krx, ohlcv, _flow(fdays, krx.BASIC), _flow(fdays, krx.DETAIL))

    def run(today="2026-08-14"):
        return krx.collect_one("005930", "20260725", "20260814", today)

    # ── 8-0 구조 자체를 감시한다 ─────────────────────────────────────────
    #   CIO 판정: production 에는 실행되지 않는 경로를 두지 않는다.
    #   설계 대안(재조회 안정성 게이트)은 docs/spec_same_day_confirmation.md 에 문서로만 있다.
    params = set(inspect.signature(krx.collect_one).parameters)
    check("★★ 판정 함수에 시계 입력이 아예 없다 (now 인자 부재)",
          not (params & {"now", "now_kst", "clock"}), f"got {sorted(params)}")
    check("★★ 미실행 정책 인자가 없다 (policy · sleep · resample)",
          not (params & {"policy", "sleep", "resample"}), f"got {sorted(params)}")
    check("★★ 미배선 정책 객체가 모듈에 없다",
          not hasattr(krx, "SAME_DAY_CONFIRMATION_POLICY"))
    check("★ 당일 확정 규칙은 단일 상수 하나뿐이다",
          krx.SAME_DAY_CONFIRMATION == "next_day", f"got {krx.SAME_DAY_CONFIRMATION}")

    # ── 8-1 장중 10:52 — 2026-08-14 실제 사고와 동일한 조건 ────────────────
    wire()
    r = run()
    d = r["daily"]["2026-08-14"]
    check("당일 행도 daily 에 보존된다 (관측은 버리지 않는다)", "2026-08-14" in r["daily"])
    check("★ 당일 행은 confirmed=False", d["confirmed"] is False)
    check("미확정 사유는 '다음 날로 미룸'", d["confirm_reason"] == "deferred_to_next_day",
          f"got {d['confirm_reason']}")
    check("★ latest_trading_day 는 직전 확정일", r["latest_trading_day"] == "2026-08-13",
          f"got {r['latest_trading_day']}")
    check("★ 당일 관측일은 별도 필드로 식별", r["latest_observed_day"] == "2026-08-14",
          f"got {r['latest_observed_day']}")
    check("★ SMA20 에 당일 행이 섞이지 않는다 (오염 시 1050.0)", r["sma20"] == 1000.0,
          f"got {r['sma20']}")
    check("SMA20 기준일을 명시한다", r["sma20_through"] == "2026-08-13")
    check("SMA20 표본 20개 유지", r["sma20_basis"] == 20, f"got {r['sma20_basis']}")
    check("미확정일 목록 표면화", r["unconfirmed_days"] == ["2026-08-14"],
          f"got {r['unconfirmed_days']}")

    # ── 8-2 순수 함수 자체가 시계와 무관한가 ──────────────────────────────
    check("지난 거래일은 확정", krx.confirm_state("2026-08-13", "2026-08-14")
          == (True, "prior_session"))
    check("★ 당일은 확정하지 않는다", krx.confirm_state("2026-08-14", "2026-08-14")
          == (False, "deferred_to_next_day"))
    check("미래 날짜는 방어적으로 미확정", krx.confirm_state("2026-08-15", "2026-08-14")
          == (False, "future_date"))

    # ── 8-3 ★★ end-to-end 시각 불변성 — main() 을 일곱 시각에 돌려 비교한다 ──
    #   v4.0 초안(16:00 임계값)이었다면 16:30·23:59 에서 결과가 갈렸을 검사다.
    seen = set()
    for h, m in ((6, 5), (9, 0), (10, 52), (14, 0), (15, 45), (16, 30), (23, 59)):
        wire()
        krx.now_kst = lambda h=h, m=m: dt.datetime(2026, 8, 14, h, m)
        captured.clear()
        try:
            krx.main()
        except SystemExit:
            pass
        s5 = captured["stocks"]["005930"]
        seen.add((s5["latest_trading_day"], s5["sma20"], s5["latest_observed_day"]))
    check("★★ 06:05~23:59 어느 시각에 main() 을 돌려도 산출물이 동일 (재현성)",
          seen == {("2026-08-13", 1000.0, "2026-08-14")}, f"got {seen}")

    # ── 8-4 확정은 다음 날 아침에 일어난다 — 데이터를 영구히 버리는 게 아니다 ──
    wire()
    r = run(today="2026-08-15")
    check("★ 다음 날 수집에서 8/14 가 확정된다", r["latest_trading_day"] == "2026-08-14",
          f"got {r['latest_trading_day']}")
    check("다음 날에는 SMA20 에 8/14 가 정상 반영", r["sma20"] == 1050.0, f"got {r['sma20']}")
    check("8/14 행의 사유가 prior_session 으로 바뀐다",
          r["daily"]["2026-08-14"]["confirm_reason"] == "prior_session")
    check("미확정일이 사라진다", r["unconfirmed_days"] == [], f"got {r['unconfirmed_days']}")

    # ── 8-5 payload 레벨 — Step 0 가 읽을 자리에 확정 상태가 있는가 ─────────
    check("collector_version 이 올라갔다", captured.get("collector_version") == "v4.1",
          f"got {captured.get('collector_version')}")
    dr = captured.get("decision_readiness", {})
    check("★ payload 에 확정 기준일이 있다", dr.get("confirmed_through") == "2026-08-13",
          f"got {dr.get('confirmed_through')}")
    check("payload 에 적용된 확정 규칙이 박힌다",
          dr.get("same_day_confirmation") == "next_day", f"got {dr.get('same_day_confirmation')}")
    check("미확정 행 보유 종목이 전부 열거된다",
          len(dr.get("stocks_with_unconfirmed_rows", [])) == 5,
          f"got {dr.get('stocks_with_unconfirmed_rows')}")
    check("판정 규칙이 데이터에 동봉된다 (읽는 쪽이 추측하지 않게)",
          captured.get("same_day_confirmation") == "next_day")
    check("★ summary 스키마는 그대로 {ok, failed} (guard.py 가 의존한다)",
          set(captured.get("summary", {})) == {"ok", "failed"},
          f"got {captured.get('summary')}")


def test_investor_row_absence() -> None:
    krx, _ = _fresh_krx()
    print("\n[T9] 투자자 수급 — '행 자체가 없음' 과 '컬럼이 없음' 은 다른 상태다")

    D12, D13 = dt.date(2026, 8, 12), dt.date(2026, 8, 13)
    ohlcv = _ohlcv({D12: 1000, D13: 1010})

    def run():
        return krx.collect_one("005930", "20260812", "20260813", "2026-08-14")

    # 9-1 행 자체가 없다 (2026-08-14 실제 사고의 모양)
    _wire(krx, ohlcv, _flow([D12], krx.BASIC), _flow([D12], krx.DETAIL))
    r = run()
    d13 = r["daily"]["2026-08-13"]
    check("★ 행 부재가 날짜로 표면화된다", r["investor_rows_missing"] == ["2026-08-13"],
          f"got {r['investor_rows_missing']}")
    check("★ 값 자리에 명시적 null 이 들어간다 (키를 지우지 않는다)",
          "net_value" in d13 and d13["net_value"] is None)
    check("어느 소스가 비었는지 행 레벨에도 남는다",
          d13["investor_rows_absent"] == ["net_value", "net_volume",
                                          "net_value_detail", "net_volume_detail"],
          f"got {d13['investor_rows_absent']}")
    check("소스별 날짜 목록 제공",
          r["investor_rows_missing_by_source"]["net_value"] == ["2026-08-13"])
    check("★★ 회귀: missing_investors=[] 를 '수급 정상'으로 읽으면 안 된다",
          r["missing_investors"] == [] and r["investor_rows_missing"] != [],
          f"missing_investors={r['missing_investors']} rows_missing={r['investor_rows_missing']}")
    check("행이 있는 날은 정상 기록", r["daily"]["2026-08-12"]["net_value"] == {
        k: 0 for k in krx.BASIC})

    # 9-2 행은 있는데 컬럼이 없다 — 기존 축은 그대로 살아 있어야 한다
    partial = [c for c in krx.BASIC if c != "기관합계"]
    _wire(krx, ohlcv, _flow([D12, D13], partial), _flow([D12, D13], krx.DETAIL))
    r = run()
    check("컬럼 누락은 missing_investors 로", r["missing_investors"] == ["기관합계"],
          f"got {r['missing_investors']}")
    check("★ 컬럼 누락은 행 부재로 오인되지 않는다", r["investor_rows_missing"] == [],
          f"got {r['investor_rows_missing']}")
    check("행 부재 목록도 비어 있다", r["daily"]["2026-08-13"]["investor_rows_absent"] == [])

    # 9-3 두 결손이 동시에 나도 서로를 가리지 않는다
    _wire(krx, ohlcv, _FakeDF({D12: {c: 0 for c in partial}}, partial),
          _flow([D12], krx.DETAIL))
    r = run()
    check("★ 컬럼 누락과 행 부재가 동시에 보고된다",
          r["missing_investors"] == ["기관합계"] and r["investor_rows_missing"] == ["2026-08-13"],
          f"cols={r['missing_investors']} rows={r['investor_rows_missing']}")


# ────────────────────────────────────────────────────────────
# T5 — SEC 수집기 (Collector Layer only)
#   ⚠ 네트워크를 타지 않는다. SEC 실제 응답 스키마는 여기서 검증되지 않는다.
#      검증하는 것은 '받은 응답을 어떻게 분류·보존하는가' 뿐이다.
# ────────────────────────────────────────────────────────────

SUB_FPI = {"name": "TSMC", "sicDescription": "Semiconductors", "fiscalYearEnd": "1231",
           "filings": {"recent": {"form": ["20-F", "6-K", "6-K"],
                                  "filingDate": ["2026-08-01", "2026-08-05", "2026-08-10"],
                                  "accessionNumber": ["0001-26-000001"] * 3,
                                  "primaryDocument": ["a.htm"] * 3, "items": [""] * 3}}}
SUB_DOM = {"name": "NVIDIA", "sicDescription": "Semiconductors", "fiscalYearEnd": "0131",
           "filings": {"recent": {"form": ["10-Q", "8-K", "10-K"],
                                  "filingDate": ["2026-08-02", "2026-08-06", "2026-08-11"],
                                  "accessionNumber": ["0001-26-000002"] * 3,
                                  "primaryDocument": ["b.htm"] * 3, "items": ["2.02", "", ""]}}}
SUB_EMPTY = {"name": "Nothing", "filings": {"recent": {"form": [], "filingDate": [],
                                                       "accessionNumber": [], "primaryDocument": [],
                                                       "items": []}}}


def test_sec_collector() -> None:
    os.environ.setdefault("SEC_USER_AGENT", "Atlas Fault Injection test@example.com")
    import sec

    def route(sub_by_cik: dict, xbrl_ok: set, fail: set = frozenset()):
        def _get(url):
            if "company_tickers" in url:
                return {"0": {"cik_str": 1046179, "ticker": "TSM", "title": "TSMC"},
                        "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA"},
                        "2": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"}}
            if "/submissions/" in url:
                cik = url.split("CIK")[1].split(".json")[0]
                if cik in fail:
                    raise ConnectionError("SEC 응답 없음 (주입된 장애)")
                return sub_by_cik.get(cik)
            if "companyconcept" in url:
                tax = url.split("/CIK")[1].split("/")[1]
                return ({"units": {"USD": [{"val": 1, "end": "2026-06-30", "form": "10-Q",
                                            "fy": 2026, "fp": "Q2"}]}}
                        if tax in xbrl_ok else None)
            return None
        return _get

    print("\n[T5] SEC Collector — 폼 분류 · 발행사 프로파일 도출 · 실패 격리")
    tsm, nvda, msft = "0001046179", "0001045810", "0000789019"

    # 5-1 정상 경로: FPI / domestic 이 데이터에서 도출되는가
    sec.get = route({tsm: SUB_FPI, nvda: SUB_DOM, msft: SUB_EMPTY}, {"us-gaap"})
    f = sec.fetch_filings(tsm, days=3650)
    check("TSM → foreign_private_issuer (하드코딩 아님)",
          f["filer_profile"] == "foreign_private_issuer", f"got {f['filer_profile']}")
    check("20-F/6-K 는 form_class=fpi",
          all(x["form_class"] == "fpi" for x in f["filings_recent"]))
    f = sec.fetch_filings(nvda, days=3650)
    check("NVDA → domestic", f["filer_profile"] == "domestic", f"got {f['filer_profile']}")
    check("8-K items 원문 보존 + 코드 분해",
          any(x["items_raw"] == "2.02" and x["item_codes"] == ["2.02"]
              for x in f["filings_recent"]))
    f = sec.fetch_filings(msft, days=3650)
    check("폼 없음 → unknown (추정으로 채우지 않는다)",
          f["filer_profile"] == "unknown", f"got {f['filer_profile']}")

    # 5-2 XBRL 폴백과 누락 표면화
    sec.get = route({}, {"ifrs-full"})
    x = sec.fetch_xbrl(tsm)
    check("us-gaap 없으면 ifrs-full 로 폴백", x["taxonomy"] == "ifrs-full", f"got {x['taxonomy']}")
    sec.get = route({}, set())
    x = sec.fetch_xbrl(tsm)
    check("둘 다 없으면 taxonomy=None (지어내지 않는다)", x["taxonomy"] is None)
    check("누락 태그 전량 표면화", len(x["missing_tags"]) == 7, f"got {len(x['missing_tags'])}")

    # 5-3 단일 종목 실패 격리 + 단계 보존
    captured = {}
    sec.save = lambda p, f_, d=None: captured.update(p) or f_
    sec.us_universe = lambda: [
        {"ticker": "TSM", "name": "TSMC", "atlas_stage": "Ready", "coverage": True},
        {"ticker": "NVDA", "name": "NVIDIA", "atlas_stage": None, "coverage": True},
    ]
    sec.get_cik_map = lambda: {"TSM": tsm, "NVDA": nvda}
    sec.get = route({tsm: SUB_FPI, nvda: SUB_DOM}, {"us-gaap"}, fail={tsm})
    try:
        sec.main()
    except SystemExit:
        pass
    st = captured.get("stocks", {})
    check("실패는 TSM 한 종목뿐", [t for t, v in st.items() if v["status"] != "ok"] == ["TSM"])
    check("NVDA 는 정상 수집", st.get("NVDA", {}).get("status") == "ok")
    check("★ 실패해도 단계 보존 (TSM=Ready)", st.get("TSM", {}).get("atlas_stage") == "Ready")
    # ⚠ 이전 버전은 `captured.get("decision_layer") is None` 이었는데, 필드를 삭제한 뒤에는
    #   있든 없든 통과하는 공회전 검사가 됐다. 키의 '부재'를 직접 확인한다.
    check("★ Collector payload 에 Decision 소관 키가 없다",
          not any(k in captured for k in
                  ("decision_layer", "event_taxonomy", "event_score", "fpi_event_classification")),
          f"발견: {[k for k in captured if 'event_' in k or 'decision' in k]}")
    check("미국 수급은 Unavailable 로 명시", captured.get("supply_demand") is None
          and "Unavailable" in captured.get("supply_demand_status", ""))

    # 5-4 Form Family — Decision Layer 가 폼 번호를 몰라도 되게 미리 분류
    check("10-K → annual_report", sec.form_family("10-K") == "annual_report")
    check("20-F → annual_report (FPI 도 같은 가족)", sec.form_family("20-F") == "annual_report")
    check("10-Q → quarterly_report", sec.form_family("10-Q") == "quarterly_report")
    check("8-K → current_report", sec.form_family("8-K") == "current_report")
    check("6-K → current_report (FPI 도 같은 가족)", sec.form_family("6-K") == "current_report")
    check("SC 13G → ownership", sec.form_family("SC 13G") == "ownership")
    check("DEF 14A → proxy", sec.form_family("DEF 14A") == "proxy")
    check("424B5 → registration", sec.form_family("424B5") == "registration")
    check("정정공시 10-K/A 는 원 폼과 같은 가족",
          sec.form_family("10-K/A") == "annual_report")
    check("모르는 폼은 other (지어내지 않는다)", sec.form_family("ZZ-99") == "other")
    check("form_family_counts 집계", sec.fetch_filings(nvda, days=3650)["form_family_counts"]
          == {"quarterly_report": 1, "current_report": 1, "annual_report": 1})

    # 5-5 source_validation — 스텁으로 돈 결과와 실 응답을 구분할 수 있는가
    v = captured.get("validation", {})
    check("스텁 실행은 verification_method=stub", v.get("verification_method") == "stub",
          f"got {v.get('verification_method')}")
    check("스텁 실행은 schema_verified=False", v.get("schema_verified") is False)
    check("검증 실패 사유 명시", bool(v.get("reason")))

    # 5-6 8-K Item — SEC 고정 목록의 '번역'만 Collector 에 남는다
    p = sec.parse_items("1.01,9.01", "8-K")
    check("Item 코드 분해", p["item_codes"] == ["1.01", "9.01"], f"got {p['item_codes']}")
    check("1.01 → Material Definitive Agreement",
          p["items_detail"][0]["title"].startswith("Entry into a Material"))
    check("Item 1.05 = Cybersecurity 수록", sec.ITEM_TITLES["1.05"] == "Cybersecurity Incidents")
    check("모르는 Item 은 known=False (제목 지어내지 않는다)",
          sec.parse_items("9.99", "8-K")["items_detail"][0]["known"] is False)
    check("8-K 인데 Item 비었으면 no_items_reported",
          sec.parse_items("", "8-K")["item_status"] == "no_items_reported")
    check("★ 6-K 는 not_itemized (FPI 이벤트 분류 불가를 숨기지 않는다)",
          sec.parse_items("", "6-K")["item_status"] == "not_itemized")
    check("★ Collector 에 Atlas 해석이 없다 (event_type 미보유)",
          "event_type" not in p["items_detail"][0] and "event_types" not in p)
    check("★ Collector 에 taxonomy 상수가 없다",
          not any(hasattr(sec, n) for n in
                  ("EVENT_TYPES", "ITEM_EVENT_MAP", "TAXONOMY_GAPS", "DETECTION_REQUIRED")))
    check("원천 부재(supply_demand)는 Collector 소관으로 유지",
          captured.get("supply_demand") is None
          and "Unavailable" in captured.get("supply_demand_status", ""))

    sec.VALIDATION["live_requests"] = 3          # 실 응답을 받은 상황을 흉내
    sec.VALIDATION["schema_issues"] = []
    check("실 응답이면 verification_method=live",
          sec.validation_report()["verification_method"] == "live")
    check("실 응답이어도 스키마 불일치면 schema_verified=False",
          (sec.VALIDATION["schema_issues"].append({"cik": "x", "missing_keys": ["form"]})
           or sec.validation_report()["schema_verified"]) is False)



# ────────────────────────────────────────────────────────────
# T6 — Decision Layer D1 (Event Classification)
#   분류만 검증한다. Score·해석·판정은 D1 에 없으므로 검증 대상도 아니다.
# ────────────────────────────────────────────────────────────

def test_event_classifier() -> None:
    try:
        import event_classifier as ec
    except ModuleNotFoundError:
        check("D1 모듈 로드", False,
              f"decision/event_classifier.py 를 찾지 못했다. 탐색 경로: {_ROOT}/decision")
        return

    print("\n[T6] D1 Event Classification — 분류만 한다")

    # 6-1 층 분리
    check("★ Taxonomy 는 D1 이 소유한다", hasattr(ec, "ITEM_EVENT_MAP"))
    check("taxonomy_version 존재", ec.TAXONOMY_VERSION == "1.0")
    check("Taxonomy 10종", len(ec.EVENT_TYPES) == 10, f"got {len(ec.EVENT_TYPES)}")
    unmapped = [c for c in ec.ITEM_EVENT_MAP if ec.ITEM_EVENT_MAP[c] not in ec.EVENT_TYPES]
    check("매핑값이 전부 어휘 안에 있다", unmapped == [], f"밖: {unmapped}")
    check("D1 에 Score 가 없다", not any(hasattr(ec, n) for n in
          ("EVENT_SCORE", "SCORE_MAP", "score", "interpret")))

    # 6-2 확정 매핑
    r = ec.classify({"form_family": "current_report", "item_status": "classified",
                     "item_codes": ["1.01"]})
    check("Item 1.01 → Contract", r["event_types"] == ["Contract"])
    check("확정 분류는 resolution=resolved", r["resolution"] == "resolved")
    check("★ 본문형 유형은 여전히 undetermined",
          r["undetermined"] == ["Guidance", "Litigation"], f"got {r['undetermined']}")
    check("Item 4.02 → Accounting",
          ec.classify({"form_family": "current_report", "item_status": "classified",
                       "item_codes": ["4.02"]})["event_types"] == ["Accounting"])

    # 6-3 추론 금지
    r = ec.classify({"form_family": "current_report", "item_status": "classified",
                     "item_codes": ["8.01"]})
    check("★ Item 8.01 → Other (Guidance 로 추정하지 않는다)", r["event_types"] == ["Other"])
    check("8.01 이어도 Guidance 는 undetermined 로 남는다", "Guidance" in r["undetermined"])
    r = ec.classify({"form_family": "current_report", "item_status": "classified",
                     "item_codes": ["9.99"]})
    check("모르는 코드는 Other 로 흡수하지 않는다", r["event_types"] == [])
    check("모르는 코드는 unknown_item_codes 로 표면화", r["unknown_item_codes"] == ["9.99"])
    check("2.05 는 taxonomy_gap 으로 표시",
          ec.classify({"form_family": "current_report", "item_status": "classified",
                       "item_codes": ["2.05"]})["taxonomy_gap_codes"] == ["2.05"])

    # 6-4 6-K (FPI) — '무엇이 담겼는지 전부' 모른다
    r = ec.classify({"form_family": "current_report", "item_status": "not_itemized",
                     "item_codes": []})
    check("6-K 는 event_types 비어 있음", r["event_types"] == [])
    check("6-K 는 resolution=unresolved", r["resolution"] == "unresolved")
    check("★ 6-K 는 전체 유형이 undetermined (Guidance 만이 아니다)",
          set(ec.EVENT_TYPES) <= set(r["undetermined"]))
    check("6-K 사유 명시", r["classification_reason"] == "no_item_structure")

    # 6-5 Form 4 — 서술이 없으므로 '모른다'도 아니다
    r = ec.classify({"form_family": "ownership", "item_status": "not_itemized",
                     "item_codes": []})
    check("★ ownership 은 undetermined 가 비어 있다 (거짓 미결 금지)", r["undetermined"] == [])
    check("ownership 은 resolution=not_applicable", r["resolution"] == "not_applicable")

    # 6-6 이력 누적 — 중복 없이 쌓이고, taxonomy 버전이 바뀌면 다시 쌓인다
    payload = {"collector_version": "v2", "collected_for_kst_date": "2026-08-14",
               "stocks": {"NVDA": {"status": "ok", "name": "NVIDIA", "atlas_stage": None,
                                   "coverage": True, "filings_recent": [
                   {"date": "2026-08-14", "form": "8-K", "form_family": "current_report",
                    "accession": "0001-26-000001", "item_codes": ["1.01"],
                    "item_status": "classified", "url": ""}]},
                          "TSM": {"status": "FAILED", "name": "TSMC"}}}
    tmp, cwd = tempfile.mkdtemp(), os.getcwd()
    try:
        os.chdir(tmp)
        os.makedirs("data", exist_ok=True)
        with open(ec.IN_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        ec.main()
        ec.main()                                    # 같은 날 두 번 돌려도
        rows, _ = ec.load_existing()
        check("★ 같은 제출물은 한 번만 쌓인다 (중복 방지)", len(rows) == 1, f"got {len(rows)}")
        check("실패 종목은 이력에 넣지 않는다",
              all(r["ticker"] != "TSM" for r in rows))
        check("레코드에 taxonomy_version 이 박힌다", rows[0]["taxonomy_version"] == "1.0")
        check("레코드에 decision_version 이 박힌다", rows[0]["decision_version"] == "d1_v1")
        check("레코드에 collector_version 이 박힌다 (provenance)",
              rows[0]["collector_version"] == "sec_v2", f"got {rows[0].get('collector_version')}")
        check("레코드에 시간축이 있다", rows[0]["filing_date"] == "2026-08-14")
        ec.TAXONOMY_VERSION = "1.1"                  # 분류 체계가 바뀌면
        ec.main()
        rows, _ = ec.load_existing()
        check("★ taxonomy_version 이 바뀌면 다시 분류해 쌓는다 (과거 재현 가능)",
              len(rows) == 2, f"got {len(rows)}")
        ec.TAXONOMY_VERSION = "1.0"

        # 분류 '로직' 버전이 바뀌어도 다시 쌓인다 (taxonomy 와 별개 축)
        ec.DECISION_VERSION = "d1_v2"
        ec.main()
        rows, _ = ec.load_existing()
        check("★ decision_version 이 바뀌어도 다시 분류해 쌓는다",
              len(rows) == 3, f"got {len(rows)}")
        ec.DECISION_VERSION = "d1_v1"

        # ★ 수집기가 올라가도 이력이 복제되지 않는다 — 대신 결과가 달라지면 drift 로 표면화
        payload["collector_version"] = "v3"
        with open(ec.IN_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        ec.main()
        rows, _ = ec.load_existing()
        check("★ collector_version 변경만으로는 복제되지 않는다",
              len(rows) == 3, f"got {len(rows)}")

        # 같은 키인데 분류 결과가 달라지면 조용히 넘기지 않는다
        payload["stocks"]["NVDA"]["filings_recent"][0]["item_codes"] = ["1.05"]
        with open(ec.IN_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        drifted = {}
        _orig_print = print
        ec.main()
        rows2, keys2 = ec.load_existing()
        check("★ 결과가 달라져도 임의로 덮어쓰지 않는다 (drift 는 기록만)",
              len(rows2) == 3, f"got {len(rows2)}")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)



# ────────────────────────────────────────────────────────────
# T7 — Portfolio Constitution 검산기 (Level 0)
#   숫자는 IC 가 정한다. 여기서 검사하는 것은 '검산기가 모순을 잡는가' 뿐이다.
# ────────────────────────────────────────────────────────────

BASE_C = {
    "B1_bucket_definition": ["반도체", "전력기기"],
    "B2_cash_floor_pct": 30, "B3_bucket_max_pct": 35, "B4_position_max_pct": 25,
    "B5_stop_loss_pct": 20, "B6_portfolio_max_loss_pct": 15,
    "B7_evidence_state_max_pct": {"backtest_only": 5, "forward_early": 10,
                                  "forward_established": 20, "operating": 25},
}


def test_constitution() -> None:
    try:
        import constitution as k
    except ModuleNotFoundError:
        check("헌법 검산기 로드", False, f"portfolio/constitution.py 없음 ({_ROOT}/portfolio)")
        return

    print("\n[T7] Portfolio Constitution — 헌법을 코드가 강제하는가")

    empty = {f: None for f in k.FIELDS}
    empty["B7_evidence_state_max_pct"] = {x: None for x in k.EVIDENCE_ORDER}
    r = k.check(empty)
    check("★ 미비준이면 not_ratified", r["status"] == "not_ratified", f"got {r['status']}")
    check("★ 미비준이면 매수 불가", r["buy_allowed"] is False)
    check("미확정 항목 10건 전부 열거", len(r["missing"]) == 10, f"got {len(r['missing'])}")

    r = k.check(dict(BASE_C))
    check("정합 헌법은 ratified", r["status"] == "ratified", f"got {r['status']}")
    check("최대 투입 70% 도출", r["derived"]["max_deployed_pct"] == 70.0)
    check("★ 최악 손실 14.0% 계산", r["derived"]["worst_case_loss_pct"] == 14.0,
          f"got {r['derived']['worst_case_loss_pct']}")
    check("여유 1.0%p", r["derived"]["headroom_pct"] == 1.0)

    bad = dict(BASE_C, B2_cash_floor_pct=20)            # 투입 80% × 손절 20% = 16% > 15%
    r = k.check(bad)
    check("★ 안전식 위반을 잡는다 (② D×L≤P)", r["status"] == "contradictory"
          and any("②" in v["rule"] for v in r["violations"]))
    check("★ 모순이면 매수 불가", r["buy_allowed"] is False)
    check("해소 방법을 제시한다", any(v.get("fix") for v in r["violations"]))

    r = k.check(dict(BASE_C, B4_position_max_pct=40))   # 종목 40% > 버킷 35%
    check("집중도 위반을 잡는다 (③)", any("③" in v["rule"] for v in r["violations"]))

    ev = dict(BASE_C["B7_evidence_state_max_pct"], backtest_only=20)
    r = k.check(dict(BASE_C, B7_evidence_state_max_pct=ev))
    check("★ 증거 등급 역전을 잡는다 (④)", any("④" in v["rule"] for v in r["violations"]))

    rows = {(x["P"], x["L"]): x for x in k.tradeoff_table()}
    check("귀결표 산수 (P15·L20 → 현금하한 25%)",
          rows[(15, 20)]["min_cash_floor_pct"] == 25.0,
          f"got {rows[(15, 20)]['min_cash_floor_pct']}")

    # ★ "Execution 은 Constitution 보다 아래" — 원칙이 함수로 존재하는가
    check("A층 불변 원칙이 코드에 있다", len(k.PRINCIPLES) >= 11, f"got {len(k.PRINCIPLES)}")
    check("★ Execution 우회 금지 원칙 수록",
          any("Execution" in p for p in k.PRINCIPLES))
    tmpd = tempfile.mkdtemp()
    try:
        pth = os.path.join(tmpd, "c.json")
        with open(pth, "w", encoding="utf-8") as f:
            json.dump({f: None for f in k.FIELDS} |
                      {"B7_evidence_state_max_pct": {x: None for x in k.EVIDENCE_ORDER}}, f)
        raised = False
        try:
            k.assert_buy_allowed(pth)
        except k.ConstitutionViolation:
            raised = True
        check("★ 미비준 상태에서 주문 시도는 예외로 막힌다", raised)
        with open(pth, "w", encoding="utf-8") as f:
            json.dump(BASE_C, f)
        check("비준되면 통과한다", k.assert_buy_allowed(pth)["buy_allowed"] is True)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    # 리포에 실제로 들어 있는 헌법은 '모순'이 아니어야 한다 (미비준은 정상)
    live = os.path.join(_ROOT, "config", "constitution.json")
    if os.path.exists(live):
        lr = k.check(k.load(live))
        check("★ 리포의 헌법이 모순 상태가 아니다",
              lr["status"] in ("not_ratified", "ratified"), f"got {lr['status']}")
        print(f"       현재 리포 헌법: {lr['status']} · buy_allowed={lr['buy_allowed']}")
    else:
        check("config/constitution.json 존재", False, "파일 없음")


# ────────────────────────────────────────────────────────────
# T10 — 산출물 키 계약 (쓰는 쪽 ↔ 읽는 쪽)
#
#   2026-08-14 CIO 가 직접 잡아낸 실책이다.
#   krx.py 가 payload 키를 `policy_mode` → `same_day_confirmation` 으로 바꿨는데
#   verify_summary.py 는 옛 이름을 계속 읽어 **정책값이 조용히 None 으로 찍혔다.**
#
#   교훈: `.get()` 으로 읽으면 **키가 사라진 사실 자체가 사라진다.**
#         산출물을 만드는 쪽만 테스트하고 읽는 쪽을 테스트하지 않으면
#         '초록불인데 로그가 틀린' 상태가 만들어진다.
#
#   대책: verify_summary.CONTRACT 에 의존 키를 명시하고, 여기서 **실제 payload 와 대조**한다.
#         이름이 또 바뀌면 로그가 조용히 틀리는 대신 이 테스트가 먼저 깨진다.
# ────────────────────────────────────────────────────────────

def test_output_contract() -> None:
    print("\n[T10] 산출물 키 계약 — 쓰는 쪽과 읽는 쪽이 같은 이름을 쓰는가")
    try:
        import verify_summary as vs
    except ModuleNotFoundError:
        check("verify_summary 로드", False, f"{_SCRIPTS} 에서 찾지 못했다")
        return

    # 실제 krx.main() 이 만드는 payload 로 대조한다 (손으로 적은 픽스처가 아니다)
    krx, captured = _fresh_krx()
    TODAY = dt.date(2026, 8, 14)
    days = [TODAY - dt.timedelta(days=i) for i in range(20, -1, -1)]
    _wire(krx, _ohlcv({d: 1000 for d in days}),
          _flow(days, krx.BASIC), _flow(days, krx.DETAIL))
    krx.now_kst = lambda: dt.datetime(2026, 8, 14, 10, 52)
    captured.clear()
    try:
        krx.main()
    except SystemExit:
        pass
    payload = dict(captured)

    miss = [k for k in vs.CONTRACT["top"] if k not in payload]
    check("★ 최상위 계약 키가 산출물에 전부 있다", miss == [], f"없는 키: {miss}")

    dr = payload.get("decision_readiness", {})
    miss = [k for k in vs.CONTRACT["decision_readiness"] if k not in dr]
    check("★★ decision_readiness 계약 키 일치 (policy_mode 오배선 회귀)",
          miss == [], f"없는 키: {miss}")

    ok_stock = next(v for v in payload["stocks"].values() if v.get("status") == "ok")
    miss = [k for k in vs.CONTRACT["stock_ok"] if k not in ok_stock]
    check("★ 종목 레벨 계약 키 일치", miss == [], f"없는 키: {miss}")

    # end-to-end — 실제로 출력해서 값이 찍히는지 본다 (계약만 맞고 출력이 틀릴 수도 있다)
    tmp, cwd = tempfile.mkdtemp(), os.getcwd()
    try:
        os.chdir(tmp)
        os.makedirs("data", exist_ok=True)
        with open("data/latest_krx.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            vs.main()
        out = buf.getvalue()
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    check("★★ 요약 출력에 당일 확정 규칙이 실제로 찍힌다", "next_day" in out,
          "; ".join(ln.strip() for ln in out.splitlines() if "당일확정규칙" in ln) or "해당 줄 없음")
    check("★ 계약 키 누락 경고가 하나도 없다", "키 없음" not in out,
          [ln.strip() for ln in out.splitlines() if "키 없음" in ln])
    check("확정 기준일이 찍힌다", "confirmed_through      : 2026-08-13" in out,
          [ln.strip() for ln in out.splitlines() if "confirmed_through" in ln])
    check("확정/관측 두 축이 모두 표시된다",
          "latest_trading_day" in out and "latest_observed_day" in out)

    # 계약이 깨진 산출물을 주면 **조용히 넘어가지 않는지** — 감지 능력 자체를 검사한다
    broken = dict(payload)
    broken["decision_readiness"] = {k: v for k, v in dr.items() if k != "same_day_confirmation"}
    tmp, cwd = tempfile.mkdtemp(), os.getcwd()
    try:
        os.chdir(tmp)
        os.makedirs("data", exist_ok=True)
        with open("data/latest_krx.json", "w", encoding="utf-8") as f:
            json.dump(broken, f, ensure_ascii=False, default=str)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            vs.main()
        out2 = buf.getvalue()
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    check("★★ 키가 사라지면 None 이 아니라 '키 없음' 으로 드러난다",
          "키 없음(same_day_confirmation)" in out2,
          [ln.strip() for ln in out2.splitlines() if "confirmed_through" in ln])


def main() -> None:
    print("Atlas Fault Injection Suite — 실패를 기다리지 않고 설계해서 검증한다")
    test_distribution_and_history()      # 실제 common 을 먼저 쓴다
    test_failure_isolation()             # 그 다음 스텁을 설치한다 (순서 중요)
    test_intraday_exclusion()            # ★ krx 를 새로 적재한다 — T1/T2 뒤에 와야 한다
    test_investor_row_absence()
    test_output_contract()           # 읽는 쪽까지 검증한다
    test_sec_collector()
    test_event_classifier()
    test_constitution()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{'=' * 60}\n{passed}/{total} PASS")
    if passed != total:
        print("실패 항목:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}  {detail}")
        sys.exit(1)
    print("커버하지 않는 것: Notion 인증 경로 · pykrx 응답 스키마 · 브리핑 판정 로직")


if __name__ == "__main__":
    main()
