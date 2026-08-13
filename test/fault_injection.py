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
  · 브리핑 세션의 판정 로직 — 여기 없다
  검증되지 않은 것을 검증된 것처럼 두지 않기 위해 여기 명시한다.
"""
import os
import sys
import json
import types
import shutil
import tempfile
import datetime as dt

os.environ.setdefault("KRX_ID", "faultinjection")
os.environ.setdefault("KRX_PW", "faultinjection")

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
    cm.load_universe = lambda: KR
    cm.today_kst = lambda: dt.date(2026, 8, 14)
    cm.now_utc_iso = lambda: "2026-08-13T21:00:00+00:00"
    cm.record_stage_snapshot = lambda u, d=None: "data/stage_history.json"
    cm.stage_distribution = lambda u, d=None: {"coverage_total": 11, "stage_assigned": 3, "buy": 0}
    cm.save = lambda payload, filename, date=None: captured.update(payload) or filename
    sys.modules["common"] = cm
    return captured


def _run(krx, failing: set) -> tuple:
    def flaky(code, start, end):
        if code in failing:
            raise ConnectionError("KRX 응답 없음 (주입된 장애)")
        return {"daily": {"2026-08-14": {"close": 1}}, "latest_trading_day": "2026-08-14",
                "sma20": None, "sma20_basis": 1, "missing_investors": []}
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


def main() -> None:
    print("Atlas Fault Injection Suite — 실패를 기다리지 않고 설계해서 검증한다")
    test_distribution_and_history()      # 실제 common 을 먼저 쓴다
    test_failure_isolation()             # 그 다음 스텁을 설치한다 (순서 중요)

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
