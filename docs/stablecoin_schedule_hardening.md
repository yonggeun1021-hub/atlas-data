# Stablecoin schedule hardening

`stablecoin-capture.yml`은 GitHub `schedule`의 지연·누락 가능성을 하나의
슬롯에 맡기지 않는다. 14:50(primary), 15:20, 16:20, 17:20 KST 네 슬롯이 같은
UTC-date append-only snapshot을 시도하며, 먼저 완성된 snapshot의
`_sha256.txt`가 있으면 뒤 슬롯은 DefiLlama 호출 전에 종료한다.

CRYPTO_PAPER_RUNTIME_V1은 매일 07:00Z(16:00 KST)에 finalized packet을 확정한다.
05:50Z primary와 06:20Z backup은 그 cutoff 전에 여유를 두기 위한 슬롯이다.
07:20Z/08:20Z 슬롯의 capture는 증거로 보존되지만 runtime에서는 lookahead로
거부되어 그날 LIQUIDITY 축은 missing(UNKNOWN)이 된다. 08:20Z 이전 예약 슬롯에서
당일 UTC observation row가 아직 없으면 `pending_current_observation`으로 기록하고
append-only 경로를 만들지 않으므로 뒤 슬롯이 다시 시도한다. row 판정 자체가
실패하면 원문을 잃지 않도록 경고를 남기고 capture하며, 마지막 08:20Z 슬롯은
row 유무와 무관하게 증거 보존용으로 capture한다.

## dispatch guard parity

`workflow_dispatch`는 `guard_mode` 하나만 받는다. `schedule_equivalent`가
아니면(기본값 `refuse`, 공백, 오타, 대문자 포함) capture 단계는 provider를
호출하기 전에 `dispatch_guard_mode_invalid`로 종료한다. input을 생략한 API
dispatch도 기본값이 `refuse`이므로 같은 경로로 거부된다(fail-closed).

`schedule_equivalent`로 실행되면 schedule 조기 슬롯과 **동일한**
`pending_current_observation` guard를 통과해야만 capture한다.

- 당일 UTC observation row가 없으면 `pending_current_observation`으로 종료하고
  append-only 경로를 만들지 않는다.
- 08:20Z final-slot 예외는 schedule 전용이다. dispatch는 그 예외를 빌릴 수
  없다. 이 예외까지 허용하면 dispatch가 schedule이 거부했을 기록을 쓸 수 있게
  되고, 그것이 닫으려는 구멍 자체다.
- row 판정이 실패(`check_error`)하면 schedule은 경고 후 capture하지만
  dispatch는 `dispatch_guard_undetermined`로 거부한다. guard를 판정하지 못한
  상태에서 기록을 남기지 않는다.
- schedule 이벤트에 dispatch input이 실려 오면 `trigger_input_conflict`,
  schedule/dispatch가 아닌 trigger는 `trigger_not_authorized`로 거부한다.

capture된 snapshot에는 `_trigger.json`이 함께 남아 trigger 종류, guard 적용
여부, row 판정 결과를 기록한다. 이 파일은 `_sha256.txt`와 `_manifest.json`이
확정된 뒤에 쓰이므로 ratified revision contract의 checksum inventory와 manifest
비교에는 들어가지 않는다.

`available_at`의 의미는 바뀌지 않는다. 여전히 `_downloaded_at.txt`(다운로드
직전 `date -u`)에서만 나오는 provider fetch 시각이며, dispatch가 넘길 수 있는
timestamp input은 존재하지 않는다. 07:00Z cutoff, 5일 최소치, cron 어느 것도
이 변경에 포함되지 않는다.

이 parity가 갖춰지면 서버 schedule dispatcher를 `alert_only`에서 catch-up으로
바꿀 때 dispatch가 schedule-only guard를 우회한다는 근거가 사라진다. 다만
dispatcher는 반드시 `guard_mode=schedule_equivalent`를 실어 보내야 하고, 그
서버 설정 변경은 별도 승인 대상이다.

각 runner 도착은
`data/operations/stablecoin_capture_runs/{UTC_DATE}/run-{id}-attempt-{n}.json`
에 기록한다. 이 파일은 실행 슬롯, runner 지연, capture/skip/failure와 run URL을
구분하기 위한 operations telemetry일 뿐이며 데이터 준비도나 투자 판단 권한이
없다.

17:25 KST deadline 감시는 같은 GitHub workflow에 두지 않는다. 예약 자체가
누락된 날에는 workflow 내부 알림도 실행되지 않기 때문이다. 독립된 관측자는
저장소를 읽기 전용으로 clone한 뒤 다음 명령을 실행할 수 있다.

```bash
python3 .github/scripts/check_stablecoin_capture.py \
  --date YYYY-MM-DD \
  --now YYYY-MM-DDT08:25:00Z
```

종료 코드는 `PRESENT=0`, `PENDING=2`, deadline 이후
`MISSING/INCOMPLETE/FAILED=3`이다. 실행 기록이 있는데 산출물이 없으면
`capture_failed_after_deadline`, 실행 기록조차 없으면
`snapshot_missing_after_deadline`로 구분한다. 후자는 알림 기준이지 GitHub
trigger 누락의 직접 증명은 아니다. 지연 중인 runner도 아직 기록을 남기지 못할
수 있으므로, trigger 누락 확정에는 별도 Actions 조회가 필요하다.
이 도구는 알림을 직접 보내거나 `workflow_dispatch`를 실행하지 않는다.
`manual_dispatch_authorized`는 항상 `false`이며, 수동 실행은 CIO 승인 대상이다.
`PRESENT`는 필수 파일 존재 관측일 뿐 `DATA READY`나 투자 판단을 뜻하지 않는다.
