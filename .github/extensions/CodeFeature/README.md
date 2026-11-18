<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android coding and PR workflow (`code-feature`)

`code-feature` is a standalone, repository-scoped **Copilot Dynamic Workflow** modeled
after `.github/extensions/DevDesignDoc` (`design-feature`). A compatible Copilot runtime
discovers `.github/extensions/CodeFeature/extension.mjs` and registers the `code-feature`
workflow along with role-scoped custom agents (`code-evidence`, `code-builder`, and
`code-reviewer`).

## Experimental: explicit opt-in

Normal coding is the default. `Implement PBI 3804404`, `Fix this bug`, `Code and open
a PR`, and `/code-author ...` use ordinary coding assistance, not this workflow.
The installed workflow remains available without a configuration toggle or a prompt
on every coding request.

Select it explicitly with the [code-feature skill](../../skills/code-feature/SKILL.md):

```text
/code-feature implement PBI 3804404
```

Or use any of these natural-language forms:

```text
Use the coding workflow to implement PBI 3804404
Use the dynamic workflow to implement PBI 3804404
Use the coding dynamic workflow to implement PBI 3804404
Use the coding & PR workflow to implement PBI 3804404
Use the coding and PR workflow to implement PBI 3804404
Use the coding & PR dynamic workflow to implement PBI 3804404
```

`coding and PR dynamic workflow` and `Implement ... using the coding and PR dynamic
workflow` are equivalent. Substitute the actual PBI, design doc, feature or fix; the
developer does not need JSON or tool syntax. Selection applies to the current request
only, not unrelated later tasks. Questions, negation, quoted examples and discussions
about configuring the workflow do not authorize a launch. Workers already inside an
implementation never recursively start it.

The launcher is experimental and may create a PR and perform bounded CI/review work.
Unavailable workflow tools are reported explicitly, not replaced silently with a
standalone background agent. Reload repository customizations or use a new session
to discover a newly added skill; an older session may retain its previous instructions.

## Goal

Software Factory reuses the exported `prepareCodeFeature` engine for evidence,
structured implementation, local verification and independent review. Its
application adapter owns isolated target admission, safe source application and
restoration, and publication under the exact Factory approval. The adapter is
supplied by trusted application code, never selected by workflow tool arguments.
Standalone `runCodeFeature` retains its existing authoring/publication/observer
path. Sharing preparation does not enable Factory's post-publication repairs.

Given an approved coding task or PBI specification:

1. **Validate task & approval**: Verify immutable revision binding, human approval provenance,
   unique requirement IDs, target repository metadata, and `AB#<id>` work-item linkage.
2. **Gather repository evidence**: Inspect target repositories (`common`, `broker`, `msal`,
   `adal`, or `android-complete`), verify checked-out commit revisions, and confirm approval
   provenance.
3. **Build initial implementation (`code-builder`)**: Author production code and unit tests
   following Android Auth conventions (Kotlin-first for new code, Java style preserved for
   existing Java files, copyright headers, custom `Logger`, KDoc/JavaDoc, and traceability).
4. **Loop with Independent Reviewer (`code-reviewer`) up to 2 fix iterations max**:
   - Deterministic checks validate file safety, non-empty unit tests, requirements-to-files/tests
     traceability, PR metadata, unified diffs, and any configured `verificationCommands`
     (such as Gradle compile/test commands).
   - `code-reviewer` (restricted to read-only `view`/`glob`/`grep` tools) evaluates the diff,
     tests, and PR packet against the 9-point fixed rubric and every target acceptance criterion.
   - If any validation error or review finding exists, the workflow returns to `code-builder`
     with the structured findings (`fix-1`, `fix-2`) until the reviewer reports zero
     findings or the 2-fix ceiling (`maxFixIterations: 2`) is reached.
5. **Publish source-reviewed Pull Requests**:
   - Only after structural checks and the independent source reviewer pass with zero findings, the
     workflow prepares or opens each target PR with `Fixes AB#<workItemId>` in the title and
     body, requirements traceability, test plan, and compatibility/rollout summary. A local
     timeout or environment failure may defer execution to CI; it is not a test pass.
6. **Observe CI and hand off accurately**: Return explicit `waiting-for-ci`, `ci-failed`,
   `awaiting-human-review`, `needs-owner-decision`, or `ready-for-human-review` delivery
   status, with current-head check evidence and an observer continuation when applicable.

