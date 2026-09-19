#!/usr/bin/env python3
"""Rule-lineage sidecars for existing producers (additive, decision-neutral).

Wired producers (v1), each through a separate step/workflow that runs after
the producer has written and verified its packet:

* ``decision/crypto_paper_decision_snapshot.py`` -- per candidate market:
  the per-market realtime freshness gate, the realtime liquidity floor gate
  (the only liquidity gate that emits output in this repository today) and the
  resulting candidate state
  (``python3 governance/rule_lineage_producers.py crypto-decision --packet P``);
* ``regime/paper_regime_reference.py`` -- per market PAPER risk reference
  (the market state that allocation v2 multipliers / hedge would consume)
  (``python3 governance/rule_lineage_producers.py paper-reference-scan
  --min-date D``, run by ``.github/workflows/rule-lineage-paper-reference.yml``
  after each "PAPER Market Risk Reference" run, because that workflow's own
  bytes are pinned in ``config/regime_source_owner_registry_v2.json``).

Why sidecars and a separate step, not an in-packet field or a producer edit:

* the crypto decision packet validator uses a closed top-level field set and
  every issued packet revalidates byte-for-byte (the live runtime pinned at
  48c3faa9 rebuilds packets through the same module); the PAPER reference
  validator rebuilds and compares the whole packet;
* both producer *source files* are hash-pinned elsewhere
  (``test/test_crypto_axis_trade_bridge_explanation.py`` PRODUCER_PINS and the
  KR runtime qualification record
  ``evidence/authority/kr_information_system_runtime_qualification_candidate_20260913.json``
  ``implementation_sha256``), so even an additive hook inside them would
  invalidate those bindings.

A sidecar keyed by the packet's ``payload_sha256`` changes no packet byte, no
producer byte and no decision.

Honesty rule: a ``rule_refs`` entry is emitted only where the producer code
actually executes that registered rule.  Registered rules that govern the same
subject but are not executed by the producer are listed in
``unapplied_rules`` with a reason code, so the scorecard shows NOT_EVALUATED
instead of a silent gap.

The CLI never exits non-zero for a lineage problem: it prints
``RULE_LINEAGE_EMIT_FAILED`` to stderr and a ``FAILED`` status, so lineage can
never stop the decision workflow or its evidence commit.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance import rule_refs as REFS  # noqa: E402


FRESHNESS_RULE = "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1"
CRYPTO_PRODUCER = "decision/crypto_paper_decision_snapshot.py"
REFERENCE_PRODUCER = "regime/paper_regime_reference.py"
PER_MARKET_SCHEMA_VERSIONS = (
    "crypto_paper_decision_snapshot_packet/2",
    "crypto_paper_decision_snapshot_packet/3",
    "crypto_paper_decision_snapshot_packet/4",
)
# /4 (crypto PAPER wiring v2) wires the runtime decision and the rotation
# confirmation into P5-08 contract/3 and P5-09 contract/3: their per-candidate
# ``rule_refs`` become lineage events and the "not wired" gaps no longer apply.
V4_SCHEMA_VERSION = "crypto_paper_decision_snapshot_packet/4"
# universe/crypto_candidate_promotion.py::STATE_BLOCKED -- the one contract/3
# promotion state whose rule_refs carry a BLOCKED_BY role.  Reused verbatim,
# not reinvented; this module reads the packet and never re-derives the state.
PROMOTION_STATE_BLOCKED = "BLOCKED"
CRYPTO_CANDIDATE_UNAPPLIED_V4 = (
    {"rule_id": "RULE.ROTATION.CRYPTO.V1", "reason_code": "ROTATION_BUCKET_STATE_PRODUCED_BY_CONFIRMATION_PACKET"},
)
LEGACY_SCHEMA_VERSION = "crypto_paper_decision_snapshot_packet/1"
REFERENCE_SCHEMA_VERSION = "paper_regime_reference/v2"

# Prefixes written by realtime/crypto_realtime_per_market_policy.py
# ``cap_state_for_market`` (read, never re-derived here).
REALTIME_CAP_PREFIX = "MARKET_REALTIME_FRESHNESS_NOT_FRESH:"
FLOOR_CAP_PREFIX = "REALTIME_LIQUIDITY_FLOOR_EXCLUDED:"
FRESH = "FRESH"
INCLUDED = "INCLUDED"

CRYPTO_CANDIDATE_UNAPPLIED = (
    {"rule_id": "RULE.ALLOCATION.V2", "reason_code": "ALLOCATION_NOT_APPLIED_BY_ANY_PUBLIC_PRODUCER"},
    {"rule_id": "RULE.CRYPTO.RUNTIME.V1", "reason_code": "SNAPSHOT_REGIME_NOT_WIRED_TO_RUNTIME_V1"},
    {"rule_id": "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1", "reason_code": "ROTATION_NOT_WIRED_TO_CRYPTO_CANDIDATE_FUNNEL"},
    {"rule_id": "RULE.ROTATION.CRYPTO.V1", "reason_code": "ROTATION_NOT_WIRED_TO_CRYPTO_CANDIDATE_FUNNEL"},
)
REFERENCE_UNAPPLIED = (
    {"rule_id": "RULE.ALLOCATION.V2", "reason_code": "ALLOCATION_MULTIPLIER_NOT_COMPUTED_BY_ANY_PUBLIC_PRODUCER"},
    {"rule_id": "RULE.HEDGE.INVERSE.V1", "reason_code": "HEDGE_NOT_COMPUTED_BY_ANY_PUBLIC_PRODUCER"},
)
REFERENCE_CRYPTO_UNAPPLIED = (
    {"rule_id": "RULE.CRYPTO.RUNTIME.V1", "reason_code": "REFERENCE_USES_DESCRIPTIVE_NORMALIZATION_NOT_RUNTIME_V1"},
)


def _source_packet(path: str, packet: dict) -> dict:
    return {
        "path": path,
        "schema_version": packet["schema_version"],
        "payload_sha256": packet["payload_sha256"],
    }


def crypto_decision_packet_path(packet: dict) -> str:
    return (
        f"evidence/crypto_paper_decision/{packet['capture_date']}/{packet['capture_hhmm']}/"
        f"{packet['generation_id']}/packet.json"
    )


def crypto_decision_sidecar_path(lineage_root: Path, packet: dict, registry_sha256: str) -> Path:
    # Keyed by packet *and* registry identity: a registry change adds a new
    # sidecar next to the old one instead of an append-only conflict.
    return (
        Path(lineage_root) / "crypto_paper_decision" / packet["capture_date"]
        / packet["capture_hhmm"] / packet["payload_sha256"] / f"registry-{registry_sha256}.json"
    )


def build_crypto_decision_sidecar(packet: dict, context: REFS.RegistryContext) -> dict:
    """Derive lineage from one already-validated crypto decision packet."""
    source = _source_packet(crypto_decision_packet_path(packet), packet)
    schema = packet["schema_version"]
    per_market = schema in PER_MARKET_SCHEMA_VERSIONS
    if not per_market and schema != LEGACY_SCHEMA_VERSION:
        REFS._fail("CRYPTO_DECISION_SCHEMA_UNSUPPORTED", str(schema))
    timestamp = packet["generated_at"]
    events = []

    def event(market, gate, event_type, outcome, pairs, unapplied, inputs):
        return REFS.build_lineage_event(
            context,
            decision_id=f"crypto_paper_decision:{packet['generation_id']}:{market}",
            producer=CRYPTO_PRODUCER, market="CRYPTO", instrument=market,
            timestamp_utc=timestamp, event_type=event_type, gate=gate, outcome=outcome,
            rule_refs=REFS.canonical_rule_refs(pairs, context),
            unapplied_rules=[copy.deepcopy(item) for item in unapplied], inputs=inputs,
            source_packet=source,
        )

    for row in packet.get("candidates") or []:
        market = row["market"]
        state_outcome = {
            "state": row["state"],
            "reason": row["reason"],
            "freshness_capped": row["freshness_capped"],
            "freshness_cap_reason": row["freshness_cap_reason"],
            "market_action_cap_reason": row.get("market_action_cap_reason"),
        }
        if not per_market:
            events.append(event(
                market, "candidate_state", "BLOCK" if row["freshness_capped"] else "DECISION",
                state_outcome, [],
                [{"rule_id": FRESHNESS_RULE, "reason_code": "PACKET_PREDATES_RULE_EFFECTIVE_FROM"}]
                + list(CRYPTO_CANDIDATE_UNAPPLIED),
                row,
            ))
            continue

        cap = row["market_action_cap_reason"] or ""
        cap_reason = row["freshness_cap_reason"] or ""

        realtime = row["realtime_freshness"]
        realtime_blocked = realtime["status"] != FRESH
        events.append(event(
            market, "realtime_freshness_per_market", "BLOCK" if realtime_blocked else "DECISION",
            {
                "realtime_status": realtime["status"],
                "reasons": copy.deepcopy(realtime["reasons"]),
                "is_recorded_action_cap": cap.startswith(REALTIME_CAP_PREFIX),
                "capped_actionable_state": bool(row["freshness_capped"]) and cap_reason.startswith(REALTIME_CAP_PREFIX),
            },
            [(FRESHNESS_RULE, "BLOCKED_BY" if realtime_blocked else "APPLIED")], [], realtime,
        ))

        floor = row["realtime_liquidity_floor"]
        floor_blocked = floor["status"] != INCLUDED
        events.append(event(
            market, "realtime_liquidity_floor", "BLOCK" if floor_blocked else "DECISION",
            {
                "floor_status": floor["status"],
                "reason": floor["reason"],
                "krw_30d_avg_turnover": floor["krw_30d_avg_turnover"],
                "is_recorded_action_cap": cap.startswith(FLOOR_CAP_PREFIX),
                "capped_actionable_state": bool(row["freshness_capped"]) and cap_reason.startswith(FLOOR_CAP_PREFIX),
            },
            [(FRESHNESS_RULE, "BLOCKED_BY" if floor_blocked else "APPLIED")], [], floor,
        ))

        capped_by_rule = bool(row["freshness_capped"]) and (
            cap_reason.startswith(REALTIME_CAP_PREFIX) or cap_reason.startswith(FLOOR_CAP_PREFIX)
        )
        v4 = schema == V4_SCHEMA_VERSION
        if v4:
            p5_08 = row["p5_08"]
            # A BLOCK must name the rule that blocked (rule_refs.py
            # BLOCK_EVENT_WITHOUT_BLOCKING_RULE), so this event type has to
            # agree with what the packet itself recorded.  contract/3
            # ``aggregate_t2_state`` (universe/crypto_candidate_promotion.py)
            # has three outcomes: a FAILED required condition gives BLOCKED and
            # the packet carries the T2 rule as BLOCKED_BY; an UNKNOWN one gives
            # WATCH with every ref APPLIED -- no rule blocked the promotion, the
            # inputs needed to decide it were simply not available; all-passed
            # gives FOCUSED_REVIEW.  Treating "anything but FOCUSED_REVIEW" as a
            # BLOCK therefore claimed a blocking rule the packet never named and
            # failed closed on the first natural /4 packet (2026-09-18T07:15:38Z,
            # every market WATCH on T2_REQUIRED_UNKNOWN).  BLOCK is emitted for
            # exactly the state whose refs carry BLOCKED_BY; WATCH stays a
            # DECISION that records the undetermined gate in its outcome.
            events.append(event(
                market, "promotion_t2_required",
                "BLOCK" if p5_08["promotion_state"] == PROMOTION_STATE_BLOCKED else "DECISION",
                {"promotion_state": p5_08["promotion_state"], "promotion_reason": p5_08["promotion_reason"]},
                [(ref["rule_id"], ref["role"]) for ref in p5_08["rule_refs"]],
                p5_08["unapplied_rules"], p5_08["t2_required_conditions"],
            ))
            p5_09 = row.get("p5_09")
            if p5_09 is not None:
                events.append(event(
                    market, "buy_eligibility", "DECISION" if p5_09["eligibility_state"] == "PAPER_BUY_ELIGIBLE" else "BLOCK",
                    {"eligibility_state": p5_09["eligibility_state"], "eligibility_reason": p5_09["eligibility_reason"]},
                    [(ref["rule_id"], ref["role"]) for ref in p5_09["rule_refs"]], [], p5_09["criteria"],
                ))
        events.append(event(
            market, "candidate_state", "BLOCK" if row["freshness_capped"] else "DECISION",
            state_outcome,
            [(FRESHNESS_RULE, "BLOCKED_BY" if capped_by_rule else "APPLIED")],
            CRYPTO_CANDIDATE_UNAPPLIED_V4 if v4 else CRYPTO_CANDIDATE_UNAPPLIED, row,
        ))
    return REFS.build_sidecar(context, producer=CRYPTO_PRODUCER, source_packet=source, events=events)


def reference_evidence_date(packet: dict) -> str:
    return max(row["as_of_date"] for row in packet["markets"] if row["as_of_date"])


def reference_packet_path(packet: dict) -> str:
    return (
        f"evidence/regime/paper_reference/{reference_evidence_date(packet)}/"
        f"{packet['generation_id']}/packet.json"
    )


def reference_sidecar_path(lineage_root: Path, packet: dict, registry_sha256: str) -> Path:
    return (
        Path(lineage_root) / "paper_regime_reference" / reference_evidence_date(packet)
        / packet["payload_sha256"] / f"registry-{registry_sha256}.json"
    )


def build_reference_sidecar(packet: dict, context: REFS.RegistryContext) -> dict:
    if packet.get("schema_version") != REFERENCE_SCHEMA_VERSION:
        REFS._fail("REFERENCE_SCHEMA_UNSUPPORTED", str(packet.get("schema_version")))
    source = _source_packet(reference_packet_path(packet), packet)
    events = []
    for row in packet["markets"]:
        market = row["market"]
        reference = row.get("paper_reference") or {}
        unapplied = list(REFERENCE_UNAPPLIED) + (list(REFERENCE_CRYPTO_UNAPPLIED) if market == "CRYPTO" else [])
        events.append(REFS.build_lineage_event(
            context,
            decision_id=f"paper_regime_reference:{packet['generation_id']}:{market}",
            producer=REFERENCE_PRODUCER, market=market, instrument=None,
            timestamp_utc=packet["generated_at"], event_type="DECISION",
            gate="paper_market_risk_reference",
            outcome={
                "as_of_date": row.get("as_of_date"),
                "candidate_regime": reference.get("candidate_regime"),
                "score": reference.get("score"),
                "runtime_regime": row.get("runtime_regime"),
                "classification_status": row.get("classification_status"),
            },
            rule_refs=[], unapplied_rules=unapplied, inputs=row, source_packet=source,
        ))
    return REFS.build_sidecar(context, producer=REFERENCE_PRODUCER, source_packet=source, events=events)


def verify_embedded_payload_sha256(packet: dict) -> None:
    if not isinstance(packet, dict) or not isinstance(packet.get("payload_sha256"), str):
        REFS._fail("PACKET_PAYLOAD_SHA_MISSING")
    unsigned = {k: v for k, v in packet.items() if k != "payload_sha256"}
    if REFS.payload_sha256(unsigned) != packet["payload_sha256"]:
        REFS._fail("PACKET_PAYLOAD_SHA_MISMATCH")


def _emit(build, path_for, packet: dict, lineage_root: Path, context) -> dict:
    """Never raises: lineage must not change or stop a producer's decision."""
    try:
        verify_embedded_payload_sha256(packet)
        context = context or REFS.RegistryContext.load()
        sidecar = build(packet, context)
        path = path_for(lineage_root, packet, context.sha256)
        outcome = REFS.write_sidecar_append_only(path, sidecar)
        return {"status": outcome, "path": str(path), "payload_sha256": sidecar["payload_sha256"],
                "event_count": len(sidecar["events"])}
    except Exception as exc:  # noqa: BLE001 -- reported loudly, never swallowed silently
        message = f"RULE_LINEAGE_EMIT_FAILED:{type(exc).__name__}:{exc}"
        print(message, file=sys.stderr)
        return {"status": "FAILED", "path": None, "error": message}


