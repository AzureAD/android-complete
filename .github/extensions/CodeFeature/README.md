<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android coding and PR workflow (`code-feature`)

`code-feature` is a standalone, repository-scoped **Copilot Dynamic Workflow** modeled
after `.github/extensions/DevDesignDoc` (`design-feature`). A compatible Copilot runtime
discovers `.github/extensions/CodeFeature/extension.mjs` and registers the `code-feature`
workflow along with role-scoped custom agents (`code-evidence`, `code-builder`, and
`code-reviewer`).

## Goal

Given an approved coding task or PBI specification:

1. **Validate task & approval**: Verify immutable revision binding, human approval provenance,
   unique requirement IDs, target repository metadata, and `AB#<id>` work-item linkage.
2. **Gather repository evidence**: Inspect target repositories (`common`, `broker`, `msal`,
   `adal`, or `android-complete`), verify checked-out commit revisions, and confirm approval
   provenance.
3. **Build initial implementation (`code-builder`)**: Author production code and unit tests
   following Android Auth conventions (Kotlin-first for new code, Java style preserved for
   existing Java files, copyright headers, custom `Logger`, KDoc/JavaDoc, and traceability).
4. **Loop with Independent Reviewer (`code-reviewer`) up to 3 fix iterations max**:
   - Deterministic checks validate file safety, non-empty unit tests, requirements-to-files/tests
     traceability, PR metadata, unified diffs, and any configured `verificationCommands`
     (such as Gradle compile/test commands).
   - `code-reviewer` (restricted to read-only `view`/`glob`/`grep` tools) evaluates the diff,
     tests, and PR packet against the 9-point fixed rubric and every target acceptance criterion.
   - If any validation error or review finding exists, the workflow returns to `code-builder`
     with the structured findings (`fix-1`, `fix-2`, `fix-3`) until the reviewer reports zero
     findings or the 3-fix ceiling (`maxFixIterations: 3`) is reached.
5. **Create verified Pull Requests**:
   - Only after deterministic checks and the independent reviewer pass with zero findings, the
     workflow prepares or opens each target PR with `Fixes AB#<workItemId>` in the title and
     body, full requirements traceability table, test plan, and compatibility/rollout summary.

## Input and use

`code-feature` supports **three everyday input modes** (plus pre-structured `task`/`stage0` objects):

### 1. Design Doc (`designDocPath`, `designDoc`, or `designPacket`)
Pass a path to a design doc (most common when a design already exists), raw markdown, or
the output packet from `design-feature`:

```json
{
  "designDocPath": "design-docs/[Android] Broker Throttling.md",
  "workItemId": 3802484,
  "options": {
    "createPullRequests": true
  }
}
```

### 2. Azure DevOps PBI (`pbiId`, `pbiIds`, `pbi`, or `pbis`)
Pass one or more PBI work-item IDs (fetched automatically via `az boards work-item show`)
or inline PBI objects:

```json
{
  "pbiIds": [3802484, 3802485],
  "options": {
    "createPullRequests": true
  }
}
```

### 3. Free-form Prompt (`prompt`)
Pass a plain-language request (optionally with `repository` and `workItemId`):

```json
{
  "prompt": "Add server-directed Retry-After throttling to Common for 429/503 responses (AB#3802484)",
  "repository": "common",
  "options": {
    "createPullRequests": true
  }
}
```

### How details and clarifications are handled
- For **Design Doc**, **PBI**, and **Free-form Prompt** inputs, the workflow automatically
  reads the design doc / PBI details, inspects the target repositories, and extracts concrete
  requirements (`R1`, `R2`, ...) and per-repository targets.
- It infers technical details from the design doc, PBI description, and codebase conventions
  without interrupting the engineer.
- **Only when requirements are genuinely missing, contradictory, or ambiguous** (cannot be
  determined from the design doc, PBI, or codebase), the workflow asks the user for
  clarification via session elicitation (or returns `recommendation: "needs clarification"`
  with `questions: [...]`).

### Options

- `maxFixIterations` (default `3`, max `3`): Maximum builder fix iterations after initial build.
- `createPullRequests` (default `false`): When `true`, writes verified files to the target
  repository checkout, creates branch `feat/ab-<workItemId>-<slug>`, commits with the Copilot
  trailer, pushes to `origin` using the repository's GitHub account, and runs `gh pr create`.
  When `false`, returns the complete verified PR title, body, branch, and file changes in the
  packet without mutating git remotes.
- `draftPullRequest` (default `false`): Passes `--draft` to `gh pr create` when creating PRs.
- `builderModel` / `reviewerModel` / `evidenceModel`: Optional model overrides so the
  independent reviewer can run on a distinct model from the builder.

## Fixed Code Review Rubric

1. `approvedScopeAndRequirements`
2. `architectureAndApiCompatibility`
3. `correctnessAndEdgeCases`
4. `concurrencyAndThreadSafety`
5. `securityPrivacyAndLogging`
6. `performanceAndResourceBounds`
7. `telemetryAndObservability`
8. `unitTestsAndVerification`
9. `prReadinessAndTraceability`

## Bounds, safety, and resume

- Sequential journaled `ctx.step` and `ctx.agent` calls:
  1 evidence worker + 1 initial builder + up to 4 independent reviews and 3 builder fix
  rounds (9 agents maximum).
- `code-reviewer` and `code-evidence` are registered with read-only tools (`view`, `glob`,
  `grep`) and never receive the builder's chain-of-thought reasoning.
- If workspace verification or review fails after `maxFixIterations`, any modified workspace
  files are restored from backup and no PR is created (`recommendation: "NO-GO"`).

## Checks

Run from the repository root (Node.js 18 or newer):

```powershell
node --check .github\extensions\CodeFeature\extension.mjs
node --check .github\extensions\CodeFeature\workflow.mjs
node --test .github\extensions\CodeFeature\workflow.test.mjs
```