## Input and use

### Recovery safeguards

- JDK discovery requires a supported major version in the installation's `release`
  file and both `bin/java` and `bin/javac` (`.exe` on Windows). An explicitly configured
  `options.javaHome` override is validated and never silently replaced. Without an explicit
  override, an incompatible inherited `JAVA_HOME` no longer blocks the run: the workflow
  automatically chooses an installed compatible JDK for all target repositories, announces
  its path/version in chat, and passes it to the builder and verification in a run-local
  environment. Global `JAVA_HOME` is unchanged. No installation or repeated JDK approval
  is needed. A question remains necessary when no suitable installation is found or the
  developer's explicit override is invalid.
- PowerShell builder examples quote `'-Pkotlin.compiler.execution.strategy=in-process'`
  so the native Gradle invocation receives the entire property, rather than a spurious task.
- Runtime cancellation is propagated rather than converted to a clarification answer or
  failed review; already-written builder edits are preserved for inspection.
- Quiet observer waits honor the owning run's cancellation signal, including
  embedded read-only observation. A read-only observation never returns a standalone
  `pr-observer` continuation that could restore repair/publication authority.
- Concrete Detekt weighted-issue failures remain local validation failures, even if
  the same command also reports an infrastructure error.
- Slim review results retain the review summary and citations once in the result packet.
- Pre-builder file backups skip directories, including dirty nested submodules.
- Azure DevOps publication looks up an active PR matching both source and target
  branches before creating one, and checks again after a failed creation response.
  An absent matching PR leaves the publication error visible.
- Publication review binds the original HEAD, complete index tree, pinned comparison
  base, and final tree that staging the implementation will publish. A temporary index
  computes that tree without changing the caller's index. The reviewer receives the
  entire PR diff, including staged-only changes outside the builder's file list.
  Publication checks the binding before staging, before committing, and before pushing
  an explicit commit SHA. A retry may reuse the single commit made from that exact
  reviewed tree; unrelated commits or index changes require a new source review.
- Initial publication and tactical repair stage explicit allowed files, never a
  `git add -A` fallback selected by executor identity. No-change repair assessments
  create no commit. Root/nested `local.properties` additions and edits remain local;
  tracked deletions are allowed so an approved cleanup reaches the commit. An already
  staged SDK configuration addition/edit blocks publication explicitly without
  discarding the caller's index or local file, including a no-change assessment whose
  only indexed change is that SDK file. The index is checked before discovery and
  again after staging allowed repair files. Returned push-head evidence uses the
  supplied executor in both standalone and embedded observation.

### Verification and delivery receipts

- Builder/reviewer prose is not proof of command execution. Authored tests, source review,
  local command outcomes, and current-head CI outcomes are recorded separately.
- `verification[].local` records `unverified`, `deferred`, `failed`, or `passed`.
  Only successful deterministic verification commands establish a local pass. Compile
  directories alone never attest successful tests. The builder's local artifacts/prose
  cannot establish that every requested task passed.
- `verification[].ci` records status, observed PR source head, and concrete check/build
  evidence. ADO builds are bound to `system.pullRequest.sourceCommitId` in the build
  parameters, with the exact current merge commit as a fallback only when source parameters
  are absent. Policy-referenced builds missing from the latest-build list are fetched
  only for positive integer IDs. Zero, absent or malformed IDs cannot cause a build-zero
  lookup and leave required validation pending/unknown, never passed.
  Rejected/canceling validation for an older source cannot fail the new source, even if a
  provider's `buildIsNotCurrent` flag is false. A head/merge change during collection
  requires a fresh snapshot; stale policies, partial success and absent results cannot pass.
  Required build policies are identified by their policy type even before a build ID is
  assigned. Missing required build results remain pending with an observer continuation,
  even when another current-head build has succeeded; human approval policies stay separate.
  Stale required ADO build policies are refreshed once per durable observation key;
  queued validation is then observed without spending a code-repair iteration. Already
  running current-head validation is not requeued merely because an older policy lags.
- PR acceptance checkboxes are not automatically checked, and authored test counts are
  not reported as executed tests. The compact verification marker carries actual states.
- The observer updates the PR status/verification markers from its final snapshot, guarding
  against a concurrently changed head or description. Write failures are explicit.
