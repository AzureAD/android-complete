---
name: pbi-creator
description: Create a Software Factory feature's approved backlog through its guarded provider adapters. Use for "create the PBIs", "create work items from this plan", or "push PBIs to ADO".
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory Backlog

Follow `.github/agents/software-factory.agent.md`.

1. Resolve the saved feature and inspect its plan, approval gates, and configured
   repository/account/ADO board targets. Chat text is not an approved plan.
2. Resolve missing configuration through the shared configuration command and
   genuine confirmation. Do not guess area paths, iterations, or accounts.
3. Obtain outstanding exact plan approval, then separately preview and confirm
   the backlog gate. Separately preview and confirm `create_backlog`.
4. Report ADO-backed work-item receipts and unresolved operations.

The application creates/links work items and reconciles ambiguous responses.
Do not invoke a creation subagent, call provider mutation APIs directly, or
create a second backlog after a timeout. Historical records require explicit
preview/import and new applicable approvals; they are not execution authority.
