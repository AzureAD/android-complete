---
name: design-reviewer
description: Submit feedback on a saved Software Factory design through its exact-artifact revision path. Use for "address design feedback", "revise this feature design", or "handle my design review comments".
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory Design Revisions

Follow `.github/agents/software-factory.agent.md`. Resolve the feature and read
its current saved design artifact before processing feedback.

Preview `request_revision` with the exact feature ID, `stage: "design"`,
`expectedArtifactId`, and the user's feedback. Obtain genuine confirmation
through the shared command surface. Explain invalidated downstream approvals
and obtain separate execution consent before the next design attempt.

Review the resulting artifact and present its real approval gate. Do not edit
a local file as canonical state, delete review comments from a sidecar JSON,
post to PR threads, or treat a corrected design as automatically approved.
For feedback without a matching Factory feature/artifact, explain the missing
context; do not invent a binding or start an unrelated feature.

## Required Revision Checks

When revising a design, inspect its full **Accessibility design** section and
linked requirements and Testing strategy verification matrix against the
`design-author` accessibility contract: assess stable ID coverage, concrete
decisions for every applicable screen/state and interaction mode, reuse
justification, and verification owners/evidence/gaps. Retain the section through
edits; require a reason for non-UI/N/A, and flag missing applicable coverage as
blocking rather than silently removing it. Check representative multi-screen
paths, including loading, errors, cancellation, return and re-entry, contrast,
and non-color cues. Keep planned runtime evidence `Unverified`; design approval
is not accessibility or rollout acceptance.

After substantive design revisions, ensure the paired **Human Design Review
Brief** is regenerated and re-reviewed for the same revision with changes since
prior human review highlighted; never reuse stale approval.
