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
The existing `.github/skills/design-author/references/design-standards.md` informs the design content;
this workflow does **not** execute `.github/skills/design-author/SKILL.md`'s ADO publishing steps.

The executable template is the `sections`/`designSchema`, author/revision prompts
and independent-review rubric in `workflow.mjs`. Evidence and design-authoring turns
emit native Markdown sections plus a compact trailing ```` ```json ```` metadata fence
(parsed into the canonical `evidence` and `design` snapshots with host-stamped digests
and single-pin commit revisions, while keeping explicit worker `approvalVerification`
and `sourceVerification` fail-closed) so
long architectural designs and KQL/Mermaid blocks never hit JSON-string output truncation.
Round 2+ independent review turns receive a deterministic `reviewDelta` packet
(`changedSections`, `unchangedPassingSections` SHA-256 hashes, `changedRegisters`,
and `priorOpenFindings`) so reviewers focus source reads on modified claims, prior
open findings, and affected cross-cutting invariants without weakening any gate.
It does not load the external
`design-docs/Template/template.md` or the orchestrator plugin's Markdown template.

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
  "context": "Optional private input locations and prior review feedback; treated as untrusted data.",
  "humanDecisions": [
    {
      "id": "HD-1",
      "topic": "IPC timeout policy",
      "decision": "Use a 30-second client timeout with no automatic retry.",
      "sourceReference": "human-review/comment-42",
      "recordedAt": "2026-11-23T12:00:00Z",
      "supersedesIds": []
    }
  ],
  "continuation": {
    "evidence": { "...": "Optional previously validated evidence snapshot matching sourceManifest and stage0" },
    "design": { "...": "Optional previously authored design draft (requires continuation.evidence)" }
  }
}
```

### Native continuation, flat `humanDecisions`, and bounded payloads

- **Native continuation (`continuation.evidence` / `continuation.design`):**
  - Supply `continuation.evidence` to reuse a previously validated evidence snapshot
    directly without re-running the `Repository evidence` agent
    (`continuationMode: "from-evidence"`). `validateStage0` and `validateEvidence`
    verify exact `sourceManifest` pin matches, `approvalVerification`,
    `sourceVerification`, `priorReviewVerification` (when `priorHumanReview` is
    present), traces, and decision register provenance before any agent runs.
  - Supply both `continuation.evidence` and `continuation.design` to jump straight
    into a bounded `Design revision` turn incorporating current `stage0`, active
    `humanDecisions`, and `context`, followed by fresh `Independent design review`
    (`continuationMode: "from-draft"`). Continuation never skips independent review,
    never imports prior human approval, and never bypasses `validateStage0`,
    `validateEvidence`, or `validateDesign`.
  - Mismatched `sourceManifest` pins, unverified approval/source verification,
    malformed snapshots, mismatched `evidenceDigest` for an unchanged Stage 0
    revision, or `continuation.design` without `continuation.evidence` fail closed
    with `needs clarification` before spawning any agent.
- **Flat, deduplicated `humanDecisions` ledger:**
  - Optional top-level `humanDecisions` accepts flat objects with
    `{ id, topic, decision, sourceReference, recordedAt?, supersedesIds? }`
    (`additionalProperties: false`). Duplicate IDs, self-supersession, unknown
    `supersedesIds`, supersession cycles, and nested recursive blobs
    (`inheritedDecisions`, `previousAmendment`, `previousRequirements`) are
    rejected in `validateStage0`.
  - `normalizeHumanDecisions(decisions)` resolves `supersedesIds` into
    `{ active, supersededIds }` so workers receive only active decisions plus a
    compact retired-ID list — keeping payload size linear O(N) across arbitrary
    rounds of human amendments.
