"""TSMC Monthly Revenue collector — 회귀 (CIO 승인 2026-08-15 pilot).

★ 네트워크를 타지 않는다. 실제 IR 표를 한 번 관측해 fixture 로 고정했고
  회귀는 그 fixture 만 읽는다 — Actions 에서도 결정론적으로 돈다.

★ 이 회귀가 지키는 계약
    ① Decision 입력은 **TSMC 공표값**뿐이다. 재계산 YoY 는 판정에 쓰지 않는다.
    ② 공표 정밀도를 그대로 쓴다 — 추가 반올림·자릿수 확장 금지.
    ③ `target_month` 와 `published_at` 은 분리된다. 발표일 하드코딩 금지.
    ④ 결측월은 연속으로 세지 않는다.
    ⑤ revision 은 silent overwrite 되지 않고 탐지된다.
    ⑥ collector 는 Rule threshold 를 알지 못한다.
    ⑦ collector 가 존재한다는 이유만으로 Rule 상태가 바뀌지 않는다.
"""
from __future__ import annotations

import copy
import json
import re
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
import tsmc_monthly as T                                             # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def expect_raise(name, fn, needle=""):
    try:
        fn()
    except T.SourceUnavailable as e:
        check(name, needle in str(e), f"{e}")
    except Exception as e:                                   # noqa: BLE001
        check(name, False, f"다른 예외: {type(e).__name__}: {e}")
    else:
        check(name, False, "예외가 나지 않았다")


RAW = open(T.FIXTURE, encoding="utf-8").read()
N = T.from_fixture(published_at="2026-08-15")
RI = T.rule_inputs(N)

print("T-0 실제 IR 스냅샷을 그대로 읽는다")
check("연도 2026", N["year"] == 2026)
check("관측 월 7건 (Jan~Jul)", len(N["months"]) == 7, str(len(N["months"])))
check("미발표월 5건은 실리지 않는다",
      not ({"2026-08", "2026-09", "2026-10", "2026-11", "2026-12"} & set(N["months"])))
check("primary source 가 IR 로 고정돼 있다",
      N["source_url"] == "https://investor.tsmc.com/english/monthly-revenue")
check("보도자료는 secondary verification only",
      N["secondary_verification_only"]["role"] == "secondary verification only"
      and "자동 승격 금지" in N["secondary_verification_only"]["note"])

print("T-1 ① Decision 입력은 공표값뿐")
check("최신 단월 YoY 가 공표 문자열 그대로",
      N["months"]["2026-07"]["monthly_yoy_pct_published"] == "44.7")
check("누계 YoY 가 공표 Total 그대로",
      N["cumulative"]["cumulative_yoy_pct_published"] == "37.0")
check("판정 입력 계약이 명시돼 있다", "판정 입력이다" in N["decision_input_contract"])
check("재계산 누계 YoY 를 만들지 않았다",
      not any("yoy" in k.lower() for k in N["validation"]))
check("매출 합계 검증은 남긴다 — 그리고 실제로 일치한다",
      N["validation"]["cumulative_revenue_matches"] is True)
check("합계가 공표 Total 과 같다",
      Decimal(N["validation"]["calculated_cumulative_revenue_ntd_mn"])
      == Decimal("2872064"))

print("T-2 ② 공표 정밀도를 바꾸지 않는다")
for k, v in N["months"].items():
    check(f"{k} YoY 가 문자열로 보존된다", isinstance(v["monthly_yoy_pct_published"], str))
check("자릿수를 확장하지 않았다 — 소수 1자리 그대로",
      all(len(v["monthly_yoy_pct_published"].split(".")[-1]) == 1
          for v in N["months"].values()))
check("float 왕복 흔적이 없다",
      "37.0" == N["cumulative"]["cumulative_yoy_pct_published"])
check("십진 비교가 가능하다 — 경계 근처에서 흔들리지 않는다",
      Decimal("34.6") >= Decimal("34.6") and not (Decimal("34.5") >= Decimal("34.6")))

print("T-3 ③ target_month 와 published_at 분리")
check("published_at 이 관측값으로 실린다", N["published_at"] == "2026-08-15")
check("target_month 가 각 행에 있다",
      all(v["target_month"] == k for k, v in N["months"].items()))
check("발표일이 대상월과 다르다 — 익월 10일 하드코딩 아님",
      N["published_at"][:7] != N["cumulative"]["through_month"])
