#!/usr/bin/env bash
# Push the current HEAD commit(s) to the default branch, rebasing onto whatever
# landed meanwhile.
#
# This repository commits collector evidence every few minutes, so a producer
# step that builds a commit and pushes once loses the race whenever it is slow
# or late: the 2026-09-15 evening and 2026-09-16 morning briefings both died at
# "Publish sealed draft" with "failed to push some refs" (runs 34913230305 and
# 35038562866) after the briefing itself had succeeded, and the same day
# fred-dexkous-fx.yml and spdr-sector-holdings.yml raced each other on their
# first live runs (34926979498 / 34926977666).
#
# THIS IS THE ONE PUSH RETRY IMPLEMENTATION for append-only evidence producers.
# Before 2026-09-18 there were six divergent copies (a shared 5-attempt script,
# fred's and spdr's 3-attempt inline copies, population's fourth copy, plus
# macro-event-calendar's and kr-paper-runtime-daily-publish's own variants) and
# 26 further workflows with no retry at all. Divergence is how a new producer
# ships without the protection its siblings already have -- exactly what
# happened when population-symbol-observation-daily.yml was turned on daily.
# test/test_push_retry_consolidation.py fails if a new commit-and-push step is
# added without calling this script.
#
# USE THIS ONLY FOR APPEND-ONLY EVIDENCE, where replaying the same commit on a
# newer base is still correct. Do NOT use it for a result that is a function of
# the branch tip (a briefing locator, a dynamic clock, a runtime decision built
# from the current head): rebasing those republishes a stale computation as if
# it were fresh. Those producers must fail closed or recompute instead, and are
# listed as NO_REBASE_BY_DESIGN in the consolidation test.
#
# Fail-closed guarantees, all covered by tests:
#   * a rebase conflict aborts the rebase and returns non-zero -- nothing is
#     published, and the working tree is not left mid-rebase;
#   * exhausting MAX_ATTEMPTS returns non-zero;
#   * the push is never forced, so a commit on the branch is never discarded;
#   * if the rebase drops this run's commit (git drops a commit that became
#     empty because the identical content already landed upstream), that is
#     reported loudly rather than exiting 0 in silence having published nothing.
set -euo pipefail

BRANCH="${1:?default branch required}"
MAX_ATTEMPTS="${2:-5}"
# Seconds added per attempt (attempt 1 waits BACKOFF, attempt 2 waits 2*BACKOFF,
# ...). rotation-confirmation.yml carried a 15s step before consolidation and
# passes 15 here so its backoff is preserved exactly.
BACKOFF_SECONDS="${PUSH_RETRY_BACKOFF_SECONDS:-5}"

case "$MAX_ATTEMPTS" in
  ''|*[!0-9]*) echo "STOP: MAX_ATTEMPTS must be a positive integer, got '$MAX_ATTEMPTS'" >&2; exit 1 ;;
esac
[ "$MAX_ATTEMPTS" -ge 1 ] || { echo "STOP: MAX_ATTEMPTS must be >= 1" >&2; exit 1; }

attempt=1
while true; do
  if git push origin "HEAD:$BRANCH"; then
    exit 0
  fi
  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "STOP: push to $BRANCH failed after $attempt attempts" >&2
    exit 1
  fi
  echo "$BRANCH advanced while this commit was built; rebasing (attempt $attempt)" >&2
  git fetch --quiet origin "$BRANCH"
  before=$(git rev-list --count "origin/$BRANCH..HEAD")
  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort || true
    echo "STOP: rebase onto origin/$BRANCH conflicted; nothing was published" >&2
    exit 1
  fi
  after=$(git rev-list --count "origin/$BRANCH..HEAD")
  if [ "$before" -gt 0 ] && [ "$after" -eq 0 ]; then
    # Not a failure: the identical content is already on the branch, so there is
    # nothing left to publish. Say so, because exiting 0 here without a word is
    # indistinguishable from a successful publish in the run log.
    echo "NOTE: rebase onto origin/$BRANCH dropped this run's $before commit(s) as empty; the identical content is already published. Nothing further to push." >&2
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep $((BACKOFF_SECONDS * (attempt - 1)))
done
