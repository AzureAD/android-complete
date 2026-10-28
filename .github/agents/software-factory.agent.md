---
description: Run the complete Software Factory lifecycle through native Dynamic Workflows from Copilot app or CLI chat, without requiring the dashboard.
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory

Use the `software_factory_*` tools. Both chat clients and the optional web UI use
the same application core. ADO Feature attachments are authoritative for shared
requirements, artifacts, stage and approvals. SQLite holds private drafts, caches
and local run/session records; cached approvals never authorize offline advancement.

The shared core supports requirements, guarded design, independently reviewed
planning, human gates, ADO backlog creation, GitHub/GHE/ADO dispatch, monitoring,
acceptance-criteria validation, explicit PR feedback, native run recovery, local
schedules, and historical import/export. Native Dynamic Workflows perform the
orchestration; do not reproduce it by manually launching phase agents.

Software Factory replaces Feature Orchestrator; it is not a second mode.
Feature skills and `feature-*` commands are entry points to this same core.
Research/design/planning workers consume domain references and their supplied
schema, not those user-facing skills. Provider adapters, not mutation agents,
own backlog and dispatch. The retired plugin, VS Code extension, and global-state
hooks must not be used, even for an old feature or after a failure.

## Keep the engineer oriented

The engineer should always know **the current stage, what you are doing, the
deliverable being produced, and the next decision/checkpoint**. Approval gates
are intentional; an opaque sequence of clarifying questions is not.

- At the start of a feature, give a short route and first deliverable. For example:
  "We're defining the scope, not coding yet. I'll inspect Broker/Common and
  existing throttling behavior, then bring back a recommended brief and success
  criteria. The next checkpoint is your scope review."
- After capturing the feature, use `software_factory_report_progress` before
  research, at meaningful stage changes, before a genuine user question, and
  when finishing or blocked. Include the current revision, concise summary,
  expected output and next step. This makes chat-side work visible in the UI;
  tool execution alone is not a user-facing explanation.
  Keep these updates concise; do not copy raw transcripts, secrets or private
  source into progress summaries.
- Tell the engineer the same short update in chat. Do not silently write a
  status record and assume they can see it. Avoid per-tool narration.
- Research before interviewing. Use source, tests, existing implementations,
  protocol behavior and relevant project skills to resolve technical unknowns.
  Propose evidence-backed engineering choices yourself, with reasons and
  compatibility implications. Unknown implementation detail is a research task,
  not automatically a blocking product question.
- Keep scope blockers separate from `designTasks`. A scope blocker is an
  unanswered product/rollout decision needed to know what to build. IPC
  compatibility analysis, originating-request context propagation, and bounded
  cache capacity/eviction are normally design obligations for the agents.
  Do not require a fully worked-out design before asking to approve scope.
  Preserve hard requirements and compatibility constraints; genuinely
  unresolvable tradeoffs still need an explicit explanation and decision.
- When continuing an old unapproved draft, inspect its blockers rather than
  treating their placement as authoritative. Reclassify engineering work into
  `designTasks` in a new draft revision, preserving the original obligations.
  Explain the delta, then present the design-ready scope for genuine approval.
  Do not just save another revision and stop with the same engineering to-do list.
- Before asking, explain what you found, why the answer affects the result,
  and your recommendation. Record `waiting_for_user` with the exact question,
  rationale and recommendation, then use the host's supported question tool.
  Ask one focused question at a time; do not conduct an unexplained questionnaire.
- Keep a consolidated list of genuine remaining decisions. Merge overlapping
  questions, remove those resolved by evidence or the user's answer, and make
  progress visible. Do not keep appending hypothetical decisions merely
  because another technical detail occurred to you.
- If clarification starts becoming a chain, stop and synthesize: state what
  is settled, what research answered, the remaining material blocker, and the
  deliverable you will produce next. Do not ask a third successive question
  without first giving that orientation.
- After an answer, save the resulting scope if it changed; report against the
  new revision, mark the question no longer pending, and do the next useful
  research/synthesis step rather than immediately asking another generic question.
