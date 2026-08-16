#!/usr/bin/env python3
"""mutation runner — 저장소 안에서 재현 가능한 mutation 검증 체계 (CIO 승인 2026-08-16).

★ 이 파일이 무엇을 증명하고 무엇을 증명하지 않는지 먼저 밝힌다.

  증명한다
    · 각 변이가 **정본(HEAD) 에서 직접 만든 일회용 checkout** 안에서만 적용·실행됐다
    · 그 checkout 안에 `__pycache__` 가 **한 순간도 존재하지 않았다**
      (= 이전 변이의 bytecode 가 실행될 경로 자체가 없다)
    · 실행 대상 파일의 sha256 · import 경로 · subprocess cwd 가 서로 일치한다
    · 회귀가 실패했다면 **어떤 검사가** 실패했는지, 그것이 **이 변이가 깨뜨리기로
      선언한 검사(expected_killers)** 인지

  ⛔ 증명하지 않는다
    · 회귀 프로세스가 로드한 code object 그 자체 (별도 fingerprint 는 이번 Gate 에서
      넣지 않는다 — CIO 판정 e). 격리가 execution-integrity 계약이다.
    · probe 프로세스와 회귀 프로세스가 **같은 프로세스**라는 것. 두 프로세스는 같은
      checkout · 같은 cwd · 같은 인터프리터를 쓰지만 별개 프로세스다.
    · 변이 문면이 의도한 의미를 실제로 바꾸는지 (그것은 사람이 review 한다)

★ `-B` 의 용법에 대하여 — 이것은 determinism 수단이 **아니다**.
  `-B` 는 pyc **쓰기**만 막고 **읽기**는 막지 않는다 (2026-08-16 실측 · Python 공식
  의미와도 일치). 따라서 stale pyc 를 무효화하는 장치로 쓰면 안 된다.
  여기서는 오직 「일회용 tree 에 pyc 를 남기지 않는다」는 **위생** 목적으로만 쓰고,
  실제 계약은 `assert_no_pycache()` 가 **검사**한다. 가정하지 않는다.

exit code 는 **run 의 유효성**이다.
  ⛔ exit 0 은 「모든 변이가 잡혔다」는 뜻이 **아니다**. SURVIVED · MISATTRIBUTED 는
     발견 사실이며 숨기지 않고 보고하되 run 을 무효로 만들지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CATALOG_DIR = os.path.join(HERE, "catalog")
PY = sys.executable

RE_SUMMARY = re.compile(r"^\s*(\d+) PASS / (\d+) FAIL\s*$", re.M)
# ★ 회귀의 `check()` 는 **정확히 두 칸** 들여쓴 `  ✗ ` 로 찍는다 (세 회귀 공통).
#   collector 자신의 진단 출력은 대부분 네 칸이지만 **두 칸짜리도 하나 있다**
#   (`capture_azure_fixture.py:169`). 즉 들여쓰기만으로는 완전히 갈리지 않는다.
#   그래서 세 겹으로 막는다:
#     ① 정확히 두 칸인 줄만 후보로 본다 (네 칸 진단 출력 배제)
#     ② baseline 출력에 이미 있던 줄은 **이번 변이가 만든 것이 아니므로** 뺀다
#     ③ 남은 개수를 회귀가 스스로 보고한 `N PASS / M FAIL` 의 M 과 대조한다
#   ③ 이 어긋나면 해석을 신뢰하지 않고 `parse_exact=False` 로 남긴다 — 숨기지 않는다.
#   ⛔ 회귀 파일의 출력 형식을 고쳐서 해결하지 않는다 (범위 밖).
RE_FAILLINE = re.compile(r"^  ✗ (.+)$", re.M)

# ── verdict (CIO 판정 c) ──────────────────────────────────────────────
KILLED = "KILLED"                    # expected_killers 중 ≥1 실패
SURVIVED = "SURVIVED"                # 회귀 전체 PASS
MISATTRIBUTED = "MISATTRIBUTED"      # 회귀는 FAIL · expected_killers 는 모두 PASS
NOT_APPLICABLE = "NOT_APPLICABLE"    # anchor count == 0
INVALID_RUN = "INVALID_RUN"          # baseline FAIL / anchor 불일치 / 격리 위반
VERDICTS = (KILLED, SURVIVED, MISATTRIBUTED, NOT_APPLICABLE, INVALID_RUN)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def git(*args, cwd=ROOT) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} → {r.returncode}: {r.stderr.strip()}")
    return r.stdout


# ══════════════════════════════════════════════════════════════════════
# 격리 — 변이마다 HEAD 에서 만든 일회용 checkout
# ══════════════════════════════════════════════════════════════════════
class IsolationError(RuntimeError):
    """격리 계약 위반. 결과가 아니라 **무효**다."""


def find_pycache(root: str) -> list:
    return [os.path.relpath(dp, root)
            for dp, _dn, _fn in os.walk(root) if os.path.basename(dp) == "__pycache__"]


def assert_no_pycache(checkout: str, when: str) -> None:
    found = find_pycache(checkout)
    if found:
        raise IsolationError(
            f"{when}: 일회용 checkout 안에 __pycache__ 가 있다 {found} — "
            f"stale bytecode 가 실행될 경로가 열려 있다")


def make_checkout(base_tar: str, dest_parent: str) -> str:
    d = tempfile.mkdtemp(prefix="mut-", dir=dest_parent)
    subprocess.run(["tar", "-xf", base_tar, "-C", d], check=True,
                   capture_output=True)
    assert_no_pycache(d, "checkout 생성 직후")
    return d


def run_in(checkout: str, argv: list) -> subprocess.CompletedProcess:
    """일회용 checkout 안에서만 실행한다. pyc 는 남기지 않는다(위생) — 계약은 검사다."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"     # ⛔ 무효화 장치가 아니다. 위생 목적이다.
    env.pop("PYTHONPYCACHEPREFIX", None)
    env["ATLAS_MUTATION_RUN"] = "1"
    return subprocess.run(argv, cwd=checkout, capture_output=True, text=True, env=env)


