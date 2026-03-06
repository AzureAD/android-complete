---
name: pbi-dispatcher
description: Dispatch Azure DevOps PBIs to GitHub Copilot coding agent for autonomous implementation. Use this skill when PBIs have been created (by the `pbi-creator` skill or manually) and you want to send them to Copilot coding agent to generate PRs. Triggers include "dispatch PBIs to agent", "assign to Copilot", "send work items to coding agent", "kick off agent implementation", "dispatch these work items", or any request to have Copilot coding agent implement ADO work items.
---

# PBI Dispatcher

Dispatch Azure DevOps PBIs to GitHub Copilot coding agent by creating GitHub Issues in the
target repos and assigning them to `copilot-swe-agent[bot]`.

## Prerequisites

- **ADO MCP Server** running (for reading PBI details)
- **GitHub CLI** (`gh`) authenticated with accounts for target repos
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

### 3. Switch gh Account + Dispatch to Copilot Agent

For each ready PBI:

**Step 1: Switch to the correct gh account** (using the discovered username from above):
```bash
# For AzureAD/* repos (common, msal, adal):
gh auth switch --user <discovered_public_username>

# For identity-authnz-teams/* repos (broker):
gh auth switch --user <discovered_emu_username>
```

**Step 2: Dispatch using `gh agent-task create` (PREFERRED — requires gh v2.80+):**

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

**Step 3 (FALLBACK — if `gh agent-task create` fails):**

Create a GitHub Issue and assign to Copilot:
```bash
gh issue create \
  --repo "OWNER/REPO" \
  --title "[PBI Title]" \
  --body "[Full PBI description with 'Fixes AB#ID']"
```
Then assign via API (extract issue number from the URL output):
```bash
echo '{"assignees":["copilot-swe-agent[bot]"],"agent_assignment":{"target_repo":"OWNER/REPO","base_branch":"dev","custom_instructions":"Follow copilot-instructions.md. PR title must include Fixes AB#ID."}}' | gh api /repos/OWNER/REPO/issues/ISSUE_NUMBER/assignees --method POST --input -
```

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