- **Bounded reviewer context (`buildReviewerPayload`):**
  - The independent reviewer receives `stage0`, normalized `humanDecisions`,
    `designDigest`, `evidenceDigest`, `pairRevision`, `appendixDescriptors`, the
    exact rendered `human-design-review-brief.md` (`renderedBrief`), the complete
    structured `design` (all 13 sections + registers), compact `evidence`
    (canonical fields plus discovery provenance, omitting giant supplemental blobs
    such as `summaryMarkdown`), `reviewDelta` (Round 2+), and compact
    `priorReviews` summaries (findings, failed criteria/checks, `briefChecks`, and
    new references/decisions).
  - Instead of duplicating the multi-hundred-KB rendered `detailed-design.md`
    prose alongside `design.sections`, the host deterministically verifies
    `validatePairedDocuments` before calling the reviewer and passes a compact
    `detailedDesignSummary` (`path`, `sha256`, `byteLength`, `sectionAnchors`,
    `tableAnchors`, `itemAnchorCounts`, `hostVerified`). All `review.briefChecks`
    and fixed-rubric gates remain intact.

### Source-only design gates (no Gradle builds during design)

- **Strictly source-only:** Workers never run Gradle builds, assemble APKs/AARs,
  generate merged manifests or Dagger/DI graphs, or collect compiled build
  artifacts during design. When build-time generated wiring (such as merged
  manifests or generated DI graphs) cannot be proved from checked-in source
  alone, record an explicit scope assumption or open `release-check` verification
  item for implementation/CI rather than blocking design authoring demanding a
  local build.
- **Baseline trace clarity:** Stage 1 `evidence.scenarioTraces` describe **current
  baseline code behavior** only. Future design decisions (IPC contract shape,
  timeouts, flag wiring, build-time verification) belong in `design-acceptance`
  or `release-check` records, never as unresolved baseline trace blockers that
  fail Stage 1 validation.

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
   required accessibility design, default-off flag register/isolation and a pre-mortem
   authoring brief/post-implementation handoff in the detailed design. In the same
   author call, derive a separate Human Design Review Brief from that authoritative
   design; do not author a competing design or replace detailed contracts.
4. Check sections, revision, exact evidence digest, links and trace/necessity coverage.
   Already-handled behavior is `preserve-existing` / `regression-only`; optional
   improvements are deferred. Required work needs a `confirmed-gap`. Unresolved
   claims cannot become implementation mandates or a review-ready artifact.
   Validate flag contracts, change mappings, brief coverage/support links and the
   complete rendered brief's text budget before independent review.
5. Spawn a separate independent reviewer against the fixed rubric in `workflow.mjs`.
   The reviewer reads pinned sources, seeks disconfirming paths for every proposed
   change, and checks for mandates missing from the register. Per-change checks and
   the review bind to the exact design/evidence digests and paired revision; every check must pass.
   `accessibilityDesignAndVerification`, `defaultOffFeatureIsolation` and
   `preMortemAndReleaseGates` and `humanDesignReviewBrief` are mandatory. The reviewer
   receives the exact rendered brief and separately checks consistency, completeness,
   visible risks, evidence versus assumptions and accuracy of changes since review.
   Reviewers return `references` (empty if none) for discoveries outside the initial
   reference list, with fresh IDs and full repository/revision/path/locator metadata.
   Discoveries must match exact `sourceManifest` pairs; malformed, duplicate,
   reused or conflicting IDs and unpinned sources fail closed.
6. Fix and re-review at most twice; never change requirements or waive criteria.
7. Return the full JSON human gate packet, including the approved input, evidence,
   design artifact, validation/review history, fix count, rubric, reasons, next action
   and the paired publication-ready Markdown documents for the exact reviewed revision.
   The package also contains lossless hash-bound JSON appendices; publish those with
   both Markdown documents.

The `design.sections` object and structured registers remain the authoritative
detailed design; `design.humanReviewBrief` contains its derived human decision
document. The workflow returns both rather than writing files into a repository.
The runtime journals step outputs.
`Ready for human review` and `reviewReady: true` are only automated recommendations when all
structural checks, per-change checks and rubric criteria pass with no findings or
blocking unknowns. Other packets contain diagnostic drafts, not validated deliverables.
The packet always
says `humanApproval: "pending"` and `planningReady: false`. Humans review the brief first, drilling into linked
detail as needed, and inspect provenance/remaining decisions before explicitly
approving the precise pair for planning. `NO-GO` means validation/review failed or two fixes were exhausted.
No ADO items, code changes, merges, deployments, or human approvals are created.
`ctx.pause()` is not used.

