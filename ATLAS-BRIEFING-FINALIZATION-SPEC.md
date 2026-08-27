# Atlas Briefing Finalization Gate — 계약 스펙 `briefing_finalization/17`

rev 18 · 2026-08-27 · **Core `ACCEPTED` · B `ACCEPTED` · per-stream authority `ACCEPTED` · workflow wiring `GO`**
rev 17 receipt binding `ACCEPTED`. rev 18 = **receipt revision authority** 1건.

### CONTRACT_VERSION 규칙 (P1 수렴)

`CONTRACT_VERSION` 은 **아티팩트 스키마와 해석 의미론 둘 다**를 포함한다.
rev 12 는 필드를 하나도 안 바꾸고 authority 해석만 stream 별로 바꿨는데 버전이 그대로여서,
서로 호환되지 않는 두 해석이 같은 라벨을 달 뻔했다. **의미론만 바뀌어도 올린다.**
이 rev 는 **B(결정론적 validator) 단독** + rev 8 P1 + 문서 drift 정리다. Core 동작은 바꾸지 않았다.
rev 7 `P0 2건 (inbox authority · post-delivery reseal)` → rev 8.
rev 6 `패처 ACCEPT / CORE REJECT` (P0 3 + P1 1) → rev 7.
rev 4 `SECURITY + STATE BINDING NOT ACCEPTED` → rev 5 `SECURITY CORE IMPROVED, INTEGRATION & ROLLOUT BOUNDARY NOT ACCEPTED` (P0 7) → rev 6.

**rev 6 부터 패치는 live 파일 실측 기반이다.** `.github/workflows/daily-briefing.yml` 원문을 받아
`git hash-object` = **`4bf0fc9ecd27e3affb7c4f85d9f93df5f888dd75`** (12,536 bytes) 로 CIO 제시값과 일치 확인했다.
이전 rev 의 패처는 facsimile 추정으로 작성돼 **실제 main 에는 anchor miss 로 적용조차 되지 않았다.**

> ⛔ 운영 규칙이 아니다. 병합 전까지 어떤 authority 도 열지 않는다.
> ⛔ 이 세션은 저장소에 push 할 수 없다. 산출물은 파일로만 전달한다.
> **이 문서는 rev 1·2 서술을 승계하지 않는다.** 이전 문구와 충돌하면 이 문서가 정본이다.

---

## 1. 이 게이트가 하는 일

기존 H-24 체인 **안에** 끼어든다. 새 브리핑 시스템을 옆에 만들지 않는다.

```
daily_orchestrator.py publish            -> evidence/daily_briefing/{slot}/{date}/rev-NNN/{packet.json,briefing.md}
daily_briefing_delivery.py publish-locator -> data/briefing/daily_briefing_sources.json
daily_briefing_delivery.py consume       -> 렌더된 delivery markdown
>>> briefing_finalization.py  seal -> validate -> deliver <<<
사람 도달 (step summary / kakao / …)
```

`seal` 은 locator 가 지목한 것만 읽는다 — `EXACT_POINTER_ONLY_NO_FALLBACK`.
디렉터리 스캔·전일 fallback·다른 슬롯 추정 전부 금지.

---

## 1-A. 침묵과 고장은 같은 상태가 아니다

**rev 6 은 이 둘을 같게 취급했다.** validator 가 응답을 냈지만 그게 손상됐을 때
ingest 오류를 삼키고, 20분 뒤 `UNVALIDATED_TIMEOUT` 을 발행해 **그대로 발송하고 exit 0** 이었다.

| 상태 | 판정 |
|---|---|
| inbox 파일 **없음** = 침묵 | fail-**OPEN** (20분 실경과 후 `UNVALIDATED_TIMEOUT`) |
| inbox 파일 **있는데 기록된 verdict 0** = malformed · stale · unbound | **`FINALIZATION_VALIDATION_INVALID`** · exit 10 · fail-**CLOSED** |

`deliver` 는 timeout 을 발행하기 직전에 inbox 를 직접 확인한다 — drain 이 오류를 삼켰는지와 무관하게
게이트 자체가 막는다. 회귀 `test_stale_payload_sha_does_not_time_out_into_delivery`,
그리고 fail-open 이 죽지 않았음을 지키는 `test_true_silence_still_times_out`.

## 2. fail 방향 — 세 계층이 서로 다르다

| 계층 | 방향 | 근거 |
|---|---|---|
| **검증** | fail-**OPEN**, 단 `sealed_at_utc` 기준 **실경과 20분 이후에만** | 멈춘 validator 가 브리핑을 영구히 침묵시키면 안 된다. 그러나 1초 만의 timeout 선언도 안 된다 |
| **CIO Gate** | fail-**CLOSED** | 승인 없이 사람에게 도달하면 Gate 가 아니다 |
| **Transport** | fail-**CLOSED** | 보내지 않았는데 보냈다는 증거를 만들면 안 된다. receipt 가 없으므로 재시도 가능 |
| **Reconcile** | fail-**CLOSED (사람에게)** | 도달 여부가 불명이면 추측하지 않는다 |
| **HOLD** | fail-**CLOSED (무조건)** | `HOLD` 는 어떤 경로로도 사람에게 가지 않는다 |
| **Durable intent** | fail-**CLOSED** | intent 가 remote 에 없으면 전송 자체를 하지 않는다 |

「발송 0건은 정상 출력이 아니다」는 유지하되, **receipt 위조가 아니라 backlog drain 재시도로** 달성한다.

---

## 3. `investment_conclusion_changed` — 여전히 `UNKNOWN`, 그리고 이번엔 우회 불가

**rev 4 는 이 계약이 열려 있었다.** `spec_version` 이 non-null 이기만 하면 신뢰해서,
verdict 에 `"evil/999"` 를 적고 changed 필드를 `false` 로 넣으면
`auto_apply_allowed=true` · `cio_gate_required=false` 가 됐다.
**심사받는 산출물이 자기 심사 권한을 자기가 선언하는 구조**였다.

rev 5 의 이중 잠금:

1. `PHASE_A_AUTO_APPLY_DISABLED = True` — **Phase A 에서는 무조건 auto-apply 불가.**
   이 상수를 뒤집는 것은 코드 수정이 아니라 비준 행위다.
2. `config/atlas_conclusion_diff_allowlist.json` — 비준된 spec 목록을 **저장소에서 로컬로** 읽는다.
   verdict 가 자기 spec 을 뭐라고 부르든 이 목록에 없으면 권한이 없다. 현재 목록은 **비어 있다.**

회귀 `test_invented_spec_version_grants_nothing`, `test_even_a_ratified_spec_is_inert_while_phase_a_holds`.


Cockpit 정본상 `Discovery 등재 요건` · `Stage 승격/강등 조건` · `ENTRY_LANGUAGE` ·
`INVALIDATION` · `POSITION_POLICY` · `L1/L2/L3 기계적 정의` 가 전부 `Undefined` 다.
「투자결론이 무엇으로 구성되는가」가 비준되기 전에 그 분기를 코드로 만들면
Decision Boundary 가 금지한 **현장에서 정의 만들기**가 된다.

`conclusion_diff.spec_version` 이 `null` 인 동안:
- `investment_conclusion_changed` = **`"UNKNOWN"`** (validator 가 `false` 를 주장해도 **파생 규칙이 덮어쓴다**)
- `auto_apply_allowed` = **`false`** (영구)
- 정정이 1건이라도 있으면 **CIO Gate 필수**
- 정정 0건이면 Gate 없이 확정 → **오늘부터 바로 유용하다**

Phase B(자동정정 · Portal 자동 projection)는 비준된 `conclusion_diff/N` 이 병합될 때 열린다.

---

## 4. 아티팩트 레이아웃 (append-only)

