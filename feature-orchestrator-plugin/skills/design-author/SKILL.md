---
name: design-author
description: Create detailed design specs for features. Use when asked to design a feature, create a design spec, write a design doc, or create an implementation plan. Triggers include "design this feature", "create a design spec", "write a design doc".
---

# Design Author

Create detailed design specs for features, save them locally, and optionally open PRs for review.

## Configuration

Read `.github/orchestrator-config.json` for:
- `design.docsPath` — where to save design docs (e.g., `design-docs/` or `docs/designs/`)
- `design.templatePath` — path to design spec template (optional)
- `design.folderPattern` — folder naming pattern (e.g., `[{platform}] {featureName}`)
- `design.reviewRepo` — repo for design review PRs (optional)

If no config, save to `docs/designs/` and use the built-in template below.

## Design Spec Template

Key sections every design spec should include:

1. **Title** — Feature name
2. **Components** — Which modules/repos affected
3. **Problem description** — User problem, business context, examples
4. **Requirements** — Functional requirements (must-have)
5. **System Qualities** — Performance, telemetry, security, supportability
6. **Solution options** — At least 2 options with pseudo code, pros/cons
7. **Solution Decision** — Recommended option with reasoning
8. **API surface** — Public/internal classes, methods (if applicable)
9. **Data flow** — Request/response flow across components
10. **Feature flag** — Android-complete runtime features require default-off isolation; reasoned N/A only for non-runtime work
11. **Telemetry** — Key metrics, span names, success/failure signals
12. **Testing strategy** — Unit tests, integration tests, E2E coverage
13. **Rollout plan** — Staged rollout, feature flag configuration
14. **Cross-repo impact** — Which repos need changes and in what order

If a template file exists at the configured `design.templatePath`, follow that instead.

For Android-complete's Dev Design Dynamic Workflow, generate a separate
**Human Design Review Brief** from the same authoritative detailed design and
publish both for one paired revision. The brief is the primary human approval
surface: decisions needed first; customer outcome/scope; plain-language architecture
and flow; recommendations, rejected alternatives and meaningful tradeoffs;
evidence versus assumptions/uncertainties; consequential security/authentication,
accessibility, compatibility/cross-repo, default-off and rollback risks/safeguards
(justified N/A when appropriate); accountable owners and approval blockers.
Link supporting sections/evidence in the same detailed design. Preserve full
contracts and independently check consistency/completeness, never hide risks behind
links. Target ten minutes with the complete rendered 1,800-word/16,000-character
budget; fail visibly rather than silently omitting approval-critical information.
Bind human approval to the precise published pair; substantive edits regenerate
and re-review both, highlighting changes since verified prior review. Never reuse
stale artifacts/approval. Design approval is not rollout approval; this human brief
does not move full pre-mortem authoring ahead of implementation completion/dispatch.

