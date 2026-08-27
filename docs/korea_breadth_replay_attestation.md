# Korea Breadth Replay Attestation v1

P8-04 keeps KRX response bodies and per-symbol rows only in the private
`atlas-private-evidence` repository.  The public repository stores a sanitized,
exactly pinned attestation that private workflow run `33089264800` reproduced
the four retained KOSPI/KOSDAQ historical/recent aggregate facts.

## Proven

- private evidence commit `5fbc9283211ffa773f4bcd573020ee5201afd766`
- private manifest SHA-256
  `e2ca51c2a03c7ed1d0eef50746db3673864e756b506ede3b93fca2dc8f0367e9`
- exact public implementation commit
  `8b9e0414ed94d4485085f6f2e0b67f98b9a7c979`
- exact retained public bundle payload
  `352ad44a23d3e1a57ff7305a68ddbdf30c55bf388260d2eb969e44d43e3a6b38`
- four packet links and eight independently fetched raw responses matched
- stable aggregate facts matched for every packet

## Not disclosed

No KRX response body, per-symbol row, response-hash list, or API key is copied
into the public repository.  The public packet exposes only approved lineage,
counts, boolean match results, and false authority fields.

## Still blocked

This closes only `RAW_SOURCE_NOT_REPLAYABLE`.  It changes the KR/BREADTH
deferred reason to `SOURCE_REPLAY_PROVEN_SCORING_POLICY_UNRATIFIED` while
keeping the axis `UNDEFINED` and Korea coverage `0/5`.  A separate reviewed and
ratified Breadth scoring policy is required before axis promotion.  Regime,
direction, confidence, strategy, action, order, capital, Production, and
trading authority remain unavailable.