- Failed/deferred local-verification summaries retain a diagnostic excerpt of at most
  500 characters. Recorded stopping/failure/defer causes take priority over long command
  narration, including a recorded 12-minute cap. Terminal evidence uses the same cause
  priority and bound, including when the local receipt predates a repaired head; full
  evidence remains unchanged in the workflow verification receipt. Repeated CI updates
  preserve that local reason without expanding the compact PR marker schema.
  Disabling source repairs is reported as disabled repairs,
  not as a spent budget or proof of a CI failure.
- ADO creation descriptions are budgeted to 3,500 UTF-16 characters, preserving required
  work-item/telemetry and JSON status markers and reserving room for final status updates.
  Oversized generated narrative is explicitly summarized with a receipt reference. Observer
  updates compact owned JSON but refuse to discard human-maintained narrative above the
  hard 4,000-character API limit.
- Same-run recovery refreshes the workflow's status/verification/observer dependencies by
  source revision, not just its entry module. Deferred-to-remote-CI builder handoffs do not
  trigger another local Gradle attempt or imply a local pass.
- Pure CI waiting and concrete failed tasks are handled deterministically before invoking
  a classifier. At most two tactical repairs occur; exhaustion retains exact remaining
  diagnostics and a continuation rather than an empty success-shaped packet.
- A current-head checkout-only connection reset/timeout with readable diagnostics
  requests one durable remote retry per source head before dispatching repair agents.
  It uses the existing CI observation bound, not a source-repair slot or new allowance.
  Checkout recovery is stage-only: only failed/canceled stages are retried, successful
  stages are untouched, and missing stage identifiers or any failed stage PATCH leave
  the retry unconfirmed for a pipeline-owner handoff. This caller never falls back to
  requeuing a whole build policy; the legacy fallback remains separate.
  Failures from all current-head non-review builds, including unfinished builds, are
  inspected before recovery, while retry targets remain restricted to failed builds. Disabled
  pushes or an absent wait allowance produce an explicit pipeline-owner handoff.
  Unconfirmed retries and repeated failures also hand off, rather than looping.
  Mixed compiler/test failures still use source repair; unreadable or stale diagnostics
  cannot authorize a transport retry. Actual tool cancellation propagates.
- Pending checks never bypass merge-conflict or actionable-review classification,
  including when feedback first appears during a CI poll. GitHub/GHE snapshots collect
  inline threads and Azure build diagnostics through the supplied executor, whether
  observing standalone or from `code-feature`; unavailable diagnostic reads stay explicit.
- Once the observer classifies existing feedback as waiting for CI, unchanged evidence
  reuses that decision through the existing wait bound, including the final observation.
  Edited comment/review bodies, review states, policies, diagnostics, conflicts, and
  source/merge/build bindings invalidate it. Collection timestamps and unordered check,
  build, policy and thread ordering do not. This in-memory decision cache is separate
  from durable milestone fingerprints; it adds no agents, polling or repair allowance.
- Hosted-image brownouts and provider-unavailable cancellations retain timeline evidence
  and return `infrastructure-blocked` without spending source/test-repair agents or
  modifying pipeline YAML, even if an external coverage status is pending. Retry requires
  provider recovery or an approved owner change. Successful CI does not hide merge conflicts.
- Snapshot collection is read-only. Timeline/log/test reads retain explicit diagnostic
  errors rather than silently dropping them. Internal Maven feed/plugin-resolution
  failures are configuration/dependency handoffs, not proven source defects; no source
  repair is assigned without actual current-head failure evidence. Genuine compiler or
  failing-test diagnostics take precedence over simultaneous feed errors.
- Standalone `pr-observer` honors an approved run-scoped `javaHome`; a local pass for an
  older commit is never promoted to a new CI-repair head.
- Pending CI is a handoff, not completion of the developer's readiness objective.
  `observations[].continuation` provides the `pr-observer` invocation to continue without
  rebuilding or creating another PR. Human-only approvals and merge policies are reported
  separately and must never be bypassed.
- Continuations carry the remaining repair allowance, including an explicit zero; they
  never silently restart two repair slots. An observer error with unknown allowance
  hands off observation-only work until its budget is verified.
- Mixed Gradle commands retain separate deferred receipts for lint and connected-device
  tasks removed from local execution. Successful compilation of the remaining tasks is
  partial verification (`deferred`), not a local pass for the original request.

