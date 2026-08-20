# P4-04 Company IR / official release evidence

This capability normalizes two already-approved source paths into
`evidence_envelope/1` without creating a global source hierarchy:

- TSMC Investor Relations historical monthly revenue;
- Microsoft official earnings releases acquired as exact SEC `EX-99.1` exhibits.

The registry is closed. An unknown source ID, host, URL prefix, collector identity, or
release identity fails closed. `source_hierarchy_status` remains `UNRATIFIED`, so a
registered source is not a global ranking decision. Automatic fallback is prohibited.
In particular, the TSMC press-release site remains secondary verification only, and
Microsoft IR Metrics remains a human cross-check rather than an automatic source.

## Evidence boundary

An available envelope requires the registered URL, source SHA-256, retrieval time,
observed availability date, and an approved acquisition kind. TSMC requires a live
official capture; the tracked synthetic/regression fixtures are blocked. Microsoft
may use a tracked slice only when its full-source hash, slice hash, exhibit identity,
and verbatim-substring proof are present.

The observation keeps the company's raw percentage text, exact economic period,
row/column locator, source identity, and audit provenance. It does not compare a
threshold or assign strengthening/weakening meaning. Missing evidence is
`EVIDENCE_UNRESOLVED`; incomplete provenance is `EVIDENCE_BLOCKED`; distinct source
revisions for one subject/measurement/period are blocked rather than selected.

The briefing adapter accepts both the original SEC identity and the registered
official-release identity, preserving the same facts/evaluation/action separation.

## Authority

Evidence only. Source ranking, interpretation, Rule evaluation, Production wiring,
and trading authority are all false. Live scheduled ingestion and operating proof are
separate gates; this implementation does not dispatch a workflow or write `data/`.

## Operational transport boundary

The weekly read-only live workflow follows the acquisition contract already locked in
`config/rules.json`: TSMC's SEC EDGAR 6-K is the automated primary acquisition path,
and the TSMC IR page is a human secondary-verification surface. The SEC probe must
parse the exact consolidated NT$ million table and fails closed without falling back
to IR, prose, a precision table, or an older identified report.

IR reachability is still recorded as a separate artifact. A WAF/HTTP 403 on that
secondary surface is a warning and does not turn a successful SEC primary probe into
a failed workflow. This separation does not ratify a new source hierarchy, bypass a
WAF, publish tracked data, evaluate a Rule, or grant Production/trading authority.
