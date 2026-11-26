---
agent: software-factory
description: "Dispatch an exact dependency-ready work set through Software Factory"
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

Review the current Software Factory `dispatch` scope: eligible versus blocked
items, coding mode (`local` by default in the current Copilot CLI/app session, or
`remote` provider dispatch), independent reviewer loop limit (`maxReviewRounds`,
default 3, configurable e.g. to 2), provider/hostname/repository/account/base
branch, merged dependencies, and the distinction between workflow usage and
remote coding-task usage.

Obtain genuine approval for that exact set, then separately preview and confirm
`dispatch_work` (or `start_workflow` with `workflow: "implement"`). In `local`
mode (the default), the local builder and independent reviewer iterate up to
`maxReviewRounds` passes in `localPath`, then push a branch and open a PR once
the reviewer passes. Do not manually dispatch via agent shell commands, ignore
unknown dependencies, or guess PR/session IDs. Reconcile receipts on uncertain
outcomes; never automatically switch to legacy dispatch or merge anything.
