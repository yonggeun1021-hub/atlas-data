# P0-05 Read Model Authority Contract

P0-05A gives every generated Step-0, health, and compact artifact one
deterministic `generation_id`. P0-05B defines how a consumer retrieves that
generation without trusting a floating branch URL or a cache-dependent raw
path.

The consumer first resolves `refs/heads/main` through the GitHub Git Data API
with `Cache-Control: no-cache`. It accepts only a full immutable commit SHA.
Every content request then uses the GitHub Contents API with `ref=<that exact
SHA>`, verifies the returned path, decodes the bytes, and verifies the Git
blob SHA-1. Step-0, health, and every requested compact must share the expected
KST date and one generation ID.

`source_commit` therefore belongs to the retrieval envelope, not inside the
artifact committed by that same source commit. Embedding an artifact's final
commit SHA in its own bytes is circular and impossible. The retrieval envelope
is the non-circular authority record that satisfies the intended WBS control.

No prior date, alternate endpoint, branch content fetch, raw-CDN fallback, or
directory listing exists. Failure is explicit and fail-closed. This is a
read-only delivery contract; all investment and trading authorities remain
false.

Example:

```bash
python3 .github/scripts/fetch_briefing_read_model.py \
  --expected-kst-date 2026-08-25 \
  --krx-symbol 005930 --sec-symbol TSM \
  --output-dir /tmp/atlas-read-model-2026-08-25
```
