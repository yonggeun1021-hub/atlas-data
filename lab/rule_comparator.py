"""Entry Validation Lab — KR Rule Comparator v1

  ★★  규칙을 찾는 것이 아니라, 재료를 살아남게 하거나 기각하는 것이다.  ★★
                                                        (CIO 확정 2026-08-13)

이 도구는 '최고 규칙을 뽑는 기계'가 아니라 **'재료를 탈락시키는 기계'** 다.
`외국인 수급 → 유의성 없음` 이라는 결론도 성공이다.

★ v1 이 지키는 네 가지
  1. 단일 재료 먼저      — 조합은 개별 재료가 살아남은 뒤에만
  2. 기저율 대비만 보고  — 조건부 +4% 는 무조건부 +6% 앞에서 손해다
  3. 사전 등록          — 후보를 파일에 박고, 검정 횟수를 결과에 남긴다
  4. Pass / Fail / insufficient_evidence + **사유**

⛔ 하지 않는 것
  · 임계값 탐색(fishing) — 후보는 아래 HYPOTHESES 로 고정된다
  · 조합 최적화 — v1 에 없다
  · '최고 규칙' 선정 — 이 도구의 출력에 순위는 없다

입력: data/kr_history.json      (kr_history.py 산출)
출력: data/rule_comparator.json
"""
import os
import sys
import json
import random
import statistics as st

ANALYSIS_VERSION = "kr_lab_v1"          # 도구가 바뀌면 올린다 (결과 재현용)

# ★★ v1 의 용도는 Feature Screening 이지 Entry Validation 이 아니다 (CIO 확정 2026-08-13).
#    현재 유니버스는 '2026년에 좋아 보여서 고른 종목'이라 생존자 편향이 크다.
#    편향된 표본에서 '증분 정보가 없다'(기각)는 비교적 견고하지만,
#    '있다'(채택)는 편향 때문일 수 있어 쓸 수 없다.
#    → 그래서 v1 출력 어휘에서 **Pass 를 없앤다.** 'Pass' 라는 단어가 있으면 반드시 채택에 쓰인다.
UNIVERSE_META = {
    "universe_type": "current_curated",
    "survivorship_bias": "high",
    "usable_for": "feature screening only",
    "not_usable_for": "entry validation",
    "note": ("기각(rejected_in_sample)은 근거로 쓸 수 있다. "
             "retained 는 '아직 기각되지 않았다'는 뜻이며 채택 근거가 아니다. "
             "Entry Rule 채택은 Point-in-Time 유니버스를 쓰는 v2 에서만 가능하다."),
}
IN_PATH = "data/kr_history.json"
OUT_PATH = "data/rule_comparator.json"

HORIZONS = (5, 20)                      # 영업일
REGIME_CACHE: dict = {}

# ── 판정 기준 (사전에 고정한다. 결과를 보고 바꾸지 않는다) ────────────────────
#   숫자는 '진리'가 아니라 '선언한 관례'다. 바꾸려면 버전을 올리고 전부 다시 돌린다.
N_MIN = 30                  # 이보다 적으면 결과가 좋아도 insufficient_evidence
MIN_YEARS = 3               # 표본이 걸친 서로 다른 연도 수
BOOTSTRAP_ITERS = 2000
CI_LOW, CI_HIGH = 5, 95     # 90% 구간
ALPHA = (100 - CI_HIGH) / 100   # 단측 오탐률 0.05
SEED = 20260813             # 재현 가능해야 한다

# ★ 다중검정 — 44회를 돌리면 아무 신호가 없어도 평균 2.2건이 Pass 로 나온다.
#   개별 Pass 를 근거로 쓰면 그게 곧 우연을 채택하는 것이다.
#   그래서 '가족 단위 판정'을 함께 낸다: Pass 수가 기대 오탐 수를 넘지 못하면 근거 없음.
FAMILY_RULE = (
    "검정 횟수 × 0.05 = 기대 오탐 수. Pass 수가 이 값 이하면 family_level=no_evidence "
    "— 개별 Pass 를 재료 채택 근거로 쓰지 않는다."
)

