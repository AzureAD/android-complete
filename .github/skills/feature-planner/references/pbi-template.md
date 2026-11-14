# PBI Description Template

Use this template for every PBI created by the feature-planner skill. The description must be
self-contained — a Copilot coding agent should be able to implement the PBI using only this
description and the repo's `copilot-instructions.md`.

---

## Template

The HTML below shows the section structure for ADO. Present feature-plan
descriptions in plain Markdown; the PBI creator converts them to HTML.

```html
<h2>Objective</h2>
<p>Implement [what] in [which module] that [does what] for [purpose].</p>

<h2>Target Repository</h2>
<table>
  <tr><td><strong>Repo</strong></td><td>[org/repo-name]</td></tr>
  <tr><td><strong>Base Branch</strong></td><td>dev</td></tr>
  <tr><td><strong>Module</strong></td><td>[common | common4j | msal | AADAuthenticator | broker4j | adal | 1ES-Pipelines]</td></tr>
</table>

<h2>Context</h2>
<p>[Why this change is needed. How it fits into the larger feature. What user problem it solves.]</p>

<h2>Feature Flag</h2>
<p>[Required for runtime features: inline the approved flag key, owning repo/framework,
Boolean default=false, configuration-failure and production defaults=false, owner,
guarded entry points, off-path isolation, cohort enablement, rollback and verification.
Only non-runtime work may use a reasoned "N/A"; do not invent a flag API.]</p>

<h2>Technical Requirements</h2>
<ul>
  <li>[Specific implementation detail 1]</li>
  <li>[Specific implementation detail 2]</li>
  <li>[Pattern to follow — reference existing class/method if applicable]</li>
  <li>[Error handling requirements]</li>
  <li>[Telemetry/logging requirements]</li>
</ul>

<h2>Scope</h2>
<p><strong>In scope:</strong></p>
<ul>
  <li>[What to implement]</li>
</ul>
<p><strong>Out of scope:</strong></p>
<ul>
  <li>[What NOT to implement — important for constraining the agent]</li>
</ul>

<h2>Files to Modify/Create</h2>
<ul>
  <li><code>[path/to/existing/File.kt]</code> — [what to change]</li>
  <li><code>[path/to/new/File.kt]</code> — [new file, what it contains]</li>
</ul>

<h2>Acceptance Criteria</h2>
<ul>
  <li>[ ] [Functional criterion 1]</li>
  <li>[ ] [Functional criterion 2]</li>
  <li>[ ] Runtime feature: actual flag default and configuration-failure fallback are false; production remains off until separately approved enablement</li>
  <li>[ ] Runtime feature: all entry points preserve existing behavior with zero new side effects when disabled</li>
  <li>[ ] Runtime feature: tests cover default/missing/invalid/unavailable/expired configuration, disabled/enabled behavior and rollback including active attempts</li>
  <li>[ ] Unit tests added for new/modified logic</li>
  <li>[ ] No new lint warnings introduced</li>
  <li>[ ] Compile check passes: <code>./gradlew :[module]:compile[Variant]Kotlin</code></li>
  <li>[ ] Unit tests pass: <code>./gradlew :[module]:test[Variant]UnitTest</code></li>
</ul>

<h2>Dependencies</h2>
<p>[List any PBIs that must be completed before this one, with AB# IDs]</p>
<p>Or: "None — this PBI can be implemented independently."</p>

<h2>Additional Context</h2>
<p>[Links to design docs, related PRs, or existing patterns to follow]</p>
```

---

## Required Default-off Feature Contract

Every Android-complete runtime feature, including small/single-repo features,
must have an approved default-off flag contract. Carry the exact design flag IDs,
keys, repository/framework, owner and change mappings into implementation PBIs
and coding instructions; do not replace it with generic "if flagged" wording.
Use the source-verified framework in the owning repository, not a universal enum
name or an enabled existing flag. Verify reuse scope/ownership; new keys are
proposals until implemented. Multiple flags may be needed across repositories.

The contract includes code `defaultValue: false`, `configurationFailureValue: false`
and `productionDefaultValue: false` as Booleans, guarded entry points and placement,
source evidence, and disabled behavior with **zero new side effects** beyond the
minimal flag read. Cover missing/invalid/unavailable/expired configuration and
cached/remote override precedence; preserve off unless valid authorized cohort
enablement applies. Name production enablement/approval and rollback owners,
approved enabled E2E test cohort, rollback triggers/propagation, active attempts
and persisted/partial-state recovery. Do not require broad production enablement.

Implementation/review owners must attach code-review/CI evidence for actual false
defaults, configuration failures, unchanged off-path behavior, enabled behavior
and rollback before release; release-owner approval remains separate. Design
approval, PBI creation and documentation completion are not implemented isolation
or activation permission. Non-runtime/documentation, regression-only and deferred
work needs an explicit no-runtime-impact reason; size/convenience is not an exemption.
Return missing/contradictory contracts to design/planning for approval rather than
silently adding flags or changing an approved plan.

