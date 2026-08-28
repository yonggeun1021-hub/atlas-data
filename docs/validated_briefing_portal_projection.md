# Validated briefing → Portal Projection v2

This contract closes the producer side of P-PORTAL/#274 without granting any
investment or execution authority.

## Boundary

The producer accepts four exact files:

1. the final `briefing.md` bytes shown to the validator;
2. `claim_ledger/1` in `READY_FOR_CHATGPT_VALIDATION` state;
3. `briefing_validation_report/1`, which binds the SHA-256 of the briefing,
   claim ledger, and display proposal;
4. `portal_display_proposal/1`, restricted to the Portal v2 allowlist.

It re-reads every source reference from the exact 40-character
`source_commit`. Only `FACT/VERIFIED` claims with retained source references
enter `verified_facts`. `INFERENCE` is never renamed to fact. `UNKNOWN` is
excluded from facts and copied to `unknown_blocked`; the validation report must
say `unknown_escalation=ESCALATE`.

The output is an immutable `portal_projection/2` envelope under
`evidence/validated_briefing_portal/{morning|evening}/{date}/rev-NNN/`. The
whole revision directory is renamed into place atomically and then an atomic
index points to the newest revision. Rebuilding the same canonical validated
content returns `NO_CHANGE`; reusing a projection ID for other bytes is an
error.

## Post-delivery corrections

A post-delivery validation report must be `PASS_WITH_CORRECTION`, name the
`post_delivery_change_key`, include the exact signed ruling as a source
reference, and keep `redelivery=FORBIDDEN`. The producer verifies the ruling
with the existing Finalization Ed25519 public key and the out-of-band
`ATLAS_APPROVAL_PUBKEY_FINGERPRINT` anchor. It only creates a Portal correction
projection; it never calls the human delivery path again.

## Dispatch and receipt ownership

`dispatch-validated-portal-projection.yml` is explicit-caller-only and has no cron. It
can run only from the repository default branch and checks out only that
trusted executor. It never checks out or executes `envelope_commit`; the
envelope, sibling bundle/index, validation artifacts, and exact source refs are
read as data through the GitHub API. Both `envelope_commit` and `source_commit`
must be exact full-SHA ancestors of current `main`, and `source_commit` must be
an actual ancestor of `envelope_commit` (two sibling histories are rejected).
The envelope path is restricted to the immutable producer directory, every
bundle artifact hash is rechecked, and known generation declarations inside
source JSON are independently rebound to the envelope generation. The
envelope is rebuilt from the claim ledger/report/display bytes before any
event is emitted. For a post-delivery correction, the trusted default-branch
dispatcher also repeats the exact-field, source-ref/hash, fingerprint-anchor,
and Ed25519 signature checks; producer acceptance alone is not trusted. Only
then is `portal_projection_validated_v2` emitted to atlas-portal. The envelope's
`source_commit` remains the evidence authority; `envelope_commit` is only the
publication authority for the envelope itself.

atlas-portal exclusively owns `APPLIED | NO_CHANGE | BLOCKED`, the allowlisted
write, Portal deployment verification, and its receipt. The named Codex #274
heartbeat may invoke this workflow only after publishing an explicit semantic
verdict. After independently verifying the target viewer and Notion receipt,
it records the source-side Portal final receipt and invokes finalization
`drain`. This producer never delivers a briefing to a person and never creates
an order.

Required user-managed secret (not needed for local code/tests):
`ATLAS_PORTAL_DISPATCH_TOKEN` in the **atlas-data repository → Environments →
`atlas-portal-dispatch` → Environment secrets**. The environment must restrict
deployment branches to `main`; this keeps a feature-branch workflow from
receiving the token even if someone manually selects that branch. The existing
repository secret `ATLAS_APPROVAL_PUBKEY_FINGERPRINT` is consumed as the
out-of-band anchor; its value is never copied into the committed bundle.

## Example

```bash
python3 .github/scripts/validated_briefing_portal_producer.py build \
  --briefing /path/to/briefing.md \
  --claim-ledger /path/to/claim-ledger.json \
  --validation-report /path/to/validation-report.json \
  --display-proposal /path/to/display-proposal.json
```

The command prints the result, projection ID, immutable envelope path, exact
envelope SHA-256, and evidence source commit. Committing that bundle is a
separate reviewable step; dispatch cannot read an uncommitted envelope.
