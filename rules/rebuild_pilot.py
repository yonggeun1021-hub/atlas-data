"""pilot 산출물을 46칸 빌더와 **같은 배치·결합표기 경로**로 재생성한다.

★ 왜 필요한가 — pilot 은 결합 표기 정책이 정해지기 전에 손으로 작성됐다.
  CIO 검수 ③ 으로 validator 의 delimiter 정책을 builder 와 일치시키자,
  pilot 의 ' / ' 구간이 미분해로 드러났다. 사람 판독은 그대로 두고
  배치·결합표기 완성만 동일 함수로 다시 태운다.
"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import validate_decomposition as V
from build_full_decomposition import place_and_merge

pilot = json.load(open(os.path.join(ROOT, "rules", "decompose_pilot.json"), encoding="utf-8"))
rows = json.load(open(os.path.join(ROOT, "_watchlist_rows.json"), encoding="utf-8"))["results"]
extra = {f"{r['티커']}::편입 사유": r["편입 사유"] for r in rows if r.get("편입 사유")}
raws = V._raw_texts(os.path.join(ROOT, "config", "rules.candidates.json"), extra)

errs = []
for cell in pilot["cells"]:
    for i, fr in enumerate(cell["fragments"], 1):
        fr["_orig_idx"] = i
    merged, e = place_and_merge(cell["candidate_id"], raws[cell["candidate_id"]],
                                cell["fragments"], cell["decomposition_scope"])
    errs += e
    cell["fragments"] = merged
if errs:
    print("REBUILD FAILED"); [print("  ✗", x) for x in errs]; sys.exit(1)
json.dump(pilot, open(os.path.join(ROOT, "rules", "decompose_pilot.json"), "w",
                      encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"REBUILD OK — 셀 {len(pilot['cells'])} · 조각 "
      f"{sum(len(c['fragments']) for c in pilot['cells'])}")
