---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Create work items in Azure DevOps from an approved plan"
---

# Backlog Phase

You are in the **Backlog** phase. Create work items in ADO from the approved plan.

**First**: Read `.github/orchestrator-config.json` for ADO project, org, and work item type.

Use the `pbi-creator` skill to:
1. Parse the approved plan from the previous phase
2. Discover ADO defaults (area path, iteration, assignee) from your recent work items
3. Present ALL settings for confirmation via `askQuestion` — batch into one call
4. Create all work items in dependency order
5. Link dependencies and parent to Feature work item
6. Mark all as Committed
7. Report AB# IDs with dispatch order

**Pipeline**: ✅ Design → ✅ Plan → 📝 **Backlog** → ○ Dispatch → ○ Monitor
