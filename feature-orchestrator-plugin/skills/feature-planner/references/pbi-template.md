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

## Android-complete Feature Pre-mortem PBI

For every Android-complete feature, including unflagged and single-repo features,
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
- Document rollback triggers, decision/execution owner, flag or non-flag mechanism,
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