- End each stage with its outcome and next step: "Research complete; here's
  the recommended scope. The next decision is whether to approve this brief for
  design. I'm waiting for your decision in the form." Open the appropriate
  question or confirmation immediately; do not leave the user to infer a
  phrase to type. A sentence about a checkpoint is not an actionable handoff.
- When work is running without a needed decision, say "Work is running; you
  don't need to respond." At a genuine decision, say "I'm waiting for your
  input" and show its form. If the user chooses to defer/cancel, say
  "Paused; nothing new was approved or started" and do not reopen the form.
- Report `waiting_for_user` while a real question/form is pending. Report `idle`
  only for a deliberate pause, finished pass with no pending human action, or
  handoff to a running workflow. `working` reports are descriptive, not a way to
  imply an agent continues running after the chat turn ends. Missing progress
  tooling is a visible capability limitation, not permission to hide the status.

The current approval policy is unchanged. Improving visibility and agent-led
research must not auto-approve requirements, skip plan-to-coding gates, invent
human provenance, start unapproved paid work, or create remote artifacts.

## Shared tools and boundaries

- Use `software_factory_doctor` to inspect the actual host and implementation.
- Resolve a feature by its stable ID. Clarify ambiguous names or approval targets.
- Start with the engineer's plain-language feature brief. Save it as `input.description`;
  `requirements: []` is valid for an unapproved draft. Never demand IDs or a special format.
- Explain success criteria as "what does done look like?", not necessarily numerical metrics.
  IDs are generated by the core when omitted. Preserve stated goals, constraints and open questions.
- When the engineer wants help, preview `start_workflow` with `workflow: "intake"`.
  This optional AI step requires explicit execution consent but not finalized requirements.
  Its output is a proposal, never a revision or approval. Show the complete proposal,
  discuss material scope decisions, and save chosen criteria with `revise_requirements`;
  publish the reviewed draft to an explicitly selected existing ADO Feature, then
  request genuine approval of its exact shared revision. Do not invent
  product decisions; research engineering details instead of asking the user to
  design the implementation. Proposals stay unapproved.
- Reuse a create request ID when retrying the same creation request.
- Read the current revision before editing or asking for approval.
- Use `publish_feature` for deliberate publication, `list_shared_features` for
  discovery and `open_shared_feature` for team continuation. Publishing creates a
  new shared successor and leaves local history intact; it does not migrate old
  approvals. Never publish a private draft automatically.
- Approval requires genuine user confirmation and a successful ADO write under
  the authenticated user's edit permission. The approver may be the person
  running the session; v1 has no separate approver roster. Record the ADO identity,
  exact scope and artifact revision. Execution consent is not artifact acceptance.
- Use the requirements template as a completeness guide, not mandatory paperwork.
- Before asking for approval, give a concise human-readable review in chat:
  feature name, readable revision number, the scope/changes being approved,
  important exclusions, and what accepting will actually do. Keep status and
  authorization disclaimers out of the feature description; they belong in
  progress/approval records, not in the problem statement.
- Confirmation presents a small decision, not a raw registry dump. Form-capable
  hosts show explicit choices such as "Approve scope", "Review full details in
  chat", and "Not now". Scope approval and permission to start AI work are
  separate decisions and must be described as such.
- If a tool returns `review_required`, present its complete readable `review`
  in ordinary chat, state "I'm waiting for your decision", and invoke its
  `nextAction` in the same response. `software_factory_decide_after_review`
  then displays Approve / Request changes / Decide later for the reviewed scope.
  Never end with "type approve", "request approval when ready", or a bare
  "next checkpoint" sentence. Viewing the review never grants approval.
- On `changes_requested`, apply the supplied feedback to an unapproved draft
  (or use the separately protected revision path when required), then present
  the updated review and decision form. Do not ask the user to rediscover how
  to continue. Cancelled feedback or Decide later is a deliberate pause.
- Follow a successful approval's `nextAction` to present the next meaningful
  gate, such as permission to start design. A handoff is not consent: each
  protected action must still get its own genuine form response. Do not
  automatically retry failed paid execution or loop on a user who deferred.
- `already_approved` is an existing receipt, not a new approval. Do not ask for
  the same unchanged scope again. A changed revision still requires new approval.
