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
    { "repository": "Relevant repository identity", "revision": "immutable-source-commit" }
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
   necessity, counterevidence search, classification and verification.
4. Check sections, revision, exact evidence digest, links and trace/necessity coverage.
   Already-handled behavior is `preserve-existing` / `regression-only`; optional
   improvements are deferred. Required work needs a `confirmed-gap`. Unresolved
   claims cannot become implementation mandates or a review-ready artifact.
5. Spawn a separate independent reviewer against the fixed rubric in `workflow.mjs`.
   The reviewer reads pinned sources, seeks disconfirming paths for every proposed
   change, and checks for mandates missing from the register. Per-change checks and
   the review bind to the exact design digest; every check must pass.
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

Checkpoint keys use schema/prompt version `design-feature-v3` and a digest of the
entire input, including source pins and feedback context. Author/fix/review keys also
bind the validated evidence digest; review keys include the design digest.
Resume only against unchanged pinned source revisions and input. Journal replay does
not recheck a mutable branch or re-read edited local snapshots; do not alter snapshots
in place. Start a fresh run with new pins if the source/evidence changes; change the key version when workflow semantics,
schemas, prompts, or rubric change. Steps have no intentional external side effects,
so replay does not publish duplicate artifacts.

## Checks

Run from the repository root (Node.js 18 or newer):

```powershell
node --check .github\extensions\DevDesignDoc\extension.mjs
node --check .github\extensions\DevDesignDoc\workflow.mjs
node --test .github\extensions\DevDesignDoc\workflow.test.mjs
```

Tests use mocked agents/steps and require no SDK installation, network, or paid
agents. They cover approval gates, source/trace validation, ordinary/hard failures,
fixed rubric/per-change coverage, bounded fixes, recommendation semantics, and
source/evidence-bound checkpoint identity. Synthetic executable completion fixtures
cover indirect handling (reuse/regression-only) and a genuinely missing handler
(verified defect), plus a disputed false gap that must be fixed and re-reviewed.
These are deterministic gate tests with mocked agent judgments, **not semantic
quality eval results**. For a live agent eval, present both synthetic variants
without telling the reviewer the expected classification; confirm it follows the
terminal chain, rejects unnecessary forwarding, and identifies the genuine gap.
Separately verify extension discovery and a human-approved representative run in
a compatible Copilot runtime; mocks do not validate live agent quality.

The same baseline/necessity requirements apply to the `design-author` skill and
direct authoring. An interrupted or skipped independent review must be labeled
**unvalidated draft**, never silently treated as a successful workflow.
