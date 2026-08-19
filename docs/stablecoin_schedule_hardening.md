# Stablecoin schedule hardening

`stablecoin-capture.yml`은 GitHub `schedule`의 지연·누락 가능성을 하나의
15:20 KST 슬롯에 맡기지 않는다. 15:20, 16:20, 17:20 KST 세 슬롯이 같은
UTC-date append-only snapshot을 시도하며, 먼저 완성된 snapshot의
`_sha256.txt`가 있으면 뒤 슬롯은 DefiLlama 호출 전에 종료한다.

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