## Required Feature Pre-mortem PBI

For **every feature in Android-complete**, including non-runtime and single-repo features,
include one dedicated child PBI titled `[Feature name] - Author and review pre-mortem`.
Reuse a suitable existing child item after verifying its scope; do not create a duplicate.
This is an agent-authored documentation task followed by human review, not application-code
implementation. Keep one owning repository and a named agent author/human reviewer;
record cross-repo inputs as dependencies, not a multi-repo implementation PBI.
Use the normal metadata, priority,
effort/sizing and feature-tag conventions; retain `ai-generated` for generated items,
and use `copilot-agent-ready` only when the agent-authoring scope and inputs are ready
for approved dispatch. Human-review-only follow-ups must not carry that tag.

**Timing and responsibilities**:

1. **Design**: Capture a feature-specific authoring brief: approved requirements/design
   revision, source evidence, known risks, telemetry/rollback inputs, open gaps and
   proposed owners. Do not draft the pre-mortem during design or implementation,
   or require a completed artifact before planning.
2. **Planning/backlog**: Create or reuse this dedicated child PBI after approval.
   Inline the brief, document destination, all content requirements below and the
   named human reviewer. Make this item depend on **all feature implementation
   PBIs**, not only one repository's work; never add it as an implementation
   prerequisite or make it depend on itself. Before dispatch, record
   `Pre-mortem artifact: Pending implementation completion and PBI dispatch`;
   absence of the future artifact is not a design/planning/backlog blocker.
3. **Post-implementation PBI dispatch — agent authoring**: Dispatch this PBI only
   after all feature implementation PBIs meet their acceptance criteria, their
   changes are merged where applicable, and required implementation validation
   evidence is available. Then the agent writes the complete pre-mortem against
   the implemented, pinned source revisions and exact approved design, including
   scenario analysis, safeguards, actual KQL drafts, monitoring/response ownership,
   rollback and owned validation/release
   prerequisites. Link the reviewable artifact to this PBI and the exact approved
   design revision. Do not modify application code, activate flags or run live KQL.
   Hand off with human review pending; agent completion is not PBI acceptance.
4. **Human review**: The named reviewer checks feature-specific completeness,
   customer impact, technical assumptions, query contracts/cohorts/denominators,
   accountable owners, rollback and follow-up gaps. The agent addresses requested
   revisions; the human records approval/evidence before authoring/review acceptance.
   Require reviewed pre-mortem acceptance before release/production enablement,
   not before implementation completion. Query validation and release approval
   remain separate, later owned gates.

Record every implementation dependency by planned PBI ID, then resolve it to its
AB# ID in backlog creation. For a feature with no implementation PBIs, explicitly
justify that fact and verify its approved non-runtime deliverables are complete
before authoring; do not invent a dependency or exempt it from the pre-mortem.

Use the standard Objective, Target Repository, Context, Scope, Acceptance Criteria,
Dependencies and Additional Context headings, replacing code/build criteria with:

- **Design linkage**: Link to the exact design revision/authoring-brief section using
  durable authorized URLs, not local workspace paths, and inline essential context.
  Record the PBI back-link in the design after its AB# ID is known. After agent
  authoring, add the reviewable artifact's durable URL/revision to the PBI/design;
  before dispatch, keep its explicit pending status instead of inventing a link.
- **Failure scenarios and customer impact**: Cover applicable success-path regressions,
  failure, cancellation, retry/re-entry, partial state, upgrades and cross-component
  mismatches. State blast radius, affected customers/cohorts, and severity assumptions.
- **Prevention, detection and response**: Map each scenario to safeguards, detection
  signals, response actions, responsible owners and evidence/validation needs.
- **Monitoring and KQL drafts**: Include actual draft query text for the proposed
  signals, telemetry event/span and property contracts, table/property mappings,
  units, outcome definitions, eligibility/execution/baseline cohorts, time windows,
  numerator/denominator definitions, deduplication, sampling and coverage gaps.
  Distinguish no traffic or missing instrumentation from healthy behavior.
- **Operations**: Name the monitoring owner, review cadence, observation window,
  thresholds/stop criteria and escalation/response owner. Mark unconfirmed values
  explicitly and assign their resolution rather than inventing approved thresholds.
- **Rollback**: State triggers, decision/execution owner, default-off flag mechanism
  for runtime features (non-flag mechanisms supplement it or apply to non-runtime work),
  propagation limits, active/in-flight attempts, persisted/partial state,
  cleanup/recovery and verification of preserved existing behavior.
- **Validation and release prerequisites**: Identify test cases, instrumentation/
  query validation, approvals and release gates, with a named owner and tracking item
  or explicit owner action for each later prerequisite.

