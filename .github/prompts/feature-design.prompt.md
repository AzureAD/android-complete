---
agent: feature-orchestrator
description: "Start a new feature: research the codebase and write a design spec"
---

# Design Phase

## Your Task

You are in the **Design** phase. The user will describe a feature below.

**Step 0**: Register the feature in state:
```powershell
node .github/hooks/state-utils.js add-feature '{"name": "<short feature name>", "step": "designing"}'
```

Then follow the **Full Flow** instructions from the orchestrator agent:
1. Run the `codebase-researcher` subagent with a detailed prompt
2. Pass the FULL research output to the `design-writer` subagent
3. Present the design summary and use `askQuestion` to offer next steps

**Pipeline**: 📝 **Design** → ○ Plan → ○ Backlog → ○ Dispatch → ○ Monitor

Read `.github/copilot-instructions.md` for project context.

**IMPORTANT**: Use single quotes for JSON args in PowerShell.
After the design is complete, update state:
```powershell
node .github/hooks/state-utils.js set-step "<feature name>" design_review
node .github/hooks/state-utils.js set-design "<feature name>" '{"docPath":"<path>","status":"approved"}'
```
