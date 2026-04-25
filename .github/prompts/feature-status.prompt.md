---
agent: feature-orchestrator
description: "Check the status of agent-created pull requests for a feature"
---

# Monitor Phase

## Your Task

You are in the **Monitor** phase. Check the status of agent PRs for the feature.

**Step 1**: Read the feature state to get tracked PRs:
```powershell
node .github/hooks/state-utils.js get-feature "<feature name>"
```
This returns `artifacts.agentPrs` — an array of `{repo, prNumber, prUrl, status, title}`.
**Only check the PRs listed here — do NOT scan all repos.**

**Step 2**: For each tracked PR, fetch live status:
```powershell
gh auth switch --user <discovered_username>
gh pr view <prNumber> --repo "<full-repo-slug>" --json state,title,url,statusCheckRollup,additions,deletions,changedFiles,isDraft
```

Repo slug mapping:
- `common` → `AzureAD/microsoft-authentication-library-common-for-android`
- `msal` → `AzureAD/microsoft-authentication-library-for-android`
- `broker` → `identity-authnz-teams/ad-accounts-for-android`
- `adal` → `AzureAD/azure-activedirectory-library-for-android`
- `authenticator` → `AzureAD/microsoft-authenticator-for-android`

Discover the GitHub username from `.github/developer-local.json`, or `gh auth status`, or prompt.

**Step 3**: Present results in a table:

```
## 🚀 Feature Orchestration: Monitor

**Pipeline**: ✅ Design → ✅ Plan → ✅ Backlog → ✅ Dispatch → 📡 **Monitor**

| PR | Repo | Title | Status | Checks | +/- |
|---|---|---|---|---|---|
```

**Step 4**: Update state with latest statuses:
```powershell
node .github/hooks/state-utils.js add-agent-pr "<feature name>" '{"repo":"...","prNumber":<n>,"prUrl":"...","status":"<open|merged|closed>","title":"..."}'
```

End with: "Use `@copilot` in PR comments to iterate with the coding agent."
