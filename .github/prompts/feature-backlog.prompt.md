---
agent: feature-orchestrator
description: "Create approved PBIs as work items in Azure DevOps"
---

# Backlog Phase

Read the PBI creator skill from #file:.github/skills/pbi-creator/SKILL.md

## Your Task

You are in the **Backlog** phase. The plan has been approved and you need to create PBIs in ADO.

**Step 1**: Read the feature state:
```powershell
node .github/hooks/state-utils.js get-feature "<feature name>"
```

**Step 2**: Follow the **Creation Phase** instructions from the orchestrator agent:
1. Pass the FULL plan to the `pbi-creator` subagent
2. The pbi-creator will discover ADO defaults, present options via `askQuestion`, and create work items
3. Present the creation summary with AB# IDs
4. Use `askQuestion` to gate the next stage

**Pipeline**: ✅ Design → ✅ Plan → 📝 **Backlog** → ○ Dispatch → ○ Monitor

After PBIs are created, update state for EACH PBI:
```powershell
node .github/hooks/state-utils.js set-step "<feature name>" backlog_review
node .github/hooks/state-utils.js add-pbi "<feature name>" '{"adoId":<id>,"title":"...","module":"...","status":"Committed","dependsOn":[<dep-ids>]}'
```

**IMPORTANT**: Use single quotes for JSON args in PowerShell.
