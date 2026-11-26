---
agent: software-factory
description: "Review actual PR feedback and post only explicitly approved iterations"
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

Read the feature's full design, plan, canonical PR/head, validation, and provider
review feedback with the shared `review_pr` command. Present proposed resolutions
with source references. If PR association is ambiguous, verify an explicit
`attach_pr` binding; never match a PBI by title substring.

Use `post_pr_feedback` only after genuine confirmation of its exact body and
current head. Reuse its request ID for retries and reconcile uncertain posting.
Mentions may trigger coding-agent usage; disclose that in the preview.
Do not post automatically, approve/merge PRs, or manipulate the user's checkout.