### Owning-session completion delivery

The extension watches `workflow.run_started`, `workflow.run_updated`,
`workflow.run_settled`, and `session.resume`, and uses the SDK's `waitForRun` settlement
watcher. It sends a system-sourced, immediate follow-up to the owning session after a run
settles, with actual delivery/verification status, PR links, and remaining actions. A
completed workflow is never described as green solely because its body returned.

### Attached supervision during quiet workflow phases

The launcher calls `code_feature_workflow_wait` after a run starts. This is a real
attached tool wait on the existing run through the public SDK `workflow.waitForRun`,
not a status-poll loop or a new workflow. It returns on a directly delivered milestone,
attempt settlement, or a five-minute observation boundary. While work remains active,
the caller relays new progress and waits again on the same ID instead of declaring
launch complete and leaving the session idle for the entire CI wait.

`supervision.mjs` registers its milestone listener before reading status, cancels its
SDK watcher/timer when the call ends, and handles the caller's cancellation signal.
Cancelling a wait does not cancel/resume/restart the workflow. Paused/halted/error
outcomes remain explicit and preserve their recovery requirements. Completion of a
wait, a source-review round, or a workflow body is not a CI success claim.

This mitigates the desktop idle-reaper failure without changing the app: the observed
app unloaded quiet chat sessions while their extension-only workflows were still
waiting for CI. A native client-background-task registration was also tested and was
insufficient: the app still reaped its owner after ten minutes. That rejected approach
is not used. There are no heartbeat messages, dummy shells, extra model agents, or
global timeout changes. The tradeoff is that the owning caller must remain in attached
supervision; app exit, interrupted supervision, or host loss still requires recovery.
Already-loaded sessions need refreshed extension/skill instructions before adopting
this behavior. Do not restart an existing run or reset its budgets to install it.

A bounded native-app validation kept the production wait handler attached for
720,036 ms with a synthetic workflow provider, with no intervening chat/tool activity
and no main/session idle event during the call. It returned normally after twelve minutes,
past the observed ten-minute cleanup threshold. This proves the attached-tool lifecycle
mitigation on that runtime, not a complete feature/CI E2E or an app-panel repair.

### Phase and milestone updates in chat

The same owning-session delivery path now relays explicit durable milestone receipts
**while the workflow is running**, rather than waiting for its final result:

- Evidence/requirements validated; initial implementation or a revision handed off.
- Builder dispatched; implementation or a tactical repair in progress.
- Local verification completed, blocked, or deferred (with the actual verification state).
- Independent source-review rounds completed or returned findings.
- Each PR published, immediately with its link, even when another target later fails.
- PR handed to CI; tactical work assessed and reviewed, push completed; observation ended.

Entering a phase, routine tool logs, or `BUILD SUCCESSFUL` text alone never creates a
completed-phase notice. Source review and publication updates explicitly do not claim
tests/CI passed. Pending CI is reported once per head, not on every poll.

Chat uses concise, plain-language summaries, not raw outcome labels or workflow
transport diagnostics. Each update states the recorded fact and the relevant next
step; specific failures, deferred checks and required human actions remain explicit.
Local verification, source review, publication, exact-head CI and merge approval
are separate facts, but do not require a repeated disclaimer at every milestone.

| Recorded event | Example chat wording |
| --- | --- |
| Evidence validated | Evidence complete; implementation is next. |
| Builder dispatched | Implementation in progress. |
| Source authored | Source and test changes authored; verification results and independent source review follow. |
| Local command deferred | Local verification is incomplete; remaining checks need CI. Local build hit its time limit. |
| Source review passed | Independent source review passed; PR preparation is next. |
| Published with observation enabled | PR published. CI observation is next. |
| CI state observed | CI checks are queued. / CI checks are running. / Waiting for CI results for the current PR head. |
| Tactical assessment complete | Tactical assessment complete; local verification and independent review are next. |
| Push confirmed with unchanged source head | Push completed; the source head is unchanged. CI observation continues. |
| New source commit confirmed | Repair pushed; waiting for CI results for that commit. |
| External blocker | CI provider is unavailable; the provider or pipeline owner must resolve it before retrying. |
| Verified handoff | CI and observed PR checks passed; human review and merge approval are still required. |