def emit_crypto_decision_lineage(packet: dict, lineage_root: Path, context=None) -> dict:
    return _emit(build_crypto_decision_sidecar, crypto_decision_sidecar_path, packet, lineage_root, context)


def emit_reference_lineage(packet: dict, lineage_root: Path, context=None) -> dict:
    return _emit(build_reference_sidecar, reference_sidecar_path, packet, lineage_root, context)


def run_cli(kind: str, packet_path: Path, *, root: Path = ROOT, lineage_root: Path | None = None,
            context=None) -> dict:
    """Read one committed producer packet from ``root`` and emit its sidecar.

    The packet must sit at its canonical evidence path (crypto) or be the
    ``latest`` pointer whose bytes equal its canonical evidence copy
    (reference), so the sidecar's ``source_packet.path`` is always true.
    """
    lineage_root = Path(lineage_root) if lineage_root is not None else Path(root) / "evidence" / "rule_lineage"
    try:
        packet_path = Path(packet_path)
        if not packet_path.is_absolute():
            packet_path = Path(root) / packet_path
        raw = packet_path.read_bytes()
        packet = json.loads(raw.decode("utf-8"))
        if kind == "crypto-decision":
            canonical = Path(root) / crypto_decision_packet_path(packet)
            if packet_path.resolve() != canonical.resolve():
                REFS._fail("CRYPTO_PACKET_NOT_AT_CANONICAL_PATH", str(packet_path))
            return emit_crypto_decision_lineage(packet, lineage_root, context)
        if kind == "paper-reference":
            # Either the canonical evidence packet itself or the latest pointer
            # whose bytes equal that evidence copy.
            canonical = Path(root) / reference_packet_path(packet)
            if not canonical.is_file() or canonical.read_bytes() != raw:
                REFS._fail("REFERENCE_PACKET_EVIDENCE_COPY_MISMATCH", str(canonical))
            return emit_reference_lineage(packet, lineage_root, context)
        REFS._fail("KIND_INVALID", str(kind))
    except Exception as exc:  # noqa: BLE001
        message = f"RULE_LINEAGE_EMIT_FAILED:{type(exc).__name__}:{exc}"
        print(message, file=sys.stderr)
        return {"status": "FAILED", "path": None, "error": message}


