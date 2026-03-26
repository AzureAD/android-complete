---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Dispatch work items to GitHub Copilot coding agent for implementation"
---

# Dispatch Phase

You are in the **Dispatch** phase. Send work items to Copilot coding agent.

**First**: Read `.github/orchestrator-config.json` for repo slugs, base branches, and account types.

Use the `pbi-dispatcher` skill to:
1. Discover GitHub accounts (from developer config file or `gh auth status`)
2. Read work item details from ADO
3. Check dependencies — skip blocked items
4. For each ready item:
   - Switch to correct `gh` account (based on repo's `accountType` from config)
   - Dispatch via `gh agent-task create` with the full PBI description as prompt
   - Include `Fixes AB#ID` in the prompt
5. Update ADO state (Active + agent-dispatched tag)
6. Update orchestrator state:
   ```powershell
   $su = Join-Path $HOME ".feature-orchestrator" "state-utils.js"
   node $su set-step "<feature>" monitoring
   node $su add-agent-pr "<feature>" '{"repo":"...","prNumber":N,"prUrl":"...","status":"open"}'
   ```

**Pipeline**: ✅ Design → ✅ Plan → ✅ Backlog → 🚀 **Dispatch** → ○ Monitor
