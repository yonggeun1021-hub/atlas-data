"""
Atlas — 산출물 신선도·건강성 판정기

출력은 딱 두 가지이며, 뜻은 하나뿐이다 — **"대상이 오늘자이고 실패가 없는가"**.

    fresh   대상 전부가 오늘자(KST) 이고 summary.failed == 0
    stale   그 외 — 파일 없음 · 파싱 실패 · 날짜 불일치 · 실패 잔존

두 곳에서 쓴다.

    ① collect.yml Guard        인자 없음 → TARGETS 전체를 본다
                               fresh → 이 회차는 건너뛴다 (이미 다 돼 있다)

    ② collect.yml D1 게이트     인자로 data/latest_sec.json 하나만 넘긴다
                               fresh → D1 을 실행한다 (오늘자 SEC 가 확보됐다)

★ 같은 `fresh` 가 한쪽에서는 skip, 다른 쪽에서는 run 을 뜻한다.
  이 스크립트는 **사실(신선한가)만 말하고**, 그 사실로 무엇을 할지는 워크플로가 정한다.
  판정과 행동을 섞지 않는다.

설계 원칙 — **안전한 기본값은 `stale` 이다.**
판단이 안 서면 무조건 `stale` 을 출력한다. 그 결과는 양쪽 모두 안전한 방향이다.
  · Guard 쪽   → 건너뛰지 않고 다시 수집한다
  · D1 게이트 쪽 → 낡은 입력으로 분류하지 않고 멈춘다
"""
import json
import os
import sys

# collect.yml Guard 가 인자 없이 부를 때 검사하는 대상 전체
TARGETS = (
    "data/latest_krx.json",
    "data/latest_dart.json",
    "data/latest_sec.json",
)


def is_fresh_and_healthy(path: str, today: str) -> bool:
    """해당 산출물이 오늘자(KST)이고 실패 0건이면 True."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # 파일이 없거나 깨졌다 → 신선하다고 볼 수 없다
        return False

    if data.get("collected_for_kst_date") != today:
        return False

    summary = data.get("summary")
    if not isinstance(summary, dict):
        return False

    # failed 키가 없으면 건강하다고 가정하지 않는다 → 기본값 1
    return summary.get("failed", 1) == 0


def main() -> None:
    today = os.environ.get("TODAY", "").strip()
    if not today:
        print("stale")
        return

    paths = sys.argv[1:] or list(TARGETS)
    print("fresh" if all(is_fresh_and_healthy(p, today) for p in paths) else "stale")


if __name__ == "__main__":
    main()
    sys.exit(0)
