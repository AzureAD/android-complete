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

## Baseline and necessity gates

- Before drafting, pin relevant source revisions and record scenario traces from
  the trigger and applicable flags/account types through callbacks, state/result
  changes, observers, and completion/lifecycle handlers to the final observable
  outcome. Include success, failure, cancellation, and retry branches.
- Do not splice revisions into a single execution path. Missing relevant source
  or an unresolved terminal path is a blocking unknown, not evidence that
  behavior is absent.
- Include a visible necessity register for every implementation mandate
  (requirement, section, pinned baseline trace, existing behavior, requested
  delta, necessity, inspected counterevidence, and verification). Classify each
  entry as new feature work, verified defect, existing behavior to preserve
  (regression-only), optional improvement (deferred), or unresolved.

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
7. Default-off feature flags and entry-point isolation: every runtime behavior
   change maps to a source-verified flag contract with Boolean `false` code,
   configuration-failure, and production defaults, guarded converging entry
   points, and zero new side effects on the disabled path; non-runtime work
   declares an explicit reasoned non-runtime justification. Adopt the
   [Android Auth Flighting Standard (Team Defaults)](#android-auth-flighting-standard-team-defaults)
   below automatically and escalate only intentional deviations.
8. Telemetry events/spans/attributes, customer-visible success/failure signals,
   and measurable verification without invented thresholds, disabled-path
   telemetry, or collection of sensitive data.
9. Accessibility design and verification: retain explicit accessibility coverage
   (or a reasoned N/A when no UI is affected), mapping affected Android UI
   states to TalkBack semantics/announcements, focus order/restoration,
   keyboard/D-pad navigation, 48dp by 48dp touch targets, font/display scaling,
   contrast/non-color cues, and planned verification evidence.
10. Unit, instrumented, integration/E2E tests and evaluation scenarios, including
    disabled paths, configuration-failure defaults, accessibility checks,
    compatibility boundaries, and failure cases.
11. Cross-repository dependencies, implementation order, owning teams, external
    OneAuth notifications where needed, assumptions, and open decisions.
12. Post-implementation pre-mortem and operational readiness authoring brief:
    capture requirement/trace/telemetry/rollback inputs, customer-visible
    healthy-operation and failure scenarios, planned Kusto (KQL) query contracts
    with cohorts/denominators/windows, and handoff rules for the dependent
    post-implementation pre-mortem PBI without fabricating production thresholds.
13. Paired Human Design Review Brief when requested by the schema: derive a
    concise engineer-facing decision document (owned decisions first, outcome,
    architecture/flow, evidence vs. uncertainty, all five consequential risk
    categories, and changes since prior review) strictly consistent with the
    detailed design and within the caller's word/character budgets.

## Android Auth Flighting Standard (Team Defaults)

Apply these team defaults automatically in every runtime design instead of
inventing bespoke flighting mechanics or asking the human to reconfirm standard
behavior:

- **Single controlling-boundary flag by default:** Use one default-off Boolean
  flag owned by the initiating/controlling boundary (for example, `Broker` for
  Broker→Authenticator flows, or the owning `Common`/`Broker`/`Authenticator`
  repository in its source-verified framework such as `BrokerFlight`,
  `CommonFlight`, or `EcsFlight`) unless approved requirements or a concrete,
  verified cross-boundary safety/isolation requirement justifies a second flag.
- **Default-off everywhere:** Code default, configuration-failure fallback, and
  production default are all Boolean `false`.
- **Single boundary evaluation (local state, no per-request network fetch):**
  Evaluate the latest locally received/cached ECS/flight value once at the
  converging entry boundary. Extract non-trivial flight-on logic into a
  dedicated helper and avoid redundant nested or mid-flow re-checks and
  per-request network fetches.
- **Immediate OFF on new/uncommitted boundaries (zero grace buffer):** Receiving
  an `OFF` update takes effect immediately for new/uncommitted boundaries with
  no grace period or TTL buffer; already-dispatched requests cannot be recalled.
  Keep zero new allocations, collaborator callbacks, navigation changes, state
  writes, or telemetry on the disabled path.
- **Exception-only human escalation:** Adopt the defaults above automatically
  without asking the human to reconfirm standard flighting mechanics. Surface a
  human flighting decision or question **only** when a feature intentionally
  deviates from the standard (for example: needing a second flag across process
  boundaries, intentionally running compatibility/sanitization logic such as
  reserved-header stripping while disabled, or requiring a custom staleness/TTL
  cap), and document the deviation explicitly in the feature-flags section and
  Human Design Review Brief.

## Traceability and independent review

Map every approved requirement to design sections, evidence references, and a
concrete verification. Address every supplied engineering design task with
choices and evidence, not a request that the engineer design the implementation.
Keep genuine scope decisions distinct from solvable technical research.

Review source and evidence independently of the author's claims. Apply the
supplied fixed rubric without waiving criteria or downgrading findings to obtain
a pass. On revision rounds where a review delta is provided, verify closure of
every prior finding, audit modified sections/registers and cross-cutting
invariants, and fail closed on newly introduced regressions. Return specific
locations, evidence, and actionable required changes. Revision work must address
findings without deleting requirements or evidence.
