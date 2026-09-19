# Offline Crypto PAPER taxonomy compatibility

The approved offline runtime candidate `3cf1661c9ba45c52398b2eaac096cd849a6f7a09`
extends the existing pinned runtime `64d10489f346c0877cce26b6f4b6d718e552b6a8`
with exactly six asset identities that were already adopted on public `main`:
CHIP, NPC, QUID and SN8 from `866d9aac18a7ebdaa2eb06e926698ca7ae5c3e52`,
and DRV and RAY from `9b001f31b0713502b5ba120bf4fe271f678b67f9`.

The candidate changes only `config/crypto_breadth_exclusion_taxonomy.json`.
All previous 154 records, metadata, fail-closed unknown policy, runtime code,
transforms, tests and authority fields remain unchanged. The resulting taxonomy
file is byte-identical to the already-adopted file on `main`, with SHA-256
`26a1308f8d3df5f82af73f21946266ac0e6ee98e90adb2d5264d337d7f62fb38`.

Validation rebuilt the retained 2026-09-09 07:30 UTC Crypto decision with the
candidate and reproduced the complete packet and component registry exactly.
The 2026-08-29 pre-adoption registry remained identical to the original pinned
runtime. Eight sparse approval-chain fixtures were restored byte-for-byte from
the pinned Git commit for this validation; they are not part of the candidate
delta. The original missing-fixture failure and the subsequent fail-closed
diagnostic remain preserved separately.

Independent review passed the exact one-file patch. This integration records
the immutable candidate as an ancestor while retaining current `main` code and
tests byte-for-byte. It does not install or activate a private runtime pin,
provider request, host service, ledger, order, production, real-capital or
trading authority. The private approved runtime pin remains a separate change
after this public integration passes normal CI.
