# Korea Breadth aggregate retention

This capability prevents the existing P1-KR-05 aggregate observations from
expiring with a GitHub Actions artifact. It does not upgrade the observations
to a Korea Breadth Regime axis.

## Retained evidence

The first retained bundle comes from successful workflow run `33049365069`,
attempt 1, source commit `32c40a781dd7a9294bef23cb77e3f766d5d8bb50`,
artifact `9636936926` (`p1-kr05-derived-outputs-33049365069-1`). The artifact
was created at `2026-08-27T07:21:45Z` and was scheduled to expire at
`2026-11-25T07:21:14Z`. Its observed archive SHA-256 is
`b45b0e842e9bb44e0b9c4c4d9f3a5e6ad15dbb858adc13130f81be8ac8eded82`.

Four exact `korea_breadth_observation/1` files are retained:

- KOSPI and KOSDAQ historical observations for 2010-01-04 → 2010-01-05;
- KOSPI and KOSDAQ recent observations for 2026-08-25 → 2026-08-26.

The manifest binds the workflow run, attempt, source commit, artifact identity,
artifact digest, every exact file byte hash and size, and each packet's own
payload hash. The validator independently checks:

- exact market/scope/date/endpoint identity;
- response-hash and timestamp shapes;
- current/previous/shared/entered/exited count arithmetic;
- paired/advance/decline/unchanged count arithmetic;
- all three twelve-decimal fractions using the producer's half-even rule;
- `classification=UNDEFINED` and every authority flag false;
- packet, file, and manifest hashes.

Publishing is atomic and append-only under
`data/observations/korea_breadth_aggregate/<as_of_date>/run-<id>-attempt-<n>`.
An exact rerun verifies existing bytes. A partial bundle, changed source packet,
self-rehashed arithmetic mutation, or authority mutation fails closed and is
never repaired in place.

Both approved live capture paths now perform this retention from the same
already-uploaded artifact, after rebasing their write job onto the live `main`
tip: standalone `p1-kr05-korea-breadth-live.yml` and dependency-ordered
`p2-03-korea-observation-pair.yml`. No second KRX request or new schedule is
introduced. The upload step's artifact ID and digest, the run attempt, and the
producer commit are passed directly into the manifest; an unapproved workflow
identity is rejected.

## Boundary that remains open

The original public producer deliberately emitted no KRX raw response body and
no per-symbol identity or price row. Aggregate retention by itself therefore
proves only what the producer recorded. A later private, fixed-pair replay
retained eight exact raw responses and reproduced all four source hashes and
stable aggregate facts. The public repository records that result only through
the separately pinned, sanitized `korea_breadth_replay_attestation/1`; no raw
body, per-symbol row, or response-hash list is republished.

That attestation closes the source-replay blocker but does not ratify a
Korea-specific Breadth interpretation. `KR/BREADTH` remains `UNDEFINED` with
reason `SOURCE_REPLAY_PROVEN_SCORING_POLICY_UNRATIFIED`; KR coverage remains
`0/5`, Regime/direction remain `UNKNOWN`, and confidence remains null. Nothing
here grants classification, threshold, Regime, strategy, action, order,
capital, Production, or trading authority.
