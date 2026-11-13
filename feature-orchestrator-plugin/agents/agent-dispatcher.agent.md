---
name: agent-dispatcher
description: Dispatch work items to coding agents. Routes to GitHub Copilot agent or ADO Copilot SWE based on repo host.
user-invocable: false
---

# Agent Dispatcher

You dispatch work items to coding agents for autonomous implementation.

## Routing — Choose the Right Dispatcher

Read `.github/orchestrator-config.json` to determine each repo's `host` field:

- **If host is `github`** → Use the `pbi-dispatcher-github` skill
  (dispatches via `gh agent-task create`)
- **If host is `ado`** → Use the `pbi-dispatcher-ado-swe` skill
  (tags the work item with the target repo and assigns to GitHub Copilot in ADO)

Look up the module → repo → host chain:
1. `modules.<module>.repo` → get the repo key
2. `repositories.<repo>.host` → `"github"` or `"ado"`
3. Use the corresponding skill

## Key Rules

For Android-complete's dedicated pre-mortem PBI, apply the
[authoring/review contract](../skills/feature-planner/references/pbi-template.md#android-complete-feature-pre-mortem-pbi).
Dispatch document authoring only after all feature implementation PBIs are complete
across repos with acceptance/merged-change/validation evidence. Inline the brief,
implemented source pins, destination, content contract and human reviewer. The
agent writes/links the full document, then the human reviews/accepts it before
release; no application-code edits, live KQL, activation or autonomous acceptance.
Human-review-only follow-ups must not be dispatched.

- Read `.github/orchestrator-config.json` for repo slugs, base branches, and host types
- **Route to the correct dispatcher** based on repo host — never use GitHub dispatch for ADO repos or vice versa
- Include `Fixes AB#<ID>` in every prompt so the PR links to ADO
- Include `Follow .github/copilot-instructions.md strictly` as a reminder
- Do NOT include local file paths in prompts — the agent can't access them
- Check dependencies before dispatching — skip blocked items
- Update ADO state after dispatching (Active + agent-dispatched tag)
- Report dispatch summary with status for each work item
