#!/usr/bin/env python3
"""Closes a verification gap in
``config/theme_taxonomy_source_fact_registry.json`` found 2026-09-18.

That registry pins provenance commits as ``first_seen_commit`` values (one
per ``sources[]`` entry). The only validation anywhere in the repository
was a **format** check --
``rotation/theme_taxonomy_population.py``'s ``COMMIT_RE = re.compile(r"^[0-
9a-f]{40}$")`` -- applied in ``_validate_registry()``. Nothing anywhere
confirmed the pinned commit actually *exists* and is *reachable from HEAD*.
``_source_facts()`` in that same module does run a real
``git merge-base --is-ancestor`` check, but it only ever executes against
whatever ``trusted_commit``/root a caller supplies -- in production, a
manually-invoked audit script; in the existing unit test
(``test/p2_01_theme_taxonomy_population/test_population.py``), a
synthetic, from-scratch temp git repository with its own fabricated
commits, never this repository's own committed history. Nothing in CI ever
asked "are the five commits actually pinned in the real, committed
registry file still reachable from HEAD in the real repository?"

This is not hypothetical. Earlier the same day, the CIO squash-merged PR
#809 (commit ``0ceb4fe75``), which orphaned
``7ef75f76453f2bbb90ecbb79247dc13a2e475aa6`` -- pinned in this registry for
``CRYPTO.KRAKEN.IDENTITY_EXCLUSION`` -- from ``main``'s history for several
hours with nothing complaining. It was repaired only incidentally, because
PR #816 (merge commit ``33e9cb4f1``) happened to land as a merge commit
and carried that commit back in. Had #816 been squashed too, there would
now be two unreachable provenance pins instead of zero.

Design notes:

  - Pin discovery below (``_iter_first_seen_commit_pins``) walks the whole
    parsed registry document rather than reading a hardcoded list of
    commits or a hardcoded ``sources[i]`` index. A hardcoded list goes
    stale the moment a record is added, and would let a *new*,
    unpinned-but-unreachable record slip through unseen. Walking the
    document means a new ``first_seen_commit`` field, wherever it is
    nested, is found automatically.
  - ``test_registry_actually_has_pins_to_check`` fails closed on shape: if
    the registry's shape drifts so the walk finds nothing, or finds fewer
    labels than the known ``sources[]`` list has entries, that is a
    failure -- not a silent zero-pins-checked pass.
  - ``test_full_history_is_actually_available_in_this_checkout`` fails
    loud if this checkout is shallow, rather than letting a truncated
    history make every ancestry check below vacuously pass. See
    ``test/test_workflow_history_checkout_depth.py`` for the class-wide
    ``fetch-depth: 0`` guard this repository already uses for exactly this
    failure mode; this file's own checks live in ``test/`` and are
    therefore this test's own business per that file's module docstring
    (its repo-wide sweep excludes ``test/`` and ``validation/``), but the
    job that runs this file (``actions-pass.yml``'s ``regression`` matrix)
    already sets ``fetch-depth: 0`` unconditionally across all four shards
    -- verified by reading that workflow directly, not assumed.

This test does not modify the registry, rewrite any commit, or dispatch
any workflow. It is read-only: it reads the committed registry file and
runs read-only git queries (``rev-parse``, ``merge-base --is-ancestor``)
against this checkout's own history.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REGISTRY_PATH = ROOT / "config" / "theme_taxonomy_source_fact_registry.json"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

# Manually verified during this task (2026-09-18) -- see the module
# docstring. Referenced only in failure messages, never used to decide
# which commits get checked; discovery is always the generic walk below.
_KNOWN_ORPHANING_INCIDENT_COMMIT = "7ef75f76453f2bbb90ecbb79247dc13a2e475aa6"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _iter_first_seen_commit_pins(node, label_hint="registry", path="registry"):
    """Recursively find every ``first_seen_commit`` field anywhere in the
    parsed registry document, however deeply nested, and yield
    ``(label, path, commit_value)`` for each.

    ``label`` prefers the pin's own ``source_id`` (the identifier a reader
    would actually recognize -- matching how every existing error code in
    ``rotation/theme_taxonomy_population.py`` names a broken pin, e.g.
    ``SOURCE_FIRST_SEEN_NOT_ANCESTOR:{source_id}``); it falls back to the
    nearest enclosing list item's own identity, then to the JSON-ish
    ``path`` string, so a pin is never reported anonymously even if a
    future record has no ``source_id`` field at all.

    Deliberately not restricted to ``registry["sources"]``: a hardcoded
    "look only at the sources list" walk would go stale the instant a pin
    moves elsewhere (e.g. onto a consumer record), and this repository has
    already shown pins living under differently-named sibling fields
    (``evidence_first_seen_commit`` in ``config/data_coverage_registry.json``,
    ``source_first_seen_commit`` in
    ``config/kr_internal_paper_theme_application_contract.json``) -- so this
    walk finds every literal ``first_seen_commit`` key regardless of where
    the record shape puts it, rather than assuming today's shape is final.
    """
    found = []
    if isinstance(node, dict):
        if "first_seen_commit" in node:
            label = node.get("source_id") or label_hint
            found.append((label, path, node["first_seen_commit"]))
        for key, value in node.items():
            found.extend(
                _iter_first_seen_commit_pins(value, label_hint, f"{path}.{key}")
            )
    elif isinstance(node, list):
        for index, item in enumerate(node):
            item_label = item.get("source_id") if isinstance(item, dict) else None
            found.extend(
                _iter_first_seen_commit_pins(
                    item, item_label or label_hint, f"{path}[{index}]"
                )
            )
    return found


class ThemeTaxonomySourceFactRegistryProvenanceTests(unittest.TestCase):
    """Every ``first_seen_commit`` pin in the real, committed registry must
    be an ancestor of real HEAD in THIS repository's real history -- not a
    synthetic fixture repo, and not merely well-formed hex."""

    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        head = _git("rev-parse", "HEAD")
        if head.returncode != 0 or not head.stdout.strip():
            raise AssertionError(
                "could not resolve HEAD via `git rev-parse HEAD` in the "
                f"checkout running this test (cwd={ROOT}); stderr: "
                f"{head.stderr.strip()}"
            )
        cls.head = head.stdout.strip()

    def test_full_history_is_actually_available_in_this_checkout(self):
        # A shallow clone would make every ancestry assertion below
        # vacuously pass against a truncated history it cannot see past --
        # a blind guard verifying nothing. Fail loudly instead of letting
        # that happen silently. `git rev-list --count HEAD` on a full
        # clone of atlas-data was 4625 as of 2026-09-18; any implausibly
        # small count here means history was truncated upstream of this
        # test (missing `fetch-depth: 0`), not that every pin happens to
        # be reachable.
        result = _git("rev-list", "--count", self.head)
        self.assertEqual(result.returncode, 0, result.stderr)
        count = int(result.stdout.strip())
        self.assertGreater(
            count,
            1000,
            f"`git rev-list --count HEAD` returned {count}, which is "
            "implausibly small for a full clone of atlas-data -- this "
            "checkout is almost certainly shallow (missing "
            "`fetch-depth: 0` on its checkout step), which would make "
            "every first_seen_commit ancestry check in this file "
            "meaningless rather than failing loudly on its own.",
        )

    def test_registry_actually_has_pins_to_check(self):
        # Fail closed on shape: zero pins found means either the walker
        # broke or the registry's field name/shape changed underneath it
        # -- either way this must not silently report "nothing to check,
        # all passed".
        pins = _iter_first_seen_commit_pins(self.registry)
        self.assertGreater(
            len(pins),
            0,
            "found no first_seen_commit pins anywhere in "
            f"{REGISTRY_PATH.relative_to(ROOT)} -- either the discovery "
            "walk in this test broke, or the field was renamed/removed "
            "from the registry; this test must not silently pass with "
            "nothing checked.",
        )
        # Cross-check against the registry's own known `sources` list:
        # every declared source_id must be among what the generic walk
        # discovered. This catches a record whose pin the walk somehow
        # missed even though the sources list itself is non-empty and
        # well-formed -- i.e. the walk quietly stopped matching the
        # registry's actual shape.
        declared_source_ids = {
            source.get("source_id")
            for source in self.registry.get("sources", [])
            if isinstance(source, dict)
        }
        discovered_labels = {label for label, _path, _commit in pins}
        missing = declared_source_ids - discovered_labels
        self.assertEqual(
            missing,
            set(),
            f"registry source(s) {sorted(missing)} were not discovered by "
            "the generic first_seen_commit walk even though they appear "
            "in registry['sources'] -- the walk's shape assumption no "
            "longer matches the registry's actual shape; fix the walk "
            "before trusting any PASS from this file.",
        )

    def test_every_first_seen_commit_pin_is_reachable_from_head(self):
        pins = _iter_first_seen_commit_pins(self.registry)
        failures = []
        for label, path, commit in pins:
            if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
                failures.append(
                    f"{label} ({path}) = {commit!r} is not a well-formed "
                    "40-hex commit SHA"
                )
                continue
            verify = _git("rev-parse", "--verify", f"{commit}^{{commit}}")
            if verify.returncode != 0 or verify.stdout.strip() != commit:
                failures.append(
                    f"{label} ({path}): first_seen_commit {commit} does "
                    "not exist as a commit object anywhere in this "
                    f"checkout's history (git rev-parse --verify failed: "
                    f"{verify.stderr.strip()})"
                )
                continue
            ancestor = _git("merge-base", "--is-ancestor", commit, self.head)
            if ancestor.returncode != 0:
                incident_note = (
                    " (exactly the shape of the 2026-09-18 PR #809 "
                    "squash-merge incident that orphaned "
                    f"{_KNOWN_ORPHANING_INCIDENT_COMMIT} for "
                    "CRYPTO.KRAKEN.IDENTITY_EXCLUSION for several hours, "
                    "recovered only incidentally when PR #816 landed as a "
                    "merge commit)"
                    if commit == _KNOWN_ORPHANING_INCIDENT_COMMIT
                    else ""
                )
                failures.append(
                    f"{label} ({path}): first_seen_commit {commit} exists "
                    f"but is NOT an ancestor of HEAD ({self.head}) -- this "
                    f"provenance pin is unreachable from current history"
                    f"{incident_note}."
                )
        self.assertEqual(
            failures,
            [],
            "unreachable/nonexistent first_seen_commit provenance pin(s) "
            f"in {REGISTRY_PATH.relative_to(ROOT)}:\n"
            + "\n".join(f"  - {item}" for item in failures),
        )


if __name__ == "__main__":
    unittest.main()
