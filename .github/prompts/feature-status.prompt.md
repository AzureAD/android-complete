---
agent: software-factory
description: "Inspect Software Factory status or run an approved bounded monitor"
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

Read the exact feature, owning native executions, receipts, and latest observation.
Show timestamps, stale data, blocking gates, and partial/unknown results.
If the user wants a fresh provider/acceptance-criteria pass, request confirmation
and start the native `monitor` workflow. Do not silently incur model usage for a
cached-status request. A completed workflow or closed-unmerged PR is not delivered
scope. Never scan unrelated PRs, post feedback, approve PRs, or merge.