Builder-start receipts are emitted only inside the existing journaled agent-call
producer, after invoking the builder. Replaying a cached builder step does not
announce a new start or launch another agent. Their unchanged `completed` outcome
records the dispatch event, not completion of implementation; chat uses the summary.
The same durable identity/outbox and cancellation rules apply to these receipts.
Delayed/replayed receipts are historical evidence, not proof work is still active.
Final handoffs name the next responsible owner and preserve diagnostics; continuation
arguments alone never claim an observer is running.
Tactical assessments may accept a no-change disposition. They do not automatically
mean source was authored; a completed push only claims a new repair commit when the
returned head differs from the observed pre-repair head. A no-op push still records
the actual push action, and an unavailable resulting head stays explicit.

Milestones are journaled by stable identity and pushed directly from the executing
workflow to its owning-session outbox. They do not wait for an ephemeral run-updated
event, a status read, or terminal settlement. The next stage waits for queue admission
(not a model response) for at most 10 seconds, so a synchronous local build cannot
starve the milestone handoff. An unavailable chat never blocks source work indefinitely:
its durable pending receipt and explicit diagnostic remain available for retry.

Durable progress is also read incrementally for catch-up. Missed events are reconciled
on the next lifecycle event/session resume; extension restart pages all unseen receipts.
Failed sends remain queued for retry, and duplicate/replayed events do not repeat already
acknowledged milestones. Per-message recorded/enqueued/admitted timestamps, returned
message ID, and independently observed system-message timestamp distinguish queue
admission from actual transcript delivery. The same at-least-once crash-window caveat applies as to final
outcomes. Progress-storage failure is surfaced but cannot hide a terminal blocker/result.
Milestone catch-up has a bounded admission wait so an unresolved send cannot prevent
observing settlement or persisting/sending a separate terminal outcome. The original
in-flight milestone send remains singular and its pending receipt is retained; a terminal
notice can therefore arrive before that delayed milestone. Worker retirement is atomic
with its final pending/dirty check so a racing settlement cannot join a finished worker.
The observation/admission window expiring is a **delay**, not a failed send or a workflow
error. Its internal delayed result is not a user-facing workflow status. Historical
window/elapsed timing is kept once per in-flight operation in the ignored local outbox's
`notificationDiagnostics`, with no chat message or session log at any severity. It never
writes a failed handoff into workflow progress or falsely acknowledges the receipt. Reconciliation
shares a single unfinished progress read across retries. A genuine read/send/storage rejection,
including one arriving after the bounded wait, still follows the error-reporting and retry
path. Pending receipts remain durable and are acknowledged only after actual admission.
The registered workflow also runs Git/Azure CLI/GitHub CLI prerequisite probes
asynchronously. Previously these synchronous subprocess waits blocked the same extension
loop that receives SDK replies and wakes attached supervision, so a quickly admitted
milestone could not be processed until credential checks finished. The async and sync
entrypoints share the same validation rules and account selection. Async probes preserve
the run-scoped environment, have bounded subprocess execution, and propagate cancellation;
they do not spawn model agents or install anything. This removes that owned startup
blocker, not every possible host/chat queue delay or all synchronous work elsewhere.
Caller instructions require a short visible chat update for each receipt, without starting
a status-poll loop or restarting the still-running workflow.
The registered-extension integration test runs the real `code-feature` handler with
synthetic model outputs and no progress invalidation events, verifying that all phase
notices are admitted before settlement and that a compatible installed JDK needs no prompt.
The same integration path verifies that attached supervision receives direct milestones
even when completion-delivery initialization fails because the outbox cannot be read.

The ignored outbox is scoped by `SHA-256(session.sessionId)` under
`.github/local-context/code-feature/completion/`; it catches up settled runs after extension
restart and deduplicates lifecycle events. Failed delivery is retained for retry. This is
at-least-once queue admission, not a guarantee that an unavailable client displayed a reply;
crash-window duplicates remain possible. No cross-session runs or credentials are stored.