```
data/briefing/finalization/{KST_DATE}/{slot}/
  draft-rev-NNN.json        locator 바인딩 + payload 해시 + delivery_marker
  payload-rev-NNN.md        사람에게 갈 실제 bytes (봉인)
  validation-inbox.json     validator verdict 투입구 (단일)
  validation-inbox-rev-NNN.json  동 append-only 형식 — 여러 verdict 를 순서대로 넣을 때
  delivery_progress.json    채널별 proof 누적 · payload 에 바인딩됨
  progress-superseded-*.json  reseal 로 무효화된 이전 payload 의 progress (격리 보관)
  validation-rev-NNN.json   기록된 verdict + 파생 routing
  approval-rev-NNN.json     Ed25519 서명된 CIO 승인 (private key 는 CIO 로컬에만)
  delivery_intent.json      transport 직전 기록 · OPEN → CLOSED
  delivery_receipt.json     1회만 · 이후 불변 · 재발송 영구 차단
  corrections.jsonl         사후 정정 append-only
  corrections_surfaced.json 정정 고지 watermark (1회만 노출)

data/briefing/finalization/approval_trust_log.jsonl   어떤 키가 무엇을 승인했는지 append-only
```

`slot` ∈ `morning` | `evening` (기존 어휘 그대로). `briefing_id` = `{KST_DATE}-{am|pm}`.

---

## 4-A. 이 rev 가 상정하는 장애모델 — fresh runner

rev 3 의 exactly-once 논증은 **같은 runner 파일시스템이 살아 있는 retry** 를 가정했다.
GitHub Actions 의 실제 장애모델은 **새 runner · 새 checkout** 이고, 거기서는 push 된 것만 존재한다.
CIO 재현에서 실제 메시지 2건이 발생한 원인이 이것이다.

따라서 rev 4 의 순서는:

```
intent 기록 → intent commit+push (같은 프로세스 안) → push 확인 → 전송 → progress/receipt 기록 → push
```

push 가 확인되지 않으면 **전송하지 않는다**(`FINALIZATION_INTENT_NOT_DURABLE`, exit 7).
publish 자체가 실패해도 전송하지 않는다(`FINALIZATION_INTENT_PUBLISH_FAILED`).

**intent publish 를 workflow step 으로 쪼개면 안 된다.** 쪼개면 다음 프로세스가
「아직 안 보냄」과 「보냈을 수도 있음」을 구분하지 못해 unprobeable 채널의 첫 시도마다 오탐 escalate 한다.
그래서 publisher 는 `deliver` 프로세스 안에서 호출된다.

## 5. exactly-once 는 어떻게 보장되는가 — 그리고 어디까지만 보장되는가

**rev 2 의 「receipt 가 있으니 절대 중복 없음」은 틀렸다.**
transport 쓰기와 receipt 쓰기는 원자적으로 묶을 수 없다. 그 사이 crash 나 이후 push 실패가 나면
**사람에게는 도달했는데 main 에는 기록이 없는** 상태가 남는다.

rev 3 의 처리:

1. transport **직전에** `delivery_intent.json`(`state: OPEN`)을 기록한다.
2. 성공하면 receipt 를 쓰고 intent 를 `CLOSED` 로 닫는다.
3. 다음 시도에서 **OPEN intent + receipt 없음**을 발견하면 추측하지 않고 채널별로 판정한다:

| 판정 | 조건 | 행동 |
|---|---|---|
| `PROBED_DELIVERED` | 전송물에 `delivery_marker` 가 이미 있음 | 그 채널 **건너뜀** |
| `PROBED_NOT_DELIVERED` | 읽어봤는데 marker 없음 | 재전송 |
| `RESEND_SAFE` | probe 불가 + 채널이 **run 단위 스코프**(job log) | 재전송 |
| **`RECONCILE_PENDING`** | probe 불가 + 사람에게 push 하는 채널 | **중단 · exit 6 · 사람 판단 대기** |

`delivery_marker` = `<!-- atlas-delivery-id: {briefing_id}/rev-NNN -->` 를 payload 안에 넣어
전송물을 되읽을 수 있는 채널에서 probe 가 성립하게 한다.

**한계를 명시한다:** 멱등 키가 없는 전송(카카오 memo API 등)에서
crash 후 도달 여부를 기계가 판정할 방법은 없다. rev 3 은 그 경우 **자동 재발송을 하지 않고 멈춘다.**
이것이 「중복 발송 없음」을 지킬 수 있는 유일한 정직한 방법이다.

---

## 5-A. 채널 의미론 — required / fidelity / 전송 bytes

- **`required_channels`** — 필수 채널이 하나라도 미달이면 **receipt 를 쓰지 않는다.**
  rev 3 은 step summary 만 성공해도 global receipt 를 만들어 이후 전 채널을 `ALREADY_DELIVERED` 로 영구 봉쇄했다.
  이제 채널별 proof 는 `delivery_progress.json` 에 누적되고, 재시도는 **미달 채널만** 시도한다.
- **`payload_fidelity`** — `FULL` 은 봉인 payload 를 그대로 전송, `SUMMARY` 는 손실 전송.
  카카오는 API 길이 제한이 있어 구조적으로 `SUMMARY` 이며, **전문 전달을 증명하지 못한다.**
- **전송 bytes 해시** — proof 마다 `transmitted_sha256` · `transmitted_bytes` · `covers_full_payload` 를 기록한다.
  receipt 의 `sealed_payload_sha256` 은 봉인물 해시이고 전송물 해시와 **별개 필드**다.
  rev 3 은 카카오가 1000자로 자른 것을 보내면서 전체 payload 해시를 receipt 에 적어 계약이 깨졌다.
- **marker 위치** — 카카오 메시지는 marker 를 **맨 앞**에 둔다. 뒤에 두면 잘려나가 probe 가 불가능해진다.

## 6. validation 바인딩 (P0-4)

`delivery_payload_sha256` 은 **필수**다. 없으면 `FINALIZATION_VALIDATION_PAYLOAD_UNBOUND` 로 거부.
rev 2 에서는 optional 이라, reseal 이후 늦게 도착한 낡은 verdict 가 최신 draft 에 자동으로 붙는
TOCTOU 구멍이 있었다. 회귀 테스트 `test_19`, `test_20`.

## 6-A. 상태 자체가 게이트다 (P0-3 / P0-4)

| status | 사람에게 도달 | 제출 주체 |
|---|---|---|
| `PASS` | 가능 | 외부 validator |
| `PASS_WITH_CORRECTION` | 가능 (정정 있으면 CIO Gate 경유) | 외부 validator |
| **`HOLD`** | **불가 — 무조건 fail-closed** (exit 8) | 외부 validator |
| `UNVALIDATED_TIMEOUT` | 가능 | **내부 전용.** `deliver` 가 실경과 시간을 잰 뒤에만 발행 |

`UNVALIDATED_TIMEOUT` 을 외부에서 제출하면 `FINALIZATION_STATUS_NOT_EXTERNALLY_SUBMITTABLE` 로 거부한다.
inbox 경유도 같은 경로를 타므로 20분 타임아웃을 우회할 수 없다.

## 7. approval 신뢰경계 (P0-5)

`approved_by: "CIO"` 라고 쓴 JSON 은 승인이 아니다 — repo write 권한자면 누구나 만든다.
**rev 3 의 HMAC 은 경계가 될 수 없었다** — 검증할 수 있는 주체는 위조도 할 수 있고,
키를 안 주면 검증조차 못 한다. rev 4 는 **Ed25519 비대칭 서명**을 쓴다.

```
message   = "{briefing_id}|{payload_sha256}|{validation_rev}|{approved_by}"
signature = Ed25519-Sign(CIO private key, message)
```

| | 위치 |
|---|---|
| **private key** | **CIO 로컬 머신에만.** 저장소·CI·secret 어디에도 없다 |
| **public key** | `config/atlas_approval_pubkey.txt` — 저장소에 공개 |

