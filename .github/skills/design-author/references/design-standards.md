<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android Design Standards

Use the caller's approved scope, evidence, repository configuration, schema, and
review rubric. This reference specifies content quality, not lifecycle actions.
Never add requirements or numeric targets that the approved scope does not contain.

## Evidence and prior art

- Verify current source before relying on an existing design. Documents in
  `design-docs/` may be stale or disagree with the implementation.
- Use available relevant designs as historical context and style references,
  not as approval or proof of current behavior. A local template is not a
  prerequisite: the supplied schema defines required sections.
- Cite concrete source locations and revisions. Mark unsupported claims as
  assumptions and identify gaps that prevent a safe design.

## Required design coverage

1. Problem, user impact, goals/non-goals, approved requirements, and applicable
   platforms/priorities.
2. Components and request/data/control flow across MSAL/OneAuth, Common IPC,
   Broker, and eSTS as applicable. Distinguish Authenticator app behavior.
3. At least two meaningful solution options with pseudocode/API signatures,
   concrete pros/cons, and a justified recommended decision.
4. Public/internal API surface, AIDL/Bundle/schema changes, protocol negotiation,
   existing-client compatibility, and ownership boundaries.
5. State transitions, lifecycle, concurrency, failures, retries, cancellation,
   recovery, bounded resource use, and performance implications.
6. Security/privacy, trust boundaries, sensitive-data handling, and safe logging.
7. Flags, ECS configuration where relevant, rollout stages, rollback/reversal,
   and behavior when the feature is disabled.
8. Telemetry events/spans/attributes, success/failure signals, and measurable
   verification without invented thresholds or collection of sensitive data.
9. Unit, instrumented, integration/E2E tests and evaluation scenarios, including
   disabled paths, compatibility boundaries, and failure cases.
10. Cross-repository dependencies, implementation order, owning teams, external
    OneAuth notifications where needed, assumptions, and open decisions.

## Traceability and independent review

Map every approved requirement to design sections, evidence references, and a
concrete verification. Address every supplied engineering design task with
choices and evidence, not a request that the engineer design the implementation.
Keep genuine scope decisions distinct from solvable technical research.

Review source and evidence independently of the author's claims. Apply the
supplied fixed rubric without waiving criteria or downgrading findings to obtain
a pass. Return specific locations, evidence, and actionable required changes.
Revision work must address findings without deleting requirements or evidence.
