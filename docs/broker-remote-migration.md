# Broker Remote Migration: `github.com` → `msft.ghe.com`

The broker repository moved from the public GitHub EMU org to Microsoft GitHub Enterprise (Proxima):

| | Old | New |
|---|---|---|
| **Host** | `github.com` | `msft.ghe.com` |
| **Slug** | `identity-authnz-teams/ad-accounts-for-android` | `security/ad-accounts-for-android` |
| **URL** | https://github.com/identity-authnz-teams/ad-accounts-for-android | https://msft.ghe.com/security/ad-accounts-for-android |

Only the **broker** module moved. `msal`, `common`, and `adal` stay on public `github.com/AzureAD`.

---

## TL;DR — repoint your existing checkout (no re-clone)

Run from `android-complete/broker`:

```powershell
# 1. Authenticate gh against the new host (one-time, opens browser)
gh auth login --hostname msft.ghe.com --git-protocol https --web

# 2. Point the broker remote at the new URL
cd C:\repos\android-complete\broker
git remote set-url origin https://msft.ghe.com/security/ad-accounts-for-android.git

# 3. Verify + refresh
git remote -v
git fetch origin
```

That's it — your branches, stashes, and local commits are untouched. `git pull`/`push` now hit Proxima.

---

## Let Copilot do it for you

If you'd rather not run the steps by hand, use the shared prompt at
[`.github/prompts/broker-ghe-migrate.prompt.md`](../.github/prompts/broker-ghe-migrate.prompt.md):
in VS Code Copilot Chat type **`/broker-ghe-migrate`** (or open the file and copy-paste its
body). It authenticates to `msft.ghe.com`, repoints the broker `git remote`, then pulls
latest on `android-complete` / `broker` / `common` (which already carry the migrated
pipeline YAML and `gh`/`curl` references), and finally fixes your per-developer
`developer-local.json`. It does **not** hand-edit tracked files or touch historical
`changes.txt`/PR links, and stops for your review before committing or pushing.

---

## One-time prerequisites

1. **Access.** Request the **`security/ad-accounts-for-android`** access package in My Access: https://myaccess.microsoft.com/@msesmecloud.onmicrosoft.com#/access-packages/0cf20674-39ff-406b-a447-e31b1923f985 — approval grants access to the repo on `msft.ghe.com`. Once granted, confirm you can open https://msft.ghe.com/security/ad-accounts-for-android in a browser before continuing.
2. **gh CLI ≥ 2.x.** Check with `gh --version`. The EMU account switch trick is gone — `gh` now routes by **host**, so you stay logged into both `github.com` and `msft.ghe.com` at the same time.
3. **Git credential helper.** If you use the Git Credential Manager, the first `git fetch` after the remote change will prompt you to authenticate to `msft.ghe.com` — complete that once and it's cached.

Verify both hosts are authenticated:

```powershell
gh auth status --hostname github.com
gh auth status --hostname msft.ghe.com
```

---

## Fresh setup (new machine / new clone)

The `git droidSetup` alias in `.gitconfig` already points at the new URL, so a fresh `droidSetup` just works **once you're logged into `msft.ghe.com`**. If you clone broker manually:

```powershell
git clone -b dev https://msft.ghe.com/security/ad-accounts-for-android.git broker
```

---

## Working with the broker repo day-to-day

Because broker lives on a different host, add `--hostname msft.ghe.com` to `gh` commands that talk to it (PRs, API calls, releases):

```powershell
# List / view PRs on broker
gh pr list  --repo msft.ghe.com/security/ad-accounts-for-android
gh pr view <n> --repo msft.ghe.com/security/ad-accounts-for-android

# Raw API calls use the GHE API base
gh api --hostname msft.ghe.com repos/security/ad-accounts-for-android/pulls/<n>
```

For `msal`/`common`/`adal` keep using plain `github.com` (no `--hostname` needed).

---

## Azure DevOps pipelines (already updated in this repo)

The pipeline YAML now references the new location. **A pipeline admin must create the service connection once**, then everything resolves:

- **New service connection name:** `MSFT_GHE_SECURITY` (GitHub Enterprise Server type, pointing at `https://msft.ghe.com`, org `security`).
  - Replaces the old `github-inside-microsoft` GitHub connection for broker.
- **Repository resources** changed from:
  ```yaml
  - repository: broker
    type: github
    name: identity-authnz-teams/ad-accounts-for-android
    endpoint: github-inside-microsoft   # or ANDROID_GITHUB
  ```
  to:
  ```yaml
  - repository: broker
    type: githubenterprise
    name: security/ad-accounts-for-android
    endpoint: MSFT_GHE_SECURITY
  ```
- **`GitHubRelease@1` tasks** and release-note existence checks now use `gitHubConnection: 'MSFT_GHE_SECURITY'` and the `https://msft.ghe.com/api/v3/...` REST base.

> ⚠️ Until the `MSFT_GHE_SECURITY` service connection exists and is authorized for the pipelines, broker-dependent pipelines will fail at checkout. Coordinate the connection creation with the pipeline change rollout.

---

## What was changed in the codebase (for reviewers)

| Area | Files |
|---|---|
| Clone alias | `.gitconfig` (`droidSetup`) |
| AI-orchestrator docs/skills | `.github/copilot-instructions.md`, `.github/agents/*`, `.github/prompts/*`, `.github/skills/{pbi-dispatcher,feature-planner,copilot-review-analyst,oncall-weekly-telemetry-report,release-monitoring-report}/**`, `AI Driven Development Guide.md`, `scripts/setup-ai-orchestrator.ps1` |
| Legacy pipelines | `azure-pipelines/**` (broker repo resources → `githubenterprise` + `MSFT_GHE_SECURITY`) |
| Broker repo | `azure-pipelines/pull-request-validation/*`, `.github/workflows/validate-pr-ab-id.yml` (host-agnostic API URL), gradle POM URLs, doc links |
| 1ES pipelines | `AuthClientAndroidPipelines/**` (resources, release-note publish steps, docs) |
| Account routing | The `identity-authnz-teams` account key is now `msft.ghe.com` in `developer-local.json`; scripts authenticate per-host instead of `gh auth switch`-ing to an EMU account |

Historical changelog entries (`changes.txt`) were intentionally **not** rewritten — their links are historical records.

### `.github/developer-local.json` (per-developer, gitignored)

Update the key from the old org to the new host:

```json
{
  "github_accounts": {
    "AzureAD": "<your_public_username>",
    "msft.ghe.com": "<your_ghe_username>"
  }
}
```

---

## Rollback

If you need to point back during the grace period:

```powershell
git remote set-url origin https://github.com/identity-authnz-teams/ad-accounts-for-android.git
```
