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

## Mandatory Backlog Verifications

- **Feature pre-mortem child PBI**: Verify the approved plan includes (or reuses
  without duplicating) one dedicated pre-mortem child PBI under the confirmed
  parent Feature per
  [Required Feature Pre-mortem PBI](../feature-planner/references/pbi-template.md#required-feature-pre-mortem-pbi),
  with the design-time authoring brief, document destination, named human
  reviewer, and `Pre-mortem artifact: Pending implementation completion and PBI
  dispatch` inlined. Confirm dependencies on every implementation PBI (never the
  reverse or a self-link) and bidirectional design/PBI linkage (reporting any
  unavailable back-link rather than claiming completion). Apply only approved
  updates on reused items without resetting state or dropping unrelated metadata,
  and never apply `copilot-agent-ready` to human-review-only follow-ups.
- **Default-off feature contracts**: Verify runtime implementation PBIs preserve
  the exact approved
  [default-off contract](../feature-planner/references/pbi-template.md#required-default-off-feature-contract)
  and non-runtime items declare a no-runtime-impact reason. Missing or
  contradictory contracts return to design/planning for approval rather than
  silently adding flags or expanding the plan. Distinguish created versus reused
  items and name later query-validation and release-gate owners in the summary;
  backlog creation is not implemented isolation or rollout approval.
