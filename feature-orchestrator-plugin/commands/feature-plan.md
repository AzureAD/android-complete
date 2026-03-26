---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Decompose an approved design into repo-targeted work items"
---

# Plan Phase

You are in the **Plan** phase. Decompose the approved design into work items.

**First**: Read `.github/orchestrator-config.json` for repository routing and module definitions.

**Step 1**: Read the approved design spec (from the design phase or from the configured docs path).

**Step 2**: Use the `feature-planner` skill to break it into right-sized, self-contained work items:
- One per repo/module (use repo mapping from config)
- Each must be implementable from its description alone — the coding agent has no access to design docs
- Include: objective, context, technical requirements, acceptance criteria, files to modify
- Reference existing code patterns discovered during research

**Step 3**: Present the plan using `askQuestion`:
```
askQuestion({
  question: "Work item plan is ready. What next?",
  options: [
    { label: "✅ Create in ADO", description: "Create work items in Azure DevOps" },
    { label: "✏️ Revise plan", description: "Adjust the breakdown first" }
  ]
})
```

**Pipeline**: ✅ Design → 📋 **Plan** → ○ Backlog → ○ Dispatch → ○ Monitor
