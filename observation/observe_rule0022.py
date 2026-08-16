#!/usr/bin/env python3
"""S4A 단계 1 — OBSERVE. 층 ① → ② → ③ 을 잇고 **emit 만** 한다.

    Acquisition → RULE-0022 Observer → Normalization → (emit)

⛔ 이 파일이 하지 않는 것 — 이것이 S4A 의 핵심 계약이다
   ⛔ Observation Store 를 **import 하지 않는다** · 적용하지 않는다 (층 ④ 는 persist 단계)
   ⛔ 저장소(repository)에 쓰지 않는다 — emit 경로가 저장소 안이면 **fail-closed**
   ⛔ Git · workflow 를 모른다
   ⛔ pair · evaluator · 임계값 판정을 모른다

★ source 는 **명시**해야 한다. `live` / `fixture` 중 하나를 반드시 고른다.
  ⛔ live 실패 시 fixture 로 조용히 내려가는 fallback 을 두지 않는다 —
     그런 경로가 있으면 「live 로 돌았다」는 증거가 사라진다.

★ emit 산출물은 canonical JSON 이며 timestamp 같은 비결정 값을 담지 않는다.
  같은 입력이면 같은 바이트열이 나와야 재실행 idempotency 를 증명할 수 있다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "collectors"))

import msft_sec_results_acquisition as ACQ                          # noqa: E402
import rule0022_commercial_rpo as OBS                               # noqa: E402
from record import try_build                                        # noqa: E402

EMISSION_SCHEMA_VERSION = "observation_emission/1"

SOURCE_LIVE = "live"
SOURCE_FIXTURE = "fixture"


class ObserveError(RuntimeError):
    """관측 단계 실패. ⛔ 실패를 성공 emit 으로 흘려보내지 않는다."""


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def assert_outside_repository(path: str) -> str:
    """emit 경로가 저장소 안이면 거부한다.

    ★ 「collector 가 repo state 를 바꾸지 않는다」를 **경로 수준에서** 강제한다.
      workflow 의 `git diff --exit-code` 가드가 사후 확인이라면, 이것은 사전 차단이다.
    """
    ap = os.path.abspath(path)
    rp = os.path.abspath(ROOT)
    if ap == rp or ap.startswith(rp + os.sep):
        raise ObserveError(
            f"emit 경로가 저장소 안이다: {ap} — observe 단계는 저장소에 쓰지 않는다")
    return ap


def observe_fixture(manifest_path: str) -> dict:
    """고정 표본에서 관측한다. ⛔ 네트워크를 쓰지 않는다."""
    man = json.load(open(manifest_path, encoding="utf-8"))
    fx_dir = os.path.dirname(os.path.abspath(manifest_path))
    records, failures = [], []
    for c in sorted(man["captured"], key=lambda x: x["filing_date"]):
        path = os.path.join(fx_dir, c["fixture_file"])
        html = open(path, encoding="utf-8").read()
        prov = ACQ.exhibit_provenance(c, c["exhibit"], c["exhibit_sha256"])
        prov["slice_sha256"] = c["slice_sha256"]
        rec, why = _observe_one(html, prov)
        (records if rec else failures).append(rec or why)
    return _emission(SOURCE_FIXTURE, records, failures,
                     {"manifest": os.path.basename(manifest_path),
                      "attempted": len(man["captured"])})


def _observe_one(html: str, prov: dict):
    """(record | None, failure | None). 층 ② → ③ 을 **외부에서 명시적으로** 잇는다."""
    draft, problems, _ = OBS.observe_html(html, provenance=prov)
    if draft is None:
        return None, {"stage": "observation",
                      "outcome": (OBS.ROW_ABSENT if OBS.observation_absent(problems)
                                  else "OBSERVATION_FAILED"),
                      "accession": prov.get("accession"),
                      "filing_date": prov.get("filing_date"),
                      "problems": [str(p) for p in problems]}
    rec, err = try_build(draft)
    if rec is None:
        return None, {"stage": "normalization", "outcome": "NORMALIZATION_FAILED",
                      "accession": prov.get("accession"),
                      "filing_date": prov.get("filing_date"),
                      "problems": [str(err)]}
    return rec, None


def observe_live(limit: int) -> dict:                               # pragma: no cover
    """SEC 에서 직접 관측한다. ⛔ S4A 범위 밖 — S4B 승인 전에는 호출되지 않는다."""
    raise ObserveError(
        "live 관측은 S4B 승인 전까지 실행하지 않는다 (S4A 는 fixture 전용이다)")


def _emission(source: str, records: list, failures: list, meta: dict) -> dict:
    return {"schema_version": EMISSION_SCHEMA_VERSION,
            "source": source,
            "meta": meta,
            "records": records,
            "failures": failures,
            "observed": len(records),
            "failed": len(failures)}


def write_emission(emission: dict, out_path: str) -> str:
    """emit. ⛔ 저장소 안이면 쓰지 않는다."""
    ap = assert_outside_repository(out_path)
    os.makedirs(os.path.dirname(ap) or ".", exist_ok=True)
    with open(ap, "w", encoding="utf-8") as f:
        f.write(canonical_json(emission) + "\n")
    return ap


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RULE-0022 observe 단계 (S4A)")
    ap.add_argument("--source", required=True, choices=[SOURCE_LIVE, SOURCE_FIXTURE],
                    help="⛔ 명시 필수 — live 실패 시 fixture 로 내려가는 fallback 은 없다")
    ap.add_argument("--manifest", help="fixture manifest 경로 (source=fixture 일 때 필수)")
    ap.add_argument("--limit", type=int, default=4, help="live 조회 상한")
    ap.add_argument("--out", required=True, help="emit 경로 — ⛔ 저장소 밖이어야 한다")
    a = ap.parse_args(argv)

    print("=" * 70)
    print("RULE-0022 OBSERVE (S4A) — Acquisition → Observer → Normalization → emit")
    print("  ⛔ Observation Store 를 건드리지 않는다 · ⛔ 저장소에 쓰지 않는다")
    print("=" * 70)

    if a.source == SOURCE_FIXTURE:
        if not a.manifest:
            print("✗ source=fixture 인데 --manifest 가 없다")
            return 2
        em = observe_fixture(a.manifest)
    else:
        try:
            em = observe_live(a.limit)
        except ObserveError as e:
            print(f"✗ {e}")
            return 2

    try:
        path = write_emission(em, a.out)
    except ObserveError as e:
        print(f"✗ {e}")
        return 2

    print(f"  source   {em['source']}  meta {em['meta']}")
    print(f"  observed {em['observed']} · failed {em['failed']}")
    for r in em["records"]:
        print(f"    ✓ {r['economic_period_end']}  "
              f"{r['decision']['raw_value']:>6} → {r['decision']['numeric_value']:>5} "
              f"({r['decision']['sign_convention']})")
    for f in em["failures"]:
        print(f"    · {f['filing_date']}  {f['outcome']}  {f['problems'][0][:60]}")
    print(f"  emit → {path}")
    # ⛔ 관측 0건은 성공이 아니다 — persist 단계로 넘기지 않는다.
    if em["observed"] == 0:
        print("✗ 관측 0건 — emit 은 남기되 성공으로 보고하지 않는다")
        return 1
    return 0


if __name__ == "__main__":                                          # pragma: no cover
    sys.exit(main())
