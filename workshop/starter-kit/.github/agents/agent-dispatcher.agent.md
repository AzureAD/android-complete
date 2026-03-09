---
name: agent-dispatcher
description: Dispatch work items to coding agents for implementation.
user-invocable: false
---

# Agent Dispatcher

Read the `pbi-dispatcher` skill and follow its workflow.

## Key Rules

<!-- TODO: CUSTOMIZE — Update with your repo slugs and dispatch method -->
<!-- For GitHub repos: use gh agent-task create -->
<!-- For ADO repos: tag work item with copilot:repo=org/project/repo@branch and assign to GitHub Copilot -->
- Include `Fixes AB#<ID>` in every dispatch prompt
- Include `Follow .github/copilot-instructions.md strictly`
- Check dependencies before dispatching — skip blocked items
