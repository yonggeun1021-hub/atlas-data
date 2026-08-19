#!/usr/bin/env python3
"""Actions runner — Atlas B1 migration package.

CIO 판정 2026-08-15 로 확정된 `Actions PASS` 계약 네 가지를 기계적으로 검증한다.

  ① clean checkout 에서 승인된 회귀 전량 실행, 0 FAIL
  ② 승인 산출물을 builder ①→⑭ 직렬 재빌드해 committed 본과 **byte-identical**
  ③ 기존 fail-closed / authority / evaluator / Production HOLD /
     Inventory-Population / RULE-MON identity 경계 유지 (①이 담당)
  ④ 별도 승인된 Fault Injection suite PASS

★ 이 runner 가 orchestration authority 다.
  builder 마다 종료 방식을 통일하지 않는다 — uncaught exception · 명시적 non-zero ·
  검증 오류 · 산출물 미생성 · byte 불일치를 **여기서** 종합해 최종 non-zero 를 낸다.

★ 재빌드는 **사본 보존 방식**이다 (CIO 판정 — (나) API 개조는 기각).
  committed 산출물을 먼저 byte-for-byte 사본으로 떠 두고, 정상 경로에서 재빌드한 뒤
  사본과 비교한다. builder 에 범용 out_path/input injection 을 추가하지 않는다.
  ⛔ 비교 기준은 bytes 동일성이다. SHA-256 은 표시·진단용으로만 쓴다.

⛔ 이 runner 가 하지 않는 것 — Production 상태 변경 · evaluator 배선 ·
   `consumable_by_evaluator` 전환 · 산출물 의미 보정 · 실패 자동 복구.
"""
from __future__ import annotations

import filecmp
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# ══════════════════════════════════════════════════════════════════════
# 계약 상수 — 전부 CIO 판정에서 왔다. 여기서 새로 만들지 않는다.
# ══════════════════════════════════════════════════════════════════════

# ★ builder 직렬 실행 순서 ①→⑭ (CIO 확정). DAG 의 유효한 topological ordering 하나를
#   실행 순서로 **선택**한 것이며 dependency 를 새로 만든 것이 아니다.
BUILDERS = [
    ("rules/extract.py", "config/rules.candidates.json"),
    ("rules/build_full_decomposition.py", "rules/decompose_full.json"),
    ("rules/canonicalize.py", "rules/canonical_rules.json"),
    ("rules/equivalence_candidates.py", "rules/equivalence_candidates.json"),
    ("rules/merge_decision.py", "rules/merge_decision.json"),
    ("rules/definition_inventory.py", "rules/definition_inventory.json"),
    ("rules/definition_decision.py", "rules/definition_decision.json"),
    ("rules/data_source_ambiguity.py", "rules/data_source_ambiguity.json"),
    ("rules/decision_normalization.py", "rules/decision_normalization.json"),
    ("rules/decision_cards.py", "rules/decision_cards.json"),
    ("rules/ssot_mapping.py", "rules/ssot_mapping.json"),
    ("rules/promote_rules_ssot.py", "config/rules.json"),
    ("rules/monitoring_identity.py", "rules/monitoring_identity.json"),
    ("rules/rule_inventory.py", "rules/rule_inventory.json"),
]

# byte 비교 대상 = 위 14개 산출물.
#   ⛔ decompose_pilot.json · populations.json 은 비권위 view 이므로 제외한다
#      (CIO 판정 13). 저장소 보존은 하되 authority 산출물처럼 취급하지 않는다.
COMPARED = [out for _, out in BUILDERS]
NON_AUTHORITY_VIEWS = ["rules/decompose_pilot.json", "rules/populations.json"]