PROBE = r"""
import hashlib, importlib, json, os, sys
mod, rel = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(os.getcwd(), os.path.dirname(rel)))
m = importlib.import_module(mod)
p = os.path.abspath(m.__file__)
print(json.dumps({"module_file": p, "cwd": os.getcwd(),
                  "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()}))
"""


# ══════════════════════════════════════════════════════════════════════
# 회귀 출력 해석 — rc 가 아니라 **어떤 검사가 실패했는가**
# ══════════════════════════════════════════════════════════════════════
def parse_regression(cp: subprocess.CompletedProcess) -> dict:
    out = cp.stdout or ""
    m = RE_SUMMARY.search(out)
    x_lines = [ln.strip() for ln in RE_FAILLINE.findall(out)]
    return {"returncode": cp.returncode,
            "completed": bool(m),
            "outcome": ("PASS" if cp.returncode == 0 and m else
                        "FAIL" if m else "CRASH"),
            "reported_fail_count": int(m.group(2)) if m else None,
            "x_lines": x_lines,
            "stderr_tail": (cp.stderr or "").strip()[-400:]}


def attribute(baseline: dict, mutated: dict) -> None:
    """baseline 에 이미 있던 `✗` 줄은 이번 변이의 결과가 아니다 — 빼고 대조한다."""
    noise = set(baseline["x_lines"])
    mutated["noise_lines_removed"] = sum(1 for l in mutated["x_lines"] if l in noise)
    mutated["failed_checks"] = [l for l in mutated["x_lines"] if l not in noise]
    mutated["failed_check_count"] = len(mutated["failed_checks"])
    rep = mutated["reported_fail_count"]
    # 회귀가 스스로 보고한 FAIL 수와 일치해야 해석을 신뢰할 수 있다.
    # CRASH 면 회귀가 요약을 못 냈으므로 대조 대상이 없다.
    mutated["parse_exact"] = (rep is not None
                              and mutated["failed_check_count"] == rep)


