# P3-12 first-party identity evidence capture contract

Status: **evidence acquisition only; every authority remains false**.

The P3-12 governance audit froze the prior Upbit identity registry because
plain URL strings did not prove source type, authority domain, exact content,
or observation time. This capture closes only that technical evidence gap for
the eight previously named Crypto PAPER markets: BTC, ETH, LINK, SHIB, SOL,
SUI, WLD, and XRP.

Each append-only snapshot binds:

- `source_type` and `validated_authority_domain`;
- requested and effective HTTPS URLs, with a fail-closed redirect allowlist;
- the exact uncompressed response `content_sha256` and deterministic gzip;
- Atlas `observed_at` and `available_at` capture timestamps;
- `source_published_at: null`, so capture time is never misrepresented as a
  publisher-issued release time;
- at least two contract-reviewed identity markers in the captured bytes;
- exact hashes of both the capture contract and governance-freeze file.

The workflow is manual (`workflow_dispatch`) because identity evidence changes
slowly and should be reviewed. It has no schedule, secret, authenticated API,
or exchange write path. A successful capture does **not** unfreeze P3-12. A
separate reviewed change must bind the evidence to a corrected registry and
taxonomy, pass focused/related/full CI, publish their exact hashes, and receive
explicit CIO approval naming those hashes.

Offline verification:

```bash
python3 test/test_upbit_first_party_identity_capture.py
python3 .github/scripts/upbit_first_party_identity_capture.py \
  --snapshot-root /tmp/atlas-upbit-first-party
```
