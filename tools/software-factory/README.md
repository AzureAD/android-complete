<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory

The same feature-development application is available from **Copilot app chat,
interactive Copilot CLI chat, and an optional local web UI**. All three use a
transactional local registry, the same domain commands and approval scopes, and
**native Copilot Dynamic Workflows**. There is no separate prompt-driven workflow
engine behind the dashboard.

## What is connected

| Workflow | Responsibility |
|---|---|
| `software-factory-intake` | Turns a saved plain-language brief into draft success criteria and open questions for human review; never edits or approves requirements. |
| `software-factory-design` | Reuses the existing evidence/traceability/independent-review design algorithm, then pauses for genuine human design approval. |
| `software-factory-lifecycle` | Independently reviewed planning, plan/backlog/dispatch checkpoints, recoverable ADO creation/linking, and dependency-ready coding-agent waves. |
| `software-factory-monitor` | One bounded GitHub/GHE/ADO refresh and acceptance-criteria validation pass; no posting or merging. |
| `software-factory-check` | Native registration, journaling, checkpoint, and cold-resume diagnostics with zero agents. |

The standalone `design-feature` workflow remains unchanged. Its Software Factory
wrapper preserves the existing seven-agent / two-fix bound, one concurrent agent,
and 1,800-second active-time cap, but deliberately does **not** inherit its
20-credit limit. **Software Factory has no default AI-credit cap.** New runs
explicitly request an unlimited credit dimension unless the user supplies a
ceiling; removing the cap does not make execution free or bypass human gates.
Optional user-requested credit ceilings remain soft post-paid limits. Remote
coding tasks have separate usage.
Run observations preserve native active time, subagent count, AI usage in
reported nano-AIU, effective resource ceilings, and requested/resolved agent
models. The same measurements are available in chat and the UI; missing or failed
observations remain explicit rather than displaying zero cost.

### Session ownership and native chat execution

New, unbound features started in app/interactive CLI chat use the extension's
joined native session. Named Factory workers expose only scoped read/find/search
tools; actual reads still pass through the native host's permission/content-policy
pipeline. Normal chat tools and permissions are not replaced with the workers'
read-only policy. Native phases and completion/failure notifications are enabled
in that same chat; usage snapshots also reach the dashboard while work runs.

The feature records a canonical session binding across phase executions.
Dashboard starts/controls for a chat-bound feature route to its resident owner,
without creating another SDK session or resuming the chat through a second client.
Owner leases for both chat and background sessions, plus one-time admission
claims, reject competing controllers/starts. A second dashboard process queues
to the resident background owner instead of resuming its session in parallel.
An uncertain admission without a confirmed native run ID must be inspected and
explicitly superseded/reconciled before another paid submission.
Offline owners are shown explicitly; reconnect the original chat rather than
silently transferring its journals. Genuine approvals and source/version guards
still apply to every protected action.

Legacy executions retain their original owning session and history. They are
not automatically migrated or retried. Standalone UI/CLI starts without a binding
still use an owned SDK session; app discovery of those sessions remains unverified.

Lifecycle-state JSON now uses version 2. Version-1 records are read compatibly and
upgraded on their next write, without rewriting immutable requirements/approvals.
Old loaded integrations must be reloaded; their version-1 reader refuses new
ownership state rather than accidentally taking over a chat run. Backup clones
clear session bindings/resident leases as well as schedules and pending controls.

### Required cross-interface continuation

A feature must be continuable in either direction: dashboard/standalone-CLI
sessions must appear in the Copilot app, and app/interactive-CLI features must be
continuable from the dashboard. This means the same feature, session/run binding,
approvals, artifacts, checkpoints and workspace, not a new conversation with a
copied prompt. Multiple clients must not become competing execution owners.

