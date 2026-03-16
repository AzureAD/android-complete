---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Check the status of agent-created pull requests"
---

# Monitor Phase

You are in the **Monitor** phase. Check PR status only.

**Do NOT** ask about creating PBIs, planning, or other phases. Just report status.

**First**: Read `.github/orchestrator-config.json` for repo slug mapping.

1. Set state util path: `$su = Join-Path $HOME ".feature-orchestrator" "state-utils.js"`
2. Read feature state: `node $su get-feature "<feature>"`
2. For each tracked PR in `artifacts.agentPrs`:
   ```powershell
   gh pr view <prNumber> --repo "<slug from config>" --json state,title,url,statusCheckRollup,additions,deletions,changedFiles,isDraft
   ```
3. Present results in a table:

   | PR | Repo | Title | Status | Checks | +/- Lines |
   |----|------|-------|--------|--------|-----------|

4. Update state with latest PR statuses
5. Suggest: "Use `@copilot` in PR comments to iterate with the coding agent."

**Pipeline**: ✅ Design → ✅ Plan → ✅ Backlog → ✅ Dispatch → 📡 **Monitor**
