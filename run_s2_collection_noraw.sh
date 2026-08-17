#!/usr/bin/env bash
# Atlas — S-2 no-raw-archive one-time research backfill runner
set -euo pipefail

TOOL="${TOOL:-atlas_fred_s2_noraw.py}"
BATCH="${BATCH:-25}"
ATLAS_FRED_DERIVED_DIR="${ATLAS_FRED_DERIVED_DIR:-atlas_derived/fred_s2}"
export ATLAS_FRED_DERIVED_DIR

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${STAMP}_${BASHPID}"
LOG="s2_noraw_run_${RUN_ID}.log"
SUM="s2_noraw_summary_${RUN_ID}.txt"

if [ -z "${FRED_API_KEY:-}" ]; then
  echo "FRED_API_KEY 가 설정되지 않았습니다. 키를 인자나 파일로 넘기지 마십시오." >&2
  exit 2
fi
if [ ! -f "$TOOL" ]; then
  echo "no-raw 수집기를 찾을 수 없습니다: $TOOL" >&2
  exit 2
fi

log() { printf '%s\n' "$*" | tee -a "$LOG"; }
run_series() {
  local sid="$1" obs="$2"
  log ""
  log "=== ${sid} no-raw S-2 run_id=${RUN_ID} ==="
  python3 "$TOOL" run "$sid" --batch "$BATCH" --revision-obs "$obs" --run-id "$RUN_ID" 2>&1 | tee -a "$LOG"
}

log "Atlas S-2 no-raw run started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "run_id=${RUN_ID} tool=${TOOL} batch=${BATCH} root=${ATLAS_FRED_DERIVED_DIR}"

run_series "WRESBAL" "2008-09-10"
run_series "TOTBKCR" "2008-10-01"

ROOT="$ATLAS_FRED_DERIVED_DIR" RID="$RUN_ID" python3 - <<'PY' > "$SUM"
import json, os
root = os.environ["ROOT"]
rid = os.environ["RID"]
print("Atlas S-2 no-raw summary")
print("run_id=%s" % rid)
print("root=%s" % root)
print("=" * 72)
for sid in ("WRESBAL", "TOTBKCR"):
    p = os.path.join(root, sid, "runs", rid, "run_manifest.json")
    with open(p, encoding="utf-8") as f:
        m = json.load(f)
    print("[%s]" % sid)
    print("  complete_gate=%s no_raw_archive=%s raw_response_files_written=%s" %
          (m["complete_gate"], m["no_raw_archive"], m["raw_response_files_written"]))
    print("  inventory=%s meta=%s vintages=%s" %
          (m["inventory_sha"], m["meta_sha"], m["inventory_count"]))
    print("  normalized_rows=%s revision_rows=%s lag_rows=%s" %
          (m["normalized_row_count"], m["revision_row_count"], m["lag_row_count"]))
    for kind, a in m["artifacts"].items():
        print("  %-17s rows=%-8s bytes=%-10s sha256=%s" %
              (kind, a["row_count"], a["bytes"], a["artifact_sha256"][:16]))
    print()
print("Production/evaluator/trading authorization: NONE")
PY

cat "$SUM" | tee -a "$LOG"
log "Atlas S-2 no-raw run completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
