# P10-04 Decision change lineage

This offline capability records why, from which evidence, and when a
caller-supplied Decision reference changed. Decision payloads remain opaque and
are bound only by SHA-256 because the repository does not yet have the unified
Decision Contract required by P8-02.

Change types are derived, not trusted from input:

- no prior plus current: `CREATED`;
- equal prior/current Decision SHA: `UNCHANGED`;
- different prior/current Decision SHA: `CHANGED`;
- prior plus no current: `RETIRED`.

Created, changed, and retired entries require at least one canonical reason code
and one evidence record available by the change time. Unchanged entries must
have neither. Multiple claims for one Decision key must form an exact chain:
the next prior snapshot equals the previous current snapshot.

The output never contains Decision content or interpretation and cannot create
or change a Decision, candidate stage, action, or order. Production shadow-ledger
wiring and the unified Decision Contract remain unresolved. The CLI has no
network behavior and writes only outside the repository.