VERDICT_RULE = (
    "n >= 30 AND 표본 연도 >= 3 AND 상승·하락 국면 모두 포함 이면 판정 가능. "
    "그 조건에서 초과수익 90% 부트스트랩 구간이 0 을 포함하지 않고 양수면 Pass, "
    "그 외에는 Fail(no_incremental_information). 조건 미달이면 insufficient_evidence."
)


# ── 재료 (CIO 확정 7개 중 v1 은 6개. DART 는 v2) ─────────────────────────────
FEATURES = ("foreign_flow", "institution_flow", "price", "volume", "trend",
            "peer_strength")
FEATURES_DEFERRED = {"dart_event": "v2 — 수년치 백필이 선행되어야 한다"}


# ── 사전 등록된 가설 ────────────────────────────────────────────────────────
#   각 항목은 (feature, id, 설명, 조건함수). 조건함수는 그날 시점에서 참/거짓만 답한다.
#   ⛔ 이 목록을 실행 후에 늘리면 그것은 '새 실행'이며 검정 횟수가 다시 세어진다.
def _streak(series, i, k, positive=True):
    """직전 k 영업일이 모두 순매수(또는 순매도)인가."""
    if i + 1 < k:
        return False
    vals = series[i - k + 1: i + 1]
    return all((v > 0) if positive else (v < 0) for v in vals)


HYPOTHESES = [
    ("foreign_flow", "F1", "외국인 순매수 1일", lambda d, i: _streak(d["foreign"], i, 1)),
    ("foreign_flow", "F2", "외국인 순매수 2일 연속", lambda d, i: _streak(d["foreign"], i, 2)),
    ("foreign_flow", "F3", "외국인 순매수 3일 연속", lambda d, i: _streak(d["foreign"], i, 3)),
    ("foreign_flow", "F5", "외국인 순매수 5일 연속", lambda d, i: _streak(d["foreign"], i, 5)),

    ("institution_flow", "I1", "기관 순매수 1일", lambda d, i: _streak(d["inst"], i, 1)),
    ("institution_flow", "I2", "기관 순매수 2일 연속", lambda d, i: _streak(d["inst"], i, 2)),
    ("institution_flow", "I3", "기관 순매수 3일 연속", lambda d, i: _streak(d["inst"], i, 3)),
    ("institution_flow", "I5", "기관 순매수 5일 연속", lambda d, i: _streak(d["inst"], i, 5)),

    ("foreign_flow", "B1", "외국인·기관 동시 순매수 1일",
     lambda d, i: _streak(d["foreign"], i, 1) and _streak(d["inst"], i, 1)),
    ("foreign_flow", "B3", "외국인·기관 동시 순매수 3일 연속",
     lambda d, i: _streak(d["foreign"], i, 3) and _streak(d["inst"], i, 3)),

    ("price", "P20", "20일 신고가", lambda d, i: _is_high(d, i, 20)),
    ("price", "P60", "60일 신고가", lambda d, i: _is_high(d, i, 60)),
    ("price", "P252", "252일 신고가", lambda d, i: _is_high(d, i, 252)),
    ("price", "PC", "종가가 당일 고저 상위 20% 구간 마감", lambda d, i: _close_position(d, i, 0.8)),

    ("volume", "V15", "거래량 20일평균 1.5배 이상", lambda d, i: _vol_ratio(d, i, 1.5)),
    ("volume", "V20", "거래량 20일평균 2.0배 이상", lambda d, i: _vol_ratio(d, i, 2.0)),
    ("volume", "V30", "거래량 20일평균 3.0배 이상", lambda d, i: _vol_ratio(d, i, 3.0)),

    ("trend", "T20", "종가 > 20일선", lambda d, i: _above_ma(d, i, 20)),
    ("trend", "T60", "종가 > 60일선", lambda d, i: _above_ma(d, i, 60)),
    ("trend", "TA", "20일선 > 60일선 (정배열)", lambda d, i: _ma_order(d, i)),

    ("peer_strength", "PR0", "유니버스 peer 평균 수익률 > 0", lambda d, i: _peer(d, i, 0.0)),
    ("peer_strength", "PR2", "유니버스 peer 평균 수익률 > +2%", lambda d, i: _peer(d, i, 0.02)),
]


