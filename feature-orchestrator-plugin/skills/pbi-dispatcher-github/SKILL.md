---
name: pbi-dispatcher-github
description: Dispatch work items to GitHub Copilot coding agent for GitHub-hosted repos. Uses `gh agent-task create` to create agent tasks.
---

# PBI Dispatcher — GitHub

Dispatch work items to GitHub Copilot coding agent by creating agent tasks in GitHub-hosted repos.
**This skill is for GitHub repos only.** For ADO repos, use `pbi-dispatcher-ado`.

## Configuration

Read `.github/orchestrator-config.json` for:
- `modules` — module-to-repo mapping (each module has a `repo` key)
- `repositories` — repo details: slug, baseBranch, host
- `github.configFile` — per-developer config path (default: `.github/developer-local.json`)

Read the developer-local config file for GitHub account mapping:
```json
// .github/developer-local.json
{
  "github_accounts": {
    "org/common-repo": "johndoe",
    "enterprise-org/service-repo": "johndoe_microsoft"
  }
}
```

To resolve a module to dispatch details:
1. Look up `modules.<module>.repo` → get the repo key
2. Look up `repositories.<repo>` → get `slug`, `baseBranch`, `host`
3. Look up `developer-local.github_accounts.<slug>` → get the GitHub username
4. Run `gh auth switch --user <username>` before dispatching

## Prerequisites

- **GitHub CLI** (`gh`) authenticated
- Work items in ADO with tag `copilot-agent-ready`
- Copilot coding agent enabled on target repos

## GitHub Account Discovery

**CRITICAL**: Determine which `gh` CLI accounts to use. **Never hardcode usernames.**

### Discovery Sequence (stop at first success)

**Step 0: Verify `gh` CLI is installed:**
```powershell
gh --version
```
If not found, offer to install:
- Windows: `winget install --id GitHub.cli -e`
- macOS: `brew install gh`

**Step 1: Check developer config file** (from `github.configFile` in config):
```powershell
$config = Get-Content "<configFile>" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
```

**Step 2: Discover from `gh auth status`:**
```powershell
$ghStatus = gh auth status 2>&1
```
Map accounts to types:
- Non-EMU account (no `_` suffix) → `public` repos
- EMU account (`_microsoft` suffix) → `emu` repos

**Step 3: Prompt the developer** (fallback):
> "I need your GitHub usernames:
> 1. **Public GitHub** (for public org repos): ___
> 2. **GitHub EMU** (for enterprise repos, if applicable): ___"

Offer to save to the developer config file.

**Step 4: Not signed in at all:**
> "Please run: `gh auth login --hostname github.com`"

## Repo Routing

Use `modules` → `repositories` → `developer-local.json` to resolve dispatch details:

```json
// orchestrator-config.json (committed, shared):
"repositories": {
  "common-repo": { "slug": "org/common-repo", "host": "github", "baseBranch": "main" },
  "service-repo": { "slug": "enterprise-org/service-repo", "host": "github", "baseBranch": "dev" }
},
"modules": {
  "core": { "repo": "common-repo" },
  "service": { "repo": "service-repo" }
}

// developer-local.json (per-developer, gitignored):
"github_accounts": {
  "org/common-repo": "johndoe",
  "enterprise-org/service-repo": "johndoe_microsoft"
}

// Resolution: module "core" → repo "common-repo" → slug "org/common-repo"
//   → gh account "johndoe" (from developer-local) → gh auth switch --user johndoe
```

## Workflow

### 1. Read Work Items

Read PBI details from ADO (via MCP) or from the chat context. Need:
- AB# ID
- Full description (Objective, Technical Requirements, Acceptance Criteria)
- Target repo module

### 2. Check Dependencies

For each work item, check if dependencies (other AB# IDs) have merged PRs. Skip blocked items.

### 2a. Gather Cross-PBI Context for Dependencies

For each work item that HAS dependencies on already-merged PBIs, enrich the dispatch
prompt with context about what those dependencies changed. This is critical — the coding
agent implementing PBI #3 needs to know what PBI #1 introduced.

For each merged dependency:
```powershell
gh pr list --repo "<slug>" --search "AB#<dep-id>" --state merged --json number,title,files --jq '.[0]'
```

If a merged PR is found, add this block to the dispatch prompt:
```
## Dependency Context

This work item depends on already-merged changes:

### AB#<dep-id>: <dep-title> (PR #<number>, merged)
Files changed:
- <file1> (added/modified)
- <file2> (added/modified)

Key APIs introduced: [extract from PR title/files — e.g., new classes, interfaces]
Build on these changes. Do NOT duplicate or re-implement what the dependency PR already added.
```

If the PR can't be found (no AB# match), skip gracefully — the PBI description is still self-contained.

### 3. Switch Account + Dispatch

For each ready work item:

**Switch to correct account** (based on repo's `accountType` from config):
```powershell
gh auth switch --user <discovered_username_for_account_type>
```

**Dispatch via `gh agent-task create`** (preferred, requires gh v2.80+):

Write the full PBI description to a temp file to avoid shell escaping issues:
```powershell
$prompt = @"
<full PBI description — Objective, Context, Technical Requirements,
Acceptance Criteria. Include 'Fixes AB#ID'. Include 'Follow .github/copilot-instructions.md strictly.'>
"@
$prompt | Set-Content -Path "$env:TEMP\pbi-prompt.txt"
gh agent-task create (Get-Content "$env:TEMP\pbi-prompt.txt" -Raw) --repo "<slug>" --base <baseBranch>
```

**IMPORTANT prompt content:**
- Include FULL PBI description (not truncated)
- Include `Fixes AB#<ID>` so PR links to ADO
- Include `Follow .github/copilot-instructions.md strictly`
- Do NOT include local file paths — agent can't access them

**Fallback** (if `gh agent-task create` fails): Create a GitHub Issue and assign to Copilot.

### 4. Update ADO State

Mark the ADO work item as `Active`, add tag `agent-dispatched`.

### 5. Report Summary

```markdown
## Dispatch Summary

| AB# | Repo | Method | Status |
|-----|------|--------|--------|
| AB#12345 | org/common-repo | agent-task | ✅ Dispatched |
| AB#12346 | org/service-repo | agent-task | ✅ Dispatched |
| AB#12347 | org/client-repo | ⏸ Blocked | Waiting on AB#12345 |

### Next Step
> Say **"status"** to check agent PR progress.
> Use `@copilot` in PR comments to iterate with the coding agent.
```

## Review Feedback Loop

After PRs are created, use `@copilot` in PR comments to iterate:
```
@copilot Please add unit tests for the error case.
@copilot Use the Logger class instead of direct logging.
```
