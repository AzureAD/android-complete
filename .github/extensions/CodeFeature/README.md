<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android coding and PR workflow (`code-feature`)

`code-feature` is a standalone, repository-scoped **Copilot Dynamic Workflow** modeled
after `.github/extensions/DevDesignDoc` (`design-feature`). A compatible Copilot runtime
discovers `.github/extensions/CodeFeature/extension.mjs` and registers the `code-feature`
workflow along with role-scoped custom agents (`code-evidence`, `code-builder`, and
`code-reviewer`).

## Goal

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
  invalid `JAVA_HOME` is reported with its path and detected version; the workflow offers
  any discovered compatible JDK for this run, or accepts another compatible path. The
  selected JDK is passed to the builder and local verification without changing the
  host's global environment.
- PowerShell builder examples quote `'-Pkotlin.compiler.execution.strategy=in-process'`
  so the native Gradle invocation receives the entire property, rather than a spurious task.
- Runtime cancellation is propagated rather than converted to a clarification answer or
  failed review; already-written builder edits are preserved for inspection.
- Concrete Detekt weighted-issue failures remain local validation failures, even if
  the same command also reports an infrastructure error.
- Slim review results retain the review summary and citations once in the result packet.
- Pre-builder file backups skip directories, including dirty nested submodules.
- Azure DevOps publication looks up an active PR matching both source and target
  branches before creating one, and checks again after a failed creation response.
  An absent matching PR leaves the publication error visible.

### Verification and delivery receipts

- Builder/reviewer prose is not proof of command execution. Authored tests, source review,
  local command outcomes, and current-head CI outcomes are recorded separately.
- `verification[].local` records `unverified`, `deferred`, `failed`, or `passed`.
  Only successful deterministic verification commands establish a local pass. Compile
  directories alone never attest successful tests. The builder's local artifacts/prose
  cannot establish that every requested task passed.
- `verification[].ci` records status, observed PR source head, and concrete check/build
  evidence. ADO build evidence is filtered to the current PR merge commit; stale build
  policies, partial success, failed checks, and absent results cannot produce a pass.
  Stale required ADO build policies are refreshed once per durable observation key;
  queued validation is then observed without spending a code-repair iteration.
- PR acceptance checkboxes are not automatically checked, and authored test counts are
  not reported as executed tests. The compact verification marker carries actual states.
- The observer updates the PR status/verification markers from its final snapshot, guarding
  against a concurrently changed head or description. Write failures are explicit.
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
- Hosted-image brownouts and provider-unavailable cancellations retain timeline evidence
  and return `infrastructure-blocked` without spending source/test-repair agents or
  modifying pipeline YAML. Retry requires provider recovery or an approved owner change.
- Pending CI is a handoff, not completion of the developer's readiness objective.
  `observations[].continuation` provides the `pr-observer` invocation to continue without
  rebuilding or creating another PR. Human-only approvals and merge policies are reported
  separately and must never be bypassed.

### Owning-session completion delivery

The extension watches `workflow.run_started`, `workflow.run_updated`,
`workflow.run_settled`, and `session.resume`, and uses the SDK's `waitForRun` settlement
watcher. It sends a system-sourced, immediate follow-up to the owning session after a run
settles, with actual delivery/verification status, PR links, and remaining actions. A
completed workflow is never described as green solely because its body returned.

The ignored outbox is scoped by `SHA-256(session.sessionId)` under
`.github/local-context/code-feature/completion/`; it catches up settled runs after extension
restart and deduplicates lifecycle events. Failed delivery is retained for retry. This is
at-least-once queue admission, not a guarantee that an unavailable client displayed a reply;
crash-window duplicates remain possible. No cross-session runs or credentials are stored.

On recovery, fast-forward the workflow source only after the current attempt settles,
then resume the same run with unchanged approved task inputs. Verify that cached
evidence/build/review steps are reused rather than starting another implementation.
Normalized implementation metadata is journaled before publication commits; a committed
source-branch diff also reconstructs slim file/test metadata when upgrading an older run.
Local gate and review receipts are bound to implementation-file content rather than
dirty-worktree status. Existing committed task branches are preserved, not reset to base,
and publication skips commits when its staged file set is empty.
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
node --test .github\extensions\CodeFeature\workflow.test.mjs
```