# ★ 승인 회귀 목록. 이 목록과 실제 test/test_*.py 집합이 다르면 FAIL 이다.
#   ⛔ FI suite 는 여기 섞지 않는다 (CIO 판정 9).
APPROVED_TESTS = [
    # ★ P1-CR-02 — Stablecoin endpoint / revision / PIT contract.
    #   historical chart revision·reindex·backfill·append와 live snapshot 변화를
    #   분리하고 append-only provenance manifest를 검증한다.
    #   ⛔ live DefiLlama 호출 없음 — committed evidence read-only + temp fixtures.
    "test/test_stablecoin_revision_contract.py",
    # ★ P0-02 — Daily Collect scheduler self-observability.
    #   slot/run identity, runner-start delay, Guard result/skip을 작은 telemetry로
    #   남기며 manual/unknown schedule은 지연을 추정하지 않는다.
    #   ⛔ live GitHub/KRX 호출 없음 — temp output root에서 production helper 검증.
    "test/test_p002_scheduler_telemetry.py",
    "test/test_canonical_identity.py",
    "test/test_data_source_ambiguity.py",
    "test/test_decision_cards.py",
    "test/test_decision_normalization.py",
    "test/test_decomposition_pilot.py",
    "test/test_definition_decision.py",
    "test/test_definition_inventory.py",
    "test/test_equivalence.py",
    "test/test_full_decomposition.py",
    "test/test_merge_decision.py",
    "test/test_rule_inventory.py",
    "test/test_rules_extract.py",
    "test/test_rules_ssot.py",
    "test/test_ssot_mapping.py",
    # ★ P0-03 — briefing read model 회귀.
    #   Step 0 summary/source hash/date-basis, KRX tail-symbol exact view,
    #   bounded SEC view, truncated JSON fail-closed 계약을 검증한다.
    #   ⛔ live network 없음 — committed local data 기반.
    "test/test_briefing_inputs.py",
    # ★ P0-03 hardening — Daily Collect workflow repair-path 계약.
    #   Guard=fresh 는 collector만 skip하고 briefing read model은 검증/repair를 계속한다.
    #   ⛔ live network 없음 — workflow YAML 구조만 실제 파싱해 검증한다.
    "test/test_p003_workflow_contract.py",
    # ★ CIO 승인 2026-08-15 — TSMC Monthly Revenue collector pilot 회귀 추가.
    #   승인 목록은 늘어날 수 있다(테스트 삭제·누락만 FI-4 가 잡는다).
    "test/test_tsmc_monthly.py",
    # ★ CIO 승인 2026-08-15 — C4 SEC EDGAR parser 회귀 추가.
    #   ⛔ live network 호출은 넣지 않는다 — fixture 기반 결정론적 회귀만 승인 목록에 든다.
    "test/test_c4_sec_edgar.py",
    # ★ CIO 승인 2026-08-15 — RULE-0021 Azure cc 추출 회귀 추가.
    #   ⛔ live network 호출은 넣지 않는다 — fixture 기반 결정론적 회귀만 든다.
    "test/test_msft_azure_cc.py",
    # ★ CIO 판정 2026-08-16 항목 5 — fixture 슬라이서 회귀.
    #   이 회귀는 **슬라이서의 성질**(원문 부분 문자열 · 표 여닫이 균형 · fail-closed)만
    #   검증한다. 추출 계약은 검증하지 않는다 — 그것은 실제 fixture 확보 후다.
    #   ⛔ CIO 가 이 파일 자체를 아직 승인한 적은 없다. 목록에 넣지 않으면 test-set
    #      대조에서 「미승인」으로 잡히므로 숨기지 않고 등록한 뒤 보고한다.
    "test/test_capture_azure_fixture.py",
    # ★ CIO 승인 2026-08-16 — TSMC raw fixture capture 회귀.
    #   ⛔ C4 parser 를 검증하지 않는다. capture 도구의 성질만 본다.
    #   ⛔ 이 등록은 CIO 확인 대상이다 — 목록에 넣지 않으면 test-set 대조에서
    #      「미승인」으로 잡히므로 숨길 수 없다.
    "test/test_capture_tsmc_fixture.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S1 · RULE-0022 Commercial RPO observer.
    #   증명: FY26 4건 row exactly-one → GAAP raw 관측 / FY25 4건 row exactly-zero →
    #        ROW_ABSENT (D-6) / title·row·column 0건·복수건 fail-closed /
    #        observer 가 `msft_azure_cc` 를 import 하지 않는다 (RULE-0021 격리).
    #   ⛔ live network 없음 — fixture only.
    #   ⛔ normalization · store · pair · evaluator 는 검증하지 않는다 (S2 이후 Gate).
    "test/test_rule0022_commercial_rpo.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S2 · 층 ③ Normalization + Record.
    #   증명: 승인 percent 표기 → exact Decimal · sign_convention 보존 /
    #        malformed fault matrix 전건 fail-closed / raw 문면 보존 /
    #        numeric 문자열 직렬화 · float 부재 / CC·impact evidence-only /
    #        record invariant 전건 fail-closed / 층 순서(observer 는 이 층을 모른다).
    #   ⛔ live network 없음 — fixture only.
    #   ⛔ store · pair · evaluator 는 검증하지 않는다 (S3 이후 Gate).
    "test/test_observation_normalize.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S3 · 층 ④ Observation Store.
    #   증명: key = subject+measurement+period 세 축 / 첫 동작이 validate_record /
    #        D-6 경계 PRE_SERIES_BACKFILL_FORBIDDEN / IDEMPOTENT·CONFLICT·REVISION 분리 /
    #        조용한 overwrite·revision 삭제·authority 자동선택 없음 /
    #        deterministic serialization / store 가 Git·workflow·evaluator 를 모른다.
    #   ⛔ live network 없음 — fixture only.
    #   ⛔ pair · runtime state · evaluator 는 검증하지 않는다 (S4 이후 Gate).
    "test/test_observation_store.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S4A · Integration Wiring (offline).
    #   증명: observe/persist 물리적 분리(AST) / observe 는 저장소 밖으로만 emit /
    #        FY26 4건 end-to-end(draft 4 → record 4 → Store NEW 4) / 재적용 IDEMPOTENT /
    #        malformed·pre-series·conflict·revision·observe 실패 fault injection /
    #        workflow 계약 순서.
    #   ⛔ live network 없음 · dispatch 없음 — fixture only (S4B 미승인).
    "test/test_rule0022_integration.py",
    # ══════════════════════════════════════════════════════════════════
    # ★ CIO 승인 2026-08-17 — WS1~WS4 integration patch.
    #   base `bc18bb0` 에 1-WS1 → 2-WS3 → 3-WS4 → 4-WS2 순으로 적용해
    #   `1,590 checks / 0 FAIL / 0 ERROR / 0 SKIPPED` 재현을 확인한 뒤 등록한다.
    #   ⛔ 아래 3개만 신규 등록 대상이다. `test_rule0022_integration.py` 는
    #      기존 승인 테스트의 **수정**이므로 새로 등록하지 않는다.
    # ══════════════════════════════════════════════════════════════════
    # ★ WS3 — Evidence Bridge. 검증된 observation 만 Decision Layer 입구까지
    #   전달하는 **입력 자격 계약**만 검증한다.
    #   ⛔ Rule 판단 · evaluator 배선 · consumable_by_evaluator 전환은 검증하지 않는다.
    #   ⛔ live network 없음 — fixture only.
    "test/test_evidence_envelope.py",
    # ★ WS4 — Briefing Adapter. 확인된 사실과 투자판정/행동의 **분리**만 검증한다.
    #   ⛔ 브리핑 문안이나 투자 판정 내용 자체는 검증 대상이 아니다.
    #   ⛔ live network 없음 — fixture only.
    "test/test_briefing_evidence_adapter.py",
    # ★ WS2 — rule0022-observation workflow 계약. 실제/연습 source 명시 선택 ·
    #   모순 입력 fail-closed · parameter application guard 를 **워크플로 정의
    #   자체**에 대해 검증한다.
    #   ★ PyYAML 로 워크플로를 파싱한다 (CIO 판정 2026-08-17 — YAML 계약 검증을
    #     수제 parser 나 문자열 검색으로 낮추지 않는다). CI 의존성은
    #     `requirements-ci.txt` 에 정식 선언한다.
    #   ⛔ 이 회귀는 workflow 를 **실행하지 않는다** — 정의만 읽는다. dispatch 없음.
    "test/test_rule0022_workflow_contract.py",
]

