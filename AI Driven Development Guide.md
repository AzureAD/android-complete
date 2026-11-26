<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory Developer Guide

Software Factory replaces Feature Orchestrator. Use one lifecycle, ADO Feature
attachments for shared requirements/artifacts/approvals, and separate native
Copilot Dynamic Workflows from app/interactive CLI chat or
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

Publish a private draft deliberately to an existing ADO Feature before approval.
Chat/dashboard confirmations are attributed to the authenticated ADO user and
require edit permission on that Feature; the session's user may approve their
own AI-assisted work. SQLite is only a draft/cache/local execution store.
Design and planning finish before human review. Backlog creation and dispatch
are separate confirmed commands, not steps in a paused lifecycle workflow.

Existing `feature-design`, `feature-plan`, `feature-backlog`, `feature-dispatch`,
`feature-status`, `feature-continue`, and `feature-pr-iterate` commands all route
to Software Factory. Design/planning/backlog/dispatch skills are entry points
to the same tools, not instructions to manually launch specialist agents.

Research/design/planning workers consume domain references and return structured
data under a single read-only contract. Provider adapters perform mutations.
No hook infers an active feature from the most recently updated JSON record.

### Default-off feature isolation

Every Android-complete **runtime feature** requires a flag that defaults to
Boolean `false` in code, configuration-failure handling, and planned production
configuration. Small or single-repository features are not exemptions. Explicitly
non-runtime/documentation, regression-only, or deferred changes need a reasoned
no-runtime-impact declaration; every feature still needs its pre-mortem.

The Design Doc Dynamic Workflow generates a structured flag register and
**Feature flags and isolation** section, maps runtime changes to pinned,
source-evidenced contracts, deterministically rejects missing/non-false defaults
and incomplete mappings, and independently reviews isolation. Contracts name the
actual framework/key/owner, every guarded entry point, zero new off-path side
effects, configuration/cache/override policy, approved enabled E2E cohort,
active-attempt rollback, and owned verification. New keys are proposals; workflow
workers do not create flags or inspect/change live production configuration.

Planning and backlog creation retain the exact contracts and code-review/CI
acceptance evidence; gaps return for design/plan approval rather than silent
scope expansion. Implementation owners prove actual false defaults,
configuration failure handling, unchanged off behavior, enabled behavior, and
rollback. Release owners separately approve enablement. **Design approval is not
implemented isolation or activation permission**; an approved enabled test cohort
does not require broad rollout.

### Feature pre-mortem

Every Android-complete feature, including non-runtime features, requires a
design-linked pre-mortem and a dedicated child PBI to author/review it. Planning
includes the item; approved backlog creation verifies and reuses a suitable
existing item rather than duplicating it, confirms the parent Feature and normal
metadata, and records design/PBI links in both directions.

The [Design Doc Dynamic Workflow](.github/extensions/DevDesignDoc/README.md#generated-feature-pre-mortem)
generates a required feature-specific **authoring brief**, not the pre-mortem
draft, in its JSON design artifact. Independent design review checks inputs,
planned content, ownership, and handoff. The packet records pending/existing
child-PBI linkage and `Pre-mortem artifact: Pending implementation completion
and PBI dispatch`; the workflow does not create the PBI or draft/publish the
future document. Planning and backlog creation follow explicit human design
approval.

The dedicated PBI depends on **all feature implementation PBIs** across
repositories. Only after every implementation item meets acceptance criteria,
changes are merged where applicable, and implementation validation evidence is
available is it dispatched. The agent then writes the full pre-mortem against
implemented, pinned sources and the approved design, including actual KQL drafts,
and links the artifact. The named human reviewer checks scenarios/impact,
assumptions, monitoring contracts, owners, rollback, and gaps; the agent
addresses revisions and the human records acceptance before release/production
enablement. Agent completion is not acceptance. Do not create a reverse
dependency making implementation wait for this document. For no implementation
PBIs, justify that case and verify approved non-runtime deliverables are complete
before authoring.

Use the [pre-mortem content and acceptance contract](.github/skills/feature-planner/references/pbi-template.md#required-feature-pre-mortem-pbi).
Authoring/review can finish with explicitly unexecuted pre-flight KQL, unresolved
mappings, and named follow-up owners; absent production data while disabled is
expected. Before monitoring is used for rollout, its owner validates queries and
telemetry against an approved enabled E2E test cohort. The release owner
separately checks release/rollback prerequisites and approval; broad production
enablement is not needed to validate queries. PBI completion is not rollout
approval, even when the dashboard labels the feature complete.

## Continuing work

Open the shared ADO Feature to continue with the team's current artifacts and
approvals, using your own local checkout and account mappings. Say, for example,
"Continue Broker throttling." Read current status before retrying a failure.
An interrupted native run still belongs to its original owner. Reconnect that
owner or explicitly reconcile/release its shared claim; never silently take over.
After a stage finishes, another teammate can start the next in their own session.
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
