#!/usr/bin/env python3
"""Daily scheduled run for the KR / US population symbol observation.

Why this script exists at all
----------------------------
``decision/korea_population_symbol_observation.py`` and
``decision/us_population_symbol_observation.py`` had **no**
``.github/workflows`` trigger of any kind (``watchdog/daily_producer_freshness``
recorded exactly that), so the two observations sat at 2026-09-10 / 2026-09-11
while the committed source universes they consume had already advanced to
2026-09-16.  This is the scheduled entrypoint that closes that gap.

It adds **no** collection: every input is already-committed evidence in this
repository.  There is no network call here, and no pass rule -- ``passed_count``
stays 0 with ``NO_RATIFIED_PASS_RULE_ZERO_IS_ABSENCE_OF_RULE`` and every
authority flag stays false.  Both are asserted below, so a future edit that
tries to smuggle a rule in through the scheduled path fails the run.

The already-captured guard lives HERE, not in workflow YAML
-----------------------------------------------------------
``decision/population_symbol_observation.persist_packet`` **overwrites** an
existing packet with ``outcome="superseded_generation"`` whenever the
``generation_id`` differs.  The generation is a hash over the *rolling* inputs
(stage history, the bounded review pointer, the KRX watchlist), so simply
re-running ``build()`` for a date that is already committed would rewrite
committed evidence as soon as any of those pointers moved -- append-only
violated, silently.

So this script resolves the session date FIRST, from committed evidence only,
and never calls ``build()`` for a date whose packet already exists.  An
already-captured date takes the ``reverify`` path instead and reports the
repository's established ``verified_existing`` outcome (the same token
``persist_packet`` returns, and the same one the p2-03 pair workflow prints as
``no change (verified_existing)``).  A repeat run is therefore a no-op that
neither rewrites nor fails.

Because the guard is a property of this script rather than of a workflow
``if:`` condition, a ``workflow_dispatch`` run and a ``schedule`` run execute
byte-identical logic.  That is the guard-equivalence the server-side dispatcher
requires before it may be registered to catch missed slots.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from decision import population_symbol_observation as CORE  # noqa: E402

MARKETS = ("KR", "US")

# The observation is an observation.  If either of these ever stops holding,
# something has turned a read-only packet into a decision and the run must fail
# rather than commit it.
REQUIRED_PASSED_COUNT = 0
REQUIRED_PASSED_SEMANTICS = "NO_RATIFIED_PASS_RULE_ZERO_IS_ABSENCE_OF_RULE"

#: Producer refusals that mean "this session cannot be produced honestly from
#: the evidence committed right now" -- a data condition, not a broken
#: producer.  They are recorded as ``blocked`` and the run stays green, because
#: a scheduled job that goes red every day for a condition nobody can fix by
#: retrying teaches its readers to ignore it.  The gap stays visible: nothing is
#: committed, the reason is printed, and the freshness watchdog still reports
#: the producer as behind its source.
#:
#: Everything NOT in this set is a real failure and is allowed to fail the run.
NOT_PRODUCIBLE_CODES = frozenset({
    # The committed bounded review cannot be rebuilt from the committed market
    # data pointer -- the two disagree, so no honest packet exists for it.
    "US_BOUNDED_REVIEW_NOT_REPRODUCIBLE",
    "KR_BOUNDED_REVIEW_NOT_REPRODUCIBLE",
    # The market data on hand describes a different session than the newest
    # committed universe claims -- the capture already holds a later session,
    # or does not reach this one.
    "US_BOUNDED_REVIEW_SESSION_MISMATCH",
    "KR_BOUNDED_REVIEW_SESSION_MISMATCH",
    # The session's own universe / capture is not committed yet.
    "KR_UNIVERSE_FOR_SESSION_MISSING",
    "US_UNIVERSE_FOR_SESSION_MISSING",
    # A capture with no daily bars cannot cover any session.
    "US_MARKET_DATA_NO_SESSION_BARS",
})


def _adapter(market: str):
    """The market adapter, via the core loader (single implementation)."""
    return CORE._adapter(market)


def resolve_session_date(market: str, root: Path = ROOT) -> str:
    """Session date the producer would pick, from committed evidence only.

    KR takes it from ``data/latest_korea_market_signals.json``'s ``as_of_date``
    and then requires an exact-date ``krx_global_universe`` directory; US takes
    the newest committed ``us_global_universe`` directory's ``source_date``.
    Neither reads the network and neither builds anything, so this is cheap
    enough to run before the guard.
    """
    return _adapter(market).default_inputs(root)["session_date"]


def output_dir_for(market: str, session_date: str, root: Path = ROOT) -> Path:
    default_root = CORE.DEFAULT_OUTPUT_ROOTS[market]
    if root != ROOT:
        default_root = root / default_root.relative_to(ROOT)
    return default_root / session_date


def already_captured(output_dir: Path) -> bool:
    """True when this date's packet is already committed (gz or plain)."""
    return CORE._packet_target(output_dir) is not None