delivery job 은 공개키로 **검증만** 할 수 있고 서명은 구조적으로 불가능하다.
`cryptography` 의존성을 추가하면 CI 계약을 건드리므로 RFC 8032 참조 구현을 `atlas_ed25519.py` 에 인라인했고,
**RFC 8032 §7.1 공식 테스트 벡터로 검증**한다.

승인 발행은 `sign_approval.py` 로 CIO 가 **오프라인**에서 한다.
따라서 CI 안에 승인 발행 job 도, protected environment 도, 추가 `uses:` 도 필요 없다.

### 서명 대상 — 게이트가 보는 모든 필드가 들어간다

**rev 4 는 `decision` 을 서명 밖에 뒀다.** DENY 로 서명한 artifact 의 JSON 에서
`"decision"` 만 `"APPROVE"` 로 바꿔도 서명이 그대로 검증됐다.

```
message = canonical_json({
    contract_version, purpose, briefing_id, decision,
    approves_payload_sha256, approves_validation_rev, approved_by })
```

회귀 `test_deny_cannot_be_edited_into_approve`.

### 공개키의 root-of-trust — 코드가 혼자 만들 수 없다

**지적이 정확하다.** 공개키 파일과 검증 코드를 같은 repo writer 가 고칠 수 있으면
키를 바꿔치고 self-approval 이 통과한다. 실제로 재현하셨다.

**코드는 자기 신뢰근거를 부트스트랩할 수 없다.** 할 수 있는 것은 두 가지뿐이다.

| | 수단 | 성격 |
|---|---|---|
| **차단** | repo secret `ATLAS_APPROVAL_PUBKEY_FINGERPRINT` | repo writer 가 키 파일은 고쳐도 **secret 은 못 고친다.** 지문 불일치 시 `FINALIZATION_APPROVAL_PUBKEY_UNTRUSTED` fail-closed |
| **차단** | `.github/CODEOWNERS` | **advisory 전용** — 강제하는 ruleset 이 없다 (§7 참조) |
| **탐지** | `approval_trust_log.jsonl` + receipt 의 `cio_approval_pubkey_fingerprint` | 키가 바뀌면 **무엇이 어떤 키로 승인됐는지 사후에 드러난다** |

### ⚠ ruleset 접근 자체를 철회한다 — 그리고 보안 주장을 축소한다

rev 5 는 PR ruleset 을 제안했다가 bot 의 direct push(=durable intent)를 막는다는 이유로 철회했고,
rev 6 은 push ruleset + `file_path_restriction` 으로 갈아탔다. **그것도 틀렸다.**

- Atlas 저장소는 **public** 이고, push ruleset 은 private/internal 용이다.
- `Restrict file paths` 는 「그 경로는 PR 로만」이 아니라 **해당 경로를 포함한 push 자체를 차단**한다.
  「data push 는 유지하고 trust root 만 PR 로」라는 rev 6 의 설명은 이 규칙의 의미가 아니었다.
- 그리고 현재 main 은 **protection 이 꺼져 있다.**

⇒ `config/atlas_branch_ruleset.json` 은 **삭제했다.**

**보안 주장을 여기까지로 축소한다:**

| 위협 | rev 7 상태 |
|---|---|
| 공개키 파일 교체 후 self-approval | **차단됨.** `ATLAS_APPROVAL_PUBKEY_FINGERPRINT` 를 **필수화**했다. 미설정이면 `FINALIZATION_APPROVAL_ANCHOR_MISSING` 으로 승인이 거부된다 (rev 6 은 로그만 남기고 통과시켰다) |
| 서명 위조 | 차단됨 (Ed25519, private key 는 CI 밖) |
| DENY→APPROVE 변조 | 차단됨 (decision 이 서명 안에 있음) |
| **검증 코드(`briefing_finalization.py`·`atlas_ed25519.py`) 자체를 고칠 수 있는 repo writer** | **미해결.** 코드가 자기 무결성을 스스로 보증할 수 없고, 지금 저장소에는 이를 막을 branch protection 이 없다 |

**「악의적 repo code writer 까지 막는다」는 주장은 철회한다.** 이 축의 해법은 Finalization 범위가 아니라
**runtime data direct-push 와 code/trust-root 를 분리하는 별도 시스템 작업**이다
(예: data write 전용 branch/repo 를 두고 main 은 PR+CODEOWNERS 로 보호).

`.github/CODEOWNERS` 는 동봉하되 **현재로서는 advisory 다** — 강제하는 ruleset 이 없기 때문이다.

## 6-B. verdict authority — **stream 별로** 최상위 numbered rev

**rev 11 의 결함:** stream 은 나눴는데 authoritative inbox 는 여전히 **전체 최신 파일 하나**였다.
그래서 다른 stream 의 **아직 ingest 되지 않은** verdict 가 「superseded」로 밀려 아예 기록되지 않았고,
명시했던 보증이 **타이밍만으로 무너졌다**:

| 재현 | rev 11 결과 |
|---|---|
| 미기록 `machine HOLD` + 더 최신 `semantic PASS` | PASS 만 기록 → **PASS 발송** (「semantic PASS 는 machine HOLD 를 못 푼다」 파기) |
| 미기록 `semantic HOLD` + 더 최신 `MACHINE_CLEARED` | clear 만 기록 → **governing = None** (「machine 은 자기 것만 푼다」 파기) |

회귀가 이를 못 잡은 이유도 명확하다 — 기존 테스트는 상대 stream verdict 를 **미리 `record_validation` 으로 기록한 뒤** 경쟁시켰다.
두 stream 의 inbox 가 drain 전에 **동시에 쌓이는 interleaving** 을 검사하지 않았다.

rev 12:

```
machine authority  = 최신 machine inbox revision
semantic authority = 최신 semantic inbox revision
governing          = union(machine 최신, semantic 최신)
```

- `ingest_inbox` 는 **각 stream 의 authority 를 각각** 기록한다. 한 stream 의 새 파일이 다른 stream 의 미독 verdict 를 묻지 않는다.
- `resolve_validation` 은 **모든 stream 의 authority 가 기록됐는지** 확인한다. 하나라도 미기록이면 `FINALIZATION_VALIDATION_INVALID` fail-closed.
- **stream 내부 atomicity 는 rev 8 그대로다** — good→bad 면 bad 가 authority 라 fail-closed, bad→good 면 good 으로 복구.
- 파싱 불가 inbox 파일은 **침묵이 아니다** — `unreadable_files` 로 분류하고 fail-closed.
- legacy 단일 파일은 rev 0 으로 정렬되어 같은 stream 의 어떤 numbered revision 에도 밀린다.

새 회귀 8건 (`StreamInterleaving`) 은 **아무것도 미리 기록하지 않고** 두 verdict 를 inbox 에 함께 놓고 drain 한다.

**rev 7 은 inbox 를 순차 side-effect 로 처리해서 양방향 모두 깨졌다.**

| 시나리오 | rev 7 | rev 8 |
|---|---|---|
| good `rev-001` → bad `rev-002` | rev-001 이 먼저 기록되고 rev-002 는 예외. `deliver` 는 「verdict 가 있다」며 **낡은 PASS 로 발송** | `rev-002` 가 authority · 미기록 → **`FINALIZATION_VALIDATION_INVALID` fail-closed** |
| bad `rev-001` → good `rev-002` | 매번 rev-001 에서 죽어 rev-002 에 **영원히 도달 불가** (append-only 라 삭제도 불가) | `rev-002` 가 authority → **복구됨** |

- `authoritative_inbox()` — numbered 가 있으면 **가장 큰 rev 하나만** authority. 나머지는 history(`superseded_files`).
  numbered 가 없을 때만 legacy 단일 파일이 authority.
