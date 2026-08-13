"""Portfolio Constitution — 검산기 (Level 0)

  ★★  수익은 시장이 결정하지만, 최대 손실은 헌법이 결정한다.  ★★
  ★★  겉으로 보이는 개수보다 실제 독립성을 우선한다.        ★★
                                                    (CIO 확정 2026-08-13)

이 파일은 헌법을 '문서'에서 '어길 수 없는 규칙'으로 바꾼다.
숫자가 비어 있으면 비준되지 않은 것이고, 비준 전에는 어떤 매수도 없다.

★ 안전식은 한 줄이다 — 버킷 정의가 바뀌어도 흔들리지 않는다
      D = 100% − C            최대 투입 비중
      D × L ≤ P               전 포지션 동시 손절 시 손실 ≤ 포트폴리오 한도
  N_eff(위험 단위 수)는 안전식에 들어가지 않는다. 집중도만 통제한다.

★ 검산 4개
      ① D = 100% − C
      ② D × L ≤ P                          안전
      ③ W_position ≤ W_bucket              집중도
      ④ W₀ ≤ W₁ ≤ W₂ ≤ W_position          증거 등급 단조

⛔ 이 파일은 숫자를 제안하지 않는다. 검산만 한다.
   C·L·P 는 위험 허용도의 문제이지 계산의 문제가 아니며, IC 가 정한다.
"""
import os
import sys
import json

PATH = os.getenv("CONSTITUTION_PATH", "config/constitution.json")

FIELDS = ("B1_bucket_definition", "B2_cash_floor_pct", "B3_bucket_max_pct",
          "B4_position_max_pct", "B5_stop_loss_pct", "B6_portfolio_max_loss_pct")
EVIDENCE_ORDER = ("backtest_only", "forward_early", "forward_established", "operating")


def load(path: str = PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check(c: dict) -> dict:
    """비준 여부와 위반 목록을 낸다. 판정 어휘는 ratified / not_ratified / contradictory."""
    missing = [f for f in FIELDS if c.get(f) in (None, "")]
    ev = c.get("B7_evidence_state_max_pct") or {}
    missing += [f"B7.{k}" for k in EVIDENCE_ORDER if ev.get(k) is None]

    if missing:
        return {"status": "not_ratified", "missing": missing, "violations": [],
                "derived": {}, "buy_allowed": False,
                "reason": "숫자가 비어 있다 — IC 세션에서 확정해야 한다"}

    C = c["B2_cash_floor_pct"]
    L = c["B5_stop_loss_pct"]
    P = c["B6_portfolio_max_loss_pct"]
    Wb = c["B3_bucket_max_pct"]
    Wp = c["B4_position_max_pct"]

    D = 100.0 - C                              # ①
    worst = D * L / 100.0                      # ② 전 포지션 동시 손절 시 손실(%)

    v = []
    if worst > P:
        v.append({"rule": "② D × L ≤ P", "detail":
                  f"최대 투입 {D:.0f}% × 손절 {L:.0f}% = {worst:.1f}% > 한도 {P:.0f}%",
                  "fix": f"C 를 {100 - P * 100 / L:.0f}% 이상으로 올리거나, "
                         f"L 을 {P * 100 / D:.1f}% 이하로 줄이거나, P 를 {worst:.1f}% 이상으로"})
    if Wp > Wb:
        v.append({"rule": "③ W_position ≤ W_bucket",
                  "detail": f"종목 {Wp:.0f}% > 버킷 {Wb:.0f}%"})
    if Wb > D:
        v.append({"rule": "③' W_bucket ≤ D",
                  "detail": f"버킷 {Wb:.0f}% > 최대 투입 {D:.0f}%"})

    seq = [ev[k] for k in EVIDENCE_ORDER]
    if any(a > b for a, b in zip(seq, seq[1:])):
        v.append({"rule": "④ 증거 등급 단조 증가",
                  "detail": f"{dict(zip(EVIDENCE_ORDER, seq))}"})
    if seq[-1] > Wp:
        v.append({"rule": "④' 최고 등급 ≤ W_position",
                  "detail": f"{seq[-1]:.0f}% > {Wp:.0f}%"})

    return {
        "status": "ratified" if not v else "contradictory",
        "missing": [],
        "violations": v,
        "derived": {"max_deployed_pct": D, "worst_case_loss_pct": round(worst, 2),
                    "headroom_pct": round(P - worst, 2)},
        "buy_allowed": not v,
        "reason": None if not v else "헌법 내부에 모순이 있다",
    }


def tradeoff_table(P_options=(10, 15, 20), L_options=(10, 15, 20, 25)) -> list:
    """②를 만족하려면 현금 하한이 최소 얼마여야 하는가.
    ⛔ 권고가 아니다. 각 조합의 '귀결'만 보여준다 — 선택은 IC 가 한다."""
    rows = []
    for P in P_options:
        for L in L_options:
            c_min = 100 - (P * 100 / L)
            rows.append({"P": P, "L": L,
                         "min_cash_floor_pct": round(max(0.0, c_min), 1),
                         "max_deployed_pct": round(min(100.0, P * 100 / L), 1)})
    return rows


def main() -> None:
    if not os.path.exists(PATH):
        print(f"FATAL: {PATH} 없음")
        sys.exit(1)

    c = load()
    r = check(c)

    print(f"[헌법] status = {r['status']}   buy_allowed = {r['buy_allowed']}")
    if r["missing"]:
        print(f"[헌법] 미확정 {len(r['missing'])}건: {r['missing']}")
        print(f"[헌법] {r['reason']}")
        print("[헌법] ⚠ 비준 전에는 어떤 매수도 없다. 이 상태가 정상이며 숨기지 않는다.")
        print("\n[헌법] ② D × L ≤ P 의 귀결 — 선택이 아니라 산수다")
        print("       P(한도)  L(손절)  →  현금하한 최소   최대투입")
        for row in tradeoff_table():
            print(f"        {row['P']:>4}%   {row['L']:>4}%  →  "
                  f"{row['min_cash_floor_pct']:>8.1f}%   {row['max_deployed_pct']:>7.1f}%")
    for v in r["violations"]:
        print(f"[헌법] ⛔ {v['rule']} — {v['detail']}")
        if v.get("fix"):
            print(f"[헌법]    해소: {v['fix']}")
    if r["status"] == "ratified":
        d = r["derived"]
        print(f"[헌법] 최대 투입 {d['max_deployed_pct']:.0f}% · "
              f"최악 손실 {d['worst_case_loss_pct']:.1f}% · 여유 {d['headroom_pct']:.1f}%p")

    if r["status"] == "contradictory":
        sys.exit(1)                 # 모순된 헌법은 커밋 시점에 빨간불


if __name__ == "__main__":
    main()