# ★ 관측과 판정 준비의 분리 (CIO 확정) — 발표일이 없어도 월매출 관측은 살린다.
_noday = T.from_fixture()
check("발표일이 없어도 월매출 관측은 성공한다",
      len(_noday["months"]) == 7
      and _noday["months"]["2026-07"]["monthly_yoy_pct_published"] == "44.7"
      and _noday["cumulative"]["cumulative_yoy_pct_published"] == "37.0")
check("그 경우 published_at 은 모른다고 표시된다",
      _noday["published_at"] is None
      and _noday["published_at_status"] == "unobserved")
check("★ 그러나 decision_ready 는 열리지 않는다",
      _noday["decision_ready"] is False
      and _noday["decision_ready_blockers"] == ["published_at_unobserved"])
check("발표일을 추정해 채우지 않는다고 명시한다",
      "추정해 채우지 않는다" in _noday["decision_ready_note"])
check("발표일이 관측되면 decision_ready 가 열린다",
      N["decision_ready"] is True and N["decision_ready_blockers"] == [])
expect_raise("발표일 형식이 틀리면 거부한다",
             lambda: T.normalize(T.parse_table(RAW), "2026/08/15"), "형식")
_late = T.from_fixture(published_at="2026-08-13")
check("연기 발표일도 그대로 받는다 (일자 계산 없음)",
      _late["published_at"] == "2026-08-13"
      and _late["months"] == N["months"])
check("당해연도는 unaudited 로 보존된다", N["audited"] is False)
check("source_status 가 official_published", N["source_status"] == "official_published")

