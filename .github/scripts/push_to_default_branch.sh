#!/usr/bin/env bash
# Push the current HEAD commit(s) to the default branch, rebasing onto whatever
# landed meanwhile.
#
# This repository commits collector evidence every few minutes, so a briefing
# step that builds a commit and pushes once loses the race whenever it is slow
# or late: the 2026-09-15 evening and 2026-09-16 morning briefings both died at
# "Publish sealed draft" with "failed to push some refs" (runs 34913230305 and
# 35038562866) after the briefing itself had succeeded.
#
# Fail-closed: a rebase conflict aborts the rebase and returns non-zero, and so
# does exhausting the attempts. The caller's commit is only ever published as a
# replay of the same paths on top of the newest default branch.
set -euo pipefail

BRANCH="${1:?default branch required}"
MAX_ATTEMPTS="${2:-5}"

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
  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort || true
    echo "STOP: rebase onto origin/$BRANCH conflicted; nothing was published" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 5
done
