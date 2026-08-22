#!/usr/bin/env python3
"""AUDIT_CONFIRMED_MISS registry (CIO review round 1 on PR #211, item 4).

Reads PR #210's own committed, real audit output --
`evidence/audit/pit_replay/opportunity_miss_episodes.json` -- rather than
hardcoding a guessed subject/date list. An episode qualifies for the
AUDIT_CONFIRMED_MISS exception only if its `root_cause` is
`ACTION_CONVERSION_FAILURE` (a real, then-available signal that was
detectable and actionable but wasn't acted on -- the strongest category PR
#210's audit produces; other root causes like SIGNAL_MISS/GATE_BLOCK/
DATA_FAILURE/DECISION_LATENCY/NO_POSITION_RULE do not represent a confirmed
missed opportunity in the same sense and are deliberately excluded here).

This module is READ-ONLY against that committed file -- it never writes,
never re-runs the replay, and never invents an entry. If the file is
missing or malformed, `AUDIT_CONFIRMED_MISS_TAG` lookups fail closed
(return no match) rather than crash the whole Dynamic Clock run; the caller
(`clock/review_candidate.py`) treats "no match" as "no exception applies",
which is the conservative, safe default.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISS_EPISODES_PATH = ROOT / "evidence" / "audit" / "pit_replay" / "opportunity_miss_episodes.json"
QUALIFYING_ROOT_CAUSE = "ACTION_CONVERSION_FAILURE"

_cache: list[dict] | None = None


def _load() -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    if not MISS_EPISODES_PATH.is_file():
        _cache = []
        return _cache
    try:
        doc = json.loads(MISS_EPISODES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _cache = []
        return _cache
    if not isinstance(doc, list):
        _cache = []
        return _cache
    _cache = [e for e in doc if isinstance(e, dict) and e.get("root_cause") == QUALIFYING_ROOT_CAUSE]
    return _cache


def confirmed_miss_for(subject: str, detected_at: str) -> dict | None:
    """Returns the real PR #210 Miss Episode record if `subject` has a
    QUALIFYING_ROOT_CAUSE episode whose [episode_start_date, episode_end_date]
    window covers `detected_at`, else None. Deterministic, pure read of
    already-committed evidence -- no wall clock, no re-computation."""
    for episode in _load():
        if episode.get("subject") != subject:
            continue
        start = episode.get("episode_start_date")
        end = episode.get("episode_end_date")
        if start is None or end is None:
            continue
        if start <= detected_at <= end:
            return episode
    return None
