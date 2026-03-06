---
agent: feature-orchestrator
description: "Resume working on a feature from its current pipeline step"
---

# Continue Feature

## Your Task

Resume working on a feature. The user will provide the feature name below.

**Step 1**: Read the feature state:
```powershell
node .github/hooks/state-utils.js get-feature "<feature name>"
```

**Step 2**: Determine the current step from the `step` field and resume from there:

| Step | What to do |
|------|-----------|
| `designing` | Continue writing the design spec |
| `design_review` | Design is done — ask user if they want to plan PBIs. Use `askQuestion`. |
| `planning` | Continue planning PBIs |
| `plan_review` | Plan is done — ask user if they want to backlog in ADO. Use `askQuestion`. |
| `backlogging` | Continue creating PBIs in ADO |
| `backlog_review` | PBIs created — ask user if they want to dispatch. Use `askQuestion`. |
| `dispatching` | Continue dispatching |
| `monitoring` | Check PR status (follow Monitor phase instructions) |

**Step 3**: Show the pipeline progress header:
```
## 🚀 Feature Orchestration: [Phase Name]

**Feature**: [feature name]
**Pipeline**: [show ✅/📋/○ for each stage based on current step]
```

Read `.github/copilot-instructions.md` for project context.

**IMPORTANT**: Use single quotes for JSON args in PowerShell.
Always update state after completing a phase step.