FI_SUITE = "test/test_fault_injection.py"

# ★ Production / evaluator 경계 — 이 실행으로 바뀌면 안 되는 값.
FROZEN_BOUNDARY = {
    "config/rules.json": {"authority": True, "consumable_by_evaluator": False},
    "rules/rule_inventory.json": {"authority": False,
                                  "consumable_by_evaluator": False},
}

# ══════════════════════════════════════════════════════════════════════
# ★ authoritative mode 는 **파괴적**이다 — 정상 경로에서 재빌드하므로 작업 트리의
#   committed 산출물을 덮어쓴다. disposable clean checkout(= Actions) 에서만 허용한다.
#   ⛔ 자동 restore/rollback 을 넣지 않는다 — 검증기가 작업 트리 mutation manager 가
#      되면 어느 쪽이 원본인지 판단하는 주체가 하나 더 생긴다.
#   따라서 막는 방식은 하나뿐이다: **mutation 이 일어나기 전에 fail-closed 한다.**
DISPOSABLE_ENV = "ATLAS_DISPOSABLE_CHECKOUT"


def disposable_checkout_proof():
    """authoritative mode 를 열어도 되는지. 증명하지 못하면 열지 않는다."""
    problems = []
    if os.environ.get(DISPOSABLE_ENV) != "1":
        problems.append(
            f"{DISPOSABLE_ENV}=1 이 아니다 — 이 실행 환경이 버려도 되는 checkout 이라는 "
            f"선언이 없다. Actions workflow 가 이 값을 설정한다")
    # git worktree 가 있으면 깨끗해야 한다. 더러우면 잃을 것이 있다는 뜻이다.
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            problems.append("git worktree 가 dirty 하다 — 재빌드가 덮어쓸 변경이 있다")
    except FileNotFoundError:
        pass          # git 이 없으면 이 축으로는 판정하지 않는다
    return problems