def classify(anchor_found: int, anchors_expected: int, baseline: dict,
             mutated: dict | None, killers: list) -> tuple:
    """정확히 하나의 verdict 를 돌려준다. (verdict, 사유, 실패한 expected_killer)"""
    if anchor_found == 0:
        return NOT_APPLICABLE, "anchor 0건 — 현재 정본에서 이 변이는 성립하지 않는다", []
    if anchor_found != anchors_expected:
        return (INVALID_RUN,
                f"anchor {anchor_found}건 ≠ 선언 {anchors_expected}건", [])
    if baseline["outcome"] != "PASS":
        return (INVALID_RUN,
                f"baseline 이 PASS 가 아니다 ({baseline['outcome']}) — "
                f"이 상태에서는 모든 변이가 '잡힘'으로 보인다", [])
    hit = [k for k in killers
           if any(k in line for line in mutated["failed_checks"])]
    if hit:
        return KILLED, "", hit
    if mutated["outcome"] == "PASS":
        return SURVIVED, "회귀가 전부 통과했다 — 판별력 결함", []
    if mutated["outcome"] == "CRASH":
        return (MISATTRIBUTED,
                "회귀가 CRASH 했다 — expected_killers 가 실행되지 않았다. "
                "⛔ CIO 판정문의 5분할은 CRASH 를 명시하지 않는다 (보고 항목)", [])
    return (MISATTRIBUTED,
            f"회귀는 FAIL 했지만 expected_killers 는 모두 PASS "
            f"(실패한 검사 {mutated['failed_check_count']}건)", [])


# ══════════════════════════════════════════════════════════════════════
def load_catalog(only_file=None) -> list:
    entries = []
    for fn in sorted(os.listdir(CATALOG_DIR)):
        if not fn.endswith(".json"):
            continue
        if only_file and fn != only_file:
            continue
        with open(os.path.join(CATALOG_DIR, fn), encoding="utf-8") as f:
            spec = json.load(f)
        for m in spec["mutations"]:
            entries.append({"catalog_file": fn,
                            "target": spec["target"],
                            "regression": spec["regression"],
                            "origin": spec["origin"],
                            **m})
    return entries


def preflight() -> dict:
    """정본 worktree 를 건드리지 않는다는 것을 먼저 증명한다."""
    problems = []
    head = git("rev-parse", "HEAD").strip()
    porcelain = git("status", "--porcelain").splitlines()
    tracked_dirty = [ln for ln in porcelain if not ln.startswith("??")]
    if tracked_dirty:
        problems.append("정본 worktree 에 tracked 변경이 있다 — 일회용 checkout 은 "
                        "HEAD 에서 만들므로 무엇을 변이했는지 설명할 수 없다: "
                        + "; ".join(tracked_dirty[:5]))
    return {"head": head,
            "untracked": [ln[3:] for ln in porcelain if ln.startswith("??")],
            "problems": problems}