print("T-4 ④ 결측월은 연속으로 세지 않는다")
check("현재는 1~7월이 한 구간", T.consecutive_runs(N["months"])
      == [["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]])
_gap = copy.deepcopy(N["months"])
del _gap["2026-04"]
check("가운데가 비면 두 구간으로 갈라진다",
      T.consecutive_runs(_gap) == [["2026-01", "2026-02", "2026-03"],
                                   ["2026-05", "2026-06", "2026-07"]])
check("연속 2개월이 결측을 건너뛰어 성립하지 않는다",
      all(len(r) < 7 for r in T.consecutive_runs(_gap)))
_solo = {"2026-01": N["months"]["2026-01"], "2026-07": N["months"]["2026-07"]}
check("떨어진 두 달은 각각 길이 1", T.consecutive_runs(_solo)
      == [["2026-01"], ["2026-07"]])

print("T-5 ⑤ revision 탐지 — silent overwrite 금지")
check("변화가 없으면 revision 0", T.detect_revisions(N, N) == [])
_rev = copy.deepcopy(N)
_rev["published_at"] = "2026-09-10"
_rev["months"]["2026-07"]["monthly_yoy_pct_published"] = "44.5"
r = T.detect_revisions(N, _rev)
check("공표 YoY 정정이 탐지된다", len(r) == 1 and r[0]["target_month"] == "2026-07",
      str(r))
check("이전 값과 새 값을 둘 다 남긴다",
      r and r[0]["from"] == "44.7" and r[0]["to"] == "44.5")
check("어느 발표에서 바뀌었는지도 남긴다",
      r and r[0]["prev_published_at"] == "2026-08-15"
      and r[0]["new_published_at"] == "2026-09-10")
_rev2 = copy.deepcopy(N)
_rev2["months"]["2026-03"]["net_revenue_ntd_mn"] = "415192"
check("매출 정정도 탐지된다",
      [x["field"] for x in T.detect_revisions(N, _rev2)] == ["net_revenue_ntd_mn"])
_rev3 = copy.deepcopy(N)
_rev3["cumulative"]["cumulative_yoy_pct_published"] = "36.9"
check("누계 YoY 정정도 탐지된다 (같은 through_month 일 때)",
      any(x["field"].startswith("cumulative.")
          for x in T.detect_revisions(N, _rev3)))
check("첫 수집(이전 스냅샷 없음)은 revision 0", T.detect_revisions(None, N) == [])

print("T-6 ⑥ collector 는 Rule threshold 를 모른다")
# ★ 문자열 검색으로는 산문과 로직을 구별할 수 없다 (주석에도 40/35/34.6 이 적혀 있다).
#   그래서 AST 를 훑어 **임계값과의 비교 연산**이 실제로 있는지 본다.
import ast                                                          # noqa: E402
src = open(os.path.join(ROOT, "collectors", "tsmc_monthly.py"), encoding="utf-8").read()
_tree = ast.parse(src)
_THRESH = {Decimal("34.6"), Decimal("35"), Decimal("40")}


def _const_vals(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float, str)):
            try:
                out.append(Decimal(str(n.value)))
            except Exception:                                        # noqa: BLE001
                pass
    return out


_cmp_hits = []
for n in ast.walk(_tree):
    if isinstance(n, ast.Compare):
        vals = _const_vals(n.left) + [v for c in n.comparators for v in _const_vals(c)]
        _cmp_hits += [v for v in vals if v in _THRESH]
check("임계값과의 비교 연산이 코드에 하나도 없다", _cmp_hits == [], str(_cmp_hits))
_lits = []
for n in ast.walk(_tree):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
            and not isinstance(n.value, bool):
        try:
            v = Decimal(str(n.value))
        except Exception:                                            # noqa: BLE001
            continue
        if v in _THRESH:
            _lits.append(str(n.value))
check("임계값이 숫자 리터럴로도 존재하지 않는다", _lits == [], str(_lits))
check("rule_inputs 가 판정하지 않는다고 명시한다",
      "임계값 비교는 Rule 평가 층의 일" in RI["threshold_note"])
check("0003 입력 = 단월 YoY + 연속 구간",
      set(RI["RULE-0003"]) >= {"monthly_yoy_pct_published", "consecutive_runs"})
check("0007 · 0008 입력 = 단월 + 누계 공표 YoY",
      RI["RULE-0007"]["cumulative_yoy_pct_published"] == "37.0"
      and RI["RULE-0008"]["monthly_yoy_pct_published"] == "44.7")
check("0007 과 0008 은 같은 관측을 본다",
      RI["RULE-0007"]["cumulative_yoy_pct_published"]
      == RI["RULE-0008"]["cumulative_yoy_pct_published"])
check("판정·성립 여부를 뜻하는 키가 없다",
      not any(k in json.dumps(RI, ensure_ascii=False)
              for k in ("\"verdict\"", "\"triggered\"", "\"pass\"", "\"weak\"")))

print("T-7 fail-closed — 원천을 신뢰할 수 없으면 산출물을 만들지 않는다")
expect_raise("Total 행이 없으면 거부", lambda: T.parse_table(
    "# year=2026\nMonth\tConsolidated Net Revenue\tYoY Change\nJan.\t1\t1.0%"), "Total")
expect_raise("연도 표기가 없으면 거부", lambda: T.parse_table(
    "Month\tConsolidated Net Revenue\tYoY Change\n" + "\n".join(
        f"{m}\t1\t1.0%" for m in T.MONTHS) + "\nTotal\t12\t1.0%"), "연도")
expect_raise("월 행이 12개가 아니면 거부", lambda: T.parse_table(
    "# year=2026\nJan.\t1\t1.0%\nTotal\t1\t1.0%"), "12개")
expect_raise("숫자가 아닌 매출은 거부", lambda: T.parse_table(
    RAW.replace("401,255", "n/a")), "수치")
expect_raise("퍼센트 형식이 아니면 거부", lambda: T.parse_table(
    RAW.replace("36.8%", "36.8")), "퍼센트")
expect_raise("알 수 없는 행 라벨은 거부", lambda: T.parse_table(
    RAW.replace("Total\t", "Subtotal\t")), "라벨")
expect_raise("매출만 있고 YoY 가 없는 부분 관측은 거부",
             lambda: T.normalize(T.parse_table(
                 RAW.replace("Jan.\t401,255\t36.8%", "Jan.\t401,255\t")),
                 "2026-08-15"), "부분 관측")

print("T-8 ⑦ 이 collector(C1) 는 Rule 상태의 근거가 아니다")
# ★ 상태 변경 근거는 C4(SEC EDGAR)의 live run 2회다 (CIO 판정 2026-08-15).
#   C1 TSMC IR 은 secondary verification source 로 남았고, 이 파일은 그 파서 회귀다.
#   ⛔ 따라서 여기서 확인할 것은 "이 collector 가 상태를 만들지 않았다" 는 사실이다.
RJ = json.load(open(os.path.join(ROOT, "config", "rules.json"), encoding="utf-8"))
by = {r["rule_id"]: r for r in RJ["rules"]}
for rid in ("RULE-0003", "RULE-0007", "RULE-0008"):
    r = by[rid]
    a = r.get("data_capability_application")
    check(f"{rid} 상태 변경에는 적용 기록이 있다", bool(a))
    check(f"{rid} 근거가 C1 이 아니라 SEC EDGAR 다",
          "SEC EDGAR" in a["acquisition_contract"]["primary_acquisition"]
          and "investor.tsmc.com" in a["acquisition_contract"]["secondary_verification"])
    check(f"{rid} 적용 전 값이 MISSING · SOURCE_UNRESOLVED 로 보존된다",
          r["data_status_before_application"] == "MISSING"
          and r["source_qualification_before_application"] == "SOURCE_UNRESOLVED")
    check(f"{rid} legacy UNRESOLVED 필드는 그대로",
          r["data_capability"] == "UNRESOLVED"
          and r["condition_semantics"] == "UNRESOLVED" and r["scope"] == "UNRESOLVED")
check("rules.json 은 여전히 소비 불가", RJ["consumable_by_evaluator"] is False)
# ⛔ 이 회귀는 TSMC 3건을 검증한다. 전역 READY 총계는 다른 Rule 의 승인으로도
#    바뀌므로 여기에 박지 않는다 — 박으면 무관한 승인에서 이 파일이 깨진다.
check("Rule 25 · TSMC 3건이 READY 다",
      RJ["rule_count"] == 25
      and all(by[r]["evaluator_status"] == "READY"
              for r in ("RULE-0003", "RULE-0007", "RULE-0008")),
      str(RJ["state_counts"]["evaluator_ready"]))
check("★ READY 여도 Production HOLD 는 유지된다", "HOLD" in RJ["production_state"])
check("collector 는 authoritative artifact 를 쓰지 않는다",
      not os.path.exists(os.path.join(ROOT, "data", "latest_tsmc_monthly.json")))
check("산출물이 관측임을 명시한다", "관측이다" in N["observation_only"])


print("T-10 Official Revenue HTML Extraction — URL 계약")
check("연도는 경로에 들어간다",
      T.monthly_revenue_url(2026)
      == "https://investor.tsmc.com/english/monthly-revenue/2026")
check("?year= 방식을 쓰지 않는다", "?year=" not in T.MONTHLY_URL_TEMPLATE)
for bad in (2025.0, "2026", None, 1998, 2101):
    expect_raise(f"비정상 연도 {bad!r} 거부", lambda b=bad: T.monthly_revenue_url(b), "연도")
check("User-Agent · timeout 이 명시돼 있다",
      "@" in T.FETCH_USER_AGENT and isinstance(T.FETCH_TIMEOUT_SEC, int))

print("T-11 HTML fixture 추출 — 네트워크 없이")
H = T.from_html_fixture()
check("관측 월 7건", len(H["months"]) == 7, str(len(H["months"])))
check("빈 미래월을 observation 으로 만들지 않는다",
      not ({"2026-08", "2026-09", "2026-10", "2026-11", "2026-12"} & set(H["months"])))
check("&nbsp; 빈 칸도 미발표로 처리된다", "2026-08" not in H["months"])
check("Total 은 월 행과 별도 구조",
      H["cumulative"]["cumulative_yoy_pct_published"] == "37.0"
      and "Total" not in H["months"])
check("published_at 분리 유지 — 관측 성공 · decision_ready 는 닫힘",
      H["published_at"] is None and H["decision_ready"] is False
      and H["decision_ready_blockers"] == ["published_at_unobserved"])

print("T-12 differential — TSV parser 와 HTML extractor 가 같은 관측을 낸다")
check("months 동일", H["months"] == N["months"], "다름")
check("cumulative 동일", H["cumulative"] == N["cumulative"], "다름")
_hn = T.from_html_fixture(published_at="2026-08-15")
check("발표일까지 주면 payload 전체가 동일",
      json.dumps(_hn, ensure_ascii=False, sort_keys=True)
      == json.dumps(N, ensure_ascii=False, sort_keys=True))
check("rule_inputs 도 동일",
      json.dumps(T.rule_inputs(_hn), ensure_ascii=False, sort_keys=True)
      == json.dumps(RI, ensure_ascii=False, sort_keys=True))

print("T-13 HTML 구조 변경 fault injection — 전부 fail-closed")
HRAW = open(T.HTML_FIXTURE, encoding="utf-8").read()


def _x(html, year=2026):
    return lambda: T.extract_from_html(html, year)


expect_raise("요청 연도와 heading 연도가 다르면 거부",
             lambda: T.extract_from_html(HRAW, 2025), "heading 연도")
expect_raise("heading 이 없으면 거부",
             _x(HRAW.replace("2026 Monthly Revenue", "Monthly Revenue")), "heading")
expect_raise("컬럼이 바뀌면 거부",
             _x(HRAW.replace("<th>YoY Change</th>", "<th>YoY</th>")), "컬럼")
expect_raise("컬럼이 빠지면 거부",
             _x(HRAW.replace("<th>Consolidated Net Revenue</th>", "")), "컬럼")
expect_raise("월 행이 중복되면 거부",
             _x(HRAW.replace("<tr><td>Feb.</td><td>317,657</td><td>22.2%</td></tr>",
                             "<tr><td>Feb.</td><td>317,657</td><td>22.2%</td></tr>"
                             "<tr><td>Feb.</td><td>317,657</td><td>22.2%</td></tr>")),
             "중복")
expect_raise("Total 이 없으면 거부",
             _x(HRAW.replace("<tr><td>Total</td><td>2,872,064</td><td>37.0%</td></tr>",
                             "")), "Total 행이 없다")
expect_raise("Total 이 둘이면 거부",
             _x(HRAW.replace("<tr><td>Total</td><td>2,872,064</td><td>37.0%</td></tr>",
                             "<tr><td>Total</td><td>2,872,064</td><td>37.0%</td></tr>"
                             "<tr><td>Total</td><td>2,872,064</td><td>37.0%</td></tr>")),
             "둘 이상")
expect_raise("월 행이 12개가 아니면 거부",
             _x(HRAW.replace("<tr><td>Dec.</td><td></td><td></td></tr>", "")), "12개")
expect_raise("★ 예상 외 populated month 배열은 거부",
             _x(HRAW.replace("<tr><td>Dec.</td><td></td><td></td></tr>",
                             "<tr><td>Dec.</td><td>1,000</td><td>1.0%</td></tr>")),
             "populated month")
expect_raise("알 수 없는 행 라벨은 거부",
             _x(HRAW.replace("<td>Total</td>", "<td>Subtotal</td>")), "라벨")
expect_raise("표가 아예 없으면 거부",
             _x(re.sub(r"<table.*?</table>", "", HRAW, flags=re.S)), "표가 없다")

print("T-14 마크업 변형에 취약하지 않다")
_wrapped = HRAW.replace("<td>467,580</td>", "<td><span> 467,580 </span></td>") \
               .replace("<td>44.7%</td>", "<td><b>44.7%</b>&nbsp;</td>") \
               .replace("<td>Jul.</td>", "<td>Jul.*</td>")
_w = T.normalize(T.extract_from_html(_wrapped, 2026), published_at="2026-08-15")
check("중첩 태그 · nbsp · 각주 표식이 있어도 같은 값을 낸다",
      _w["months"]["2026-07"] == N["months"]["2026-07"])
_reordered = HRAW.replace(
    "<tr><td>Jan.</td><td>401,255</td><td>36.8%</td></tr>\n      "
    "<tr><td>Feb.</td><td>317,657</td><td>22.2%</td></tr>",
    "<tr><td>Feb.</td><td>317,657</td><td>22.2%</td></tr>\n      "
    "<tr><td>Jan.</td><td>401,255</td><td>36.8%</td></tr>")
check("행 표시 순서가 바뀌어도 라벨로 읽는다",
      T.normalize(T.extract_from_html(_reordered, 2026))["months"] == H["months"])

print("T-15 live fetch 는 회귀에서 호출하지 않는다")
_MODSRC = open(T.__file__, encoding="utf-8").read()
_before, _after = _MODSRC.split("def fetch_live")
check("네트워크 import 가 fetch_live 안에만 있다", "urllib" not in _before)
check("fetch_live 는 fixture 로 대체하지 않는다", "FIXTURE" not in _after)
_SELF = open(__file__, encoding="utf-8").read()
# ★ needle 을 조립해 만든다 — 리터럴로 쓰면 이 파일 자신이 걸려 자기모순이 된다.
_needle = "T." + "fetch" + "_live("
check("회귀 자신이 live fetch 를 호출하지 않는다", _needle not in _SELF)

print("T-9 재현성")
check("같은 입력이면 두 번 읽어도 같다",
      json.dumps(T.from_fixture(published_at="2026-08-15"),
                 ensure_ascii=False, sort_keys=True)
      == json.dumps(N, ensure_ascii=False, sort_keys=True))
check("발표일 유무만 payload 를 가른다 — 관측 내용은 동일",
      T.from_fixture()["months"] == N["months"]
      and T.from_fixture()["cumulative"] == N["cumulative"])

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