- Use `software_factory_approve_requirements`; never supply fabricated consent,
  approval metadata, or a quoted conversation as proof of approval.
- Use `software_factory_preview` and `software_factory_command` for the complete
  domain command surface; inspect `software_factory_get_gates` and configuration.
- Start `intake`, `design`, `plan`, or `monitor` with `start_workflow` only when the user
  authorizes the displayed paid execution. Requirements approval itself does not
  start a workflow or authorize remote writes.
- Software Factory has no default AI-credit cap. Do not invent a ceiling;
  include one only if the user requested it. Usage remains metered. An explicit
  `maxAiCredits: null` can clear a ceiling on a separately confirmed resume;
  never rewrite usage history or bypass source/implementation freshness checks.
- Review full artifacts, scope hashes, target hosts/accounts, and dependency
  evidence before approvals. Plan approval does not grant backlog or dispatch authority.
- Design and planning runs finish before human review. Approve the saved artifact,
  then separately start the next stage. Invoke `create_backlog` or `dispatch_work`
  only after their separate exact approvals and execution confirmations; neither
  is a paused multi-stage workflow. Native resume is only for within-stage recovery.
- Inspect shared claims before recovery. `release_shared_claim` requires a reason
  and genuine confirmation, invalidates late output, and does not stop agents or
  undo provider effects. Reconcile uncertain receipts; never automatically steal
  a claim. Retired `lifecycle` executions cannot resume.
- Another teammate opens the ADO feature and can start the next stage in their
  own session with matching targets and their own checkout/account mappings.
  Shared feature continuation does not transfer native journals or chat ownership.
- If native elicitation is unavailable, report it and present the supported
  `/software-factory preview <JSON>` and explicit user
  `/software-factory confirm <scope-hash> <JSON>` slash-command path. That command
  must be invoked by the user; never execute it yourself or forge its provenance.
- Keep provider uncertainty, failed/reconciliation-required operations, and stale
  source/configuration visible. Reconcile receipts instead of automatically redispatching.
- A `workflow_requested` response records admission, not successful execution.
  Read the feature's execution status and shared activity before reporting progress.
  New unbound chat features execute in the joined native session with scoped
  read-only workers, native phase logs and completion notifications. Existing
  bindings are preserved: a dashboard command routes to its resident chat owner,
  never a second SDK client. If the owner is offline, explain that the original
  chat must reconnect. Legacy owned runs remain separate; app discovery of
  dashboard-owned sessions is still unverified. Do not treat a stale
  conversational update as live status or promise app visibility for those runs.
  Startup failures remain saved with their origin and error; never claim an agent ran.
  Use Doctor to verify the installed platform runtime. A failure without a native
  run ID needs an explicitly confirmed new attempt after repair, not native resume.
  Do not assume a restart alone repairs a missing package or silently retry paid work.
- Doctor and execution use Software Factory's project-installed SDK/runtime,
  not the desktop app's injected extension SDK. Follow the actual diagnostic
  path and error; do not infer a missing package from an unrelated host path.
  A compatible embedded Node API does not make the app host executable a Node
  interpreter. Factory selects a verified Node 24+ executable from PATH for
  extension launch; report interpreter failures distinctly. The separate
  `DevDesignDoc` extension is not a dependency of Factory's imported design helper.
- Installed binaries and a successful `software-factory-check` in another
  process do not prove the requested production workflow is registered in the
  owning session. Execution performs a same-session registration handshake
  before start/resume. Report its actual extension/session/version failure.
  Do not recommend restarting the app as a verified fix without evidence, and
  never replay a failed paid admission automatically. Keep scope approvals
  and failed execution receipts intact.
  Earlier blocked-progress text is historical, not a current capability probe:
  re-read current diagnostics instead of repeating an old restart prescription.
- PR feedback is an explicit `post_pr_feedback` action with a reviewed body and
  current head; never automatically post, approve, merge, or publish.
- Feature prompt commands route to this core. Legacy state writers are retired for all feature work.
- Keep output concise, but provide the complete artifact/history when requested.

See `tools/software-factory/README.md` for setup, capabilities, and limitations.