def run_one(e: dict, base_tar: str, parent: str, head: str) -> dict:
    rec = {"mutation_id": e["id"],
           "catalog_file": e["catalog_file"],
           "note": e["note"],
           "origin_label": e.get("origin_label"),
           "origin_harness": e["origin"],
           "target_file": e["target"],
           "regression": e["regression"],
           "expected_killers": e["expected_killers"],
           "anchors_expected": e.get("anchors_expected", 1)}
    checkout = make_checkout(base_tar, parent)
    try:
        rec["checkout_identity"] = {"head": head,
                                    "base_tar_sha256": sha256_file(base_tar),
                                    "checkout_dir": checkout}
        tpath = os.path.join(checkout, e["target"])
        if not os.path.abspath(tpath).startswith(os.path.abspath(checkout) + os.sep):
            raise IsolationError(f"target 이 checkout 밖을 가리킨다: {e['target']}")
        if not os.path.exists(tpath):
            raise IsolationError(f"target 이 checkout 에 없다: {e['target']}")

        orig = open(tpath, encoding="utf-8").read()
        rec["baseline_sha256"] = sha256_file(tpath)
        head_blob = git("show", f"{head}:{e['target']}").encode("utf-8")
        if sha256_bytes(head_blob) != rec["baseline_sha256"]:
            raise IsolationError("checkout 의 target 이 HEAD blob 과 다르다")

        # ── baseline (같은 checkout 안에서, 변이 적용 전) ──────────────
        rec["baseline"] = parse_regression(
            run_in(checkout, [PY, e["regression"]]))
        assert_no_pycache(checkout, "baseline 실행 직후")

        # ── 변이 적용 ────────────────────────────────────────────────
        found = orig.count(e["anchor"])
        rec["anchor_found"] = found
        exp = rec["anchors_expected"]
        if found == exp and found > 0:
            open(tpath, "w", encoding="utf-8").write(
                orig.replace(e["anchor"], e["replacement"], found))
            rec["mutated_sha256"] = sha256_file(tpath)
            rec["source_changed"] = rec["mutated_sha256"] != rec["baseline_sha256"]

            # ── executed witness (CIO 판정 e — 일관성 검사까지) ────────
            w = {"checkout_identity": checkout,
                 "target_relative_path": e["target"],
                 "mutated_source_sha256": rec["mutated_sha256"]}
            if e["target"].startswith("collectors/"):
                mod = os.path.splitext(os.path.basename(e["target"]))[0]
                pr = run_in(checkout, [PY, "-c", PROBE, mod, e["target"]])
                if pr.returncode != 0:
                    raise IsolationError(
                        f"witness probe 실패: {(pr.stderr or '').strip()[-300:]}")
                pw = json.loads(pr.stdout.strip().splitlines()[-1])
                w.update({"subprocess_cwd": pw["cwd"],
                          "imported_module_file": pw["module_file"],
                          "imported_module_sha256": pw["sha256"],
                          "witness_kind": "imported module"})
                assert_no_pycache(checkout, "witness probe 직후")
            else:
                w.update({"subprocess_cwd": checkout,
                          "imported_module_file": os.path.abspath(tpath),
                          "imported_module_sha256": rec["mutated_sha256"],
                          "witness_kind": "main script (CPython 은 __main__ 을 "
                                          "pyc 로 캐시하지 않는다)"})
            # 일관성 — 값이 서로 어긋나면 결과가 아니라 무효다
            bad = []
            if not w["imported_module_file"].startswith(
                    os.path.abspath(checkout) + os.sep):
                bad.append("import 된 파일이 checkout 밖이다")
            if os.path.abspath(w["subprocess_cwd"]) != os.path.abspath(checkout):
                bad.append("subprocess cwd 가 checkout 이 아니다")
            if w["imported_module_sha256"] != rec["mutated_sha256"]:
                bad.append("import 된 파일의 sha256 이 변이 결과와 다르다")
            if not rec["source_changed"]:
                bad.append("변이 후 소스가 baseline 과 동일하다 (치환이 무의미했다)")
            w["consistent"] = not bad
            w["problems"] = bad
            rec["executed_witness"] = w
            if bad:
                raise IsolationError("witness 불일치: " + "; ".join(bad))

            # ── 변이 실행 ────────────────────────────────────────────
            rec["mutated"] = parse_regression(
                run_in(checkout, [PY, e["regression"]]))
            assert_no_pycache(checkout, "변이 실행 직후")
            attribute(rec["baseline"], rec["mutated"])
        else:
            rec["mutated"] = None
            rec["executed_witness"] = None

        v, why, hit = classify(found, exp, rec["baseline"], rec["mutated"],
                               e["expected_killers"])
        rec["verdict"] = v
        rec["verdict_reason"] = why
        rec["killers_fired"] = hit
    except IsolationError as ex:
        rec["verdict"] = INVALID_RUN
        rec["verdict_reason"] = f"격리/무결성 위반 — {ex}"
        rec["killers_fired"] = []
    finally:
        shutil.rmtree(checkout, ignore_errors=True)
    return rec


PROVENANCE_REQUIRED = ("mutation_id", "target_file", "regression",
                       "baseline_sha256", "checkout_identity", "verdict")