CRYPTO_EVIDENCE_RELATIVE = "evidence/crypto_paper_decision"
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def scan_crypto_decision_evidence(min_date: str, *, root: Path = ROOT, lineage_root: Path | None = None,
                                  context=None) -> list:
    """Emit sidecars for every retained crypto PAPER decision packet dated >= ``min_date``.

    Idempotent (existing sidecars are verified, never rewritten -- a repeat
    scan over an already-covered range costs one read and one comparison per
    packet).  Exists because the per-run sidecar step wired into
    ``.github/workflows/upbit-realtime-capture.yml`` is guarded on the
    capture job not having been cancelled: whenever that job overruns its
    timeout (observed since 2026-09-18), every step guarded that way is
    skipped even though the decision packet itself still commits (its own
    step runs unconditionally). This scan is a second, independent path to
    the same sidecars that depends on nothing about how the capture job
    ended -- it only reads packets already committed to evidence -- so a
    stretch of cancelled/overrun capture runs no longer means a stretch of
    missing lineage. ``evidence/crypto_paper_decision/_sources`` (the packet
    dedup store, not a date) is excluded by requiring the directory name to
    match ``YYYY-MM-DD`` before the ``>= min_date`` comparison; a plain
    string compare would otherwise place ``_sources`` after every date.
    """
    results = []
    base = Path(root) / CRYPTO_EVIDENCE_RELATIVE
    if not base.is_dir():
        return results
    context = context or REFS.RegistryContext.load()
    for date_dir in sorted(p for p in base.iterdir()
                            if p.is_dir() and DATE_DIR_RE.match(p.name) and p.name >= min_date):
        for packet_path in sorted(date_dir.glob("*/*/packet.json")):
            result = run_cli("crypto-decision", packet_path, root=root, lineage_root=lineage_root, context=context)
            result["packet"] = str(packet_path)
            results.append(result)
    return results