def _assert_observation_only(summary: dict, authority: dict, where: str) -> None:
    """Fail closed: no pass rule, no authority, however we got here."""
    if summary.get("passed_count") != REQUIRED_PASSED_COUNT:
        raise SystemExit(f"STOP: {where}: passed_count={summary.get('passed_count')!r} -- a pass rule was introduced")
    if summary.get("passed_semantics") != REQUIRED_PASSED_SEMANTICS:
        raise SystemExit(f"STOP: {where}: passed_semantics={summary.get('passed_semantics')!r} changed")
    if authority.get("observation_only") is not True:
        raise SystemExit(f"STOP: {where}: observation_only is not true")
    granted = sorted(k for k, v in authority.items() if k != "observation_only" and v is not False)
    if granted:
        raise SystemExit(f"STOP: {where}: authority granted: {granted}")


def _refusal_code(exc: Exception) -> str:
    """The bare code from a producer refusal (``CODE`` or ``CODE:detail``)."""
    return str(exc).split(":", 1)[0]


def observe(market: str, *, generated_at: str, root: Path = ROOT, work_dir: Path | None = None) -> dict:
    """One market's daily step: skip an already-captured date, else build it."""
    try:
        session_date = resolve_session_date(market, root)
    except CORE.PopulationSymbolObservationError as exc:
        if _refusal_code(exc) in NOT_PRODUCIBLE_CODES:
            return {"market": market, "session_date": None, "lookup_at": generated_at,
                    "outcome": "blocked", "already_captured": False,
                    "blocked_reason": str(exc), "wrote_anything": False}
        raise
    output_dir = output_dir_for(market, session_date, root)
    record = {"market": market, "session_date": session_date,
              "output_dir": output_dir.relative_to(root).as_posix(), "lookup_at": generated_at}

    if already_captured(output_dir):
        # Append-only: re-read and re-validate the committed bytes, never rebuild.
        result = CORE.reverify(output_dir)
        packet = CORE.read_packet_file(CORE._packet_target(output_dir))
        _assert_observation_only(packet["summary"], packet["authority"], f"{market} existing packet")
        record.update(outcome="verified_existing", already_captured=True,
                      generation_id=result["generation_id"], payload_sha256=result["payload_sha256"],
                      file_sha256=result["file_sha256"], wrote_anything=False)
        return record

    # Not captured yet -- build it.  Chunks/receipt go to a scratch work dir so
    # no intermediate state is ever staged for commit.
    try:
        built = CORE.build(market, generated_at=generated_at, work_dir=work_dir,
                           output_dir=output_dir, compress=True)
    except CORE.PopulationSymbolObservationError as exc:
        if _refusal_code(exc) in NOT_PRODUCIBLE_CODES:
            record.update(outcome="blocked", already_captured=False,
                          blocked_reason=str(exc), wrote_anything=False)
            return record
        raise
    packet = built["packet"]
    if packet is None:
        raise SystemExit(f"STOP: {market} {session_date}: build did not complete ({built['resume']})")
    _assert_observation_only(packet["summary"], packet["authority"], f"{market} new packet")
    persist = built["persist"]
    if persist["outcome"] != "populated":
        # The guard above should make this unreachable; refuse rather than
        # quietly accept a supersede of committed evidence.
        raise SystemExit(f"STOP: {market} {session_date}: unexpected persist outcome {persist['outcome']!r}")
    # Fresh-process-equivalent re-read of what we just wrote.
    CORE.reverify(output_dir)
    record.update(outcome="populated", already_captured=False,
                  generation_id=packet["generation_id"], payload_sha256=packet["payload_sha256"],
                  population_count=packet["summary"].get("population_count"),
                  passed_count=packet["summary"]["passed_count"], wrote_anything=True)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=MARKETS, action="append",
                        help="repeatable; default is every market")
    parser.add_argument("--generated-at", required=True,
                        help="UTC lookup time (run time); sources must be no newer")
    parser.add_argument("--work-dir", type=Path,
                        help="scratch dir for chunks/receipt; must be outside this repository")
    args = parser.parse_args(argv)
    markets = tuple(dict.fromkeys(args.market or MARKETS))

    work_root = args.work_dir
    tmp = None
    if work_root is None:
        tmp = tempfile.TemporaryDirectory(prefix="population-observation-")
        work_root = Path(tmp.name)
    if CORE.inside_public_repository(work_root):
        raise SystemExit(f"STOP: --work-dir must be outside this repository: {work_root}")

    try:
        records = [observe(market, generated_at=args.generated_at, work_dir=Path(work_root) / market)
                   for market in markets]
    finally:
        if tmp is not None:
            tmp.cleanup()

    print(json.dumps({"lookup_at": args.generated_at, "markets": list(markets),
                      "wrote_anything": any(r["wrote_anything"] for r in records),
                      "observations": records}, ensure_ascii=False, indent=2, sort_keys=True))
    # A blocked market is not an error, but it must not be quiet either -- say so
    # on stderr so it is visible in the job log without failing the run.
    for record in records:
        if record["outcome"] == "blocked":
            print(f"BLOCKED {record['market']}: not producible from committed evidence "
                  f"right now -- {record['blocked_reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
