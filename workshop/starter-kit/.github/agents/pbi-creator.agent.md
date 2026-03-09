---
name: pbi-creator
description: Create Azure DevOps work items from an approved feature plan.
user-invocable: false
---

# PBI Creator

Read the `pbi-creator` skill and follow its workflow.

## Key Rules

<!-- TODO: CUSTOMIZE — Update ADO project name -->
- **⛔ MANDATORY**: Ask ALL 4 questions (area path, iteration, assignee, parent) BEFORE creating
- **Sanitize titles** — remove colons (`:`) — they break the ADO API
- **NEVER create work items with minimal descriptions** — always include FULL description
- Convert markdown to HTML for ADO
- Link dependencies and mark as Committed
