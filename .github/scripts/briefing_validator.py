#!/usr/bin/env python3
"""Atlas deterministic briefing validator (option B) -- ``briefing_validator/4``.

Checks ONLY what can be proved from bytes already in the repository: hash
bindings, arithmetic over machine-written fields, date bindings, and SSOT
cross-references.  Everything requiring judgement -- whether a claim is true,
whether a cause is the cause, whether a market reading is sound -- is listed
explicitly under ``unverified_semantic`` and is NOT silently treated as passed.

Why this exists: a timeout is not semantic validation.  The finalization gate
now remains sealed after timeout, while this validator closes the
machine-checkable half and leaves the other half honestly marked.

Structural validation is DELEGATED, never reimplemented.  rev 1 carried its own
abbreviated packet/locator checks and consequently passed a packet whose
internal ``packet_sha256`` was stale, and passed a locator carrying
``schema_version="evil/9"`` and ``authority.trading=true``.  The production
H-24 path already checks all of that, so rev 2 calls it and fails closed when it
cannot be reached.

Hard invariants, enforced here and pinned by tests:

  * ``conclusion_diff.spec_version`` is ALWAYS null.  A validator must never be
    able to hand itself auto-apply authority by naming a spec.
  * ``UNVALIDATED_TIMEOUT`` is never emitted -- it asserts elapsed time, which
    only the gate may measure.
  * The verdict always names the exact ``delivery_payload_sha256`` it examined.
  * **A clean machine result is NOT a final PASS.**  Machine checks cannot see
    whether a claim is true, so a clean run publishes a machine record and
    leaves the gate's verdict slot empty.  Only a HOLD or a deterministic
    correction is written to the authoritative inbox -- those are things the
    machine really does know.
  * A clean run withdraws its OWN prior block via ``MACHINE_CLEARED`` on the
    machine stream.  rev 2 could raise a structural HOLD but never retract it,
    so one transient fault held a briefing forever even after it was fixed.
    The withdrawal is scoped: it cannot lift a semantic or CIO hold, and it is
    not a PASS.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import briefing_finalization as bf  # noqa: E402

VALIDATOR_CONTRACT = "briefing_validator/4"
VALIDATOR_ID = "atlas-deterministic-validator"
EVIDENCE_RULE_PATH = "config/atlas_evidence_grade_rule.json"

#: Claims this validator structurally cannot judge.  Always reported.
UNVERIFIED_SEMANTIC = [
    "FACT_CLAIMS: whether statements about the world are true",
    "CAUSAL_CLAIMS: whether a stated cause is the cause",
    "MARKET_INTERPRETATION: whether a reading of price/flow is sound",
    "STAGE_DECISIONS: Discovery/Candidate/Ready transitions (criteria Undefined)",
    "NUMBERS_IN_PROSE: figures not cross-referable to a machine-written field",
    "COMPLETENESS: whether something that should have been said was omitted",
]


CANONICAL_ORCHESTRATOR = "briefing/daily_orchestrator.py"
CANONICAL_DELIVERY = ".github/scripts/daily_briefing_delivery.py"


class Finding(dict):
    pass


def _finding(code: str, severity: str, detail: str, **extra) -> Finding:
    return Finding(code=code, severity=severity, detail=detail, **extra)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- check groups

def _run(repo_root: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, *args], cwd=repo_root,
                          capture_output=True, text=True)
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()[:2000]


def _history_argv(history_context: dict | None) -> list[str]:
    """The external history context as canonical CLI arguments.

    Both canonical validators accept the same three optional arguments, so the
    SAME trusted context reaches both subprocesses.  An absent context adds no
    arguments at all -- which is the normal case, because a current (Flow
    version 1) packet carries its own frozen envelope.

    Nothing here invents a value: no live HEAD lookup, no reading a commit out
    of the locator or the packet.  When context is required and was not
    supplied, the subprocess fails and that stays a blocking STRUCTURAL
    finding below, exactly as any other canonical failure does.
    """
    context = history_context or {}
    argv: list[str] = []
    for flag, key in (
        ("--historical-source-commit", "historical_source_commit"),
        ("--trusted-repository-root", "trusted_repository_root"),
        ("--trusted-validation-head", "trusted_validation_head"),
    ):
        value = context.get(key)
        if value is not None:
            argv += [flag, str(value)]
    return argv


def check_canonical_structure(
    repo_root: Path, kst_date: str, slot: str,
    *, history_context: dict | None = None,
) -> tuple[list[Finding], dict | None]:
    """Delegate structural validation to the production H-24 path.

    `daily_orchestrator.py validate` recomputes the packet's own
    ``packet_sha256`` over canonical JSON; `daily_briefing_delivery.py consume`
    checks the locator's schema_version, delivery_scope and authority flags and
    rebuilds the locator.  Re-implementing any of that here is how rev 1 ended
    up blessing artifacts the real consumer would have rejected.

    If the canonical scripts are not reachable, that is a STRUCTURAL failure --
    the alternative is to quietly downgrade to weaker checks and call it PASS.
    """
    findings: list[Finding] = []
    missing = [rel for rel in (CANONICAL_ORCHESTRATOR, CANONICAL_DELIVERY)
               if not (repo_root / rel).exists()]
    if missing:
        return [_finding(
            "CANONICAL_VALIDATOR_UNAVAILABLE", "STRUCTURAL",
            f"canonical structural validators are absent ({missing}); refusing to "
            "substitute a weaker local implementation")], None

    try:
        locator = _load(repo_root / bf.LOCATOR_PATH)
        packet_path = locator["packet_path"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return [_finding("FINALIZATION_LOCATOR_UNREADABLE", "STRUCTURAL", str(exc))], None

    history_argv = _history_argv(history_context)
    code, detail = _run(repo_root, [CANONICAL_ORCHESTRATOR, "validate", packet_path,
                                    *history_argv])
    if code != 0:
        findings.append(_finding("CANONICAL_PACKET_VALIDATION_FAILED", "STRUCTURAL",
                                 detail or f"exit {code}", validator=CANONICAL_ORCHESTRATOR))

    code, detail = _run(repo_root, [CANONICAL_DELIVERY, "consume",
                                    "--slot", slot, "--decision-date", kst_date,
                                    *history_argv])
    if code != 0:
        findings.append(_finding("CANONICAL_CONSUME_FAILED", "STRUCTURAL",
                                 detail or f"exit {code}", validator=CANONICAL_DELIVERY))

    # The gate's own byte-binding contract, which the canonical path does not cover.
    bound = None
    try:
        bound = bf.bind_locator(repo_root, kst_date, slot)
    except bf.FinalizationError as exc:
        findings.append(_finding(exc.code, "STRUCTURAL", exc.message))
    return findings, bound


def load_validated_packet(repo_root: Path, bound: dict | None) -> tuple[list[Finding], dict | None]:
    """Freeze the exact packet bytes accepted by ``bind_locator()``.

    The canonical subprocesses and finalization binding validate path-backed
    bytes.  Downstream checks must not reread a replacement locator/packet pair
    after that point.  Matching the in-memory locator's byte hash proves this
    snapshot is the same packet whose present ``DYNAMIC_CLOCK`` type, shape,
    report SHA, and decision date were checked by ``bind_locator()``.
    """
    if bound is None:
        return [], None
    locator = bound["locator"]
    try:
        packet_bytes = (repo_root / locator["packet_path"]).read_bytes()
    except (OSError, KeyError) as exc:
        return [_finding(
            "VALIDATED_PACKET_CHANGED_BEFORE_USE", "STRUCTURAL",
            f"validated packet is no longer readable: {type(exc).__name__}")], None
    actual = bf._sha256(packet_bytes)
    if actual != locator.get("packet_file_sha256"):
        return [_finding(
            "VALIDATED_PACKET_CHANGED_BEFORE_USE", "STRUCTURAL",
            "packet bytes changed after finalization binding")], None
    try:
        packet = json.loads(packet_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [_finding(
            "VALIDATED_PACKET_CHANGED_BEFORE_USE", "STRUCTURAL",
            f"validated packet became unreadable JSON: {type(exc).__name__}")], None
    if type(packet) is not dict:  # noqa: E721 - exact JSON boundary
        return [_finding(
            "VALIDATED_PACKET_CHANGED_BEFORE_USE", "STRUCTURAL",
            "validated packet is no longer a JSON object")], None
    return [], packet


def check_payload_binding(repo_root: Path, kst_date: str, slot: str) -> tuple[list[Finding], dict | None]:
    directory = bf.slot_dir(repo_root, kst_date, slot)
    draft_file = bf._latest(directory, "draft")
    if draft_file is None:
        return [_finding("NO_SEALED_DRAFT", "STRUCTURAL",
                         f"no sealed draft for {kst_date}/{slot}")], None
    draft = _load(draft_file)
    payload_file = directory / f"payload-rev-{draft['rev']:03d}.md"
    if not payload_file.exists():
        return [_finding("SEALED_PAYLOAD_MISSING", "STRUCTURAL",
                         f"{payload_file.name} is absent")], draft

    findings: list[Finding] = []
    payload = payload_file.read_bytes()
    if bf._sha256(payload) != draft["delivery_payload_sha256"]:
        findings.append(_finding("SEALED_PAYLOAD_SHA_MISMATCH", "STRUCTURAL",
                                 "sealed payload bytes do not match the sealed hash"))
    if draft["delivery_marker"].encode() not in payload:
        findings.append(_finding("DELIVERY_MARKER_ABSENT", "STRUCTURAL",
                                 "the payload does not carry its own delivery marker"))
    try:
        briefing = (repo_root / draft["source"]["briefing_path"]).read_bytes()
        if briefing.rstrip(b"\n") not in payload:
            findings.append(_finding("PAYLOAD_DOES_NOT_CONTAIN_BRIEFING", "STRUCTURAL",
                                     "the sealed payload does not contain briefing.md verbatim"))
    except OSError:
        findings.append(_finding("BRIEFING_UNREADABLE", "STRUCTURAL",
                                 "briefing.md named by the draft could not be read"))
    return findings, draft


def check_arithmetic(
    repo_root: Path,
    kst_date: str,
    slot: str,
    *,
    locator: dict | None,
    packet: dict | None,
) -> list[Finding]:
    """Arithmetic over machine-written fields -- not figures in prose."""
    findings: list[Finding] = []
    if locator is None or packet is None:
        return findings
    try:
        index = _load(repo_root / locator["index_path"])
    except (OSError, json.JSONDecodeError, KeyError):
        return findings

    revisions = index.get("revisions", [])
    numbers = [r.get("revision") for r in revisions]
    if numbers and index.get("latest_revision") != max(numbers):
        findings.append(_finding(
            "INDEX_LATEST_REVISION_WRONG", "CORRECTION", "latest_revision disagrees with revisions[]",
            correction_class="ARITHMETIC", field_path="index.latest_revision",
            before=index.get("latest_revision"), after=max(numbers)))
    if len(numbers) != len(set(numbers)):
        findings.append(_finding("INDEX_REVISIONS_NOT_UNIQUE", "STRUCTURAL",
                                 f"duplicate revision numbers in index: {numbers}"))

    entry = next((r for r in revisions if r.get("revision") == locator.get("revision")), None)
    counts = (entry or {}).get("component_status_counts") or {}
    components = packet.get("components") or []
    if counts and components:
        total = sum(counts.values())
        if total != len(components):
            findings.append(_finding(
                "COMPONENT_COUNT_MISMATCH", "CORRECTION",
                f"component_status_counts sums to {total} but the packet has {len(components)}",
                correction_class="ARITHMETIC", field_path="index.component_status_counts",
                before=total, after=len(components)))
        actual: dict[str, int] = {}
        for component in components:
            status = component.get("status")
            if status is not None:
                actual[status] = actual.get(status, 0) + 1
        for status, declared in counts.items():
            if actual.get(status, 0) != declared:
                findings.append(_finding(
                    "COMPONENT_STATUS_COUNT_WRONG", "CORRECTION",
                    f"status {status!r}: index says {declared}, packet has {actual.get(status, 0)}",
                    correction_class="ARITHMETIC",
                    field_path=f"index.component_status_counts.{status}",
                    before=declared, after=actual.get(status, 0)))

    step0_path = repo_root / "data/briefing/step0_status.json"
    if step0_path.exists():
        step0 = _load(step0_path)
        collectors = (step0.get("collectors") or {}).values()
        totals = step0.get("totals") or {}
        for key in ("ok", "failed"):
            summed = sum(c.get(key, 0) for c in collectors)
            if key in totals and totals[key] != summed:
                findings.append(_finding(
                    "STEP0_TOTALS_WRONG", "CORRECTION",
                    f"step0 totals.{key} is {totals[key]} but collectors sum to {summed}",
                    correction_class="ARITHMETIC", field_path=f"step0_status.totals.{key}",
                    before=totals[key], after=summed))
    return findings


def check_dates(repo_root: Path, kst_date: str, slot: str, draft: dict | None) -> list[Finding]:
    findings: list[Finding] = []
    if draft is None:
        return findings
    try:
        briefing = (repo_root / draft["source"]["briefing_path"]).read_text(encoding="utf-8")
    except OSError:
        return findings

    header = briefing.splitlines()[0] if briefing.strip() else ""
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", header)
    if not dates:
        findings.append(_finding(
            "BRIEFING_HEADER_DATE_ABSENT", "CORRECTION",
            "briefing header carries no date; the date binding cannot be checked",
            correction_class="DATE", field_path="briefing.md:1",
            before=None, after=kst_date))
    elif kst_date not in dates:
        findings.append(_finding(
            "BRIEFING_HEADER_DATE_MISMATCH", "CORRECTION",
            f"briefing header names {dates[0]}, the slot is {kst_date}",
            correction_class="DATE", field_path="briefing.md:1",
            before=dates[0], after=kst_date))
    if slot not in header.lower() and header:
        findings.append(_finding(
            "BRIEFING_HEADER_SLOT_ABSENT", "OBSERVATION",
            f"briefing header does not name the slot {slot!r}"))

    step0_path = repo_root / "data/briefing/step0_status.json"
    if step0_path.exists():
        step0 = _load(step0_path)
        if step0.get("expected_kst_date") != kst_date:
            # Not a correction: a briefing may legitimately report that data is
            # not ready. Recorded so a reader can see the gap, not judged.
            findings.append(_finding(
                "STEP0_DATE_NOT_TODAY", "OBSERVATION",
                f"step0_status.expected_kst_date is {step0.get('expected_kst_date')}, "
                f"the slot is {kst_date}"))
        for name, collector in (step0.get("collectors") or {}).items():
            if collector.get("collected_for_kst_date") != kst_date:
                findings.append(_finding(
                    "COLLECTOR_DATE_NOT_TODAY", "OBSERVATION",
                    f"collector {name} collected_for_kst_date="
                    f"{collector.get('collected_for_kst_date')}"))
    return findings


def check_ssot_cross_reference(repo_root: Path, kst_date: str) -> list[Finding]:
    findings: list[Finding] = []
    for family in ("krx", "sec", "dart"):
        directory = repo_root / "data/briefing" / family
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                view = _load(path)
            except json.JSONDecodeError:
                findings.append(_finding("COMPACT_VIEW_UNREADABLE", "OBSERVATION",
                                         f"{family}/{path.name} is not valid JSON"))
                continue
            collected = (view.get("source") or {}).get("collected_for_kst_date")
            if collected is not None and collected != kst_date:
                findings.append(_finding(
                    "COMPACT_VIEW_DATE_NOT_TODAY", "OBSERVATION",
                    f"{family}/{path.name} collected_for_kst_date={collected}",
                    symbol=path.stem))
    return findings


def check_evidence_grades(repo_root: Path, draft: dict | None) -> tuple[list[Finding], list[str]]:
    """Only runs against a ratified, mechanical rule.  Absent rule == not checked.

    Inventing the rule here would be exactly the `Undefined` -> made-up-on-site
    move the Decision Boundary forbids.
    """
    rule_path = repo_root / EVIDENCE_RULE_PATH
    if not rule_path.exists():
        return [], ["EVIDENCE_GRADES: no ratified mechanical rule at "
                    f"{EVIDENCE_RULE_PATH}; grade tags were not checked"]
    rule = _load(rule_path)
    patterns = rule.get("grade_patterns") or {}
    if not patterns or draft is None:
        return [], ["EVIDENCE_GRADES: rule file present but defines no grade_patterns"]

    try:
        briefing = (repo_root / draft["source"]["briefing_path"]).read_text(encoding="utf-8")
    except OSError:
        return [], ["EVIDENCE_GRADES: briefing.md unreadable"]

    findings = []
    for grade, spec in patterns.items():
        marker = spec.get("marker")
        requires = spec.get("requires_source_path", False)
        if not marker:
            continue
        for lineno, line in enumerate(briefing.splitlines(), start=1):
            if marker not in line:
                continue
            if requires:
                cited = re.findall(r"[\w./-]+\.(?:json|md|txt|jsonl)", line)
                if not any((repo_root / c).exists() for c in cited):
                    findings.append(_finding(
                        "EVIDENCE_GRADE_SOURCE_MISSING", "CORRECTION",
                        f"line {lineno} is tagged {grade} but cites no existing repo path",
                        correction_class="EVIDENCE_GRADE",
                        field_path=f"briefing.md:{lineno}", before=grade, after=None))
    return findings, []


BLOCKING_MACHINE_STATUSES = ("HOLD", "PASS_WITH_CORRECTION")


def machine_stream_is_blocking(repo_root: Path, kst_date: str, slot: str) -> bool:
    """Did THIS stream leave a block standing that is now stale?

    Both the recorded state AND the current authoritative machine inbox count.
    rev 3 looked only at recorded verdicts, so this sequence cost a whole round:
    B raises a machine HOLD, the runner dies before drain records it, the cause
    is fixed, B sees no recorded block and issues no withdrawal -- and then drain
    ingests the stale HOLD for the first time and blocks the fixed briefing.
    """
    directory = bf.slot_dir(repo_root, kst_date, slot)
    latest = bf._latest_per_stream(bf._recorded_validations(directory)).get("machine")
    if latest and latest["validation_status"] in BLOCKING_MACHINE_STATUSES:
        return True
    authority, _superseded, unreadable = bf.authoritative_inboxes(directory)
    if unreadable:
        # Unparseable material is not "no block" -- the gate fails closed on it,
        # so the validator must not report the slot as clear either.
        return True
    pending = authority.get("machine")
    if pending is None:
        return False
    try:
        return _load(pending).get("validation_status") in BLOCKING_MACHINE_STATUSES
    except (OSError, json.JSONDecodeError):
        return True


def collect_post_delivery_inputs(repo_root: Path, kst_date: str, slot: str) -> list[dict]:
    directory = bf.slot_dir(repo_root, kst_date, slot)
    out = []
    for path in sorted(directory.glob("post-delivery-change-rev-*.json")):
        body = _load(path)
        out.append({"rev": body["rev"], "changed_axes": [a["axis"] for a in body.get("changed_axes", [])],
                    "post_delivery_change_key": body.get("post_delivery_change_key"),
                    "capital_impact": bf.UNKNOWN})
    return out


# --------------------------------------------------------------------- verdict

def validate(
    repo_root: Path, kst_date: str, slot: str,
    *, history_context: dict | None = None,
) -> dict:
    payload_findings, draft = check_payload_binding(repo_root, kst_date, slot)
    findings = list(payload_findings)
    structure_findings, bound = check_canonical_structure(
        repo_root, kst_date, slot, history_context=history_context
    )
    findings += structure_findings
    snapshot_findings, packet = load_validated_packet(repo_root, bound)
    findings += snapshot_findings
    findings += check_arithmetic(
        repo_root,
        kst_date,
        slot,
        locator=bound["locator"] if bound is not None else None,
        packet=packet,
    )
    findings += check_dates(repo_root, kst_date, slot, draft)
    findings += check_ssot_cross_reference(repo_root, kst_date)
    grade_findings, grade_unverified = check_evidence_grades(repo_root, draft)
    findings += grade_findings

    structural = [f for f in findings if f["severity"] == "STRUCTURAL"]
    corrections = [
        {"id": f"COR-{i:02d}", "class": f["correction_class"], "field_path": f["field_path"],
         "before": f.get("before"), "after": f.get("after"), "source": f["code"]}
        for i, f in enumerate((f for f in findings if f["severity"] == "CORRECTION"), start=1)
    ]

    if structural:
        machine_status = "HOLD"
    elif corrections:
        machine_status = "PASS_WITH_CORRECTION"
    else:
        machine_status = "MACHINE_PASS"

    # (P0) A clean machine run is NOT a final PASS.  Nothing here can see
    # whether a claim is true, so the gate's verdict slot stays empty until the
    # named semantic validator answers. Submitting PASS would be the machine
    # vouching for facts it never examined -- and rev 1 did exactly that,
    # straight through to delivery.
    semantic_status = "UNVERIFIED"
    submit = machine_status in ("HOLD", "PASS_WITH_CORRECTION")
    clears_prior_block = False
    if not submit and machine_stream_is_blocking(repo_root, kst_date, slot):
        # The machine raised a block earlier and the cause is gone. Withdrawing
        # it is the one thing a clean machine run is entitled to say.
        submit = True
        clears_prior_block = True

    verdict = {
        "validator_contract": VALIDATOR_CONTRACT,
        "validator_id": VALIDATOR_ID,
        "validated_at_utc": bf._iso(bf._utcnow()),
        "authority_stream": "machine",
        "machine_status": machine_status,
        "semantic_status": semantic_status,
        "clears_prior_machine_block": clears_prior_block,
        "final_validation_open": not submit,
        "submits_to_gate": submit,
        "corrections": corrections,
        # Never non-null: naming a spec is how a validator would grant itself
        # auto-apply, and no spec is ratified.
        "conclusion_diff": {"spec_version": None},
        "findings": findings,
        "unverified_semantic": UNVERIFIED_SEMANTIC + grade_unverified,
        "post_delivery_inputs": collect_post_delivery_inputs(repo_root, kst_date, slot),
        "checks_run": ["canonical_structure", "payload_binding", "arithmetic", "dates",
                       "ssot_cross_reference", "evidence_grades"],
        "structural_authority": [CANONICAL_ORCHESTRATOR, CANONICAL_DELIVERY],
    }
    if submit:
        # Only statuses the machine genuinely knows are handed to the gate.
        verdict["validation_status"] = (bf.MACHINE_CLEARED if clears_prior_block
                                        else machine_status)
    if draft is not None:
        verdict["delivery_payload_sha256"] = draft["delivery_payload_sha256"]
    return verdict


def emit(repo_root: Path, kst_date: str, slot: str, verdict: dict) -> dict:
    """Write the machine record; hand the gate a verdict only when entitled to.

    A clean machine run produces `machine-validation-rev-NNN.json` and NO inbox
    file, so the gate still sees an open verdict slot.  Writing a clean result
    into the inbox would assert a final PASS the machine cannot support.
    """
    directory = bf.slot_dir(repo_root, kst_date, slot)
    directory.mkdir(parents=True, exist_ok=True)
    # Written temp-then-rename: the gate fails closed on an unparseable inbox
    # file, so a writer that can leave half a file behind can permanently block
    # a slot by crashing mid-write. With atomic publication, unreadable durable
    # material means real corruption -- and fail-closed is then the right call.
    body = json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"

    rev = bf._next_rev(directory, "machine-validation")
    record = directory / f"machine-validation-rev-{rev:03d}.json"
    bf._atomic_write(record, body)
    out = {"machine_record": str(record.relative_to(repo_root)), "inbox": None}

    if verdict.get("submits_to_gate"):
        rev = bf._next_rev(directory, "validation-inbox")
        inbox = directory / f"validation-inbox-rev-{rev:03d}.json"
        bf._atomic_write(inbox, body)
        out["inbox"] = str(inbox.relative_to(repo_root))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atlas deterministic briefing validator")
    parser.add_argument("--slot", required=True, choices=list(bf.SUPPORTED_SLOTS))
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--emit-inbox", action="store_true",
                        help="write the verdict to the authoritative inbox path")
    # Optional external history context, forwarded verbatim to BOTH canonical
    # subprocesses. Never defaulted from the locator, the packet or live HEAD.
    parser.add_argument(
        "--historical-source-commit",
        help="externally trusted ORIGINAL Flow source commit for a legacy packet")
    parser.add_argument("--trusted-repository-root")
    parser.add_argument("--trusted-validation-head")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    verdict = validate(repo_root, args.decision_date, args.slot, history_context={
        "historical_source_commit": args.historical_source_commit,
        "trusted_repository_root": args.trusted_repository_root,
        "trusted_validation_head": args.trusted_validation_head,
    })
    if args.emit_inbox:
        verdict["emitted"] = emit(repo_root, args.decision_date, args.slot, verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True))
    if verdict.get("clears_prior_machine_block"):
        print("machine checks clean; a prior machine block was withdrawn. "
              "This is NOT a PASS -- the semantic verdict slot is still open.",
              file=sys.stderr)
    elif verdict.get("final_validation_open"):
        print("machine checks clean; final validation remains OPEN "
              "(semantic_status=UNVERIFIED). No verdict was handed to the gate.",
              file=sys.stderr)
    # A HOLD is a valid, successful validation run; the gate decides what it means.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