This is **not implemented end to end** by sharing the registry. Chat-native
execution and dashboard control of that resident run have a real zero-agent
integration check, including original-session/original-journal resume and native
read delegation. The installed SDK
provides experimental authenticated AHP hosting and explicit session publication;
the publication/catalog regression is isolated and starts no agents. Actual
desktop discovery, attachment, live worker visibility and cross-interface controls
still require integration acceptance. The observed app settings did not expose a
generic SDK-host connection; a failing WSL discovery page is not evidence that
Factory needs WSL or that such attachment is impossible. No settings are changed
to work around that unverified path. An SDK session ID is not an app-local
session ID; app records and links must come from supported host APIs.

Source is implemented and covered by deterministic workflow/provider/transport
tests. Native registration, a real checkpoint, and cold resume are exercised by
`check:runtime` without starting agents. Paid model quality, live provider writes,
and individual account/onboarding permissions still require an approved pilot;
offline tests are not evidence that a specific account can dispatch a real task.

## Install: chat only by default

Requirements: Node.js 24+ and npm. The declared Copilot SDK includes its supported
runtime; authentication uses the developer's existing Copilot account.

From the repository root:

```powershell
.\tools\software-factory\install.ps1
```

This installs/checks the core, not the frontend. It does not change profiles,
create branches/worktrees, authenticate accounts, or run paid agents.
Setup explicitly includes npm optional dependencies and verifies the SDK's
matching platform runtime and native files before reporting success. These
optional packages are required for Software Factory execution, even though npm
permits omitting them. **Check setup** / `node .\tools\software-factory\cli.ts doctor`
reports missing/incomplete installations without launching agents.
Doctor reports the selected `sdkRoot`. Diagnostics, the owned client, and its
extension runtime use this project's installed SDK and its declared public
exports. Copilot app hosts may redirect bare SDK imports to their bundled SDK;
that registration SDK is deliberately not used to locate or launch Factory's
owned runtime. An error naming the desktop application's root is not evidence
that a project runtime package is missing.

Owned execution also pins the SDK's validated native runtime executable and an
explicit child-process transport; inherited runtime-path/transport overrides
cannot silently redirect it. Only the documented parent-extension bindings
(`SESSION_ID`, `COPILOT_SDK_PATH`, `COPILOT_EXTENSION_PARENT_PID`) are removed from
the child's environment. Other environment, authentication, user and security
settings are preserved, and the parent environment is never mutated.

The extension interpreter is separate from both the app host and the SDK native
runtime. A packaged host may report a compatible Node API while
`process.execPath` names `copilot-runtime.exe`, not `node.exe`. Factory therefore
probes `node` on PATH, verifies Node 24+ and its real executable path, and uses
that interpreter to launch the canonical extension. It never assumes the host
executable can run a JavaScript file. Missing/old Node produces a specific
interpreter error, not advice to reinstall the Copilot SDK. Run details record
the host, selected Node and native-runtime executables; the successful
registration receipt also reports the interpreter actually running the extension.

Open a **new trusted repository session** in the Copilot app or CLI. For CLI,
start `copilot --experimental` in the repository. Select `software-factory`, use
the compatibility `feature-orchestrator` agent, or ask for Software Factory tools.
The project extension supplies its workflows, tools, and CLI slash commands.
Restart the owning host after editing workflow implementation or updating SDK
dependencies; do not hot-resume old code against new journals.

```text
Help me capture this feature idea, then propose success criteria for my review.
Show the full design gate and its evidence.
Approve only the exact plan I reviewed, not backlog or dispatch.
Resume this feature's owning native run.
Show stale provider observations and uncertain operations.
```

### Start with an idea, not a requirements format

Describe the problem, who it affects, and anything that must stay unchanged.
Save a title and plain-language brief first; success criteria can be empty while
the feature is a draft. Saving does not run agents or grant approval.

**Success criteria** means "what does done look like?" They need to be checkable,
not necessarily numerical. Enter plain text or bullets; IDs are assigned
automatically. Pasted `R1: ...`, the original `R1 | ...` format, and indented
continuation lines remain supported. Existing unchanged outcomes retain their IDs.

