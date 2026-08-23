# Secret Management Runbook

Cross-cutting procedural notes for handling GitHub Actions repo secrets in
this repo. Not a per-feature contract -- applies to every secret, every
workflow.

## Standing rule (added 2026-08-23): scan for ALL consumers before deleting any shared secret

**Before deleting or rotating any repo secret, run a full recursive scan
of the workflows/collectors that reference it, not just the one you have
in mind.**

This rule exists because of a real incident on 2026-08-23: during the
Portfolio Risk Input Contract's Alpaca-credential cutover to a private
repo (`yonggeun1021-hub/atlas-private-evidence`), `ALPACA_API_KEY`/
`ALPACA_API_SECRET` were deleted from this repo's secrets under the
assumption that they were used only by `.github/workflows/portfolio-risk-input.yml`.
A recursive scan run immediately afterward found they were ALSO used by
`.github/workflows/free-market-data.yml` (`collectors/free_market_data.py`),
an unrelated, pre-existing, already-scheduled market-data collector --
which then lost its credential as unintended collateral damage.

### The check to run, every time

```
grep -rl "secrets.<SECRET_NAME>" .github/workflows/
grep -rn "os.getenv(\"<SECRET_NAME>\"" --include="*.py" .
```

Enumerate every match, confirm you understand what EACH consumer needs the
secret for, and get an explicit decision on each one (keep / migrate /
provision a dedicated replacement) BEFORE deleting anything -- not after.

### Naming discipline this incident also established

When a credential legitimately serves two different purposes for two
different consumers (e.g. one for real account/trading access, one for
public market-data reads), give each consumer its OWN, differently-named
secret and env var -- never let two unrelated consumers share one secret
under a single generic name. A shared name is exactly what made this
incident's collateral damage invisible until a scan was actually run:
`portfolio_risk/`'s account-access use and `collectors/free_market_data.py`'s
market-data use were both silently reading the same `ALPACA_API_KEY`.

The permanent regression for "no workflow in this repo references
the account/trading Alpaca secret names" lives in
`test/test_portfolio_risk_input.py::PublicRepoNeverReceivesRealFinancialData::test_public_repo_has_no_live_capture_workflow_at_all`.
