---
name: pbi-dispatcher
description: >
  Dispatch Azure DevOps PBIs to GitHub Copilot coding agent for autonomous implementation.
  Use this skill when PBIs have been created (by the feature-planner or manually) and you
  want to send them to Copilot coding agent to generate PRs. Triggers include "dispatch PBIs
  to agent", "assign to Copilot", "send work items to coding agent", "kick off agent
  implementation", or any request to have Copilot coding agent pick up and implement
  Azure DevOps work items.
---

# PBI Dispatcher

Dispatch Azure DevOps PBIs to GitHub Copilot coding agent by creating GitHub Issues
and assigning them to `copilot-swe-agent[bot]`.

## Prerequisites

- **ADO MCP Server** must be running (for reading PBI details)
- **GitHub CLI** (`gh`) must be authenticated with access to target repos
- PBIs must exist in ADO with tag `copilot-agent-ready`
- Target repos must have Copilot coding agent enabled

## Dispatch Workflow

### Step 1: Identify PBIs to Dispatch

Query ADO for PBIs ready for dispatch. Use the ADO MCP Server to:
1. List work items tagged `copilot-agent-ready` in `IdentityDivision/Engineering`
2. Filter by iteration/sprint if specified
3. Sort by dependency order (PBIs with no dependencies first)

### Step 2: Parse Target Repository

Extract the target repository from the PBI description. The PBI template includes a
"Target Repository" section with the format:
- `AzureAD/microsoft-authentication-library-common-for-android` → common
- `AzureAD/microsoft-authentication-library-for-android` → msal
- `identity-authnz-teams/ad-accounts-for-android` → broker
- `AzureAD/azure-activedirectory-library-for-android` → adal

### Step 3: Check Dependencies

Before dispatching a PBI:
1. Check if it has dependencies listed (other AB# IDs)
2. If dependencies exist, verify their PRs have been merged
3. If dependencies are not yet merged, skip and flag for later dispatch

### Step 4: Create GitHub Issue

For each ready PBI, create a GitHub Issue in the target repo:

**Issue Title**: Same as PBI title

**Issue Body** (template):
```markdown
## Auto-dispatched from Azure DevOps

**Work Item**: Fixes AB#<PBI_ID>
**Priority**: <priority>
**Iteration**: <iteration>

---

<Full PBI description from ADO, converted to Markdown>

---

> This issue was auto-created from ADO PBI AB#<PBI_ID> for Copilot coding agent dispatch.
> Do not modify this issue directly — it is managed by the dispatch pipeline.
```

### Step 5: Assign to Copilot Coding Agent

After creating the issue, assign it to Copilot coding agent.

**Method A — GitHub CLI** (recommended for interactive use):
```bash
# Create the issue and assign to Copilot in one step
gh issue create \
  --repo "OWNER/REPO" \
  --title "PBI Title" \
  --body "Issue body with Fixes AB#12345" \
  --assignee "copilot-swe-agent[bot]"
```

**Method B — GitHub REST API** (recommended for scripted automation):
```bash
# Step 1: Create issue
gh api /repos/OWNER/REPO/issues \
  --method POST \
  -f title="PBI Title" \
  -f body="Issue body" \
  | jq '.number'

# Step 2: Assign to Copilot
gh api /repos/OWNER/REPO/issues/ISSUE_NUMBER/assignees \
  --method POST \
  --input - <<< '{
    "assignees": ["copilot-swe-agent[bot]"],
    "agent_assignment": {
      "target_repo": "OWNER/REPO",
      "base_branch": "dev",
      "custom_instructions": "Follow the repo copilot-instructions.md. PR title must include Fixes AB#<ID>."
    }
  }'
```

### Step 6: Update ADO Work Item

After dispatching, update the ADO work item:
- State: `Active` (or `In Progress`)
- Add tag: `agent-dispatched`
- Add comment: `Dispatched to Copilot coding agent via GitHub Issue #<number> in <repo>`

### Step 7: Report Dispatch Summary

```markdown
## Dispatch Summary

| AB# | Title | Repo | GitHub Issue | Agent Status |
|-----|-------|------|-------------|--------------|
| AB#12345 | [title] | common | #42 | Dispatched |
| AB#12346 | [title] | broker | — | Blocked (depends on AB#12345) |
| AB#12347 | [title] | msal | #18 | Dispatched |

### Next Steps
- Monitor agent sessions at https://github.com/copilot/agents
- After AB#12345 PR merges, re-run dispatch for blocked PBIs
- Review generated PRs and use `@copilot` for feedback
```

## Batch Dispatch (Overnight Mode)

For the "developer leaves, everything is done by morning" workflow:

1. Run the orchestration script (`scripts/agent-pipeline/orchestrate.py`)
2. It dispatches all independent PBIs in parallel
3. Set up a cron job or scheduled GitHub Action to re-check dependencies every 2 hours
4. When a dependency PR merges, the next wave of PBIs is dispatched automatically

## Review Feedback Loop

After agent PRs are created, reviewers can iterate:
1. Leave review comments on the PR
2. Mention `@copilot` with specific feedback
3. Copilot coding agent picks up the feedback and pushes fixes
4. Repeat until the PR is approved

Example feedback comment:
```
@copilot This method should use the Logger class instead of android.util.Log.
Also please add unit tests for the error case when the IPC call throws DeadObjectException.
```
