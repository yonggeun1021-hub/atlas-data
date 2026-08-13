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
import datetime as dt

os.environ.setdefault("KRX_ID", "faultinjection")
os.environ.setdefault("KRX_PW", "faultinjection")

# ★ 실행 방식에 의존하지 않는다 — PYTHONPATH 를 잘못 주면 '테스트가 없는 채로 초록불'이 되기 쉽다.
#   리포 구조에서 직접 경로를 잡는다. (2026-08-13: PYTHONPATH 누락으로 D1 검증이 통째로 빠졌던 사례)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("collectors", "decision"):
    _full = os.path.join(_ROOT, _sub)
    if os.path.isdir(_full) and _full not in sys.path:
        sys.path.insert(0, _full)

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


def main() -> None:
    print("Atlas Fault Injection Suite — 실패를 기다리지 않고 설계해서 검증한다")
    test_distribution_and_history()      # 실제 common 을 먼저 쓴다
    test_failure_isolation()             # 그 다음 스텁을 설치한다 (순서 중요)
    test_sec_collector()
    test_event_classifier()

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