In the UI, **Draft success criteria with Factory** explicitly starts the native
intake workflow after confirmation of model usage. It saves a proposal, not new
requirements. Choose **Review and edit suggestions**, make any changes, then
save the revision. **Approve for design** remains a separate human decision;
missing success criteria or unresolved scope decisions block it.

The same flow works in chat: save `input.description` with `requirements: []`,
preview/start `start_workflow` with `workflow: "intake"` when help is wanted,
review the complete proposal from `software_factory_get_feature`, then use
`revise_requirements` to save the chosen criteria. Criterion objects may omit
`id`; the core assigns it. Do not treat a generated proposal, saving, or native
workflow completion as approval. Existing questions remain in proposals unless
their exact text is explicitly proposed as design work for review.

**Scope decisions and design work are different.** Scope decisions establish
what to build (for example, which caller families are in the first rollout).
`blockingUnknowns` contains those unanswered choices. `designTasks` carries
engineering obligations such as tracing IPC compatibility, propagating request
context through shared helpers, and designing cache capacity/overflow behavior.
Design tasks do not block scope approval or design startup: solving them is the
design agents' job, and independent design review must assess the resulting
solution. Hard constraints and unresolved safety/compatibility issues are never
waived by moving a note into this field.

When continuing an older unapproved draft, Factory should reclassify technical
blockers into design tasks in a new revision, preserving the obligations, then
present the design-ready scope for genuine approval. It must not demand that
the engineer supply a completed design before the design workflow can run.

### Tool and command entry points

- `software_factory_report_progress`: save the current conversational stage,
  what is being investigated, expected deliverable and next step. A pending
  question includes its rationale and recommendation. This updates shared
  status only; it cannot start a workflow, edit criteria, approve or advance gates.
- `software_factory_decide_after_review`: show the next real decision after
  the agent has displayed a `review_required` result. Never interprets viewing
  the review as approval.
- `software_factory_doctor`: actual host confirmation capability and runtime guidance.
- `software_factory_list_features` / `software_factory_get_feature`: complete
  feature state, immutable artifacts, approvals, receipts, run ownership and history.
- `software_factory_create_feature`, `software_factory_revise_requirements`,
  `software_factory_approve_requirements`: shared brief, success-criteria and
  explicit approval tools; existing Stage 0 contracts remain compatible.
- `software_factory_configuration` / `software_factory_get_gates`: exact target
  mappings and approval previews, with explicit blockers.
- `software_factory_preview` / `software_factory_command`: the full shared
  domain-command surface below.

Native host confirmation is used for approvals, paid starts/resumes, configuration,
remote-effect authority, and recovery attestations. Model-supplied approval fields
or quoted chat text do not constitute consent.

Confirmations distinguish the decision being made: **Approve feature scope**,
**Start design work**, **Authorize PBI creation**, and **Authorize coding tasks**
are not interchangeable. The main prompt names the feature and readable revision,
states the effect and exclusions, and does not dump UUIDs, hashes or raw JSON.
Exact machine scope/provenance checks remain unchanged behind the presentation.

On hosts supporting the SDK's structured elicitation API, the form uses a
**Your decision** choice with a non-approving default, an explicit action label,
**Review full details in chat**, and **Not now**. Requesting review returns the
complete readable content without granting approval; the agent presents it in
ordinary chat and immediately follows the returned `nextAction` to present
**Approve / Request changes / Decide later** for that reviewed scope. The user
does not need to discover or type a trigger phrase. The app still owns the surrounding
Accept/Decline/Cancel buttons. Older confirm-only adapters use a full plain-text
review and the host's generic confirmation UI; headless hosts remain blocked
unless the user invokes the documented explicit slash-confirm path.