For Android-complete runtime features, retain a **Feature flags and isolation**
section using the [default-off contract](../feature-planner/references/pbi-template.md#android-complete-default-off-feature-contract):
source-verified owning framework, key/owner, Boolean false code/configuration-failure/
production defaults, all guarded entry points, zero new off-path side effects,
override/cache behavior, approved enabled E2E cohort, active-attempt rollback and
owned code-review/CI verification. Map runtime changes to flags; documentation/
regression-only/deferred work needs an explicit no-runtime-impact reason. Small
features cannot be exempted. Carry the contract into implementation PBI acceptance
criteria. Do not invent APIs or inspect/change live production configuration;
design approval is not code verification or activation permission.

For Android-complete, supplement either template with a **Pre-mortem and operational
readiness** section containing a design-time authoring brief, not a pre-mortem
draft: requirements/source evidence, known risks, telemetry/rollback inputs, gaps
and proposed owners. Plan coverage using the
[pre-mortem content and acceptance contract](../feature-planner/references/pbi-template.md#android-complete-feature-pre-mortem-pbi).
This applies to every feature, including non-runtime work. Include a
**Pre-mortem child PBI** field (`Pending feature planning`, or existing AB# and URL);
planning includes the dedicated authoring/review child item, reusing a suitable
existing item, and approved backlog creation adds reciprocal design/PBI links.
Record **Pre-mortem artifact: Pending implementation completion and PBI dispatch**.
The dedicated PBI depends on all feature implementation PBIs. Only after all
feature implementation is complete, dispatch it for agent authoring against
implemented, pinned sources. The agent writes the complete document (including
actual KQL drafts) and links it for the named human reviewer to check completeness, assumptions,
owners and gaps and record approval. Agent completion/design approval is not
pre-mortem acceptance; the future artifact is not a design/planning/implementation
prerequisite and must not be drafted at those stages. Require human-reviewed
acceptance before release/production enablement, without authorizing either.
Explicit unexecuted pre-flight KQL and unresolved mappings are acceptable with
named follow-up owners. No production data while disabled is expected. Separate
document review from approved enabled-E2E query validation and release approval;
do not require broad production activation to complete the documentation PBI.

### Accessibility design (required)

Retain an **Accessibility design** section in every spec, including when the
configured template lacks it. For new or changed Authenticator UI, map the stable
requirement IDs from `docs/Templates/requirements-template.md` section 3.6 and
section 4 to concrete design decisions. Requirements define what must be observed;
this section explains how the chosen solution achieves it. For features without
affected Authenticator UI, retain the section and mark it N/A with a reason.

Use Authenticator's `.github/skills/android-ui-development/SKILL.md` and
`.github/skills/android-ui-development/references/accessibility.md` in the owning
checkout as mandatory baselines. Record missing access as an evidence gap.
Resolve only ambiguous feature-specific behavior with the owner; do not reconfirm
established standards as optional choices or invent missing requirements.

| Requirement / scenario / flow IDs | Affected screens and states | Design decision and rationale | Verification IDs |
|---|---|---|---|
| [Stable IDs from requirements] | [Screen IDs, entry points, transitions, and state variants] | [How the chosen solution satisfies acceptance; existing behavior to preserve or required delta] | [Checks in Testing strategy] |

Cover the following areas with feature-specific decisions, or an individual N/A
and rationale:
- **TalkBack**: localized names, roles, states, headings and dynamic values;
  nonduplicated semantics; forward/backward reading order; focus placement,
  announcements and restoration across transitions.
- **Keyboard / D-pad**: reachability from screen entry without touch or prior
  navigation in another mode, focus order and visible focus, activation and exit.
- **Text scaling / layout**: reflow and reachable content/actions at large
  font/display sizes, short viewports, landscape, and light/dark themes.
- **Touch targets**: at least 48dp by 48dp for every interactive control,
  including Back/Close and secondary actions.
- **Contrast / non-color cues**: baseline-compliant contrast and perceivable
  labels, states and errors that do not rely on color alone.

Map applicable loading, error, success, cancellation, Back/Close, handoff/return,
retry/re-entry and restoration/rotation states across the whole multi-screen flow,
including feature-on/off paths. Distinguish app-owned behavior from external UI:
specify app handoff/return checks without inventing external guarantees. For reused
screens/components, cite the inspected revision and explain how their behavior
satisfies each feature-specific requirement and transition; reuse alone is not
justification or runtime evidence.

In **Testing strategy**, include a verification matrix referenced by this section:

| Verification ID / requirement IDs | Entry steps, actions and expected results | Build / device / Android and configuration | Test owner / reviewer | Evidence location and status / gap |
|---|---|---|---|---|
| [CHECK-1; stable IDs] | [Real-app TalkBack and separate keyboard-only checks from entry] | [Build/commit, device/emulator, Android, theme/font/display, feature/cohort, TalkBack version/settings or input setup; Unknown if not yet tested] | [Confirmed owner or Unknown] | [Planned location; Passed with evidence / Unverified / Blocked with reason and follow-up owner / N/A with rationale] |

Plan representative multi-screen checks, not just isolated controls. Supporting
screenshots, compilation, semantics assertions or forced-focus tests do not prove
runtime accessibility. Preserve requirements' evidence statuses and gaps; require
revision/scenario coverage before reusing evidence and keep sensitive recordings
in authorized locations. Record missing applicable coverage as a blocking design
question, not N/A. Runtime evidence may remain planned/Unverified during design.
Carry owners, checks and gaps into PBI acceptance; reference this matrix from
**Rollout plan** instead of duplicating it. Record accepted deferrals with decision
owner, rationale, tracking item and release consequence. Design approval never
establishes runtime accessibility acceptance or rollout approval.

## Workflow

### Step 1: Understand the Feature

Gather from the developer:
1. What the feature does and why it's needed
2. Which components/flows it affects
3. Scope boundaries (in/out)
4. Any existing designs to reference

### Step 2: Research the Codebase

Use the `codebase-researcher` skill to:
- Understand how related functionality currently works
- Identify which repos/files would be affected
- Find existing patterns to follow (feature flags, error handling, telemetry)
- Check for existing design docs on the same topic

### Step 3: Research Existing Designs

If `design.docsPath` is configured, search for related designs:
```bash
ls <docsPath>/ | grep -i "<keyword>"
```
Use existing designs as **style reference and historical context**, not ground truth for behavior.

### Step 4: Write the Design Spec

Create the spec at:
```
<docsPath>/<folderPattern>/<spec-name>.md
```

For the **Solution options** section:
- Always provide at least 2 options
- Include pseudo code / API signatures for each
- List concrete pros/cons
- Clear recommendation in Solution Decision

### Agent Implementation Notes

Write the design knowing a coding agent will implement it. Be explicit about:
- Class boundaries and responsibilities
- Threading model
- Error contracts
- Integration points with other modules

### Step 5: Present Design for Review

Assess the Accessibility design section before handoff and after revisions.
Check every applicable requirement, screen/state and verification mapping, including
reused components and justified non-UI/N/A cases. Missing applicable design coverage
blocks readiness; planned runtime evidence must remain explicitly Unverified rather
than being treated as a pass.

After writing, **STOP and present choices** using `askQuestion`:

```
askQuestion({
  question: "Design spec written. What would you like to do?",
  options: [
    { label: "📖 Review locally", description: "Open in editor for inline review" },
    { label: "✅ Approve & plan PBIs", description: "Skip PR, move to work item planning" },
    { label: "📋 Open draft PR", description: "Push to review repo as draft PR" },
    { label: "🚀 Open published PR", description: "Push and publish PR for team review" },
    { label: "✏️ Request changes", description: "Tell me what to revise" }
  ]
})
```

**MANDATORY**: Wait for the developer's explicit choice. Do NOT auto-select.

### Step 5a: Local Review (option 1)

Open the file: `code "<spec path>"`

Tell the developer:
> "The spec is open. Here's how to review:
> 1. Click the **+ icon** in the gutter to add inline comments
> 2. When done, click the status bar button to submit comments
> 3. I'll address each comment and present choices again"

### Step 5b: Push and Create PR (options 3 or 4)

**Branch naming**: Discover alias from `git config user.email` (strip @domain):
```powershell
$alias = (git config user.email) -replace '@.*', ''
git checkout -b "$alias/design-<feature-name-kebab-case>"
```

**Git workflow** (from design docs directory):
```powershell
cd <docsPath>/
git add "<folder name>"
git commit -m "Add design spec: <Feature Name>"
git push origin $BRANCH_NAME
```

**Create PR**: Use `gh pr create` or ADO MCP tools if available.
- Set `--draft` for option 3, omit for option 4
- **PR description**: Use actual line breaks or HTML formatting, NOT literal `\n` escape sequences
- Target branch: `main` (or the repo's default branch)

Present the PR link:
```markdown
### PR Created
**PR**: [link to PR]
**Status**: Draft / Published

### How to Review
1. Open the PR link above
2. Use inline commenting to leave feedback
3. When done, say: **"address my design review comments"**
4. I'll read the comments and update the spec

When the team approves, say: **"design approved, plan the PBIs"**
```

### Step 6: Address Review Comments

When asked to address comments (from PR or local review):
1. Read the feedback (from PR comments or `reviews.json`)
2. For each comment:
   - Understand the feedback
   - Edit the local design spec to address it
   - If on a PR branch, reply to the thread confirming the resolution
3. Commit and push the updates to the same branch
4. Report a summary of changes made
5. Return to Step 5 (present choices again)

### Step 7: Proceed to Implementation

When the developer confirms the design is approved:
1. The PR can be completed/merged
2. Hand off to the `feature-planner` skill for PBI decomposition

## Important Caveats

- **Existing designs may be outdated** — last-minute PR discussions often cause code to deviate.
  Always verify proposed patterns against the **current codebase**, not just existing designs.
- **Use existing designs as style reference**, not as ground truth for current behavior.
- For paths with brackets `[]` or spaces, use PowerShell with `-LiteralPath`

### Open Questions

If there are genuine unknowns during design, use `askQuestion` to resolve them interactively,
or list them in the spec for the team to discuss during review.
