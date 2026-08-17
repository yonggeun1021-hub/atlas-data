#!/usr/bin/env python3
"""S4A 단계 2 — PERSIST. emit 된 record 를 **다시 읽어** 층 ④ 에 적용한다.

    (emitted record) → validate_record → Observation Store → persistence artifact

★ observe 단계와 **물리적으로 분리**돼 있다는 것이 이 파일의 존재 이유다.
   ⛔ acquisition · observer · normalize 를 **import 하지 않는다.**
      직렬화돼 돌아온 record 만 받는다 — Store 는 남의 record 를 받는다고 가정한다.
   ⛔ 관측 실패를 여기서 복구하지 않는다.
   ⛔ Git · workflow 를 모른다. 파일 경로만 안다.

★ 첫 동작은 `validate_record()` 다 (store 가 강제한다).
★ CONFLICT · REVISION 이 있으면 **성공으로 보고하지 않는다** — 소비 가능해지지 않는다.

★ canonical Store 영속 경로 제안 (CIO 판정 대상 · 이번 단계에서 commit 하지 않는다)

    observations/MSFT/commercial-remaining-performance-obligation.json

  근거
    · `data/<YYYY-MM-DD>/` 의 **run-date snapshot 모델과 물리적으로 분리**된다.
      기존 `common.save()` 를 고쳐 끼워 넣지 않는다 — 두 모델은 키가 다르다.
    · 경로가 subject / measurement 를 그대로 드러내 economic-period series 임이 보인다.
    · canonical JSON 한 파일이라 Git diff 가 **사람이 감사 가능**하다
      (키 정렬 · 고정 구분자 → 재실행 시 무의미한 diff 가 나지 않는다).
    · revision chain · conflict evidence · rejection 로그가 같은 파일 안에 보존된다.
  ⛔ 이번 S4A 에서는 이 경로에 **commit 하지 않는다.** 제안만 한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import store as ST                                                  # noqa: E402

PERSIST_SCHEMA_VERSION = "observation_persist_result/1"
EMISSION_SCHEMA_VERSION = "observation_emission/1"

# ★ 제안 경로 — 상수로 박아 두되 이번 단계에서 쓰기를 강제하지 않는다.
PROPOSED_STORE_PATH = "observations/MSFT/commercial-remaining-performance-obligation.json"


class PersistError(RuntimeError):
    """persist 단계 실패."""


def read_emission(path: str) -> dict:
    """emit 산출물을 읽는다. ⛔ schema 가 다르면 조용히 수용하지 않는다."""
    with open(path, encoding="utf-8") as f:
        em = json.load(f)
    if em.get("schema_version") != EMISSION_SCHEMA_VERSION:
        raise PersistError(f"emission schema 가 다르다: {em.get('schema_version')!r}")
    if not isinstance(em.get("records"), list):
        raise PersistError("emission 에 records 목록이 없다")
    return em


def persist(emission: dict, state: dict) -> tuple:
    """(new_state, result). Store 가 첫 동작으로 record 를 검증한다.

    ⛔ 거부된 record 는 series 에 들어가지 않고 rejection 로그로만 남는다.
    """
    cur, outcomes = state, []
    for rec in emission["records"]:
        cur, res = ST.apply_record(cur, rec)
        if res["accepted"] is False:
            cur = ST.record_rejection(cur, res)
        outcomes.append(res)

    counts = {}
    for o in outcomes:
        counts[o["outcome"]] = counts.get(o["outcome"], 0) + 1
    blocked = ST.blocked_keys(cur)
    result = {
        "schema_version": PERSIST_SCHEMA_VERSION,
        "emission_source": emission.get("source"),
        "emitted_records": len(emission["records"]),
        "emitted_failures": len(emission.get("failures") or []),
        "outcomes": outcomes,
        "outcome_counts": counts,
        "consumable_keys": ST.consumable_keys(cur),
        "blocked_keys": blocked,
        "store_digest": ST.digest(cur),
        # ⛔ 미해소 CONFLICT/REVISION 이 있으면 성공으로 보고하지 않는다.
        "unresolved": bool(blocked),
    }
    return cur, result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RULE-0022 persist 단계 (S4A)")
    ap.add_argument("--emission", required=True, help="observe 단계 emit 경로")
    ap.add_argument("--store", required=True, help="Observation Store 파일 경로")
    ap.add_argument("--result", required=True, help="persistence artifact 출력 경로")
    a = ap.parse_args(argv)

    print("=" * 70)
    print("RULE-0022 PERSIST (S4A) — emitted record → validate → Store")
    print("  ⛔ acquisition · observer · normalize 를 import 하지 않는다")
    print(f"  ★ canonical store 제안 경로: {PROPOSED_STORE_PATH} (이번 단계 commit 없음)")
    print("=" * 70)

    try:
        em = read_emission(a.emission)
    except (PersistError, json.JSONDecodeError, OSError) as e:
        print(f"✗ emission 을 읽지 못했다 — {type(e).__name__}: {e}")
        return 2
    if not em["records"]:
        print("✗ emit 된 record 가 0건 — persist 하지 않는다")
        return 2

    state = ST.load_state(a.store)
    new_state, result = persist(em, state)

    for o in result["outcomes"]:
        pe = (o.get("key") or {}).get("economic_period_end", "?")
        print(f"    {o['outcome']:<28} {pe}  consumable={o['consumable']}"
              + (f"  blocked={o['blocked_by']}" if o["blocked_by"] else ""))
    print(f"  counts    {result['outcome_counts']}")
    print(f"  consumable {len(result['consumable_keys'])} · blocked {len(result['blocked_keys'])}")

    ST.save_state(new_state, a.store)
    os.makedirs(os.path.dirname(os.path.abspath(a.result)) or ".", exist_ok=True)
    with open(a.result, "w", encoding="utf-8") as f:
        f.write(ST.canonical_json(result) + "\n")
    print(f"  store  → {a.store}")
    print(f"  result → {a.result}")

    if result["unresolved"]:
        print("⛔ 미해소 CONFLICT/REVISION 이 있다 — 정상 성공으로 소비하지 않는다")
        return 1
    if any(o["accepted"] is False for o in result["outcomes"]):
        print("⛔ 거부된 record 가 있다")
        return 1
    return 0


if __name__ == "__main__":                                          # pragma: no cover
    sys.exit(main())