- `resolve_validation()` — `deliver` 는 「기록된 verdict 가 있는가」가 아니라
  **「authority 가 주장하는 그 verdict 가 기록됐는가」**를 본다. 아니면 fail-closed.
- 과거 rev 는 기록조차 하지 않는다 — 회귀 `test_only_the_highest_revision_is_ingested`.

## 6-F. liveness — 기다림이 무의미한 경우를 없앤다

**rev 13 을 그대로 켰다면 정상 clean 회차가 매번 red + 미발송이었다.**

```
seal → B = MACHINE_PASS(제출 없음) → 즉시 drain → FINALIZATION_VALIDATION_PENDING → exit 9
```
job timeout 은 15분, fail-open 은 seal+20분. **같은 job 안에서 기다릴 수 없고, 20분 뒤 다시 부를 주체도 없다.**

### 조사 결과 — 독립 caller 는 존재하지 않는다

| 확인 | 사실 |
|---|---|
| 「P0-02/P0-04 독립 cron 패턴」 | **GitHub Actions 내부**의 staggered cron (`collect.yml` 06:05·06:25·06:45 KST). 측정 표본도 전부 `event_name: "schedule"` |
| 외부 소비자 계약 | `retrieval_pointer_only: true` · `read_model_retrieval_only: true` · 나머지 authority 전부 `false` ⇒ **`repository_dispatch` 는 write 라 권한 밖** |
| `repository_dispatch` 기존 사용 | 저장소 어디에도 **없음** |
| 저장소 자체 서술 | 「**No automatic same-day retrigger is scheduled.** … an unapproved new cron was **deliberately not added** to manufacture one — honest WBS blocker `SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED`」 |

⇒ **cron 추가는 이미 상위에서 의도적으로 거부된 선택지다.** rev 5 의 sweep cron 도, rev 13 이 넣으려던 재진입 cron 도 그 결정을 우회하는 것이었다. **추가하지 않는다.**
회귀 `test_12e_no_cron_is_added` 가 패치가 cron 을 몰래 늘리지 못하게 고정한다.

### 그래서 대기 정책을 조건부로 만든다

20분 대기는 **의미 검증자에게 답할 시간을 주는 것**이다. 검증자가 없으면 그 20분은
답이 올 가능성이 0인 채로 회차를 죽일 뿐이다.

`config/atlas_semantic_validator.json`:

| `expected` | 동작 | 기록되는 status |
|---|---|---|
| `true` | 기존대로 `timeout_minutes` 대기 후 fail-open. **재진입 caller 가 hard prerequisite** | `UNVALIDATED_TIMEOUT` |
| **`false`** (현재) | 기다리지 않고 발송 | **`UNVALIDATED_NO_VALIDATOR`** · `waited_minutes` 는 **0 으로 고정**(rev 14 는 실제 경과를 적어 하지 않은 대기를 기록했다) |
| 파일 없음 | **보수적으로 `true`** 취급 | — |

재시도해도 같은 내부 verdict 를 **다시 발행하지 않는다** — `govern()` 이
`UNVALIDATED_NO_VALIDATOR` 를 semantic 지배값으로 인정하므로 기록이 하나로 유지된다(rev 14 는 매 시도마다 쌓았다).

두 status 를 합치지 않는 이유: 「기대했는데 안 왔다」와 「기대할 대상이 없었다」는 다른 사건이고,
합치면 **하지 않은 대기를 기록**하게 된다. 둘 다 내부 전용이라 외부 validator 는 주장할 수 없다.

**아무것도 완화되지 않는다** — 회귀로 고정: `HOLD` 여전히 차단 · 정정은 여전히 서명 승인 필요 ·
intent durability 여전히 필수 · 외부 제출 금지.

⚠ **`expected: true` 로 뒤집는 순간 재진입 caller 가 다시 필수가 된다.**
현재 그런 caller 는 없고, 만들려면 **cron 추가 거부 결정**과 **read-only 소비자 계약** 두 가지를 먼저 풀어야 한다.
그건 Finalization 범위가 아니라 별도 결정이다.

## 6-E. 워크플로 배선

```
resolve → producer(Phase A/B) → seal → publish sealed draft
        → reconcile prior verdicts → validator(B) → publish verdict
        → drain(ingest per stream → deliver) → commit
```

### 인수조건 ① pre-B reconciliation

**재현된 결함:** B 가 machine HOLD 를 inbox 에 쓰고 → drain 전에 runner 중단 → 원인 수정 →
다음 실행에서 B 는 **기록된** 블록이 없으니 `MACHINE_CLEARED` 를 내지 않음 →
drain 이 그제서야 낡은 HOLD 를 처음 ingest → **이미 고쳐진 브리핑이 한 회차 더 차단.**
영구 deadlock 은 아니지만 회차 하나를 잃는다.

**양쪽 다 닫았다:**
1. 배선에 `ingest` step 을 **B 앞에** 둔다. 새 runner 가 먼저 지난 블록을 기록하므로 B 가 그것을 보고 철회할 수 있다.
   이 step 은 **non-fatal** 이다 — 기록 불가한 verdict 는 drain 에서 어차피 fail-closed 이고,
   여기서 죽이면 validator 자체를 건너뛰게 된다.
2. `machine_stream_is_blocking()` 이 **기록된 상태 + 현재 authoritative machine inbox** 를 모두 본다.
   CLI 로 직접 돌릴 때도 성립한다. unparseable 자료가 있으면 **blocking 으로 간주**한다 —
   게이트가 fail-closed 하는 대상을 validator 가 clear 라고 보고하면 안 된다.

### 인수조건 ② atomic publication

B 의 `emit()` 은 `_atomic_write`(temp → `os.replace`)를 쓴다.
게이트는 unparseable inbox 에 **의도적으로 fail-closed** 하므로, 반쪽 파일을 남길 수 있는 writer 는
crash 만으로 **자기가 복구 불가능한 block 을 만들 수 있다.**
원자적 발행 이후에는 「durable 한 unreadable 자료」가 정상 writer 의 crash 결과가 아니라
**실제 corruption** 뿐이며, 그 경우 fail-closed 가 타당하다.
회귀: `os.replace` 를 실패시켜도 `validation-inbox-rev-*.json` 이 **하나도 남지 않고** 게이트가 막히지 않는다.

## 6-D. B — 결정론적 validator (`briefing_validator/4`)

**증명 가능한 것만 검사하고, 나머지는 미검증으로 남긴다.** 조용히 통과시키지 않는다.

### 구조 검증은 **위임**한다 — 재구현하지 않는다

rev 1 은 packet/locator 검사를 자체 축약본으로 들고 있었고, 그 결과
**packet 내부 `packet_sha256` 이 낡은 채로 PASS** 했고
`schema_version="evil/9"` · `authority.trading=true` · `delivery_scope=["EVIL"]` 도 전부 통과했다.
production H-24 경로가 이미 그것을 검사하는데 더 약한 사본을 옆에 만든 것이 원인이다.

rev 2 는 **호출**한다:

| 위임 대상 | 무엇을 보증하는가 |
|---|---|
| `briefing/daily_orchestrator.py validate <packet>` | packet 자체 `packet_sha256` 을 canonical JSON 으로 **재계산** |
| `.github/scripts/daily_briefing_delivery.py consume` | locator `schema_version`·`delivery_scope`·`authority` 검사 + locator 재구축 + packet 검증 |

**두 스크립트에 닿지 못하면 `CANONICAL_VALIDATOR_UNAVAILABLE` 로 `HOLD` 한다** —
더 약한 로컬 구현으로 조용히 내려앉지 않는다.

