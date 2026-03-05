---
agent: feature-orchestrator
description: "Decompose an approved design into repo-targeted PBIs"
---

# Plan Phase

## Your Task

You are in the **Plan** phase. The design has been approved and you need to break it into PBIs.

**Step 1**: Read the feature state to find the design doc:
```powershell
node .github/hooks/state-utils.js get-feature "<feature name>"
```

**Step 2**: Read the design spec from `design-docs/` (the path is in the feature state).

**Step 3**: Follow the **Planning Phase** instructions from the orchestrator agent:
1. Pass BOTH research findings AND the design spec to the `feature-planner` subagent
2. Present the structured plan with Summary Table + PBI Details
3. Use `askQuestion` to gate the next stage

**Pipeline**: ✅ Design → 📋 **Plan** → ○ Backlog → ○ Dispatch → ○ Monitor

After planning is complete, update state:
```powershell
node .github/hooks/state-utils.js set-step "<feature name>" plan_review
```

**IMPORTANT**: Do NOT create ADO work items in this phase. Only produce the structured plan.
Use single quotes for JSON args in PowerShell.
