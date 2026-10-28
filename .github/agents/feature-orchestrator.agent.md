---
description: Compatibility entry point for Software Factory's native Dynamic Workflows in Copilot app and CLI chat.
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Feature Orchestrator compatibility

Follow `.github/agents/software-factory.agent.md`. This name is retained for
engineers using the existing feature-orchestrator entry point.

Use the shared `software_factory_*` commands, registry, exact-scope human gates,
and native Dynamic Workflows. Do not manually coordinate phase subagents or
write legacy `state-utils.js` records for Software Factory features.

Capture new features as plain-language briefs; do not demand IDs or finalized
criteria up front. Optional native `intake` runs propose success criteria for
review and editing. Save the chosen criteria, then obtain separate approval
before guarded design. Map planning to the
`lifecycle` workflow after explicit design approval. Backlog and dispatch require
their own exact previews and approvals, followed by resuming the owning run.
Status can read recorded state or start an explicitly approved bounded monitor.
Continue uses the persisted owning session/run; PR iteration reads actual review
feedback and posts only the exact body a user approves.

Legacy JSON is historical input, not a trusted approval or resumable execution.
Use preview/import for old records. The old standalone plugin remains an explicit
legacy path for unmigrated work, never an automatic fallback after failure.
