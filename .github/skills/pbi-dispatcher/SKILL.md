---
name: pbi-dispatcher
description: Dispatch Azure DevOps PBIs to GitHub Copilot coding agent for autonomous implementation. Use this skill when PBIs have been created (by the `pbi-creator` skill or manually) and you want to send them to Copilot coding agent to generate PRs. Triggers include "dispatch PBIs to agent", "assign to Copilot", "send work items to coding agent", "kick off agent implementation", "dispatch these work items", or any request to have Copilot coding agent implement ADO work items.
---

# PBI Dispatcher

Dispatch Azure DevOps PBIs to GitHub Copilot coding agent by creating PRs via the
`create_pull_request_with_copilot` GitHub MCP tool (preferred) or `gh agent-task create` CLI.

**IMPORTANT**: Always create PRs, never issues. The goal is to get Copilot coding agent to
generate a pull request with code changes — NOT to create a GitHub Issue.

## Dispatch Method Priority

Try these methods **in order**. Use the first one that succeeds:

1. **`create_pull_request_with_copilot` MCP tool** (PREFERRED — works inside Copilot CLI)
2. **`assign_copilot_to_issue` MCP tool** (if an issue already exists)
3. **`gh agent-task create` CLI** (if MCP tools return 401/403 due to auth limitations)
4. **Generate a script** for the developer to run manually (last resort)