**Authoring/review acceptance**: The agent-written artifact is linked, approved by
the named human reviewer with recorded evidence, and contains all
sections above; unresolved mappings, unexecuted queries, coverage gaps and follow-up
owners are explicit. KQL may remain an **unexecuted pre-flight draft** with unresolved
table/property mappings. Missing production data is expected while a feature is
disabled and does not block this documentation PBI.

**Separate rollout gate**: Before relying on monitoring for rollout, the telemetry/
query validation owner must validate contracts, mappings, outcomes, cohorts and
denominators against an **approved enabled E2E test cohort** and attach evidence.
Broad production enablement is not required for validation. The release owner must
resolve release-blocking gaps and approve release prerequisites and rollback readiness.
Completing the authoring/review PBI is **not rollout approval**, query validation, or
permission to activate the feature. Track actual prerequisites, not an artificial
dependency on production activation.

## Example: Adding Retry Logic to IPC Token Request

```html
<h2>Objective</h2>
<p>Add automatic retry with exponential backoff for failed IPC token requests in the Common
module to improve reliability when the Broker process is temporarily unavailable.</p>

<h2>Target Repository</h2>
<table>
  <tr><td><strong>Repo</strong></td><td>AzureAD/microsoft-authentication-library-common-for-android</td></tr>
  <tr><td><strong>Base Branch</strong></td><td>dev</td></tr>
  <tr><td><strong>Module</strong></td><td>common</td></tr>
</table>

<h2>Context</h2>
<p>Users experience intermittent IPC failures when the Broker process is starting up or under
heavy load. Adding retry logic at the IPC layer in Common will transparently improve
reliability for all consumers (MSAL, OneAuth) without requiring changes in each client.</p>

<h2>Feature Flag</h2>
<p>Use the source-verified Common flight framework with proposed key
<code>IPC_RETRY_ENABLED</code>; verify the actual API before implementation.
Default, configuration-failure and production values are false. Gate all entry points
before retry state or telemetry; feature owner controls approved test-cohort enablement
and rollback, and release owner approves production enablement separately.</p>

<h2>Technical Requirements</h2>
<ul>
  <li>Add retry logic in the IPC strategy classes (BrokerAccountManagerStrategy or similar)</li>
  <li>Use exponential backoff: 500ms, 1s, 2s (3 attempts max)</li>
  <li>Only retry on transient IPC errors (DeadObjectException, RemoteException), not on auth errors</li>
  <li>Log each retry attempt using the Logger class with correlation ID</li>
  <li>Add telemetry span attribute for retry count</li>
  <li>Follow existing error handling patterns in the IPC layer</li>
</ul>

<h2>Scope</h2>
<p><strong>In scope:</strong></p>
<ul>
  <li>Retry logic with exponential backoff</li>
  <li>Logging and telemetry for retries</li>
  <li>Feature flag gating</li>
</ul>
<p><strong>Out of scope:</strong></p>
<ul>
  <li>UI-level retry indication</li>
  <li>Per-operation retry configuration</li>
  <li>Changes in MSAL or Broker repos</li>
</ul>

<h2>Files to Modify/Create</h2>
<ul>
  <li><code>common/src/main/java/com/microsoft/identity/common/internal/broker/ipc/IpcRetryPolicy.kt</code> — New file: retry policy with exponential backoff</li>
  <li><code>common/src/main/java/com/microsoft/identity/common/internal/broker/ipc/BrokerAccountManagerStrategy.java</code> — Wrap IPC calls with retry policy</li>
  <li><code>common/src/test/java/com/microsoft/identity/common/internal/broker/ipc/IpcRetryPolicyTest.kt</code> — New file: unit tests</li>
</ul>

<h2>Acceptance Criteria</h2>
<ul>
  <li>[ ] Retries on DeadObjectException and RemoteException</li>
  <li>[ ] Does not retry on AuthenticationException or other auth errors</li>
  <li>[ ] Respects max retry count of 3</li>
  <li>[ ] Backoff timing: 500ms, 1s, 2s</li>
  <li>[ ] Feature flag disables all retry behavior when off</li>
  <li>[ ] Code-review/CI evidence verifies false defaults and missing/invalid/unavailable/expired configuration</li>
  <li>[ ] Disabled path has no new retry state or telemetry; enabled tests and active-attempt rollback pass</li>
  <li>[ ] Logger output includes correlation ID and retry attempt number</li>
  <li>[ ] Unit tests cover: success first try, retry-then-success, max-retries-exceeded</li>
  <li>[ ] Compile check passes: <code>./gradlew :common:compileDebugKotlin</code></li>
  <li>[ ] Unit tests pass: <code>./gradlew :common:testDebugUnitTest</code></li>
</ul>

<h2>Dependencies</h2>
<p>None — this PBI can be implemented independently.</p>
```
