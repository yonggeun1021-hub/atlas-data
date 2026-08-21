# P7-01 explicit-only bucket membership contract

The repository default Portfolio Constitution is still `not_ratified` and its
`B1_bucket_definition` is null. This capability does not invent bucket names,
assignments, limits, or sizing rules. Running it against that default fails
closed with `CONSTITUTION_NOT_RATIFIED`.

Once an external CIO-ratified Constitution and assignment set are supplied, the
validator binds the set to the exact Constitution and opaque B1 SHA-256. Every
candidate or holding must have one explicit effective-dated bucket assignment,
an exact Global Asset identity hash, a Rule-result hash, and the appropriate
Discovery or holding lineage. No symbol, market, theme, score, or Rule result is
used to infer a bucket.

The registry rejects:

- unratified or hash-mismatched Constitution/assignment inputs;
- unknown or duplicate buckets;
- overlapping assignments or no active assignment at the requested date;
- asset identity collisions and identity/market/kind drift across history;
- missing candidate Discovery lineage, holding lineage, or Rule lineage; and
- authority expansion, invalid effective intervals, and input digest drift.

The output preserves complete assignment history and emits exactly one active
membership per subject for the requested date. It authorizes validation only:
automatic assignment, bucket limits, position sizing, orders, Production, and
trading all remain false. The CLI is offline and writes only outside the
repository.

`validate_packet()` revalidates the embedded bucket definitions and assignment
history, recomputes the active memberships and summary for `as_of_date`, and
checks lineage and packet hashes. A self-rehashed membership or summary drift
is rejected. The validator still requires an externally ratified Constitution
and never infers an assignment.