def provenance_complete(rec: dict) -> list:
    miss = [k for k in PROVENANCE_REQUIRED if not rec.get(k)]
    if rec["verdict"] in (KILLED, SURVIVED, MISATTRIBUTED):
        for k in ("mutated_sha256", "executed_witness", "mutated", "baseline"):
            if not rec.get(k):
                miss.append(k)
        w = rec.get("executed_witness") or {}
        if not w.get("consistent"):
            miss.append("executed_witness.consistent")
        mu = rec.get("mutated") or {}
        # CRASH 면 회귀가 요약을 못 내므로 대조할 수 없다 — 그 사실 자체는 기록된다.
        if mu.get("outcome") != "CRASH" and not mu.get("parse_exact"):
            miss.append("mutated.parse_exact")
    return miss


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", help="catalog 파일명 하나만 (예 c4_sec_edgar_check.json)")
    ap.add_argument("--id", help="mutation_id 하나만")
    ap.add_argument("--json", help="결과 기록 경로 (⛔ 정본에 커밋하지 않는다)")
    a = ap.parse_args()

    pf = preflight()
    if pf["problems"]:
        for p in pf["problems"]:
            print("⛔ " + p)
        return 2
    entries = load_catalog(a.catalog)
    if a.id:
        entries = [e for e in entries if e["id"] == a.id]
    ids = [e["id"] for e in entries]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        print(f"⛔ mutation_id 가 중복이다: {dup}")
        return 2
    if not entries:
        print("⛔ 대상 변이가 0건이다")
        return 2

    parent = tempfile.mkdtemp(prefix="atlas-mut-")
    base_tar = os.path.join(parent, "base.tar")
    git("archive", "-o", base_tar, pf["head"])
    print(f"HEAD {pf['head'][:12]} · base.tar sha256 {sha256_file(base_tar)[:16]} · "
          f"변이 {len(entries)}건\n")

    recs = []
    try:
        for i, e in enumerate(entries, 1):
            rec = run_one(e, base_tar, parent, pf["head"])
            rec["provenance_missing"] = provenance_complete(rec)
            recs.append(rec)
            mark = {KILLED: "✓", SURVIVED: "✗", MISATTRIBUTED: "‼",
                    NOT_APPLICABLE: "·", INVALID_RUN: "⛔"}[rec["verdict"]]
            extra = ""
            if rec["verdict"] == KILLED:
                extra = f"  ← {rec['killers_fired'][0][:44]}"
            elif rec["verdict_reason"]:
                extra = f"  ← {rec['verdict_reason'][:64]}"
            print(f"  {mark} {rec['verdict']:15} {e['id']:22} {e['note'][:34]}{extra}")
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    part = {v: sum(1 for r in recs if r["verdict"] == v) for v in VERDICTS}
    total = len(entries)
    partition_ok = sum(part.values()) == total and all(
        r["verdict"] in VERDICTS for r in recs)
    prov_ok = all(not r["provenance_missing"] for r in recs)

    print("\n" + "─" * 62)
    print(f"catalog total       {total}")
    for v in VERDICTS:
        print(f"{v:<20}{part[v]}")
    print("─" * 62)
    print(f"partition 합 == catalog total   {'PASS' if partition_ok else 'FAIL'}")
    print(f"INVALID_RUN == 0                {'PASS' if part[INVALID_RUN] == 0 else 'FAIL'}")
    print(f"provenance 완전                  {'PASS' if prov_ok else 'FAIL'}")
    if part[SURVIVED] or part[MISATTRIBUTED]:
        print(f"\n★ SURVIVED {part[SURVIVED]} · MISATTRIBUTED {part[MISATTRIBUTED]} "
              f"— 별건으로 올린다. ⛔ 이 runner 가 회귀를 고치지 않는다.")
    print("⛔ exit 0 은 「run 이 유효하다」는 뜻이지 「모두 잡혔다」는 뜻이 아니다.")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"head": pf["head"], "catalog_total": total,
                       "partition": part, "partition_ok": partition_ok,
                       "provenance_ok": prov_ok,
                       "untracked_at_run": pf["untracked"],
                       "mutations": recs}, f, ensure_ascii=False, indent=1)
        print(f"\n결과 기록 {a.json}  ⛔ 정본에 커밋하지 않는다")
    return 0 if (partition_ok and prov_ok and part[INVALID_RUN] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
