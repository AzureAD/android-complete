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
