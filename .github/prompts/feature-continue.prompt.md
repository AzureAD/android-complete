---
agent: software-factory
description: "Continue the actual owning Software Factory workflow safely"
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

Resolve the shared ADO feature and inspect its artifacts, claims and current gate.
Finished stages do not resume for approval: approve their exact saved artifact,
then separately start the next stage. Resume only an interrupted within-stage
run using its actual persisted execution/session/run IDs. Another teammate can
open the shared feature and start a subsequent stage in their own session.

Raise only a user-approved limit that was reached. Reject source/configuration
drift or superseded requirements and explain successor/reconciliation actions.
Never fabricate ownership, copy journals, silently restart, or use legacy JSON as
an execution checkpoint. Chat-only operation must not require the browser.