An already-approved unchanged requirements revision or stage scope is reported as
`already_approved` without another confirmation or a new receipt. Scope changes
still need new approval. Web confirmations use the same decision descriptions,
with full content and technical references behind separate review disclosures.

The post-review handoff uses `software_factory_decide_after_review`. Its
same-session review ID only identifies the stored command/scope; it is not an
approval token. A changed scope requires a new review, and every affirmative
action still comes from a genuine host form. Request changes opens a feedback
form and returns the request for an unapproved revision; it does not approve or
start work. Decide later/Cancel is an intentional pause, not another prompt loop.
Successful scope/stage approvals return an explicit next-decision handoff where
applicable, rather than leaving the user to guess what to type.

If a chat host lacks elicitation, stay in chat using the supported **user-invoked**
slash command:

```text
/software-factory list
/software-factory get <feature-id>
/software-factory preview {"kind":"approve_requirements","featureId":"<id>","expectedRevision":"<revision>"}
/software-factory confirm <hash-from-preview> <the-same-JSON-command>
```

The explicit confirm handler receives a native user command and checks the exact
current scope hash. Agents must never execute that confirm command on the user's
behalf. It is not a consent parameter accepted by `software_factory_command`.

The familiar `/feature-design`, `/feature-plan`, `/feature-backlog`,
`/feature-dispatch`, `/feature-status`, `/feature-continue`, and
`/feature-pr-iterate` entries route into this shared core. They do not invoke
legacy state writers or coordinate phase agents themselves.

## Configure targets, not credentials

Use **Connections** in the web UI or the `configure` command in chat. Repository
provider/hostname/account/branch are explicit. Board metadata may remain empty
for repository-only analysis; backlog is blocked until required fields are known.
For example, replace these placeholders with verified local mappings:

```json
{
  "kind": "configure",
  "configuration": {
    "repositories": {
      "common": {
        "provider": "github",
        "hostname": "github.com",
        "slug": "YOUR_ORG/YOUR_REPO",
        "baseBranch": "CONFIRMED_BASE",
        "account": "YOUR_AUTHENTICATED_ACCOUNT",
        "localPath": "common"
      }
    },
    "boards": {
      "organization": "",
      "project": "",
      "workItemType": "Product Backlog Item",
      "areaPath": "",
      "iterationPath": "",
      "assignee": "",
      "parentId": null
    }
  }
}
```

For GHE, use its actual hostname, not a GitHub.com organization alias. ADO
repositories use provider `ado`, hostname `dev.azure.com`, and an
`organization/project/repository` slug. Their hosting project can differ from
the ADO project holding the backlog.

GitHub/GHE adapters require `gh` with `agent-task create -F -`, authenticated
accounts on each host, and coding-agent enablement. They select each account's
credential per child request, never switch the global active account.

The standalone ADO adapter uses the existing `az` account to obtain an ADO access
token and call documented REST endpoints. It does not assume that this app owns
another conversation's scoped MCP connection or OAuth grants. The `Provider`
boundary allows a host-specific MCP implementation without changing approval or
receipt semantics; agent-side ADO tasks should continue preferring configured MCP.
No token is stored in configuration, artifacts, URLs, or logs.

Use `preflight` / **Verify provider connections** to verify repository access,
configured branches and work item metadata without creating work.

## Lifecycle

1. Save a plain-language feature brief. Add success criteria yourself or
   explicitly run the optional `intake` workflow and review/edit its proposal.
   Save the chosen criteria, resolve open questions, then approve the exact
   revision for design. No IDs or special input syntax are required.
2. Explicitly start the `design` workflow. Review the complete design packet,
   sources, traceability and independent findings. NO-GO never advances.
3. Approve the `design` gate, then resume its owning design run to finish it.
4. Start `lifecycle`. Review the generated repo-targeted PBI plan and approve
   the `plan` gate. This is not permission to create remote work.
5. Review and approve the separate `backlog` scope, including board settings and
   dependency/parent links. Resume the native run.
