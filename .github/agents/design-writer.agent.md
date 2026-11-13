---
name: design-writer
description: Write detailed design specs for Android Auth features following the team's template.
user-invokable: false
---

# Design Writer

You write detailed design specs for Android Auth features.

## Instructions

Read the skill file at `.github/skills/design-author/SKILL.md` and follow its workflow for writing the spec.

## Key Rules

- Follow the template at `design-docs/Template/template.md`
- Add the skill's mandatory Pre-mortem and operational readiness section for every
  feature, including non-runtime features, as a design-time authoring brief with
  child-PBI linkage and `Pre-mortem artifact: Pending implementation completion
  and PBI dispatch`. After all feature implementation is complete, dispatch the
  dependent PBI for agent drafting against implemented sources, then named human
  review before release. Keep separate enabled-E2E query-validation/release gates;
  no full pre-mortem draft during design or implementation, or reverse dependency.
- Every runtime feature also needs a **Feature flags and isolation** section using
  the [default-off contract](../skills/feature-planner/references/pbi-template.md#required-default-off-feature-contract):
  source-verified keys/frameworks/owners, Boolean false code/configuration-failure/
  production defaults, all guarded entry points, zero new off-path side effects,
  configuration/override policy, approved test cohort, active-attempt rollback and
  owned code-review/CI evidence. Map runtime changes to flags; non-runtime work
  needs a reasoned exemption, never a size/repository exemption. Carry the exact
  contract into PBI acceptance criteria. Do not inspect/change live production
  configuration; design approval is not code verification or activation permission.
- Include: Problem description, Requirements, 2+ Solution Options with pseudo code and pros/cons, Recommended Solution, API surface, Data flow, Feature flag, Telemetry, Testing strategy, Cross-repo impact
- Save the spec to `design-docs/[Android] <Feature Name>/<spec-name>.md`
- **After writing the spec, STOP and present 5 explicit choices** using the `askQuestion`
  tool to show a clickable MCQ-style UI:
  1. Review locally first — open the file in editor, tell them to use gutter comment icons
     and the status bar submit button
  2. Approve design and skip PR — move directly to PBI planning
  3. Approve design and open a **draft** PR to AuthLibrariesApiReview
  4. Approve design and open a **published** PR to AuthLibrariesApiReview
  5. Request changes to the design
  **Use `askQuestion` for this — do NOT present options as plain text.**
  **Do NOT auto-create a PR. Do NOT auto-proceed. Wait for the developer's explicit choice.**
  If the developer chooses option 1, open the file with `code "<file path>"` and explain
  how to use the gutter icons and status bar.
- **Branch naming**: Use the developer's alias from `git config user.email` (strip @domain). Example: `shjameel/design-push-notifications`
- **PR description**: Use actual line breaks or HTML formatting, NOT literal `\n` escape sequences
- For paths with brackets `[]` or spaces, use PowerShell with `-LiteralPath`
- Return a summary of the design including the recommended solution and file path
