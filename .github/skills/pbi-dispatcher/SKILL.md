---
name: pbi-dispatcher
description: Dispatch a Software Factory feature's approved backlog through dependency-aware native execution and provider adapters. Use for "dispatch these PBIs", "assign to Copilot", or "start agent implementation".
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory Dispatch

Follow `.github/agents/software-factory.agent.md`. Resolve the stable feature,
read the backlog receipts, target hosts/accounts/base branches, coding mode
(`local` by default, or `remote`), independent reviewer round limit
(`maxReviewRounds`, default 3, configurable e.g. to 2), dependency evidence, and
exact dispatch gate. Obtain genuine dispatch confirmation, then separately
preview and confirm `dispatch_work`. By default (`mode: "local"`), coding runs
locally in the current Copilot CLI/app session with a builder ↔ independent
reviewer loop in `localPath`, then pushes a branch and opens a PR after the
reviewer passes.

Read persisted dispatch receipts and actual run/provider status before reporting
success. Reconcile uncertain operations instead of re-sending them. Respect
dependency-ready waves and report blocked dependencies.

Do not invoke a dispatch subagent, globally switch accounts, create fallback
issues, or dispatch directly from chat text or arbitrary AB# IDs. An unsupported
target or missing feature record is a visible blocker, not permission to bypass
Software Factory's configuration, approvals, or receipts.
