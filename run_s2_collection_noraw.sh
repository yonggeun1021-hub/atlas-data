#!/usr/bin/env bash
# Atlas — S-2 no-raw-archive one-time research backfill runner
set -euo pipefail

TOOL="${TOOL:-atlas_fred_s2_noraw.py}"
BATCH="${BATCH:-25}"
ATLAS_FRED_DERIVED_DIR="${ATLAS_FRED_DERIVED_DIR:-atlas_derived/fred_s2}"
export ATLAS_FRED_DERIVED_DIR

# Resolve Python exactly once.  Do not let later PATH/hash differences select
# a different interpreter for collection vs summary generation.
PYTHON_BIN="${PYTHON_BIN:-python3}"
case "$PYTHON_BIN" in
  */*) ;;
  *) PYTHON_BIN="$(command -v "$PYTHON_BIN" 2>/dev/null || true)" ;;
esac
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "실행 가능한 Python을 찾을 수 없습니다: ${PYTHON_BIN:-<empty>}" >&2
  exit 2
fi
readonly PYTHON_BIN

# TLS verification stays enabled.  Respect an explicit SSL_CERT_FILE first.
# Otherwise use the selected Python's system CA; when that Python has no
# usable default cafile, fall back to its certifi bundle.  If neither exists,
# fail before any FRED network request.
TLS_CA_SOURCE="system-default"
if [ -n "${SSL_CERT_FILE:-}" ]; then
  if [ ! -r "$SSL_CERT_FILE" ]; then
    echo "SSL_CERT_FILE을 읽을 수 없습니다: $SSL_CERT_FILE" >&2
    exit 2
  fi
  TLS_CA_SOURCE="env:$SSL_CERT_FILE"
else
  DEFAULT_CA="$("$PYTHON_BIN" -c 'import ssl; p=ssl.get_default_verify_paths().cafile; print(p or "")' 2>/dev/null || true)"
  if [ -n "$DEFAULT_CA" ] && [ -r "$DEFAULT_CA" ]; then
    TLS_CA_SOURCE="system:$DEFAULT_CA"
  else
    CERTIFI_CA="$("$PYTHON_BIN" -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
    if [ -n "$CERTIFI_CA" ] && [ -r "$CERTIFI_CA" ]; then
      export SSL_CERT_FILE="$CERTIFI_CA"
      TLS_CA_SOURCE="certifi:$CERTIFI_CA"
    else
      echo "선택된 Python에 사용 가능한 TLS CA가 없습니다: $PYTHON_BIN" >&2
      echo "시스템 CA를 복구하거나 certifi가 설치된 Python을 PYTHON_BIN으로 지정하십시오." >&2
      exit 2
    fi
  fi
fi
readonly TLS_CA_SOURCE

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="${STAMP}_$$"
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

WRESBAL_PUBLISHED=0
TOTBKCR_PUBLISHED=0
COMPLETED=0

rollback_incomplete_run() {
  local rc=$?
  local final
  if [ "$COMPLETED" -ne 1 ]; then
    if [ "$WRESBAL_PUBLISHED" -eq 1 ]; then
      final="${ATLAS_FRED_DERIVED_DIR}/WRESBAL/runs/${RUN_ID}"
      if [ -d "$final" ]; then
        rm -rf -- "$final"
      fi
    fi
    if [ "$TOTBKCR_PUBLISHED" -eq 1 ]; then
      final="${ATLAS_FRED_DERIVED_DIR}/TOTBKCR/runs/${RUN_ID}"
      if [ -d "$final" ]; then
        rm -rf -- "$final"
      fi
    fi
    rm -f -- "$SUM"
  fi
  return "$rc"
}
trap rollback_incomplete_run EXIT

run_series() {
  local sid="$1" obs="$2"
  log ""
  log "=== ${sid} no-raw S-2 run_id=${RUN_ID} ==="
  "$PYTHON_BIN" "$TOOL" run "$sid" --batch "$BATCH" --revision-obs "$obs" --run-id "$RUN_ID" 2>&1 | tee -a "$LOG"
}

log "Atlas S-2 no-raw run started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "run_id=${RUN_ID} tool=${TOOL} batch=${BATCH} root=${ATLAS_FRED_DERIVED_DIR} python=${PYTHON_BIN} tls_ca=${TLS_CA_SOURCE}"

run_series "WRESBAL" "2008-09-10"
WRESBAL_PUBLISHED=1
run_series "TOTBKCR" "2008-10-01"
TOTBKCR_PUBLISHED=1

ROOT="$ATLAS_FRED_DERIVED_DIR" RID="$RUN_ID" "$PYTHON_BIN" - <<'PY' > "$SUM"
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
COMPLETED=1