Windows readers and scanners can briefly deny atomic replacement of an existing
outbox snapshot (`EPERM`, `EACCES`, or `EBUSY`). Persistence retries these failures
asynchronously with bounded backoff, without blocking SDK replies or changing the
workflow/admission wait windows. All outbox commits are serialized across runs:
the previous snapshot remains authoritative until the replacement succeeds, and
no notice is sent before its pending receipt is durable. Exhausted retries remain
explicit errors; they never become false delivery acknowledgements. Flush operations
also wait for queued persistence. External diagnostic readers should allow delete
sharing on Windows and close their handles before waiting for another file event.
Disposal during a pending terminal-notice commit prevents the subsequent send; the
committed notice remains pending for a later delivery instance.

Notification failures are coalesced by operation and underlying cause, not Error-object
identity. The shared outbox has one storage incident across runs; reads and sends retain
their run/operation scope. A continuing incident produces one concise informational
notice rather than repeated session-error banners. A changed cause produces another
notice, and success of the affected operation produces one recovery notice. Unrelated
successful reads, writes or deliveries do not clear it. This coalescing is process-local:
an extension restart can report an existing problem again. It does not deduplicate actual
workflow failures, change terminal outcomes, or claim that a message was rendered.

`code_feature_workflow_pause` exposes the documented SDK pause from the joined owner
session, outside the workflow body. It checks run identity, workflow name and state,
applies normal tool permission gating, and confirms the settled envelope and subsequent
live-agent evidence. Confirmation requires an explicit numeric zero live-agent count;
missing, null or malformed counts are unconfirmed, not a successful stop receipt.
It cannot select another session, resume, replace, requeue, merge
or change limits. Already-settled attempts are read without applying a control.
Use it only for a requested stop or an authorized stop-on-error policy. User-initiated
pause receipts do not fabricate prerequisite questions. A failed or unconfirmed pause
is explicit, never a success-shaped stop claim.

Full notification-error details and repeat counts stay in the ignored
`completion/diagnostics/<session-hash>.jsonl`, independently of the atomic outbox snapshot.
The log rotates at 1 MiB and retains one `.previous` file. Diagnostic-write failures are
explicit, with coalesced extension-log fallback rather than recursive session logging.
Ordinary admission-window delays remain silent and are not notification incidents.

On recovery, fast-forward the workflow source only after the current attempt settles,
then resume the same run with unchanged approved task inputs. Verify that cached
evidence/build/review steps are reused rather than starting another implementation.
The final publication-ready implementation, review history, verification and source-content
bindings are journaled before publication. An unchanged publication retry bypasses all
earlier source rounds, including a successful source-changing fix; changed reviewed content
requires a new review and is not overwritten. Source bindings include present/missing
state and the bytes on disk, including files marked for deletion. Removing a reviewed
file or recreating a reviewed deletion cannot reuse the old approval, even when the
cached content or recreated file is empty. Publishing also requires the complete Git
tree/index binding described above. Older bindings missing either guard fail closed;
they are not silently upgraded or re-reviewed on replay.
Normalized metadata and a committed
source-branch diff preserve slim file/test lists. Local gate/review receipts are content-bound,
not dirty-status-bound. Existing task commits are preserved and empty commits are skipped.
An authored workflow with the same name can be shadowed by extension registration;
verify the selected definition and an explicit fixture marker before treating a synthetic
run as test evidence. Host-injected terminal inspection is not a manual user wake.

Use the read-only `code_feature_workflow_status` tool for an existing run's current phase,
recent progress, accounting, or settled result, rather than inferring status from a background
agent list or parent-session idleness. It exposes SDK `consumed` units without inventing a
credit conversion, direct agent counts, and a separately labeled `lastObservedPhase` when
only durable progress is available. That fallback is not a live-phase assertion. The tool
does not resume/restart the run.

`code-feature` supports **three everyday input modes** (plus pre-structured `task`/`stage0` objects):

### 1. Design Doc (`designDocPath`, `designDoc`, or `designPacket`)
Pass a path to a design doc (most common when a design already exists), raw markdown, or
the output packet from `design-feature`:

```json
{
  "designDocPath": "design-docs/[Android] Broker Throttling.md",
  "workItemId": 3802484,
  "options": {
    "createPullRequests": true
  }
}
```

### 2. Azure DevOps PBI (`pbiId`, `pbiIds`, `pbi`, or `pbis`)
Pass one or more PBI work-item IDs (fetched automatically via `az boards work-item show`)
or inline PBI objects:

```json
{
  "pbiIds": [3802484, 3802485],
  "options": {
    "createPullRequests": true
  }
}
```

