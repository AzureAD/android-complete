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

## Mandatory Planning Contracts

- **Dedicated pre-mortem child PBI**: Every feature, including small,
  single-repo, and non-runtime features, must include one dedicated
  `documentation/review` pre-mortem child PBI (or propose reuse of a suitable
  existing item) following
  [Required Feature Pre-mortem PBI](references/pbi-template.md#required-feature-pre-mortem-pbi).
  Inline the design-time authoring brief, document destination, named human
  reviewer, full content/acceptance contract, and
  `Pre-mortem artifact: Pending implementation completion and PBI dispatch`.
  Make it depend on all feature implementation PBIs across repositories (never
  the reverse). Use `copilot-agent-ready` only when agent-authoring scope and
  inputs are ready for approved post-implementation dispatch, never for
  human-review-only follow-ups. Agent completion does not accept the PBI;
  enabled-E2E query validation and release approval remain separate gates.
- **Default-off feature isolation**: Runtime features must carry the approved
  [default-off flag contract](references/pbi-template.md#required-default-off-feature-contract)
  into every runtime implementation PBI (exact flag IDs/keys, source-verified
  owning frameworks and owners, Boolean `false` code/configuration-failure/
  production defaults, entry-point guards, off-path isolation, configuration
  policy, approved test cohort, active-attempt rollback, and owned
  code-review/CI evidence). Non-runtime items require a reasoned
  no-runtime-impact declaration, not blanket N/A. Missing or contradictory flag
  or pre-mortem contracts return to design review rather than silently expanding
  the approved design or inventing flags.
