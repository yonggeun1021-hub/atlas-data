# KOFIA Append-Only First-Seen Contract (P1-KR-03)

Status: free API capture approved and service key registered; first-seen
evidence collecting; source release timing, durable historical range, API unit,
Regime, Production, and trading authority remain unverified or closed.

## Why this layer exists

The official catalog documents `basDt` and the two selected operations, but its
time range is blank and it does not publish when a row first becomes available.
Atlas therefore separates three facts:

1. `basDt`: the date represented by the row;
2. `captured_at_utc`: when Atlas successfully observed the response;
3. `available_at`: when the source officially made the row available.

Only the first two are currently known.  `available_at` remains null.

Primary sources:

- https://www.data.go.kr/data/15094809/openapi.do
- https://www.data.go.kr/catalog/15094809/openapi.json
- https://www.kofia.or.kr/voc/m_113/view.do?answer_seq=0&page=1&srchTp=&srchWord=&voc_id=3009

## Scheduled first-seen probes

At 06:30, 09:30, 13:30, 17:30, and 21:30 KST, the workflow queries both
operations for each of the most recent eight calendar dates using the official
exact `basDt` filter.  Weekend and holiday gaps are preserved as missing query
dates; they are never filled with zero.

Each idempotent GET uses a 20-second connection timeout and at most three
attempts with 1-second and 3-second backoff after network errors. HTTP errors,
authentication failures, response-contract failures, and invalid values are
not retried. If every network attempt fails, the entire staged run is discarded
and no partial evidence is committed.

Every run has a unique immutable directory:

```text
evidence/kofia/first_seen/{KST_DATE}/run-{RUN_ID}-attempt-{ATTEMPT}/
  _captured_at.txt
  _manifest.json
  _observation.json
  raw/{operation}/{YYYYMMDD}.json.gz
```

The service key is read only from `DATA_GO_KR_SERVICE_KEY`.  It is never placed
in the manifest, observation, output path, console summary, or persisted raw
request metadata.  Response bytes are gzip-compressed without modification and
bound by SHA-256 and byte length.

Numeric source fields accept the official JSON `number` representation and the
canonical unsigned decimal-string representations observed from the live
gateway, including a fraction with an omitted leading zero. The raw response
preserves which representation arrived. Missing,
blank, signed, exponent, comma-grouped, placeholder, or null values fail closed
and are never treated as zero.

For a given operation, observation date, and normalized row hash,
`atlas_first_seen_at_utc` is the earliest timestamp found in the existing
append-only Atlas sequence.  This is an Atlas observation bound, not a KOFIA
publication timestamp.  A later revision to the same date changes the row hash
and starts a separate first-seen lineage.

### History replay boundary

Before every scheduled capture issues a single request to the source, and
again before a staged capture is validated for publication,
`kofia_first_seen.py` replays the entire committed `first_seen` evidence
history in `captured_at_utc` order and rebuilds the `atlas_first_seen_at_utc`
ledger from raw bytes rather than trusting any prior `_observation.json` on
its face. For each committed bundle the replay:

- rejects a symlink anywhere in the bundle;
- requires the exact file set the bundle's own `_manifest.json` declares —
  `_captured_at.txt`, `_manifest.json`, `_observation.json`, and every raw
  gzip entry the manifest lists — with no unexpected or missing files;
- decompresses each raw gzip response and re-verifies its SHA-256, byte
  length, and official response schema against the manifest entry;
- requires `_manifest.json`, `_observation.json`, and `_captured_at.txt` to
  match their canonical serialization byte-for-byte (`sort_keys=True`,
  `indent=2`, trailing newline for the JSON files);
- regenerates `_observation.json` from that bundle's raw responses using only
  the ledger accumulated from earlier, already-verified bundles, and rejects
  the bundle if the regenerated observation does not match the committed one
  exactly.

Only a bundle that survives every check above contributes its
`atlas_first_seen_at_utc` values to the ledger used for later captures. A
tampered, truncated, or semantically altered prior bundle fails the run
closed — via `CaptureError` codes prefixed `PRIOR_EVIDENCE_*` — before any
network request is made, and before a new staging capture can be published.
Empty history (the very first run) is accepted.

This replay validates against the capture and source contracts currently
checked into the repository. `collector_version` and
`source_contract_version` have changed twice in this project's history; if
they change again, bundles captured under a since-superseded contract need
either a compatible replay path or a documented, evidence-based exemption —
silently reusing today's contract against a bundle captured under a
materially different one is out of scope for this replay and must not be
assumed safe.

## Full-coverage observation

The manual `full_coverage` mode requests every page for both operations and
requires stable `totalCount`, complete pagination, exact official fields,
unique dates, and non-negative finite numbers.  It records the observed row
count and min/max `basDt` in a separate immutable directory. Canonical numeric
strings use the same explicit normalization contract as scheduled probes.

An observed complete response answers “what this API returned in this run.” It
does not prove that KOFIA guarantees that range permanently.  Consequently:

- `historical_range_status = unverified`;
- `source_release_time_status = atlas_first_seen_sequence_collecting` for
  scheduled probes and `unverified` for full coverage;
- `available_at = null`;
- `decision_eligible = false`;
- Regime, Production, and trading authorities remain false.

## Live commands

The workflow is the only approved network path.  After merge:

```bash
gh workflow run p1-kr03-kofia-first-seen.yml \
  --repo yonggeun1021-hub/atlas-data \
  --ref main -f mode=first_seen

gh workflow run p1-kr03-kofia-first-seen.yml \
  --repo yonggeun1021-hub/atlas-data \
  --ref main -f mode=full_coverage
```

The WBS Exit Gate remains open until an official answer or a separately
approved policy defines source release timing and the required replay range.