# ── 재료 계산 (원천 수치 → 조건) ────────────────────────────────────────────

def _is_high(d, i, win):
    if i + 1 < win:
        return False
    return d["close"][i] >= max(d["close"][i - win + 1: i + 1])


def _close_position(d, i, thr):
    hi, lo, c = d["high"][i], d["low"][i], d["close"][i]
    if hi == lo:
        return False
    return (c - lo) / (hi - lo) >= thr


def _vol_ratio(d, i, mult):
    if i < 20:
        return False
    avg = sum(d["volume"][i - 20: i]) / 20
    return avg > 0 and d["volume"][i] >= avg * mult


def _ma(d, i, win):
    if i + 1 < win:
        return None
    return sum(d["close"][i - win + 1: i + 1]) / win


def _above_ma(d, i, win):
    m = _ma(d, i, win)
    return m is not None and d["close"][i] > m


def _ma_order(d, i):
    a, b = _ma(d, i, 20), _ma(d, i, 60)
    return a is not None and b is not None and a > b


def _peer(d, i, thr):
    """★ sector 가 아니라 peer 다 — 유니버스 안의 동행성만 본다.
    측정하지 않은 것(진짜 섹터)을 측정했다고 말하지 않기 위해 이름을 이렇게 둔다."""
    vals = d["peer_ret"][i]
    return vals is not None and vals > thr


# ── 통계 ────────────────────────────────────────────────────────────────────

def forward_return(closes, i, h):
    if i + h >= len(closes):
        return None
    a, b = closes[i], closes[i + h]
    return (b / a - 1) if a else None


def block_bootstrap_ci(values, block, iters=BOOTSTRAP_ITERS, seed=SEED):
    """겹치는 보유구간 때문에 표본이 독립이 아니다 → 연속 블록으로 재표집한다.
    ⚠ 그래도 완전한 보정은 아니다. 결과에 overlapping_windows 로 남긴다."""
    n = len(values)
    if n == 0:
        return None, None
    block = max(1, min(block, n))
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        pick = []
        while len(pick) < n:
            s = rng.randrange(0, n)
            pick.extend(values[s:s + block] or values[0:1])
        means.append(sum(pick[:n]) / n)
    means.sort()
    return (means[int(len(means) * CI_LOW / 100)],
            means[int(len(means) * CI_HIGH / 100) - 1])


# ── 실행 ────────────────────────────────────────────────────────────────────

def build_series(payload: dict) -> dict:
    """종목별 시계열 + peer 수익률(같은 날 다른 종목 평균)."""
    stocks = {c: s for c, s in (payload.get("stocks") or {}).items()
              if s.get("status") == "ok"}
    all_dates = sorted({d for s in stocks.values() for d in s["daily"]})

    ret_by_date = {}                     # date -> {code: 당일 수익률}
    prev = {}
    for date in all_dates:
        for code, s in stocks.items():
            row = s["daily"].get(date)
            if not row:
                continue
            p = prev.get(code)
            if p:
                ret_by_date.setdefault(date, {})[code] = row["close"] / p - 1
            prev[code] = row["close"]

    out = {}
    for code, s in stocks.items():
        dates = sorted(s["daily"])
        d = {"code": code, "name": s.get("name"), "dates": dates,
             "close": [], "high": [], "low": [], "volume": [],
             "foreign": [], "inst": [], "peer_ret": []}
        for date in dates:
            r = s["daily"][date]
            d["close"].append(r["close"])
            d["high"].append(r.get("high", r["close"]))
            d["low"].append(r.get("low", r["close"]))
            d["volume"].append(r.get("volume", 0))
            d["foreign"].append(r.get("foreign", 0))
            d["inst"].append(r.get("inst", 0))
            peers = [v for c, v in (ret_by_date.get(date) or {}).items() if c != code]
            d["peer_ret"].append(sum(peers) / len(peers) if peers else None)
        out[code] = d
    return out


