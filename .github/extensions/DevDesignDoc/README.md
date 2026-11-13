<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android feature design workflow

`design-feature` is a repository-scoped **Copilot Dynamic Workflow**, not a GitHub
Actions/Agentic Workflow. A compatible Copilot runtime discovers `extension.mjs`
under `.github/extensions/DevDesignDoc`. The entry point is
`.github/extensions/DevDesignDoc/extension.mjs`; the workflow invocation name remains
`design-feature`. The runtime supplies
`@github/copilot-sdk/extension`; do not install SDK dependencies in this directory.
ES modules are required by the extension runtime (the Android Kotlin-first rule
still applies to application code).

API reference: [official Dynamic Workflow documentation](https://github.com/github/copilot-sdk/blob/main/nodejs/docs/workflows.md).
The existing `.github/skills/design-author/SKILL.md` informs the design content;
this workflow does **not** execute that skill's ADO publishing steps.

## Input and use

Start `design-feature` through the runtime's Dynamic Workflow tooling, passing
this argument shape:

```json
{
  "stage0": {
    "title": "Feature name",
    "revision": "immutable-requirements-revision",
    "approval": {
      "approvedRevision": "immutable-requirements-revision",
      "approvedBy": "human-owner",
      "reference": "human-approval-record-reference"
    },
    "requirements": [
      { "id": "R1", "text": "An approved, measurable requirement." }
    ],
    "blockingUnknowns": []
  },
  "sourceManifest": [
    { "repository": "Relevant repository identity", "revision": "0123456789abcdef0123456789abcdef01234567" }
  ],
  "context": "Optional private input locations and prior review feedback; treated as untrusted data."
}
```

The caller supplies a genuine approval record. Matching strings are **not**
authentication or proof of approval: the evidence worker must inspect the approval
provenance, and humans must verify it at the gate. Missing/stale approval,
conflicting source evidence, unavailable relevant repositories, or blocking
unknowns return `needs clarification`, not invented requirements.

Current behavior differing from future requirements is an expected implementation
delta, not an evidence conflict. Missing final design approval is intentional.
Separate release/sign-off gates from decisions preventing an implementable app
design, and verify externally owned behavior at the affected app contract rather
than inventing guarantees about unspecified server pages.

Resolve the relevant current commits **before** invoking the workflow and supply them
in `sourceManifest`; include the requirements-source commit when it is inspected.
Each source revision must be a full Git commit object ID: 40 hexadecimal characters
for SHA-1 or 64 for SHA-256. Branch names, `HEAD`, branch/tag refs, tag names, and
abbreviated hashes are rejected before any checkpoint or agent call. Resolve refs to
full commit IDs outside the workflow. Stage 0 revisions remain approval-snapshot
labels and are not subject to the source commit format. A correctly shaped hash
alone does not prove that the commit exists or was inspected.
Authenticated remote reads or locally hash-verified snapshots at those commits are
valid. The evidence worker must inspect each pin and record `sourceVerification`;
references outside the manifest fail validation. A pin is a reproducible baseline,
not proof that a branch has not advanced. Re-resolve pins for a new regeneration.
Do not include private source, incident details, or feature outputs in this public
repository. Keep private input locations/evidence and the returned packet in private
session storage. A request to draft is not automatically requirements approval.

## Flow and output

1. Validate Stage 0 revision, approval metadata, unique requirements, and unknowns.
2. Gather current repository evidence with repository/commit/path/source locators;
   verify source pins and gather scenario traces through indirect callbacks,
   state/results, observers and completion handlers to final observable outcomes.
   Cover each requirement and applicable success/failure/cancellation branches;
   unresolved traces stop before authoring.
3. Author structured design sections, requirement traceability, and a visible
   per-change necessity register: baseline trace, existing behavior, requested delta,
   necessity, counterevidence search, classification and verification. Generate the
   required default-off flag register/isolation section and a pre-mortem
   authoring brief/post-implementation handoff in the same artifact.
4. Check sections, revision, exact evidence digest, links and trace/necessity coverage.
   Already-handled behavior is `preserve-existing` / `regression-only`; optional
   improvements are deferred. Required work needs a `confirmed-gap`. Unresolved
   claims cannot become implementation mandates or a review-ready artifact.
   Validate flag contracts and change mappings before independent review.
5. Spawn a separate independent reviewer against the fixed rubric in `workflow.mjs`.
   The reviewer reads pinned sources, seeks disconfirming paths for every proposed
   change, and checks for mandates missing from the register. Per-change checks and
   the review bind to the exact design digest; every check must pass.
   `defaultOffFeatureIsolation` and `preMortemAndReleaseGates` are mandatory.
   Reviewers return `references` (empty if none) for discoveries outside the initial
   reference list, with fresh IDs and full repository/revision/path/locator metadata.
   Discoveries must match exact `sourceManifest` pairs; malformed, duplicate,
   reused or conflicting IDs and unpinned sources fail closed.
6. Fix and re-review at most twice; never change requirements or waive criteria.
7. Return the full JSON human gate packet, including the approved input, evidence,
   design artifact, validation/review history, fix count, rubric, reasons, and next action.

The `design.sections` object is the design artifact; the workflow returns it rather
than writing a design document into a repository. The runtime journals step outputs.
`GO to Planning` and `reviewReady: true` are only automated recommendations when all
structural checks, per-change checks and rubric criteria pass with no findings or
blocking unknowns. Other packets contain diagnostic drafts, not validated deliverables.
The packet always
says `humanApproval: "pending"`. Review provenance, design, remaining nonblocking
assumptions/open decisions, and repository revisions before explicitly approving
planning. `NO-GO` means validation/review failed or two fixes were exhausted.
No ADO items, code changes, merges, deployments, or human approvals are created.
`ctx.pause()` is not used.

The packet preserves the original `evidence` unchanged. Validated reviewer sources
remain in review history and `referenceDiscoveries` with their discovery round.
`availableEvidence` combines the original references with those discoveries and
their provenance; revision authors, design validators and later reviewers use this
expanded context. Reviewers bind both `designDigest` and the input `evidenceDigest`.
Every new discovery changes the evidence digest and requires a rebound design and
fresh independent review, even if the discovering review otherwise passes.
Repaired traceability and change/counterevidence records may cite discovered IDs.
Original claims are retained for provenance, not silently endorsed over findings.

### Required default-off flag contracts

Every runtime feature needs a required `featureFlags` register and
`featureFlagsAndIsolation` section. Each change declares `runtimeBehaviorChange`,
`flagIds` and `noFlagReason`. Required runtime changes map to one or more flags;
non-runtime/documentation, regression-only and deferred work requires a reasoned
no-runtime-impact declaration with no flag mapping. Small/single-repo features
cannot be exempted. Independent review challenges false non-runtime declarations.

Each flag has `id`, `key`, pinned owning `repository`, source-verified `framework`,
`owner`, `evidenceIds`, guarded `entryPoints` (repository, entry point, placement,
disabled behavior and source evidence), `configurationPolicy`,
`productionEnablement`, `testCohort`, `rollback` and `verification`.
`defaultValue`, `configurationFailureValue` and `productionDefaultValue` must
all be **Boolean false**, enforced by schema and manual validation. Validation
also rejects missing fields, unknown/duplicate mappings, orphan flags, unpinned
repositories and evidence that does not cover each declaring repository.
Suitable existing default-off flags may be reused with verified scope/ownership;
new keys remain proposals, not created configuration.

Authors/reviewers cover all entry points/direct callers, zero new disabled-path
side effects except the minimal flag read, missing/invalid/unavailable/expired
configuration and cache/override precedence. Contracts name owners, separately
approved production enablement, approved enabled E2E cohort, rollback propagation,
active attempts and persisted state, and later code-review/CI evidence. Turning
a flag off does not automatically cancel admitted work.

The human packet carries these contracts into planning/PBI coding acceptance
criteria. Missing/contradictory scope returns for approval, not silent flag
creation. The independent `defaultOffFeatureIsolation` criterion is mandatory.
These are design-contract checks, **not verification of implemented isolation or
production settings**. Workers do not inspect/change live configuration, execute
application tests or activate features; implementation and release owners verify
code/configuration/test evidence and authorize enablement separately.

### Generated feature pre-mortem

The executable template is the JSON `design.sections` schema in `workflow.mjs`,
not the external `design-docs/Template/template.md`. Every design must contain a
nonblank `preMortemAndOperationalReadiness` section, including non-runtime and
single-repository features. Despite the section key, author/revision prompts
generate a self-contained **design-time authoring brief, not the pre-mortem draft**.
The existing heading/anchor is retained for links; the complete document is written
only after all feature implementation is complete and its dedicated PBI is dispatched.

The brief captures requirements/trace/source evidence, known risks, telemetry/
rollback inputs, gaps and proposed owners. It plans the eventual
[feature pre-mortem contract](../../skills/feature-planner/references/pbi-template.md#required-feature-pre-mortem-pbi):
requirement/trace-linked failure scenarios and customer impact; prevention,
detection and response; actual KQL drafts and telemetry contracts, mappings,
cohorts, denominators and coverage gaps; monitoring ownership, cadence, stop
criteria and escalation; rollback including active/in-flight attempts and partial
state; and owned validation/release prerequisites. It includes a
`Pre-mortem child PBI: Pending feature planning` field, or a supplied existing
AB# and durable URL with reuse subject to backlog verification. After human design
approval, planning/backlog creation includes or reuses the dedicated author/review
child PBI and establishes reciprocal links. This design-only workflow creates no
work items and does not publish a separate Markdown file. Before authoring, the
artifact status is `Pending implementation completion and PBI dispatch`; its
future URL is not a design/planning/backlog prerequisite.

The dedicated PBI depends on **all feature implementation PBIs** across repositories.
Dispatch waits for their acceptance criteria, merged changes where applicable and
required implementation validation evidence. Then the agent writes the full
pre-mortem (including actual KQL drafts) against implemented, pinned source
revisions and the exact approved design, links it to the PBI/design and hands off
with human review pending. The named human reviewer checks completeness, impact,
assumptions, contracts/cohorts/denominators, ownership, rollback and gaps, requests
agent revisions and records approval/evidence before PBI acceptance and release.
No reverse dependency may make implementation wait for the document. If no
implementation PBIs exist, justify that case and verify the approved non-runtime
deliverables are complete first. Human-review-only follow-ups are not agent work;
the agent-authoring scope may use `copilot-agent-ready` when ready for approved dispatch.

The fixed independent-review rubric requires `preMortemAndReleaseGates`.
Structural validation rejects absent/blank sections or missing rubric coverage;
the reviewer evaluates substantive brief/handoff completeness, source accuracy and gate
separation. Placeholders, invented verified telemetry, omitted applicable rollback
or unowned follow-ups cannot pass review merely by containing the right words.
Findings must be repaired and independently re-reviewed within the existing bounds.
It rejects premature full-draft generation and missing implementation dependencies;
passing design review is not authoring completion or human pre-mortem acceptance.

Pre-flight KQL may remain explicitly unexecuted with unresolved future mappings
and accountable follow-up owners. Missing production data while disabled is
expected, not a reason to activate production or reject document authorship.
Before relying on monitoring for rollout, the telemetry owner validates contracts,
mappings, outcomes, cohorts and denominators against an approved enabled E2E
test cohort and attaches evidence. Broad production enablement is not required.
The release owner separately approves prerequisites and rollback readiness.
Document review, PBI completion and `GO to Planning` are not query validation,
activation permission or rollout approval. These deferred gates do not waive
missing evidence needed to specify current baseline behavior.

## Bounds, safety, and resume

The workflow uses journaled `ctx.step` calls and sequential `ctx.agent` calls:
one evidence worker, one author, up to three independent reviews and two fixes
(seven agents maximum). Limits are one concurrent agent, seven total agents,
1,800 seconds, and 20 AI credits. Do not raise limits to bypass the two-fix bound.
Null/malformed ordinary worker output fails closed; cancellation and hard runtime
errors propagate. An interrupted/limit-reached run does not imply a GO.

Worker prompts prohibit writes and side effects. These are behavioral constraints,
**not a tool sandbox**: `ctx.agent` has no documented tool allow-list option.
Run only with suitable host permission controls; never blanket-approve writes or
external transmission. Repository contents and worker output are untrusted data.

Checkpoint keys use schema/prompt version `design-feature-v9` and a digest of the
entire input, including source pins and feedback context. Author/fix/review keys also
bind the current expanded evidence digest (including discovery provenance); review
keys include the design digest; review and fix keys also bind the review/validation history.
Journal replay rebuilds discoveries from validated review outputs without mutating
the original evidence or reusing reviews against stale reference context.
Resume only against unchanged pinned source revisions and input. Journal replay does
not recheck a mutable branch or re-read edited local snapshots; do not alter snapshots
in place. Start a fresh run with new pins if the source/evidence changes; change the key version when workflow semantics,
schemas, prompts, or rubric change. Steps have no intentional external side effects,
so replay does not publish duplicate artifacts.
The post-implementation authoring handoff does not reuse prior v5 outputs,
accessibility-only v6, pre-mortem-only v7 or design-time full-pre-mortem/default-off v8 outputs.

## Checks

Run from the repository root (Node.js 18 or newer):

```powershell
node --check .github\extensions\DevDesignDoc\extension.mjs
node --check .github\extensions\DevDesignDoc\workflow.mjs
node --test .github\extensions\DevDesignDoc\workflow.test.mjs
```

Tests use mocked agents/steps and require no SDK installation, network, or paid
agents. They cover approval gates, rejection of mutable/abbreviated source revisions
before checkpoint creation, full SHA-1/SHA-256 pins, source/trace validation, ordinary/hard failures,
fixed rubric/per-change coverage, bounded fixes, recommendation semantics, and
source/evidence-bound checkpoint identity. Pre-mortem regressions cover missing/
blank briefs, runtime/non-runtime post-implementation handoff, omitted/failed review
criteria, repair/re-review, owned pre-flight gaps without production activation,
and stale v5/v6/v7/v8 checkpoint isolation. Default-off regressions cover missing/
non-false contracts, invalid mappings/source ownership, non-runtime declarations,
shared/multiple flags, schema/prompt handoff, mandatory independent criteria and
repair/re-review. Synthetic executable completion fixtures
also cover new pinned reviewer references, repair/re-review, invalid discoveries,
stale digest rejection and journal replay with preserved provenance. Completion fixtures
cover indirect handling (reuse/regression-only) and a genuinely missing handler
(verified defect), plus a disputed false gap that must be fixed and re-reviewed.
These are deterministic gate tests with mocked agent judgments, **not semantic
quality eval results**, actual Android isolation tests or live production
configuration verification. For a live agent eval, present both synthetic variants
without telling the reviewer the expected classification; confirm it follows the
terminal chain, rejects unnecessary forwarding, and identifies the genuine gap.
Separately verify extension discovery and a human-approved representative run in
a compatible Copilot runtime; mocks do not validate live agent quality.

The same baseline/necessity requirements apply to the `design-author` skill and
direct authoring. An interrupted or skipped independent review must be labeled
**unvalidated draft**, never silently treated as a successful workflow.
