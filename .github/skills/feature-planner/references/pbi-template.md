<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# PBI Description Template

Use the following Markdown sections inside each planned item's `description`.
Keep item metadata, requirement IDs, dependencies, files, and tests in the
caller's structured fields as well. The provider handles remote formatting.
No HTML envelope, summary-table parser, or existing AB# IDs are required.

## Objective

Describe the behavior to implement, affected module, and expected outcome.

## Target Repository

Use the supplied repository identity and configured base branch. Specify modules.

## Context

Explain the problem and inline the design decisions needed to implement the
item without another checkout, a local spec file, or conversation history.

## Feature Flag

Required for runtime features: inline the approved flag key, owning
repository/framework, Boolean `default=false`, configuration-failure and
production defaults `false`, owner, guarded entry points, off-path isolation,
approved test cohort, rollback, and verification. Only non-runtime work may use
a reasoned "N/A"; do not invent a flag API.

## Technical Requirements

Give APIs/contracts, relevant existing patterns, error handling, compatibility,
security/privacy, telemetry, and flag/rollout requirements from the design.

## Scope

State inclusions and exclusions. Preserve approved caller/platform boundaries.

## Files and Tests

List known repository-relative source and test paths. Specify concrete behavior,
failure, compatibility, and disabled-path cases plus appropriate build/test commands.

## Acceptance Criteria

Use verifiable criteria tied to the approved requirement IDs. For runtime
features, include actual `false` flag defaults and configuration-failure
fallbacks, unchanged disabled-path behavior with zero new side effects, and
tests covering default/missing/invalid/unavailable/expired configuration,
disabled/enabled behavior, and active-attempt rollback. Include necessary test
coverage and avoid unsupported quantitative targets.

## Dependencies and Coordination

Use the plan's local item IDs and explain what each dependency supplies. Include
cross-team notifications or integration work. Use "None" when independent.

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
- **Healthy operation and failure detection**: Retain this exact section in the
  post-implementation pre-mortem; seed its customer-success definitions and known
  measurement inputs in the design-time brief. Generate feature-specific happy-path
  and failure/degradation scenarios with stable IDs linked to requirements and the
  exact design telemetry contract. Define success as observable customer completion,
  not merely API success or absence of errors. Cover eligible/exposed users, attempts,
  completions/completion rate, cancellation versus abandonment, latency and silent/
  missing telemetry. For each measure define user/device/journey/attempt units,
  numerators/denominators, eligible/exposed/executing/baseline cohorts, version/flag
  dimensions, observation/completion windows, retry/deduplication/sampling rules and
  ingestion delay. Use existing permitted signals, never new disabled-path telemetry;
  unavailable eligibility/exposure counts remain explicit owned coverage gaps.
  Generate actual Kusto (KQL) draft text with query IDs covering every applicable
  scenario/measure, expected output columns/units, healthy baseline or explicitly
  unconfirmed target, and interpretation/action. Shared queries may cover multiple
  scenarios if mappings are explicit; query titles alone are insufficient. Distinguish
  time-window-matured abandonment from cancellation, in-flight work and ingestion
  loss; treat zero denominators/low samples as insufficient evidence, not 100% success
  or automatic health/failure. Connect deviations to failure/customer impact,
  prevention and response; identify monitoring/response owners, cadence/window and
  investigation, stop-expansion and rollback triggers. Unknown targets/owners need
  explicit resolution actions, not invented approval. Inline the scenario/query/
  response mapping, referencing the canonical telemetry contract rather than copying
  a whole dashboard specification. For non-runtime work, justify each inapplicable
  metric/query and provide observable alternative verification/ownership; do not
  omit the section or fabricate telemetry. Generate the scenarios and queries only
  after implementation completion and PBI dispatch, not at design time.
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
Exercise controlled happy-path/failure, missing/late-terminal and zero/low-sample
cases to verify query outputs and detection semantics, not just success-event arrival.
Broad production enablement is not required for validation. The release owner must
resolve release-blocking gaps and approve release prerequisites and rollback readiness.
Completing the authoring/review PBI is **not rollout approval**, query validation, or
permission to activate the feature. Track actual prerequisites, not an artificial
dependency on production activation.
