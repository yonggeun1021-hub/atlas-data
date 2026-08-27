# 기존 체인 삽입 패치 rev 5 — `.github/workflows/daily-briefing.yml`

> **rev 5 부터 이 문서는 참고용이고, 실제 적용은 `apply_finalization_patch.py` 가 한다.**
>
> ```bash
> python3 .github/scripts/apply_finalization_patch.py --dry-run   # unified diff 확인
> python3 .github/scripts/apply_finalization_patch.py --apply
> ```
>
> rev 4 지적대로 문서만으로는 병합 가능한 산출물이 아니었다. 특히 **`MODE=drain` 을 결정하는 로직이
> 그 mode 가 건너뛸 producer step 안에** 있어서 early-exit 이 성립하지 않았다.
> 패처는 **resolver 를 producer 앞의 독립 step 으로** 넣어 이를 해소한다.
>
> 패처는 앵커를 **먼저 전부 검증**하고, 하나라도 어긋나면 **아무것도 바꾸지 않고 중단**한다.
> live 파일 전체를 여기서 충실히 읽을 수 없으므로, 못 읽는 producer 로직을 추측으로 재작성하는 대신
> 정확히 맞출 수 있는 부분만 고치고 나머지는 손대지 않는 방식을 택했다.


새 워크플로 파일 **0개**, 새 `uses:` **0개**. 전부 기존 `daily-briefing.yml` 안에서 일어난다.
rev 3 의 별도 sweep 워크플로는 삭제된 상태 그대로다.

---

## rev 3 패치 문서의 구조 충돌 2건 — 수정

- `workflow_dispatch.mode` 옵션은 `brief|drain` 뿐인데 approve job 이 `inputs.mode == 'approve'` 를 봤다.
- 「새 `uses:` 0개」라고 써놓고 approve job 예시가 `checkout` 을 하나 더 썼다.

**둘 다 approve job 을 없애서 해소한다.** 승인은 이제 **CIO 로컬 오프라인 서명**이다(아래 참조).
CI 안에 승인 발행 경로가 없으므로 job 도, 환경도, 추가 `uses:` 도 필요 없다.

---

## P0-2 — 승인은 비대칭 서명, private key 는 CI 에 없다

HMAC 은 경계가 될 수 없다는 지적이 맞다. 검증할 수 있는 주체는 위조도 할 수 있다.

```
private key   CIO 로컬 머신에만 존재. 저장소·CI·secret 어디에도 없다.
public key    config/atlas_approval_pubkey.txt 로 저장소에 공개.
delivery      public key 로 검증만 한다. 서명은 구조적으로 불가능하다.
```

```bash
# 최초 1회 (CIO 로컬)
python3 .github/scripts/sign_approval.py keygen --out ~/.atlas/approval_key
#   -> 개인키는 로컬에 mode 600, 공개키는 stdout. 공개키만 커밋한다.

# 승인이 필요할 때 (CIO 로컬)
python3 .github/scripts/sign_approval.py sign --key ~/.atlas/approval_key \
    --repo-root . --slot evening --decision-date 2026-08-27 --approved-by "CIO"
#   -> approval-rev-NNN.json 생성. 내용 확인 후 커밋·푸시.
```

서명 대상은 `{briefing_id}|{payload_sha256}|{validation_rev}|{approved_by}` 라서
**다른 payload·다른 verdict 로 승인이 전이되지 않는다.** 공개키 부재 시 `deliver` 는 fail-closed.

---

## P0-8 — `repository_dispatch` payload 계약

`on.repository_dispatch` 만 추가해서는 복구 경로가 열리지 않는다는 지적이 맞다.
현재 slot 결정은 `inputs.slot` 또는 정확한 `github.event.schedule` 만 읽고, dispatch 에서는 둘 다 비어
`unsupported slot` 으로 종료한다.

### `on:` 블록

```yaml
on:
  schedule:
    - cron: "5 22 * * 0-4"    # 07:05 KST (기존)
    - cron: "30 9 * * 1-5"    # 18:30 KST (기존)
  repository_dispatch:
    types: [atlas-briefing-run, atlas-finalization-drain]
  workflow_dispatch:
    inputs:
      slot: { required: false, type: choice, options: [morning, evening] }
      mode: { required: false, default: brief, type: choice, options: [brief, drain] }
      decision_date: { required: false, type: string, description: "KST YYYY-MM-DD (drain 회수용)" }
```