### One authoritative decision/status register

This is workflow data, validation and document generation, not a dashboard or
Android product UI. Each issue or proposed decision has one record with a stable
`id`, `category`, `status` (`open` or `resolved`), `question`/obligation, accountable
`owner`, `requirementIds`, `changeIds`, `sectionIds` and `evidenceIds`. Proposed
acceptance decisions also require a `recommendation`, `rejectedAlternatives` and
meaningful `tradeoffs`. All records include those fields (empty proposal fields
are allowed for non-acceptance obligations) and `resolution`/`resolutionReference`
(both empty while open).

| Category | Blocking gate while open | Meaning |
| --- | --- | --- |
| `missing-requirement` | Drafting | A requirement needs owner clarification; it cannot be invented. |
| `source-uncertainty` | Drafting | The affected baseline or conflicting evidence is unresolved. |
| `design-acceptance` | Planning | A proposed design choice awaits human acceptance; drafting and independent review may proceed. |
| `release-check` | Release | Later implementation, runtime testing, telemetry validation or release sign-off is still required; design generation may proceed. |

The gate is derived from category, never an author-selected override. Research
owns `evidence.decisionRegister`; authors add new records in
`design.decisionRegister` without copying or overriding research records.
`collectDecisionRegister` combines these disjoint contributions into the single
canonical register. IDs must be globally unique, including across stages.
There are no competing writable `openDecisions`, `conflicts`, `unknowns`,
`blockingUnknowns` or `humanReviewBrief.decisions` lists in worker output; even
empty legacy arrays are rejected. The approved Stage 0 input contract retains
its existing `blockingUnknowns`: before any worker starts these are normalized
into owned, open `missing-requirement` records in the diagnostic gate packet.
An unconfirmed owner is an explicitly unassigned role, not an invented person.

Scenario traces retain their verified/unresolved **source-proof** status but link
uncertainties through `uncertaintyIds` to the same register, not separate prose
unknowns. Before drafting, every baseline trace must be verified; any linked
uncertainty must be a resolved requirements/source record. Relabeling an
unresolved baseline as a release check cannot bypass trace validation.

Resolving a source discrepancy requires a reconciliation rationale and a
`resolutionReference` to a pinned source reference in that record's `evidenceIds`.
Resolving a missing requirement requires the exact inspected Stage 0 approval
reference and approved requirement IDs. Structural provenance is not enough:
independent review must verify the resolution and classification. Workers cannot
mark `design-acceptance` or `release-check` records resolved, invent approval, or
certify future runtime checks.

Reviewers return only newly discovered **open** records in
`review.decisionRegister`; corrections to existing records are findings, not ID
overwrites. Discoveries are preserved in `decisionDiscoveries` and incorporated
into `availableEvidence.decisionRegister`, keeping original evidence intact.
New requirements/source blockers stop for clarification. Other discoveries force
an evidence-digest rebind and fresh independent review before readiness, even
when all rubric checks in the discovering review pass. Immutable research
corrections require new evidence, not an author-side override.
Valid owned drafting blockers bound to the approved requirements/evidence stop
even an incomplete author candidate; the workflow does not spend fix iterations
asking the author to invent the missing answer.

Both documents' summaries, the brief's decisions-needed section and the packet's
`decisionRegister`/`decisionSummary` are derived from this canonical register.
The packet adds derived `blockingGate` values; the summary groups open IDs by
category and lists resolved IDs separately. Malformed register data yields null
packet register/summary fields, never a misleading empty/all-clear register.
The detailed document retains every record, resolution and supporting link;
lossless original stage contributions and discovery provenance remain in the
hash-bound appendices. Authored narrative references record IDs instead of
maintaining separate counts or status lists; reviewers reject stale or
contradictory claims.

