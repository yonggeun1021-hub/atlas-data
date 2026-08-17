#!/usr/bin/env python3

import os
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(sys.argv[1] if len(sys.argv) > 1 else "run_s2_collection_noraw.sh").resolve()
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s  %s%s" % (
        "PASS" if cond else "FAIL",
        name,
        (" — " + detail) if detail else "",
    ))


def make_fake_collector(root, fail_sid=None):
    tool = Path(root) / "fake_collector.py"
    tool.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if len(args) < 2 or args[0] != "run":
    sys.exit(90)

sid = args[1]

def value(flag):
    i = args.index(flag)
    return args[i + 1]

run_id = value("--run-id")
fail_sid = os.environ.get("FAKE_FAIL_SID", "")

if sid == fail_sid:
    print("synthetic failure for %s" % sid, file=sys.stderr)
    sys.exit(23)

root = os.environ["ATLAS_FRED_DERIVED_DIR"]
final = os.path.join(root, sid, "runs", run_id)
os.makedirs(final)

manifest = {
    "complete_gate": True,
    "no_raw_archive": True,
    "raw_response_files_written": 0,
    "inventory_sha": "inventory-" + sid,
    "meta_sha": "meta-" + sid,
    "inventory_count": 1,
    "normalized_row_count": 1,
    "revision_row_count": 1,
    "lag_row_count": 1,
    "artifacts": {
        "normalized_panel": {
            "row_count": 1,
            "bytes": 1,
            "artifact_sha256": "a" * 64
        },
        "revision_target": {
            "row_count": 1,
            "bytes": 1,
            "artifact_sha256": "b" * 64
        },
        "lag": {
            "row_count": 1,
            "bytes": 1,
            "artifact_sha256": "c" * 64
        }
    }
}

with open(os.path.join(final, "run_manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f)

print("[FAKE PASS] %s run_id=%s" % (sid, run_id))
sys.exit(0)
"""
    )
    tool.chmod(0o755)
    return tool


def run_case(
    fail_sid=None,
    preexisting_sid=None,
    break_summary=False,
    poison_path=False,
    certifi_fallback=False,
    invalid_ssl_cert=False,
):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        derived = td / "derived"
        tool = make_fake_collector(td, fail_sid)

        env = os.environ.copy()
        env.pop("SSL_CERT_FILE", None)
        env.pop("SSL_CERT_DIR", None)
        env["FRED_API_KEY"] = "TEST_ONLY_NOT_REAL"
        env["TOOL"] = str(tool)
        env["ATLAS_FRED_DERIVED_DIR"] = str(derived)
        env["PYTHON_BIN"] = sys.executable

        sentinel = None
        if preexisting_sid:
            existing = derived / preexisting_sid / "runs"
            existing.mkdir(parents=True, exist_ok=True)
            sentinel = existing / "PREEXISTING_SENTINEL"
            sentinel.mkdir()
            (sentinel / "keep.txt").write_text("do not delete\n")

        if poison_path:
            poison_python = td / "python3"
            poison_python.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'POISON PATH python3 WAS USED' >&2\n"
                "exit 88\n"
            )
            poison_python.chmod(0o755)
            env["PATH"] = str(td) + os.pathsep + env.get("PATH", "")

        if certifi_fallback:
            fake_ca = td / "fake-certifi-ca.pem"
            fake_ca.write_text("fixture-only-ca\n")
            fake_python = td / "python-no-default-ca"
            real_python = sys.executable
            fake_python.write_text(
                """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *"ssl.get_default_verify_paths"*)
      printf '\n'
      exit 0
      ;;
    *"import certifi"*)
      printf '%%s\n' "$FAKE_CERTIFI_CA"
      exit 0
      ;;
  esac
fi
exec %s "$@"
""" % real_python
            )
            fake_python.chmod(0o755)
            env["FAKE_CERTIFI_CA"] = str(fake_ca)
            env["PYTHON_BIN"] = str(fake_python)

        if invalid_ssl_cert:
            env["SSL_CERT_FILE"] = str(td / "missing-ca.pem")

        if break_summary:
            fake_python = td / "python3"
            real_python = sys.executable
            fake_python.write_text(
                """#!/usr/bin/env bash
if [ "$1" = "-" ]; then
  echo "synthetic summary failure" >&2
  exit 41