### slot / mode / date 결정 — 3개 트리거 전부 처리

```bash
set -euo pipefail
MODE="brief"; SLOT=""; DECISION_DATE=""

case "${{ github.event_name }}" in
  schedule)
    case "$EVENT_SCHEDULE" in
      "5 22 * * 0-4") SLOT="morning" ;;
      "30 9 * * 1-5") SLOT="evening" ;;
      *) echo "STOP: unrecognized schedule expression: $EVENT_SCHEDULE" >&2; exit 2 ;;
    esac
    ;;
  workflow_dispatch)
    SLOT="${{ inputs.slot }}"
    MODE="${{ inputs.mode }}"
    DECISION_DATE="${{ inputs.decision_date }}"
    ;;
  repository_dispatch)
    # client_payload 는 외부 입력이다. 신뢰하지 않고 화이트리스트로 검증한다.
    SLOT="${{ github.event.client_payload.slot }}"
    MODE="${{ github.event.client_payload.mode }}"
    DECISION_DATE="${{ github.event.client_payload.decision_date }}"
    [ "${{ github.event.action }}" = "atlas-finalization-drain" ] && MODE="${MODE:-drain}"
    ;;
  *) echo "STOP: unsupported event ${{ github.event_name }}" >&2; exit 2 ;;
esac

case "$MODE" in brief|drain) ;; *) echo "STOP: unsupported mode '$MODE'" >&2; exit 2 ;; esac
if [ "$MODE" = "brief" ]; then
  case "$SLOT" in morning|evening) ;; *) echo "STOP: brief mode requires slot" >&2; exit 2 ;; esac
fi
if [ -n "$DECISION_DATE" ]; then
  echo "$DECISION_DATE" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
    || { echo "STOP: malformed decision_date" >&2; exit 2; }
else
  DECISION_DATE=$(TZ=Asia/Seoul date +%F)
fi
```

`MODE=drain` 이면 **step 6(producer)을 통째로 건너뛰고** finalization 회수만 수행한다.
외부 호출자(P0-02/P0-04 independent-cron 패턴)가 GitHub 스케줄러와 무관하게 켤 수 있다.

---

## P1-2 (rev3) — export/copy 를 Phase A 루프 **밖**에서

```bash
# --- Phase A 루프 종료 직후, Phase B 보다 앞. 루프 밖. ---
echo "capture_path=${CAPTURE_PATH:-}"  >> "$GITHUB_OUTPUT"
echo "decision_date=$DECISION_DATE"    >> "$GITHUB_OUTPUT"
echo "slot=$SLOT"                      >> "$GITHUB_OUTPUT"
echo "mode=$MODE"                      >> "$GITHUB_OUTPUT"
if [ -f /tmp/investment-review-delivery.md ]; then
  cp /tmp/investment-review-delivery.md "$RUNNER_TEMP/consume.md"
  echo "consume_ready=true"  >> "$GITHUB_OUTPUT"
else
  echo "consume_ready=false" >> "$GITHUB_OUTPUT"
fi
```

## 변경 — step 6 말미의 `GITHUB_STEP_SUMMARY` 블록 **삭제**

이후 그 쓰기는 `StepSummaryAdapter.send()` 안에서만 일어나고,
**파일 크기가 실제로 증가했을 때만** proof 가 생긴다.
주석 「Push/email/Claude delivery is not implemented here…」는 그대로 둔다.

---

## 새 step — seal → 봉인물 push → ingest → deliver → drain

**순서가 계약의 일부다.** 봉인물은 deliver 전에 push 돼 있어야 하고,
intent push 는 **deliver 프로세스 안에서** 일어난다(아래 P0-1 주의).