The state boundary is **Drafting -> independent review passed / Ready for human
review -> externally recorded exact-revision human approval / Approved for
planning**. This workflow stops at the middle state and never records the last
one. Readiness is not planning permission, and design approval is not release
permission. A genuine human approval must bind the complete published pair,
reviewer and durable reference; it is recorded separately rather than editing
the reviewed register or inheriting an AI recommendation.

Register/schema, continuation, bounded-payload, and source-only gate changes use
checkpoint namespace `v16` and document format 4. Previous checkpoints (`v15` and
earlier) and stale reviews cannot be reused under the new schema. Existing reviewed
artifacts remain unchanged; refresh the extension registration before a future run
uses the changed implementation.

### Primary Human Design Review Brief and paired publication

The separate brief is an engineer- and PM-facing plain-English **decision document**,
not a compressed implementation spec. Its target is a ten-minute read / roughly two to three pages.
The measurable gate is **1,800 whitespace-delimited words and 16,000 JavaScript
string characters** across the entire rendered document, including automatically
rendered behavior/change tables, assumptions, status summaries, links and change history.
No physical-page estimate is enforced. Over-budget output returns for bounded
revision, then fails closed if it still cannot fit; it is never truncated and
approval-critical information must never be omitted to meet either budget.

The brief puts decisions needed first as crisp **Decision → Proposal → Tradeoff**
blocks (`Proposal`, `Tradeoff`, `Rejected alternatives`, `Owner`, `Status`, `Changes`,
`Detail`) derived from pending `design-acceptance` records in `decisionRegister`
covering every change. It then shows customer outcome, a plain-language
**Situation | Behavior** table (`humanReviewBrief.behaviorSummary`), a compact
scannable **Change | Baseline → Proposed | Flag / Scope | Detail** table linking
each change to `#change-<id>` in `detailed-design.md`, proposed architecture in
plain language and a simple flow diagram, supported evidence versus assumptions
and canonical decision/status summary, and consequential security/authentication,
accessibility, compatibility/cross-repo, default-off and rollback risks/safeguards.
Raw class/file paths, commit SHA hashes, and exhaustive registers stay in
`detailed-design.md` via links rather than cluttering brief prose. All five risk
categories require owned substantive impact and safeguards or explicit justified N/A.
Role owners may be proposed when people are unconfirmed; do not invent assigned
people or approval.

Content, decisions and risks link via section/evidence IDs into **the same**
authoritative detailed design. The renderer generates its stable section/evidence
anchors and renders every change baseline/delta and design/evidence assumption, then
derives decision/status summaries from the canonical register so open obligations
cannot silently disappear from the brief.
These elements count against the budget. Consequential risks must be visible in the
brief, not concealed behind links. Empty evidence IDs are allowed for clearly labeled
proposals/N/A; independent review must reject unsupported claims presented as facts.

Supporting links use deduplicated Markdown reference definitions (`[label][d1]` ...
`[d1]: detailed-design.md#...`) at the bottom of the brief. All labels and
destinations remain available, and the complete definitions still count toward the
word/character budgets; repeated URLs do not crowd out decision content.
Each change row links to its full anchored necessity record (`#change-<id>`) in
the same detailed design, retaining all source, trace, counterevidence and
verification fields without repeating every support link in the short document.
The brief lists pending human approval decisions separately from unresolved
requirements/source blockers. A recommendation awaiting approval does not prevent
drafting or independent review. Disabled behavior refers to the linked isolation
and compatibility contracts rather than asserting unconditional baseline preservation;
authors and reviewers must surface any intentional exception explicitly.

`humanDesignReviewBrief` and all five Boolean `review.briefChecks` must pass with a
substantive rationale, as well as existing gates. The reviewer compares both
documents with pinned evidence, requirements and verified prior-review snapshots;
it rejects omissions, contradictions, link-only safeguards, invented certainty,
unjustified N/A and stale change explanations. These are independent agent judgments,
not keyword-based semantic certification.