### 3. Free-form Prompt (`prompt`)
Pass a plain-language request (optionally with `repository` and `workItemId`):

```json
{
  "prompt": "Add server-directed Retry-After throttling to Common for 429/503 responses (AB#3802484)",
  "repository": "common",
  "options": {
    "createPullRequests": true
  }
}
```

### How details and clarifications are handled
- For **Design Doc**, **PBI**, and **Free-form Prompt** inputs, the workflow automatically
  reads the design doc / PBI details, inspects the target repositories, and extracts concrete
  requirements (`R1`, `R2`, ...) and per-repository targets.
- It infers technical details from the design doc, PBI description, and codebase conventions
  without interrupting the engineer.
- **Only when requirements are genuinely missing, contradictory, or ambiguous** (cannot be
  determined from the design doc, PBI, or codebase), the workflow asks the user for
  clarification via session elicitation (or returns `recommendation: "needs clarification"`
  with `questions: [...]`).

### Options

- `javaHome`: Optional, explicitly approved run-scoped JDK installation path. It is
  validated against each target's requirements and never changes the host environment.
  Supply it for unattended execution when the developer has already selected a JDK;
  otherwise use the prerequisite approval/recovery flow.
- `maxFixIterations` (default `2`, max `2`): Maximum builder fix iterations after initial build.
- `createPullRequests` (default `true` in live Copilot sessions, `false` in offline unit tests):
  When `true`, writes verified files to the target repository checkout, creates branch
  `<alias>/ab-<workItemId>-<slug>` (or `users/<alias>/...` in ADO), commits with the Copilot
  trailer, pushes to `origin`, and opens the PR via `gh pr create` (GitHub/GHE) or
  `az repos pr create` (Azure DevOps `authenticator` with `WiClose` and `pr-telemetry` footer).
  Set `createPullRequests: false` for dry-run packet generation without pushing.
- `draftPullRequest` (default `false`): Passes `--draft` to `gh pr create` when creating PRs.
- `iterativeIntake` (default `false`): Re-invokes `code-evidence` with recorded user answers
  for up to 3 intake rounds so the extracted specification incorporates clarifications cleanly.
- `requireBriefApproval` (default `false`): Presents the extracted brief for explicit
  `start` / `refine` / `cancel` confirmation before running `code-builder`.
- `runLocalVerification` (default `true` when `applyToWorkspace` or on-disk target edits exist):
  Runs target `verificationCommands` with automatic `local.properties` (`sdk.dir`), `JAVA_HOME`,
  and Azure CLI VSTS token provisioning before `code-reviewer`; compiler/test failures feed
  directly into `code-builder` (`fix-1..3`), while missing local credentials defer cleanly to CI.
- `pauseOnBlocker` (default enabled when the runtime exposes `ctx.pause`): Invokes
  `await ctx.pause(key)` on unresolved blockers. Resume starts a fresh attempt of the same
  `runId`; it does not supply new answers or environment variables.
- `builderModel` / `reviewerModel` / `evidenceModel`: Optional model overrides so the
  independent reviewer can run on a distinct model from the builder.
- `slimSchemas` (default `true` in live sessions): Uses compact `{ verdict, summary, evidence }`
  agent schemas and discovers changed files and tests directly from `git status --porcelain` in
  the target worktree.

## Companion workflow: `pr-observer`

`.github/extensions/CodeFeature/extension.mjs` also registers **`pr-observer`**
(`.github/extensions/CodeFeature/observer.mjs`) alongside `code-feature`:

```json
{
  "prUrl": "https://msft.ghe.com/security/ad-accounts-for-android/pull/362"
}
```

`pr-observer` inspects a published Android Auth PR's CI checks, merge state, and review threads
(`Observe`), runs `code-builder` + `code-reviewer` to apply and verify tactical repairs (`Repair`
$\rightarrow$ `Verify & Push`), pushes fixes to the PR head branch using the repository's GitHub
account (`shahzaibj` or `shjameel`), and stops with `status: "ready-for-human-merge"` once all
checks are green and review threads are resolved.

## Repository Skills Leveraged

Each workflow agent loads the repository's existing skill and instruction files:

- **`code-evidence` (Evidence & Spec Extraction)**:
  - `.github/skills/codebase-researcher/SKILL.md` and `references/search-patterns.md`
  - `.github/skills/code-author/SKILL.md` and `references/implementation-standards.md`
  - `.github/skills/feature-planner/SKILL.md` and `references/pbi-template.md`
