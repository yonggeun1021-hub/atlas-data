# P4-04 Sandisk Official-Release Summary Observation

## Purpose

This adapter records an exact, reviewable observation from official corporate
release bytes that P4-02 has already retained. The first narrow slice is the
complete ordered `News Summary` block in Sandisk's fiscal-Q4-2026 Exhibit 99.1.
It makes official-release facts available to later evidence work without
allowing this adapter to select a favourable subset or make an investment
judgment.

## Input and population

- Source: committed P4-02 SEC content manifests and gzip payloads under
  `data/sec_content/SNDK/`.
- Registered release: accession `0001628280-26-053346`, form `8-K`, exact
  Exhibit 99.1 document `sndkq4-26ex991xpressrelease.htm`.
- Population: every retained SNDK manifest known by `decision_at` is accounted
  for. The registered release becomes one observation; every other eligible
  filing is recorded as an explicit exclusion.
- Selection rule: the whole ordered `News Summary` block is captured. No bullet
  can be silently omitted or added.

The registration is narrow implementation scope. It is not a ratified source
hierarchy, importance ranking, or claim that this release outranks another
source.

## PIT and provenance contract

- `decision_at` must be an exact UTC timestamp.
- Only manifests whose retained availability is no later than `decision_at`
  may contribute.
- The release publication date must equal the filing date and cannot be later
  than the retained capture date.
- Manifest and raw exhibit bytes are revalidated through the existing P4-02
  manifest validator before extraction.
- The packet binds the exact manifest and exhibit references, hashes,
  accession, source URI, capture time, title offsets, ordered item offsets, and
  a hash of the complete summary list.
- Validation rebuilds the packet independently from retained source bytes;
  recomputing only the packet hash cannot legitimize changed facts or authority.

## Output

`official_release_summary_packet/1` is content-addressed and append-only under
`data/observations/official_release_summary_observations/<evidence-date>/`.
The current retained population yields one observed release, one explicit
non-registered filing, and five ordered summary items.

## Authority boundary

The only true capability is `observation_recording_only`. Fact selection,
source ranking, interpretation, Rule evaluation, Stage change, Action, Order,
Production, and trading authority are all false. The observation carries
`interpretation_status=UNDETERMINED`, `rule_impact=NONE`, no stage change, and
no trade proposal.

## Fail-closed conditions

The adapter rejects missing or duplicate registered documents, title/heading
cardinality changes, summary item-count drift, manifest or raw-byte tamper,
publication/filing/capture time contradictions, packet hash drift, authority
changes, and any self-rehashed packet that cannot be rebuilt byte-for-byte from
the retained evidence.

## Operational wiring and exit gate

The daily collect workflow runs this provider-free adapter after P4-02 content
retention and before the existing SEC event-case population. No new network
request or provider is added. A successful local/CI build proves the contract
and wiring; P4-04 remains in development until a genuine scheduled run commits
the first natural packet and the remaining source-hierarchy work is separately
designed and authorized.