fi
exec %s "$@"
""" % real_python
            )
            fake_python.chmod(0o755)
            env["PYTHON_BIN"] = str(fake_python)

        if fail_sid:
            env["FAKE_FAIL_SID"] = fail_sid
        else:
            env.pop("FAKE_FAIL_SID", None)

        cp = subprocess.run(
            ["bash", str(RUNNER)],
            cwd=td,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        finals = {}
        for sid in ("WRESBAL", "TOTBKCR"):
            runs = derived / sid / "runs"
            finals[sid] = list(runs.iterdir()) if runs.is_dir() else []

        logs = list(td.glob("s2_noraw_run_*.log"))
        summaries = list(td.glob("s2_noraw_summary_*.txt"))

        sentinel_kept = (
            sentinel is not None
            and sentinel.is_dir()
            and (sentinel / "keep.txt").read_text() == "do not delete\n"
        )
        return cp.returncode, cp.stdout, finals, logs, summaries, sentinel_kept


print("=" * 72)
print("Atlas S-2 runner atomicity regression")
print("=" * 72)

rc, out, finals, logs, summaries, _ = run_case()

check("R1 successful runner exits zero", rc == 0, "rc=%s" % rc)
check(
    "R2 success publishes WRESBAL",
    len(finals["WRESBAL"]) == 1,
    "count=%d" % len(finals["WRESBAL"]),
)
check(
    "R3 success publishes TOTBKCR",
    len(finals["TOTBKCR"]) == 1,
    "count=%d" % len(finals["TOTBKCR"]),
)
check("R4 success writes one summary", len(summaries) == 1)
check("R5 success writes one log", len(logs) == 1)

rc, out, finals, logs, summaries, _ = run_case("TOTBKCR")

check("R6 second-series failure preserves collector rc", rc == 23, "rc=%s" % rc)
check(
    "R7 second-series failure rolls back WRESBAL",
    len(finals["WRESBAL"]) == 0,
    "count=%d" % len(finals["WRESBAL"]),
)
check(
    "R8 failed TOTBKCR is not published",
    len(finals["TOTBKCR"]) == 0,
    "count=%d" % len(finals["TOTBKCR"]),
)
check(
    "R9 failed run does not publish summary",
    len(summaries) == 0,
    "count=%d" % len(summaries),
)
check(
    "R9B second-series failure has no trap error",
    "unbound variable" not in out,
    "tail=%r" % out[-300:],
)

rc, out, finals, logs, summaries, _ = run_case("WRESBAL")

check("R10 first-series failure preserves collector rc", rc == 23, "rc=%s" % rc)
check(
    "R11 first-series failure publishes nothing",
    len(finals["WRESBAL"]) == 0 and len(finals["TOTBKCR"]) == 0,
    "WRESBAL=%d TOTBKCR=%d"
    % (len(finals["WRESBAL"]), len(finals["TOTBKCR"])),
)
check(
    "R11B first-series failure has no trap error",
    "unbound variable" not in out,
    "tail=%r" % out[-300:],
)

# Existing final not created by this runner must survive rollback.
rc, out, finals, logs, summaries, sentinel_kept = run_case(
    fail_sid="WRESBAL",
    preexisting_sid="WRESBAL",
)

check("R12 pre-existing final survives failed runner", sentinel_kept, "rc=%s" % rc)

# Both series may publish successfully and summary generation can still fail.
# In that case the whole runner transaction must roll back.
rc, out, finals, logs, summaries, _ = run_case(break_summary=True)

check(
    "R13 summary failure rolls back whole run",
    rc == 41
    and len(finals["WRESBAL"]) == 0
    and len(finals["TOTBKCR"]) == 0
    and len(summaries) == 0,
    "rc=%s WRESBAL=%d TOTBKCR=%d summaries=%d"
    % (
        rc,
        len(finals["WRESBAL"]),
        len(finals["TOTBKCR"]),
        len(summaries),
    ),
)

# PATH may contain a broken python3, but a pinned PYTHON_BIN must be used for
# preflight, both collectors, and summary generation.
rc, out, finals, logs, summaries, _ = run_case(poison_path=True)
check(
    "R14 pinned PYTHON_BIN ignores hostile PATH python3",
    rc == 0
    and len(finals["WRESBAL"]) == 1
    and len(finals["TOTBKCR"]) == 1
    and len(summaries) == 1
    and "POISON PATH python3 WAS USED" not in out,
    "rc=%s" % rc,
)

# Selected Python has no usable system cafile: runner must fall back to that
# interpreter's certifi bundle while keeping TLS verification enabled.
rc, out, finals, logs, summaries, _ = run_case(certifi_fallback=True)
check(
    "R15 missing default CA falls back to selected Python certifi",
    rc == 0
    and "tls_ca=certifi:" in out
    and len(finals["WRESBAL"]) == 1
    and len(finals["TOTBKCR"]) == 1,
    "rc=%s" % rc,
)

# Explicit but unreadable CA input must fail before collection/publish.
rc, out, finals, logs, summaries, _ = run_case(invalid_ssl_cert=True)
check(
    "R16 invalid SSL_CERT_FILE fails closed before publish",
    rc == 2
    and len(finals["WRESBAL"]) == 0
    and len(finals["TOTBKCR"]) == 0
    and len(summaries) == 0,
    "rc=%s" % rc,
)

passed = sum(1 for _, ok, _ in RESULTS if ok)

print("=" * 72)
print("%d/%d 통과" % (passed, len(RESULTS)))

if passed != len(RESULTS):
    for name, ok, detail in RESULTS:
        if not ok:
            print("FAIL:", name, detail)
    sys.exit(1)
