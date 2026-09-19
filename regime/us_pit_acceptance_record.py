#!/usr/bin/env python3
"""US PIT acceptance record generator (US-DATA-1 U3).

``regime/us_paper_runtime.py::load_acceptance`` is the only authority on this
artifact's shape, and it is exact-match.  Given the adoption identity's
``pit_acceptance`` block it requires, in order:

1. ``bundle_path``, ``acceptance_record_path`` — repo-relative, no ``..``;
2. ``bundle_sha256``, ``acceptance_record_sha256``, ``replay_report_sha256`` —
   all three present and 64 lowercase hex;
3. ``sha256(bundle file bytes) == bundle_sha256`` and
   ``sha256(record file bytes) == acceptance_record_sha256``;
4. the record's ``schema_version == "us_pit_acceptance_record/1"``,
   ``market == "US"``, and ``bundle_sha256`` equal to the bound bundle hash;
5. ``regime.us_historical_replay_population.validate_population(bundle)`` passes
   over the bundle's own bytes — every time, not once at adoption;
6. ``regime.market_scoped_pit_acceptance.evaluate_market_pit_acceptance("US",
   bundle)`` re-derived and compared to the record's ``evaluation`` field
   CANONICALLY BYTE FOR BYTE, and its ``status`` must be ``PIT_ACCEPTED``;
7. ``evaluation["replay_report_sha256"] == replay_report_sha256``, and that hash
   must also equal ``payload_sha256(replay_common_v1(_build_sequence(...)))``;
8. ``history_last_session_date`` must equal the last step's ``as_of_date``.

The runtime reads only ``schema_version``, ``market``, ``bundle_sha256`` and
``evaluation`` out of the record, so every other field below is audit metadata —
and is deliberately derived, never asserted, so a re-run is byte-identical.

This module writes nothing time-varying: the record is a pure function of the
bundle bytes and the committed session calendar, so ``build`` twice over the same
inputs produces the same file and the same sha256 an adoption pins.

It also binds the session calendar, which the runtime checks only indirectly
(``session_plan`` requires ``history_last`` to be a session in the bound calendar
and requires at least one session after it).  Catching that here turns a daily
``HISTORY_LAST_SESSION_NOT_IN_OFFICIAL_CALENDAR`` UNKNOWN into a generation-time
refusal.

Current state, stated plainly because the generator fails closed on it: US PIT
acceptance is reachable in ARITHMETIC but not yet BINDABLE, for one remaining
reason outside this module.

(a) RESOLVED 2026-09-20.  ``config/us_historical_pit_replay_identity_v1.json``
carried ``replay_population_wiring_activated: false``, so the US replay
populated three axes (TREND/RISK_VOL/LIQUIDITY) and left BREADTH/LEADERSHIP
UNKNOWN — common-v1 then classified every step UNKNOWN and no bundle could
observe the four regimes ``PIT_ACCEPTED`` requires.  The flag is now ``true``
(user ratification ``USER_RATIFICATION_US_REPLAY_FLAG_20260920``) and a
1,480-session population over the declared range does reach ``PIT_ACCEPTED``
with all four regimes observed.  ``build`` over such a bundle succeeds.

(b) RESOLVED 2026-09-20, by routing around the contradiction rather than
yielding on either side of it.  There are still no bundle bytes in the
repository to hash — ``.github/workflows/us-regime-historical-replay.yml``
commits nothing, and ``_forbid_tracked_output`` still refuses, by design, to
write historical replay evidence to ANY path inside the checkout — and the
``bundle_path`` binding still refuses an absolute path outright
(``US_PIT_POPULATION_BUNDLE_PATH_INVALID``).  What changed is that
``us_paper_runtime`` grew a second binding mode,
``COMMITTED_REPLAY_RECEIPT_HASH_BOUND_UNCOMMITTED_BUNDLE``: the
``replay_common_v1`` output and a receipt are committed, and the ~64 MiB
population is hash-bound without being committed.  That is KR's long-standing
shape, not a new convention.  ``receipt`` below is the subcommand that produces
the pair; ``build`` still serves the older ``COMMITTED_POPULATION_BUNDLE`` mode
and is unchanged.

So this module now has two products.  ``build`` turns a committed bundle into a
``us_pit_acceptance_record/1``.  ``receipt`` turns an out-of-checkout bundle into
a committed (replay output, ``us_pit_acceptance_receipt/1``) pair whose every
derivable value the runtime recomputes on each evaluation.  Both still refuse,
with named reasons, on anything they cannot derive.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import decision_authority as COMMON  # noqa: E402
from regime import market_scoped_pit_acceptance as PIT  # noqa: E402
from regime import us_paper_runtime as RUNTIME  # noqa: E402


RECORD_SCHEMA = "us_pit_acceptance_record/1"
GENERATOR_PATH = "regime/us_pit_acceptance_record.py"
DEFAULT_BUNDLE_PATH = "evidence/us_regime_replay/population_bundle_v1.json"
DEFAULT_RECORD_PATH = "evidence/us_regime_replay/pit_acceptance_record_v1.json"
DEFAULT_CALENDAR_PATH = "data/us_official_session_calendar_v1.json"

# RECEIPT mode artifacts (see us_paper_runtime's PIT_BINDING_MODE_* commentary).
# Named after KR's own committed pair -- ``final-receipt.json`` beside
# ``common-v1-replay-through-<last session>.json`` under a ``history/`` root --
# because this is KR's binding shape applied to US, not a new convention.
DEFAULT_RECEIPT_PATH = "evidence/us_regime_replay/history/final-receipt.json"
RECEIPT_SCHEMA = "us_pit_acceptance_receipt/1"
RECEIPT_REPLAY_DIR = "evidence/us_regime_replay/history"


def replay_relative_path(history_last: str) -> str:
    return f"{RECEIPT_REPLAY_DIR}/common-v1-replay-through-{history_last}.json"

AUTHORITY_CLOSED = {
    "acceptance_record_only": True,
    "adoption_authorized": False,
    "runtime_binding_authorized": False,
    "strategy_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "capital_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authorized": False,
}


class UsPitAcceptanceRecordError(ValueError):
    """A US PIT acceptance-record invariant failed closed."""


def fail(code: str, detail: str = "") -> None:
    raise UsPitAcceptanceRecordError(f"{code}:{detail}" if detail else code)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def population_module():
    """The bundle's owning validator — the same import the runtime performs."""
    from regime import us_historical_replay_population as POPULATION

    return POPULATION