Only a `Ready for human review` packet supplies `pairedDocuments` and `approvalRevision`; diagnostic
drafts supply neither. `pairedDocuments` contains exactly
`human-design-review-brief.md` (the primary document) and `detailed-design.md`, their
UTF-8 Markdown byte SHA-256 hashes, common revision and prior-review context in
`documents`, plus the required `appendices` described below. The
pair revision binds the entire structured design **including the brief**, exact
evidence digest, requirements revision, document-format version, appendix paths/hashes
and prior-review identity. Both documents
display that revision and cross-link. Publish the complete pair together in one
revision-scoped private artifact collection/commit, preserving these relative names
and exact bytes; never publish just one or mix revisions. The workflow itself has
no external publisher/write permissions: returning one packet is atomic, but the
caller is responsible for durable publication and checking the manifest hashes.
`validatePairedDocuments(pair, design, availableEvidence, priorHumanReview)` compares
the entire manifest and exact rendered bytes with the reviewed snapshots; it rejects
missing documents, mixed revisions and post-review edits even if someone recalculates
the edited file's hash. Publishers should use this check before displaying an approval
surface and after reading back their durable publication.
Publication failure must not become human approval or planning permission.

#### Readable detailed design and provenance appendices

The detailed Markdown retains the complete authored narrative, a contents list and
readable tables for change necessity/counterevidence, flags and guarded entry points,
requirement traceability, decisions/uncertainty, source facts, trigger-to-terminal
scenario traces, source pins/references and approval provenance. Existing section,
change and evidence anchors remain stable; flag, decision and trace links resolve within the
same document. Table rendering escapes pipes, HTML and line breaks rather than
truncating fields. Focused JSON API/payload examples in authored sections are still
allowed; generated full-register and evidence dumps are not repeated in the main file.

`pairedDocuments.appendices` contains two required entries:

| Path | Content |
| --- | --- |
| `appendices/design.json` | The complete structured design, including all sections, registers and derived brief. |
| `appendices/evidence.json` | The complete available evidence, including original claims, discovered references, decision discoveries, approval verification and any supplemental reconciliation/external-context provenance. |

Each entry has `path`, `mediaType: "application/json"`, `content` and `sha256`.
The content is lossless compact `JSON.stringify` output, without a trailing newline;
its UTF-8 byte hash equals the existing design/evidence digest. Do not pretty-print,
normalize line endings or otherwise reserialize these files during publication.
The detailed design links both appendices and displays their hashes. Supplemental
evidence fields are indexed there and retained in full, never silently dropped or
promoted to verified repository facts.

The independent reviewer receives the exact rendered `human-design-review-brief.md`,
a compact `detailedDesignSummary` (with exact `sha256`, byte length, section/table
anchor inventory, and host-verified `validatePairedDocuments`), and
`appendixDescriptors` alongside the full structured `design` and compact `evidence`.
Publish **all four files**
in their original relative layout as one revision-scoped collection. Missing,
extra, renamed, mixed or edited appendices fail `validatePairedDocuments`, even with
recomputed file hashes. A format change creates a new revision and requires fresh
independent and human review; it does not rewrite or reuse earlier reviewed files.
The workflow returns artifacts only; callers remain responsible for writing,
read-back validation and complete publication. Refresh the extension registration
before running changed workflow code; editing repository files does not hot-reload
an already registered session alias.

Human approval must record this exact `approvalRevision`, reviewer and durable
approval reference. It remains pending in every workflow packet; no old approval
is imported. Post-review edits require regeneration and independent review of both
artifacts, a new publication and fresh human approval. Approval of this design does
not certify implemented flags/accessibility, validate monitoring queries, authorize
PBI dispatch or permit rollout. Full pre-mortem authoring still waits for all
implementation completion and authorized dispatch of its dedicated PBI.

