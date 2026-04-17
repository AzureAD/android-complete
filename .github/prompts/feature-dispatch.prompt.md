---
agent: feature-orchestrator
description: "Dispatch PBIs to GitHub Copilot coding agent for implementation"
---

# Dispatch Phase

Read the dispatcher skill from #file:.github/skills/pbi-dispatcher/SKILL.md

## Your Task

You are in the **Dispatch** phase. PBIs have been created in ADO and you need to dispatch them.

**Step 1**: Read the feature state to get PBI details:
```powershell
node .github/hooks/state-utils.js get-feature "<feature name>"
```

**Step 2**: Follow the **Dispatch Phase** instructions from the orchestrator agent:
1. Run the `agent-dispatcher` subagent to dispatch PBIs to Copilot coding agent
2. Record each dispatched PR in state
3. Use `askQuestion` to gate the next stage

**Pipeline**: ✅ Design → ✅ Plan → ✅ Backlog → 🚀 **Dispatch** → ○ Monitor

After dispatch, update state:
```powershell
node .github/hooks/state-utils.js set-step "<feature name>" monitoring
node .github/hooks/state-utils.js add-agent-pr "<feature name>" '{"repo":"<label>","prNumber":<n>,"prUrl":"<url>","status":"open","title":"<title>"}'
```

**IMPORTANT**: Use single quotes for JSON args in PowerShell.