def _read_json_object(path: Path, code: str) -> tuple[dict, bytes]:
    path = Path(path)
    if not path.is_file():
        fail(code + "_ABSENT", str(path))
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail(code + "_INVALID", str(path))
    if not isinstance(value, dict):
        fail(code + "_INVALID", str(path))
    return value, raw


def _relative(path: Path, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        fail("PATH_OUTSIDE_REPOSITORY", str(path))


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_acceptance_record(
    bundle_raw: bytes,
    *,
    calendar: dict,
    bundle_relative_path: str,
    calendar_relative_path: str,
    calendar_file_sha256: str,
    population=None,
) -> dict:
    """Derive ``us_pit_acceptance_record/1`` from committed bundle bytes.

    Every step is the runtime's own, in the runtime's order, over the bundle's
    FILE bytes — because ``bundle_sha256`` is a hash of those bytes and a record
    built from a re-serialised copy would bind a file that does not exist.
    """
    if not isinstance(bundle_raw, bytes) or not bundle_raw:
        fail("BUNDLE_BYTES_REQUIRED")
    bundle_sha = sha256_bytes(bundle_raw)
    try:
        bundle = json.loads(bundle_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("BUNDLE_JSON_INVALID")
    if not isinstance(bundle, dict):
        fail("BUNDLE_JSON_INVALID")

    module = population_module() if population is None else population
    try:
        module.validate_population(copy.deepcopy(bundle))
    except Exception as exc:  # the owner validator fails closed with its own codes
        fail("BUNDLE_REVALIDATION_FAILED", f"{type(exc).__name__}:{exc}")

    evaluation = PIT.evaluate_market_pit_acceptance("US", bundle)
    if evaluation.get("status") != PIT.STATUS_PIT_ACCEPTED:
        fail(
            "US_PIT_NOT_ACCEPTED",
            f"{evaluation.get('status')}:{','.join(evaluation.get('reasons') or [])}",
        )
    replay_report_sha256 = evaluation.get("replay_report_sha256")
    if not isinstance(replay_report_sha256, str) or RUNTIME.SHA256.fullmatch(replay_report_sha256) is None:
        fail("REPLAY_REPORT_SHA256_INVALID", str(replay_report_sha256))

    sequence = PIT._build_sequence("US", bundle["records"])
    if sequence is None:
        fail("US_PIT_SEQUENCE_NOT_BUILDABLE")
    rederived = PIT.payload_sha256(COMMON.replay_common_v1(copy.deepcopy(sequence)))
    if rederived != replay_report_sha256:
        fail("REPLAY_REPORT_REDERIVATION_MISMATCH", f"{rederived}!={replay_report_sha256}")
    steps = sequence["steps"]
    if not steps:
        fail("US_PIT_SEQUENCE_EMPTY")
    history_first = steps[0]["as_of_date"]
    history_last = steps[-1]["as_of_date"]

    session_dates = _calendar_session_dates(calendar)
    if history_last not in session_dates:
        fail("HISTORY_LAST_SESSION_NOT_IN_OFFICIAL_CALENDAR", history_last)
    if not any(date > history_last for date in session_dates):
        # ``session_plan`` requires a context session strictly after
        # ``history_last``; without one the runtime is UNKNOWN every day.
        fail("OFFICIAL_CALENDAR_HAS_NO_SESSION_AFTER_HISTORY", history_last)

    record = {
        "schema_version": RECORD_SCHEMA,
        "market": "US",
        "scope": "US_INTERNAL_VIRTUAL_PAPER_ONLY",
        "generator": GENERATOR_PATH,
        "bundle_sha256": bundle_sha,
        "evaluation": copy.deepcopy(evaluation),
        "derivation": {
            "bundle_path": bundle_relative_path,
            "bundle_validated_by": "regime.us_historical_replay_population.validate_population",
            "acceptance_evaluated_by": "regime.market_scoped_pit_acceptance.evaluate_market_pit_acceptance",
            "replay_consumed_via": "regime.decision_authority.replay_common_v1",
            "replay_report_sha256": replay_report_sha256,
            "history_first_session_date": history_first,
            "history_last_session_date": history_last,
            "history_step_count": len(steps),
        },
        "session_calendar": {
            "path": calendar_relative_path,
            "sha256": calendar_file_sha256,
            "schema_version": calendar.get("schema_version"),
            "coverage_start": calendar.get("coverage_start"),
            "coverage_end": calendar.get("coverage_end"),
            "derived_payload_sha256": calendar.get("derived_payload_sha256"),
        },
        "runtime_contract": {
            "path": RUNTIME.CONTRACT_RELATIVE,
            "sha256": RUNTIME.CONTRACT_SHA256,
            "pit_acceptance_contract_path": RUNTIME.PIT_CONTRACT_RELATIVE,
            "pit_acceptance_contract_sha256": RUNTIME.PIT_CONTRACT_SHA256,
        },
        "authority": dict(AUTHORITY_CLOSED),
    }
    return record


def _calendar_session_dates(calendar: object) -> list[str]:
    if not isinstance(calendar, dict):
        fail("CALENDAR_NOT_AN_OBJECT")
    rows = calendar.get("sessions")
    if not isinstance(rows, list) or not rows:
        fail("CALENDAR_SESSIONS_EMPTY")
    dates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            fail("CALENDAR_ROW_INVALID")
        dates.append(row["date"])
    return dates


def record_bytes(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# The U5 adoption block (values only — this module never writes the adoption)
# ---------------------------------------------------------------------------


def u5_adoption_values(record: dict, *, record_relative_path: str, record_file_sha256: str) -> dict:
    """Exactly the two adoption sub-objects U5 must paste, plus the hashes.

    This module deliberately does NOT create or activate
    ``config/us_paper_runtime_adoption_v1.json``: activating it is the user
    ratification U5, not a producer's output.
    """
    derivation = record["derivation"]
    return {
        "note": "U5 INPUT ONLY — config/us_paper_runtime_adoption_v1.json is a user ratification and is not created here.",
        "pit_acceptance": {
            "bundle_path": derivation["bundle_path"],
            "bundle_sha256": record["bundle_sha256"],
            "acceptance_record_path": record_relative_path,
            "acceptance_record_sha256": record_file_sha256,
            "replay_report_sha256": derivation["replay_report_sha256"],
            "history_last_session_date": derivation["history_last_session_date"],
        },
        "session_calendar": {
            "path": record["session_calendar"]["path"],
            "sha256": record["session_calendar"]["sha256"],
        },
        "bindings": {
            "contract_sha256": RUNTIME.CONTRACT_SHA256,
            "implementation_sha256": RUNTIME.implementation_sha256(),
            "common_v1_binding_payload_sha256": RUNTIME.payload_sha256(
                COMMON.load_common_v1_policy()["binding"]
            ),
        },
    }


def calendar_only_u5_values(calendar_relative_path: str, calendar_file_sha256: str) -> dict:
    """The half of U5 that U3's calendar already settles, with no bundle yet."""
    return {
        "note": "U5 INPUT ONLY — the session-calendar half. pit_acceptance stays unfillable until a PIT_ACCEPTED bundle is committed.",
        "session_calendar": {"path": calendar_relative_path, "sha256": calendar_file_sha256},
        "bindings": {
            "contract_sha256": RUNTIME.CONTRACT_SHA256,
            "implementation_sha256": RUNTIME.implementation_sha256(),
            "common_v1_binding_payload_sha256": RUNTIME.payload_sha256(
                COMMON.load_common_v1_policy()["binding"]
            ),
        },
        "pit_acceptance": None,
    }


# ---------------------------------------------------------------------------
# RECEIPT mode: the committed pair, derived from an out-of-checkout bundle
# ---------------------------------------------------------------------------


def build_receipt_pair(bundle_raw: bytes, *, bundle_source_path: str,
                       regeneration_command: str, population=None) -> tuple[dict, dict]:
    """Derive the (replay report, receipt) pair the RECEIPT binding needs.

    The bundle is read but NEVER copied into the checkout: what gets committed is
    the replay output plus this receipt, and the bundle itself is hash-bound only.
    That is KR's arrangement, and it is the only one compatible with
    ``us_historical_replay_population._forbid_tracked_output``.

    Pure function of the bundle bytes: nothing time-varying, so re-running over
    the same bundle yields the same two files and the same two sha256 values an
    adoption pins.
    """
    if not isinstance(bundle_raw, bytes) or not bundle_raw:
        fail("BUNDLE_BYTES_REQUIRED")
    bundle_file_sha = sha256_bytes(bundle_raw)
    try:
        bundle = json.loads(bundle_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("BUNDLE_JSON_INVALID")
    if not isinstance(bundle, dict):
        fail("BUNDLE_JSON_INVALID")

    # The full validator runs HERE, where the bundle exists, and its verdict is
    # what the receipt attests to.  In-checkout verification cannot repeat it.
    module = population_module() if population is None else population
    try:
        module.validate_population(copy.deepcopy(bundle))
    except Exception as exc:
        fail("BUNDLE_REVALIDATION_FAILED", f"{type(exc).__name__}:{exc}")

    evaluation = PIT.evaluate_market_pit_acceptance("US", bundle)
    if evaluation.get("status") != PIT.STATUS_PIT_ACCEPTED:
        fail("US_PIT_NOT_ACCEPTED",
             f"{evaluation.get('status')}:{','.join(evaluation.get('reasons') or [])}")

    sequence = PIT._build_sequence("US", bundle["records"])
    if sequence is None:
        fail("US_PIT_SEQUENCE_NOT_BUILDABLE")
    replay = COMMON.replay_common_v1(copy.deepcopy(sequence))
    if PIT.payload_sha256(replay) != evaluation["replay_report_sha256"]:
        fail("REPLAY_REPORT_REDERIVATION_MISMATCH")

    # The runtime will re-derive the evaluation from the replay alone; prove here
    # that the two derivations already agree, so a receipt can never be committed
    # that the runtime would then reject.
    rederived = RUNTIME._evaluation_from_replay(replay)
    if RUNTIME.canonical_bytes(rederived) != RUNTIME.canonical_bytes(evaluation):
        fail("RECEIPT_EVALUATION_DIVERGES_FROM_BUNDLE_EVALUATION")
    rebuilt = RUNTIME._sequence_from_replay(replay)
    if PIT.payload_sha256(rebuilt) != PIT.payload_sha256(sequence):
        fail("RECEIPT_SEQUENCE_NOT_REBUILDABLE_FROM_REPLAY")

    steps = sequence["steps"]
    history_first, history_last = steps[0]["as_of_date"], steps[-1]["as_of_date"]
    replay_relative = replay_relative_path(history_last)
    replay_file_sha = sha256_bytes(record_bytes(replay))

    regimes = {}
    for row in replay["steps"]:
        regimes[row["confirmed_regime"]] = regimes.get(row["confirmed_regime"], 0) + 1

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "market": "US",
        "scope": "US_INTERNAL_VIRTUAL_PAPER_ONLY",
        "binding_mode": RUNTIME.PIT_BINDING_MODE_RECEIPT,
        "generator": GENERATOR_PATH,
        "replay_report_path": replay_relative,
        "replay_report_file_sha256": replay_file_sha,
        "replay_report_payload_sha256": evaluation["replay_report_sha256"],
        "source_sequence_sha256": replay["source_sequence_sha256"],
        "population": {
            "committed": False,
            "why_not_committed": (
                "us_historical_replay_population._forbid_tracked_output refuses, by design, to "
                "write historical replay evidence to any path inside the checkout, and user "
                "ratification USER_RATIFICATION_US_REPLAY_FLAG_20260920 forbids both relaxing "
                "that guard and committing the bundle. The bundle is reproducible from committed "
                "inputs and is hash-bound here instead -- the same arrangement KR uses "
                "(evidence/authority/kr_paper_runtime_adoption_v1.json, raw_provider_rows_committed false)."
            ),
            "file_sha256": bundle_file_sha,
            "payload_sha256": bundle["payload_sha256"],
            "byte_count": len(bundle_raw),
            "record_count": len(bundle["records"]),
            "source_mode": bundle.get("pit_source", {}).get("source_mode"),
            "validate_population": "PASS",
            "validated_by": "regime/us_historical_replay_population.py::validate_population",
            "raw_provider_rows_committed": False,
            "raw_provider_rows_scope": "OUT_OF_CHECKOUT_REPRODUCIBLE_HASH_BOUND_IN_THIS_RECEIPT",
            "path_scope": "OUTSIDE_THIS_CHECKOUT",
            "observed_at_generation": bundle_source_path,
            "regeneration_command": regeneration_command,
            "compaction_rule": (
                "Committed here is the replay_common_v1 OUTPUT, not the population. Each replay "
                "step carries its input axis_directions, so the input sequence is rebuildable and "
                "the replay is re-runnable in-checkout without the bundle. ~86% of each population "
                "record is five_axis raw measurement, which the acceptance evaluation never reads."
            ),
        },
        "range": {
            "history_first_session_date": history_first,
            "history_last_session_date": history_last,
            "session_count": len(steps),
        },
        "coverage": {
            "confirmed_regime_counts": dict(sorted(regimes.items())),
            "five_of_five_step_count": len(steps),
            "population_record_count": len(bundle["records"]),
        },
        "evaluation": evaluation,
        "gate": {
            "status": "GATE_ITSELF_UNVERIFIED",
            "what_this_is": "PIT acceptance gate passage. NOT an alpha claim.",
            "sealed_verification_verdict_20260920": "PARTIAL",
            "regime_gate_value_confirmed": False,
            "window_phases_rejected": "4 of 5",
            "max_drawdown_change": "-7.57% -> -9.03% (worse)",
            "note": (
                "evaluated_date_count and regimes_observed are statements about COVERAGE of the "
                "replayed history, not about the gate having value. The 2026-09-20 sealed "
                "verification returned PARTIAL and the regime gate's value is not confirmed. "
                "This receipt does not change that and must not be read as evidence of alpha."
            ),
        },
        "authority": dict(AUTHORITY_CLOSED),
    }
    return replay, receipt


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_inputs(args, root: Path):
    calendar, calendar_raw = _read_json_object(root / args.calendar, "CALENDAR")
    bundle_path = root / args.bundle
    if not bundle_path.is_file():
        fail("BUNDLE_ABSENT", str(args.bundle))
    bundle_raw = bundle_path.read_bytes()
    return calendar, sha256_bytes(calendar_raw), bundle_raw


def _build(args) -> int:
    root = ROOT
    calendar, calendar_sha, bundle_raw = _load_inputs(args, root)
    record = build_acceptance_record(
        bundle_raw,
        calendar=calendar,
        bundle_relative_path=args.bundle,
        calendar_relative_path=args.calendar,
        calendar_file_sha256=calendar_sha,
    )
    raw = record_bytes(record)
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    print(json.dumps(
        u5_adoption_values(record, record_relative_path=args.out, record_file_sha256=sha256_bytes(raw)),
        indent=2, sort_keys=True,
    ))
    return 0


def _verify(args) -> int:
    root = ROOT
    calendar, calendar_sha, bundle_raw = _load_inputs(args, root)
    expected = record_bytes(build_acceptance_record(
        bundle_raw,
        calendar=calendar,
        bundle_relative_path=args.bundle,
        calendar_relative_path=args.calendar,
        calendar_file_sha256=calendar_sha,
    ))
    actual = (root / args.record).read_bytes() if (root / args.record).is_file() else b""
    if actual != expected:
        fail("RECORD_NOT_BYTE_IDENTICAL_TO_REDERIVATION", args.record)
    print(json.dumps({"record": args.record, "status": "BYTE_IDENTICAL",
                      "record_sha256": sha256_bytes(actual)}, indent=2, sort_keys=True))
    return 0


def _u5_values(args) -> int:
    root = ROOT
    _, calendar_raw = _read_json_object(root / args.calendar, "CALENDAR")
    calendar_sha = sha256_bytes(calendar_raw)
    record_path = root / args.record
    if not record_path.is_file():
        print(json.dumps(calendar_only_u5_values(args.calendar, calendar_sha), indent=2, sort_keys=True))
        return 0
    record, raw = _read_json_object(record_path, "RECORD")
    print(json.dumps(
        u5_adoption_values(record, record_relative_path=args.record, record_file_sha256=sha256_bytes(raw)),
        indent=2, sort_keys=True,
    ))
    return 0


def _receipt(args) -> int:
    """Write the committed RECEIPT-mode pair from an out-of-checkout bundle."""
    root = ROOT
    bundle_path = Path(args.bundle)
    if not bundle_path.is_absolute():
        bundle_path = root / bundle_path
    if not bundle_path.is_file():
        fail("BUNDLE_ABSENT", str(args.bundle))
    # Refuse to read a bundle that someone has already copied into the checkout:
    # the whole point is that it lives outside, and accepting an in-tree copy
    # would quietly re-create exactly what the output guard forbids.
    try:
        bundle_path.resolve().relative_to(root.resolve())
        fail("BUNDLE_MUST_BE_OUTSIDE_CHECKOUT", str(bundle_path))
    except ValueError:
        pass
    replay, receipt = build_receipt_pair(
        bundle_path.read_bytes(),
        bundle_source_path=str(bundle_path),
        regeneration_command=args.regeneration_command,
    )
    replay_relative = receipt["replay_report_path"]
    replay_out = root / replay_relative
    replay_out.parent.mkdir(parents=True, exist_ok=True)
    replay_raw = record_bytes(replay)
    replay_out.write_bytes(replay_raw)
    receipt_out = root / args.out
    receipt_out.parent.mkdir(parents=True, exist_ok=True)
    receipt_raw = record_bytes(receipt)
    receipt_out.write_bytes(receipt_raw)
    print(json.dumps({
        "pit_acceptance": {
            "receipt_path": args.out,
            "receipt_sha256": sha256_bytes(receipt_raw),
            "replay_path": replay_relative,
            "replay_sha256": sha256_bytes(replay_raw),
            "bundle_sha256": receipt["population"]["file_sha256"],
            "bundle_committed": False,
            "replay_report_sha256": receipt["replay_report_payload_sha256"],
            "history_last_session_date": receipt["range"]["history_last_session_date"],
        },
        "session_calendar": {
            "path": args.calendar,
            "sha256": sha256_bytes((root / args.calendar).read_bytes()),
        },
        "bindings": {
            "contract_sha256": RUNTIME.CONTRACT_SHA256,
            "implementation_sha256": RUNTIME.implementation_sha256(),
            "common_v1_binding_payload_sha256": RUNTIME.payload_sha256(
                COMMON.load_common_v1_policy()["binding"]
            ),
        },
    }, indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="derive the acceptance record from a committed bundle")
    build.add_argument("--bundle", default=DEFAULT_BUNDLE_PATH)
    build.add_argument("--calendar", default=DEFAULT_CALENDAR_PATH)
    build.add_argument("--out", default=DEFAULT_RECORD_PATH)
    build.set_defaults(func=_build)

    verify = sub.add_parser("verify", help="prove a committed record re-derives byte for byte")
    verify.add_argument("--bundle", default=DEFAULT_BUNDLE_PATH)
    verify.add_argument("--calendar", default=DEFAULT_CALENDAR_PATH)
    verify.add_argument("--record", default=DEFAULT_RECORD_PATH)
    verify.set_defaults(func=_verify)

    receipt = sub.add_parser(
        "receipt", help="write the committed RECEIPT-mode pair from an out-of-checkout bundle")
    receipt.add_argument("--bundle", required=True,
                         help="path to the population bundle; MUST be outside this checkout")
    receipt.add_argument("--calendar", default=DEFAULT_CALENDAR_PATH)
    receipt.add_argument("--out", default=DEFAULT_RECEIPT_PATH)
    receipt.add_argument("--regeneration-command", required=True,
                         dest="regeneration_command",
                         help="the exact command that reproduces the bundle, recorded in the receipt")
    receipt.set_defaults(func=_receipt)

    values = sub.add_parser("u5-values", help="print the exact adoption values U5 must ratify")
    values.add_argument("--calendar", default=DEFAULT_CALENDAR_PATH)
    values.add_argument("--record", default=DEFAULT_RECORD_PATH)
    values.set_defaults(func=_u5_values)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (UsPitAcceptanceRecordError, PIT.MarketScopedPitAcceptanceError,
            COMMON.DecisionAuthorityError) as exc:
        print(f"STOP {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
