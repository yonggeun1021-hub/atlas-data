#!/usr/bin/env python3
"""Class-wide guard: every GitHub Actions checkout that feeds a real
git-history-walking consumer must use ``fetch-depth: 0``.

Why this exists -- read ``docs/do_not_touch_and_why.md`` entry 1 for the
full incident record (path reference only: that file is added by PR #817,
which may not be on ``main`` yet when this test lands; do not import it).

  - 2026-08-25: a shallow-checkout change landed because a local grep of
    this repository's own tree did not find the history-walking consumer.
    ``identity/canonical_identity.py``'s ``resolve_instrument_identity()``
    calls ``_git_history_commits()`` (``git log --follow``) and
    ``verify_document_matches_source()`` (``git show <commit>:<path>``) to
    derive real first-seen/tamper verdicts. On a shallow clone those calls
    fail *closed* to ``NOT_COMPUTABLE`` rather than erroring loudly, so a
    shallow checkout upstream of this class of consumer breaks silently --
    it does not fail CI on its own.
  - 2026-09-18: the same mistake was nearly repeated. Five capture
    workflows were correctly narrowed to ``fetch-depth: 1`` because they
    never read git history, and ``crypto-breadth-capture.yml`` was swept up
    with them by mistake and had to be reverted byte-for-byte (commit
    ``b12e33d60``, part of PR #787) -- a sha256 pin issue
    (``config/regime_source_owner_registry_v2.json``), not a history-walking
    one, but it shows how easily this class of checkout gets narrowed by
    accident during an otherwise-correct cleanup.
  - ``.github/workflows/actions-pass.yml``'s ``regression`` job has its own
    hand-written guard
    (``test_runner_reporting.py::ActionsPassDiagnosticIsolationTest``).
    ``.github/workflows/btc-price-capture.yml`` has the exact same shape of
    consumer (``p3_10_crypto_risk_population.py`` ->
    ``identity/canonical_identity.py``) and, as of 2026-09-18, already has
    the correct ``fetch-depth: 0`` -- but no test in this repository
    asserted it, so a future PR that shallowed it would have passed CI
    silently. This file closes that specific gap and the general class it
    belongs to in one place.

Design, and why it is one test instead of one-per-file:

This walks every workflow under ``.github/workflows`` and, for every job,
checks whether any step's ``run:`` text invokes one of the
``HISTORY_DEPENDENT_SCRIPTS`` below. A *newly added* workflow that calls an
*already-registered* history-dependent script is therefore covered
automatically, with no new test to write.

What is NOT automatic, and is instead an explicit, hand-verified registry:
which scripts actually walk git history for a real verdict. This was tried
as a fully automatic transitive-import sweep first and rejected -- it
produced both false positives (e.g. ``crypto_breadth.py`` defines its own,
unrelated, same-named local function ``canonical_identity()``; matching on
the name alone would have wrongly required full history for
``crypto-breadth-capture.yml``) and false negatives (several consumers call
a local ``_git()``/``_git_result(repo, *args)`` wrapper, so the git verb
never appears adjacent to the literal string ``"git"``). A per-file
allowlist is the fallback the task that produced this file explicitly
sanctioned for exactly this reason.

To keep that allowlist from silently going stale, ``test_registry_covers_
every_git_history_shape_in_the_repository`` independently re-derives the
same signal by scanning every ``.py`` file in the repository (outside
``test/`` and ``validation/``, where git-history assertions are the test's
own business, not a production consumer's) for the literal
``["git", ..., "show"/"log"/"merge-base"/"worktree"/"cat-file"]`` argv shape
or the equivalent ``_git()``/``_git_result()`` wrapper call, and for
``--follow``. Any file with that shape that is not a key in
``HISTORY_DEPENDENT_SCRIPTS`` fails the test -- this is the fail-closed
behavior for a newly added consumer that nobody remembered to register,
rather than the consumer silently going unenforced.

A consumer can also live in code checked out *by* the workflow rather than
in this repository's own tree -- exactly the shape that caused the
2026-08-25 miss (a consumer in a sibling private repository, invisible to
a grep of this one). ``import-p8-15-portal-observation.yml`` clones
``atlas-portal`` at runtime via ``gh repo clone`` (not
``actions/checkout``, so it has no ``fetch-depth`` field at all) and
``acceptance/portal_observation_receipt.py`` walks *that* clone's own
history via ``git show``. ``gh repo clone`` defaults to a full clone, so
this is not shallow today, but nothing about the ``fetch-depth:`` class of
assertion protects it if someone later adds a ``--depth`` flag to that
clone command.  ``test_portal_clone_is_not_narrowed_to_a_shallow_depth``
below is this file's narrow, structurally-different guard for that case
(``test_capital_rotation_e2e_acceptance.py`` already pins the exact clone
command string, which incidentally also catches this; this test asserts
the same property directly and by name, so the reason survives even if
that other test is ever refactored).
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOWS_DIR = ROOT / ".github" / "workflows"

# Directories excluded from the repo-wide fail-closed sweep. Test/validation
# files legitimately call real git history to PROVE a production consumer's
# result (e.g. test_replay_asset_identity.py); that is the test's own
# business and is already covered by whichever job runs it, not a signal
# that some *other* workflow's checkout needs fetch-depth: 0.
SWEEP_EXCLUDED_TOP_DIRS = {"test", "validation", ".git"}

# ---------------------------------------------------------------------------
# The registry. Every entry was opened and its git subprocess call(s) read,
# not inferred from a name or a docstring (docs/do_not_touch_and_why.md's
# own opening paragraph: "reading a comment is not verification"). Verified
# against `main` at 04c08193a.
# ---------------------------------------------------------------------------
HISTORY_DEPENDENT_SCRIPTS: dict[str, str] = {
    # The reported gap's own consumer.
    "identity/canonical_identity.py":
        "resolve_instrument_identity()/_git_history_commits() walks "
        "`git log --follow`; verify_document_matches_source() reads "
        "`git show <commit>:<path>` for first-seen/tamper verdicts.",
    # Direct wrapper entrypoints: invoked by a workflow `run:` step and
    # themselves import identity.canonical_identity unconditionally on
    # their main code path (one hop, hand-verified -- see module docstring
    # for why this is not resolved automatically).
    ".github/scripts/p3_10_crypto_risk_population.py":
        "imports identity.canonical_identity unconditionally "
        "(btc-price-capture.yml's own consumer).",
    "clock/candidate_validity_window.py":
        "imports identity.canonical_identity unconditionally.",
    "decision/shadow_entry_review.py":
        "imports identity.canonical_identity unconditionally.",
    "identity/candidate_identity_authority_proposal.py":
        "imports identity.canonical_identity unconditionally.",
    "identity/candidate_identity_gap_inventory.py":
        "imports identity.canonical_identity unconditionally.",
    "identity/candidate_identity_observation.py":
        "imports identity.canonical_identity unconditionally.",
    ".github/scripts/daily_briefing_recovery.py":
        "dynamically imports .github/scripts/briefing_finalization.py "
        "unconditionally (already guarded -- see "
        "test_daily_briefing_recovery.py).",
    # Other confirmed direct git-history producers (subprocess argv or the
    # local _git()/_git_result() wrapper call site opened and read).
    ".github/scripts/briefing_finalization.py":
        "walks `git merge-base --is-ancestor` and `git show` to bind "
        "finalized bytes to real commit lineage.",
    ".github/scripts/korea_capital_rotation_ledger_proof.py":
        "PAPER-consumption path walks `git merge-base --is-ancestor` and "
        "`git show` to bind a runtime release to a reviewed publication "
        "commit.",
    ".github/scripts/korea_observation_pair_controller.py":
        "verify-handoff walks `git merge-base --is-ancestor` and "
        "`git cat-file` to prove a public commit is on current main.",
    ".github/scripts/publish_scheduled_briefing_authority.py":
        "reads `git cat-file`/`git show` for scheduled-briefing publication "
        "provenance.",
    ".github/scripts/validated_briefing_portal_producer.py":
        "reads `git show <commit>:<path>` to bind a validated portal "
        "projection to committed bytes.",
    "acceptance/capital_rotation_e2e.py":
        "reads `git show <commit>:<path>` for capital-rotation acceptance "
        "evidence.",
    "acceptance/portal_observation_receipt.py":
        "reads `git show <commit>:<path>` -- from an EXTERNALLY cloned "
        "atlas-portal checkout; see the module docstring's note on "
        "consumers living in code the workflow checks out at runtime.",
    "audit/data_coverage_matrix.py":
        "reads `git log`/`git show` for coverage-matrix provenance.",
    "briefing/daily_orchestrator.py":
        "reads `git cat-file` for briefing orchestration provenance.",
    "briefing_core/chain.py":
        "reads `git cat-file`/`git show` for briefing chain provenance.",
    "decision/decision_change_lineage_operational.py":
        "walks `git merge-base`, `git show`, `git cat-file` and "
        "`git worktree` to derive real decision-change lineage.",
    "discovery/wildcard_operational_intake.py":
        "walks `git log`/`git show` for wildcard intake first-seen "
        "verdicts (already guarded -- see "
        "test_wildcard_operational_intake.py).",
    "identity/kis_071050_proposal_review.py":
        "reads `git show <commit>:<path>` for identity proposal review.",
    "identity/kis_official_evidence_resolver.py":
        "reads `git cat-file`/`git show` for official-evidence resolution.",
    "portfolio/capital_flow_posture_reference.py":
        "_git_result(root, 'merge-base', '--is-ancestor', ...) verifies "
        "flow-replay provenance against real commit ancestry.",
    "portfolio/profit_harvest_readiness.py":
        "reads `git show <commit>:<path>` for profit-harvest readiness "
        "evidence.",
    "portfolio_risk/kis_valuation_authority.py":
        "_row_first_seen()/_approval_first_seen() walk `git log`/`git show` "
        "for real first-seen verdicts (docs/do_not_touch_and_why.md entry "
        "1's second confirmed consumer in the actions-pass.yml regression "
        "job).",
    "portfolio_risk/kis_valuation_freshness_policy_review.py":
        "reads `git cat-file`/`git show` for valuation freshness-policy "
        "review.",
    "portfolio_risk/kis_valuation_semantic_review.py":
        "reads `git show <commit>:<path>` for valuation semantic review.",
    "portfolio_risk/portfolio_account_fact_consumption_authority.py":
        "reads `git log`/`git show` for portfolio account-fact consumption "
        "authority.",
    "regime/kr_paper_runtime_adoption_v1.py":
        "walks `git log` for KR paper-runtime adoption lineage.",
    "regime/stage1_market_tuple.py":
        "reads `git show <commit>:<path>` for Stage1 market-tuple "
        "provenance.",
    "rotation/crypto_rotation_30d_coverage_recalc.py":
        "reads `git show <commit>:<path>` for 30-day coverage "
        "recalculation.",
    "rotation/rotation_state_ledger_operational_readiness.py":
        "_git_result(root, 'merge-base', '--is-ancestor', ...) and "
        "`git show`/`git cat-file` verify rotation-state ledger "
        "provenance.",
    "rotation/theme_taxonomy_authority.py":
        "reads `git log`/`git show` for theme-taxonomy authority.",
    "rotation/theme_taxonomy_population.py":
        "walks `git merge-base --is-ancestor` and `git show` for "
        "theme-taxonomy population provenance.",
    "rules/ratified_rule_decision.py":
        "walks `git log`/`git show` for real ratified-rule first-seen "
        "lineage.",
    "universe/global_asset_master_population_readiness.py":
        "walks `git log`/`git show` for global-asset-master first-seen "
        "lineage.",
    "universe/global_asset_master_theme_application_cli.py":
        "reads `git show <commit>:<path>` for theme-application "
        "provenance.",
}

# Verb shapes that make a git subprocess call a real history walk rather
# than a plain status/add/commit/push operation.
_GIT_HISTORY_VERBS = ("show", "log", "merge-base", "worktree", "cat-file")
_LITERAL_ARGV_RE = re.compile(
    r'\[\s*"git"[^\]]*?"(' + "|".join(_GIT_HISTORY_VERBS) + r')"'
)
_WRAPPER_CALL_RE = re.compile(
    r'_git(?:_result)?\(\s*(?:[A-Za-z_.]+,\s*)?"('
    + "|".join(_GIT_HISTORY_VERBS) + r')"'
)


def _git_history_shape(content: str) -> set[str]:
    hits = {m.group(1) for m in _LITERAL_ARGV_RE.finditer(content)}
    hits |= {m.group(1) for m in _WRAPPER_CALL_RE.finditer(content)}
    if '"--follow"' in content:
        hits.add("--follow")
    return hits


def _iter_repo_python_files():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if ".git" in rel.parts:
            continue
        if rel.parts[0] in SWEEP_EXCLUDED_TOP_DIRS:
            continue
        yield rel.as_posix(), path


def _load_workflow(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)
    assert isinstance(doc, dict) and "jobs" in doc, f"{path} has no jobs:"
    return doc


def _self_repo_checkout_steps(job: dict) -> list[dict]:
    steps = job.get("steps") or []
    out = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses", "")
        if not uses.startswith("actions/checkout@"):
            continue
        withc = step.get("with", {}) or {}
        # A checkout of a DIFFERENT repository is a distinct class (see the
        # module docstring's note on external clones); this file only
        # asserts fetch-depth for checkouts of this repository itself.
        if withc.get("repository"):
            continue
        out.append(step)
    return out


def _job_run_text(job: dict) -> str:
    steps = job.get("steps") or []
    parts = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if run:
            # Strip whole-line/trailing shell comments so a path merely
            # mentioned in a `#` comment cannot count as an invocation.
            parts.append(re.sub(r"(?m)#.*$", "", run))
    return "\n".join(parts)


def _script_invoked(run_text: str, rel_path: str) -> bool:
    stem = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    dotted = stem.replace("/", ".")
    file_pattern = re.compile(
        r"python3?\s+(?:\./)?" + re.escape(rel_path) + r"\b"
    )
    module_pattern = re.compile(
        r"python3?\s+-m\s+" + re.escape(dotted) + r"\b"
    )
    return bool(file_pattern.search(run_text) or module_pattern.search(run_text))


class RegistryHygieneTests(unittest.TestCase):
    """The registry itself must stay accurate, in both directions."""

    def test_every_registered_script_exists_on_disk(self):
        for rel_path in HISTORY_DEPENDENT_SCRIPTS:
            with self.subTest(script=rel_path):
                self.assertTrue(
                    (ROOT / rel_path).is_file(),
                    f"{rel_path} is registered as history-dependent but no "
                    f"longer exists -- update HISTORY_DEPENDENT_SCRIPTS "
                    f"(renamed/removed file, stale registry entry).",
                )

    def test_every_registered_script_still_shows_a_git_history_shape(self):
        # A wrapper entrypoint (imports a leaf, does not call git itself)
        # is allowed to show no shape of its own.
        wrapper_only = {
            ".github/scripts/p3_10_crypto_risk_population.py",
            "clock/candidate_validity_window.py",
            "decision/shadow_entry_review.py",
            "identity/candidate_identity_authority_proposal.py",
            "identity/candidate_identity_gap_inventory.py",
            "identity/candidate_identity_observation.py",
            ".github/scripts/daily_briefing_recovery.py",
        }
        for rel_path in HISTORY_DEPENDENT_SCRIPTS:
            if rel_path in wrapper_only:
                continue
            with self.subTest(script=rel_path):
                content = (ROOT / rel_path).read_text(encoding="utf-8", errors="ignore")
                self.assertTrue(
                    _git_history_shape(content),
                    f"{rel_path} is registered as history-dependent but no "
                    f"longer shows a git log/show/merge-base/worktree/"
                    f"cat-file/--follow shape -- if it was rewritten to "
                    f"read a committed manifest instead, remove it from "
                    f"HISTORY_DEPENDENT_SCRIPTS (see the module docstring's "
                    f"'legitimate way to change it' note).",
                )

    def test_registry_covers_every_git_history_shape_in_the_repository(self):
        """Fail closed: a NEW consumer with this shape that nobody
        registered must break this test, not go silently unenforced."""
        unregistered = []
        for rel_path, path in _iter_repo_python_files():
            if rel_path in HISTORY_DEPENDENT_SCRIPTS:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            if _git_history_shape(content):
                unregistered.append(rel_path)
        self.assertEqual(
            unregistered, [],
            "New git-history-walking code found that is not in "
            "HISTORY_DEPENDENT_SCRIPTS: "
            f"{unregistered!r}. Classify it: add it to the registry in "
            "this file (test/test_workflow_history_checkout_depth.py) with "
            "the exact call site, then confirm every workflow job that "
            "invokes it (or a script that imports it unconditionally) sets "
            "fetch-depth: 0 on its checkout.",
        )


class WorkflowCheckoutDepthTests(unittest.TestCase):
    """The actual class-wide guard: any job whose steps invoke a
    registered history-dependent script must check out full history."""

    def test_history_dependent_jobs_use_fetch_depth_zero(self):
        self.assertTrue(WORKFLOWS_DIR.is_dir())
        workflow_paths = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(
            WORKFLOWS_DIR.glob("*.yaml")
        )
        self.assertGreater(len(workflow_paths), 0)
        checked_any = False
        for wf_path in workflow_paths:
            doc = _load_workflow(wf_path)
            jobs = doc.get("jobs") or {}
            for job_name, job in jobs.items():
                if not isinstance(job, dict):
                    continue
                run_text = _job_run_text(job)
                if not run_text:
                    continue
                matched_scripts = [
                    rel_path
                    for rel_path in HISTORY_DEPENDENT_SCRIPTS
                    if _script_invoked(run_text, rel_path)
                ]
                if not matched_scripts:
                    continue
                checked_any = True
                checkouts = _self_repo_checkout_steps(job)
                with self.subTest(workflow=wf_path.name, job=job_name):
                    if not checkouts:
                        # Not every job uses actions/checkout. A manual
                        # `git init` + `git fetch <ref>` (no actions/
                        # checkout, so no fetch-depth field to read) is a
                        # full-history checkout by construction as long as
                        # the fetch never passes --depth -- accept that
                        # shape instead of treating "no actions/checkout
                        # step" as automatically unguarded.
                        self.assertRegex(
                            run_text, r"git\s+fetch\b",
                            f"{wf_path.name}:{job_name} invokes "
                            f"{matched_scripts} (history-dependent) but has "
                            f"no actions/checkout step AND no manual "
                            f"`git fetch` checkout -- no full-history "
                            f"checkout mechanism found at all.",
                        )
                        self.assertNotIn(
                            "--depth", run_text,
                            f"{wf_path.name}:{job_name} invokes "
                            f"{matched_scripts} (history-dependent) via a "
                            f"manual `git fetch` checkout, and that fetch "
                            f"now has a --depth flag -- this is the "
                            f"2026-08-25 mistake's shape via a manual "
                            f"checkout instead of actions/checkout's "
                            f"fetch-depth.",
                        )
                        continue
                    for step in checkouts:
                        depth = (step.get("with") or {}).get("fetch-depth")
                        self.assertEqual(
                            depth, 0,
                            f"{wf_path.name}:{job_name} invokes "
                            f"{matched_scripts} (history-dependent: "
                            f"{[HISTORY_DEPENDENT_SCRIPTS[s] for s in matched_scripts]}) "
                            f"but its checkout step has fetch-depth="
                            f"{depth!r}, not 0. A shallow clone here fails "
                            f"closed and silently, per docs/do_not_touch_"
                            f"and_why.md entry 1 -- this is exactly the "
                            f"2026-08-25 / 2026-09-18 mistake.",
                        )
        self.assertTrue(
            checked_any,
            "No workflow job matched any HISTORY_DEPENDENT_SCRIPTS entry -- "
            "the invocation-matching regex likely broke; this test should "
            "be exercising at least btc-price-capture.yml.",
        )

    def test_btc_price_capture_is_covered_by_this_guard(self):
        """The specific reported gap this file was written to close."""
        doc = _load_workflow(WORKFLOWS_DIR / "btc-price-capture.yml")
        job = doc["jobs"]["capture"]
        run_text = _job_run_text(job)
        self.assertTrue(
            _script_invoked(
                run_text, ".github/scripts/p3_10_crypto_risk_population.py"
            )
        )
        checkouts = _self_repo_checkout_steps(job)
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"]["fetch-depth"], 0)


class ExternalHistoryCheckoutTests(unittest.TestCase):
    """The class also covers a consumer walking a repo checked out by the
    workflow at runtime rather than via actions/checkout (see the module
    docstring)."""

    def test_portal_clone_is_not_narrowed_to_a_shallow_depth(self):
        wf_path = WORKFLOWS_DIR / "import-p8-15-portal-observation.yml"
        text = wf_path.read_text(encoding="utf-8")
        self.assertIn("gh repo clone yonggeun1021-hub/atlas-portal", text)
        clone_line = next(
            line for line in text.splitlines()
            if "gh repo clone yonggeun1021-hub/atlas-portal" in line
        )
        self.assertNotIn(
            "--depth", clone_line,
            "import-p8-15-portal-observation.yml's `gh repo clone` gained a "
            "--depth flag. acceptance/portal_observation_receipt.py reads "
            "`git show <commit>:<path>` against this clone's own history; "
            "`gh repo clone` defaults to full history with no --depth, so "
            "adding one here is exactly the 2026-08-25 mistake's shape, "
            "just via `gh repo clone` instead of actions/checkout's "
            "fetch-depth.",
        )


if __name__ == "__main__":
    unittest.main()