For regeneration after human review, optionally pass `priorHumanReview` containing
the prior `design` snapshot, `revision` from its `pairedDocuments`, durable human
review `reference`, and `priorReviewContext` copied from that manifest (null for an
initial pair). The input rejects mismatched/legacy unpaired snapshots; the evidence
worker must inspect the review record and return matching `priorReviewVerification`
before a reviewed delta may be claimed. Historical sources are comparison data,
not current source evidence. Current pins still require independent inspection.
The runtime identifies changed sections/registers, evidence and requirements revision;
the author also explains substantive changes, new/resolved decisions and risks.
Reviewers compare snapshots rather than trusting that explanation. For a first
review, explicitly say so rather than inventing a review history. A resumed unchanged
run may replay the same pair, but always returns human approval pending.

### Accessibility design and evidence

Every design retains a nonempty `design.sections.accessibility`, including a
reasoned N/A when no UI is affected. For affected Android UI, map approved
requirement and scenario/screen/flow IDs (requirements-template sections 3.6/4
when supplied) to semantics, TalkBack reading/announcements and focus,
keyboard/D-pad navigation, scaling/reflow, 48dp by 48dp targets, contrast/non-color
cues, and verification. Include reused screens/components and relevant loading,
error, cancellation, retry, return/re-entry, restoration and flag-on/off states.
Use pinned owning-app accessibility guidance; do not invent new product decisions
or change approved requirements to fill gaps.

Carry measurable expected outcomes, testing/review owners, configurations,
planned evidence locations and explicit gaps into `testsAndEvals` and the later
testing-PBI/release prerequisites. Real-app TalkBack and separate keyboard-only
checks may remain planned/Unverified during design authoring. Missing evidence
or access is Unverified/Blocked, never a pass. Compilation, screenshots,
semantics/navigation assertions and forced-focus tests are supporting evidence.
Documentation/design approval does not certify runtime accessibility or approve
rollout; deferrals need an owner, rationale, tracking and release consequence.

Structural validation rejects a missing/blank section. The mandatory independent
`accessibilityDesignAndVerification` rubric criterion assesses applicability,
justified N/A, concrete per-state coverage, source/requirement traceability and
owned evidence plans; missing applicable coverage is a finding, not a pass.
Substantive assessment is an agent judgment, not keyword-based certification.

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

#### Android Auth Flighting Standard (Team Defaults)

Authors adopt these team defaults automatically without inventing bespoke
flighting mechanics or asking the human to reconfirm standard behavior:

- **Single controlling-boundary flag by default:** Use one default-off Boolean
  flag owned by the initiating/controlling boundary (for example, `Broker` for
  Broker→Authenticator flows, using its source-verified framework such as
  `BrokerFlight`, `CommonFlight`, or `EcsFlight`) unless approved requirements
  or a verified cross-boundary safety/isolation gap justifies a second flag.
- **Default-off everywhere:** `defaultValue`, `configurationFailureValue`, and
  `productionDefaultValue` are all Boolean `false`.
- **Single boundary evaluation (local state, no per-request network fetch):**
  Evaluate the latest locally received/cached ECS/flight value once at the
  converging entry boundary, extract non-trivial flight-on logic into a
  dedicated helper, and avoid redundant nested or mid-flow re-checks and
  per-request network fetches.
- **Immediate OFF on new/uncommitted boundaries (zero grace buffer):** Receiving
  an `OFF` update takes effect immediately for new/uncommitted boundaries with
  no grace period or TTL buffer; already-dispatched requests cannot be recalled.
  Keep zero new allocations, callbacks, navigation changes, state writes, or
  telemetry on the disabled path.
- **Exception-only human escalation:** Surface a human flighting decision or
  question **only** when a feature intentionally deviates from these defaults
  (for example, needing a second flag across process boundaries, intentionally
  running compatibility/sanitization logic like reserved-header stripping while
  disabled, or requiring a custom staleness/TTL cap), and document the deviation
  explicitly in `featureFlagsAndIsolation` and `humanReviewBrief`.

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
a flag off does not automatically cancel admitted work. Under
`defaultOffFeatureIsolation`, the independent reviewer also challenges
unnecessary multi-flag proliferation, invented TTL/grace buffers, per-request
flight network fetches, and unprompted mid-flow re-checks as over-engineering
findings while still enforcing strict default-off isolation, verified framework
pins, and explicit justification for any accepted deviation or off-path
exception.

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
work items and does not publish the future pre-mortem Markdown file. Before authoring, the
artifact status is `Pending implementation completion and PBI dispatch`; its
future URL is not a design/planning/backlog prerequisite.

