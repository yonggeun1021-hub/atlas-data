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

For a given operation, observation date, and normalized row hash,
`atlas_first_seen_at_utc` is the earliest timestamp found in the existing
append-only Atlas sequence.  This is an Atlas observation bound, not a KOFIA
publication timestamp.  A later revision to the same date changes the row hash
and starts a separate first-seen lineage.

## Full-coverage observation

The manual `full_coverage` mode requests every page for both operations and
requires stable `totalCount`, complete pagination, exact official fields,
unique dates, and non-negative finite numbers.  It records the observed row
count and min/max `basDt` in a separate immutable directory.

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