**NEVER create a bare GitHub Issue as the dispatch mechanism.** If the MCP tool fails with
401 (EMU auth can't access public repos), fall back to generating a `gh agent-task create`
script — NOT a `gh issue create` script.

## Prerequisites

- **ADO MCP Server** running (for reading PBI details)
- **GitHub MCP Server** configured (for `create_pull_request_with_copilot`)
- **GitHub CLI** (`gh`) authenticated as fallback
- PBIs in ADO with tag `copilot-agent-ready`
- Copilot coding agent enabled on target repos

## GitHub Account Discovery

**CRITICAL**: Before dispatching, you must determine which `gh` CLI accounts to use.
**Never hardcode GitHub usernames** — they vary per developer.

### Discovery Sequence

Follow these steps **in order**. Stop at the first one that succeeds:

**Step 0: Verify `gh` CLI is installed:**
```powershell
gh --version
```
If this fails (command not found), offer to install it for the developer:
> "GitHub CLI (`gh`) is not installed. Want me to install it for you?"
> 1. **Yes, install it** (recommended)
> 2. **No, I'll install it myself**

If the developer agrees, run the appropriate install command:
- **Windows**: `winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements`
- **macOS**: `brew install gh`

After installation completes, verify with `gh --version`, then prompt authentication:
> "gh CLI installed! Now you need to sign in:
> ```
> gh auth login --hostname github.com
> ```
> Run this, complete the auth flow, then say 'continue'."

**Step 1: Check `.github/developer-local.json`** (fastest — developer already configured):
```powershell
$config = Get-Content ".github/developer-local.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
$publicUser = $config.github_accounts.AzureAD
$emuUser = $config.github_accounts.'identity-authnz-teams'
```

**Step 2: Discover from `gh auth status`** (zero-config if logged in):
```powershell
$ghStatus = gh auth status 2>&1
# Parse output for logged-in accounts on each host
# Look for lines like: "Logged in to github.com account <username>"
```
Map accounts to orgs:
- Non-EMU account (no `_` suffix) → `AzureAD/*` repos
- EMU account (ends with `_microsoft` or similar) → `identity-authnz-teams/*` repos

**Step 3: Prompt the developer** (fallback — save for next time):
If neither Step 1 nor Step 2 yields both accounts, ask:
> "I need your GitHub usernames for dispatching:
> 1. **Public GitHub** (for AzureAD/* repos like common, msal, adal): ___
> 2. **GitHub Enterprise / EMU** (for identity-authnz-teams/* repos like broker): ___"

After receiving the answer, offer to save:
> "Save these to `.github/developer-local.json` so you don't have to enter them again? (Y/n)"

If yes, write the config file:
```json
{
  "github_accounts": {
    "AzureAD": "<public_username>",
    "identity-authnz-teams": "<emu_username>"
  }
}
```

**Step 4: Not signed in at all** — if `gh auth status` shows no accounts:
> "You're not signed in to GitHub CLI. Please run:
> ```
> gh auth login --hostname github.com
> ```
> Then try dispatching again."

Do NOT attempt to proceed without valid accounts — fail fast with clear instructions.

## Repo Routing

| Target in PBI | GitHub Repo | Account Type |
|---------------|-------------|--------------|
| common / common4j | `AzureAD/microsoft-authentication-library-common-for-android` | Public (AzureAD) |
| msal | `AzureAD/microsoft-authentication-library-for-android` | Public (AzureAD) |
| broker / broker4j / AADAuthenticator | `identity-authnz-teams/ad-accounts-for-android` | EMU (identity-authnz-teams) |
| adal | `AzureAD/azure-activedirectory-library-for-android` | Public (AzureAD) |

## Workflow

### 1. Read PBIs from ADO
Use ADO MCP Server tools to list/get work items tagged `copilot-agent-ready`. Read the full
PBI description — it will be needed for the dispatch prompt.

### 2. Check Dependencies
For each PBI, check if its dependencies (other AB# IDs) have merged PRs. Skip blocked PBIs.

### 3. Dispatch to Copilot Agent

For each ready PBI, try these methods **in order**:

#### Method 1: `create_pull_request_with_copilot` MCP Tool (PREFERRED)

This is the most direct method — it creates a PR with Copilot coding agent in one step,
no shell commands needed:

```
create_pull_request_with_copilot(
  owner: "AzureAD",                    // or "identity-authnz-teams" for broker
  repo: "microsoft-authentication-library-common-for-android",
  base_ref: "dev",
  title: "[PBI Title]",
  problem_statement: "<full PBI description including 'Fixes AB#ID'>"
)
```

**If this returns 401/403** (common when GitHub MCP uses EMU auth that can't access public
repos), fall back to Method 2.

#### Method 2: `gh agent-task create` CLI

**Step 1: Switch to the correct gh account** (using the discovered username):
```bash
# For AzureAD/* repos (common, msal, adal):
gh auth switch --user <discovered_public_username>

# For identity-authnz-teams/* repos (broker):
gh auth switch --user <discovered_emu_username>
```

**Step 2: Dispatch:**

Write the full PBI description to a temp file and pipe it as the prompt. This avoids
shell escaping issues and ensures the full context reaches the agent:

```bash
# Write PBI description to temp file
echo "<full PBI description text here>" > /tmp/pbi-prompt.txt

# Create agent task with full PBI content
gh agent-task create "$(cat /tmp/pbi-prompt.txt)" \
  --repo "OWNER/REPO" \
  --base dev
```

On Windows PowerShell:
```powershell
$prompt = @"
<full PBI description text here — include Objective, Context, Technical Requirements,
Acceptance Criteria, etc. from the ADO work item. Include 'Fixes AB#ID' in the prompt.>
"@
$prompt | Set-Content -Path "$env:TEMP\pbi-prompt.txt"
gh agent-task create (Get-Content "$env:TEMP\pbi-prompt.txt" -Raw) --repo "OWNER/REPO" --base dev
```

**IMPORTANT for prompt content:**
- Include the FULL PBI description (Objective, Context, Technical Requirements, Acceptance Criteria)
- Include `Fixes AB#<ID>` so the PR links to the ADO work item
- Include `Follow .github/copilot-instructions.md strictly` as a reminder
- Do NOT include local file paths (design-docs/, etc.) — the agent can't access them
- Do NOT truncate — the full description IS the implementation spec

**If `gh agent-task create` also fails** (not installed, pwsh unavailable), fall back to
Method 3.

#### Method 3: Generate Script for Developer (LAST RESORT)

If neither MCP tools nor `gh` CLI are available from this environment, generate a
PowerShell script file the developer can run in their own terminal. The script MUST use
`gh agent-task create` (NOT `gh issue create`):

```powershell
# Save to a .ps1 file for the developer:
gh auth switch --user <discovered_public_username>
gh agent-task create "<prompt>" --repo "OWNER/REPO" --base dev
```

**NEVER generate a script that uses `gh issue create`.** Issues are not PRs — they don't
trigger Copilot coding agent to write code.

### 4. Update ADO State
Mark the ADO work item as `Active`, add tag `agent-dispatched`.

### 5. Report Summary
Output a dispatch summary table with AB#, repo, dispatch method, and status.

## Batch Dispatch Script

For overnight automation, use [`scripts/agent-pipeline/orchestrate.py`](../../../scripts/agent-pipeline/orchestrate.py).
This script handles dependency ordering, parallel dispatch, and ADO state updates.

## Review Feedback Loop

After PRs are created, use `@copilot` in PR comments to iterate:
```
@copilot Please use the Logger class instead of android.util.Log.
Add unit tests for the error case.
```