def annual_returns(series: dict) -> dict:
    """종목별 연도 수익률(첫 종가 → 마지막 종가).
    ★ '생존자 편향'은 내 추론이었다. 연도 수익률을 함께 실어 추론 자체를 검증 가능하게 둔다.
      예: 2022 는 반도체 급락기였다. 여기서 삼성전자·SK하이닉스가 크게 음수인데
          pooled 국면이 up 이라면, 편향이 아니라 국면 지표가 오해를 부르는 것이다."""
    out = {}
    for d in series.values():
        by_year = {}
        for i, date in enumerate(d["dates"]):
            by_year.setdefault(date[:4], []).append(d["close"][i])
        out[d["name"] or d["code"]] = {
            y: round(v[-1] / v[0] - 1, 4) for y, v in sorted(by_year.items()) if len(v) > 1
        }
    return out


def regime_by_year(series: dict, h: int) -> dict:
    """연도별 무조건부 h일 수익률 평균 — '이 표본에 하락 국면이 있었는가'의 근거."""
    acc = {}
    for d in series.values():
        for i, date in enumerate(d["dates"]):
            fr = forward_return(d["close"], i, h)
            if fr is not None:
                acc.setdefault(date[:4], []).append(fr)
    return {y: {"mean": st.mean(v), "n": len(v), "direction": "up" if st.mean(v) > 0 else "down"}
            for y, v in sorted(acc.items())}


def evaluate(series: dict) -> list:
    results = []
    for feature, hid, desc, cond in HYPOTHESES:
        for h in HORIZONS:
            cond_rets, years, bench_all = [], set(), []
            for d in series.values():
                closes = d["close"]
                for i in range(len(closes)):
                    fr = forward_return(closes, i, h)
                    if fr is None:
                        continue
                    bench_all.append(fr)                  # 무조건부 기저율
                    try:
                        hit = cond(d, i)
                    except Exception:                     # noqa: BLE001
                        hit = False
                    if hit:
                        cond_rets.append(fr)
                        years.add(d["dates"][i][:4])

            n = len(cond_rets)
            bench = st.mean(bench_all) if bench_all else 0.0
            cond_mean = st.mean(cond_rets) if n else None
            excess = (cond_mean - bench) if n else None

            # 국면 커버리지 — 표본이 상승 연도와 하락 연도를 모두 포함하는가
            reg = REGIME_CACHE.setdefault(h, regime_by_year(series, h))
            up = {y for y, v in reg.items() if v["direction"] == "up" and y in years}
            down = {y for y, v in reg.items() if v["direction"] == "down" and y in years}

            if n < N_MIN:
                status, reason, ci = "undecidable", "sample_size", (None, None)
            elif len(years) < MIN_YEARS:
                status, reason, ci = "undecidable", "period_coverage", (None, None)
            elif not up or not down:
                status, reason, ci = "undecidable", "regime_coverage", (None, None)
            else:
                ex = [r - bench for r in cond_rets]
                ci = block_bootstrap_ci(ex, block=h)
                if ci[0] is not None and ci[0] > 0:
                    # ⛔ Pass 가 아니다. '이 표본에서 기각되지 않았다'는 뜻뿐이다.
                    status, reason = "retained", "not_rejected_in_this_sample"
                else:
                    status, reason = "rejected_in_sample", "no_incremental_information"

            results.append({
                "feature": feature, "hypothesis_id": hid, "description": desc,
                "horizon_days": h,
                "status": status, "reason": reason,
                "n": n,
                "conditional_return": cond_mean,
                "benchmark_return": bench,
                "excess_return": excess,
                "excess_ci_90": list(ci) if ci[0] is not None else None,
                "years_covered": sorted(years),
                "regime_up_years": sorted(up), "regime_down_years": sorted(down),
                "overlapping_windows": True,
            })
    return results


