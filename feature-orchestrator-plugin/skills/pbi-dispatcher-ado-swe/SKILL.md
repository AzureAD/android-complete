---
name: pbi-dispatcher-ado-swe
description: Dispatch work items to Copilot SWE agent for ADO-hosted repos. Tags the work item with the target repo and assigns to GitHub Copilot, which creates a draft PR automatically.
---

# PBI Dispatcher — ADO (Copilot SWE)

Dispatch work items to the Copilot SWE agent for ADO-hosted repos. The agent is triggered
by tagging the work item with the target repo and assigning it to **GitHub Copilot**.
**This skill is for ADO repos only.** For GitHub repos, use `pbi-dispatcher-github`.

## Configuration

Read `.github/orchestrator-config.json` for:
- `modules` — module-to-repo mapping (each module has a `repo` key)
- `repositories` — repo details: slug (`org/project/repo`), baseBranch, host
- `ado.org` — ADO organization name
- `ado.project` — ADO project name

To resolve a module to dispatch details:
1. Look up `modules.<module>.repo` → get the repo key
2. Look up `repositories.<repo>` → get `slug`, `baseBranch`
3. Parse the slug to extract org, project, and repo name

## Prerequisites

- **ADO MCP Server** running — for updating work items
- Copilot SWE agent enabled/onboarded for the target ADO repository
- Work items with clear descriptions (from the `pbi-creator` skill)

## Workflow

### 1. Read Work Items

Read PBI details from ADO (via MCP) or from chat context. Need:
- AB# ID (work item ID)
- Target repo module
- Full description should already be on the work item (set by `pbi-creator`)

### 2. Check Dependencies

For each work item, check if dependencies (other AB# IDs) have merged PRs. Skip blocked items.

### 3. Tag Work Item with Target Repository

Add a tag to the work item using the format:
```
copilot:repo=<org>/<project>/<repo>@<branch>
```

Use `mcp_ado_wit_update_work_item` to add the tag:

```json
{
  "id": <work-item-id>,
  "fields": [
    {
      "name": "System.Tags",
      "value": "<existing-tags>; copilot:repo=<org>/<project>/<repo>@<branch>"
    }
  ]
}
```

**Building the tag value** from config:
- The repo slug in config is `org/project/repo` format
- The base branch comes from `repositories.<repo>.baseBranch`
- Example: slug `msazure/One/AD-MFA-phonefactor-phoneApp-android`, branch `working`
  → tag: `copilot:repo=msazure/One/AD-MFA-phonefactor-phoneApp-android@working`

**⚠️ IMPORTANT:**
- Use only ONE linking method per work item — the tag OR an artifact link, not both
- Only one repository can be linked per work item
- The branch after `@` is required — use the base branch from config
- **Append** the new tag to existing tags (semicolon-separated), don't overwrite them.
  Read existing tags first via `mcp_ado_wit_get_work_item`, then append.

### 4. Assign to GitHub Copilot

Use `mcp_ado_wit_update_work_item` to assign the work item to **GitHub Copilot**:

```json
{
  "id": <work-item-id>,
  "fields": [
    {
      "name": "System.AssignedTo",
      "value": "GitHub Copilot"
    }
  ]
}
```

**Note**: The display name is `GitHub Copilot`. If this doesn't work, the identity may
be registered differently in the org. Check with the user.

### 5. What Happens Next

After assignment, the Copilot SWE agent will automatically:
1. Create a **draft/WIP PR** in the target repo
2. Add a **comment to the work item** with the PR link
3. Link the PR to the work item
4. Begin implementing the solution from the work item description

The agent uses `.github/copilot-instructions.md` in the target repo for coding conventions.

### 6. Update Orchestrator State

```powershell
$su = Join-Path $HOME ".feature-orchestrator" "state-utils.js"
node $su set-step "<feature>" monitoring
```

Note: The PR URL won't be available immediately — the agent takes a few minutes to create
the draft PR. The user can check status later via the Monitor phase.

### 7. Report Summary

```markdown
## Dispatch Summary

| AB# | Repo | Method | Status |
|-----|------|--------|--------|
| AB#12345 | org/project/repo | Copilot SWE | ✅ Tagged & assigned to GitHub Copilot |
| AB#12346 | org/project/repo | Copilot SWE | ⏸ Blocked (waiting on AB#12345) |

### What to Expect
- The Copilot SWE agent will create a **draft PR** in a few minutes
- It will add a comment on the work item with the PR link
- Once the PR is published, review the changes and add comments to iterate
- Tag `@GitHub Copilot` in PR comments to request changes

### Next Step
> Check back in a few minutes and say **"status"** to see if the PR has been created.
> Or open the work item in ADO to see the agent's comment with the PR link.
```

## Iterating on the PR

After the agent creates the PR:
- Add comments at the PR level or on specific files
- **Tag `@GitHub Copilot`** in PR comments (the agent won't act without the explicit tag)
- If ADO doesn't auto-complete the @-mention, type the literal text `@<GitHub Copilot>`
- The agent will create a new iteration with updates

## Error Handling

### "Repository is not yet onboarded"
The target repo needs to be onboarded to the Copilot SWE pilot program.
Guide the user to follow their org's onboarding process.

### Assignment fails
The `GitHub Copilot` identity may not be available in the org. Check:
- Is Copilot SWE enabled for this ADO organization?
- Is the identity name different? (Try searching for "Copilot" in the assignee field)

### Tag format errors
Ensure the tag follows exactly: `copilot:repo=<org>/<project>/<repo>@<branch>`
- No spaces around `=` or `@`
- Branch name is required
- Org/project/repo must match exactly what's in ADO
