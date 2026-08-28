#!/usr/bin/env python3
"""Deterministically patch .github/workflows/daily-briefing.yml.

Written against the LIVE file, verified byte-for-byte: the expected git blob is
4bf0fc9ecd27e3affb7c4f85d9f93df5f888dd75 (12,536 bytes).  Earlier revisions of
this script were written against a reconstructed facsimile and did not match
the real file -- the human-reaching write is a braced group, not five separate
redirects, so the anchors missed and dry-run refused.

Every anchor is verified before anything is edited, and the result is checked
against postconditions (single `workflow_dispatch`, no `$GITHUB_STEP_SUMMARY`
left in the producer, resolver actually wired into the producer).  If any check
fails, nothing is written.

    python3 .github/scripts/apply_finalization_patch.py --dry-run
    python3 .github/scripts/apply_finalization_patch.py --apply
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import sys
from pathlib import Path

WORKFLOW = ".github/workflows/daily-briefing.yml"
EXPECTED_BLOB = "4bf0fc9ecd27e3affb7c4f85d9f93df5f888dd75"


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# --------------------------------------------------------------- exact anchors
ON_BLOCK_OLD = '''on:
  schedule:
    # 07:05 KST weekday morning slot -- after the 06:57 P0-02 recovery gate,
    # confirmed-history only. Sun-Thu UTC == Mon-Fri KST next day.
    - cron: "5 22 * * 0-4"
    # 18:30 KST weekday evening slot -- after the P0-04 18:00 KRX post-close
    # cutoff. Mon-Fri UTC == Mon-Fri KST same day.
    - cron: "30 9 * * 1-5"
  workflow_dispatch:
    inputs:
      slot:
        description: "morning or evening (required for manual dispatch)"
        required: true
        type: choice
        options:
          - morning
          - evening
'''

ON_BLOCK_NEW = '''on:
  schedule:
    # 07:05 KST weekday morning slot -- after the 06:57 P0-02 recovery gate,
    # confirmed-history only. Sun-Thu UTC == Mon-Fri KST next day.
    - cron: "5 22 * * 0-4"
    # 18:30 KST weekday evening slot -- after the P0-04 18:00 KRX post-close
    # cutoff. Mon-Fri UTC == Mon-Fri KST same day.
    - cron: "30 9 * * 1-5"
  # A manual/operator entry point for re-running or draining a slot.
  #
  # NOT a recovery guarantee. The "P0-02/P0-04 independent-cron pattern" is
  # staggered GitHub Actions crons, i.e. the same failure plane, and the
  # external consumer contract is read-only (retrieval_pointer_only: true), so
  # it has no authority to fire this. No caller exists today. A same-day
  # automatic retrigger remains the disclosed upstream blocker
  # SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED, and a cron to manufacture
  # one was deliberately rejected there -- so none is added here either.
  repository_dispatch:
    types: [atlas-briefing-run, atlas-finalization-drain]
  workflow_dispatch:
    inputs:
      slot:
        description: "morning or evening (required for brief mode)"
        required: false
        type: choice
        options:
          - morning
          - evening
      mode:
        description: "brief = normal round / drain = recover undelivered only"
        required: false
        default: brief
        type: choice
        options:
          - brief
          - drain
      decision_date:
        description: "KST YYYY-MM-DD (drain recovery; defaults to today)"
        required: false
        type: string
'''

PRODUCER_NAME = "      - name: Publish provider-free daily briefing packet\n"

PRODUCER_ENV_OLD = '''        env:
          DISPATCH_SLOT: ${{ inputs.slot }}
          EVENT_SCHEDULE: ${{ github.event.schedule }}
'''

PRODUCER_ENV_NEW = '''        env:
          # Fed by the resolver so repository_dispatch reaches the producer at
          # all -- `inputs.slot` is empty for that event.
          DISPATCH_SLOT: ${{ steps.resolve.outputs.slot }}
          RESOLVED_DATE: ${{ steps.resolve.outputs.decision_date }}
          EVENT_SCHEDULE: ${{ github.event.schedule }}
'''

# Anchored with the preceding `esac` because the resolver step this patch
# inserts also assigns DECISION_DATE, at a deeper indent -- and the bare
# 10-space form is a SUBSTRING of the 12-space one, so an unanchored replace
# rewrites the resolver instead of the producer.
DATE_OLD = '''          esac
          DECISION_DATE=$(TZ=Asia/Seoul date +%F)
'''
DATE_NEW = '''          esac
          # Supplied by the resolver so every trigger agrees on one date.
          DECISION_DATE="$RESOLVED_DATE"
'''

SUMMARY_OLD = '''          {
            echo "## Atlas Daily Briefing — $SLOT $DECISION_DATE"
            echo
            cat "$CAPTURE_PATH/briefing.md"
            echo
            cat /tmp/investment-review-delivery.md
          } >> "$GITHUB_STEP_SUMMARY"
'''

SUMMARY_NEW = '''          # The rendered briefing now reaches a person only through the
          # finalization gate, which delivers it once, records what was
          # transmitted, and refuses when a verdict is HOLD or an approval is
          # missing. This step only hands the gate its input.
          if [ -f /tmp/investment-review-delivery.md ]; then
            cp /tmp/investment-review-delivery.md "$RUNNER_TEMP/consume.md"
            echo "consume_ready=true" >> "$GITHUB_OUTPUT"
          else
            echo "consume_ready=false" >> "$GITHUB_OUTPUT"
          fi
'''

RESOLVER_STEP = '''      # Resolves slot/mode/date for all three triggers BEFORE the producer runs.
      # It cannot live inside the producer step: in `drain` mode that step is
      # skipped entirely, so the decision to skip it cannot be made there.
      - name: Resolve slot, mode and decision date
        id: resolve
        env:
          DISPATCH_SLOT: ${{ inputs.slot }}
          DISPATCH_MODE: ${{ inputs.mode }}
          DISPATCH_DATE: ${{ inputs.decision_date }}
          EVENT_SCHEDULE: ${{ github.event.schedule }}
          EVENT_ACTION: ${{ github.event.action }}
          PAYLOAD_SLOT: ${{ github.event.client_payload.slot }}
          PAYLOAD_MODE: ${{ github.event.client_payload.mode }}
          PAYLOAD_DATE: ${{ github.event.client_payload.decision_date }}
        run: |
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
              SLOT="${DISPATCH_SLOT:-}"
              MODE="${DISPATCH_MODE:-brief}"
              DECISION_DATE="${DISPATCH_DATE:-}"
              ;;
            repository_dispatch)
              # The event type is the authority, not the payload. A payload
              # claiming mode=brief must not be able to turn a drain request
              # into a briefing run.
              case "$EVENT_ACTION" in
                atlas-finalization-drain) MODE="drain" ;;
                atlas-briefing-run)       MODE="brief" ;;
                *) echo "STOP: unsupported dispatch type '$EVENT_ACTION'" >&2; exit 2 ;;
              esac
              if [ -n "${PAYLOAD_MODE:-}" ] && [ "${PAYLOAD_MODE}" != "$MODE" ]; then
                echo "STOP: client_payload.mode '${PAYLOAD_MODE}' contradicts dispatch type '$EVENT_ACTION'" >&2
                exit 2
              fi
              SLOT="${PAYLOAD_SLOT:-}"
              DECISION_DATE="${PAYLOAD_DATE:-}"
              ;;
            *) echo "STOP: unsupported event ${{ github.event_name }}" >&2; exit 2 ;;
          esac
          case "$MODE" in
            brief|drain) ;;
            *) echo "STOP: unsupported mode '$MODE'" >&2; exit 2 ;;
          esac
          if [ "$MODE" = "brief" ]; then
            case "$SLOT" in
              morning|evening) ;;
              *) echo "STOP: brief mode requires a slot" >&2; exit 2 ;;
            esac
          fi
          if [ -n "$DECISION_DATE" ]; then
            echo "$DECISION_DATE" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' \\
              || { echo "STOP: malformed decision_date '$DECISION_DATE'" >&2; exit 2; }
          else
            DECISION_DATE=$(TZ=Asia/Seoul date +%F)
          fi
          echo "slot=$SLOT" >> "$GITHUB_OUTPUT"
          echo "mode=$MODE" >> "$GITHUB_OUTPUT"
          echo "decision_date=$DECISION_DATE" >> "$GITHUB_OUTPUT"

'''

PRODUCER_NEW = PRODUCER_NAME + "        if: steps.resolve.outputs.mode == 'brief'\n"

NEW_STEPS = '''
      - name: Seal briefing for finalization
        if: steps.resolve.outputs.mode == 'brief' && steps.briefing.outputs.capture_path != ''
        run: |
          set -euo pipefail
          ARGS=""
          if [ "${{ steps.briefing.outputs.consume_ready }}" = "true" ]; then
            ARGS="--consume-output $RUNNER_TEMP/consume.md"
          fi
          python3 .github/scripts/briefing_finalization.py seal \\
            --slot "${{ steps.resolve.outputs.slot }}" \\
            --decision-date "${{ steps.resolve.outputs.decision_date }}" \\
            --repo-root . $ARGS

      # The sealed payload must be durable before delivery. `seal` is idempotent
      # on identical input, so a retry reuses the same draft, marker and clock.
      - name: Publish sealed draft
        if: steps.resolve.outputs.mode == 'brief' && steps.briefing.outputs.capture_path != ''
        env:
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/briefing/finalization
          git diff --cached --quiet || {
            git commit -m "finalization seal ${{ steps.resolve.outputs.decision_date }} ${{ steps.resolve.outputs.slot }}"
            git push origin "HEAD:$DEFAULT_BRANCH"
          }

      # Record any verdict a previous run published but died before ingesting.
      # Without this a machine HOLD left unrecorded by a dead runner would be
      # ingested for the first time AFTER the validator had already decided
      # there was nothing to withdraw -- costing the fixed briefing a round.
      # Non-fatal on purpose: an unrecordable verdict still fails the gate
      # closed at drain, and swallowing it here would only skip the validator.
      - name: Reconcile verdicts left by a previous run
        if: steps.resolve.outputs.mode == 'brief' && steps.briefing.outputs.capture_path != ''
        run: |
          set -uo pipefail
          python3 .github/scripts/briefing_finalization.py ingest \\
            --slot "${{ steps.resolve.outputs.slot }}" \\
            --decision-date "${{ steps.resolve.outputs.decision_date }}" \\
            --repo-root . || echo "::warning::pending verdict could not be recorded; the gate will fail closed at drain"

      # Deterministic checks only. A clean result is NOT a final PASS: it writes
      # a machine record and leaves the gate's verdict slot open, so the existing
      # fail-open policy still owns the outcome. Exits 0 even on HOLD -- the
      # verdict, not the exit code, is what blocks.
      - name: Run deterministic validator
        if: steps.resolve.outputs.mode == 'brief' && steps.briefing.outputs.capture_path != ''
        run: |
          set -euo pipefail
          python3 .github/scripts/briefing_validator.py \\
            --slot "${{ steps.resolve.outputs.slot }}" \\
            --decision-date "${{ steps.resolve.outputs.decision_date }}" \\
            --repo-root . --emit-inbox

      - name: Publish validator verdict
        if: steps.resolve.outputs.mode == 'brief' && steps.briefing.outputs.capture_path != ''
        env:
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          git add data/briefing/finalization
          git diff --cached --quiet || {
            git commit -m "machine validation ${{ steps.resolve.outputs.decision_date }} ${{ steps.resolve.outputs.slot }}"
            git push origin "HEAD:$DEFAULT_BRANCH"
          }

      # drain ingests each stream's authoritative verdict THEN delivers, per
      # item, and exits non-zero while any due slot is still undelivered. A
      # briefing nobody received is not a green build.
      - name: Ingest verdicts and deliver
        id: gate
        env:
          ATLAS_APPROVAL_PUBKEY_FINGERPRINT: ${{ secrets.ATLAS_APPROVAL_PUBKEY_FINGERPRINT }}
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -uo pipefail
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          python3 .github/scripts/briefing_finalization.py drain \\
            --repo-root . \\
            --channel github_step_summary \\
            --required-channel github_step_summary > drain.json
          RC=$?
          python3 .github/scripts/report_drain.py drain.json
          rm -f drain.json
          exit "$RC"

      - name: Commit finalization artifacts
        if: always()
        env:
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/briefing/finalization
          git diff --cached --quiet || {
            git commit -m "finalization ${{ steps.resolve.outputs.decision_date }}"
            git push origin "HEAD:$DEFAULT_BRANCH"
          }
'''


def patch(text: str) -> tuple[str, list[str]]:
    problems = []
    for label, anchor in (
        ("on: block", ON_BLOCK_OLD),
        ("producer step name", PRODUCER_NAME),
        ("producer env", PRODUCER_ENV_OLD),
        ("DECISION_DATE assignment", DATE_OLD),
        ("human-reaching braced summary block", SUMMARY_OLD),
    ):
        count = text.count(anchor)
        if count != 1:
            problems.append(f"anchor {label!r}: expected exactly 1 occurrence, found {count}")
    if "steps.resolve.outputs" in text:
        problems.append("workflow already patched (found steps.resolve.outputs)")
    if problems:
        return text, problems

    # Order matters: every in-place edit runs BEFORE the resolver is inserted,
    # so no anchor can accidentally match text this patch itself added.
    out = text
    out = out.replace(ON_BLOCK_OLD, ON_BLOCK_NEW, 1)
    out = out.replace(PRODUCER_ENV_OLD, PRODUCER_ENV_NEW, 1)
    out = out.replace(DATE_OLD, DATE_NEW, 1)
    out = out.replace(SUMMARY_OLD, SUMMARY_NEW, 1)
    out = out.replace(PRODUCER_NAME, RESOLVER_STEP + PRODUCER_NEW, 1)
    out = out.rstrip("\n") + "\n" + NEW_STEPS

    # postconditions -- an anchor matching is not proof the result is correct
    post = []
    if out.count("  workflow_dispatch:\n") != 1:
        post.append(f"expected exactly one workflow_dispatch, got {out.count('  workflow_dispatch:')}")
    if "GITHUB_STEP_SUMMARY" in out:
        post.append("a $GITHUB_STEP_SUMMARY write survived outside the gate")
    if "steps.resolve.outputs.slot" not in out:
        post.append("resolver output is not wired into the producer")
    if "$(TZ=Asia/Seoul date +%F)" in out.split("- name: Publish provider-free")[1].split("- name: Seal")[0]:
        post.append("producer still computes its own decision date")
    order = ["- name: Seal briefing for finalization",
             "- name: Publish sealed draft",
             "- name: Reconcile verdicts left by a previous run",
             "- name: Run deterministic validator",
             "- name: Ingest verdicts and deliver"]
    positions = [out.find(name) for name in order]
    if -1 in positions:
        post.append(f"a wiring step is missing: {order[positions.index(-1)]!r}")
    elif positions != sorted(positions):
        post.append("wiring steps are out of order; the validator must run after "
                    "reconciliation and before the gate")
    if post:
        return text, ["postcondition failed: " + p for p in post]
    return out, []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-blob", default=EXPECTED_BLOB,
                        help="refuse unless the workflow's git blob matches; '' to skip")
    args = parser.parse_args()
    if not (args.dry_run or args.apply):
        parser.error("choose --dry-run or --apply")

    path = Path(args.repo_root) / WORKFLOW
    raw = path.read_bytes()
    actual_blob = git_blob_sha(raw)
    if args.expect_blob and actual_blob != args.expect_blob:
        print(f"REFUSING TO PATCH -- workflow blob is {actual_blob}, expected {args.expect_blob}.\n"
              "The live file changed since this patch was written against it. Nothing was "
              "changed. Re-derive the anchors against the current file.", file=sys.stderr)
        return 2

    original = raw.decode("utf-8")
    patched, problems = patch(original)
    if problems:
        print("REFUSING TO PATCH:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    sys.stdout.writelines(difflib.unified_diff(
        original.splitlines(True), patched.splitlines(True),
        fromfile=f"a/{WORKFLOW}", tofile=f"b/{WORKFLOW}"))
    if args.apply:
        path.write_text(patched, encoding="utf-8")
        print(f"\napplied to {path} (was blob {actual_blob})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