def main() -> None:
    if not os.path.exists(IN_PATH):
        print(f"FATAL: {IN_PATH} 가 없습니다. kr_history.py 를 먼저 실행하세요.")
        sys.exit(1)

    with open(IN_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    series = build_series(payload)
    results = evaluate(series)

    by_status = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    # ★ '판정 불가'가 많을 때, 표본이 모자란 것인지 기간·국면이 모자란 것인지는
    #   완전히 다른 처방으로 이어진다(유니버스 확장 vs 기간 확장). 반드시 구분해 출력한다.
    by_reason = {}
    for r in results:
        if r["status"] == "undecidable":
            by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    insufficient_ids = sorted({r["hypothesis_id"] for r in results
                               if r["status"] == "undecidable"})

    n_pass = by_status.get("retained", 0)
    expected_false = len(results) * ALPHA
    family = ("no_evidence" if n_pass <= expected_false
              else "some_evidence")

    out = {
        "analysis_version": ANALYSIS_VERSION,
        "purpose": "규칙을 찾는 것이 아니라, 재료를 살아남게 하거나 기각하는 것이다",
        "history_version": payload.get("history_version"),
        "universe": UNIVERSE_META,
        "verdict_vocabulary": ["rejected_in_sample", "retained", "undecidable"],
        "features": list(FEATURES),
        "features_deferred": FEATURES_DEFERRED,
        "hypotheses_declared": len(HYPOTHESES),
        "tests_run": len(results),                 # 가설 × 기간
        "declared_before_run": True,
        "verdict_rule": VERDICT_RULE,
        "family_rule": FAMILY_RULE,
        "family_level": family,
        "pass_count": n_pass,
        "expected_false_pass_by_chance": round(expected_false, 2),
        "thresholds": {"n_min": N_MIN, "min_years": MIN_YEARS,
                       "ci": [CI_LOW, CI_HIGH], "bootstrap_iters": BOOTSTRAP_ITERS,
                       "seed": SEED},
        "caveat": ("보유구간이 겹쳐 표본이 독립이 아니다. 블록 부트스트랩으로 일부만 보정된다. "
                   "Pass 는 '유망하다'는 뜻이지 '검증되었다'는 뜻이 아니다 — Forward 가 남았다."),
        "summary": by_status,
        "insufficient_by_reason": by_reason,
        "regime_by_year": {str(h): REGIME_CACHE.get(h, {}) for h in HORIZONS},
        "annual_returns_by_stock": annual_returns(series),
        "insufficient_hypotheses": insufficient_ids,
        "family_rule_caveat": ("기대 오탐 2.2건은 검정이 서로 독립일 때의 값이다. "
                               "실제 가설들은 상관이 높아(P20·P60·P252, T20·T60·TA) "
                               "유효 독립 검정 수는 더 적다 — 즉 이 기준은 보수적이다."),
        "results": results,
    }

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[lab] {ANALYSIS_VERSION} · 가설 {len(HYPOTHESES)}개 × 기간 {len(HORIZONS)}개 "
          f"= 검정 {len(results)}회")
    print(f"[lab] {by_status}")
    print("[lab] 연도 수익률(종목별) — 생존자 편향 진단용")
    for name, yrs in annual_returns(series).items():
        print(f"[lab]   {name:<14} " + " ".join(f"{y}:{v:+.0%}" for y, v in yrs.items()))
    for h in HORIZONS:
        reg = REGIME_CACHE.get(h, {})
        line = " ".join(f"{y}:{v['direction']}({v['mean']:+.1%})" for y, v in reg.items())
        print(f"[lab] 국면 {h}일  {line}")
        if not any(v["direction"] == "down" for v in reg.values()):
            print(f"[lab]   ⚠ {h}일 기준 하락 연도가 0개 — 규칙과 국면을 분리할 수 없다")
    if by_reason:
        print(f"[lab] 판정불가 사유: {by_reason}")
        print(f"[lab] 판정불가 가설: {insufficient_ids}")
    print(f"[lab] family_level={family}  (Pass {n_pass}건 vs 우연 기대 {expected_false:.1f}건)")
    if family == "no_evidence":
        print("[lab] → 개별 Pass 를 재료 채택 근거로 쓰지 않는다. 정상 산출물이다.")
    for r in results:
        if r["status"] == "retained":
            print(f"[lab] retained(채택 아님)  {r['hypothesis_id']:>4} {r['description']} "
                  f"({r['horizon_days']}일) 초과 {r['excess_return']:+.2%} n={r['n']}")
    print(f"[lab] saved {OUT_PATH}")


if __name__ == "__main__":
    main()