| 검사군 | 내용 | 위반 시 |
|---|---|---|
| canonical structure | 위 두 스크립트 위임 + 게이트 자신의 byte-binding(`bind_locator`) | **`HOLD`** |
| payload binding | 봉인 payload 해시 · marker 존재 · payload 가 briefing.md 를 포함하는가 | **`HOLD`** |
| arithmetic | index `latest_revision` ↔ `revisions[]` · `component_status_counts` ↔ packet 실측 · step0 `totals` ↔ collector 합 | `ARITHMETIC` 정정 |
| dates | briefing 헤더 날짜 ↔ decision_date · **헤더에 날짜가 아예 없는 경우 포함** | `DATE` 정정 |
| SSOT 대조 | step0 `expected_kst_date`·collector 날짜 · compact view `collected_for_kst_date` | **`OBSERVATION`** (정정 아님) |
| evidence grade | `config/atlas_evidence_grade_rule.json` 이 **있을 때만** | 없으면 `unverified_semantic` 에 기재 |

### verdict 스트림 2개 — machine / semantic

**rev 10 의 결함:** 기계가 구조 블록을 **걸 수는 있는데 풀 수가 없었다.**
푸는 유일한 방법이 `PASS` 인데 그건 검사한 적 없는 사실을 보증하는 것이라 금지돼 있었기 때문이다.
결과적으로 **일시적 구조 오류 한 번이면 고쳐진 뒤에도 브리핑이 영구 HOLD** 였다.

기계 검사기와 의미 검토자는 **다른 질문에 답한다.** 그래서 스트림을 분리하고,
**차단은 두 스트림의 합집합**으로 계산한다 — 어느 쪽도 상대를 통과시킬 수 없다.

| `machine` 최신 | `semantic` 최신 | 지배 verdict |
|---|---|---|
| `HOLD` | 무엇이든 | **machine HOLD** (의미 PASS 로도 못 푼다 — 구조는 객관이다) |
| — | `HOLD` | **semantic HOLD** (`MACHINE_CLEARED` 로 못 푼다) |
| `PASS_WITH_CORRECTION` | — | machine 정정 → CIO Gate |
| `MACHINE_CLEARED` | 없음 | **없음** → verdict 자리 열림 → 기존 fail-open 정책 소유 |

**`MACHINE_CLEARED`** 는 기계 스트림이 **자기 자신의 이전 블록만** 철회하는 신호다.
내용에 대해 아무것도 주장하지 않으므로 그 자체로는 발송을 만들지 않는다.

강제되는 경계 3가지:
- **machine 스트림은 `PASS` 를 발행할 수 없다** (`MACHINE_STREAM_STATUSES` 에 없음).
  구조 검사 통과는 사실이 참이라는 증거가 아니다.
- **`MACHINE_CLEARED` 를 semantic 스트림으로 제출할 수 없다.**
- 스트림 미기재 legacy verdict 는 `semantic` 으로 해석한다.

### machine PASS ≠ 최종 PASS

**rev 1 의 가장 큰 결함이다.** 깨끗한 기계 검사 결과가 곧바로 `PASS` 가 되어 게이트를 통과하고
사용자에게 도달했다. 「미검증이라고 적어두는 것」과 「미검증이 게이트를 통과하지 않는 것」은 다르고,
rev 1 은 앞의 것만 했다. 테스트가 그 동작을 의도적으로 고정하고 있었다는 점이 더 나빴다.

rev 2 의 상태 계약:

| 기계 결과 | `machine_status` | 게이트에 제출? | 결과 |
|---|---|---|---|
| 구조 오류 | `HOLD` | ✅ 제출 | 게이트가 `FINALIZATION_HELD` 로 차단 |
| 결정론적 정정 | `PASS_WITH_CORRECTION` | ✅ 제출 | 정정 있음 → CIO Gate |
| 깨끗함 | **`MACHINE_PASS`** | ❌ **제출 안 함** | 게이트의 verdict 자리는 **비어 있음** → 기존 fail-open 정책이 소유 |

깨끗한 회차는 `machine-validation-rev-NNN.json` 만 남기고 **inbox 에 쓰지 않는다.**
`validation_status` 필드 자체가 없어서 게이트가 PASS 로 오인할 수 없다
(`test_a_clean_verdict_is_not_even_submittable`).
의미 검증 주체가 끝내 나타나지 않으면 **기존 20분 fail-open 정책**이 그대로 적용된다 —
B 가 사실검증을 했다고 가장하지 않으면서도 구조 오류는 즉시 막는다.

**불변식 4개 (테스트로 고정):**
1. `conclusion_diff.spec_version` 은 **언제나 `null`** — validator 가 spec 을 지어내 auto-apply 를 열 수 없다.
   제출 가능한 verdict 로도 allowlist 우회가 안 되는지 확인한다.
2. **`UNVALIDATED_TIMEOUT` 을 절대 발행하지 않는다** — 경과시간 주장은 게이트만 할 수 있다.
3. verdict 는 **자기가 검사한 payload 해시를 반드시 명시**한다.
4. **깨끗한 기계 결과는 최종 PASS 가 아니다** — 게이트에 제출되지 않는다.

**의도적으로 검사하지 않는 것** (매 verdict 에 `unverified_semantic` 로 동봉):
사실 주장의 진위 · 인과 확정 · 시장 해석 · Stage 전이(기준 `Undefined`) · 산문 속 숫자 · 누락 여부.

**stale step0 를 정정으로 만들지 않는 이유:** 브리핑이 「데이터 미도착」이라고 정직하게 적는 것이
정당한 산출물이기 때문이다. 기계는 격차를 **기록**하되 산문을 판정하지 않는다
(`test_stale_step0_is_observed_not_corrected`).

**evidence grade 규칙이 없으면 검사하지 않는다** — 여기서 규칙을 지어내는 것이
Decision Boundary 가 금지한 「`Undefined` 를 현장에서 정의」다.

**워크플로 배선은 rev 13 에서 완료했다** (§6-E). live blob `4bf0fc9e…` 기준 dry-run 재생성 · semantic 19/19.

## 6-C. 전달 이후의 source 변경 — 새 브리핑이 아니라 정정이다

producer 는 같은 날 데이터가 회복되면 **새 revision 을 만들도록 설계돼 있다.** 이론적 경우가 아니다.
**rev 7 은 그때 `draft-rev-002` 를 정상 봉인했고, 그 draft 는 아무도 받지 않는데
`backlog` 는 receipt 만 보고 그 슬롯을 완료로 취급했다.** receipt 가 최신 payload 를 대표한다는 의미가 깨진다.

rev 8 의 `seal` 은 receipt 존재를 먼저 본다:

| 조건 | 결과 |
|---|---|
| 동일 payload | **no-op** (`reused: true`) |
| payload 변경 | **`post-delivery-change-rev-NNN.json` 기록 + 실제로 움직인 축마다 correction 발행.** `draft-rev-NNN.json` 은 **만들지 않는다** |

**멱등성 (rev 8 P1)** — 같은 관측은 `post_delivery_change_key`(delivered_seal_key → new_seal_key)로 식별해
**아티팩트도 원장 항목도 재생성하지 않는다.** rev 8 은 seal 을 두 번 부르면 `-rev-001`·`-rev-002` 가 둘 다 생기고
원장에 같은 항목이 두 번 쌓였다.

**델타 정확화 (rev 8 P1)** — `source_fingerprint` 를 축별로 분해해 기록한다:
`revision` · `briefing_sha256` · `packet_sha256` · `consume_sha256` · **`body_sha256`**(marker 제외 content hash).
움직인 축만 correction 이 되므로 consume 만 바뀐 경우 더 이상 `SOURCE_REVISION: 1 → 1` 이 기록되지 않는다
(`test_consume_only_change_does_not_claim_a_revision_change`). 축이 bytes 면 `SOURCE_CONTENT`, 번호면 `SOURCE_REVISION`.
`capital_impact` 는 `UNKNOWN` 으로 두고 B 의 `post_delivery_inputs` 로 넘긴다 — 판정은 비준된 conclusion spec 이 있어야 한다.