REFERENCE_EVIDENCE_RELATIVE = "evidence/regime/paper_reference"


def scan_reference_evidence(min_date: str, *, root: Path = ROOT, lineage_root: Path | None = None,
                            context=None) -> list:
    """Emit sidecars for every retained reference packet dated >= ``min_date``.

    Idempotent (existing sidecars are verified, never rewritten); used by the
    separate lineage workflow so the hash-pinned reference workflow stays
    untouched.  Legacy ``paper_regime_reference/v1`` packets are skipped.
    """
    results = []
    base = Path(root) / REFERENCE_EVIDENCE_RELATIVE
    if not base.is_dir():
        return results
    context = context or REFS.RegistryContext.load()
    for date_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name >= min_date):
        for packet_path in sorted(date_dir.glob("*/packet.json")):
            try:
                schema = json.loads(packet_path.read_text(encoding="utf-8")).get("schema_version")
            except (OSError, ValueError):
                schema = None
            if schema is not None and schema != REFERENCE_SCHEMA_VERSION:
                results.append({"status": "SKIPPED_SCHEMA", "packet": str(packet_path)})
                continue
            result = run_cli("paper-reference", packet_path, root=root, lineage_root=lineage_root, context=context)
            result["packet"] = str(packet_path)
            results.append(result)
    return results