6. Review and approve the exact `dispatch` work set. Resume to create only those
   coding tasks; dependency PRs must actually be merged.
7. Start a bounded `monitor` pass. Missing PR links, failed reads/checks, and
   unknown acceptance criteria remain visible.
8. When dependencies merge, approve a fresh dispatch wave and start a successor
   lifecycle run. It reuses the approved plan and completed operation receipts.
9. Review completion evidence. Reasoned human validation waivers bind to the
   exact current monitor; they cannot waive unmerged PRs or failed delivery.
   Completion is explicit and rechecks provider delivery state.

Native Resume alone is never approval. Repeated unapproved resumes pause at new
durable checkpoints rather than passing a one-shot gate.

### Shared domain commands

Use `software_factory_preview` first, then `software_factory_command`, or the UI.
All JSON commands include a `kind`:

| Kind | Additional fields |
|---|---|
| `report_progress` | `featureId`, `expectedRevision`, stable `requestId`, `progress` (`stage`, `status`, `summary`, `expectedOutput`, `nextStep`; waiting status also requires `question` with `text`, `whyNeeded`, `recommendation`) |
| `configure` | `configuration` |
| `start_workflow` | `featureId`, `workflow` (`intake`, `design`, `lifecycle`, `monitor`), optional `limits` |
| `approve_stage` | `featureId`, `stage`, `expectedScopeHash`, `approved` |
| `request_revision` | `featureId`, `stage` (`design` or `plan`), `expectedArtifactId`, `feedback` |
| `control_run` | `featureId`, `executionId`, `action` (`refresh`, `pause`, `resume`, `cancel`), optional `limits` |
| `supersede_execution` | `featureId`, `executionId`, `reason`; explicitly revoke unrecoverable local authority without claiming native cancellation |
| `reconcile_operation` | `featureId`, `operationKey` |
| `confirm_operation_not_sent` | `featureId`, `operationKey`, `reason`; explicit risky operator attestation, never automatic retry |
| `attach_pr` | `featureId`, `itemId`, `number`; verifies target, base, and actual work-item link |
| `review_pr` | `featureId`, `itemId`; reads complete reviews/comments without posting |
| `post_pr_feedback` | `featureId`, `itemId`, `requestId`, `body`; exact-head/body confirmation, stable retry ID |
| `accept_validation_gap` | `featureId`, `monitorId`, `itemId`, `criterion`, `reason` |
| `set_schedule` | `featureId`, `minutes` (positive integer or `null` to disable) |
| `preview_import` | `path` to legacy JSON |
| `import_legacy` | `path`, `expectedHash`, `mappings` of `{legacyId,input}` with explicit requirements |
| `export_feature` | `featureId` |
| `cancel_feature` | `featureId`; remote tasks/artifacts are not deleted or cancelled |
| `preflight` | no additional fields |
| `backup_registry` | no additional fields; creates a consistent private SQLite snapshot |
| `inspect_backup` | `path`; verifies integrity, schema, and content hash |
| `restore_backup_clone` | `path`, `expectedHash`, `originalHostsStopped: true`; isolated restore only |

Limits can specify `maxAiCredits`, `maxConcurrentSubagents`,
`maxTotalSubagents`, and `timeoutSeconds`. Set them deliberately; a limit resume
retains the same run's accumulated accounting. The bounded design algorithm
cannot acquire extra fix iterations by raising a runtime limit.
Omitting `maxAiCredits` on a new run means no credit cap. An explicit positive
number opts into a cap. On resume, omission preserves the run's existing limit;
`"maxAiCredits": null` explicitly removes it and requires execution consent.
Historical approvals, failed runs, and recorded usage are never rewritten by
changing the default. Implementation/source freshness checks still apply to
recovery; removing a cap does not silently restart an old run.

## Optional web UI

```powershell
.\tools\software-factory\install.ps1 -WebUI
node .\tools\software-factory\cli.ts serve
```

