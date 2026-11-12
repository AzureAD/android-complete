---
name: feature-planner
description: Plan an approved Software Factory feature into repo-targeted PBIs using its native planning workflow. Use for "plan this feature", "break this down into PBIs", or "decompose this into tasks".
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory Planning

Follow `.github/agents/software-factory.agent.md`. Resolve the feature by stable
ID and read its saved design and exact gate. Obtain outstanding design approval,
then start the separate `plan` workflow
through a preview and genuine execution confirmation.

The planning run finishes and saves its artifact to ADO before human review.
Present the saved plan and dependencies for human review. Plan approval does not
authorize backlog creation or dispatch. Do not run planner agents manually,
parse a chat Markdown plan as canonical state, or ask for a separate local spec.
Report missing capabilities or prerequisites without a second execution path.

Workers use [planning standards](references/planning-standards.md) and the
[PBI description template](references/pbi-template.md) to return schema-bound
plans. The shared core persists artifacts and validates requirement coverage.