`normal_delivery: false` · `redelivery: "FORBIDDEN"` 로 표시하고 `backlog.post_delivery_changes` 와
`report_drain.py` 의 `::warning::` 으로 노출한다.

**`seal` 은 이때 exit 0 이다** (CIO 승인). producer 의 정당한 same-day recovery 실행을
build failure 로 만들지 않기 위해서다.

### 그러나 `capital_impact = UNKNOWN` 은 완료 상태가 아니다 (rev 15)

**rev 14 는 경고만 하고 green 으로 끝났다.** 「이 변경이 투자결론을 움직이는지 아직 모른다」인데
workflow 는 성공으로 종료됐다 — 사용자 채널이 붙는 순간 중요한 사후 변경을 조용히 놓치게 된다.

의미 판정을 자동으로 만들 필요는 없다. **모른다는 사실 자체가 escalation 조건**이다.

| 상태 | drain |
|---|---|
| 미해소(`UNKNOWN`) | **non-green · `EXIT_CIO_ATTENTION_REQUIRED`(12)** · `cio_attention_required` 에 등재 |
| 서명된 ruling `NONE` | green · audit 종료 |
| 서명된 ruling `PRESENT` | green · CIO 가 이미 판단·조치했고 `action_taken` 이 기록됨 |
| **어느 경우에도** | **재발송 `FORBIDDEN`** |

ruling 은 **승인과 같은 Ed25519 키·같은 out-of-band anchor** 로 서명한다 — 서명 없는 파일은 ruling 이 아니고,
다른 키로 서명한 것도, 다른 change key 에 대한 것도 인정되지 않는다.
**`action_taken` 도 서명 안에 있다** — rev 15 는 이걸 밖에 둬서 「Portal 갱신·알림 발송」으로 서명한 ruling 을
「NO ALERT SENT; PORTAL NOT UPDATED; ORDER XYZ EXECUTED」로 고쳐도 검증이 통과했다.
DENY→APPROVE 변조와 같은 등급의 결함이었다. `PRESENT` 는 **비어 있지 않은 `action_taken` 이 필수**다.

```bash
python3 .github/scripts/sign_approval.py resolve-change --key ~/.atlas/approval_key \
  --repo-root . --slot evening --decision-date 2026-08-27 \
  --change-key <drain 이 알려준 키> --capital-impact NONE|PRESENT \
  --approved-by "CIO" --action-taken "무엇을 했는지"
```

### ruling 은 완료가 아니다 (rev 16)

**rev 15 는 서명된 ruling 하나로 green 이 됐다.** 그러나 ruling 이 증명하는 것은
「이 변경이 투자결론을 움직이는가」뿐이다. 다음 두 가지는 **별개의 사실이고 각각 증거가 필요하다**:

- 정본(Portal/SSOT)이 **실제로 정정됐는가**
- 자본 영향이 있다면 사용자에게 **실제로 알림이 도달했는가**

rev 15 에서 이건 `portal_synced` 라는 **손으로 세우는 boolean** 이었다 — 아무도 쓰지 않은 채널을 적어 넣던
rev 1 의 receipt 와 같은 모양이다. **`--portal-synced` CLI 플래그는 제거했고**, 이제 `portal_synced` 는
**projection receipt 에서만 파생**된다.

| capital_impact | 완료 조건 |
|---|---|
| `NONE` | 서명 ruling + **Portal projection receipt** |
| `PRESENT` | 서명 ruling(+`action_taken`) + Portal projection receipt + **user-reaching 채널의 alert receipt** |

미충족 항목은 `blocked_by` 로 이름이 나온다:
`CIO_RULING_MISSING` · `PORTAL_ADAPTER_NOT_IMPLEMENTED` · `PORTAL_PROJECTION_RECEIPT_MISSING` ·
`NO_USER_REACHING_CHANNEL_CONFIGURED` · `CAPITAL_ALERT_RECEIPT_MISSING`.

`config/atlas_projection.json` 이 어떤 어댑터가 실재하는지 선언한다.
**`github_step_summary` 는 user-reaching 채널이 아니다** — 사람이 Actions 를 열어야 하기 때문이다.
현재 `user_reaching_channels` 는 **비어 있다.**

### receipt 는 파일 존재가 아니라 수행 증거여야 한다 (rev 17)

**rev 16 은 그 이름의 JSON 이 있고 change key 만 맞으면 receipt 로 인정했다.**
`portal.implemented = false` 인 상태에서 `{"post_delivery_change_key": "..."}` 두 줄짜리 파일 하나로
`portal_synced = true` · `complete = true` · `exit 0` 이 됐다.
「어댑터가 없으니 receipt 를 만들 수 없다」는 **산문이었을 뿐 코드가 강제하지 않았다.**

rev 17 이 강제하는 것:

1. **`implemented: false` 면 어떤 receipt 도 완료로 인정하지 않는다.**
   receipt 파일이 있으면 그 사실 자체를 `PORTAL_RECEIPT_WITHOUT_ADAPTER` 로 함께 보고한다.
2. `implemented: true` 이후에도 **존재 확인이 아니라 내용 검증**을 한다:
   `post_delivery_change_key` 정확 일치 · `adapter` 가 정책이 지정한 어댑터 · `target` 과 `written_at_utc` 비어있지 않음 ·
   **`content_sha256` 가 기대 projection 해시와 일치**.
3. **기대 해시는 change + 서명된 ruling 에서 파생된다:**

```
expected_projection_content = { contract_version, purpose, briefing_id,
                                post_delivery_change_key, changed_axes,
                                capital_impact, action_taken, redelivery }
```

   따라서 receipt 는 **ruling 이전에 만들어질 수 없고**, ruling 이 바뀌면 **이전 receipt 는 자동 무효**가 된다
   (`test_a_projection_hash_does_not_survive_a_changed_ruling`).
4. alert receipt 도 동일하다 — 채널이 `user_reaching_channels` 에 있어야 하고,
   `sent_at_utc` · `transport_id` 가 있어야 하며, `transmitted_sha256` 가 기대 alert 해시와 일치해야 한다.

기대 해시는 `backlog`/`drain` 출력과 `report_drain` 로그에 **공개**되므로 어댑터가 무엇을 써야 하는지 알 수 있다.

### receipt 도 최신 revision 이 authority 다 (rev 18)

**rev 17 은 `setdefault` 를 써서 같은 change 의 receipt 중 「가장 오래된」 것이 영원히 authority 였다.**
잘못된 첫 receipt 를 올바른 것으로 교체할 방법이 없었고, 아티팩트가 append-only 라 **파일을 지워야만** 풀렸다.
재판정에서 특히 치명적이었다 — `NONE` → receipt → `PRESENT` 재판정 → 새 Portal write → 새 receipt 를
정상 수행해도 시스템은 계속 옛 receipt 를 읽어 **영구 미완료**였다.

inbox 에서 이미 확립한 계약과 같게 맞췄다 — **change 별 최신 revision 이 authority**:

| 순서 | 결과 |
|---|---|
| bad rev-001 → good rev-002 | **복구** |
| good rev-001 → bad rev-002 | 최신 bad 로 **fail-closed** |
| 옛 ruling receipt → 새 ruling receipt | 새 receipt 로 **정상 완료** |
| alert receipt | 동일 규칙 |

⚠ `CONTRACT_VERSION` 은 기대 projection/alert 내용에 포함되므로, **contract 를 올리면 미결 receipt 는 전부 무효**가 된다.
계약이 바뀌었으니 옳은 동작이며, 현재는 live receipt 가 없어 영향이 없다.

### ⚠ live canary 전에는 post-delivery change 가 non-green 이다 — 의도된 것이다