Open its printed loopback URL. A free port is selected by default;
`serve --port 8080` requests one. Ctrl+C pauses supported owned runs and stops
the host. No browser is opened automatically.

Requirements, full artifact/approval previews, workflow starts and run controls,
configuration, legacy import, remote receipts/recovery, canonical PR association,
review feedback, and schedules all use the same commands as chat.
Closing a browser tab does not cancel its owning workflow. Chat-only operation
requires neither the web process nor frontend installation.

The dashboard opens on the **current step and next action**, not a grid of every
possible command. A feature's running phase, chat/UI origin, failure and retry or
recovery action appear above its brief. Review artifacts, run controls, limits
and exceptional recovery remain under **Artifacts, run controls and advanced
recovery**. Ordinary approvals use an in-page review, with the exact scope and
targets available before confirmation.

The shared registry refreshes every two seconds even when the dashboard is idle,
and immediately on window focus. New chat-created features appear in the sidebar;
running phases, errors, suggestions and saved scope changes update without manual
refresh. **Shared activity** combines saved chat/UI revisions, workflow progress,
approvals and failures. It does not mirror free-form chat messages or unsaved
conversations. An offline indicator retains the last snapshot and unsaved drafts;
edits are never overwritten by a background update. All interfaces must still
use the same checkout and `SOFTWARE_FACTORY_HOME`.

### Conversational progress is part of the product

Chat should start with the current stage, what Factory will investigate, the
deliverable it is producing and the next decision/checkpoint. It should not
be an unexplained questionnaire. Research source/tests/precedent before handing
technical unknowns back to the engineer; make evidence-backed recommendations
and reserve questions for material scope or compatibility decisions.

The chat entry point records these updates using `software_factory_report_progress`
at meaningful transitions and before genuine questions. The UI shows the same
summary, expected result, next step and question/rationale/recommendation, even
before a native workflow has started. Answers are still given in the originating
chat. The reports are timestamped, descriptive status—not proof that a model is
currently executing and never approval or execution authority. Native run state
takes precedence. A changed brief or later approval retires outdated current
reports while preserving their history.

The approval policy is unchanged: meaningful transitions, including permission
to create work or launch coding tasks, still require their explicit gates.
More autonomous execution is a separate policy choice, not a side effect of
clearer conversational progress.

A workflow attempt is persisted before SDK startup/source inspection. Missing
runtime dependencies and other startup failures remain visible in both chat and
the UI, including errors without native run IDs. Repair the reported installation,
then explicitly retry; no automatic paid retry, remote-effect replay, or
success-shaped fallback occurs.

## Storage, safety and recovery

- Registry: `%LOCALAPPDATA%\SoftwareFactory\registry.sqlite` on Windows, or the
  user's local data directory on other platforms. `SOFTWARE_FACTORY_HOME` selects
  an isolated private data directory; use the same value in every interface.
- Schema v1 upgrades create a consistent SQLite backup before the v2 migration.
  Unknown schema versions fail without resetting data.
- Registry backup/restore is also available in Connections or chat. Restoration
  creates a new data directory, never replaces the live database, disables
  schedules, and marks interrupted executions for inspection. Stop original
  hosts before using a clone; native journals and ownership are not copied.
- Stable project/feature IDs prevent name collisions. Artifact and approval
  histories remain available after revisions; old approval does not approve new work.
- Source pins include commits, dirty diffs and untracked source hashes. Workflow
  implementation/SDK fingerprints prevent old code from reusing new semantics.
- Requirement, source-routing or code drift requires explicit successor/recovery.
  Board/account-only changes preserve analysis artifacts but invalidate active
  execution/operation authority; no remote object is silently moved or recreated.
- Native sessions/runs are recorded explicitly. Cross-interface control requests
  use the owning runtime; journals are never copied or written by this application.
