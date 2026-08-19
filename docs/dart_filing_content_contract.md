# P4-03 DART filing content evidence

This capability closes the gap between an OpenDART filing title and the filing body.
It is an Evidence-layer acquisition boundary, not an investment interpretation or Rule
evaluator.

## Source and identity

The only provider endpoint is the official OpenDART original-document API:
`https://opendart.fss.or.kr/api/document.xml`. A request is identified by the exact
14-digit `rcept_no`; the credential is never written to URLs in evidence, logs, or
manifests. The canonical source record contains the credential-free endpoint,
receipt number, ZIP SHA-256, ZIP byte size, and every member's name, SHA-256, byte
size, and, for textual members, normalized-text fingerprint.

## Capture boundary

- Candidate, Ready, Buy, and Holding relevant filings are required captures.
- Discovery is best-effort. A missing or unassigned Stage is index-only.
- The upstream relevant-title set remains the existing DART metadata contract.
- The complete ZIP is one content unit. No member is selected as a guessed primary.
- ZIP response, member count, individual size, and total expanded size are bounded.
- Nested paths, duplicate names, encrypted members, path traversal, corrupt CRC, an
  error XML response, and any silent truncation fail closed.
- A prior successful receipt is skipped without another provider request. A changed
  source hash is never allowed to overwrite the canonical receipt directory.

Raw source ZIP and deterministic gzip member cache are retained permanently for
Ready, Buy, and Holding and for required material captures. Best-effort raw cache may
be deleted after 90 days; the canonical hash/URL/index remains permanent.

## Extraction and authority boundary

The WBS dependency `item extraction policy` is not ratified. Therefore version 1
records `content_status=OK` after complete acquisition but leaves
`evidence_status=PENDING` with `ITEM_EXTRACTION_POLICY_UNRATIFIED`. It does not infer
numbers, meaning, direction, Rule impact, Stage, Production state, or a trading action.

Daily Collect runs the helper independently from metadata collection so Guard=fresh
can repair content-only failures. Run truth is published to
`data/latest_dart_content.json`; immutable receipt directories are under
`data/dart_content/<SYMBOL>/<RCEPT_NO>/`; the briefing read model overlays the status
under `data/briefing/dart/<SYMBOL>.json`.
