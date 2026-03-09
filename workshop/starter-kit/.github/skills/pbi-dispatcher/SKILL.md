---
name: pbi-dispatcher
description: Dispatch work items to coding agents for implementation.
---

# PBI Dispatcher

Dispatch work items to coding agents.

## For GitHub-hosted repos

Use `gh agent-task create`:

```powershell
$prompt = "<full PBI description with 'Fixes AB#ID'>"
$prompt | Set-Content -Path "$env:TEMP\pbi-prompt.txt"
gh agent-task create (Get-Content "$env:TEMP\pbi-prompt.txt" -Raw) --repo "OWNER/REPO" --base dev
```

<!-- TODO: CUSTOMIZE — Update repo slugs and base branches -->

## For ADO-hosted repos

Tag the work item and assign to GitHub Copilot:

1. Add tag: `copilot:repo=<org>/<project>/<repo>@<branch>`
2. Assign to `GitHub Copilot`

Use `mcp_ado_wit_update_work_item` for both operations.

<!-- TODO: CUSTOMIZE — Update org/project/repo values -->

## Key Rules

- Include FULL PBI description in the prompt (not truncated)
- Include `Fixes AB#<ID>` so PR links to ADO
- Check dependencies before dispatching — skip blocked items
- Update ADO state after dispatching (Active + agent-dispatched tag)
