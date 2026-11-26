---
agent: software-factory
description: "Capture a feature idea, define success together, and run Software Factory's guarded design workflow"
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

Use Software Factory for the supplied feature description or stable feature ID.
Start with a plain-language feature brief; do not require IDs, numerical metrics,
or a special requirements syntax. Save the draft even if success criteria are
not yet known. When requested and explicitly authorized, use the native `intake`
workflow to propose checkable outcomes and open questions for review. Saving
chosen criteria and approving them are separate steps. Obtain genuine approval
of the exact saved revision before design. When the user authorizes execution, use
`start_workflow` with `workflow: "design"` through the shared command tool.
Present the full gate packet and source evidence; a GO recommendation is not
human approval. Do not invoke phase subagents manually or use legacy state files.
