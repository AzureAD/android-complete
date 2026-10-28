<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory Developer Guide

Software Factory replaces Feature Orchestrator. Use one lifecycle, one local
registry, and native Copilot Dynamic Workflows from app/interactive CLI chat or
the optional dashboard. There is no separate editor orchestration mode.

## Setup

From the repository root with Node.js 24+:

```powershell
.\tools\software-factory\install.ps1
```

Open a fresh trusted repository chat and select `software-factory`. In Copilot
CLI, start `copilot --experimental`. Begin with a plain-language feature brief;
you do not need criterion IDs, finalized requirements, or a local design file.
Use Doctor to check the actual host rather than assuming old chat instructions
or another process prove readiness.

## Lifecycle

| Stage | What happens | Human boundary |
|---|---|---|
| Brief/intake | Save intent; optionally research and propose criteria | Save/review criteria, then exact scope approval |
| Design | Evidence, alternatives, traceability, independent review and bounded fixes | Separate execution consent, then design approval |
| Plan | Repo-targeted self-contained PBIs and independent review | Plan approval |
| Backlog | Guarded adapters create/link ADO work items with durable receipts | Separate backlog approval |
| Dispatch | Dependency-ready coding tasks on configured targets | Separate dispatch approval |
| Monitor/revision | Inspect results, validate criteria, request changes | Exact feedback/revision actions; no automatic PR approval or merge |

Scope approval does not start paid work. A generated artifact, a chat message,
or a native resume is not human approval. The agent should clearly state when
work is running versus when a form needs your input.

Existing `feature-design`, `feature-plan`, `feature-backlog`, `feature-dispatch`,
`feature-status`, `feature-continue`, and `feature-pr-iterate` commands all route
to Software Factory. Design/planning/backlog/dispatch skills are entry points
to the same tools, not instructions to manually launch specialist agents.

Research/design/planning workers consume domain references and return structured
data under a single read-only contract. Provider adapters perform mutations.
No hook infers an active feature from the most recently updated JSON record.

## Continuing work

Use the stable feature and its recorded owning session/run. Say, for example,
"Continue Broker throttling." Read current status before retrying a failure.
An offline chat owner must reconnect; a dashboard must not create another owner.
App discovery of dashboard-owned SDK sessions is still unverified. Shared
registry visibility alone is not proof of app session attachment.

## Upgrading from Feature Orchestrator

Disable/uninstall any old `feature-orchestrator` plugin or
`AzureAD.feature-orchestrator` VS Code extension and remove retained plugin-path
or hook registrations in that client. Their tracked sources/registrations are
retired; installed copies are not automatically removed by pulling this repo.
The old setup script no longer installs or authenticates anything.

Start a fresh chat/reload the integration to replace cached instructions.
Preserve old feature JSON and design documents. Use explicit `preview_import`
and `import_legacy` commands if you need historical records; imported approval
text is not authority, and old runs are not automatically resumed.

See [Software Factory](tools/software-factory/README.md) for command schemas,
approval scopes, setup, recovery, storage, and remaining platform limitations.
