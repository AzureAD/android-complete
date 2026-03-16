---
name: pbi-dispatcher-ado
description: Dispatch work items to ADO Agency for ADO-hosted repos. Uses the Agency REST API to create coding agent jobs that produce draft PRs.
---

# PBI Dispatcher — ADO (Agency)

Dispatch work items to ADO Agency for ADO-hosted repos. Agency generates a solution
as a draft pull request in Azure DevOps.
**This skill is for ADO repos only.** For GitHub repos, use `pbi-dispatcher-github`.

## Configuration

Read `.github/orchestrator-config.json` for:
- `modules` — module-to-repo mapping (each module has a `repo` key)
- `repositories` — repo details: slug (`org/project/repo`), baseBranch, host
- `ado.org` — ADO organization name
- `ado.project` — ADO project name

To resolve a module to dispatch details:
1. Look up `modules.<module>.repo` → get the repo key
2. Look up `repositories.<repo>` → get `slug`, `baseBranch`, `host`
3. Parse the slug to extract org, project, and repo name

## Prerequisites

- **Azure CLI** (`az`) authenticated — needed to acquire the Agency API token
- Work items in ADO with tag `copilot-agent-ready`
- Agency enabled for the target ADO organization/project

## Workflow

### 1. Read Work Items

Read PBI details from ADO (via MCP) or from the chat context. Need:
- AB# ID
- Full description (Objective, Technical Requirements, Acceptance Criteria)
- Target repo module

### 2. Check Dependencies

For each work item, check if dependencies (other AB# IDs) have merged PRs. Skip blocked items.

### 3. Acquire Agency API Token

Use Azure CLI to get a bearer token for the Agency API:

```powershell
$token = az account get-access-token --resource "api://81bbac67-d541-4a6d-a48b-b1c0f9a57888" --query accessToken -o tsv
```

If this fails:
- Check `az account show` — user may not be authenticated
- Guide: `az login`
- If `az` is not installed, tell the user Agency dispatch requires Azure CLI

### 4. Dispatch to Agency

For each ready work item, call the Agency REST API:

```powershell
$body = @{
    organization = "<org-from-config>"
    project = "<project-from-repo-slug>"
    repository = "<repo-name-from-slug>"
    targetBranch = "<baseBranch-from-config>"
    prompt = @"
<Full PBI description — Objective, Context, Technical Requirements,
Acceptance Criteria. Include 'Fixes AB#<ID>'.>
"@
    options = @{
        pullRequest = @{
            create = $true
            publish = $true
        }
    }
} | ConvertTo-Json -Depth 4

$response = Invoke-RestMethod `
    -Uri "https://copilotswe.app.prod.gitops.startclean.microsoft.com/api/agency/jobs" `
    -Method Post `
    -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" } `
    -Body $body

Write-Host "Agency job created: $($response | ConvertTo-Json)"
```

**Parsing the repo slug** for Agency API parameters:
- Slug format is `org/project/repo` (from config)
- `organization` = first segment (e.g., `msazure`)
- `project` = second segment (e.g., `One`)
- `repository` = third segment (e.g., `AD-MFA-phonefactor-phoneApp-android`)

**IMPORTANT for prompt content:**
- Include the FULL PBI description (not truncated)
- Include `Fixes AB#<ID>` so the PR links to the ADO work item
- Do NOT include local file paths — the agent can't access them

### 5. Update ADO State

Mark the ADO work item as `Active`, add tag `agent-dispatched`.

### 6. Report Summary

```markdown
## Dispatch Summary

| AB# | Repo | Method | Status |
|-----|------|--------|--------|
| AB#12345 | org/project/repo | ADO Agency | ✅ Dispatched |
| AB#12346 | org/project/repo | ADO Agency | ⏸ Blocked (waiting on AB#12345) |

### Next Step
> Say **"status"** to check agent PR progress.
> Agency will create a draft PR in ADO when implementation is ready.
```

## Error Handling

### Token acquisition fails
```
az account get-access-token --resource "api://81bbac67-d541-4a6d-a48b-b1c0f9a57888"
```
If this returns an error:
- "AADSTS..." → user may not have access to Agency. They need to request access.
- "Please run 'az login'" → guide the user to authenticate

### Agency API returns 403
The user's account may not have Agency enabled for the target repo/org.
Tell the user to check their Agency access at their org's Agency administration page.

### Agency API returns 400
Check the request body — ensure org, project, and repository match exactly what's in ADO.
The repository name must match the ADO repo name, not a slug or URL.
