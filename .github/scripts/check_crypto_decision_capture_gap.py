#!/usr/bin/env python3
"""Capture-to-decision timing guard for the P9-06 -> Crypto PAPER decision.

``decision/crypto_paper_decision_snapshot.py`` re-evaluates the RATIFIED
P9-01 CRYPTO freshness policy (``P9_06_UPBIT_CRYPTO_PAPER_V1``) at its own
``generated_at``.  Every second between the end of the realtime capture and
that ``generated_at`` is therefore added verbatim to every ticker's
provider age.  Until 2026-09-14 the scheduler ran a separate 30 second
decision-isolated validation capture in between, so the decision always
observed tickers at least 30 seconds old and the ratified 20 second
provider-age limit made every ticker STALE -- a pipeline-ordering defect,
not a market-data one.

This helper measures that gap from the exact bytes the decision consumed:

* capture end = the realtime run's ``run.status.generated_at`` (the same
  ``capture_observed_at`` the decision module rebuilds the ratified result
  at), and
* decision time = the decision step's ``generated_at`` (cross-checked against
  the written decision packet when one exists).  The decision module stamps a
  new packet at the first whole second no realtime input postdates
  (``decision_time_not_before_inputs``), which is the sampled second or the
  next one; the guard then measures from the packet's instant.

ENGINEERING BUDGET, NOT POLICY.  ``ENGINEERING_BUDGET_SECONDS`` is a
scheduler-ordering regression budget for GitHub Actions step hand-off.  It is
not a freshness threshold, it is never read by any freshness evaluation, it
does not relax or tighten the ratified 20s provider-age / 3s transport-delay
limits, and changing it changes no decision outcome -- it only decides
whether this guard step fails loudly.  With the decision step placed directly
after the capture step the expected gap is 0-1s (both timestamps are
second-truncated); the budget leaves room for runner hand-off jitter while
staying far below the ratified 20s CRYPTO provider-age limit.

Read-only: no network, no order/exchange endpoint, no repository writes other
than the optional ``$GITHUB_OUTPUT`` / ``$GITHUB_STEP_SUMMARY`` lines.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys


ENGINEERING_BUDGET_SECONDS = 5
WITHIN_BUDGET = "WITHIN_BUDGET"
OVER_BUDGET = "OVER_BUDGET"
UTC = dt.timezone.utc


class CaptureGapError(RuntimeError):
    """Invalid or inconsistent guard input (fails closed)."""


def _parse_utc(value, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CaptureGapError(f"{label}_NOT_CANONICAL_UTC")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CaptureGapError(f"{label}_NOT_CANONICAL_UTC") from exc
    return parsed.astimezone(UTC)


def _read_json(path: Path, label: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaptureGapError(f"{label}_UNREADABLE") from exc
    if not isinstance(payload, dict):
        raise CaptureGapError(f"{label}_NOT_OBJECT")
    return payload


def capture_observed_at(realtime_run_path: Path) -> dt.datetime:
    record = _read_json(realtime_run_path, "REALTIME_RUN")
    run = record.get("run")
    status = run.get("status") if isinstance(run, dict) else None
    if not isinstance(status, dict):
        raise CaptureGapError("REALTIME_RUN_STATUS_MISSING")
    return _parse_utc(status.get("generated_at"), "REALTIME_RUN_GENERATED_AT")


def _latest_realtime_input_at(realtime_run_path: Path) -> dt.datetime | None:
    run = _read_json(realtime_run_path, "REALTIME_RUN").get("run") or {}
    values = [run.get("ended_at"), (run.get("status") or {}).get("generated_at")]
    values += [(row or {}).get("received_at") for row in (run.get("latest_public_messages") or {}).values()]
    values += [(row or {}).get("received_at") for row in (run.get("message_log") or [])]
    instants = [_parse_utc(value, "REALTIME_INPUT") for value in values if isinstance(value, str)]
    return max(instants) if instants else None


def measure(
    *,
    realtime_run_path: Path,
    decision_generated_at: str,
    decision_packet_path: Path | None = None,
    budget_seconds: int = ENGINEERING_BUDGET_SECONDS,
) -> dict:
    if (
        isinstance(budget_seconds, bool)
        or not isinstance(budget_seconds, int)
        or budget_seconds < 0
    ):
        raise CaptureGapError("ENGINEERING_BUDGET_INVALID")
    decision_at = _parse_utc(decision_generated_at, "DECISION_GENERATED_AT")
    if decision_packet_path is not None:
        packet = _read_json(decision_packet_path, "DECISION_PACKET")
        if packet.get("generated_at") != decision_generated_at:
            stamped = _parse_utc(packet.get("generated_at"), "DECISION_PACKET_GENERATED_AT")
            latest_input = _latest_realtime_input_at(realtime_run_path)
            # Only the input-bounded +1s stamp is accepted: some realtime input
            # postdates the sampled second and none postdates the packet.
            if not (
                stamped - decision_at == dt.timedelta(seconds=1)
                and latest_input is not None and decision_at < latest_input <= stamped
            ):
                raise CaptureGapError("DECISION_PACKET_GENERATED_AT_MISMATCH")
            decision_generated_at = packet["generated_at"]
            decision_at = stamped
    captured_at = capture_observed_at(realtime_run_path)
    gap = (decision_at - captured_at).total_seconds()
    if gap < 0:
        raise CaptureGapError("DECISION_PRECEDES_CAPTURE")
    return {
        "capture_observed_at": captured_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decision_generated_at": decision_generated_at,
        "gap_seconds": int(gap) if gap == int(gap) else gap,
        "engineering_budget_seconds": budget_seconds,
        "status": WITHIN_BUDGET if gap <= budget_seconds else OVER_BUDGET,
    }


def _append(env_name: str, text: str) -> None:
    path = os.environ.get(env_name)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--realtime-run-path", required=True, type=Path)
    parser.add_argument("--decision-generated-at", required=True)
    parser.add_argument("--decision-packet", type=Path, default=None)
    args = parser.parse_args(argv)
    decision_packet = args.decision_packet
    if decision_packet is not None and str(decision_packet) == "":
        decision_packet = None
    try:
        result = measure(
            realtime_run_path=args.realtime_run_path,
            decision_generated_at=args.decision_generated_at,
            decision_packet_path=decision_packet,
        )
    except CaptureGapError as exc:
        print(json.dumps({"status": "INVALID", "reason": str(exc)}, sort_keys=True))
        _append("GITHUB_OUTPUT", "status=INVALID\n")
        return 2
    print(json.dumps(result, sort_keys=True))
    _append(
        "GITHUB_OUTPUT",
        f"status={result['status']}\n"
        f"gap_seconds={result['gap_seconds']}\n"
        f"engineering_budget_seconds={result['engineering_budget_seconds']}\n",
    )
    _append(
        "GITHUB_STEP_SUMMARY",
        "### Crypto capture-to-decision gap\n\n"
        f"- capture observed_at: `{result['capture_observed_at']}`\n"
        f"- decision generated_at: `{result['decision_generated_at']}`\n"
        f"- gap: **{result['gap_seconds']}s** (engineering budget "
        f"{result['engineering_budget_seconds']}s; not a freshness policy)\n"
        f"- status: `{result['status']}`\n",
    )
    return 0 if result["status"] == WITHIN_BUDGET else 1


if __name__ == "__main__":
    sys.exit(main())