def _warn(message: str) -> None:
    """Visible GitHub Actions warning annotation on stderr.

    Stdout stays one parseable JSON document (the lineage workflow pipes it
    into ``json.load``); Actions reads workflow commands from stderr too.
    """
    text = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning title=Rule lineage sidecar failed::{text}", file=sys.stderr)


SCAN_KINDS = {
    "crypto-decision-scan": scan_crypto_decision_evidence,
    "paper-reference-scan": scan_reference_evidence,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Emit additive rule-lineage sidecars for producer packets.")
    parser.add_argument("kind", choices=["crypto-decision", "paper-reference",
                                          "crypto-decision-scan", "paper-reference-scan"])
    parser.add_argument("--packet", type=Path, default=None)
    parser.add_argument("--min-date", default=None,
                         help="*-scan: first evidence date (YYYY-MM-DD)")
    parser.add_argument("--lineage-root", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.kind in SCAN_KINDS:
        if not args.min_date or len(args.min_date) != 10:
            parser.error(f"{args.kind} requires --min-date YYYY-MM-DD")
        results = SCAN_KINDS[args.kind](args.min_date, lineage_root=args.lineage_root)
        summary = {}
        for result in results:
            summary[result["status"]] = summary.get(result["status"], 0) + 1
        print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, sort_keys=True))
        if summary.get("FAILED"):
            _warn(f"{summary['FAILED']} sidecar(s) failed for {args.kind}; see RULE_LINEAGE_EMIT_FAILED lines")
        return 0
    if args.packet is None:
        parser.error(f"{args.kind} requires --packet")
    result = run_cli(args.kind, args.packet, lineage_root=args.lineage_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("status") == "FAILED":
        _warn(result.get("error") or "RULE_LINEAGE_EMIT_FAILED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