SNAPSHOT_DIR = "_committed_snapshot"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class Runner:
    def __init__(self):
        self.failures = []
        self.lines = []

    def fail(self, stage, msg):
        self.failures.append(f"[{stage}] {msg}")

    def say(self, msg):
        self.lines.append(msg)
        print(msg, flush=True)

    # ── ② 사본 보존 ──────────────────────────────────────────────────
    def snapshot(self, dest):
        missing = [p for p in COMPARED if not os.path.exists(os.path.join(ROOT, p))]
        if missing:
            for p in missing:
                self.fail("snapshot", f"committed 산출물이 없다: {p}")
            return {}
        kept = {}
        for p in COMPARED:
            src = os.path.join(ROOT, p)
            dst = os.path.join(dest, p.replace("/", "__"))
            shutil.copyfile(src, dst)          # byte-for-byte
            kept[p] = dst
            if not filecmp.cmp(src, dst, shallow=False):
                self.fail("snapshot", f"사본이 원본과 다르다: {p}")
        return kept

    # ── builder 직렬 실행 ────────────────────────────────────────────
    def rebuild(self):
        for i, (script, out) in enumerate(BUILDERS, 1):
            r = subprocess.run([PY, script], cwd=ROOT, capture_output=True, text=True)
            tag = f"{i:02d} {script}"
            if r.returncode != 0:
                self.fail("rebuild", f"{tag} → exit {r.returncode}\n"
                                     f"{(r.stderr or r.stdout).strip()[-600:]}")
                return False           # 순서가 의미를 가지므로 즉시 중단한다
            if not os.path.exists(os.path.join(ROOT, out)):
                self.fail("rebuild", f"{tag} → 산출물 미생성: {out}")
                return False
            self.say(f"  {tag} ok")
        return True

    # ── ② byte 비교 ─────────────────────────────────────────────────
    def compare(self, kept):
        same = 0
        for p, snap in kept.items():
            cur = os.path.join(ROOT, p)
            if not os.path.exists(cur):
                self.fail("compare", f"재빌드 산출물이 없다: {p}")
                continue
            if filecmp.cmp(snap, cur, shallow=False):
                same += 1
            else:
                self.fail("compare",
                          f"committed 와 재빌드가 다르다: {p}\n"
                          f"        committed {sha(snap)[:16]} / rebuilt {sha(cur)[:16]}")
        self.say(f"  byte-identical {same}/{len(kept)}")
        return same == len(kept)

    # ── ①③ 승인 회귀 ───────────────────────────────────────────────
    def approved_tests(self):
        actual = sorted("test/" + f for f in os.listdir(os.path.join(ROOT, "test"))
                        if f.startswith("test_") and f.endswith(".py"))
        expected = sorted(APPROVED_TESTS + [FI_SUITE])
        if actual != expected:
            self.fail("test-set",
                      f"승인 목록과 실제 test 집합이 다르다\n"
                      f"        누락 {sorted(set(expected) - set(actual))}\n"
                      f"        미승인 {sorted(set(actual) - set(expected))}")
            return False
        ok = True
        for t in APPROVED_TESTS:
            r = subprocess.run([PY, t], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                ok = False
                self.fail("regression",
                          f"{t} → exit {r.returncode}\n"
                          f"{(r.stdout or r.stderr).strip()[-600:]}")
            else:
                self.say(f"  {t} ok")
        return ok

    # ── ④ Fault Injection ───────────────────────────────────────────
    def fault_injection(self):
        r = subprocess.run([PY, FI_SUITE], cwd=ROOT, capture_output=True, text=True)
        print(r.stdout, end="", flush=True)
        if r.returncode != 0:
            self.fail("fault-injection",
                      f"{FI_SUITE} → exit {r.returncode}\n"
                      f"{(r.stdout or r.stderr).strip()[-600:]}")
            return False
        return True

    # ── Production / evaluator 경계 ─────────────────────────────────
    def boundary(self):
        import json
        ok = True
        for path, expect in FROZEN_BOUNDARY.items():
            d = json.load(open(os.path.join(ROOT, path), encoding="utf-8"))
            for k, v in expect.items():
                if d.get(k) is not v:
                    ok = False
                    self.fail("boundary", f"{path}: {k} 가 {d.get(k)!r} 다 (기대 {v!r})")
        inv = json.load(open(os.path.join(ROOT, "rules/rule_inventory.json"),
                             encoding="utf-8"))
        if inv["counts"]["evaluator_consumable"] != 0:
            ok = False
            self.fail("boundary", "Evaluator Consumable 이 0 이 아니다")
        if "HOLD" not in inv["production_state"]:
            ok = False
            self.fail("boundary", "Production HOLD 표기가 사라졌다")
        return ok


def main():
    r = Runner()
    print("Atlas Actions runner — Python", sys.version.split()[0])
    print(f"⛔ Production HOLD · evaluator 미연결 · 이 실행은 상태를 바꾸지 않는다\n")

    authoritative = "--authoritative" in sys.argv
    with tempfile.TemporaryDirectory(prefix="atlas_committed_") as snap_dir:
        if not authoritative:
            print("[1-3/5] rebuild · byte 비교 — 건너뜀 (inspection mode)")
            print("        ★ authoritative rebuild 는 파괴적이라 disposable clean")
            print("          checkout 에서만 실행한다. `--authoritative` 로 요청하고")
            print(f"          {DISPOSABLE_ENV}=1 로 그 환경임을 선언한다.")
            r.fail("mode", "inspection mode 는 Actions PASS 조건 ② 를 검증하지 않는다")
            kept = {}
        else:
            blockers = disposable_checkout_proof()
            if blockers:
                # ★ 여기서 멈춘다 — 사본을 뜨기 전, 재빌드가 파일을 건드리기 전이다.
                for b in blockers:
                    r.fail("guard", b)
                print("[1-3/5] ⛔ authoritative rebuild 차단 — 어떤 파일도 건드리지 않았다")
                kept = {}
            else:
                print("[1/5] committed 산출물 사본 보존")
                kept = r.snapshot(snap_dir)

                if kept:
                    print("[2/5] builder ①→⑭ 직렬 재빌드")
                    r.rebuild()

                    print("[3/5] committed ↔ rebuilt byte 비교")
                    r.compare(kept)

        print("[4/5] 승인 회귀 14파일")
        r.approved_tests()

        # ★ `--no-fi` 는 **Fault Injection suite 전용** 스위치다. FI-1 · FI-4 는 이
        #   runner 자체를 사본에서 실행해 Gate 동작을 검증하는데, 그 사본이 다시 FI
        #   suite 를 부르면 무한 재귀가 된다. Actions 는 이 스위치 없이 실행한다.
        if "--no-fi" in sys.argv:
            print("[5/5] Fault Injection suite — 건너뜀 (--no-fi, FI 내부 실행)")
        else:
            print("[5/5] Fault Injection suite")
            r.fault_injection()

        r.boundary()

    print()
    if r.failures:
        print(f"⛔ FAIL — {len(r.failures)}건")
        for f in r.failures:
            print("  •", f)
        print("\nActions PASS = NO")
        return 1
    print("✅ Actions PASS = YES")
    print("   ⛔ 단, 이것은 CI 통과이지 Production 승인도 evaluator 승인도 아니다.")
    print("   ★ FI-3 frozen input tamper = KNOWN GAP / NOT GATED (미검증 영역)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
