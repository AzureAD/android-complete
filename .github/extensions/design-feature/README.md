<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android feature design workflow

`design-feature` is a repository-scoped **Copilot Dynamic Workflow**, not a GitHub
Actions/Agentic Workflow. A compatible Copilot runtime discovers `extension.mjs`
under `.github/extensions/design-feature`. The runtime supplies
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
  }
}
```

The caller supplies a genuine approval record. Matching strings are **not**
authentication or proof of approval: the evidence worker must inspect the approval
provenance, and humans must verify it at the gate. Missing/stale approval,
conflicting source evidence, unavailable relevant repositories, or blocking
unknowns return `needs clarification`, not invented requirements.

## Flow and output

1. Validate Stage 0 revision, approval metadata, unique requirements, and unknowns.
2. Gather current repository evidence with repository/commit/path/source locators;
   separate facts, conflicts, assumptions, unknowns, and blocking unknowns.
3. Author structured design sections and one traceability row per requirement.
4. Check required sections, revision, evidence links, and traceability in code.
5. Spawn a separate independent reviewer against the fixed rubric in `workflow.mjs`.
6. Fix and re-review at most twice; never change requirements or waive criteria.
7. Return the full JSON human gate packet, including the approved input, evidence,
   design artifact, validation/review history, fix count, rubric, reasons, and next action.

The `design.sections` object is the design artifact; the workflow returns it rather
than writing a design document into a repository. The runtime journals step outputs.
`GO to Planning` is only an automated recommendation when all structural checks
and rubric criteria pass with no findings or blocking unknowns. The packet always
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

Checkpoint keys include a workflow version and digest of the approved input.
Resume only against unchanged source revisions and input. Start a fresh run if
the checkout/evidence changes; change the key version when workflow semantics,
schemas, prompts, or rubric change. Steps have no intentional external side effects,
so replay does not publish duplicate artifacts.

## Checks

Run from the repository root (Node.js 18 or newer):

```powershell
node --check .github\extensions\design-feature\extension.mjs
node --check .github\extensions\design-feature\workflow.mjs
node --test .github\extensions\design-feature\workflow.test.mjs
```

Tests use mocked agents/steps and require no SDK installation, network, or paid
agents. They cover approval gates, source/trace validation, ordinary/hard failures,
fixed rubric coverage, bounded fixes, recommendation semantics, and checkpoint
identity. Separately verify extension discovery and a human-approved representative
run in a compatible Copilot runtime; mocks do not validate live agent quality.