- Before start/resume, the actual owning session performs a read-only registration
  handshake with `project:SoftwareFactory`. Session identity, requested workflow
  name and loaded implementation fingerprint must match. A starting extension
  is awaited; one session-local extension reload may repair a missing/failed
  activation before admission. Disabled extensions are never silently enabled.
  Registration failure records the observed discovery/launch state, not a generic
  recommendation to restart the app. No paid workflow is retried by this repair.
  Only `project:SoftwareFactory` is required here; the separately registered
  standalone `DevDesignDoc` extension is not required by Factory's imported design
  algorithm and is not treated as an admission dependency.
  Read-only run observation and cancellation do not require new workflow admission.
- Workers have a read-only tool allow-list and path-scoped permissions. Writes,
  shell, MCP mutation, managed-policy bypass and out-of-scope reads are denied.
  Relevant unavailable evidence remains a blocker.
- External effects have durable intent/receipts and correlation markers. A crash
  after remote success reconciles the object instead of blindly resending.
  Definite rejection and uncertain mutation are distinct states.
- PR feedback rechecks its exact scope and live PR before sending. Uncertain
  feedback is reconciled against its preserved original target, even after
  configuration or canonical-PR changes; reconciliation never reposts it.
- Reconciliation may repair idempotent links/status, but never redispatches a
  coding task. A no-effect attestation requires actual human investigation,
  a reason, and separate confirmation; it can duplicate work if wrong.
- Local monitor schedules are opt-in, scope-bound and active only while a host
  is online. Scope changes invalidate them. This is not a hosted team service.
- The HTTP surface is loopback-only, Host/Origin checked, cookie/CSRF protected,
  and scope-hash checked. Inputs/artifacts render as data, not executable HTML.
- No automatic PR approval, merge, checkout/stash/reset, deployment, or legacy
  backend fallback. No credentials or app-internal Copilot DB edits.

Import previews never modify old files. Imports require real requirements and
mark old artifacts/approvals historical and untrusted. Stop old active writers
before importing. The legacy standalone plugin remains for explicitly unmigrated
work; never run it concurrently for a Software Factory feature.

## Verification

```powershell
npm --prefix .\tools\software-factory run check
npm --prefix .\tools\software-factory test
npm --prefix .\tools\software-factory run check:runtime
npm --prefix .\tools\software-factory\web run build
npm --prefix .\tools\software-factory\web run test:browser
node --test .\.github\extensions\DevDesignDoc\workflow.test.mjs
```

Ordinary tests use temporary registries, deterministic agents/providers and real
loopback HTTP. Browser checks use installed headless Edge and save screenshots
under ignored `web\test-results`. No credentials, real work items or paid models
are used. `check:runtime` starts a real SDK session, verifies its registration
handshake, and calls the actual intake/design/lifecycle/monitor names with
deliberately nonexistent feature/execution IDs. Each must reach its normal scope
guard with zero agents, not merely avoid a transport error. Those intentional
diagnostic runs end in an expected scope-guard error; they do not represent failed
user feature executions. The check also pauses a zero-agent check workflow,
stops/restarts the runtime, and resumes the same native run. The controller's
same-session registration receipt is included in diagnostic output. A headless
host reporting no interactive confirmation is
expected; no consent is manufactured.

Regression coverage additionally invokes the factory client from inside a real
extension subprocess, rather than only testing a standalone shell process, and
verifies operation under conflicting inherited runtime-path/transport settings.
It also simulates an embedded app whose process executable is not Node; this
reproduces the otherwise misleading pair of failed extension activations and
verifies that the selected real Node interpreter reaches the design scope guard
with zero agents.
These checks do not prove paid model quality or all desktop-host behavior. An
already-approved feature does not need a new scope approval after a startup
failure; a failed execution with no native run ID needs a newly confirmed attempt,
not a fabricated resume or an automatic retry.

See the [implementation plan](../../docs/software-factory-implementation-plan.md)
for requirements, scope boundaries, alternatives and rollout acceptance.