Notion adapter 코드와 workflow pre-delivery 배선은 추가됐지만
`implemented = false` · `verified_against_live_api = false` 이므로, Finalization은 아직 어떤 receipt도
완료 증거로 인정하지 않는다. GitHub Actions identity가 실제 Atlas Briefing SSOT에 write한 뒤 전 필드를
read-after-write로 대조하고 atomic receipt를 남기는 canary가 끝나야 두 flag를 함께 올린다.

앞서 세 번(rev 10 machine HOLD · rev 14 liveness · rev 15 PRESENT) 「풀 수 없는 red 는 함정」이라고 판단했지만
이번은 성격이 다르다:

- rev 10/14 는 **원인이 이미 해소됐는데 해소 신호가 없어서** 걸린 red 였다 → 함정
- canary 전에는 **Portal write/readback이 증명되지 않은 것이 사실** → red 가 정확한 상태 서술

그러므로 이 red 는 함정이 아니라 **「활성화하면 안 된다」는 신호**다.

후속 adapter 구현은 `briefing_id`/`post_delivery_change_key` 멱등 upsert, canonical JSON SHA256,
전 필드 read-after-write, 생성 경쟁 중복 탐지, atomic append-only receipt, change별 latest receipt authority를
강제한다. 정상 최종 payload는 **Portal projection 뒤에만 1회 전달**되고, 사후 정정은 같은 행을 갱신하되
`redelivery = FORBIDDEN`을 유지한다. 상세 운영 계약과 canary 절차는
`docs/notion_briefing_ssot_projection.md`가 담당한다.

## 7-A. 상태 바인딩 (rev 5)

- **progress ↔ payload** — `delivery_progress.json` 은 자기가 기록된 payload 해시를 들고 있고,
  현재 draft 와 다르면 `progress-superseded-*.json` 으로 **격리**하고 빈 상태에서 시작한다.
  rev 4 는 draft-1 의 proof 를 draft-2 로 그대로 옮겨서, **그 채널이 draft-2 를 받은 적이 없는데도**
  receipt 가 완성됐다.
- **intent ↔ payload** — 다른 payload 에 대한 OPEN intent 가 있으면
  `FINALIZATION_INTENT_PAYLOAD_MISMATCH` 로 막는다. 사람이 옛 버전을 들고 있을 수 있는 상태를 상속하지 않는다.
- **verdict dedupe** — `verdict_digest()` 는 verdict 가 **주장하는 모든 것**의 canonical 해시다.
  rev 4 는 `(payload, status)` 만 봐서 **corrections 만 다른 두 번째 verdict 를 버렸다.**

## 7-B0. debt 는 만료되지 않는다

**rev 6 은 `MISSED_SLOT_LOOKBACK_DAYS = 5` 를 debt 판정에도 써서, activation 이후 seal 됐지만
receipt 가 없는 슬롯이 5일이 지나면 backlog 에서 사라지고 `complete=true` 가 됐다.**

rev 7 은 둘을 분리한다:

| | 범위 | 근거 |
|---|---|---|
| `pending_delivery` (**debt**) | 저장소 **전수 스캔** · 나이 제한 **없음** · activation 필터 **없음** | **sealed draft 의 존재가 유일한 판정 근거다.** draft 가 있다는 것 자체가 finalization 이 그 회차를 담당했다는 증거이므로, 그 날짜가 activation epoch 이전이든 아니든 debt 다 |
| `missing_production` | lookback 5일 + activation epoch | 「산출물이 아예 없는 슬롯이 지각인가」를 정하는 용도로만 쓴다 |

각 debt 항목은 `age_days` 를 달고 나오며 `report_drain.py` 가 `::error::` 로 노출한다.
회귀 `test_old_sealed_but_undelivered_slot_stays_in_the_backlog`, `test_drain_is_not_green_while_old_debt_exists`.

## 7-B. rollout 경계 — activation epoch

`config/atlas_finalization_activation.json` 의 `active_from_kst_date` 는 **`missing_production` 판정에만 쓴다.**
그 이전 날짜에 대해서는 「산출물이 없다」를 지각으로 세지 않는다 — finalization 산출물은 기능이 존재하기 전에 있을 수 없기 때문이다.

⚠ **activation epoch 은 `pending_delivery`(debt)에는 적용되지 않는다.** sealed draft 가 존재하면
그 날짜가 epoch 이전이더라도 debt 다 (§7-B0). 두 개념을 섞지 않는다 — 하나는 「없는 것을 지각으로 셀 것인가」,
다른 하나는 「만들어졌는데 아무도 못 받은 것을 잊을 것인가」이며 후자의 답은 언제나 아니오다.

**rev 5 는 이게 없어서, main 에 병합하는 순간 lookback 5일치 과거 슬롯이 전부
`missing_production` 으로 잡히고 drain 이 첫날부터 계속 exit 9 였다.**

- 파일이 없거나 `active_from_kst_date` 가 `null` → **미활성. 아무것도 owed 아님. drain green.**
- 활성화 날짜·슬롯 이후부터만 debt 로 센다.
- epoch 이전을 정상 브리핑으로 backfill 하지 않는 판단은 유지한다 (§8 참조).

## 7-C. verdict 순서 권위

`validation-inbox-rev-NNN.json` 이 하나라도 있으면 **legacy `validation-inbox.json` 은 무시**하고
`ignored_files` 로 보고한다. rev 5 는 numbered 를 먼저 읽고 legacy 를 **마지막에** 처리해서,
아무도 건드리지 않은 옛 파일의 `HOLD` 가 새 `PASS` 를 되돌렸다.
numbered 는 오름차순으로 ingest 하며 **가장 큰 rev 가 최종**이다.

## 8. 복구 (P0-3 / P0-6 / P0-7 / P0-8)

**rev 3 의 「하루 통째로 건너뛰어도 다음 실행이 전부 회수한다」는 거짓이었다.**
`backlog` 가 「seal 은 있는데 receipt 없음」만 봤기 때문에, 스케줄러가 하루 종일 0건이면
draft 자체가 없어 `backlog=[]` 였다.

rev 4 의 `backlog` 는 **있어야 할 것**을 먼저 열거한다:

- `expected_slots()` — 주중 · 슬롯 정시(07:05 / 18:30 KST) + 60분 유예를 지난 것만
- `missing_production` — 정시가 지났는데 **draft 자체가 없는** 슬롯 → `action: RUN_PRODUCER`
- `pending_delivery` — seal 은 됐는데 receipt 없는 슬롯 → `action: INGEST_THEN_DELIVER`

⚠ KRX 휴장일 달력이 없으므로 공휴일은 **오탐**으로 나온다.
`calendar_confidence: WEEKDAY_ONLY_HOLIDAYS_UNKNOWN` 으로 표시하며 **휴장일을 임의로 만들지 않는다.**

`drain()` 은 항목마다 **`ingest_inbox` → `deliver` 순서**로 처리한다.
rev 3 은 normal run 만 inbox 를 읽고 drain 은 곧바로 deliver 해서,
첫 실행 뒤 게시된 verdict 를 영영 못 읽고 timeout 발송으로 갔다.

`repository_dispatch` 는 `client_payload.{slot,mode,decision_date}` 를 **화이트리스트 검증** 후 사용한다
(`on.repository_dispatch` 만 추가하면 slot 이 비어 `unsupported slot` 으로 죽는다).

**`missing_production` 은 경고로 노출하되 producer 를 자동 재실행하지는 않는다** —
지난 거래일 브리핑을 사후 생성하면 **당시 존재했던 기록과 사후 재구성물이 뒤섞인다.**
훗날 필요해지면 `LATE_RECONSTRUCTION` / `generated_after_slot=true` / `normal_delivery=false` 로
원본 회차와 명확히 분리된 별도 계약이어야 하며, 정상 브리핑으로 자동 발송해서는 안 된다.

