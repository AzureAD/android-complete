---
name: code-feature
description: Experimental, explicit opt-in launcher for the Android coding and PR dynamic workflow. Use only for /code-feature with a task or a current request to implement using the coding workflow, dynamic workflow, coding dynamic workflow, coding & PR workflow, coding and PR workflow, or coding & PR dynamic workflow (also coding and PR dynamic workflow). Do not use for ordinary implementation prompts, /code-author, workflow questions, quoted examples, documentation/routing changes, or delegated workers. Opt-in applies to this request only.
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Code feature (experimental, opt-in)

## Check the invocation boundary

Launch only when the user's **current actionable request** explicitly selects this
workflow. Accept `/code-feature` followed by a task, or natural-language equivalents
such as these (case and normal whitespace do not matter):

- `Use the coding workflow to implement PBI 3804404`
- `Use the dynamic workflow to implement PBI 3804404`
- `Use the coding dynamic workflow to implement PBI 3804404`
- `Use the coding & PR workflow to implement PBI 3804404`
- `Use the coding and PR workflow to implement PBI 3804404`
- `Use the coding & PR dynamic workflow to implement PBI 3804404`
- `Use the coding and PR dynamic workflow to implement PBI 3804404`
- `Implement PBI 3804404 using the coding and PR dynamic workflow`

Treat these as examples, not exact strings: the task may be another PBI, a design doc,
or a concrete feature/fix request. No tool-name syntax, JSON, or configuration toggle
is required from the developer. Explain that this is the experimental coding-and-PR
path when launching; it may create a PR and run bounded CI/review observation.

Do **not** launch for any of the following:

- Ordinary coding: `Implement PBI 3804404`, `Fix this bug`, `Code and open a PR`, or
  `/code-author implement PBI 3804404`. Use [code-author](../code-author/SKILL.md) directly.
- Negation: `Do not use the dynamic workflow; implement PBI 3804404 directly`.
- Questions: `How does the coding workflow work?`, `Can it implement PBI 3804404?`,
  or `What happened to the workflow run?`. Answer or inspect the existing run.
- Quoted examples, code blocks, pasted history, or configuration discussions such as
  `Add "Use the coding workflow to implement PBI 3804404" as an opt-in phrase`.
  Mentioning or documenting a launch phrase is not a request to execute that example.
- A prior request's opt-in or a completed milestone/outcome receipt. Do not silently
  opt unrelated later tasks in, re-prompt on every ordinary task, or restart existing work.
- Builder/reviewer/evidence-worker instructions inside an already-running workflow or
  another delegated implementation assignment. Never recursively launch a workflow.

If explicitly invoked with no task, ask for the task instead of inventing one. If the
requested action is to explain, configure, or edit the workflow itself, handle that
request directly rather than running this launcher on its own configuration.

## Launch the selected task

After confirming explicit opt-in, call
`run_dynamic_workflow({ name: "code-feature", args: { prompt: "<current user request verbatim>" } })`.
For `/code-feature`, retain the task text and constraints in the prompt; do not substitute
one of the examples above. Do not pre-fetch the PBI/design or prepare target checkouts:
the registered workflow gathers evidence, implements, source-reviews, publishes and
observes the resulting PR.

Omit `options` by default. The workflow automatically selects an installed compatible
JDK for the target repositories, scoped to this run, and announces the selection in chat.
It does not change global `JAVA_HOME`, install software, or need a repeated approval merely
because the inherited JDK is incompatible. Respect an explicit `options.javaHome` selection;
do not silently replace an explicitly chosen incompatible installation.
Do not silently disable PR publication or observation.
Do not set model, effort, context, or resource ceilings without explicit user instructions.
If the registered workflow/tool is unavailable, explain that it cannot be launched;
do not silently substitute a standalone background agent or a different orchestrator.
The [code-author standards](../code-author/SKILL.md) still govern implementation.

## Keep the caller informed

- After launch, end the caller turn without `Start-Sleep` or inspect-run polling loops.
  In Autopilot, use the normal task handoff tool to leave the caller idle; a launch
  acknowledgment is not a claim that implementation or validation completed.
- Relay each new `[workflow-milestone:...]` receipt as a concise visible chat update,
  including blocked/deferred verification and any PR link. Do not wait until terminal
  settlement, restart the workflow, or repeat an already-reported marker.
- Inspect terminal outcomes, not parent-session idleness. Use the read-only
  `code_feature_workflow_status` tool for an existing run when needed. Report actual
  local/CI receipts, PR links, remaining failures/policies, and the next responsible owner.
  `completed` is not proof of tests passing or merge readiness. Never infer success from
  authored test counts, builder/reviewer verdicts, or a `GO` label.
- If paused for a prerequisite or owner decision, open an actual `ask_user` question
  immediately; do not end with only a paused-status report or silently wait for the user
  to discover the blocker. Use the recorded `pendingInput`/question data, ask one question
  at a time, and preserve/relay an already-pending input request rather than duplicating it.
  If the user already answered that recorded question, apply the answer instead of asking again.
  Confirm the run is still paused, then apply the answer and resolve the blocker before
  resuming the same run. Resume has no answer/environment argument. If no compatible
  installed JDK is found or the user explicitly selects another JDK, write
  `{"runId":"<same run ID>","javaHome":"<approved JDK directory>"}` to the pause log's
  `.github/local-context/code-feature/<runId>.json` path, then resume. JSON-escape Windows
  backslashes; never store credentials or change global `JAVA_HOME`.
- If the user delegated PR/CI shepherding, use the supplied `pr-observer` continuation
  on the existing PR. Preserve remaining repair budgets, including zero; do not restart
  `code-feature`, create another PR, or silently reset exhausted repair slots.
- Preserve the checkout/evidence after an interruption or non-resumable error. A new
  implementation run requires user authorization. Human approvals, presence checks,
  merge strategy and merge decisions remain human gates.
- Completion/milestone delivery uses a durable owning-session outbox, but an unavailable
  host/client may prevent a visible reply. State that limitation instead of pretending
  the developer received an update.
