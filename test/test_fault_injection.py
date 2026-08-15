"""Fault Injection suite — CIO 판정 2026-08-15 로 확정된 5개 class 중 4개.

    FI-1 committed artifact drift        REQUIRED PASS
    FI-2 required input/artifact missing REQUIRED PASS
    FI-3 frozen input tamper             ★ KNOWN GAP / NOT GATED — 여기서 검증하지 않는다
    FI-4 approved 14-test omission       REQUIRED PASS
    FI-5 malformed required JSON         REQUIRED PASS
    FI-6 invalid vocabulary value        REQUIRED PASS — 결함 C (CIO 판정 2026-08-15)

★ 이 suite 는 Actions / clean-checkout 경계를 넘으면서 **새로 생긴 공백만** 담당한다.
  기존 14개 fault 유형(원문 표류 · 상태 위조 · 제외 객체 유입 · 판정 위조 · 비결정성 …)은
  기존 회귀가 이미 담당하며 Actions PASS 조건 ① 에서 전량 재실행된다.
  ⛔ 그것들을 FI 라는 이름으로 다시 쓰지 않는다.

★ PASS 정의 (FI-1/2/5) — 셋을 모두 만족해야 한다.
    ① 위반이 검출된다
    ② 해당 실행이 non-zero 로 실패한다
    ③ 실패 때문에 기존 정상 authority / committed artifact 가 변경되지 않는다
  FI-4 는 성격이 다르다 — 누락 자체가 Actions Gate 실패로 검출되면 PASS 다.

★ 모든 주입은 패키지 **사본** 위에서만 한다. 원본 작업 트리는 읽기만 한다.
⛔ scope 확대 금지 — permission · symlink · 임의 인코딩 손상 · test source 악의적 변조 ·
   runner/OS/network/GitHub 장애는 이 suite 의 대상이 아니다.
⛔ FI-3 을 통과시키려고 새 file-level hash 정책이나 integrity SSOT 를 만들지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
AUTHORITY = "config/rules.json"          # 훼손되면 안 되는 대표 authority 산출물

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def clone(dst):
    """패키지 사본. 원본은 절대 건드리지 않는다."""
    for d in ("rules", "test", "config"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dst, d))
    for f in ("_watchlist_rows.json", "run_all.py"):
        shutil.copyfile(os.path.join(ROOT, f), os.path.join(dst, f))


def run(cmd, cwd, disposable=False):
    """사본은 임시 디렉터리이므로 authoritative mode 를 열어도 되는 환경이다."""
    env = dict(os.environ)
    if disposable:
        env["ATLAS_DISPOSABLE_CHECKOUT"] = "1"
    else:
        env.pop("ATLAS_DISPOSABLE_CHECKOUT", None)
    return subprocess.run([PY] + cmd, cwd=cwd, capture_output=True, text=True, env=env)


ORIG_AUTHORITY_SHA = sha(os.path.join(ROOT, AUTHORITY))


# ══════════════════════════════════════════════════════════════════════
print("FI-1 committed artifact drift")
with tempfile.TemporaryDirectory(prefix="fi1_") as d:
    clone(d)
    target = os.path.join(d, "rules", "decision_cards.json")
    before = sha(target)
    with open(target, "a", encoding="utf-8") as f:
        f.write(" ")                      # committed 본만 1바이트 달라진다
    check("주입으로 committed 본이 실제로 달라졌다", sha(target) != before)

    r = run(["run_all.py", "--no-fi", "--authoritative"], d, disposable=True)
    out = r.stdout + r.stderr
    check("① 위반이 검출된다", "committed 와 재빌드가 다르다" in out, out[-300:])
    check("① 어느 파일인지 지목한다", "decision_cards.json" in out)
    check("② non-zero 로 실패한다", r.returncode != 0, str(r.returncode))
    check("② Actions PASS = NO 로 보고한다", "Actions PASS = NO" in out)
    check("③ 원본 authority 산출물이 그대로다",
          sha(os.path.join(ROOT, AUTHORITY)) == ORIG_AUTHORITY_SHA)

# ══════════════════════════════════════════════════════════════════════
print("FI-2 required input / artifact missing")
with tempfile.TemporaryDirectory(prefix="fi2_") as d:
    clone(d)
    monid = os.path.join(d, "rules", "monitoring_identity.json")
    inv = os.path.join(d, "rules", "rule_inventory.json")
    auth = os.path.join(d, AUTHORITY)
    inv_before, auth_before = sha(inv), sha(auth)
    os.unlink(monid)                      # DAG 상 required input 제거

    r = run(["rules/rule_inventory.py"], d)
    out = r.stdout + r.stderr
    check("① 필수 입력 부재가 검출된다", "required input 이 없다" in out, out[-300:])
    check("① 빈 identity 집합으로 진행하지 않는다",
          "monitoring identity 없이" in out)
    check("② non-zero 로 실패한다", r.returncode != 0, str(r.returncode))
    check("③ 기존 Inventory 산출물이 변경되지 않는다", sha(inv) == inv_before)
    check("③ authority 산출물도 변경되지 않는다", sha(auth) == auth_before)
    check("③ 원본 작업 트리 authority 불변",
          sha(os.path.join(ROOT, AUTHORITY)) == ORIG_AUTHORITY_SHA)

    # ★ optional state 까지 required 로 재분류하지 않았는지 — 반대 방향 확인
    d2 = os.path.join(d, "_opt")
    os.makedirs(d2)
    clone(d2)
    os.unlink(os.path.join(d2, "rules", "monitoring_identity.json"))
    r2 = run(["rules/monitoring_identity.py"], d2)
    check("optional state(기존 ID 파일) 부재는 실패가 아니다",
          r2.returncode == 0, (r2.stdout + r2.stderr)[-300:])

# ══════════════════════════════════════════════════════════════════════
print("FI-4 approved 14-test omission")
with tempfile.TemporaryDirectory(prefix="fi4_") as d:
    clone(d)
    dropped = "test/test_merge_decision.py"
    os.unlink(os.path.join(d, dropped))

    r = run(["run_all.py", "--no-fi", "--authoritative"], d, disposable=True)
    out = r.stdout + r.stderr
    check("① 승인 목록과 실제 집합의 불일치가 검출된다",
          "승인 목록과 실제 test 집합이 다르다" in out, out[-300:])
    check("① 누락된 파일을 지목한다", "test_merge_decision.py" in out)
    check("② Actions Gate 가 실패한다", r.returncode != 0, str(r.returncode))
    check("② Actions PASS = NO", "Actions PASS = NO" in out)

with tempfile.TemporaryDirectory(prefix="fi4b_") as d:
    clone(d)
    with open(os.path.join(d, "test", "test_unapproved.py"), "w") as f:
        f.write("import sys\nsys.exit(0)\n")

    r = run(["run_all.py", "--no-fi", "--authoritative"], d, disposable=True)
    out = r.stdout + r.stderr
    check("미승인 test 추가도 불일치로 검출된다", "미승인" in out, out[-300:])
    check("그 경우도 non-zero", r.returncode != 0, str(r.returncode))

# ══════════════════════════════════════════════════════════════════════
print("FI-5 malformed required JSON")
with tempfile.TemporaryDirectory(prefix="fi5_") as d:
    clone(d)
    mapping = os.path.join(d, "rules", "ssot_mapping.json")
    auth = os.path.join(d, AUTHORITY)
    auth_before = sha(auth)
    raw = open(mapping, "rb").read()
    open(mapping, "wb").write(raw[: len(raw) // 2])       # truncated JSON

    r = run(["rules/promote_rules_ssot.py"], d)
    out = r.stdout + r.stderr
    check("① 손상된 필수 입력에서 정상 진행하지 않는다",
          "JSONDecodeError" in out or "Expecting" in out, out[-300:])
    check("② non-zero 로 실패한다", r.returncode != 0, str(r.returncode))
    check("③ authority 산출물이 그대로다", sha(auth) == auth_before)
    check("③ 원본 작업 트리 authority 불변",
          sha(os.path.join(ROOT, AUTHORITY)) == ORIG_AUTHORITY_SHA)

    # 같은 class, 다른 구현 경로 — publish 직전 단계에서도 정상 산출물을 내지 않는가
    inv = os.path.join(d, "rules", "rule_inventory.json")
    inv_before = sha(inv)
    r2 = run(["rules/rule_inventory.py"], d)
    check("다른 builder 도 손상 입력에서 실패한다", r2.returncode != 0)
    check("그때도 기존 산출물이 남아 있다", sha(inv) == inv_before)

# ══════════════════════════════════════════════════════════════════════
print("FI-6 invalid vocabulary value — 결함 C (CIO 판정 2026-08-15)")
#   발견 경위: `SOURCE_RESOLVED` 가 어휘 집합에 없는 채로 승격에 실렸는데 회귀 전량과
#   Actions PASS 를 통과했다. 어휘 검사가 분해 단계에만 있었기 때문이다.
#   ⇒ 이제 하류 authoritative 산출물에 어휘 밖 값을 넣으면 반드시 실패해야 한다.
#   ⛔ 통과시키려고 어휘를 넓히지 않는다. 오타 하나가 정상 상태처럼 흐르는 것을 막는 것이 목적이다.
with tempfile.TemporaryDirectory(prefix="fi6_") as d:
    clone(d)
    tgt = os.path.join(d, AUTHORITY)
    doc = json.load(open(tgt, encoding="utf-8"))
    victim = doc["rules"][0]["rule_id"]
    doc["rules"][0]["source_qualification"] = "SOURCE_RESOLVEDD"      # 오타 1글자
    json.dump(doc, open(tgt, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    check("주입으로 어휘 밖 값이 실제로 들어갔다",
          json.load(open(tgt, encoding="utf-8"))["rules"][0]["source_qualification"]
          == "SOURCE_RESOLVEDD")

    r = run(["run_all.py", "--no-fi", "--authoritative"], d, disposable=True)
    out = r.stdout + r.stderr
    check("① 위반이 검출된다",
          "허용 어휘 밖" in out or "committed 와 재빌드가 다르다" in out, out[-400:])
    check("② non-zero 로 실패한다", r.returncode != 0, str(r.returncode))
    check("② Actions PASS = NO 로 보고한다", "Actions PASS = NO" in out)
    check("③ 원본 authority 산출물이 그대로다",
          sha(os.path.join(ROOT, AUTHORITY)) == ORIG_AUTHORITY_SHA)

    # ★ 검사기 자체가 이 값을 거부하는지 직접 확인한다 — 재빌드 덮어쓰기에 가려지지 않게.
    sys.path.insert(0, os.path.join(ROOT, "rules"))
    import vocabulary as _V
    check("④ 어휘 검사기가 그 값을 직접 거부한다",
          bool(_V.vocab_violations({"source_qualification": "SOURCE_RESOLVEDD"})), victim)
    check("④ 승인된 SOURCE_RESOLVED 는 거부하지 않는다",
          not _V.vocab_violations({"source_qualification": "SOURCE_RESOLVED"}))
    # ★ 목록형 필드(blocked_by)도 같은 방식으로 막히는지 — 스칼라만 막고 끝내지 않는다.
    check("④ blocked_by 원소 오타도 거부한다",
          bool(_V.vocab_violations({"blocked_by": ["DATA_MISSNG"]})))
    check("④ 정상 blocked_by 는 거부하지 않는다",
          not _V.vocab_violations({"blocked_by": ["DATA_MISSING", "SOURCE_UNRESOLVED"]}))
    check("④ 선언이 derive_blocked_by 출력과 일치한다", _V.covers_derive_outputs() == [],
          str(_V.covers_derive_outputs()))

# ══════════════════════════════════════════════════════════════════════
print("FI-3 frozen input tamper — ★ KNOWN GAP / NOT GATED")
print("  현재 계약은 `_watchlist_rows.json` · `decompose_full.b1_frozen.json` 의")
print("  파일 단위 변조를 일반적으로 차단하지 못한다. `b1_frozen.sha256` 은 산출물에")
print("  기록만 되고 대조되지 않는다. ⛔ 통과시키려고 새 hash 정책을 만들지 않는다 —")
print("  D-4~D-7 과 같이 미검증 영역으로 명시 기록한다.")


# ══════════════════════════════════════════════════════════════════════
print("REPRO · 실행 이력이 authoritative bytes 를 오염시키지 않는다")

AUTHORITATIVE = [
    "config/rules.candidates.json", "rules/decompose_full.json",
    "rules/canonical_rules.json", "rules/equivalence_candidates.json",
    "rules/merge_decision.json", "rules/definition_inventory.json",
    "rules/definition_decision.json", "rules/data_source_ambiguity.json",
    "rules/decision_normalization.json", "rules/decision_cards.json",
    "rules/ssot_mapping.json", "config/rules.json",
    "rules/monitoring_identity.json", "rules/rule_inventory.json",
]
BUILD_ORDER = [
    "extract", "build_full_decomposition", "canonicalize",
    "equivalence_candidates", "merge_decision", "definition_inventory",
    "definition_decision", "data_source_ambiguity", "decision_normalization",
    "decision_cards", "ssot_mapping", "promote_rules_ssot",
    "monitoring_identity", "rule_inventory",
]


def build_chain(d):
    for s in BUILD_ORDER:
        r = run([f"rules/{s}.py"], d)
        if r.returncode != 0:
            return s, (r.stdout + r.stderr)[-400:]
    return None, None


def chain_hashes(d):
    return {p: sha(os.path.join(d, p)) for p in AUTHORITATIVE}


_scratch = _existing = _stale = None

with tempfile.TemporaryDirectory(prefix="repro_a_") as d:
    clone(d)
    for p in AUTHORITATIVE:                      # ① 출력 파일이 없을 때
        os.unlink(os.path.join(d, p))
    bad, why = build_chain(d)
    check("① from-scratch 빌드가 성공한다", bad is None, f"{bad}: {why}")
    if bad is None:
        _scratch = chain_hashes(d)

with tempfile.TemporaryDirectory(prefix="repro_b_") as d:
    clone(d)                                     # ② 출력 파일이 있을 때 재빌드
    bad, why = build_chain(d)
    check("② existing-output 재빌드가 성공한다", bad is None, f"{bad}: {why}")
    if bad is None:
        _existing = chain_hashes(d)

with tempfile.TemporaryDirectory(prefix="repro_c_") as d:
    clone(d)                                     # ③ 이전 형태 파일이 놓여 있을 때
    canon = os.path.join(d, "rules", "canonical_rules.json")
    doc = json.load(open(canon, encoding="utf-8"))
    doc["assignment"] = {"reused": 0, "new": 25}     # 제거된 실행 이력 필드를 되살림
    mon = os.path.join(d, "rules", "monitoring_identity.json")
    mdoc = json.load(open(mon, encoding="utf-8"))
    mdoc["counts"] = dict(mdoc["counts"], unchanged=0, newly_assigned=16)
    with open(canon, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(mon, "w", encoding="utf-8") as f:
        json.dump(mdoc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    check("③ 주입으로 이전 형태가 실제로 놓였다",
          sha(canon) != (_scratch or {}).get("rules/canonical_rules.json"))
    bad, why = build_chain(d)
    check("③ 이전 형태 위에서도 빌드가 성공한다", bad is None, f"{bad}: {why}")
    if bad is None:
        _stale = chain_hashes(d)

check("★ ①②③ 세 경우의 authoritative bytes 가 전부 같다",
      _scratch is not None and _scratch == _existing == _stale,
      str([p for p in AUTHORITATIVE
           if _scratch and _existing and _stale
           and not (_scratch[p] == _existing[p] == _stale[p])][:3]))
check("실행 이력 필드가 authoritative payload 에 없다 — canonical",
      "assignment" not in json.load(
          open(os.path.join(ROOT, "rules/canonical_rules.json"), encoding="utf-8")))
check("실행 이력 필드가 authoritative payload 에 없다 — monitoring identity",
      not ({"unchanged", "newly_assigned"} & set(json.load(
          open(os.path.join(ROOT, "rules/monitoring_identity.json"),
               encoding="utf-8"))["counts"])))

# ══════════════════════════════════════════════════════════════════════
print("GUARD · authoritative rebuild 는 disposable checkout 밖에서 실행되지 않는다")
with tempfile.TemporaryDirectory(prefix="guard_") as d:
    clone(d)
    before = {p: sha(os.path.join(d, p)) for p in
              ("config/rules.json", "rules/rule_inventory.json",
               "rules/canonical_rules.json", "rules/decision_cards.json")}

    r = run(["run_all.py", "--no-fi", "--authoritative"], d, disposable=False)
    out = r.stdout + r.stderr
    check("① 선언 없이 authoritative 를 열면 차단된다",
          "ATLAS_DISPOSABLE_CHECKOUT=1 이 아니다" in out, out[-300:])
    check("① 어떤 파일도 건드리지 않았다고 보고한다",
          "어떤 파일도 건드리지 않았다" in out)
    check("② non-zero", r.returncode != 0, str(r.returncode))
    check("③ ★ mutation 전에 멈췄다 — 사본의 산출물이 전부 그대로다",
          all(sha(os.path.join(d, p)) == v for p, v in before.items()))

    r2 = run(["run_all.py", "--no-fi"], d, disposable=False)
    out2 = r2.stdout + r2.stderr
    check("기본 실행은 inspection mode 다", "inspection mode" in out2)
    check("inspection mode 는 조건 ② 를 검증하지 않는다고 명시한다",
          "조건 ② 를 검증하지 않는다" in out2)
    check("inspection mode 도 파일을 건드리지 않는다",
          all(sha(os.path.join(d, p)) == v for p, v in before.items()))
    check("inspection mode 는 Actions PASS 로 보고하지 않는다",
          "Actions PASS = NO" in out2 and r2.returncode != 0)

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