### drain 은 미발송을 green 으로 끝내지 않는다

**rev 4 는 `VALIDATION_PENDING` · `HOLD` · reconcile 을 결과 JSON 에 담고도 정상 반환했고 CLI 도 exit 0 이었다.**
아무도 브리핑을 못 받았는데 workflow 가 green 이었다.

rev 5 는 결과를 분류하고 **exit code 를 전파한다**:

| 분류 | 의미 | green? |
|---|---|---|
| `machine_failures` | HOLD · reconcile · intent · approval · 필수채널 미달 | ❌ `EXIT_DRAIN_INCOMPLETE(9)` |
| `observed_pending` | 20분 미경과 (`VALIDATION_PENDING`) | ❌ 같은 코드 |
| `missing_production` | 정시 지났는데 draft 없음 | ❌ 같은 코드 |
| 전부 배달 완료 | — | ✅ `0` |

회귀 `test_pending_slot_is_not_a_green_drain`, `test_cli_drain_propagates_a_nonzero_exit`.

---

## 9. 실행 상태 기계

```
SEALED ──(validation-inbox 수신)──► VALIDATED
   │                                   │
   │ 20분 실경과 & verdict 없음         ├─ corrections 0 ─────────────► READY
   └──────────────────────────────────►│
                        UNVALIDATED_TIMEOUT └─ corrections >0 ──► CIO_GATE ──(서명 승인)──► READY
                                                                     │
                                                                     └─(미승인)─► 보류 · exit 3
READY ──► INTENT_OPEN ──► transport ──► RECEIPT ──► INTENT_CLOSED
              │                             │
              │                             └─ 이후 정정 → corrections.jsonl (재발송 없음)
              └─(crash 후 재시도)─► RECONCILE ──► 재전송 / 건너뜀 / exit 6
```

---

## 10. 검증 상태와 남은 인수 조건

`test/test_briefing_finalization_e2e.py` — **121/121 PASS**, RFC 8032 벡터 포함.

`test/test_workflow_patch.py` — **24/24 PASS**, 그리고 이번엔 **live 파일 자체를 fixture 로 쓴다.**
`test_00` 이 fixture 의 blob 이 `4bf0fc9e…` 임을 먼저 단언하므로, fixture 가 실물에서 벗어나면 나머지 전부가 먼저 무효화된다.
검사 항목: **duplicate key 탐지 loader**(rev 5 의 `workflow_dispatch` 2중 정의는 「유효 YAML」 검사만으로는 통과했다) ·
resolver→producer 실배선(`DISPATCH_SLOT`·`RESOLVED_DATE`) · dispatch type 이 mode 권위인지 ·
producer 의 Phase A/B 로직이 보존됐는지 · `$GITHUB_STEP_SUMMARY` 잔존 0 · 새 `uses:` 0 ·
blob 불일치·앵커 불일치·이중 적용 시 **아무것도 바꾸지 않는지**.

### live 실측 결과 (rev 6 에서 실제 수행)

```
$ python3 .github/scripts/apply_finalization_patch.py --repo-root <live> --dry-run
exit 0 · unified diff 262 lines
$ (패치본 semantic regression)
24/24 PASS — duplicate key 0 · workflow_dispatch 1개 · 3개 트리거 · resolver 선행 ·
producer if 게이트 · producer 가 resolver slot/date 사용 · producer 자체 날짜 계산 제거 ·
GITHUB_STEP_SUMMARY 잔존 0 · Phase A/B 로직 보존 · gate 가 RC 전파 · 새 uses: 0
```
`FreshRunner` 하니스가 **새 runner · 새 checkout** 을 모델링한다 — push 된 것만 존재하는 환경.
`test_fresh_runner_after_crash_does_not_double_send` 가 CIO 가 재현한 중복 발송을 회귀로 고정한다.
픽스처는 실측 레이아웃을 재현한다: `evidence/daily_briefing/{slot}/{date}/index.json`
+ `rev-001/{packet.json,briefing.md}`, locator 실제 키셋
(`index_sha256`·`packet_file_sha256`·`packet_sha256`·`briefing_sha256`·`revision`·`delivery_scope`·`authority`).

**아직 닫히지 않은 것 — 병합 전 필수:**

| # | 항목 | 상태 |
|---|---|---|
| 1 | **validator producer** | **미결정.** ingestion 은 연결됐고 drain 이 항목마다 읽음. 생산 주체 A/B/C 선택 필요 |
| 2 | **사용자 delivery adapter** | `github_step_summary` 만 live 검증(FULL). `kakao` 는 구현했으나 **실 API 미검증 + SUMMARY(전문 미증명)**. notion·push·email 미구현 |
| 3 | **live Actions 증거** | 미확보. 34/34 는 시뮬레이션이지 Actions 실행이 아님 |
| 4 | **CIO 서명키 생성 + 공개키 커밋 + anchor secret** | `sign_approval.py keygen` 로컬 1회 → 공개키 커밋 + 지문을 repo secret `ATLAS_APPROVAL_PUBKEY_FINGERPRINT` 로 등록. 코드가 대신할 수 없음 |
| 4c | **패치 실제 적용** | `--dry-run` 은 live blob `4bf0fc9e…` 에서 **검증 완료**. 적용은 `--apply`. blob 이 바뀌었으면 거부한다 |
| 4d | **activation epoch 설정** | 워크플로 패치와 **같은 PR 에서** `active_from_kst_date` 를 첫 담당 회차 날짜로 설정. 그전까지 gate 는 아무것도 owed 아님 |
| 4e | ~~push ruleset~~ | **철회.** public repo 라 사용 불가. §7 참조 |
| 4f | **검증 코드 root-of-trust** | **미해결로 명시.** data push 와 code/trust-root 분리는 별도 시스템 작업 |
| 5 | **8/27 schedule run 0건 근본원인** | `Unknown`. `repository_dispatch` 는 우회일 뿐 수리가 아님 |
| 6 | **KRX 휴장일 달력** | 없음. `missing_production` 이 공휴일에 오탐 |
| 7 | **missed slot 사후 producer 재실행** | 탐지만 구현. 자동 재생성은 별도 비준 사안으로 남김 |

**②가 닫히기 전까지 「아침·저녁 딱 한 번씩 받는다」는 성립하지 않는다** — 사람이 Actions 요약을 보러 가야 한다.

---

## 11. 이 스펙이 열지 않는 것

Stage · Buy · Action · Order · Production · Trading authority **전부 `false` 유지.**
문서 전달 계층이며 투자 판단 계층이 아니다. 정정 authority 조차 Phase B 이후이고 CIO 비준이 선행한다.

---

## 부록 — rev 2 서술 정정 2건

- **P0-02 표본수**: rev 2 스펙의 「오늘 `measured_count=0` 이므로 3~5회 표본이 안 쌓이면」은 정본과 불일치였다.
  P0-02 WBS 는 8/25 기준 primary/backup/final 각 **measured n=4 로 표본수 조건 자체는 이미 충족**으로 기록한다.
  `measured_count=0` 은 **8/27 하루의 신규 표본이 0** 이라는 뜻이며 누적이 0으로 돌아간 것이 아니다.
  남은 것은 external caller 정시성·역할 적합성이다.
- **H-24 404**: rev 1·2 의 「Finalization 보다 먼저 별도 복구해야 하는 코드 결함」은 과잉 단순화였다.
  `daily-briefing.yml` 이 실행 중 `publish-locator` 로 그 파일을 생성·커밋한다.
  CIO 가 Actions REST 로 판별한 결과 8/27 UTC 의 `event=schedule` run 은 **0건**(8/26 은 14건)이므로
  (b) Phase A 실패 · (c) push 미반영은 **배제**되고 관측상 **(a) scheduled run 미생성**이다.
  근본원인은 여전히 `Unknown`.