```yaml
      - name: Seal briefing for finalization
        if: steps.briefing.outputs.mode == 'brief' && steps.briefing.outputs.capture_path != ''
        run: |
          set -euo pipefail
          ARGS=""
          [ "${{ steps.briefing.outputs.consume_ready }}" = "true" ] && ARGS="--consume-output $RUNNER_TEMP/consume.md"
          python3 .github/scripts/briefing_finalization.py seal \
            --slot "${{ steps.briefing.outputs.slot }}" \
            --decision-date "${{ steps.briefing.outputs.decision_date }}" \
            --repo-root . $ARGS

      # 봉인물을 먼저 durable 하게 만든다. seal 은 idempotent 하므로 재실행해도 안전하다.
      - name: Publish sealed draft
        if: steps.briefing.outputs.mode == 'brief' && steps.briefing.outputs.capture_path != ''
        run: |
          set -euo pipefail
          git config user.name  "atlas-bot"
          git config user.email "atlas-bot@users.noreply.github.com"
          git add data/briefing/finalization
          git diff --cached --quiet || {
            git commit -m "finalization seal ${{ steps.briefing.outputs.decision_date }} ${{ steps.briefing.outputs.slot }}"
            git push origin "HEAD:${{ github.event.repository.default_branch }}"
          }

      # drain 이 항목마다 ingest -> deliver 순으로 처리한다 (P0-6).
      # brief 모드에서도 drain 을 쓴다: 오늘 회차와 밀린 회차를 한 번에 처리한다.
      - name: Ingest verdicts and deliver (drain)
        id: gate
        run: |
          set -uo pipefail
          python3 .github/scripts/briefing_finalization.py drain \
            --repo-root . --channel github_step_summary \
            --required-channel github_step_summary | tee drain.json
          RC=$?
          python3 - <<'EOF'
          import json
          d = json.load(open("drain.json"))
          for m in d.get("missing_production", []):
              print(f"::warning::missed slot {m['briefing_id']} — {m['reason']} "
                    f"(calendar: {m['calendar_confidence']})")
          for e in d.get("drained", []):
              if not e.get("delivered"):
                  print(f"::warning::{e['briefing_id']} not delivered: {e.get('error')}")
          EOF
          exit "$RC"

      - name: Commit finalization artifacts
        if: always()
        run: |
          set -euo pipefail
          git config user.name  "atlas-bot"
          git config user.email "atlas-bot@users.noreply.github.com"
          git add data/briefing/finalization
          git diff --cached --quiet || {
            git commit -m "finalization ${{ steps.briefing.outputs.decision_date }}"
            git push origin "HEAD:${{ github.event.repository.default_branch }}"
          }
```

### ⚠ P0-1 주의 — intent push 를 step 으로 쪼개면 안 된다

`deliver` 는 intent 를 쓴 **직후 같은 프로세스 안에서** `git_intent_publisher` 로 commit·push 하고,
push 가 확인돼야 전송한다. push 실패 시 `FINALIZATION_INTENT_PUBLISH_FAILED`(exit 7) 로 **전송하지 않는다.**

이걸 step 으로 분리하면 다음 프로세스가
「intent 는 있는데 아직 안 보냄」과 「intent 있고 보냈을 수도 있음」을 **구분할 수 없다.**
그러면 unprobeable 채널의 **첫 시도마다 오탐으로 escalate** 한다. 반드시 한 프로세스 안에 둔다.

### P0-7 — missed slot 은 경고로 노출되지만 producer 자동 재실행은 하지 않는다

`drain` 이 `missing_production` 을 반환하면 위 step 이 `::warning::` 으로 띄운다.
**과거 날짜로 producer 를 자동 재실행하는 것은 이번 rev 에 넣지 않았다** — 지난 거래일 브리핑을
사후 생성하는 것은 투자 기록물의 성격을 바꾸는 행위이고, 그 자체가 CIO 비준 사안이라고 본다.
자동 재생성을 원하시면 다음 rev 에서 별도로 올리겠다. 탐지는 이번 rev 에서 닫혔다.

---

## 이 패치로도 닫히지 않는 것

1. **사용자 delivery adapter** — `github_step_summary` 만 live 검증. `kakao` 는 구현했으나 실 API 미검증이고
   `payload_fidelity: SUMMARY` 라 **전문 전달을 증명하지 못한다.** notion/push/email 미구현.
2. **validator producer** — ingestion 은 연결됐고 drain 이 항목마다 읽는다. 생산 주체는 여전히 A/B/C 미결정.
3. **live Actions 증거** — 34/34 는 시뮬레이션이지 Actions 실행이 아니다.
4. **8/27 schedule 0건 근본원인** — `Unknown`. `repository_dispatch` 는 우회이지 수리가 아니다.
5. **KRX 휴장일 달력** — `backlog` 는 주중만 본다. 공휴일은 오탐으로 나오며
   `calendar_confidence: WEEKDAY_ONLY_HOLIDAYS_UNKNOWN` 으로 표시된다.