The brief seeds **Healthy operation and failure detection** with feature-specific
customer-visible success and known measurement inputs; the dependent PBI must
generate that section after implementation. It requires happy-path and failure
scenario IDs mapped to actual Kusto draft text/query IDs, expected output columns/
units, healthy baselines (or explicitly unconfirmed targets) and owned actions.
Cover eligibility/exposure, attempts/completions and completion rates, cancellation
versus time-window-matured abandonment, latency and telemetry coverage, with explicit
units, denominators, cohorts, version/flag dimensions, windows, retries/deduplication,
sampling and ingestion delay. No errors/API success alone does not prove customer
success; missing signals, zero denominators and low samples are insufficient evidence.
Use existing permitted signals without new disabled-path telemetry. Each scenario
maps deviations to customer impact, safeguards and response owners/cadence, with
investigation, stop-expansion and rollback triggers. Retain justified per-measure
non-runtime N/A and observable alternative verification/ownership, never a blanket
exemption. Reference the canonical design telemetry contract, but inline the
scenario/query/response mapping rather than linking to an unspecified dashboard.

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

The fixed independent-review rubric requires `preMortemAndReleaseGates` and
`healthyOperationAndFailureDetection`.
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
This validation covers controlled happy-path/failure, missing/late-terminal and
zero/low-sample cases, not just success-event arrival.
The release owner separately approves prerequisites and rollback readiness.
Document review, PBI completion and `Ready for human review` are not query validation,
activation permission or rollout approval. These deferred gates do not waive
missing evidence needed to specify current baseline behavior.

## Bounds, safety, and resume

The workflow uses journaled `ctx.step` calls and sequential `ctx.agent` calls:
one evidence worker, one author, up to three independent reviews and two fixes
(seven agents maximum). Limits are one concurrent agent, seven total agents,
7,200 seconds, and 20 AI credits. Do not raise limits to bypass the two-fix bound.
Null/malformed ordinary worker output fails closed; cancellation and hard runtime
errors propagate. An interrupted/limit-reached run does not imply review readiness.

Worker prompts prohibit writes and side effects. These are behavioral constraints,
**not a tool sandbox**: `ctx.agent` has no documented tool allow-list option.
Run only with suitable host permission controls; never blanket-approve writes or
external transmission. Repository contents and worker output are untrusted data.

Checkpoint keys use schema/prompt version `design-feature-v16` and a digest of the
entire input, including source pins, `humanDecisions`, `continuation`, and feedback context. Author/fix/review keys also
bind the current expanded evidence digest (including discovery provenance); review
keys include the design digest; review and fix keys also bind the review/validation history.
Journal replay rebuilds discoveries from validated review outputs without mutating
the original evidence or reusing reviews against stale reference context.
Resume only against unchanged pinned source revisions and input. Journal replay does
not recheck a mutable branch or re-read edited local snapshots; do not alter snapshots
in place. Start a fresh run with new pins if the source/evidence changes; change the key version when workflow semantics,
schemas, prompts, or rubric change. Steps have no intentional external side effects,
so replay does not publish duplicate artifacts.
The workflow does not reuse prior v5-v15 journal outputs (including pre-continuation
v15 outputs, pre-register v12-v14 outputs, and earlier v5-v11 prompt outputs). Human-review snapshots likewise cannot reuse legacy unpaired artifacts.

## Checks

Run from the repository root (Node.js 18 or newer):

```powershell
node --check .github\extensions\DevDesignDoc\extension.mjs
node --check .github\extensions\DevDesignDoc\workflow.mjs
node --check .github\extensions\DevDesignDoc\workflow.test.mjs
node --test .github\extensions\DevDesignDoc\workflow.test.mjs
```