- **`code-builder` (Implementation & Unit Tests)**:
  - `.github/skills/code-author/SKILL.md` and `references/implementation-standards.md`
  - `.github/copilot-instructions.md` and target repo `.github/copilot-instructions.md` (`common`, `broker`, `msal`, `adal`)
  - `.github/skills/feature-planner/references/pbi-template.md`
- **`code-reviewer` (Independent Code Review)**:
  - `.github/skills/code-author/SKILL.md` and `references/implementation-standards.md`
  - `common/.github/skills/code-review/SKILL.md` + `references/` (`correctness-api-compatibility.md`, `concurrency-threading.md`, `security-privacy-crypto.md`, `performance-resources-dependencies.md`, `telemetry-observability.md`, `testing-documentation.md`, `comment-quality-severity.md`, `false-positive-suppression.md`)
  - `broker/.github/skills/code-review/SKILL.md` + `references/` (`correctness-api-ipc.md`, `concurrency-threading.md`, `security-privacy-crypto.md`, `performance-resources-dependencies.md`, `telemetry-observability.md`, `testing-documentation.md`, `comment-quality-severity.md`, `false-positive-suppression.md`)
- **PR Publishing**:
  - `.github/skills/pbi-dispatcher/SKILL.md` (GitHub account discovery via `.github/developer-local.json` and `gh auth status`, repository routing, and `Fixes AB#<id>` linking)

## Fixed Code Review Rubric

1. `approvedScopeAndRequirements`
2. `architectureAndApiCompatibility`
3. `correctnessAndEdgeCases`
4. `concurrencyAndThreadSafety`
5. `securityPrivacyAndLogging`
6. `performanceAndResourceBounds`
7. `telemetryAndObservability`
8. `unitTestsAndVerification`
9. `prReadinessAndTraceability`

## Bounds, safety, and resume

- Sequential journaled `ctx.step` and `ctx.agent` calls:
  1 evidence worker + 1 initial builder + up to 4 independent reviews and 3 builder fix
  rounds (9 agents maximum).
- `code-reviewer` and `code-evidence` are registered with read-only tools (`view`, `glob`,
  `grep`) and never receive the builder's chain-of-thought reasoning.
- If workspace verification or review fails after `maxFixIterations`, any modified workspace
  files are restored from backup and no PR is created (`recommendation: "NO-GO"`).
- Prerequisite answers accepted in the workflow UI are journaled and reapplied on replay.
  A resumed prerequisite checkpoint rechecks the environment before continuing; it never
  returns a stale failure after successful recovery.
- A pause records the exact missing-input questions and recovery path. Its owning-session
  outcome explicitly requires an actionable `ask_user` question, not a status-only reply;
  the read-only status tool exposes the structured `pendingInput`. Ask one question at a
  time, preserve any existing input identity, and confirm the same run remains paused.
  Apply the actual answer before a same-run resume. No answer implicitly authorizes a
  global toolchain change, software installation, a new implementation, or a repair-budget reset.
- If the caller must collect a JDK approval outside the workflow UI (including Autopilot),
  the pause log identifies an ignored, workspace-local file:
  `.github/local-context/code-feature/<runId>.json`. After explicit user approval, the caller
  writes `{"runId":"<same runId>","javaHome":"<approved JDK directory>"}` there, then resumes
  the same run. Use JSON-escaped backslashes on Windows. The workflow validates the run ID
  and JDK against every target before using it. No credentials or arbitrary environment
  overrides are accepted; the host's `JAVA_HOME` remains unchanged.
- Resuming without fixing the prerequisite does not grant approval or bypass checks:
  at most two recovery checkpoints are attempted, then the run returns `needs clarification`.
- A runtime error marked **not resumable** is not a successful or recoverable workflow
  result. Preserve the target checkout and available journal evidence, report the last
  durable stage, and obtain approval before starting a replacement run. An idle parent
  session alone says nothing about workflow completion.

## Checks

Run from the repository root (Node.js 18 or newer):

```powershell
node --check .github\extensions\CodeFeature\extension.mjs
node --check .github\extensions\CodeFeature\workflow.mjs
node --test .github\extensions\CodeFeature\*.test.mjs
```
