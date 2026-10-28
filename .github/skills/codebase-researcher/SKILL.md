---
name: codebase-researcher
description: Research Android authentication code, implementations, architecture, and data flows with verified source evidence. Use for "where is X implemented", "how does Y work", "trace this flow", or codebase exploration. This skill does not orchestrate feature development.
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Codebase Researcher

Read [research standards](references/research-standards.md) and apply them to the
requested question. Search relevant available repositories, inspect actual source,
and report file/line citations, confidence, and evidence gaps. Use code intelligence
when available, then scoped file and content searches.

For Authenticator app-specific research, read
`authenticator/.github/skills/codebase-researcher/SKILL.md` when available and use its
supported evidence sources within the caller's permissions. Do not bypass denied
reads or invent access to unavailable tools.

Return the caller's requested structure. For an ordinary chat question, present
concise findings and any material uncertainty. Do not start design, create work
items, dispatch agents, or infer feature ownership from previous research.