Tests use mocked agents/steps and require no SDK installation, network, or paid
agents. They cover approval gates, rejection of mutable/abbreviated source revisions
before checkpoint creation, full SHA-1/SHA-256 pins, source/trace validation, ordinary/hard failures,
fixed rubric/per-change coverage, bounded fixes, recommendation semantics, and
source/evidence-bound checkpoint identity. Pre-mortem regressions cover missing/
blank briefs, runtime/non-runtime post-implementation handoff, omitted/failed review
criteria, repair/re-review, owned pre-flight gaps without production activation,
happy-path scenario/query handoff, mandatory healthy-operation review and repair,
and stale v5-v11 checkpoint isolation. Default-off regressions cover missing/
non-false contracts, invalid mappings/source ownership, non-runtime declarations,
shared/multiple flags, schema/prompt handoff, Android Auth Flighting Standard
defaults/exception-only escalation/anti-over-engineering guidance, mandatory
independent criteria and repair/re-review. Synthetic executable completion fixtures
also cover new pinned reviewer references, repair/re-review, invalid discoveries,
stale digest rejection and journal replay with preserved provenance. Completion fixtures
cover indirect handling (reuse/regression-only) and a genuinely missing handler
(verified defect), plus a disputed false gap that must be fixed and re-reviewed.
Accessibility fixtures cover missing/blank sections, multi-screen UI traceability,
planned runtime evidence, omitted/failed accessibility review criteria, bounded
repair, justified non-UI N/A and isolation from pre-accessibility v5 checkpoints.
These are deterministic gate tests with mocked agent judgments, **not semantic
quality eval results**, actual Android isolation tests or live production
configuration verification. For a live agent eval, present both synthetic variants
without telling the reviewer the expected classification; confirm it follows the
terminal chain, rejects unnecessary forwarding, and identifies the genuine gap.
Separately verify extension discovery and a human-approved representative run in
a compatible Copilot runtime; mocks do not validate live agent quality.

Paired-brief regressions cover missing/unsupported content, plain-language
`behaviorSummary` rows, Decision → Proposal → Tradeoff blocks, compact change
tables, deduplicated reference links, risk categories, every-change decision
coverage, exact word-budget edges, long unbroken content, copied assumptions and
canonical status summaries, rejected hidden/mismatched risks despite passing rubric,
fresh review after repair, stale review digests, verified/unverified prior-review
snapshots, accurate change metadata, same-revision 4-file Markdown/appendix
hashes/support anchors and legacy checkpoint isolation/v16 journal replay without
additional agents. They do not test an external publisher or run a paid live-design
quality eval.

Decision/status regressions cover category-specific drafting/planning/release gates,
Stage 0 normalization, required ownership/links, global ID uniqueness, rejection
of legacy lists and gate overrides, resolved source/requirements provenance,
pending human acceptance, unverified later checks, matching document/packet
summaries, stale narrative repair, reviewer discoveries/rebinding/replay,
`reviewDelta` tracking of `decisionRegister`, and rejection of format-3 reviews or
v12-v15 checkpoints. AI workers never grant human approval or certify release
readiness. Continuation, bounded-payload, and source-only regressions cover native
`from-evidence` and `from-draft` continuation without re-running repository research,
fail-closed rejection of mismatched pins or unverified continuation snapshots,
flat `humanDecisions` validation and linear `supersedesIds` normalization, bounded
`buildReviewerPayload` (no duplicated `detailed-design.md` prose, stripped
supplemental blobs, compact `priorReviews`, host-verified `detailedDesignSummary`),
and strictly source-only / no-Gradle-build prompts and standards. These tests use
synthetic records and mocked reviewers; they do not prove semantic classification
or live agent quality.

The same baseline/necessity requirements apply to the `design-author` skill and
direct authoring. An interrupted or skipped independent review must be labeled
**unvalidated draft**, never silently treated as a successful workflow.
