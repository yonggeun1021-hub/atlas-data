"""
Atlas Daily Collect — 중복 실행 방지 Guard

백업 cron 슬롯(06:25 / 06:45 KST)이 돌 때, 앞 회차가 이미 오늘자 데이터를
정상 수집해 두었다면 중복 수집·중복 커밋을 하지 않도록 'yes'를 출력한다.

출력
  yes  → 이 회차는 건너뛴다 (오늘자 + failed 0)
  no   → 수집을 진행한다

설계 원칙: 안전한 기본값은 '수집'이다.
파일이 없거나, 깨졌거나, 판단이 안 서면 무조건 'no'(수집)를 출력한다.
건너뛰기는 확실할 때만 한다.
"""
import json
import os
import sys

PATH = "data/latest_krx.json"


def main() -> None:
    today = os.environ.get("TODAY", "").strip()
    if not today:
        print("no")
        return

    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        print("no")
        return

    fresh = data.get("collected_for_kst_date") == today

    summary = data.get("summary")
    if not isinstance(summary, dict):
        print("no")
        return
    # failed 키가 없으면 건강하다고 가정하지 않는다 → 기본값 1
    healthy = summary.get("failed", 1) == 0

    print("yes" if (fresh and healthy) else "no")


if __name__ == "__main__":
    main()
    sys.exit(0)
