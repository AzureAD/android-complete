---
description: "Repoint the broker repo to GitHub Enterprise (msft.ghe.com) and update local-only config"
---

# Broker → GitHub Enterprise: local setup

The broker repo moved from `github.com/identity-authnz-teams/ad-accounts-for-android`
to `msft.ghe.com/security/ad-accounts-for-android`.

**Important:** the tracked files (pipeline `repository:` resources, `gh`/`curl`/API
paths, and references) are **already updated** on `dev` in `android-complete`,
`broker`, and `common` — a `git pull` brings them in. Do **NOT** hand-edit those
files. Your job is only the things git can't sync: **auth**, the broker clone's
**remote URL**, and my **per-developer config**. Leave `common` / `msal` / `adal` /
`android-complete` remotes on github.com.

Work in this order and show me a summary before any `git push`.

## Step 1 — Authenticate to the new host (one-time)

GHE auth is needed before broker can fetch:
```powershell
gh auth status --hostname msft.ghe.com
# if not logged in:
gh auth login --hostname msft.ghe.com --git-protocol https --web
```
If auth/fetch later fails with 403/404, I likely need repo access first — request the
`security/ad-accounts-for-android` access package in My Access:
https://myaccess.microsoft.com/@msesmecloud.onmicrosoft.com#/access-packages/0cf20674-39ff-406b-a447-e31b1923f985

## Step 2 — Repoint the broker git remote (local clone config, not in source)

```powershell
git -C broker remote -v   # check current origin
# if origin still points at github.com/identity-authnz-teams/ad-accounts-for-android:
git -C broker remote set-url origin https://msft.ghe.com/security/ad-accounts-for-android.git
```
Leave `common` / `msal` / `adal` / `android-complete` remotes unchanged.

## Step 3 — Pull latest (this is what updates all tracked references/YAML)

```powershell
git -C . pull          # android-complete
git -C broker pull     # now resolves via msft.ghe.com
git -C common pull
```
After this the migrated pipeline resources and `gh`/`curl` paths are present locally.
Do NOT edit those files by hand — they came from the pull.

## Step 4 — Update `.github/developer-local.json` (per-developer, gitignored)

Change the account key from the old org to the new host, using MY GHE username
(from `gh auth status --hostname msft.ghe.com`):
```json
{ "github_accounts": { "AzureAD": "<github_user>", "msft.ghe.com": "<ghe_user>" } }
```

## Step 5 — Validate & report

1. `git -C broker remote -v` shows `msft.ghe.com/security/ad-accounts-for-android`.
2. `git -C broker fetch origin dev` succeeds.
3. `.github/developer-local.json` has the `msft.ghe.com` key (no `identity-authnz-teams`).
4. Print a short table of what changed (remote URL, `developer-local.json` only).
   Do NOT modify tracked pipeline/script files, historical `changes.txt`, or PR/commit links.
