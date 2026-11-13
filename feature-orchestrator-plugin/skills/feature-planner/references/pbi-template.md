# Work Item Template

Use this structure for every work item description. The description must be
**self-contained** — the coding agent only has this text plus the target repo.

## Objective

[1-2 sentences: What to implement and in which module/repo]

## Context

[Why this change is needed. How it fits into the larger feature.
Include enough background that someone unfamiliar could understand the motivation.]

## Technical Requirements

### What to Build

[Specific implementation details. Include:]

- Classes/functions to create or modify
- Method signatures and data structures
- Error handling approach
- Threading/concurrency model (if relevant)
- Integration points with other modules

### Code Patterns to Follow

[Reference existing patterns in the repo. Include actual code examples
or file paths within the TARGET REPO (not other repos).]

```
// Example of the pattern to follow:
class ExistingPattern {
    // show the convention
}
```

### What NOT to Do

[Explicit exclusions to prevent scope creep:]
- Do NOT modify [specific files/features outside scope]
- Do NOT add [unnecessary abstractions]
- This work item does NOT cover [related but separate concern]

## Acceptance Criteria

- [ ] [Concrete, testable criterion 1]
- [ ] [Concrete, testable criterion 2]
- [ ] [Concrete, testable criterion 3]
- [ ] All existing tests pass
- [ ] New unit tests cover the happy path and error cases
- [ ] Code follows project conventions (from copilot-instructions.md)
- [ ] Android-complete runtime feature: false code/configuration-failure defaults; production stays off until separately approved enablement
- [ ] Android-complete runtime feature: zero new off-path side effects across all entry points
- [ ] Android-complete runtime feature: default/missing/invalid/unavailable/expired configuration, off/on and active-attempt rollback test evidence

## Files to Modify

| File | Change |
|------|--------|
| `path/to/file.ext` | [What to change] |
| `path/to/new-file.ext` | [New file — what it contains] |

## Dependencies

- **Depends on**: [WI-N / AB#ID — what must be merged first and why]
- **Depended on by**: [WI-N — who is waiting for this]

## Testing

- Unit tests for [specific logic]
- Integration tests for [cross-component interaction] (if applicable)
- Test file location: `path/to/tests/`

## Android-complete Default-off Feature Contract

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

## Android-complete Feature Pre-mortem PBI

For every Android-complete feature, including non-runtime and single-repo features,
include one dedicated child PBI, `[Feature name] - Author and review pre-mortem`.
Verify and reuse a suitable existing item instead of duplicating it. Assign one
owning repository, author and reviewer; keep the work right-sized and preserve
normal metadata/feature tags. Mark it `documentation/review`, not automatic
coding-agent work; do not add `copilot-agent-ready` to human-review-only work.
Replace application-code/build acceptance criteria with document-review criteria:

- Link the exact design revision/section and reviewable pre-mortem artifact using
  durable authorized URLs, inline essential context, and record a PBI back-link
  in the design once the AB# ID is known.
- Describe failure scenarios and customer impact, affected cohorts/blast radius,
  and applicable regressions, cancellation, retry, partial state and upgrade risks.
- Map each scenario to prevention, detection, response and accountable owners.
- Include monitoring signals and actual KQL draft text; document telemetry
  event/span/property contracts, table/property mappings, units/outcomes, eligibility,
  execution and baseline cohorts, time windows, numerators/denominators,
  deduplication/sampling and coverage gaps. No data is not proof of health.
- Name monitoring/response owners, cadence, observation window, thresholds,
  stop criteria and escalation; assign unresolved values rather than inventing them.
- Document rollback triggers, decision/execution owner, default-off flag mechanism
  for runtime features (non-flag mechanisms supplement it or apply to non-runtime work),
  propagation limits, active/in-flight attempts, persisted/partial state,
  cleanup/recovery and verification of preserved behavior.
- Record validation and release prerequisites with named owners and tracking
  items or explicit owner actions, including tests and instrumentation/query checks.

Authoring/review is complete when the artifact is linked and reviewed and gaps
have explicit follow-up owners. KQL may remain an **unexecuted pre-flight draft**
with unresolved table/property mappings. Missing production data is expected
while disabled and must not block document authorship/review.

Before relying on monitoring for rollout, the telemetry/query-validation owner
must validate contracts, mappings, outcomes, cohorts and denominators using an
**approved enabled E2E test cohort** and attach evidence; broad production
enablement is not required. The release owner separately resolves release-blocking
gaps and approves release prerequisites and rollback readiness. Completing this
PBI is **not rollout approval**, query validation or activation permission.
Do not make documentation completion depend on production activation.
